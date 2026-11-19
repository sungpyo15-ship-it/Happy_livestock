"""목장 방문용 1장 요약 (A4 세로 1페이지) — 엑셀 시트 + 모바일 대시보드가 같은 내용을 공유.

구성: 머리말(등급·점수) → 핵심요약 → 알림 지표 → 7일 추세 → 그룹행동(담당목장 기준값 대비)
      → 집단신호 해석 → 확인할 개체 → 현장 점검 체크리스트(자동) → 메모란
"""
from __future__ import annotations

import statistics
from typing import Any

from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.workbook import Workbook

from .analysis import Analysis, FarmSummary
from .normalize import AlertTypeMapper

INCOMPLETE = "데이터 불완전(판정 제외)"
KPI_TYPES = ["TEMP_HIGH", "RUMINATION_DROP", "DRINKING_ABNORMAL", "PH_LOW", "KETOSIS", "MASTITIS", "HEAT", "CALVING"]


# ───────────────────────── 공통 내용 ─────────────────────────
def benchmarks(an: Analysis) -> dict[str, float]:
    """담당 목장 착유그룹 중앙값 (방문 목장 비교 기준)."""
    lac = [g for g in an.behaviour if g.lactating and g.rum is not None and g.lying is not None
           and INCOMPLETE not in g.flags]
    if not lac:
        return {}
    return {
        "n": len(lac),
        "rum": statistics.median(g.rum for g in lac),
        "lying_h": statistics.median(g.lying for g in lac) / 60,
        "rum_lying_share": statistics.median(g.rum_lying_share for g in lac if g.rum_lying_share),
    }


def checklist(s: FarmSummary) -> list[tuple[str, str]]:
    """신호 → 현장 점검 항목 (주제, 내용). 진단이 아니라 '확인할 것' 목록."""
    items: list[tuple[str, str]] = []
    c = s.counts_d0
    rum_groups = [g.group_name for g in s.behaviour if g.rum_flag]
    lying_groups = [g.group_name for g in s.behaviour
                    if any(f.startswith("횡와") for f in g.flags) and INCOMPLETE not in g.flags]
    sync_groups = [g.group_name for g in s.behaviour if any(f.startswith("횡와동조") for f in g.flags)]
    if (s.cluster and s.cluster.animals_by_type.get("RUMINATION_DROP")) or rum_groups:
        tgt = ", ".join(rum_groups) if rum_groups else "반추감소 개체 소속 그룹"
        items.append(("사료·급여", f"[{tgt}] 최근 배합·원료·조사료 로트 변경 여부, TMR 입자도(체 분석)·수분, "
                                  "사조 잔량·선별채식, 급여 시각 변경, 사료 냄새·곰팡이"))
    if c.get("PH_LOW") or (s.cluster and "SARA" in s.cluster.interpretation):
        items.append(("산독증(SARA)", "농후사료 비율·1회 급여량, 분변 상태(묽음·기포·미소화 곡류), 완충제 사용, "
                                       "신선우 농후사료 증량 속도"))
    if c.get("KETOSIS") or c.get("MILK_FEVER") or any(g.group_name and "신선" in g.group_name and g.flags
                                                         for g in s.behaviour):
        items.append(("전환기", "신선우 BCS·식욕, 요/혈중 케톤 간이검사, 건유후기 사료·DCAD, 분만 후 칼슘 보충"))
    if c.get("TEMP_HIGH", 0) >= 2 or (s.cluster and s.cluster.animals_by_type.get("TEMP_HIGH")):
        items.append(("체온·질병", "발열우 직장온 재확인, 유방염·폐렴·자궁염 감별, 동시 발생이면 수의사 진료·격리"))
    if c.get("HEAT_STRESS") or c.get("GROUP_TEMP"):
        items.append(("환경", "우사 THI·환기팬·안개분무·차광, 음수조 수량·청결"))
    if lying_groups or sync_groups:
        tgt = ", ".join(sorted(set(lying_groups + sync_groups)))
        items.append(("우사 환경", f"[{tgt}] 우상·깔짚 상태(건조·두께), 과밀(두당 우상 수), 최근 이동·합사, "
                                    "착유 대기시간·사양관리 작업 시간 변화"))
    if c.get("DRINKING_ABNORMAL"):
        items.append(("음수", "급수기 유량·위생, 수온, 음수 접근성(서열 경쟁)"))
    if c.get("MASTITIS"):
        items.append(("유방염", "CMT 검사, 착유 위생·라이너 교체 주기, 깔짚 위생"))
    if not items:
        items.append(("정기 점검", "특이 신호 없음 → 사조 잔량, 분변, 반추 관찰 등 정기 항목 위주"))
    return items


def headline(s: FarmSummary) -> list[str]:
    """핵심요약 3줄 이내."""
    lines = []
    if s.cluster:
        lines.append(f"집단신호 {s.cluster.n_animals}두 ({s.cluster.window_start:%m/%d %H:%M}~"
                     f"{s.cluster.window_end:%H:%M}) — {s.cluster.interpretation.split('.')[0]}")
    trend = f"건강알림 24h {s.health_d0}건/{s.health_animals_d0}두 (전일 {s.health_d1}건"
    trend += f", 7일평균 {s.health_7d_avg}건)" if s.health_7d_avg is not None else ")"
    if s.surge:
        trend += f" — 급증({s.surge_reason})"
    lines.append(trend)
    flagged = [f"{g.group_name} {', '.join(g.flags)}" for g in s.behaviour if g.flags and INCOMPLETE not in g.flags]
    if flagged:
        lines.append("그룹행동: " + " / ".join(flagged[:3]))
    return lines


def farm_payload(s: FarmSummary, an: Analysis, mapper: AlertTypeMapper, bench: dict) -> dict[str, Any]:
    """모바일 대시보드용 JSON (목장 1곳)."""
    def r(v, k=0):
        return None if v is None else round(v, k) if k else round(v)
    return {
        "id": s.farm_id, "name": s.name, "grade": s.grade, "score": s.score, "herd": s.herd_size,
        "last_ok": f"{s.last_ok_at:%m/%d %H:%M}" if s.last_ok_at else None, "stale": s.stale,
        "health_d0": s.health_d0, "health_animals": s.health_animals_d0, "health_d1": s.health_d1,
        "health_7d": s.health_7d_avg, "surge": s.surge, "reasons": s.reasons, "headline": headline(s),
        "counts": [{"label": mapper.label(c), "n24": s.counts_d0.get(c, 0), "n72": s.counts_72h.get(c, 0)}
                   for c in KPI_TYPES],
        "trend": [{"d": f"{d:%m/%d}", "n": n} for d, n in sorted(s.trend.items())],
        "cluster": None if not s.cluster else {
            "n": s.cluster.n_animals, "from": f"{s.cluster.window_start:%m/%d %H:%M}",
            "to": f"{s.cluster.window_end:%m/%d %H:%M}", "text": s.cluster.interpretation,
            "animals": s.cluster.animals},
        "groups": [{"name": g.group_name, "n": g.n_animals, "date": g.date, "rum": r(g.rum), "rum7": r(g.rum_7d),
                    "drum": r(g.rum_delta), "lying_h": r(g.lying / 60, 1) if g.lying is not None else None,
                    "dlying": r(g.lying_delta), "share": r(g.rum_lying_share), "sync": r(g.sync),
                    "flags": g.flags, "lact": g.lactating} for g in s.behaviour],
        "animals": [{"label": a["label"], "status": a["status"], "n": a["n"], "types": a["types"],
                     "last": f"{a['last']:%m/%d %H:%M}"} for a in s.top_animals],
        "checklist": [{"topic": t, "text": x} for t, x in checklist(s)],
        "row": {"surge": s.surge, "cluster_n": s.cluster.n_animals if s.cluster else 0, "long": s.long_active,
                "per100": s.per100, "c": {c: s.counts_d0.get(c, 0) for c in KPI_TYPES}},
        "trend_types": {k: [v[d] for d in sorted(v)] for k, v in s.trend_types.items()},
        "trend_total": s.trend_types_total,
        "alerts24": [{"t": a["occurred_at"][5:16], "animal": a["animal_label"], "type": mapper.label(a["alert_type"]),
                      "raw": a["alert_type_raw"], "status": _status(a.get("lactation_status") or "")}
                     for a in an.recent_alerts if a["farm_id"] == s.farm_id
                     and mapper.category(a["alert_type"]) in ("health", "reproduction")][:60],
    }


# ───────────────────────── 엑셀 1장 ─────────────────────────
F = "맑은 고딕"
NAVY = "1F4E78"
SEC_FILL = PatternFill("solid", fgColor="DDEBF7")
GRADE_COLOR = {"긴급": "C00000", "우선": "C65911", "관찰": "2F75B5", "정상": "548235"}
THIN = Side(style="thin", color="BFBFBF")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
COLS = 10  # A~J


def _merge_text(ws, row: int, text: str, *, bold=False, size=9, color="000000", fill=None, height=None,
                col1=1, col2=COLS, wrap=True, align="left"):
    ws.merge_cells(start_row=row, start_column=col1, end_row=row, end_column=col2)
    c = ws.cell(row=row, column=col1, value=text)
    c.font = Font(name=F, bold=bold, size=size, color=color)
    c.alignment = Alignment(wrap_text=wrap, vertical="center", horizontal=align)
    if fill:
        for col in range(col1, col2 + 1):
            ws.cell(row=row, column=col).fill = fill
    if height:
        ws.row_dimensions[row].height = height


def _section(ws, row: int, title: str) -> int:
    _merge_text(ws, row, title, bold=True, size=10, color=NAVY, fill=SEC_FILL, height=17)
    return row + 1


def _table(ws, row: int, headers: list[str], rows: list[list[Any]], spans: list[int] | None = None) -> int:
    """spans: 각 열이 차지할 칸 수(합 = COLS)."""
    spans = spans or [COLS // len(headers)] * len(headers)
    for r_i, vals in enumerate([headers] + rows):
        col = 1
        for v, sp in zip(vals, spans):
            if sp > 1:
                ws.merge_cells(start_row=row, start_column=col, end_row=row, end_column=col + sp - 1)
            c = ws.cell(row=row, column=col, value=v)
            c.font = Font(name=F, size=8 if r_i else 8, bold=(r_i == 0), color="FFFFFF" if r_i == 0 else "000000")
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            for cc in range(col, col + sp):
                ws.cell(row=row, column=cc).border = BOX
                if r_i == 0:
                    ws.cell(row=row, column=cc).fill = PatternFill("solid", fgColor=NAVY)
            col += sp
        row += 1
    return row


def add_visit_sheet(wb: Workbook, s: FarmSummary, an: Analysis, mapper: AlertTypeMapper, bench: dict,
                    title: str | None = None):
    name = (title or f"방문_{s.name}")[:31]
    for ch in '[]:*?/\\':
        name = name.replace(ch, "_")
    ws = wb.create_sheet(name)
    for i in range(1, COLS + 1):
        ws.column_dimensions[chr(64 + i)].width = 9.2
    ws.sheet_view.showGridLines = False

    # 머리말
    _merge_text(ws, 1, f"목장 방문 요약 | {s.name}", bold=True, size=15, height=24, col2=7)
    _merge_text(ws, 1, f"{s.grade}  {s.score:g}점", bold=True, size=14, color=GRADE_COLOR[s.grade],
                col1=8, col2=10, align="right")
    _merge_text(ws, 2, f"기준 {an.as_of:%Y-%m-%d %H:%M} (최근 24시간) · 센서 {s.herd_size or '-'}두 · "
                       f"마지막 수집 {s.last_ok_at:%m/%d %H:%M}" + ("  ⚠데이터 지연" if s.stale else "")
                if s.last_ok_at else "수집 이력 없음", size=8, color="595959")
    row = 4

    row = _section(ws, row, "1. 핵심 요약")
    for ln in headline(s):
        _merge_text(ws, row, "• " + ln, size=9, height=26 if len(ln) > 70 else 15)
        row += 1
    row += 1

    row = _section(ws, row, "2. 알림 현황 (24시간 / 72시간)")
    labels = [mapper.label(c) for c in KPI_TYPES]
    row = _table(ws, row, ["구분"] + labels[:4] + ["건강알림 계"],
                 [["24h"] + [s.counts_d0.get(c, 0) for c in KPI_TYPES[:4]] + [f"{s.health_d0}건/{s.health_animals_d0}두"],
                  ["72h"] + [s.counts_72h.get(c, 0) for c in KPI_TYPES[:4]] + ["-"]], [2, 1, 2, 1, 1, 3])
    row = _table(ws, row, ["구분"] + labels[4:] + ["전일 / 7일평균"],
                 [["24h"] + [s.counts_d0.get(c, 0) for c in KPI_TYPES[4:]]
                  + [f"{s.health_d1}건 / {s.health_7d_avg if s.health_7d_avg is not None else '-'}건"],
                  ["72h"] + [s.counts_72h.get(c, 0) for c in KPI_TYPES[4:]] + ["-"]], [2, 1, 1, 1, 2, 3])
    row += 1

    # 7일 추세 차트 (데이터는 오른쪽 숨김 영역 L:M)
    row = _section(ws, row, "3. 건강알림 7일 추세")
    dates = sorted(s.trend)
    for i, d in enumerate(dates):
        ws.cell(row=row + i, column=12, value=f"{d:%m/%d}")
        ws.cell(row=row + i, column=13, value=s.trend[d])
    if dates:
        ch = BarChart()
        ch.height, ch.width, ch.legend = 3.4, 17.5, None
        ch.visible_cells_only = False   # 데이터 열(L:M)을 숨겨도 그래프 표시
        ch.y_axis.majorGridlines = None
        ch.add_data(Reference(ws, min_col=13, min_row=row, max_row=row + len(dates) - 1))
        ch.set_categories(Reference(ws, min_col=12, min_row=row, max_row=row + len(dates) - 1))
        ch.series[0].graphicalProperties.solidFill = NAVY
        ws.add_chart(ch, f"A{row + 1}")
    ws.column_dimensions["L"].hidden = ws.column_dimensions["M"].hidden = True
    row += 10

    row = _section(ws, row, "4. 그룹 행동 (smaXtec behaviour, 최신 완료일)")
    if bench:
        _merge_text(ws, row, f"비교 기준 — 담당 목장 착유그룹 {bench['n']:.0f}개 중앙값: 반추 {bench['rum']:.0f}분 · "
                             f"횡와 {bench['lying_h']:.1f}h · 누워반추 {bench['rum_lying_share']:.0f}%",
                    size=8, color="595959", height=14)
        row += 1
    gb = [g for g in s.behaviour]
    if gb:
        rows = [[g.group_name, g.n_animals or "-", _n(g.rum), _n(g.rum_7d), _sgn(g.rum_delta),
                 _n(g.lying / 60 if g.lying is not None else None, 1), _sgn(g.lying_delta), _n(g.rum_lying_share),
                 ", ".join(g.flags) or "-"] for g in gb[:6]]
        row = _table(ws, row, ["그룹", "두수", "반추(분)", "7일평균", "Δ반추", "횡와(h)", "Δ횡와(분)", "누워반추%", "판정"],
                     rows, [1, 1, 1, 1, 1, 1, 1, 1, 2])
    else:
        _merge_text(ws, row, "행동 데이터 없음(그룹 미설정 또는 3두 미만)", size=8, color="7F7F7F")
        row += 1
    row += 1

    if s.cluster:
        row = _section(ws, row, "5. 집단신호 해석 (의심 신호 — 진단 아님)")
        _merge_text(ws, row, s.cluster.interpretation, size=8.5, height=40)
        row += 1
        _merge_text(ws, row, "관련 개체: " + ", ".join(s.cluster.animals), size=8, color="595959", height=15)
        row += 2

    row = _section(ws, row, f"{6 if s.cluster else 5}. 확인할 개체 (최근 72시간 건강알림, 상위 8두)")
    if s.top_animals:
        row = _table(ws, row, ["개체", "상태", "알림수", "알림 종류", "마지막"],
                     [[a["label"], _status(a["status"]), a["n"], a["types"], f"{a['last']:%m/%d %H:%M}"]
                      for a in s.top_animals[:8]], [1, 2, 1, 4, 2])
    else:
        _merge_text(ws, row, "해당 개체 없음", size=8, color="7F7F7F")
        row += 1
    row += 1

    row = _section(ws, row, f"{7 if s.cluster else 6}. 현장 점검 체크리스트")
    for topic, text in checklist(s):
        _merge_text(ws, row, f"☐ {topic}", bold=True, size=8.5, col2=2)
        _merge_text(ws, row, text, size=8, col1=3, height=26 if len(text) > 60 else 15)
        row += 1
    row += 1
    row = _section(ws, row, "메모 (농장주 의견·조치·다음 방문)")
    for _ in range(4):
        for col in range(1, COLS + 1):
            ws.cell(row=row, column=col).border = Border(bottom=Side(style="hair", color="A6A6A6"))
        ws.row_dimensions[row].height = 18
        row += 1
    _merge_text(ws, row + 1, "※ smaXtec 센서 알림·행동지표 기반 선별 자료(진단 아님). 목장 데이터 — 외부 공유 금지.",
                size=7, color="7F7F7F")

    ws.page_setup.orientation = "portrait"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 1
    ws.page_margins.left = ws.page_margins.right = 0.4
    ws.page_margins.top = ws.page_margins.bottom = 0.5
    ws.print_area = f"A1:J{row + 1}"
    return ws


def _n(v, k=0):
    return "-" if v is None else round(v, k) if k else round(v)


def _sgn(v):
    return "-" if v is None else f"{v:+.0f}"


def _status(s: str) -> str:
    return {"Lactating_Cow": "착유", "Dry_Cow": "건유", "Young_Cow": "육성", "Heifer": "미경산",
            "Fresh_Cow": "신선", "Close_Up": "분만전"}.get(s or "", s or "-")


def visit_targets(an: Analysis, limit: int = 10) -> list[FarmSummary]:
    return [s for s in an.farms if s.grade in ("긴급", "우선")][:limit]


def build_visit_workbook(an: Analysis, farms: list[FarmSummary], mapper: AlertTypeMapper, out_path) -> Any:
    wb = Workbook()
    wb.remove(wb.active)
    bench = benchmarks(an)
    for s in farms:
        add_visit_sheet(wb, s, an, mapper, bench)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return out_path

