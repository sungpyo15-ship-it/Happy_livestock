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
           ["건강알림\n두수(24h)", "참고:\n알림 건수", "100두당", "전일\n두수", "7일평균\n두수", "급증", "집단신호", "장기\n미해제",
            "평균지속\n(h, 7일)", "점수", "방문 사유", "마지막 정상수집"])
    widths = [5, 6, 18, 6] + [8] * len(cols) + [9, 6, 7, 6, 7, 6, 8, 7, 9, 6, 58, 14]
    _header(ws, 4, hdr, widths)
    for i, s in enumerate(an.farms, 1):
        r = 4 + i
        vals = ([i, s.grade, s.name, s.herd_size or "-"] + [s.counts_d0.get(c, 0) for c in cols] +
                [s.health_d0, s.health_events_d0, s.per100 if s.per100 is not None else "-", s.health_d1,
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

    # ───── 2. 7일 추세 (종류별 두수) ─────
    wt = wb.create_sheet("7일추세")
    f0 = next(iter(an.farms), None)
    dates = sorted(f0.trend) if f0 else []
    cats = list(f0.trend_types) if f0 else []          # 마지막 = "건강 전체"
    _title(wt, "목장별 건강알림 7일 추세 — 종류별 두수 (발생일 기준, 오늘은 현재시각까지)",
           "두수 = 그날 해당 알림이 1번 이상 발생한 개체 수(같은 개체 중복 제외). "
           "7일 두수 = 7일 동안 1번 이상 해당된 개체 수. 질병 의심 = 케토시스·유방염·유열")
    # A. 목장별 요약: 종류별 7일 두수 + 일별 건강 전체 두수
    wt.cell(row=4, column=1, value="A. 목장별 7일 요약 (두수)").font = Font(name=FONT, bold=True, size=11, color="1F4E78")
    hdr_a = ["목장", "센서\n두수"] + [f"{c}\n(7일)" for c in cats] + [f"건강전체\n{d:%m/%d}" for d in dates]
    _header(wt, 5, hdr_a, [18, 10] + [8] * len(cats) + [7] * len(dates))   # B열은 아래 '구분'과 공용
    for i, s_ in enumerate(an.farms, 1):
        vals = ([s_.name, s_.herd_size or "-"] + [s_.trend_types_total[c] for c in cats]
                + [s_.trend_types["건강 전체"][d] for d in dates])
        _row(wt, 5 + i, vals)
        wt.cell(row=5 + i, column=1).alignment = Alignment(horizontal="left")
        wt.cell(row=5 + i, column=2 + len(cats)).font = Font(name=FONT, size=10, bold=True)
    end_a = 5 + len(an.farms)
    if an.farms:
        wt.auto_filter.ref = f"A5:{get_column_letter(len(hdr_a))}{end_a}"
        for k in range(len(cats)):   # 종류별 열마다 색 농도
            col = get_column_letter(3 + k)
            wt.conditional_formatting.add(f"{col}6:{col}{end_a}", ColorScaleRule(
                start_type="num", start_value=0, start_color="FFFFFF", end_type="max", end_color="F8696B"))
        rng = f"{get_column_letter(3 + len(cats))}6:{get_column_letter(len(hdr_a))}{end_a}"
        wt.conditional_formatting.add(rng, ColorScaleRule(start_type="num", start_value=0, start_color="FFFFFF",
                                                          end_type="max", end_color="F8696B"))
        # 그래프: 우선순위 상위 8곳 건강 전체 두수 추이 (표 오른쪽)
        top = min(len(an.farms), 8)
        ch = LineChart()
        ch.title, ch.y_axis.title = "상위 목장 건강알림 두수 추이", "두수"
        ch.height, ch.width = 9, 18
        c1 = 3 + len(cats)
        ch.add_data(Reference(wt, min_col=c1, max_col=len(hdr_a), min_row=6, max_row=5 + top), from_rows=True)
        ch.set_categories(Reference(wt, min_col=c1, max_col=len(hdr_a), min_row=5))
        for k, ser in enumerate(ch.series):
            from openpyxl.chart.series import SeriesLabel
            ser.tx = SeriesLabel(v=an.farms[k].name)
            ser.smooth = False
        wt.add_chart(ch, f"{get_column_letter(len(hdr_a) + 2)}5")

    # B. 상세: 목장 × 종류 × 일자 (두수)
    r0 = end_a + 3
    wt.cell(row=r0, column=1, value="B. 종류별 일자 상세 (두수)").font = Font(name=FONT, bold=True, size=11, color="1F4E78")
    hdr_b = ["목장", "구분"] + [f"{d:%m/%d}" for d in dates] + ["7일 두수"]
    _header(wt, r0 + 1, hdr_b)
    r = r0 + 2
    for s_ in an.farms:
        start = r
        for c in cats:
            vals = [s_.name, c] + [s_.trend_types[c][d] for d in dates] + [s_.trend_types_total[c]]
            _row(wt, r, vals)
            if c == "건강 전체":
                for col in range(2, len(hdr_b) + 1):
                    wt.cell(row=r, column=col).font = Font(name=FONT, size=10, bold=True)
                    wt.cell(row=r, column=col).fill = PatternFill("solid", fgColor="F2F2F2")
            r += 1
        wt.merge_cells(start_row=start, start_column=1, end_row=r - 1, end_column=1)
        wt.cell(row=start, column=1).alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
    if an.farms:
        wt.conditional_formatting.add(f"C{r0 + 2}:{get_column_letter(2 + len(dates))}{r - 1}", ColorScaleRule(
            start_type="num", start_value=0, start_color="FFFFFF", end_type="percentile", end_value=98, end_color="F8696B"))
    wt.freeze_panes = "B6"

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

    # ───── 3-2. 그룹 행동 ─────
    wb_ = wb.create_sheet("그룹행동")
    bc = rules.get("behaviour", {})
    _title(wb_, "그룹별 행동 지표 (smaXtec behaviour, 최신 완료일 그룹 평균)",
           f"반추 = 서서+누워 반추(분/일). 판정: 반추 7일평균 대비 −{bc.get('rum_drop_min', 30)}분 & −{bc.get('rum_drop_pct', 7)}% 이하, "
           f"누운 시간 −{bc.get('lying_drop_min', 60)}분 이하, 착유그룹 누운 시간 <{bc.get('lying_min_hours_lactating', 9)}h, "
           f"함께 눕기 −{bc.get('lying_sync_drop_pp', 10)}%p 이하")
    lac = [g for g in an.behaviour if g.lactating and g.rum is not None and g.lying is not None
           and "데이터 불완전(판정 제외)" not in g.flags]
    if lac:
        import statistics as _st
        wb_["A3"] = (f"담당 목장 착유그룹 기준값({len(lac)}개 그룹 중앙값): 반추 {_st.median(g.rum for g in lac):.0f}분, "
                     f"누운 시간 {_st.median(g.lying for g in lac) / 60:.1f}h, "
                     f"누워반추비율 {_st.median(g.rum_lying_share for g in lac if g.rum_lying_share):.0f}%")
        wb_["A3"].font = Font(name=FONT, size=9, bold=True, color="1F4E78")
    hdr_b = ["목장", "그룹", "두수", "일자", "반추\n(분)", "전일", "7일평균", "Δ반추\n(분)", "누운\n시간(h)", "Δ누운\n시간(분)",
             "서있기\n(h)", "이동\n(h)", "누워반추\n비율%", "함께\n눕기%", "함께눕기\n7일%", "판정"]
    _header(wb_, 4, hdr_b, [18, 12, 6, 11, 7, 7, 7, 8, 7, 8, 7, 7, 9, 8, 7, 40])
    rnd = lambda v, k=0: "-" if v is None else round(v, k) if k else round(v)  # noqa: E731
    gbs = sorted(an.behaviour, key=lambda g: (not g.flags, not g.rum_flag, g.farm_name, not g.lactating))
    for i, g in enumerate(gbs, 1):
        _row(wb_, 4 + i, [g.farm_name, g.group_name, g.n_animals or "-", g.date, rnd(g.rum), rnd(g.rum_prev),
                          rnd(g.rum_7d), rnd(g.rum_delta), rnd(g.lying / 60 if g.lying is not None else None, 1),
                          rnd(g.lying_delta), rnd(g.standing / 60 if g.standing is not None else None, 1),
                          rnd(g.moving / 60 if g.moving is not None else None, 1), rnd(g.rum_lying_share, 1),
                          rnd(g.sync, 1), rnd(g.sync_7d, 1), ", ".join(g.flags)], wrap_cols={16})
        wb_.cell(row=4 + i, column=1).alignment = Alignment(horizontal="left")
        if g.flags:
            wb_.cell(row=4 + i, column=16).font = FLAG_FONT
    if gbs:
        last = 4 + len(gbs)
        wb_.conditional_formatting.add(f"H5:H{last}", ColorScaleRule(
            start_type="num", start_value=-60, start_color="F8696B", mid_type="num", mid_value=0,
            mid_color="FFFFFF", end_type="num", end_value=60, end_color="63BE7B"))
        wb_.auto_filter.ref = f"A4:P{last}"
    else:
        wb_["A5"] = "행동 데이터 없음 (api 모드에서 수집)"
    wb_.freeze_panes = "C5"

    # ───── 2-2. 누적·월별 ─────
    _history_sheet(wb, an)

    # ───── 3-3. 전환기 ─────
    _transition_sheet(wb, an, rules)
    _dhi_sheet(wb, an)

    # ───── 3-4. 투입비교 목록 ─────
    if getattr(an, "trials", None):
        from .trials_report import add_index_sheet
        add_index_sheet(wb, an.trials)

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
        ("두수 기준", "모든 숫자는 '두수' = 기간 안에 알림이 1번 이상 나온 개체 수(같은 개체 반복은 1두). "
                   "요약표 '참고: 알림 건수'만 반복 포함 건수. 급증·점수도 두수로 판정."),
        ("급증", f"건강알림 두수(24h) ≥ {sg['min_count']}두 이고, 전일 대비 {sg['ratio_vs_prev_day']}배·+{sg['min_increase']}두 이상 "
                f"또는 7일평균 대비 {sg['ratio_vs_7d_avg']}배·+{sg['min_increase']}두 이상."),
        ("집단신호", "짧은 시간 창 안에 여러 개체가 체온상승/반추감소 → 공통 원인(사료·급여·환경·전염성 질병) 의심. "
                  "반추감소+pH저하 동반 시 SARA 의심 문구."),
        ("점수", f"건강알림 두수×{pr['w_health_alert']} + pH저하×{pr['w_ph_low']} + 급증 {pr['w_surge']} + 집단 {pr['w_cluster']} "
               f"+ 장기미해제×{pr['w_long_active']} + 분만임박×{pr['w_calving']}  → 긴급≥{pr['urgent']}, 우선≥{pr['high']}, 관찰≥{pr['watch']}"),
        ("API 모드", "공식 API 이벤트는 '발생 시점' 기록이라 해제·지속시간이 없음(상태=이벤트). "
                    "장기미해제·평균지속은 web 모드 또는 향후 attentions 연동 시 계산."),
        ("해제시각", "사이트에 해제시각이 없으면 '목록에서 사라진 수집 시각'으로 추정(inferred) → 정밀도는 수집 간격(예: 2시간)."),
        ("그룹행동", "smaXtec behaviour KPI(그룹 평균, 일 단위). 반추는 서서+누워 합계. 그룹 반추 감소는 사료·급여 변화가 "
                    "그룹 전체에 미친 영향을 개체 알림보다 먼저 보여줄 수 있음. 누운 시간 부족·함께 눕는 비율 저하는 누울 자리 부족·과밀·고온 등 "
                    "환경 요인 신호. 착유 그룹 판별은 그룹명 키워드(rules.toml lactating_keywords)."),
        ("한계", "센서 알림 기반 선별 도구이며 진단 아님. 발정·분만 알림은 번식 관리용이며 건강점수에 반영하지 않음(분만은 소폭 가중)."),
        ("보안", "목장 데이터는 농장주 소유. 이 파일을 외부(타 목장·타사·SNS)에 공유하지 말 것."),
    ]
    wg.column_dimensions["A"].width, wg.column_dimensions["B"].width = 14, 110
    for i, (k, v) in enumerate(lines, 1):
        wg.cell(row=i, column=1, value=k).font = Font(name=FONT, bold=True, size=10)
        c = wg.cell(row=i, column=2, value=v)
        c.font, c.alignment = BODY_FONT, Alignment(wrap_text=True, vertical="top")

    _guide_sheet(wb, an)

    for sh in wb.worksheets:  # 인쇄: 가로, 너비 1페이지 맞춤
        sh.page_setup.orientation = "landscape"
        sh.page_setup.paperSize = sh.PAPERSIZE_A4
        sh.sheet_properties.pageSetUpPr.fitToPage = True
        sh.page_setup.fitToWidth, sh.page_setup.fitToHeight = 1, 0
        sh.print_title_rows = "4:4" if sh.title not in ("기준설명", "조치가이드") else None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return out_path


def _guide_sheet(wb, an: Analysis) -> None:
    """조치 가이드 전체 + 지금 해당 목장 (config/actions.toml — 수의사 검토용으로도 사용)."""
    from .actions import farm_guides, guides
    gs = guides()
    if not gs:
        return
    ws = wb.create_sheet("조치가이드")
    ws.cell(row=1, column=1, value="알림별 조치 가이드 — 확인 → 바로 할 조치(사양·환경) → 수의사 연락 기준").font = Font(name=FONT, bold=True, size=13)
    ws.cell(row=2, column=1, value="센서 알림은 의심 신호(진단 아님). 약품 사용·치료는 수의사 진료·처방에 따름. "
            "내용 수정: config/actions.toml").font = Font(name=FONT, size=9, color="7F7F7F")
    hits = {g["id"]: [] for g in gs}
    for f in an.farms:
        for i in farm_guides(f):
            hits.setdefault(i, []).append(f.name)
    _header(ws, 4, ["알림", "① 먼저 확인", "② 바로 할 조치 (사양·환경)", "③ 수의사 연락 기준", "지금 해당 목장"],
            [16, 52, 46, 36, 22])
    for r, g in enumerate(gs, 5):
        _row(ws, r, [g["title"], "\n".join("· " + x for x in g["check"]), "\n".join("· " + x for x in g["act"]),
                     "\n".join("· " + x for x in g["vet"]) or "-", ", ".join(hits.get(g["id"], [])) or "-"],
             wrap_cols={2, 3, 4, 5})
        ws.cell(row=r, column=1).font = Font(name=FONT, bold=True, size=10)
        for c in (2, 3, 4, 5):
            ws.cell(row=r, column=c).alignment = Alignment(vertical="top", wrap_text=True)
        lines = max(sum(-(-len("· " + x) * 18 // (w * 10)) for x in xs) or 1
                    for xs, w in ((g["check"], 52), (g["act"], 46), (g["vet"], 36)))
        ws.row_dimensions[r].height = min(400, 14 * lines + 6)


def _history_sheet(wb, an: Analysis) -> None:
    from .history import HEALTH_ALL, WINDOWS
    farms = [f for f in an.farms if f.history]
    if not farms:
        return
    ws = wb.create_sheet("누적·월별")
    h0 = farms[0].history
    cats = list(h0.cum[WINDOWS[0]].keys())
    _title(ws, "목장별 알림 누적(30·90일)과 월별 추이(12개월)",
           "두수 = 기간 안에 해당 알림이 1번 이상 나온 개체 수(중복 제외). 100두당 = 두수 ÷ 현재 센서 두수 × 100. "
           "순위 = 100두당 건강알림이 적은 순(1위가 가장 적음). 이번 달은 진행 중이라 작게 나옴")
    b = an.history_bench.get(90, {})
    if b:
        ws["A3"] = "담당 목장 중앙값(90일, 100두당): " + " · ".join(f"{k} {v}" for k, v in b.items())
        ws["A3"].font = Font(name=FONT, size=9, bold=True, color="1F4E78")
    # A. 누적
    ws.cell(row=5, column=1, value="A. 30·90일 누적").font = Font(name=FONT, bold=True, size=11, color="1F4E78")
    hdr = (["목장", "센서\n두수", "30일\n건강 두수", "30일\n100두당", "30일\n순위", "90일\n건강 두수", "90일\n100두당", "90일\n순위"]
           + [f"{c}\n90일 100두당" for c in cats if c != HEALTH_ALL])
    _header(ws, 6, hdr, [18, 6, 8, 8, 7, 8, 8, 7] + [9] * (len(cats) - 1))
    rows = sorted(farms, key=lambda f: (f.history.rank.get(90) is None, -(f.history.per100[90].get(HEALTH_ALL) or 0)))
    for i, f in enumerate(rows, 7):
        h = f.history
        rk = lambda w: f"{h.rank[w]}/{h.ranked_of}" if h.rank.get(w) else "-"  # noqa: E731
        _row(ws, i, [f.name, f.herd_size or "-", h.cum[30][HEALTH_ALL], h.per100[30][HEALTH_ALL] or "-", rk(30),
                     h.cum[90][HEALTH_ALL], h.per100[90][HEALTH_ALL] or "-", rk(90)]
             + [h.per100[90][c] if h.per100[90][c] is not None else "-" for c in cats if c != HEALTH_ALL])
        ws.cell(row=i, column=1).alignment = Alignment(horizontal="left")
    end = 6 + len(rows)
    for col in ["D", "G"] + [get_column_letter(9 + k) for k in range(len(cats) - 1)]:
        ws.conditional_formatting.add(f"{col}7:{col}{end}", ColorScaleRule(
            start_type="min", start_color="FFFFFF", end_type="max", end_color="F8696B"))
    ws.freeze_panes = "B7"
    # B·C·D. 월별 (건강 전체 / 반추 감소 / 체온 상승) 100두당
    r = end + 3
    labels = [f"{m[2:4]}.{m[5:]}" + ("\n(진행중)" if j == len(h0.months) - 1 else "") for j, m in enumerate(h0.months)]
    for title, cat in (("B. 월별 건강알림 전체", HEALTH_ALL), ("C. 월별 반추 감소", "반추 감소"), ("D. 월별 체온 상승", "체온 상승")):
        if cat not in h0.monthly:
            continue
        ws.cell(row=r, column=1, value=f"{title} (100두당 두수, 센서 두수 없으면 두수)").font = Font(name=FONT, bold=True, size=11, color="1F4E78")
        _header(ws, r + 1, ["목장", "센서\n두수"] + labels)
        for i, f in enumerate(rows, r + 2):
            h = f.history
            vals = [round(v / f.herd_size * 100, 1) if f.herd_size else v for v in h.monthly[cat]]
            _row(ws, i, [f.name, f.herd_size or "-"] + vals)
            ws.cell(row=i, column=1).alignment = Alignment(horizontal="left")
        last = r + 1 + len(rows)
        ws.conditional_formatting.add(f"C{r + 2}:{get_column_letter(1 + len(labels))}{last}", ColorScaleRule(
            start_type="min", start_color="FFFFFF", end_type="max", end_color="F8696B"))
        r = last + 3
    # E. 종류별 월별 상세 (두수) — 목장 × 종류 × 12개월
    ws.cell(row=r, column=1, value="E. 종류별 월별 상세 (두수, 한 개체가 여러 종류에 해당할 수 있음)").font = \
        Font(name=FONT, bold=True, size=11, color="1F4E78")
    _header(ws, r + 1, ["목장", "구분"] + labels)
    rr = r + 2
    for f in rows:
        h, start = f.history, rr
        for cat in cats:
            _row(ws, rr, [f.name, cat] + h.monthly[cat])
            if cat == HEALTH_ALL:
                for col in range(2, len(labels) + 3):
                    ws.cell(row=rr, column=col).font = Font(name=FONT, size=10, bold=True)
                    ws.cell(row=rr, column=col).fill = PatternFill("solid", fgColor="F2F2F2")
            rr += 1
        ws.merge_cells(start_row=start, start_column=1, end_row=rr - 1, end_column=1)
        ws.cell(row=start, column=1).alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
    if rr > r + 2:
        ws.conditional_formatting.add(f"C{r + 2}:{get_column_letter(2 + len(labels))}{rr - 1}", ColorScaleRule(
            start_type="num", start_value=0, start_color="FFFFFF", end_type="percentile", end_value=98, end_color="F8696B"))


def _dhi_sheet(wb, an: Analysis) -> None:
    farms = [f for f in an.farms if f.dhi and f.dhi.get("tests")]
    if not farms:
        return
    ws = wb.create_sheet("검정성적")
    price = farms[0].dhi["milk_price"]
    _title(ws, "검정성적·경제성 — 목장별 최근 검정",
           f"알림 소 유량 차이 = 검정일 전 30일 안에 건강알림이 있던 소 − 없던 소(비유일수 구간별 비교). 월 손실 = 차이 × 해당 두수 × 30일 × {price:,.0f}원/kg (추정). "
           "연관 관계이지 인과 아님 · F/P>1.5(분만 60일 내) 케토시스 위험, F/P<1.0 산독증 위험 지표")
    hdr = ["목장", "검정일", "검정 두수", "센서 연결", "유량 kg", "유지방 %", "유단백 %", "F/P", "초기 F/P>1.5 %", "F/P<1.0 %",
           "체세포 천", "체세포20만↑ %", "MUN", "알림 소", "알림 소 유량차 kg", "월 손실 추정(만원)", "직전 검정 대비 유량"]
    _header(ws, 4, hdr, [18, 11, 8, 8, 8, 8, 8, 7, 10, 9, 9, 10, 7, 7, 11, 11, 11])
    r = 5
    for f in sorted(farms, key=lambda f: -(f.dhi["tests"][-1].get("loss_month") or 0)):
        ts = f.dhi["tests"]
        t, prev = ts[-1], (ts[-2] if len(ts) > 1 else None)
        dm = round(t["milk"] - prev["milk"], 1) if prev and t["milk"] is not None and prev["milk"] is not None else None
        _row(ws, r, [f.name, t["date"], t["n"], t["matched"], t["milk"], t["fat"], t["protein"], t["fp"], t["fp_high_early"],
                     t["fp_low"], t["scc"], t["scc200"], t["mun"], t["alert_cows"], t["milk_diff"], t["loss_month"], dm])
        ws.cell(row=r, column=1).alignment = Alignment(horizontal="left")
        r += 1
    ws.freeze_panes = "B5"


def _transition_sheet(wb, an: Analysis, rules: dict[str, Any]) -> None:
    from .transition import INDICATORS
    tc = rules.get("transition", {})
    ws = wb.create_sheet("전환기")
    b = an.transition_bench
    _title(ws, f"전환기 성적 — 분만 후 {tc.get('post_days', 21)}일 (최근 {tc.get('window_days', 90)}일 · 12개월 완료 분만우)",
           f"발생률 = 분만 후 {tc.get('post_days', 21)}일 안에 해당 알림이 1번 이상 나온 두수 ÷ 분만 두수. "
           f"무사통과 = 건강알림 없이 지난 비율. 분만 {tc.get('min_calvings', 8)}두 미만 목장은 참고용(순위 제외). "
           "'두수' 열 = 해당 두수(분모는 왼쪽 '분만' 열). 센서 '의심' 알림 기준이라 문헌의 임상 발생률과 직접 비교 불가 → 담당 목장 간 비교용")
    if b:
        ws["A3"] = (f"담당 목장 중앙값({b['n']:.0f}곳, 최근 90일): 무사통과 {b['healthy']:.0f}% · "
                    + " · ".join(f"{lbl} {b.get(code, 0):.0f}%" for code, lbl in INDICATORS)
                    + f" · 분만전 반추↓ {b.get('pre_rum', 0):.0f}%"
                    + (f"  |  12개월 무사통과 중앙값 {b['healthy_year']:.0f}%" if b.get("healthy_year") is not None else ""))
        ws["A3"].font = Font(name=FONT, size=9, bold=True, color="1F4E78")
    farms = [f for f in an.farms if f.transition and (f.transition.n or f.transition.fresh or f.transition.upcoming)]
    farms.sort(key=lambda f: (f.transition.small, -(len(f.transition.flags)),
                              f.transition.healthy_pct if f.transition.healthy_pct is not None else 999))
    v = lambda x: "-" if x is None else x  # noqa: E731

    def n_(t, k):   # 해당 두수 (분모는 옆 '분만' 열)
        c = t.cnt.get(k)
        return c[0] if c else "-"
    # (머리글, 너비, 값 함수, 색칠: None/"good"(높을수록 좋음)/"bad"(높을수록 나쁨))
    cols = [("순위", 5, lambda f, t: t.rank or ("참고" if t.small else "-"), None),
            ("목장", 18, lambda f, t: f.name, None),
            ("분만\n(90일 완료)", 8, lambda f, t: t.n, None),
            ("초산", 5, lambda f, t: t.n_first, None),
            ("무사통과\n%", 8, lambda f, t: v(t.healthy_pct), "good"),
            ("무사통과\n두수", 7, lambda f, t: n_(t, "healthy"), None)]
    for code, lbl in INDICATORS:
        cols += [(f"{lbl}\n%", 7, lambda f, t, c=code: v(t.rates.get(c)), "bad"),
                 (f"{lbl}\n두수", 6, lambda f, t, c=code: n_(t, f"rate:{c}"), None)]
    cols += [("분만전\n반추↓%", 8, lambda f, t: v(t.pre_rum_pct), None),
             ("분만전\n반추↓두수", 8, lambda f, t: n_(t, "pre_rum"), None),
             ("첫알림\n(분만후 일)", 9, lambda f, t: v(t.first_dim), None),
             ("최근30일\n분만", 8, lambda f, t: t.n_recent, None),
             ("최근30일\n무사통과%", 9, lambda f, t: v(t.healthy_recent), None),
             ("최근30일\n무사통과두수", 10, lambda f, t: n_(t, "healthy_recent"), None),
             ("분만\n입력률%", 8, lambda f, t: v(t.confirm_pct), None),
             ("12개월\n분만", 7, lambda f, t: t.n_year, None),
             ("12개월\n무사통과%", 9, lambda f, t: v(t.healthy_year), None),
             ("12개월\n무사통과두수", 10, lambda f, t: n_(t, "healthy_year"), None),
             ("12개월\n케토시스%", 9, lambda f, t: v(t.rates_year.get("KETOSIS")), None),
             ("12개월\n케토시스두수", 10, lambda f, t: n_(t, "year:KETOSIS"), None),
             ("현재\n신선우", 7, lambda f, t: len(t.fresh), None),
             ("분만예정\n(14일)", 8, lambda f, t: len(t.upcoming), None),
             ("판정", 40, lambda f, t: ", ".join(t.flags) or ("분만 적음(참고)" if t.small and t.n else ""), None)]
    hdr = [c[0] for c in cols]
    _header(ws, 5, hdr, [c[1] for c in cols])
    ws.row_dimensions[5].height = 42
    for i, f in enumerate(farms, 1):
        t = f.transition
        _row(ws, 5 + i, [c[2](f, t) for c in cols], wrap_cols={len(hdr)})
        ws.cell(row=5 + i, column=2).alignment = Alignment(horizontal="left")
        if t.flags:
            ws.cell(row=5 + i, column=len(hdr)).font = FLAG_FONT
        if t.small:
            for c in range(1, len(hdr) + 1):
                ws.cell(row=5 + i, column=c).font = Font(name=FONT, size=10, color="8C8C8C")
    end = 5 + len(farms)
    if farms:
        for j, c in enumerate(cols, 1):
            col = get_column_letter(j)
            if c[3] == "good":
                ws.conditional_formatting.add(f"{col}6:{col}{end}", ColorScaleRule(
                    start_type="num", start_value=0, start_color="F8696B", end_type="num", end_value=100, end_color="FFFFFF"))
            elif c[3] == "bad":
                ws.conditional_formatting.add(f"{col}6:{col}{end}", ColorScaleRule(
                    start_type="num", start_value=0, start_color="FFFFFF", end_type="max", end_color="F8696B"))
        ws.auto_filter.ref = f"A5:{get_column_letter(len(hdr))}{end}"
    else:
        ws["A6"] = "분만 기록 없음 (첫 수집 시 과거 1년 이력을 받아야 계산됨)"
    ws.freeze_panes = "C6"

    # 분만월별 무사통과율
    months = sorted({m["month"] for f in farms for m in f.transition.monthly})
    if months:
        r0 = end + 3
        ws.cell(row=r0, column=1, value="분만월별 무사통과율 % (무사통과 두수/분만 두수) — 최근 12개월, 계절 비교").font = Font(name=FONT, bold=True, size=11, color="1F4E78")
        _header(ws, r0 + 1, ["", "목장"] + months)
        for i, f in enumerate(farms, 1):
            mm = {m["month"]: m for m in f.transition.monthly}
            _row(ws, r0 + 1 + i, ["", f.name] + [f"{mm[m]['healthy']:.0f}% ({mm[m].get('healthy_n', '?')}/{mm[m]['n']}두)"
                                                 if m in mm and mm[m]["healthy"] is not None else "-" for m in months])
            ws.cell(row=r0 + 1 + i, column=2).alignment = Alignment(horizontal="left")


def build_text_summary(an: Analysis, top_n: int = 10) -> str:
    lines = [f"[smaXtec 일일요약] {an.as_of:%Y-%m-%d %H:%M} 기준 (최근 24시간)", ""]
    urgent = [s for s in an.farms if s.grade in ("긴급", "우선")]
    lines.append(f"방문 우선 목장 {len(urgent)}곳 / 전체 {len(an.farms)}곳, 집단신호 {len(an.clusters)}건")
    lines.append("")
    for i, s in enumerate(an.farms[:top_n], 1):
        lines.append(f"{i:>2}. [{s.grade}] {s.name}  건강알림 {s.health_d0}두(전일 {s.health_d1}두, {s.health_events_d0}건)  "
                     f"{', '.join(s.reasons) or '-'}")
    for c in an.clusters:
        lines += ["", f"■ 집단신호: {c.farm_name} {c.n_animals}두 ({c.window_start:%m/%d %H:%M}~{c.window_end:%H:%M})",
                  f"  {c.interpretation}"]
    stale = [s.name for s in an.farms if s.stale]
    if stale:
        lines += ["", f"⚠ 데이터 지연 목장: {', '.join(stale)}"]
    lines += ["", "※ 센서 알림 기반 선별 결과(진단 아님). 목장 데이터 외부 공유 금지."]
    return "\n".join(lines)
