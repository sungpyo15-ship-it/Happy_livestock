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


def test_validate_backtest(tmp_path):
    """진단이 건강알림 뒤에 따라오는 가상 자료 → 긴급 판정 뒤 진단이 정상보다 많아야 함."""
    import random
    import sqlite3
    from datetime import timedelta
    from smxmon.validate import run
    as_of = datetime(2026, 11, 20, 6, 0)
    build_demo(tmp_path / "d.db", as_of)
    c = sqlite3.connect(tmp_path / "d.db")
    random.seed(3)
    rows = c.execute("SELECT farm_id, animal_id, occurred_at FROM alerts WHERE alert_type IN ('TEMP_HIGH','RUMINATION_DROP')").fetchall()
    for f, a, t in rows:
        if random.random() < 0.1:
            tt = (datetime.strptime(t, "%Y-%m-%d %H:%M:%S") + timedelta(hours=30)).strftime("%Y-%m-%d %H:%M:%S")
            c.execute("INSERT INTO alerts(farm_id,animal_id,alert_type,alert_type_raw,occurred_at,first_seen_at,last_seen_at,"
                      "status,detail) VALUES(?,?,?,?,?,?,?,?,?)",
                      (f, a, "MANAGEMENT", "diagnosis", tt, tt, tt, "event", json.dumps({"diagnosis_key": "MASTITIS"})))
    c.execute("INSERT INTO alerts(farm_id,animal_id,alert_type,alert_type_raw,occurred_at,first_seen_at,last_seen_at,status,detail)"
              " VALUES('1001','x','MANAGEMENT','false_positive','2026-10-01 10:00:00','2026-10-01 10:00:00','2026-10-01 10:00:00','event','{}')")
    from smxmon.feedback import ensure_schema
    ensure_schema(c)
    c.execute("INSERT INTO fb_notes VALUES('n1','1001','a1','2026-09-01 10:00:00','UDDER','MASTITIS','[]','')")
    c.execute("INSERT INTO fb_notes VALUES('n2','1001','a1','2026-09-02 10:00:00','REPRODUCTION','X','[]','')")
    c.executemany("INSERT INTO fb_todos VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  [("t1", "1001", "a1", "clinical_condition", "401_mastitis", "user-ack", None, None, "n1", '["_id"]', ""),
                   ("t2", "1001", "a2", "clinical_condition", "401_mastitis", "user-nack", None, None, None, '["_id"]', "")])
    c.commit()
    c.close()
    out, text = run([("t", sqlite3.connect(f"file:{tmp_path / 'd.db'}?mode=ro", uri=True))], tmp_path / "v.xlsx",
                    days=200, anon=True, now=as_of + timedelta(days=10))
    wb = openpyxl.load_workbook(out)
    assert wb.sheetnames == ["결론", "등급·신호별", "기준값 시험", "놓침(재현율)", "개선안 비교", "농장 확인 처리", "목장별", "자료 점검"]
    assert "질병 메모 1건" in text and "유방염 의심: 확인함 1, 해당 없음 1" in text
    v = {r[0].value: r for r in wb["개선안 비교"].iter_rows(min_row=2) if r[0].value}
    assert v["A 급증 강화"][8].value <= v["현재"][8].value          # 급증 % 감소
    assert wb["목장별"]["A2"].value == "목장01"            # 익명
    g = {r[0].value: r for r in wb["등급·신호별"].iter_rows(min_row=2) if r[0].value}
    assert g["긴급"][3].value > g["정상"][3].value          # 진단두수/100두
    assert "오경보 표시 1건" in text and "진단" in text


def test_dhi_upload_and_link(tmp_path):
    """검정성적: 머리글 자동 인식(개/ml 체세포, 소수 유지방), 개체 연결, 알림 소 유량 차이·손실 추정."""
    import sqlite3
    from openpyxl import Workbook
    from smxmon.dhi import farm_dhi, parse, store_rows
    as_of = datetime(2026, 11, 20, 6, 0)
    build_demo(tmp_path / "d.db", as_of)
    c = sqlite3.connect(tmp_path / "d.db")
    fid = "1001"
    for aid in {r[0] for r in c.execute("SELECT animal_id FROM alerts WHERE farm_id=?", (fid,))}:   # 데모엔 개체표가 없어 만들어 넣음
        c.execute("INSERT OR REPLACE INTO animals VALUES (?,?,?,?,?,?,?,?)", (fid, aid, f"KR00212{aid:0>7}", f"c{aid}", "착유우", "", 1, "x"))
    animals = c.execute("SELECT animal_id, label FROM animals WHERE farm_id=? AND label IS NOT NULL", (fid,)).fetchall()
    alerted = {r[0] for r in c.execute("SELECT DISTINCT animal_id FROM alerts WHERE farm_id=? AND alert_type IN "
                                       "('TEMP_HIGH','RUMINATION_DROP') AND occurred_at > '2026-10-06' AND occurred_at <= '2026-11-06'", (fid,))}
    wb = Workbook()
    ws = wb.active
    ws.append(["2026년 11월 검정성적표"])
    ws.append(["관리번호", "산차", "비유일수", "305일유량", "유량(kg)", "유지방률", "유단백(%)", "체세포수", "MUN"])
    for i, (aid, lab) in enumerate(animals):
        milk = 30 if aid in alerted else 36
        ws.append([lab, 2, 40 + (i % 4) * 80, 11000, milk, 0.040, 3.2, 150000 + i * 1000, 13])
    ws.append(["합계", None, None, None, None])
    wb.save(tmp_path / "dhi.xlsx")
    p = parse(tmp_path / "dhi.xlsx", "2026-11-05")
    assert p["found"]["milk"] == "유량(kg)" and p["found"]["label"] == "관리번호"       # 305일유량 무시
    assert p["test_date"] == "2026-11-05" and len(p["rows"]) == len(animals)
    assert abs(p["rows"][0]["fat"] - 4.0) < 0.01 and p["rows"][0]["scc"] == 150     # 0.04→4.0%, 개/ml→천
    st = store_rows(c, fid, p)
    assert st["matched"] == len(animals)
    d = farm_dhi(c, fid, {"TEMP_HIGH", "RUMINATION_DROP"}, 1100)
    t = d["tests"][-1]
    assert t["alert_cows"] > 0 and t["milk_diff"] == -6.0 and t["loss_month"] > 0
    c.row_factory = sqlite3.Row
    an = analyze(c, as_of, AlertTypeMapper(load_toml("alert_types.toml")), load_toml("rules.toml"))
    c.close()
    f = next(s for s in an.farms if s.farm_id == fid)
    assert f.dhi["tests"][-1]["n"] == len(animals)
    from smxmon.report import build_excel
    out = build_excel(an, AlertTypeMapper(load_toml("alert_types.toml")), load_toml("rules.toml"), tmp_path / "r.xlsx")
    assert openpyxl.load_workbook(out)["검정성적"]["A5"].value
