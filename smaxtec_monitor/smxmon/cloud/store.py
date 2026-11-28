"""클라우드 사용자 저장소: 사용자·세션·허용목록 + smaXtec 비밀번호 암호화 보관."""
from __future__ import annotations

import hashlib
import secrets
import shutil
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    uid         TEXT PRIMARY KEY,
    username    TEXT NOT NULL UNIQUE,     -- smaXtec 로그인 아이디(소문자)
    enc_pw      BLOB NOT NULL,            -- 암호화된 smaXtec 비밀번호
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending / running / ok / failed / login_failed
    message     TEXT,
    farms       INTEGER,
    created_at  TEXT NOT NULL,
    last_login  TEXT,
    last_run    TEXT,
    last_ok     TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,
    uid         TEXT NOT NULL,
    csrf        TEXT NOT NULL,
    expires     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (      -- 메모·체크리스트 (사용자별, 모든 기기 동기화)
    uid      TEXT NOT NULL,
    key      TEXT NOT NULL,
    value    TEXT NOT NULL,
    updated  TEXT NOT NULL,
    PRIMARY KEY (uid, key)
);
CREATE TABLE IF NOT EXISTS login_events (
    at          TEXT NOT NULL,
    username    TEXT,
    result      TEXT NOT NULL      -- ok / not_allowed / bad_password / api_error / rate_limited / empty
);
CREATE TABLE IF NOT EXISTS contacts (   -- 목장별 담당자(딜러·판매과장) — 팀 공용
    farm_id  TEXT NOT NULL,
    idx      INTEGER NOT NULL,
    role     TEXT, name TEXT, phone TEXT,
    farm_name TEXT,
    updated_by TEXT, updated TEXT,
    PRIMARY KEY (farm_id, idx)
);
CREATE TABLE IF NOT EXISTS push_subs (  -- 웹 푸시 구독(기기별). owner = u:<uid>(팀원) / r:<receiver id>(담당자)
    endpoint TEXT NOT NULL,             -- 한 기기가 팀원·담당자 알림을 둘 다 받을 수 있음
    owner    TEXT NOT NULL,
    p256dh   TEXT NOT NULL, auth TEXT NOT NULL,
    ua       TEXT, created TEXT, last_ok TEXT, fails INTEGER DEFAULT 0,
    PRIMARY KEY (endpoint, owner)
);
CREATE TABLE IF NOT EXISTS receivers (  -- 담당자(딜러·판매과장) 알림 받기 링크
    id      TEXT PRIMARY KEY,
    token   TEXT UNIQUE NOT NULL,
    phone   TEXT NOT NULL,             -- 숫자만 (contacts.phone 과 연결)
    name    TEXT, created TEXT, revoked INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS dispatches (  -- 자동 업무지시 1건 = 담당자 1명에게 1회
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    day     TEXT, slot TEXT, phone TEXT, name TEXT,
    farms   TEXT, text TEXT, created TEXT, test INTEGER DEFAULT 0, devices INTEGER DEFAULT 0, delivered INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS dispatch_log (  -- 담당자 × 목장 × 하루 (오후 재발송 판단·완료 보고)
    day TEXT, phone TEXT, farm_id TEXT, slot TEXT, grade TEXT, dispatch_id INTEGER, acked TEXT,
    PRIMARY KEY (day, phone, farm_id, slot)
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS heat_log (   -- 발정 알림 보낸 기록(같은 발정 두 번 안 보냄). who = u:<uid> / p:<전화>
    who TEXT NOT NULL, key TEXT NOT NULL, sent TEXT, PRIMARY KEY (who, key)
);
CREATE TABLE IF NOT EXISTS heat_ack (   -- 발정 '확인함' 표시
    who TEXT NOT NULL, key TEXT NOT NULL, at TEXT, PRIMARY KEY (who, key)
);
CREATE TABLE IF NOT EXISTS allowed (
    username    TEXT PRIMARY KEY,
    added_at    TEXT NOT NULL,
    note        TEXT
);
"""
FMT = "%Y-%m-%d %H:%M:%S"


def _now() -> str:
    return datetime.now().strftime(FMT)


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class User:
    uid: str
    username: str
    status: str
    message: str | None
    farms: int | None
    created_at: str
    last_login: str | None
    last_run: str | None
    last_ok: str | None


class CloudStore:
    def __init__(self, root: Path, key: str | bytes, admin: str = ""):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "users").mkdir(exist_ok=True)
        self.path = self.root / "cloud.db"
        self.fernet = Fernet(key if isinstance(key, bytes) else key.encode())
        self.admin = admin.strip().lower()
        self._lock = threading.Lock()
        with self._conn() as c:
            old = c.execute("SELECT sql FROM sqlite_master WHERE name='push_subs'").fetchone()
            if old and "PRIMARY KEY (endpoint, owner)" not in old[0]:     # 예전 구조(기기당 1명) → 바꿈
                c.execute("ALTER TABLE push_subs RENAME TO push_subs_old")
            c.executescript(SCHEMA)
            if old and "PRIMARY KEY (endpoint, owner)" not in old[0]:
                c.execute("INSERT OR IGNORE INTO push_subs SELECT endpoint, owner, p256dh, auth, ua, created, last_ok, fails FROM push_subs_old")
                c.execute("DROP TABLE push_subs_old")

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        return c

    # ───────── 비밀번호 ─────────
    def encrypt(self, pw: str) -> bytes:
        return self.fernet.encrypt(pw.encode())

    def password(self, uid: str) -> str | None:
        with self._conn() as c:
            r = c.execute("SELECT enc_pw FROM users WHERE uid=?", (uid,)).fetchone()
        if not r:
            return None
        try:
            return self.fernet.decrypt(r["enc_pw"]).decode()
        except InvalidToken:
            return None

    # ───────── 허용 목록 (관리자만 팀원 추가) ─────────
    def is_allowed(self, username: str) -> bool:
        u = username.strip().lower()
        if u and u == self.admin:
            return True
        with self._conn() as c:
            return c.execute("SELECT 1 FROM allowed WHERE username=?", (u,)).fetchone() is not None

    def allow(self, username: str, note: str = "") -> None:
        with self._lock, self._conn() as c:
            c.execute("INSERT OR REPLACE INTO allowed VALUES (?,?,?)", (username.strip().lower(), _now(), note))

    def disallow(self, username: str) -> None:
        u = username.strip().lower()
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM allowed WHERE username=?", (u,))
        user = self.get_by_username(u)
        if user and u != self.admin:
            self.delete_user(user.uid)

    def allowed_list(self) -> list[sqlite3.Row]:
        with self._conn() as c:
            return c.execute("SELECT * FROM allowed ORDER BY username").fetchall()

    # ───────── 사용자 ─────────
    def upsert_user(self, username: str, password: str) -> tuple[User, bool]:
        """로그인 성공 시 호출. 반환: (사용자, 새로 만들었는지)."""
        u = username.strip().lower()
        with self._lock, self._conn() as c:
            r = c.execute("SELECT uid FROM users WHERE username=?", (u,)).fetchone()
            if r:
                c.execute("""UPDATE users SET enc_pw=?, last_login=?,
                             status=CASE WHEN status='login_failed' THEN 'pending' ELSE status END,
                             message=CASE WHEN status='login_failed' THEN NULL ELSE message END WHERE uid=?""",
                          (self.encrypt(password), _now(), r["uid"]))
                uid, new = r["uid"], False
            else:
                uid, new = secrets.token_hex(6), True
                c.execute("INSERT INTO users(uid, username, enc_pw, created_at, last_login) VALUES (?,?,?,?,?)",
                          (uid, u, self.encrypt(password), _now(), _now()))
        return self.get_user(uid), new

    def get_user(self, uid: str) -> User | None:
        with self._conn() as c:
            r = c.execute("SELECT uid, username, status, message, farms, created_at, last_login, last_run, last_ok "
                          "FROM users WHERE uid=?", (uid,)).fetchone()
        return User(**dict(r)) if r else None

    def get_by_username(self, username: str) -> User | None:
        with self._conn() as c:
            r = c.execute("SELECT uid FROM users WHERE username=?", (username.strip().lower(),)).fetchone()
        return self.get_user(r["uid"]) if r else None

    def users(self) -> list[User]:
        with self._conn() as c:
            ids = [r["uid"] for r in c.execute("SELECT uid FROM users ORDER BY username")]
        return [self.get_user(i) for i in ids]

    def set_status(self, uid: str, status: str, message: str | None = None, *, farms: int | None = None,
                   ok: bool = False) -> None:
        with self._lock, self._conn() as c:
            c.execute("UPDATE users SET status=?, message=?, last_run=?, farms=COALESCE(?, farms), "
                      "last_ok=CASE WHEN ? THEN ? ELSE last_ok END WHERE uid=?",
                      (status, message, _now(), farms, ok, _now(), uid))

    def set_farms(self, uid: str, n: int) -> None:
        with self._lock, self._conn() as c:
            c.execute("UPDATE users SET farms=? WHERE uid=?", (n, uid))

    def user_dir(self, uid: str) -> Path:
        d = self.root / "users" / uid
        d.mkdir(parents=True, exist_ok=True)
        return d

    def delete_user(self, uid: str) -> None:
        """연결 해제: 비밀번호·세션·수집 데이터 모두 삭제."""
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM sessions WHERE uid=?", (uid,))
            c.execute("DELETE FROM notes WHERE uid=?", (uid,))
            c.execute("DELETE FROM users WHERE uid=?", (uid,))
        d = self.root / "users" / uid
        if d.is_dir() and d.parent == self.root / "users":
            shutil.rmtree(d, ignore_errors=True)

    # ───────── 메모·체크리스트 ─────────
    def notes(self, uid: str) -> dict:
        import json
        with self._conn() as c:
            rows = c.execute("SELECT key, value FROM notes WHERE uid=?", (uid,)).fetchall()
        return {r["key"]: json.loads(r["value"]) for r in rows}

    def set_note(self, uid: str, key: str, value) -> None:
        import json
        with self._lock, self._conn() as c:
            if value in ("", None, {}):
                c.execute("DELETE FROM notes WHERE uid=? AND key=?", (uid, key))
            else:
                c.execute("INSERT INTO notes VALUES (?,?,?,?) ON CONFLICT(uid, key) DO UPDATE SET "
                          "value=excluded.value, updated=excluded.updated",
                          (uid, key, json.dumps(value, ensure_ascii=False), _now()))

    # ───────── 목장 담당자(팀 공용) ─────────
    def contacts(self, farm_ids: list[str] | None = None) -> dict[str, list[dict]]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM contacts ORDER BY farm_id, idx").fetchall()
        out: dict[str, list[dict]] = {}
        for r in rows:
            if farm_ids is None or r["farm_id"] in farm_ids:
                out.setdefault(r["farm_id"], []).append({"role": r["role"] or "", "name": r["name"] or "",
                                                         "phone": r["phone"] or "", "farm_name": r["farm_name"] or ""})
        return out

    def set_contacts(self, farm_id: str, items: list[dict], by: str, farm_name: str = "") -> None:
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM contacts WHERE farm_id=?", (farm_id,))
            for i, it in enumerate(items):
                c.execute("INSERT INTO contacts VALUES (?,?,?,?,?,?,?,?)",
                          (farm_id, i, it.get("role", ""), it.get("name", ""), it.get("phone", ""), farm_name, by, _now()))

    # ───────── 설정 값 ─────────
    def kv_get(self, key: str, default: str | None = None) -> str | None:
        with self._conn() as c:
            r = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def kv_set(self, key: str, value: str) -> None:
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO kv VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    # ───────── 웹 푸시 구독 ─────────
    def add_sub(self, owner: str, sub: dict, ua: str = "") -> None:
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO push_subs VALUES (?,?,?,?,?,?,NULL,0) ON CONFLICT(endpoint, owner) DO UPDATE SET "
                      "p256dh=excluded.p256dh, auth=excluded.auth, ua=excluded.ua, fails=0",
                      (sub["endpoint"], owner, sub["p256dh"], sub["auth"], ua[:120], _now()))

    def subs(self, owner: str) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM push_subs WHERE owner=?", (owner,))]

    def sub_result(self, endpoint: str, ok: bool, gone: bool = False) -> None:
        with self._lock, self._conn() as c:
            if gone:
                c.execute("DELETE FROM push_subs WHERE endpoint=?", (endpoint,))
            elif ok:
                c.execute("UPDATE push_subs SET last_ok=?, fails=0 WHERE endpoint=?", (_now(), endpoint))
            else:
                c.execute("UPDATE push_subs SET fails=fails+1 WHERE endpoint=?", (endpoint,))
                c.execute("DELETE FROM push_subs WHERE endpoint=? AND fails>=10", (endpoint,))

    def del_subs(self, owner: str) -> None:
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM push_subs WHERE owner=?", (owner,))

    # ───────── 담당자 알림 링크 ─────────
    def receivers(self) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM receivers WHERE revoked=0")]

    def receiver_by_token(self, token: str) -> dict | None:
        if not token or len(token) < 20:
            return None
        with self._conn() as c:
            r = c.execute("SELECT * FROM receivers WHERE token=? AND revoked=0", (token,)).fetchone()
        return dict(r) if r else None

    def receiver_by_phone(self, phone: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM receivers WHERE phone=? AND revoked=0", (phone,)).fetchone()
        return dict(r) if r else None

    def new_receiver(self, phone: str, name: str) -> dict:
        import secrets
        old = self.receiver_by_phone(phone)
        with self._lock, self._conn() as c:
            if old:
                c.execute("UPDATE receivers SET revoked=1 WHERE id=?", (old["id"],))
                c.execute("DELETE FROM push_subs WHERE owner=?", ("r:" + old["id"],))
            rid, tok = secrets.token_hex(6), secrets.token_urlsafe(24)
            c.execute("INSERT INTO receivers VALUES (?,?,?,?,?,0)", (rid, tok, phone, name, _now()))
        return {"id": rid, "token": tok, "phone": phone, "name": name}

    def revoke_receiver(self, phone: str) -> None:
        old = self.receiver_by_phone(phone)
        if old:
            with self._lock, self._conn() as c:
                c.execute("UPDATE receivers SET revoked=1 WHERE id=?", (old["id"],))
                c.execute("DELETE FROM push_subs WHERE owner=?", ("r:" + old["id"],))

    # ───────── 자동 업무지시 기록 ─────────
    def add_dispatch(self, day: str, slot: str, phone: str, name: str, farms: list, text: str, test: bool) -> int:
        import json
        with self._lock, self._conn() as c:
            cur = c.execute("INSERT INTO dispatches(day,slot,phone,name,farms,text,created,test) VALUES (?,?,?,?,?,?,?,?)",
                            (day, slot, phone, name, json.dumps(farms, ensure_ascii=False), text, _now(), int(test)))
            did = cur.lastrowid
            for f in farms:     # 시험 모드는 't:전화' 로 따로 기록(실제 발송 판단과 섞이지 않게)
                c.execute("INSERT OR REPLACE INTO dispatch_log VALUES (?,?,?,?,?,?,NULL)",
                          (day, ("t:" if test else "") + phone, f["id"], slot, f["grade"], did))
        return did

    def dispatch_delivered(self, did: int, devices: int, delivered: int) -> None:
        with self._lock, self._conn() as c:
            c.execute("UPDATE dispatches SET devices=?, delivered=? WHERE id=?", (devices, delivered, did))

    def sent_today(self, day: str, phone: str) -> dict[str, str]:
        """{목장: 오늘 마지막으로 보낸 등급}"""
        with self._conn() as c:
            rows = c.execute("SELECT farm_id, grade, slot FROM dispatch_log WHERE day=? AND phone=? ORDER BY slot", (day, phone))
            return {r["farm_id"]: r["grade"] for r in rows}

    def dispatches(self, phone: str | None = None, day: str | None = None, limit: int = 30) -> list[dict]:
        import json
        q, args = "SELECT * FROM dispatches WHERE 1=1", []
        if phone:
            q, args = q + " AND phone=?", args + [phone]
        if day:
            q, args = q + " AND day=?", args + [day]
        with self._conn() as c:
            rows = [dict(r) for r in c.execute(q + " ORDER BY id DESC LIMIT ?", (*args, limit))]
        for r in rows:
            r["farms"] = json.loads(r["farms"] or "[]")
        return rows

    def ack(self, phone: str, did: int, farm_id: str) -> bool:
        with self._lock, self._conn() as c:
            n = c.execute("UPDATE dispatch_log SET acked=? WHERE phone IN (?,?) AND dispatch_id=? AND farm_id=?",
                          (_now(), phone, "t:" + phone, did, farm_id)).rowcount
        return n > 0

    def acked(self, phone: str, did: int) -> set[str]:
        with self._conn() as c:
            return {r[0] for r in c.execute("SELECT farm_id FROM dispatch_log WHERE phone IN (?,?) AND dispatch_id=? AND acked IS NOT NULL",
                                             (phone, "t:" + phone, did))}

    # ───────── 발정 알림 기록 ─────────
    def heat_sent(self, who: str) -> set[str]:
        with self._conn() as c:
            return {r[0] for r in c.execute("SELECT key FROM heat_log WHERE who=?", (who,))}

    def heat_mark_sent(self, who: str, keys: list[str]) -> None:
        with self._lock, self._conn() as c:
            c.executemany("INSERT OR IGNORE INTO heat_log VALUES (?,?,?)", [(who, k, _now()) for k in keys])
            c.execute("DELETE FROM heat_log WHERE sent < datetime('now','-10 days')")

    def heat_acks(self, who: str) -> set[str]:
        with self._conn() as c:
            return {r[0] for r in c.execute("SELECT key FROM heat_ack WHERE who=?", (who,))}

    def heat_ack(self, who: str, key: str, on: bool = True) -> None:
        with self._lock, self._conn() as c:
            if on:
                c.execute("INSERT OR REPLACE INTO heat_ack VALUES (?,?,?)", (who, key[:120], _now()))
            else:
                c.execute("DELETE FROM heat_ack WHERE who=? AND key=?", (who, key))

    # ───────── 로그인 기록 (관리자 화면 진단용, 최근 100건만 보관) ─────────
    def log_login(self, username: str, result: str) -> None:
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO login_events VALUES (?,?,?)", (_now(), (username or "")[:80].lower(), result))
            c.execute("DELETE FROM login_events WHERE rowid NOT IN (SELECT rowid FROM login_events ORDER BY rowid DESC LIMIT 100)")

    def login_events(self, limit: int = 15) -> list[sqlite3.Row]:
        with self._conn() as c:
            return c.execute("SELECT * FROM login_events ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()

    # ───────── 세션 ─────────
    def new_session(self, uid: str, days: int = 90) -> tuple[str, str]:
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        exp = (datetime.now() + timedelta(days=days)).strftime(FMT)
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM sessions WHERE expires < ?", (_now(),))
            c.execute("INSERT INTO sessions VALUES (?,?,?,?)", (_h(token), uid, csrf, exp))
        return token, csrf

    def session(self, token: str | None) -> tuple[User, str] | None:
        if not token:
            return None
        with self._conn() as c:
            r = c.execute("SELECT uid, csrf, expires FROM sessions WHERE token_hash=?", (_h(token),)).fetchone()
        if not r or r["expires"] < _now():
            return None
        user = self.get_user(r["uid"])
        return (user, r["csrf"]) if user else None

    def end_session(self, token: str | None) -> None:
        if token:
            with self._lock, self._conn() as c:
                c.execute("DELETE FROM sessions WHERE token_hash=?", (_h(token),))
