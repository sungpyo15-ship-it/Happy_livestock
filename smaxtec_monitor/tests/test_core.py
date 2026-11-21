from datetime import datetime, timedelta

import pytest

from smxmon.analysis import analyze
from smxmon.config import load_toml
from smxmon.db import Database
from smxmon.logging_setup import SecretMaskFilter
from smxmon.normalize import AlertTypeMapper, DateParseError, parse_datetime

NOW = datetime(2026, 11, 19, 7, 0)
FMTS = load_toml("selectors.toml")["datetime"]["formats"]


@pytest.fixture
def mapper():
    return AlertTypeMapper(load_toml("alert_types.toml"))


@pytest.fixture
def db():
    d = Database(":memory:")
    d.upsert_farm("F1", "테스트목장", NOW, 100)
    yield d
    d.close()


def test_mapper(mapper):
    assert mapper.map("Temperature increase") == "TEMP_HIGH"
    assert mapper.map("체온 하강") == "TEMP_LOW"
    assert mapper.map("반추 감소") == "RUMINATION_DROP"
    assert mapper.map("Low pH / SARA risk") == "PH_LOW"
    assert mapper.map("분만 후 체온 상승") == "TEMP_HIGH"  # 긴 키워드 우선
    assert mapper.map("Calving") == "CALVING"
    assert mapper.map("무언가 새로운 알림") == "OTHER"


def test_parse_datetime():
    assert parse_datetime("2026-11-19 03:10", FMTS, NOW) == (datetime(2026, 11, 19, 3, 10), False)
    assert parse_datetime("19.11.2026 04:20", FMTS, NOW)[0] == datetime(2026, 11, 19, 4, 20)
    assert parse_datetime("2시간 전", FMTS, NOW) == (datetime(2026, 11, 19, 5, 0), True)
    assert parse_datetime("3 hours ago", FMTS, NOW)[0] == datetime(2026, 11, 19, 4, 0)
    assert parse_datetime("12-31 23:00", FMTS, NOW)[0].year == 2025  # 연도 없는 표기, 미래면 작년
    with pytest.raises(DateParseError):
        parse_datetime("어제쯤", FMTS, NOW)


def test_dedup_and_resolve(db):
    occ = datetime(2026, 11, 19, 3, 10)
    r1 = db.start_run(NOW)
    a = db.upsert_alert(run_id=r1, now=NOW, farm_id="F1", animal_id="101", alert_type="TEMP_HIGH", occurred_at=occ)
    assert a.action == "new"
    # 같은 키 재수집 → 중복 아님
    r2 = db.start_run(NOW + timedelta(hours=2))
    b = db.upsert_alert(run_id=r2, now=NOW + timedelta(hours=2), farm_id="F1", animal_id="101",
                        alert_type="TEMP_HIGH", occurred_at=occ)
    assert (b.alert_id, b.action) == (a.alert_id, "seen")
    # 상대시각 흔들림(+20분) → 허용오차로 같은 알림
    c = db.upsert_alert(run_id=r2, now=NOW, farm_id="F1", animal_id="101", alert_type="TEMP_HIGH",
                        occurred_at=occ + timedelta(minutes=20), tolerance_minutes=30)
    assert c.alert_id == a.alert_id
    assert db.conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 1
    # 다음 수집에서 사라짐 → 해제(추정)
    t3 = NOW + timedelta(hours=4)
    assert db.resolve_missing("F1", [], t3) == 1
    row = db.conn.execute("SELECT * FROM v_alerts").fetchone()
    assert row["status"] == "resolved" and row["resolved_source"] == "inferred"
    assert row["duration_hours"] == pytest.approx(((t3 - occ).total_seconds()) / 3600, abs=0.1)
    # 다시 나타남 → 재활성
    r4 = db.start_run(t3 + timedelta(hours=2))
    d = db.upsert_alert(run_id=r4, now=t3 + timedelta(hours=2), farm_id="F1", animal_id="101",
                        alert_type="TEMP_HIGH", occurred_at=occ)
    assert d.action == "reopened"
    assert db.conn.execute("SELECT COUNT(*) FROM alert_observations").fetchone()[0] == 3


def test_site_resolved_time(db):
    r = db.start_run(NOW)
    x = db.upsert_alert(run_id=r, now=NOW, farm_id="F1", animal_id="7", alert_type="RUMINATION_DROP",
                        occurred_at=NOW - timedelta(hours=10), resolved_at=NOW - timedelta(hours=1))
    assert x.action == "resolved_by_site"
    assert db.conn.execute("SELECT duration_hours FROM v_alerts").fetchone()[0] == 9.0


def _seed(db, items):
    r = db.start_run(NOW - timedelta(days=9))
    db.finish_run(r, NOW - timedelta(days=9), "ok")
    for animal, code, t in items:
        db.upsert_alert(run_id=r, now=NOW, farm_id="F1", animal_id=animal, alert_type=code, occurred_at=t)
    db.mark_farm_ok("F1", NOW)
    db.commit()


def test_surge_and_sara_cluster(db, mapper):
    rules = load_toml("rules.toml")
    items = [("x1", "TEMP_HIGH", NOW - timedelta(days=d, hours=3)) for d in range(2, 9)]  # 평소 하루 1건
    items += [(f"c{k}", "RUMINATION_DROP", NOW - timedelta(hours=10) + timedelta(minutes=60 * k)) for k in range(6)]
    items += [("p1", "PH_LOW", NOW - timedelta(hours=8))]
    _seed(db, items)
    an = analyze(db.conn, NOW, mapper, rules)
    f = an.farms[0]
    assert f.health_d0 == 7 and f.health_d1 == 0
    assert f.surge
    assert f.cluster and f.cluster.n_animals == 6 and "SARA" in f.cluster.interpretation
    assert f.grade == "긴급"


def test_no_cluster_when_spread_out(db, mapper):
    rules = load_toml("rules.toml")
    # 24시간에 고르게 4두 → 12시간 창에는 최대 2두 → 집단 아님
    _seed(db, [(f"a{k}", "TEMP_HIGH", NOW - timedelta(hours=1 + 6.5 * k)) for k in range(4)])
    an = analyze(db.conn, NOW, mapper, rules)
    assert an.farms[0].cluster is None


def test_long_active(db, mapper):
    _seed(db, [("z", "RUMINATION_DROP", NOW - timedelta(hours=60))])
    an = analyze(db.conn, NOW, mapper, load_toml("rules.toml"))
    assert an.farms[0].long_active == 1 and len(an.long_active_alerts) == 1


def test_secret_mask():
    import logging
    rec = logging.LogRecord("x", logging.INFO, "", 0, "login %s / %s", ("me@farm.kr", "S3cret!"), None)
    SecretMaskFilter(["me@farm.kr", "S3cret!"]).filter(rec)
    assert "S3cret" not in rec.getMessage() and "me@farm.kr" not in rec.getMessage()


def test_counts_are_animals_not_events(db, mapper):
    """같은 개체가 24시간에 5번 울려도 1두 → 급증 아님. 서로 다른 5두면 급증."""
    rules = load_toml("rules.toml")
    _seed(db, [("cow1", "RUMINATION_DROP", NOW - timedelta(hours=h)) for h in (1, 3, 5, 7, 9)])
    f = analyze(db.conn, NOW, mapper, rules).farms[0]
    assert (f.health_d0, f.health_events_d0, f.counts_d0["RUMINATION_DROP"]) == (1, 5, 1)
    assert not f.surge
    _seed(db, [(f"c{k}", "TEMP_HIGH", NOW - timedelta(hours=2 + k)) for k in range(5)])
    f = analyze(db.conn, NOW, mapper, rules).farms[0]
    assert f.health_d0 == 6 and f.surge


def test_action_guides():
    from types import SimpleNamespace as NS
    from smxmon.actions import farm_guides, flag_guide, guides
    gs = guides()
    ids = {g["id"] for g in gs}
    assert {"temp_high", "lying", "sync", "group_rum"} <= ids
    assert all(g["check"] and g["act"] for g in gs)
    lying = next(g for g in gs if g["id"] == "lying")
    assert any("침파리" in x for x in lying["check"] + lying["act"])
    g = NS(flags=["누운 시간 부족 8.4h(기준 9h)"], rum_flag=False)
    s = NS(counts_72h={"TEMP_HIGH": 2, "PH_LOW": 0}, counts_d0={}, cluster=None, behaviour=[g])
    assert farm_guides(s) == ["temp_high", "lying"]
    assert flag_guide("함께 눕기 감소 -12%p") == "sync"
