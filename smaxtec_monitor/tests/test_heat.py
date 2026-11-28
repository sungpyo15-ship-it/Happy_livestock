"""발정 모아보기·알림: 소 이력 정보, 같은 발정 재발송 없음, 역할별 대상, 시험 모드, 화면."""
import dataclasses
import json
import sqlite3
from datetime import datetime, timedelta

from cryptography.fernet import Fernet

from smxmon.cloud.store import CloudStore
from smxmon.heat import note, recent_heats
from smxmon.heat_dispatch import run
from test_dispatch import FakePush

ADMIN = "boss@x.kr"
NOW = datetime(2026, 11, 28, 12, 50)


def _db(path, rows):
    from smxmon.db import Database
    with Database(path) as db:
        db.conn.execute("INSERT INTO farms VALUES ('f1','가나목장',100,'x','x','x')")
        db.conn.execute("INSERT INTO farms VALUES ('f2','다라목장',80,'x','x','x')")
        db.conn.execute("INSERT INTO animals VALUES ('f1','a1','KR1','118','착유우','',1,'x')")
        for fid, aid, raw, typ, t, det in rows:
            db.conn.execute("INSERT INTO alerts(farm_id,animal_id,alert_type,alert_type_raw,occurred_at,first_seen_at,last_seen_at,"
                            "status,detail) VALUES (?,?,?,?,?,?,?,'event',?)", (fid, aid, typ, raw, t, t, t, det))
        db.conn.commit()


ROWS = [("f1", "a1", "calving_confirmation", "MANAGEMENT", "2026-09-03 05:00:00", None),
        ("f1", "a1", "heat", "HEAT", "2026-11-07 10:00:00", None),
        ("f1", "a1", "insemination", "MANAGEMENT", "2026-11-07 20:00:00", None),
        ("f1", "a1", "heat", "HEAT", "2026-11-28 08:10:00", None),          # 오늘 발정 (분만 86일, 간격 21일, 수정 1회)
        ("f2", "b7", "heat_dnb", "HEAT", "2026-11-28 03:00:00", None)]


def test_recent_heats_info(tmp_path):
    _db(tmp_path / "s.db", ROWS)
    c = sqlite3.connect(tmp_path / "s.db")
    hs = recent_heats(c, NOW - timedelta(hours=20), NOW)
    a1 = next(h for h in hs if h["animal_id"] == "a1")
    assert (a1["label"], a1["dim"], a1["interval"], a1["n_ai"], a1["last_ai"]) == ("118", 86, 20, 1, "11/07")
    assert "분만 86일" in note(a1) and "수정 1회(11/07)" in note(a1)
    assert next(h for h in hs if h["animal_id"] == "b7")["dnb"]


def test_heat_dispatch_roles_and_no_resend(tmp_path):
    store = CloudStore(tmp_path / "c", Fernet.generate_key(), ADMIN)
    me, _ = store.upsert_user(ADMIN, "pw")
    (store.user_dir(me.uid) / "data").mkdir(parents=True)
    _db(store.user_dir(me.uid) / "data" / "smaxtec.db", ROWS)
    store.set_contacts("f1", [{"role": "수정사", "name": "박수정", "phone": "010-5555-6666"},
                              {"role": "딜러", "name": "김딜러", "phone": "010-1111-2222"}], ADMIN, "가나")
    fp = FakePush()
    store.add_sub("u:" + me.uid, fp.sub("admin"))
    r1 = store.new_receiver("01055556666", "박수정")
    store.add_sub("r:" + r1["id"], fp.sub("ai"))
    assert run(store, NOW, sender=fp)["note"] == "꺼짐" and not fp.sent
    store.kv_set("heat_enabled", "1")
    sm = run(store, NOW, sender=fp)                         # 시험 모드(기본): 관리자 기기로만
    assert {e for e, _ in fp.sent} == {"https://fcm.googleapis.com/fcm/send/admin"}
    assert any(d["title"].startswith("[시험] 박수정 님께 갈 · 발정 1두") for _, d in fp.sent)
    assert "김딜러" in sm["no_device"]                       # 딜러는 링크 없음
    fp.sent.clear()
    store.kv_set("heat_test", "0")
    run(store, NOW, sender=fp)
    ai = [d for e, d in fp.sent if e.endswith("/ai")]
    assert len(ai) == 1 and ai[0]["url"] == f"/c/{r1['token']}/heat" and ai[0]["body"].startswith("가나목장: 118(분만 86일")
    fp.sent.clear()
    run(store, NOW + timedelta(hours=6), sender=fp)          # 같은 발정은 다시 안 보냄
    assert not [d for e, d in fp.sent if e.endswith("/ai")]
    # 업무요청 역할에서 수정사 제외(기본)
    from smxmon.heat_dispatch import DEFAULT_ORDER_ROLES
    assert "수정사" not in DEFAULT_ORDER_ROLES


def test_heat_pages(tmp_path):
    from smxmon.cloud.jobs import Runner
    from smxmon.cloud.web import create_app
    from smxmon.config import load_settings
    store = CloudStore(tmp_path / "c", Fernet.generate_key(), ADMIN)
    me, _ = store.upsert_user(ADMIN, "pw")
    (store.user_dir(me.uid) / "data").mkdir(parents=True)
    now = datetime.now()
    rows = [("f1", "a1", "heat", "HEAT", (now - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S"), None)]
    _db(store.user_dir(me.uid) / "data" / "smaxtec.db", rows)
    store.set_contacts("f1", [{"role": "수정사", "name": "박수정", "phone": "010-5555-6666"}], ADMIN, "가나")
    r1 = store.new_receiver("01055556666", "박수정")
    base = dataclasses.replace(load_settings(env_file=tmp_path / "none.env"), log_dir=tmp_path / "l")
    app = create_app(store, base, secure_cookie=False, runner=Runner(base, store), verify_login=lambda u, p: 0)
    app.testing = True
    c = app.test_client()
    page = c.get(f"/c/{r1['token']}/heat").get_data(as_text=True)
    assert "박수정 님 담당 목장 발정 — 1두" in page and "가나목장" in page and "118" in page
    key = f"f1|a1|{rows[0][4][:16]}"
    assert c.post(f"/c/{r1['token']}/heat/ack", json={"key": key, "on": 1}).status_code == 200
    assert "확인 취소" in c.get(f"/c/{r1['token']}/heat").get_data(as_text=True)
    c.post("/login", data={"username": ADMIN, "password": "pw"})
    t = c.get("/heat").get_data(as_text=True)
    assert "발정 모아보기 — 1두" in t and "확인함" in t                 # 팀원 '확인함'은 따로
    acc = c.get("/account").get_data(as_text=True)
    assert "관리자 — 발정 알림" in acc and "수정사" in acc
    r = c.post("/admin/heat/now", data={"csrf": __import__("re").search(r'name="csrf" value="([^"]+)"', acc).group(1)}).get_data(as_text=True)
    assert "꺼져 있어 시험으로만 실행" in r
