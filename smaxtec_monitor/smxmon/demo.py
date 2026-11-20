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
        _demo_behaviour(db, farms, as_of, 45, rnd)
        _demo_transition(db, farms, as_of, rnd)
        _demo_year_alerts(db, farms, as_of, rnd)
        db.commit()
    return db_path


def _demo_behaviour(db: Database, farms, as_of: datetime, days: int, rnd: random.Random) -> None:
    """그룹 행동 KPI 가상값 (API 응답 형식 그대로 저장)."""
    for fid, _, herd, sc in farms:
        groups = [("착유우", int(herd * 0.6)), ("건유우", int(herd * 0.1)), ("육성우", int(herd * 0.2))]
        db.upsert_groups(fid, [{"_id": f"{fid}-{i}", "name": n, "animal_ids": ["x"] * k}
                               for i, (n, k) in enumerate(groups)], as_of)
        series = {}
        for i, (name, _k) in enumerate(groups):
            hist = []
            for d in range(days, 0, -1):
                day = (as_of - timedelta(days=d)).date()
                rs, rl = rnd.gauss(185, 8), rnd.gauss(325, 10)
                lying, standing = rnd.gauss(630, 20), rnd.gauss(320, 15)
                if d == 1 and name == "착유우" and sc == "sara":
                    rs, rl = rs - 25, rl - 35              # 그룹 반추 −60분
                if name == "착유우" and sc == "long":
                    lying = rnd.gauss(515, 8)              # 횡와 8.6h
                if d == 1 and name == "착유우" and sc == "fever":
                    lying -= 95
                if fid == "1003" and name == "착유우" and d <= 21:   # 투입비교 데모: 21일 전 첨가제 투입 → 반추·횡와 증가
                    rs, rl, lying = rs + 10, rl + 15, lying + 18
                if d == 1 and name == "육성우" and sc == "normal" and fid == "1006":
                    rs, rl = rs - 20, rl - 25
                hist.append((day, rs, rl, lying, standing, rnd.uniform(0.86, 0.92)))
            vals = []
            for j, (day, rs, rl, ly, st, sy) in enumerate(hist):
                prev = hist[j - 1] if j else hist[j]
                w = hist[max(0, j - 7):j] or [hist[j]]
                avg = lambda k: sum(x[k] for x in w) / len(w)  # noqa: E731
                m = lambda c, p, a: {"current": c, "prev_day": p, "last_7_days": a}  # noqa: E731
                notrum_l = ly - rl
                vals.append({
                    "date": str(day),
                    "rum_while_standing_mean": m(rs, prev[1], avg(1)), "rum_while_lying_mean": m(rl, prev[2], avg(2)),
                    "lying_mean": m(ly, prev[3], avg(3)), "standing_mean": m(st, prev[4], avg(4)),
                    "moving_mean": m(1440 - ly - st, 1440 - prev[3] - prev[4], 1440 - avg(3) - avg(4)),
                    "not_rum_while_standing_mean": m(st - rs, None, None),
                    "not_rum_while_lying_mean": m(notrum_l, None, None),
                    "peak_lying_sync_mean": m(sy, prev[5], avg(5)),
                })
            series[f"{fid}-{i}"] = vals
        db.upsert_behaviour(fid, series, as_of)


def _demo_transition(db: Database, farms, as_of: datetime, rnd: random.Random) -> None:
    """분만·건유·전환기 알림 가상 이력(약 14개월, 여름철 문제 증가)."""
    import json
    run = db.start_run(as_of - timedelta(days=420))
    probs = {"normal": dict(KETOSIS=.08, MILK_FEVER=.04, MASTITIS=.08, TEMP_HIGH=.15, RUMINATION_DROP=.2, pre=.1),
             "sara": dict(KETOSIS=.28, MILK_FEVER=.06, MASTITIS=.1, TEMP_HIGH=.2, RUMINATION_DROP=.45, pre=.3),
             "fever": dict(KETOSIS=.1, MILK_FEVER=.12, MASTITIS=.18, TEMP_HIGH=.35, RUMINATION_DROP=.25, pre=.1)}
    raw = {"KETOSIS": "clinical_condition_403", "MILK_FEVER": "clinical_condition_402",
           "MASTITIS": "clinical_condition_401", "TEMP_HIGH": "health_104", "RUMINATION_DROP": "health_304"}
    n = 0

    def put(fid, aid, code, rawc, t, detail=None):
        nonlocal n
        n += 1
        db.upsert_alert(run_id=run, now=as_of, farm_id=fid, animal_id=aid, alert_type=code, alert_type_raw=rawc,
                        occurred_at=t.replace(second=0, microsecond=0), point_event=True, ext_id=f"demo-tr-{n}",
                        detail=json.dumps(detail) if detail else None)
    for fid, _, herd, sc in farms:
        if "한우" in _:
            continue
        p = probs.get(sc, probs["normal"])
        t = as_of - timedelta(days=420)
        while t < as_of:
            summer = 1.6 if t.month in (7, 8) else 1.0   # 고온기 전환기 문제 증가(데모)
            if rnd.random() < herd * 0.9 / 365:
                aid = f"c{fid}{rnd.randint(1000, 9999)}"
                ct = t + timedelta(hours=rnd.uniform(0, 23))
                parity = rnd.choice([1, 1, 2, 2, 3, 4])
                put(fid, aid, "CALVING", "calving_detection", ct)
                if rnd.random() < .85:
                    put(fid, aid, "MANAGEMENT", "calving_confirmation", ct.replace(hour=0, minute=0), {"number": parity})
                if rnd.random() < p["pre"]:
                    put(fid, aid, "RUMINATION_DROP", "health_302", ct - timedelta(days=rnd.uniform(1, 18)))
                for code, pr in p.items():
                    if code != "pre" and rnd.random() < min(.95, pr * summer * (1.3 if parity == 1 and code == "RUMINATION_DROP" else 1)):
                        put(fid, aid, code, raw[code], ct + timedelta(days=rnd.uniform(0.5, 20)))
            t += timedelta(days=1)
        for k in range(rnd.randint(3, 7)):   # 분만 예정
            aid = f"p{fid}{k}"
            due = (as_of + timedelta(days=rnd.randint(0, 14))).date()
            put(fid, aid, "MANAGEMENT", "dry_off", as_of - timedelta(days=50),
                {"pregnant": True, "expected_calving_date": f"{due}T12:00:00+00:00"})
            if k == 0:
                put(fid, aid, "CALVING", "fertility_105", as_of - timedelta(hours=5))


def build_demo_trials(path: Path, as_of: datetime) -> Path:
    """투입기록 데모 파일."""
    from .trials import ensure_template
    path.unlink(missing_ok=True)
    ensure_template(path)
    from openpyxl import load_workbook
    wb = load_workbook(path)
    ws = wb.active
    ws.append(["마바", (as_of - timedelta(days=21)).date(), "효모배양물 50g/두/일 (데모)", "첨가제", "착유우", 14, 5, "가상 데이터"])
    ws.append(["가나", (as_of - timedelta(days=4)).date(), "TMR 조사료 비율 변경 (데모)", "사료배합", "", 14, 5, "진행중 예시"])
    wb.save(path)
    return path


def _demo_year_alerts(db: Database, farms, as_of: datetime, rnd: random.Random) -> None:
    """월별 추이 데모용 1년치 배경 알림(여름 체온·반추 증가, 9월 가나목장 사료 변경 후 반추 증가)."""
    run = db.start_run(as_of - timedelta(days=370))
    raw = {"TEMP_HIGH": "health_104", "RUMINATION_DROP": "health_305", "DRINKING_ABNORMAL": "health_103",
           "PH_LOW": "feeding_202", "TEMP_LOW": "health_106", "ACTIVITY_DROP": "health_703"}
    n = 0
    for fid, name, herd, sc in farms:
        base = {"TEMP_HIGH": .010, "RUMINATION_DROP": .008, "DRINKING_ABNORMAL": .004, "PH_LOW": .002,
                "TEMP_LOW": .004, "ACTIVITY_DROP": .002}
        d = as_of - timedelta(days=365)
        while d < as_of - timedelta(days=10):          # 최근 10일은 기존 데모 수집이 채움
            for code, rate in base.items():
                m = 1.0
                if d.month in (7, 8) and code in ("TEMP_HIGH", "RUMINATION_DROP", "DRINKING_ABNORMAL"):
                    m = 1.8
                if fid == "1001" and d.month in (9, 10) and code in ("RUMINATION_DROP", "PH_LOW"):
                    m = 2.2
                k = sum(1 for _ in range(herd) if rnd.random() < rate * m)
                for _ in range(k):
                    n += 1
                    db.upsert_alert(run_id=run, now=as_of, farm_id=fid, animal_id=f"y{fid}{rnd.randint(1, herd):04d}",
                                    alert_type=code, alert_type_raw=raw[code], point_event=True, ext_id=f"demo-y-{n}",
                                    occurred_at=(d + timedelta(minutes=rnd.randint(0, 1439))).replace(second=0, microsecond=0))
            d += timedelta(days=1)
