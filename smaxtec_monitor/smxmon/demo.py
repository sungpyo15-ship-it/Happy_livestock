"""가상 데이터 생성기: 실제 사이트 연결 전 리포트·판정 로직을 확인하기 위한 시뮬레이션.

실제 수집과 같은 경로(upsert_alert → resolve_missing)를 2시간 간격으로 흉내냄.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .db import Database

RAW = {
    "TEMP_HIGH": "Temperature increase", "RUMINATION_DROP": "Rumination decrease",
    "DRINKING_ABNORMAL": "Drinking cycles decrease", "PH_LOW": "pH decrease",
    "HEAT": "Heat", "CALVING": "Calving",
}


@dataclass
class _Ev:
    farm_id: str
    animal: str
    code: str
    occurred: datetime
    resolved: datetime | None


def build_demo(db_path: Path, as_of: datetime, days: int = 10, seed: int = 7) -> Path:
    rnd = random.Random(seed)
    db_path.unlink(missing_ok=True)
    farms = [  # id, 이름, 두수, 시나리오
        ("1001", "가나목장(낙농)", 150, "sara"),
        ("1002", "다라목장(낙농)", 95, "fever"),
        ("1003", "마바목장(낙농)", 210, "surge"),
        ("1004", "사아한우(번식)", 80, "normal"),
        ("1005", "자차목장(낙농)", 60, "long"),
        ("1006", "카타한우(비육)", 120, "normal"),
        ("1007", "파하목장(낙농)", 45, "normal"),
    ]
    start = as_of - timedelta(days=days)
    events: list[_Ev] = []

    def add(fid, herd, code, t, dur_h):
        animal = f"{rnd.randint(1, herd):03d}"
        events.append(_Ev(fid, animal, code, t.replace(second=0, microsecond=0),
                          None if dur_h is None else t + timedelta(hours=dur_h)))
        return animal

    for fid, _, herd, sc in farms:
        rate = {"HEAT": herd * 0.012, "CALVING": herd * 0.004, "TEMP_HIGH": herd * 0.010,
                "RUMINATION_DROP": herd * 0.008, "DRINKING_ABNORMAL": herd * 0.004, "PH_LOW": herd * 0.002}
        t = start
        while t < as_of:
            for code, lam in rate.items():
                if rnd.random() < lam / 12:  # 2시간 구간 확률
                    add(fid, herd, code, t + timedelta(minutes=rnd.randint(0, 119)), rnd.uniform(3, 30))
            t += timedelta(hours=2)
        if sc == "sara":  # 마지막 24h: 8시간 내 반추감소 7두 + pH 저하 3두
            base = as_of - timedelta(hours=14)
            for k in range(7):
                add(fid, herd, "RUMINATION_DROP", base + timedelta(minutes=70 * k), rnd.uniform(6, 20))
            for k in range(3):
                add(fid, herd, "PH_LOW", base + timedelta(minutes=90 * k + 30), rnd.uniform(4, 12))
        elif sc == "fever":  # 10시간 내 체온상승 5두
            base = as_of - timedelta(hours=11)
            for k in range(5):
                a = add(fid, herd, "TEMP_HIGH", base + timedelta(minutes=120 * k), rnd.uniform(8, 24))
                if k % 2 == 0:
                    events.append(_Ev(fid, a, "RUMINATION_DROP", base + timedelta(minutes=120 * k + 40), None))
        elif sc == "surge":  # 전일 대비 급증(분산 발생)
            for k in range(9):
                add(fid, herd, rnd.choice(["TEMP_HIGH", "DRINKING_ABNORMAL", "RUMINATION_DROP"]),
                    as_of - timedelta(hours=rnd.uniform(0.5, 23)), rnd.uniform(3, 12))
        elif sc == "long":  # 3~4일째 해제 안 되는 알림
            add(fid, herd, "RUMINATION_DROP", as_of - timedelta(hours=80), None)
            add(fid, herd, "TEMP_HIGH", as_of - timedelta(hours=60), None)

    with Database(db_path) as db:
        t = start
        while t <= as_of:
            run = db.start_run(t)
            for fid, name, herd, _ in farms:
                db.upsert_farm(fid, name, t, herd)
                seen = []
                for e in events:
                    if e.farm_id == fid and e.occurred <= t and (e.resolved is None or e.resolved > t):
                        seen.append(db.upsert_alert(run_id=run, now=t, farm_id=fid, animal_id=e.animal,
                                                    alert_type=e.code, alert_type_raw=RAW.get(e.code, e.code),
                                                    occurred_at=e.occurred).alert_id)
                db.resolve_missing(fid, seen, t)
                db.mark_farm_ok(fid, t)
            db.commit()
            db.finish_run(run, t + timedelta(minutes=4), "ok", farms_ok=len(farms))
            t += timedelta(hours=2)
    return db_path
