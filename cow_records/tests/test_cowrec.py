import json
import sqlite3
from datetime import date, datetime, timedelta

from cowrec.breeding import build_records, farm_kpis
from cowrec.build import build_all
from cowrec.health import farm_health
from cowrec.store import Store
from smxmon.db import Database

TODAY = date(2026, 11, 20)


def _db(tmp_path, events):
    db = Database(tmp_path / "smx.db")
    now = datetime(2026, 11, 20, 6, 0)
    db.upsert_farm("F", "테스트목장", now, 50)
    run = db.start_run(now)
    for i, (aid, raw, d, det) in enumerate(events):
        db.upsert_alert(run_id=run, now=now, farm_id="F", animal_id=aid, alert_type="MANAGEMENT", alert_type_raw=raw,
                        occurred_at=datetime.combine(d, datetime.min.time()) + timedelta(hours=8), point_event=True,
                        ext_id=f"e{i}", detail=json.dumps(det) if det else None)
    db.commit()
    return db


def test_pregnant_cow(tmp_path):
    c = TODAY - timedelta(days=200)
    db = _db(tmp_path, [("a", "calving_confirmation", c, {"number": 2}), ("a", "heat", c + timedelta(days=45), None),
                        ("a", "insemination", c + timedelta(days=66), None), ("a", "insemination", c + timedelta(days=87), None),
                        ("a", "pregnancy_result", c + timedelta(days=122), {"pregnant": True})])
    r = build_records(db.conn, "F", TODAY)[0]
    assert (r.status, r.parity, r.n_ai, r.days_open, r.first_ai_dim) == ("pregnant", 2, 2, 87, 66)
    assert r.expected_calving == c + timedelta(days=87 + 280) and r.expected_dry == r.expected_calving - timedelta(days=60)
    k = farm_kpis([r])
    assert (k["ai_per_conception"], k["days_open"], k["pregnant_pct"]) == (2.0, 87, 100.0)


def test_problem_cow_flags(tmp_path):
    c = TODAY - timedelta(days=180)
    ev = [("b", "calving_detection", c, None)] + [("b", "insemination", c + timedelta(days=60 + 21 * k), None) for k in range(3)]
    ev += [("b", "pregnancy_result", c + timedelta(days=150), {"pregnant": False})]
    r = build_records(_db(tmp_path, ev).conn, "F", TODAY)[0]
    assert r.status == "open_neg" and any("미임신" in f for f in r.flags) and r.days_open == 180


def test_health_and_build(tmp_path):
    db = _db(tmp_path, [("a", "calving_confirmation", TODAY - timedelta(days=30), {"number": 1})])
    with Store(tmp_path / "cow.db") as st:
        st.put_note("F", {"_id": "n1", "category": "UDDER", "note_event": "MASTITIS", "note_actions": ["ANTIBIOTICS"],
                          "note_event_comment": "좌전"}, "a", "401_mastitis", "2026-11-10 09:00:00", "now")
        st.put_comment("F", {"_id": "c1", "content": "수의사 진료"}, "a", "clinical_condition", "user-ack", "2026-11-10 10:00:00")
        st.conn.commit()
        h = farm_health(st.conn, db.conn, "F")
    assert [x["what"] for x in h["a"]][:2] == ["임상 의심 알림", "유방염"] and h["a"][1]["action"] == "항생제"
    db.close()
    r = build_all(tmp_path / "smx.db", tmp_path / "cow.db", tmp_path / "out", TODAY, memo_only=True)
    assert r["farms"] == 1 and r["files"][0].exists() and "유방염" in r["app"].read_text(encoding="utf-8")


def test_abort_after_positive_pd(tmp_path):
    c = TODAY - timedelta(days=300)
    ev = [("x", "calving_confirmation", c, {"number": 2}), ("x", "insemination", c + timedelta(days=45), None),
          ("x", "pregnancy_result", c + timedelta(days=118), {"pregnant": True}), ("x", "abort", c + timedelta(days=170), None)]
    r = build_records(_db(tmp_path, ev).conn, "F", TODAY)[0]
    assert r.status == "aborted" and r.flags[0].startswith("유산")
