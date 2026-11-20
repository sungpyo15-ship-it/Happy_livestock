"""데모 데이터로 방문요약 1장·모바일 대시보드 생성 검증."""
import json
import re
from datetime import datetime

import openpyxl

from smxmon.analysis import analyze
from smxmon.config import load_toml
from smxmon.dashboard import build_dashboard
from smxmon.db import Database
from smxmon.demo import build_demo
from smxmon.normalize import AlertTypeMapper
from smxmon.visit import build_visit_workbook, checklist, visit_targets


def test_visit_and_dashboard(tmp_path):
    as_of = datetime(2026, 11, 19, 5, 59)
    build_demo(tmp_path / "d.db", as_of)
    mapper = AlertTypeMapper(load_toml("alert_types.toml"))
    with Database(tmp_path / "d.db") as db:
        an = analyze(db.conn, as_of, mapper, load_toml("rules.toml"))
    targets = visit_targets(an)
    assert targets and all(t.grade in ("긴급", "우선") for t in targets)
    sara = next(f for f in an.farms if f.farm_id == "1001")
    assert any(t == "사료·급여" for t, _ in checklist(sara))      # 그룹 반추감소·집단신호 → 사료 점검
    assert sara.top_animals and sara.behaviour

    out = build_visit_workbook(an, targets, mapper, tmp_path / "v.xlsx")
    wb = openpyxl.load_workbook(out)
    assert len(wb.sheetnames) == len(targets)
    ws = wb.worksheets[0]
    assert ws.page_setup.fitToHeight == 1 and ws.page_setup.orientation == "portrait"
    assert "현장 점검 체크리스트" in " ".join(str(c.value) for row in ws.iter_rows() for c in row if c.value)

    html = build_dashboard(an, mapper, tmp_path / "d.html").read_text(encoding="utf-8")
    assert "http://" not in html and "https://" not in html            # 외부 연결 없음
    data = json.loads(re.search(r"const D = (\{.*?\});\n", html, re.S).group(1))
    assert len(data["farms"]) == len(an.farms) and data["farms"][0]["checklist"]


def test_transition(tmp_path):
    from smxmon.transition import INDICATORS
    as_of = datetime(2026, 11, 19, 5, 59)
    build_demo(tmp_path / "d.db", as_of)
    mapper = AlertTypeMapper(load_toml("alert_types.toml"))
    with Database(tmp_path / "d.db") as db:
        an = analyze(db.conn, as_of, mapper, load_toml("rules.toml"))
    dairy = [f for f in an.farms if f.transition and f.transition.n]
    assert len(dairy) >= 4 and an.transition_bench["n"] >= 3
    sara = next(f for f in an.farms if f.farm_id == "1001").transition
    assert sara.rates["RUMINATION_DROP"] > an.transition_bench["RUMINATION_DROP"]   # 시나리오대로 반추감소 높음
    assert sara.flags and sara.upcoming and sara.fresh
    assert all(0 <= (sara.rates[c] or 0) <= 100 for c, _ in INDICATORS)
    # 분만 후 21일이 안 지난 소는 발생률 분모에서 빠짐
    assert all(x["dim"] < 21 for x in sara.fresh)
    # 12개월 성적: 90일보다 분만 두수 많고, 월별 추이 12개월 내외
    assert sara.n_year > sara.n * 2 and sara.healthy_year is not None
    assert 10 <= len(sara.monthly) <= 13
    hanwoo = next(f for f in an.farms if f.farm_id == "1004").transition
    assert hanwoo.n == 0 and hanwoo.healthy_pct is None


def test_trials_detect_effect(tmp_path):
    """데모: 마바목장 착유우 21일 전부터 반추 +25분 → 대조보정 후 '개선(유의)', 다른 지표는 과잉 판정 없음."""
    from smxmon.demo import build_demo_trials
    from smxmon.trials import compute_all, rate_p, welch_p
    from smxmon.trials_report import build_trials_workbook
    as_of = datetime(2026, 11, 19, 5, 59)
    build_demo(tmp_path / "d.db", as_of)
    tp = build_demo_trials(tmp_path / "t.xlsx", as_of)
    mapper = AlertTypeMapper(load_toml("alert_types.toml"))
    with Database(tmp_path / "d.db") as db:
        res = compute_all(db.conn, as_of, mapper, load_toml("rules.toml"), tp)
    done = next(r for r in res if r.trial.farm_name.startswith("마바"))
    rum = next(m for m in done.beh if m.key == "rum")
    assert 18 < rum.net < 32 and rum.p < 0.01 and rum.verdict == "개선(유의)"
    assert all(m.verdict in ("차이 불분명", "참고", "개선 경향", "악화 경향") for m in done.beh if m.key != "rum")
    running = next(r for r in res if r.trial.farm_name.startswith("가나"))
    assert running.status.startswith("진행중") and not running.beh
    assert build_trials_workbook(res, tmp_path / "o.xlsx").exists()
    # 통계 기준값
    assert abs(welch_p(list(range(1, 11)), list(range(3, 13))) - 0.1569) < 1e-3
    assert abs(rate_p(10, 10, 25, 10) - 0.0167) < 1e-3


def test_history_cumulative_monthly(tmp_path):
    as_of = datetime(2026, 11, 19, 5, 59)
    build_demo(tmp_path / "d.db", as_of)
    with Database(tmp_path / "d.db") as db:
        an = analyze(db.conn, as_of, AlertTypeMapper(load_toml("alert_types.toml")), load_toml("rules.toml"))
    f = next(x for x in an.farms if x.farm_id == "1001")
    h = f.history
    assert h.cum[90]["건강 전체"] >= h.cum[30]["건강 전체"] > 0          # 90일 ≥ 30일
    assert h.months[-1] == "2026-11" and len(h.months) == 12
    rum = dict(zip(h.months, h.monthly["반추 감소"]))
    assert rum["2026-09"] > rum["2026-04"]                               # 데모: 9~10월 사료 변경 후 반추감소 증가
    assert {r for r in (x.history.rank.get(90) for x in an.farms) if r} == set(range(1, h.ranked_of + 1))
    assert an.history_bench[90]["건강 전체"] > 0
