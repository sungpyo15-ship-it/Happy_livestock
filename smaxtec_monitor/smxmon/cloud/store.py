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
CREATE TABLE IF NOT EXISTS login_events (
    at          TEXT NOT NULL,
    username    TEXT,
    result      TEXT NOT NULL      -- ok / not_allowed / bad_password / api_error / rate_limited / empty
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
            c.executescript(SCHEMA)

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
            c.execute("DELETE FROM users WHERE uid=?", (uid,))
        d = self.root / "users" / uid
        if d.is_dir() and d.parent == self.root / "users":
            shutil.rmtree(d, ignore_errors=True)

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
