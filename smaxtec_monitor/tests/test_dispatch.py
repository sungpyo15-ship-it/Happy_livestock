"""자동 업무지시 알림: 아침 전체 → 오후엔 신규·우선→긴급만, 시험 모드, 담당자 페이지·완료 보고."""
import dataclasses
import json
import os
from datetime import datetime

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import ec

from smxmon.cloud.store import CloudStore
from smxmon.cloud.webpush import _pub_bytes, b64u, decrypt
from smxmon.dispatch import run

ADMIN = "boss@x.kr"


def _farm(fid, name, grade, score):
    return {"id": fid, "name": name, "grade": grade, "score": score, "reasons": ["집단신호 5두/6시간"], "headline": ["h"],
            "guides": ["temp_high"], "animals": [{"label": "118", "types": "체온 상승"}], "checklist0": None,
            "cluster": "체온 상승 다발"}


def _state(store, uid, as_of, farms):
    p = store.user_dir(uid) / "reports"
    p.mkdir(parents=True, exist_ok=True)
    (p / "latest_state.json").write_text(json.dumps({"asOf": as_of, "farms": farms}, ensure_ascii=False), encoding="utf-8")


class FakePush:
    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.auth = os.urandom(16)
        self.sent = []

    def sub(self, n):
        return {"endpoint": f"https://fcm.googleapis.com/fcm/send/{n}", "p256dh": b64u(_pub_bytes(self.key.public_key())),
                "auth": b64u(self.auth)}

    def __call__(self, vapid, sub, data, contact):
        from smxmon.cloud.webpush import encrypt
        body = encrypt(json.dumps(data, ensure_ascii=False).encode(), sub["p256dh"], sub["auth"])
        self.sent.append((sub["endpoint"], json.loads(decrypt(body, self.key, self.auth))))   # 실제 암호화·복호화 거침
        return 201, "ok"


def test_dispatch_am_pm(tmp_path):
    store = CloudStore(tmp_path / "c", Fernet.generate_key(), ADMIN)
    me, _ = store.upsert_user(ADMIN, "pw")
    store.set_contacts("f1", [{"role": "딜러", "name": "김딜러", "phone": "010-1111-2222"}], ADMIN, "가나")
    store.set_contacts("f2", [{"role": "딜러", "name": "김딜러", "phone": "010-1111-2222"}], ADMIN, "다라")
    store.set_contacts("f3", [{"role": "판매과장", "name": "이과장", "phone": "010-3333-4444"}], ADMIN, "마바")
    fp = FakePush()
    rcv = store.new_receiver("01011112222", "김딜러")
    store.add_sub("r:" + rcv["id"], fp.sub("dealer"))
    store.add_sub("u:" + me.uid, fp.sub("admin"))
    # 아침 06:40 판정: 가나 긴급, 다라 우선, 마바 관찰
    _state(store, me.uid, "2026-11-23 06:40", [_farm("f1", "가나", "긴급", 70), _farm("f2", "다라", "우선", 25),
                                                _farm("f3", "마바", "관찰", 10)])
    # 꺼져 있으면 아무것도 안 감
    assert run(store, "am", datetime(2026, 11, 23, 8, 0), sender=fp)["note"] == "꺼짐" and not fp.sent
    store.kv_set("dispatch_enabled", "1")
    # 시험 모드(기본): 관리자 기기로만
    sm = run(store, "am", datetime(2026, 11, 23, 8, 0), sender=fp)
    assert sm["test"] and {e for e, _ in fp.sent} == {"https://fcm.googleapis.com/fcm/send/admin"}
    assert fp.sent[0][1]["title"].startswith("[시험] 김딜러")
    assert not any(k.startswith("act:") for k in store.notes(me.uid))          # 시험은 조치 기록 안 남김
    # 실제 발송
    fp.sent.clear()
    store.kv_set("dispatch_test", "0")
    sm = run(store, "am", datetime(2026, 11, 23, 8, 0), sender=fp)
    dealer = [d for e, d in fp.sent if e.endswith("dealer")]
    assert len(dealer) == 1 and dealer[0]["title"] == "업무요청 긴급 1 · 우선 1" and dealer[0]["url"] == f"/c/{rcv['token']}"
    body = dealer[0]["body"].split("\n")
    assert body[0].startswith("[긴급] 가나 · 집단신호") and body[1].startswith("  → 체온 상승:") and body[2].startswith("[우선] 다라")
    assert sm["contacts"] == 1 and sm["farms"] == 2                            # 마바(관찰)는 대상 아님
    d = store.dispatches(phone="01011112222")[0]
    assert "가나 — 긴급(70점)" in d["text"] and "다라 — 우선(25점)" in d["text"] and "오늘 중" in d["text"]
    notes = store.notes(me.uid)
    assert notes[f"act:f1:d{d['id']}"]["acts"] == ["담당자 업무지시"]
    # 오후 12:40 판정: 다라 우선→긴급, 마바 관찰→우선(신규), 가나 그대로 긴급
    fp.sent.clear()
    _state(store, me.uid, "2026-11-23 12:40", [_farm("f1", "가나", "긴급", 72), _farm("f2", "다라", "긴급", 45),
                                                _farm("f3", "마바", "우선", 22)])
    sm = run(store, "pm", datetime(2026, 11, 23, 14, 0), sender=fp)
    pm = store.dispatches(day="2026-11-23")
    kim = next(x for x in pm if x["slot"] == "pm" and x["name"] == "김딜러")
    assert [f["name"] for f in kim["farms"]] == ["다라"] and kim["farms"][0]["note"] == "(긴급으로 올라감)"
    lee = next(x for x in pm if x["slot"] == "pm" and x["name"] == "이과장")
    assert [f["note"] for f in lee["farms"]] == ["(신규)"] and lee["devices"] == 0      # 이과장은 알림 미설정
    assert "이과장" in sm["no_device"]
    # 오래된 판정(4시간 초과)이면 보내지 않음
    sm = run(store, "pm", datetime(2026, 11, 23, 18, 0), sender=fp)
    assert sm["farms"] == 0 and sm["stale"] == [ADMIN]


def test_receiver_page_and_ack(tmp_path):
    from smxmon.cloud.jobs import Runner
    from smxmon.cloud.web import create_app
    from smxmon.config import load_settings
    store = CloudStore(tmp_path / "c", Fernet.generate_key(), ADMIN)
    me, _ = store.upsert_user(ADMIN, "pw")
    store.set_contacts("f1", [{"role": "딜러", "name": "김딜러", "phone": "010-1111-2222"}], ADMIN, "가나")
    rcv = store.new_receiver("01011112222", "김딜러")
    _state(store, me.uid, datetime.now().strftime("%Y-%m-%d %H:%M"), [_farm("f1", "가나", "긴급", 70)])
    store.kv_set("dispatch_enabled", "1")
    store.kv_set("dispatch_test", "0")
    run(store, "am", sender=FakePush())
    base = dataclasses.replace(load_settings(env_file=tmp_path / "none.env"), log_dir=tmp_path / "l")
    app = create_app(store, base, secure_cookie=False, runner=Runner(base, store), verify_login=lambda u, p: 0)
    app.testing = True
    c = app.test_client()
    assert c.get("/c/nope-nope-nope-nope-nope").status_code == 404
    page = c.get(f"/c/{rcv['token']}").get_data(as_text=True)
    assert "김딜러 님 업무요청" in page and "가나" in page and "완료 보고" in page
    assert f'/c/{rcv["token"]}/manifest.webmanifest' in page
    m = c.get(f"/c/{rcv['token']}/manifest.webmanifest").json
    assert m["start_url"] == f"/c/{rcv['token']}"
    fp = FakePush()
    assert c.post(f"/c/{rcv['token']}/subscribe", json=fp.sub("x")).status_code == 200
    assert len(store.subs("r:" + rcv["id"])) == 1
    did = store.dispatches(phone="01011112222")[0]["id"]
    assert c.post(f"/c/{rcv['token']}/ack", json={"did": did, "farm_id": "f1"}).status_code == 200
    assert store.notes(me.uid)[f"act:f1:d{did}"]["res"] == "완료 보고"
    assert "완료 보고함" in c.get(f"/c/{rcv['token']}").get_data(as_text=True)
    # 링크를 새로 만들면 예전 링크·기기 끊김
    store.new_receiver("01011112222", "김딜러")
    assert c.get(f"/c/{rcv['token']}").status_code == 404 and not store.subs("r:" + rcv["id"])
    assert len(c.get("/push/key").json["key"]) > 80


def test_dispatch_tells_why_nothing_sent(tmp_path):
    """보낼 대상이 없으면 시험 모드에선 관리자에게 이유 알림 + '지금 실행'은 하루 지난 판정도 사용."""
    store = CloudStore(tmp_path / "c", Fernet.generate_key(), ADMIN)
    me, _ = store.upsert_user(ADMIN, "pw")
    fp = FakePush()
    store.add_sub("u:" + me.uid, fp.sub("admin"))
    store.kv_set("dispatch_enabled", "1")          # 시험 모드(기본)
    # 1) 판정 자료 없음
    run(store, "am", datetime(2026, 11, 23, 8, 0), sender=fp)
    assert fp.sent[-1][1]["title"] == "[시험] 보낼 업무요청 없음" and "판정 자료 없음" in fp.sent[-1][1]["body"]
    # 2) 긴급 목장은 있는데 담당자 미등록 → 이유 + 팀원 요약은 감
    _state(store, me.uid, "2026-11-23 06:40", [_farm("f1", "가나", "긴급", 70)])
    fp.sent.clear()
    sm = run(store, "am", datetime(2026, 11, 23, 8, 0), sender=fp)
    assert sm["targets"] == 1 and sm["no_contact"] == ["가나"] and sm["farms"] == 0
    assert fp.sent and fp.sent[0][1]["title"].startswith("오늘 할 일")          # 관리자 본인 '오늘 할 일' 요약
    # 3) 밤 22시: 자동은 4시간 넘은 판정이라 안 보냄, '지금 실행'은 보냄(24시간까지)
    store.set_contacts("f1", [{"role": "딜러", "name": "김딜러", "phone": "010-1111-2222"}], ADMIN, "가나")
    fp.sent.clear()
    assert run(store, "am", datetime(2026, 11, 23, 22, 0), sender=fp)["farms"] == 0
    assert "오래됨" in fp.sent[-1][1]["body"]
    fp.sent.clear()
    sm = run(store, "am", datetime(2026, 11, 23, 22, 0), sender=fp, manual=True)
    assert sm["farms"] == 1 and fp.sent[0][1]["title"].startswith("[시험] 김딜러")



def test_one_device_team_and_receiver(tmp_path):
    """같은 휴대폰이 팀원 알림과 담당자 알림을 둘 다 켜도 서로 덮어쓰지 않음 + 예전 구조 자동 변환."""
    import sqlite3
    root = tmp_path / "c"
    root.mkdir()
    old = sqlite3.connect(root / "cloud.db")   # 예전 구조(기기당 1명)
    old.execute("CREATE TABLE push_subs (endpoint TEXT PRIMARY KEY, owner TEXT NOT NULL, p256dh TEXT NOT NULL, auth TEXT NOT NULL,"
                " ua TEXT, created TEXT, last_ok TEXT, fails INTEGER DEFAULT 0)")
    old.execute("INSERT INTO push_subs VALUES ('https://x/1','u:a','k','a','',NULL,NULL,0)")
    old.commit()
    old.close()
    store = CloudStore(root, Fernet.generate_key(), ADMIN)
    assert [x["endpoint"] for x in store.subs("u:a")] == ["https://x/1"]
    sub = {"endpoint": "https://x/1", "p256dh": "k", "auth": "a"}
    store.add_sub("r:dealer", sub)
    assert store.subs("u:a") and store.subs("r:dealer")
    store.sub_result("https://x/1", False, gone=True)
    assert not store.subs("u:a") and not store.subs("r:dealer")
