"""목장별 개체 기록부 엑셀: 번식기록지 · 번식성적 · 질병기록."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .breeding import CowRecord

F = "맑은 고딕"
NAVY = "1F4E78"
THIN = Side(style="thin", color="BFBFBF")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
ST_FILL = {"pregnant": "E2EFDA", "dry": "DDEBF7", "bred": "FFF2CC", "open": "FCE4D6", "open_neg": "F8CBAD",
           "fresh": "EDEDED", "heifer": "FFFFFF"}


def _hdr(ws, row, headers, widths):
    for i, (h, w) in enumerate(zip(headers, widths), 1):
        c = ws.cell(row=row, column=i, value=h)
        c.font, c.fill, c.border = Font(name=F, bold=True, color="FFFFFF", size=9), PatternFill("solid", fgColor=NAVY), BOX
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.row_dimensions[row].height = 30


def _row(ws, row, vals, fill=None, wrap=()):
    for i, v in enumerate(vals, 1):
        c = ws.cell(row=row, column=i, value=v)
        c.font, c.border = Font(name=F, size=9), BOX
        c.alignment = Alignment(horizontal="left" if i in wrap else "center", vertical="center", wrap_text=i in wrap)
        if fill:
            c.fill = PatternFill("solid", fgColor=fill)


def _ds(d: date | None) -> str:
    return f"{d:%Y-%m-%d}" if d else ""


def build_farm_book(farm: str, recs: list[CowRecord], kpi: dict, health: dict, today: date, out: Path,
                    beef: bool = False) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "번식기록지"
    ws["A1"] = f"번식기록지 | {farm}"
    ws["A1"].font = Font(name=F, bold=True, size=14)
    ws["A2"] = (f"{today:%Y-%m-%d} 기준 · smaXtec 번식 기록(분만·발정·수정·임신감정·건유) 자동 정리 · "
                "수집 이력(약 13개월) 이전 분만은 '분만일 미확인' · 정액·메모 칸은 수기 입력용")
    ws["A2"].font = Font(name=F, size=8, color="595959")
    hdr = ["관리번호", "이표번호", "그룹", "산차", "분만일", "착유\n일수", "첫발정\n(일)", "발정\n횟수",
           "수정 1", "수정 2", "수정 3", "수정 4+", "수정\n횟수", "첫수정\n(일)", "임신감정", "결과", "수태일\n(추정)",
           "공태\n일수", "분만예정", "건유예정", "상태", "주의", "정액(수기)", "메모(수기)"]
    _hdr(ws, 4, hdr, [12, 14, 10, 5, 11, 6, 6, 5, 11, 11, 11, 11, 5, 6, 11, 6, 11, 6, 11, 11, 14, 26, 12, 16])
    for i, r in enumerate(recs, 5):
        ais = r.ais + [None] * 4
        more = ", ".join(_ds(a) for a in r.ais[3:]) if len(r.ais) > 3 else ""
        _row(ws, i, [r.label, r.official_id, r.group, r.parity or "", _ds(r.calving), r.dim if r.dim is not None else "",
                     r.first_heat_dim if r.first_heat_dim is not None else "", len(r.heats) or "",
                     _ds(ais[0]), _ds(ais[1]), _ds(ais[2]), more, r.n_ai or "",
                     r.first_ai_dim if r.first_ai_dim is not None else "", _ds(r.pd_date),
                     {True: "임신", False: "비임신"}.get(r.pd_result, ""), _ds(r.conception),
                     r.days_open if r.days_open is not None else "", _ds(r.expected_calving),
                     "" if beef else _ds(r.expected_dry), r.status_ko, ", ".join(r.flags), "", ""],
             fill=ST_FILL.get(r.status), wrap=(22, 24))
        if r.flags:
            ws.cell(row=i, column=22).font = Font(name=F, size=9, bold=True, color="C00000")
    ws.freeze_panes = "C5"
    if recs:
        ws.auto_filter.ref = f"A4:{get_column_letter(len(hdr))}{4 + len(recs)}"

    k = wb.create_sheet("번식성적")
    k["A1"] = f"번식 성적 | {farm}"
    k["A1"].font = Font(name=F, bold=True, size=14)
    rows = [("전체 개체", f"{kpi['n']}두 (분만 기록 있음 {kpi['cows']}두)"),
            ("임신우 비율", f"{kpi['pregnant_pct']}%" if kpi["pregnant_pct"] is not None else "-"),
            ("평균 공태일수 (임신우)", f"{kpi['days_open']:.0f}일" if kpi["days_open"] else "-"),
            ("평균 첫 수정 (분만 후)", f"{kpi['first_ai_dim']:.0f}일" if kpi["first_ai_dim"] else "-"),
            ("수태당 수정 횟수", f"{kpi['ai_per_conception']}회" if kpi["ai_per_conception"] else "-"),
            ("평균 분만간격", f"{kpi['calving_interval']:.0f}일" if kpi["calving_interval"] else "-"),
            ("주의 개체 (장기 미임신·무발정)", f"{kpi['problem']}두"),
            ("30일 이내 분만예정", f"{kpi['calving_30d']}두")]
    rows += [(f"상태: {s}", f"{n}두") for s, n in kpi["by_status"].items() if n]
    for i, (a, b) in enumerate(rows, 3):
        k.cell(row=i, column=1, value=a).font = Font(name=F, size=10, bold=True)
        k.cell(row=i, column=2, value=b).font = Font(name=F, size=10)
    k.column_dimensions["A"].width, k.column_dimensions["B"].width = 30, 30
    n = 4 + len(rows)
    for j, t in enumerate([
        "· 공태일수 = 분만일 → 수태일(임신 확인된 마지막 수정일). 임신 전이면 분만일 → 오늘",
        "· 수태당 수정 횟수 = 이번 산차 임신우의 수정 횟수 합 ÷ 임신우 두수",
        "· smaXtec 에 입력된 기록 기준. 수정·감정 입력이 빠진 개체는 실제와 다를 수 있음",
        "· 참고 목표(낙농, 일반적 권장 범위): 공태일수 100~120일, 첫 수정 60~70일, 수태당 수정 2회 이하, 분만간격 13개월 내외",
    ]):
        k.cell(row=n + j, column=1, value=t).font = Font(name=F, size=9, color="595959")

    h = wb.create_sheet("질병기록")
    h["A1"] = f"개체별 질병·치료 기록 | {farm}"
    h["A1"].font = Font(name=F, bold=True, size=14)
    h["A2"] = "출처: smaXtec 메모(질병·조치) · 알림 확인 코멘트 · 진단 기록 · 센서 임상 알림(케토시스·유방염·유열 의심)"
    h["A2"].font = Font(name=F, size=8, color="595959")
    _hdr(h, 4, ["관리번호", "이표번호", "날짜", "출처", "분류", "내용", "조치", "메모"], [12, 14, 16, 14, 10, 18, 18, 40])
    label = {r.animal_id: r for r in recs}
    row = 5
    for aid, items in sorted(health.items(), key=lambda kv: (label[kv[0]].label if kv[0] in label else kv[0] or "")):
        r = label.get(aid)
        for x in items:
            _row(h, row, [r.label if r else aid, r.official_id if r else "", x["date"], x["source"], x["category"],
                          x["what"], x["action"], x["memo"]], wrap=(8,))
            row += 1
    h.freeze_panes = "C5"
    if row > 5:
        h.auto_filter.ref = f"A4:H{row - 1}"
    for sh in wb.worksheets:
        sh.page_setup.orientation = "landscape"
        sh.sheet_properties.pageSetUpPr.fitToPage = True
        sh.page_setup.fitToWidth, sh.page_setup.fitToHeight = 1, 0
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out
