"""일일 요약 엑셀 리포트 + 메일 본문."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.chart import LineChart, Reference
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .analysis import Analysis
from .normalize import AlertTypeMapper

FONT = "맑은 고딕"
HDR_FILL = PatternFill("solid", fgColor="1F4E78")
HDR_FONT = Font(name=FONT, bold=True, color="FFFFFF", size=10)
BODY_FONT = Font(name=FONT, size=10)
TITLE_FONT = Font(name=FONT, bold=True, size=14)
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
GRADE_FILL = {
    "긴급": PatternFill("solid", fgColor="F8CBAD"),
    "우선": PatternFill("solid", fgColor="FFE699"),
    "관찰": PatternFill("solid", fgColor="DDEBF7"),
    "정상": PatternFill("solid", fgColor="E2EFDA"),
}
FLAG_FONT = Font(name=FONT, size=10, bold=True, color="C00000")


def _header(ws, row: int, headers: list[str], widths: list[int] | None = None) -> None:
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row=row, column=c, value=h)
        cell.fill, cell.font, cell.border = HDR_FILL, HDR_FONT, BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        if widths:
            ws.column_dimensions[get_column_letter(c)].width = widths[c - 1]
    ws.row_dimensions[row].height = 30


def _row(ws, row: int, values: list[Any], wrap_cols: set[int] = frozenset()) -> None:
    for c, v in enumerate(values, 1):
        cell = ws.cell(row=row, column=c, value=v)
        cell.font, cell.border = BODY_FONT, BORDER
        cell.alignment = Alignment(vertical="center", wrap_text=c in wrap_cols,
                                   horizontal="left" if c in wrap_cols else "center")


def _title(ws, text: str, sub: str) -> None:
    ws["A1"] = text
    ws["A1"].font = TITLE_FONT
    ws["A2"] = sub
    ws["A2"].font = Font(name=FONT, size=9, color="595959")


def build_excel(an: Analysis, mapper: AlertTypeMapper, rules: dict[str, Any], out_path: Path) -> Path:
    cols = rules.get("report", {}).get("columns", [])
    period = f"집계기간: {an.as_of - timedelta(hours=24):%m/%d %H:%M} ~ {an.as_of:%m/%d %H:%M} (최근 24시간)"
    wb = Workbook()

    # ───── 1. 요약 ─────
    ws = wb.active
    ws.title = "요약"
    _title(ws, f"smaXtec 목장 모니터링 일일 요약 — {an.as_of:%Y-%m-%d %H:%M} 기준", period +
           f"  |  수집이력 {an.history_days}일" + ("  (7일 비교는 이력 8일 이상부터 정확)" if an.history_days < 8 else ""))
    hdr = (["순위", "등급", "목장", "센서\n두수"] + [mapper.label(c) for c in cols] +
           ["건강알림\n(24h)", "알림\n두수", "100두당", "전일", "7일평균", "급증", "집단신호", "장기\n미해제",
            "평균지속\n(h, 7일)", "점수", "방문 사유", "마지막 정상수집"])
    widths = [5, 6, 18, 6] + [8] * len(cols) + [9, 6, 7, 6, 7, 6, 8, 7, 9, 6, 58, 14]
    _header(ws, 4, hdr, widths)
    for i, s in enumerate(an.farms, 1):
        r = 4 + i
        vals = ([i, s.grade, s.name, s.herd_size or "-"] + [s.counts_d0.get(c, 0) for c in cols] +
                [s.health_d0, s.health_animals_d0, s.per100 if s.per100 is not None else "-", s.health_d1,
                 s.health_7d_avg if s.health_7d_avg is not None else "-",
                 "▲" if s.surge else "", f"{s.cluster.n_animals}두" if s.cluster else "",
                 s.long_active or "", s.avg_duration_h if s.avg_duration_h is not None else "-",
                 s.score, ", ".join(s.reasons), f"{s.last_ok_at:%m/%d %H:%M}" if s.last_ok_at else "없음"])
        _row(ws, r, vals, wrap_cols={len(vals) - 1})
        ws.cell(row=r, column=2).fill = GRADE_FILL[s.grade]
        ws.cell(row=r, column=3).alignment = Alignment(horizontal="left", vertical="center")
        base = 4 + len(cols)
        for off in (6, 7):  # 급증 / 집단신호 강조
            if ws.cell(row=r, column=base + off).value:
                ws.cell(row=r, column=base + off).font = FLAG_FONT
    ws.freeze_panes = "D5"
    if an.farms:
        ws.auto_filter.ref = f"A4:{get_column_letter(len(hdr))}{4 + len(an.farms)}"
    n = len(an.farms)
    ws.cell(row=6 + n, column=1, value="등급: 긴급/우선/관찰/정상 = 점수 구간(config/rules.toml [priority]). "
            "집단신호는 사료·대사·질병 '의심 신호'이며 진단이 아님 → 현장 확인 및 수의사 판단 필요.").font = \
        Font(name=FONT, size=9, italic=True, color="595959")

    # ───── 2. 7일 추세 ─────
    wt = wb.create_sheet("7일추세")
    dates = sorted(next(iter(an.farms)).trend) if an.farms else []
    _title(wt, "목장별 건강알림 7일 추세 (발생일 기준, 오늘은 현재시각까지)", "건강알림 = 체온·반추·음수·pH·활동 등 category=health")
    _header(wt, 4, ["목장"] + [f"{d:%m/%d}" for d in dates] + ["합계"], [18] + [8] * len(dates) + [8])
    for i, s in enumerate(an.farms, 1):
        vals = [s.name] + [s.trend[d] for d in dates]
        _row(wt, 4 + i, vals + [sum(vals[1:])])
        wt.cell(row=4 + i, column=1).alignment = Alignment(horizontal="left")
    if an.farms and dates:
        last = 4 + len(an.farms)
        rng = f"B5:{get_column_letter(1 + len(dates))}{last}"
        wt.conditional_formatting.add(rng, ColorScaleRule(start_type="min", start_color="FFFFFF",
                                                          end_type="max", end_color="F8696B"))
        top = min(len(an.farms), 8)  # 우선순위 상위 목장만 그래프
        ch = LineChart()
        ch.title, ch.y_axis.title, ch.x_axis.title = "상위 목장 건강알림 추세", "건수", "날짜"
        ch.height, ch.width = 9, 22
        data = Reference(wt, min_col=1, max_col=1 + len(dates), min_row=5, max_row=4 + top)
        ch.add_data(data, from_rows=True, titles_from_data=True)
        ch.set_categories(Reference(wt, min_col=2, max_col=1 + len(dates), min_row=4))
        for ser in ch.series:  # 건수 데이터 → 곡선 보간 금지
            ser.smooth = False
        wt.add_chart(ch, f"A{last + 3}")

    # ───── 3. 집단신호 ─────
    wc = wb.create_sheet("집단신호")
    _title(wc, "단시간 다두수 알림 (사료문제·산독증·집단질병 의심 신호)",
           f"기준: {rules['cluster']['window_hours']}시간 내 서로 다른 개체 {rules['cluster']['min_animals']}두 이상"
           f" (두수 정보 있으면 {rules['cluster']['min_herd_pct']}% 이상)")
    _header(wc, 4, ["목장", "시작", "끝", "개체수", "두수대비%", "체온상승", "반추감소", "pH저하(±6h)", "해석·점검 항목", "개체번호"],
            [18, 12, 12, 7, 9, 8, 8, 10, 60, 40])
    for i, c in enumerate(sorted(an.clusters, key=lambda x: -x.n_animals), 1):
        _row(wc, 4 + i, [c.farm_name, f"{c.window_start:%m/%d %H:%M}", f"{c.window_end:%m/%d %H:%M}",
                         c.n_animals, c.herd_pct if c.herd_pct is not None else "-",
                         c.animals_by_type.get("TEMP_HIGH", 0), c.animals_by_type.get("RUMINATION_DROP", 0),
                         c.ph_low_count, c.interpretation, ", ".join(c.animals)], wrap_cols={9, 10})
    if not an.clusters:
        wc["A5"] = "해당 없음"

    # ───── 4. 최근 24h 알림 상세 ─────
    wd = wb.create_sheet("알림상세(24h)")
    _title(wd, "최근 24시간 발생 알림", "지속시간 = 해제시각(없으면 마지막 확인시각) − 발생시각. 해제구분 inferred = 목록에서 사라진 시점으로 추정")
    _header(wd, 4, ["목장", "개체", "알림", "원본코드/표기", "발생", "해제", "해제구분", "상태", "지속(h)", "상세", "비유상태"],
            [18, 12, 12, 18, 15, 15, 9, 8, 8, 30, 12])
    for i, a in enumerate(an.recent_alerts, 1):
        st = {"active": "진행중", "resolved": "해제", "event": "이벤트"}.get(a["status"], a["status"])
        _row(wd, 4 + i, [a["farm_name"], a["animal_label"], mapper.label(a["alert_type"]), a["alert_type_raw"],
                         a["occurred_at"][5:16], (a["resolved_at"] or "")[5:16], a["resolved_source"] or "",
                         st, a["duration_hours"] if a["duration_hours"] is not None else "-", a["detail"] or "",
                         a["lactation_status"] or ""])
    if an.recent_alerts:
        wd.auto_filter.ref = f"A4:K{4 + len(an.recent_alerts)}"
    wd.freeze_panes = "A5"

    # ───── 5. 장기 미해제 ─────
    wl = wb.create_sheet("장기미해제")
    _title(wl, f"{rules['long_active']['hours']}시간 이상 해제되지 않은 건강알림", "만성 문제(케토시스·제4위전위·족부 등) 또는 센서/표시 문제 확인")
    _header(wl, 4, ["목장", "개체", "알림", "발생", "경과(h)", "마지막확인"], [18, 10, 10, 16, 8, 16])
    for i, a in enumerate(an.long_active_alerts, 1):
        _row(wl, 4 + i, [a["farm_name"], a["animal_label"], mapper.label(a["alert_type"]), a["occurred_at"][:16],
                         a["open_hours"], a["last_seen_at"][:16]])

    # ───── 6. 수집현황 ─────
    wr = wb.create_sheet("수집현황")
    _title(wr, "최근 수집 실행 기록", "status: ok 정상 / partial 일부 목장 실패 / failed 실패 → 실패가 이어지면 logs/smxmon.log 확인")
    _header(wr, 4, ["run", "시작", "종료", "상태", "목장성공", "목장실패", "알림확인", "신규", "해제", "오류"],
            [6, 17, 17, 8, 8, 8, 8, 6, 6, 60])
    for i, r in enumerate(an.runs, 1):
        _row(wr, 4 + i, [r["run_id"], r["started_at"], r["finished_at"] or "", r["status"], r["farms_ok"],
                         r["farms_failed"], r["alerts_seen"], r["alerts_new"], r["alerts_resolved"],
                         r["error"] or ""], wrap_cols={10})

    # ───── 7. 기준설명 ─────
    wg = wb.create_sheet("기준설명")
    sg, pr = rules["surge"], rules["priority"]
    lines = [
        ("집계 기준", "리포트 시각 기준 최근 24시간(D0), 그 전 24시간(D-1), 그 전 7개 24시간 평균. 알림 '발생시각' 기준."),
        ("건강알림", "alert_types.toml 에서 category=health 인 알림(체온상승·하강, 반추감소, 음수이상, pH저하, 활동감소)."),
        ("급증", f"D0 ≥ {sg['min_count']}건 이고, 전일 대비 {sg['ratio_vs_prev_day']}배·+{sg['min_increase']}건 이상 "
                f"또는 7일평균 대비 {sg['ratio_vs_7d_avg']}배·+{sg['min_increase']}건 이상."),
        ("집단신호", "짧은 시간 창 안에 여러 개체가 체온상승/반추감소 → 공통 원인(사료·급여·환경·전염성 질병) 의심. "
                  "반추감소+pH저하 동반 시 SARA 의심 문구."),
        ("점수", f"건강알림×{pr['w_health_alert']} + pH저하×{pr['w_ph_low']} + 급증 {pr['w_surge']} + 집단 {pr['w_cluster']} "
               f"+ 장기미해제×{pr['w_long_active']} + 분만임박×{pr['w_calving']}  → 긴급≥{pr['urgent']}, 우선≥{pr['high']}, 관찰≥{pr['watch']}"),
        ("API 모드", "공식 API 이벤트는 '발생 시점' 기록이라 해제·지속시간이 없음(상태=이벤트). "
                    "장기미해제·평균지속은 web 모드 또는 향후 attentions 연동 시 계산."),
        ("해제시각", "사이트에 해제시각이 없으면 '목록에서 사라진 수집 시각'으로 추정(inferred) → 정밀도는 수집 간격(예: 2시간)."),
        ("한계", "센서 알림 기반 선별 도구이며 진단 아님. 발정·분만 알림은 번식 관리용이며 건강점수에 반영하지 않음(분만은 소폭 가중)."),
        ("보안", "목장 데이터는 농장주 소유. 이 파일을 외부(타 목장·타사·SNS)에 공유하지 말 것."),
    ]
    wg.column_dimensions["A"].width, wg.column_dimensions["B"].width = 14, 110
    for i, (k, v) in enumerate(lines, 1):
        wg.cell(row=i, column=1, value=k).font = Font(name=FONT, bold=True, size=10)
        c = wg.cell(row=i, column=2, value=v)
        c.font, c.alignment = BODY_FONT, Alignment(wrap_text=True, vertical="top")

    for sh in wb.worksheets:  # 인쇄: 가로, 너비 1페이지 맞춤
        sh.page_setup.orientation = "landscape"
        sh.page_setup.paperSize = sh.PAPERSIZE_A4
        sh.sheet_properties.pageSetUpPr.fitToPage = True
        sh.page_setup.fitToWidth, sh.page_setup.fitToHeight = 1, 0
        sh.print_title_rows = "4:4" if sh.title != "기준설명" else None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return out_path


def build_text_summary(an: Analysis, top_n: int = 10) -> str:
    lines = [f"[smaXtec 일일요약] {an.as_of:%Y-%m-%d %H:%M} 기준 (최근 24시간)", ""]
    urgent = [s for s in an.farms if s.grade in ("긴급", "우선")]
    lines.append(f"방문 우선 목장 {len(urgent)}곳 / 전체 {len(an.farms)}곳, 집단신호 {len(an.clusters)}건")
    lines.append("")
    for i, s in enumerate(an.farms[:top_n], 1):
        lines.append(f"{i:>2}. [{s.grade}] {s.name}  건강알림 {s.health_d0}건/{s.health_animals_d0}두(전일 {s.health_d1}건)  "
                     f"{', '.join(s.reasons) or '-'}")
    for c in an.clusters:
        lines += ["", f"■ 집단신호: {c.farm_name} {c.n_animals}두 ({c.window_start:%m/%d %H:%M}~{c.window_end:%H:%M})",
                  f"  {c.interpretation}"]
    stale = [s.name for s in an.farms if s.stale]
    if stale:
        lines += ["", f"⚠ 데이터 지연 목장: {', '.join(stale)}"]
    lines += ["", "※ 센서 알림 기반 선별 결과(진단 아님). 목장 데이터 외부 공유 금지."]
    return "\n".join(lines)
