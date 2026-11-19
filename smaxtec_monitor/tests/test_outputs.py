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
