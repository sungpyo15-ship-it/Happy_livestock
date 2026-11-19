"""투입 전후 비교 결과 → 엑셀(목록 + 건별 A4 1장) / 앱용 데이터."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.chart import LineChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill

from .trials import MetricRow, TrialResult
from .visit import BOX, NAVY, _merge_text, _section

F = "맑은 고딕"
VERDICT_COLOR = {"개선(유의)": "1E7B34", "개선 경향": "4F8A2F", "악화(유의)": "C00000", "악화 경향": "C65911"}
CAUTION = [
    "무작위 대조시험이 아닌 '관찰 비교'입니다. 같은 기간의 날씨, 사료 원료 로트, 우군 구성(분만·건유·이동) 변화가 함께 영향을 줍니다.",
    "대조 목장 변화 = 같은 날짜 다른 담당 목장(착유 그룹) 값의 중앙값 변화(알림은 대조 목장 합산 발생률 변화). 순효과 = 이 목장 변화 − 대조 변화 (계절·날씨 영향 일부 보정).",
    "p값(대조보정): 순효과가 우연일 확률. 0.05 미만이면 '우연일 가능성이 낮음'. 일별 값은 서로 연관돼 있어 실제보다 작게 나올 수 있으므로 참고용.",
    "알림 지표는 목장 전체 기준(그룹 구분 없음). 100두·일당 두수 = 기간 중 하루 평균 알림 개체 수 ÷ 센서 두수 × 100.",
]


def _fmt(v, k=1):
    return "-" if v is None else f"{v:.{k}f}"


def _sgn(v, k=1):
    return "-" if v is None else f"{v:+.{k}f}"


def _p(v):
    return "-" if v is None else ("<0.001" if v < 0.001 else f"{v:.3f}")


def _metric_rows(ms: list[MetricRow], counts: bool = False) -> list[list[Any]]:
    out = []
    for m in ms:
        out.append([m.name, m.unit, _fmt(m.before, 2 if counts else 1) + (f" ({m.n_before})" if counts else ""),
                    _fmt(m.after, 2 if counts else 1) + (f" ({m.n_after})" if counts else ""),
                    _sgn(m.delta, 2 if counts else 1), _sgn(m.pct, 0) + ("%" if m.pct is not None else ""),
                    (_sgn(m.ctrl, 2 if counts else 1) + f" ({m.ctrl_n})") if m.ctrl is not None else "-",
                    _sgn(m.net, 2 if counts else 1), _p(m.p), m.verdict])
    return out


def _table(ws, row, headers, rows):
    from openpyxl.styles import Border  # noqa: F401
    for r_i, vals in enumerate([headers] + rows):
        for c_i, v in enumerate(vals, 1):
            c = ws.cell(row=row, column=c_i, value=v)
            c.border = BOX
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            if r_i == 0:
                c.font = Font(name=F, size=8, bold=True, color="FFFFFF")
                c.fill = PatternFill("solid", fgColor=NAVY)
            else:
                c.font = Font(name=F, size=8.5, bold=(c_i == len(vals)),
                              color=VERDICT_COLOR.get(str(v), "000000") if c_i == len(vals) else "000000")
        ws.row_dimensions[row].height = 26 if r_i == 0 else 16
        row += 1
    return row


HDR = ["지표", "단위", "투입 전", "투입 후", "변화", "변화%", "대조목장\n변화(곳)", "순효과", "p값\n(대조보정)", "판정"]


def add_trial_sheet(wb: Workbook, r: TrialResult, idx: int):
    t = r.trial
    ws = wb.create_sheet(f"{idx}_{(t.farm_name or t.farm_key)[:12]}".replace("/", "_"))
    ws.sheet_view.showGridLines = False
    for col, w in zip("ABCDEFGHIJ", [15, 9, 10, 10, 8, 7, 7, 10, 8, 10]):
        ws.column_dimensions[col].width = w
    _merge_text(ws, 1, f"사료·첨가제 투입 전후 비교 | {t.farm_name or t.farm_key}", bold=True, size=14, height=22)
    _merge_text(ws, 2, f"{t.product}" + (f"  [{t.kind}]" if t.kind else ""), bold=True, size=11, color=NAVY, height=18)
    info = f"시작일 {t.start:%Y-%m-%d}"
    if r.before and r.after:
        aft = f"{r.after[0]:%m/%d}~{r.after[1]:%m/%d}" if r.after[1] >= r.after[0] else f"{r.after[0]:%m/%d}부터"
        info += (f" · 비교: 투입 전 {r.before[0]:%m/%d}~{r.before[1]:%m/%d} vs 투입 후 {aft}"
                 f" (적응 {t.adapt}일 제외)")
    _merge_text(ws, 3, info, size=8, color="595959")
    _merge_text(ws, 4, f"대상 그룹: {', '.join(r.groups) or '-'} · 센서 {r.herd or '-'}두 · 상태: {r.status}"
                + (f" · 메모: {t.memo}" if t.memo else ""), size=8, color="595959", height=15)
    row = 6
    row = _section(ws, row, "1. 요약")
    for s in r.summary:
        _merge_text(ws, row, "• " + s, size=9, height=15)
        row += 1
    row += 1
    if r.beh:
        row = _section(ws, row, "2. 그룹 행동 (일 평균, 대상 그룹 두수 가중)")
        row = _table(ws, row, HDR, _metric_rows(r.beh))
        row += 1
    if r.alerts:
        row = _section(ws, row, "3. 건강알림 (100두·일당 알림 두수, 괄호 = 기간 중 두수·일 합계)")
        row = _table(ws, row, HDR, _metric_rows(r.alerts, counts=True))
        row += 1
    if any(v is not None for _, v in r.daily_rum):
        row = _section(ws, row, "4. 일별 반추 시간 (분/일) — 투입 전 vs 투입 후")
        start = row
        b2 = f"{r.before[1]:%m/%d}" if r.before else ""
        a1 = f"{r.after[0]:%m/%d}" if r.after else ""
        phase = "before"
        ws.cell(row=start, column=12, value="날짜")
        ws.cell(row=start, column=13, value="투입 전")
        ws.cell(row=start, column=14, value="투입 후")
        for i, (d, v) in enumerate(r.daily_rum, 1):
            if d == a1:
                phase = "after"
            ws.cell(row=start + i, column=12, value=d)
            if phase == "before":
                ws.cell(row=start + i, column=13, value=None if v is None else round(v))
            elif phase == "after":
                ws.cell(row=start + i, column=14, value=None if v is None else round(v))
            if d == b2:
                phase = "adapt"
        ch = LineChart()
        ch.height, ch.width = 5.5, 17.5
        ch.visible_cells_only = False
        n = len(r.daily_rum)
        ch.add_data(Reference(ws, min_col=13, max_col=14, min_row=start, max_row=start + n), titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=12, min_row=start + 1, max_row=start + n))
        for ser, color in zip(ch.series, ("A6A6A6", NAVY)):
            ser.smooth = False
            ser.graphicalProperties.line.solidFill = color
            ser.graphicalProperties.line.width = 22000
        ch.y_axis.majorGridlines = None
        ch.legend.position = "b"
        ws.add_chart(ch, f"A{row}")
        for col in ("L", "M", "N"):
            ws.column_dimensions[col].hidden = True
        row += 13
    row = _section(ws, row, "해석 주의")
    for c in CAUTION:
        _merge_text(ws, row, "· " + c, size=7.5, color="595959", height=24)
        row += 1
    _merge_text(ws, row + 1, "※ smaXtec 센서 데이터 기반 관찰 비교 자료. 목장 데이터 — 농장주 동의 없이 외부 공유 금지.", size=7, color="7F7F7F")
    ws.page_setup.orientation = "portrait"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 1
    ws.print_area = f"A1:J{row + 1}"


def add_index_sheet(wb: Workbook, results: list[TrialResult], title: str = "투입비교"):
    ws = wb.create_sheet(title)
    ws["A1"] = "사료·첨가제 투입 전후 비교 목록"
    ws["A1"].font = Font(name=F, bold=True, size=14)
    ws["A2"] = "입력: 프로그램 폴더의 '투입기록.xlsx' (9_TRIALS.bat). 건별 상세는 reports\\투입비교_날짜.xlsx"
    ws["A2"].font = Font(name=F, size=9, color="595959")
    hdr = ["목장", "제품·변경 내용", "구분", "시작일", "상태", "반추 변화(분)", "순효과", "판정", "건강알림 변화", "판정", "요약"]
    for i, h in enumerate(hdr, 1):
        c = ws.cell(row=4, column=i, value=h)
        c.font = Font(name=F, bold=True, color="FFFFFF", size=10)
        c.fill = PatternFill("solid", fgColor=NAVY)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for col, w in zip("ABCDEFGHIJK", [18, 28, 9, 11, 26, 10, 8, 11, 11, 11, 70]):
        ws.column_dimensions[col].width = w
    for i, r in enumerate(results, 5):
        rum = next((m for m in r.beh if m.key == "rum"), None)
        hl = next((m for m in r.alerts if m.key == "_HEALTH"), None)
        vals = [r.trial.farm_name or r.trial.farm_key, r.trial.product, r.trial.kind, f"{r.trial.start:%Y-%m-%d}"
                if r.trial.start.year > 1 else "-", r.status, _sgn(rum.delta) if rum else "-",
                _sgn(rum.net) if rum else "-", rum.verdict if rum else "-",
                _sgn(hl.delta, 2) if hl else "-", hl.verdict if hl else "-", " / ".join(r.summary[:2])]
        for c_i, v in enumerate(vals, 1):
            c = ws.cell(row=i, column=c_i, value=v)
            c.font = Font(name=F, size=10, color=VERDICT_COLOR.get(str(v), "000000"), bold=str(v) in VERDICT_COLOR)
            c.border = BOX
            c.alignment = Alignment(vertical="center", wrap_text=c_i in (2, 5, 11))
    if not results:
        ws["A5"] = "등록된 투입 기록이 없습니다. 9_TRIALS.bat 으로 투입기록.xlsx 를 열어 입력하세요."
    ws.freeze_panes = "B5"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0


def build_trials_workbook(results: list[TrialResult], out: Path) -> Path:
    wb = Workbook()
    wb.remove(wb.active)
    add_index_sheet(wb, results, "목록")
    for i, r in enumerate(results, 1):
        if r.before:
            add_trial_sheet(wb, r, i)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out


def trial_payload(r: TrialResult) -> dict[str, Any]:
    def m(x: MetricRow):
        return {"name": x.name, "unit": x.unit, "before": x.before, "after": x.after, "delta": x.delta, "pct": x.pct,
                "p": x.p, "ctrl": x.ctrl, "ctrl_n": x.ctrl_n, "net": x.net, "verdict": x.verdict,
                "nb": x.n_before, "na": x.n_after}
    t = r.trial
    return {"farm": t.farm_name or t.farm_key, "farm_id": t.farm_id, "product": t.product, "kind": t.kind,
            "start": f"{t.start:%Y-%m-%d}" if t.start.year > 1 else "-", "adapt": t.adapt, "days": t.days,
            "status": r.status, "groups": r.groups, "herd": r.herd, "memo": t.memo,
            "before": [f"{d:%m/%d}" for d in r.before] if r.before else None,
            "after": ([f"{r.after[0]:%m/%d}", f"{r.after[1]:%m/%d}" if r.after[1] >= r.after[0] else None]
                      if r.after else None),
            "summary": r.summary, "beh": [m(x) for x in r.beh], "alerts": [m(x) for x in r.alerts],
            "rum": [[d, None if v is None else round(v)] for d, v in r.daily_rum]}
