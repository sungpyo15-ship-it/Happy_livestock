"""데모: smxmon 가상 DB 에 발정·수정·임신감정·건유 기록과 smaXtec 메모·코멘트를 추가."""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from pathlib import Path

from smxmon.db import Database
from smxmon.demo import build_demo

from .store import Store


def build(smx_db: Path, cow_db: Path, as_of: datetime, seed: int = 11) -> None:
    build_demo(smx_db, as_of)
    rnd = random.Random(seed)
    cow_db.unlink(missing_ok=True)
    with Database(smx_db) as db, Store(cow_db) as st:
        run = db.start_run(as_of)
        n = 0

        def put(fid, aid, raw, t, detail=None, code="MANAGEMENT"):
            nonlocal n
            n += 1
            db.upsert_alert(run_id=run, now=as_of, farm_id=fid, animal_id=aid, alert_type=code, alert_type_raw=raw,
                            occurred_at=t.replace(second=0, microsecond=0), point_event=True, ext_id=f"cow-{n}",
                            detail=json.dumps(detail) if detail else None)
        calv = db.conn.execute("""SELECT farm_id, animal_id, MAX(occurred_at) FROM alerts
                                  WHERE alert_type_raw='calving_detection' GROUP BY farm_id, animal_id""").fetchall()
        animals = []
        for fid, aid, ts in calv:
            ct = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
            dim = (as_of - ct).days
            label = f"{rnd.randint(100, 999)}"
            db.conn.execute("INSERT OR REPLACE INTO animals VALUES (?,?,?,?,?,?,?,?)",
                            (fid, aid, f"002{rnd.randint(10**8, 10**9 - 1)}", label, "착유우", "Lactating_Cow", 1, "2026-01-01 00:00:00"))
            animals.append((fid, aid, label))
            t = ct + timedelta(days=rnd.randint(35, 55))
            ai_n = 0
            while t < as_of - timedelta(days=2) and ai_n < 5:
                put(fid, aid, "heat", t, {"cycle_length": 21}, "HEAT")
                if (t - ct).days >= 50:
                    put(fid, aid, "insemination", t + timedelta(hours=10))
                    ai_n += 1
                    conceived = rnd.random() < 0.42
                    pd_t = t + timedelta(days=35)
                    if pd_t < as_of:
                        due = (t + timedelta(days=280)).date()
                        put(fid, aid, "pregnancy_result", pd_t, {"pregnant": conceived, **({"expected_calving_date": f"{due}T00:00:00+00:00"} if conceived else {})})
                        if conceived:
                            if t + timedelta(days=220) < as_of:
                                put(fid, aid, "dry_off", t + timedelta(days=220))
                            break
                    else:
                        break
                t += timedelta(days=rnd.choice([21, 21, 22, 42]))
        # 메모·코멘트 (가나·다라목장만 — '메모 쓰는 목장' 시나리오)
        notes = [("UDDER", "MASTITIS", ["ANTIBIOTICS"], "좌전 분방 응고물"), ("METABOLISM", "KETOSIS", ["PROPYLENE_GLYCOL"], ""),
                 ("METABOLISM", "MILK_FEVER_ACUTE", ["CALCIUM"], "기립불능, 칼슘 정맥"), ("REPRODUCTION", "RETAINED_PLACENTA", [], "")]
        for i, (fid, aid, label) in enumerate([a for a in animals if a[0] in ("1001", "1002")][:25]):
            cat, ev, acts, memo = notes[i % len(notes)]
            ts = (as_of - timedelta(days=rnd.randint(3, 200))).strftime("%Y-%m-%d %H:%M:%S")
            st.put_note(fid, {"_id": f"n{i}", "category": cat, "note_event": ev, "note_actions": acts,
                              "note_event_comment": memo, "source": "user-health+"}, aid, None, ts, ts)
            if i % 3 == 0:
                st.put_comment(fid, {"_id": f"c{i}", "content": "수의사 진료, 3일 관찰"}, aid, "clinical_condition", "user-ack", ts)
        st.conn.commit()
        db.commit()
