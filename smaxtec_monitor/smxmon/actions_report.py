"""조치·결과 기록 엑셀 — 컨설팅 성과 자료.

기록(앱에서 입력): 날짜·대상(목장/그룹/개체)·관련 신호·조치(복수)·내용·결과(회복·치료·도태 / 개선·변화 없음·악화)
자동 계산: 조치 전 14일 vs 조치 후 8~21일(효과가 나타날 시간) 하루 평균 건강알림 두수(100두당) 변화
  ⚠ 전후 비교일 뿐 대조군이 없어 조치 효과라고 단정할 수 없음(계절·분만 몰림 등 영향). 여러 건이 쌓여야 경향을 봄.
"""
from __future__ import annotations

import sqlite3
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

BEFORE = (14, 0)     # 조치 전 14일
AFTER = (8, 21)      # 조치 후 8~21일


def records_from_notes(notes: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for k, v in notes.items():
        if not k.startswith("act:") or not isinstance(v, dict) or v.get("del") or not v.get("d"):
            continue
        _, fid, rid = k.split(":", 2)
        out.append({"fid": fid, "id": rid, **v})
    return sorted(out, key=lambda r: (r["d"], r["id"]))


def _health_per_day(conn: sqlite3.Connection, fid: str, health: set[str]) -> dict[date, set]:
    by: dict[date, set] = defaultdict(set)
    q = f"SELECT animal_id, occurred_at FROM alerts WHERE farm_id=? AND alert_type IN ({','.join('?' * len(health))})"
    for aid, ts in conn.execute(q, (fid, *health)):
        by[datetime.strptime(ts[:10], "%Y-%m-%d").date()].add(aid)
    return by


def _window(by: dict[date, set], d0: date, a: int, b: int, sign: int) -> float | None:
    days = [d0 + timedelta(days=sign * k) for k in range(a, b + 1)] if sign > 0 else \
           [d0 - timedelta(days=k) for k in range(1, a + 1)]
    return statistics.mean(len(by.get(d, ())) for d in days) if days else None


def evaluate(records: list[dict[str, Any]], conn: sqlite3.Connection | None, health: set[str],
             today: date) -> list[dict[str, Any]]:
    farms: dict[str, tuple[str, int | None]] = {}
    labels: dict[tuple[str, str], str] = {}
    cache: dict[str, dict[date, set]] = {}
    if conn is not None:
        farms = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT farm_id, name, herd_size FROM farms")}
        for fid, aid, lab, off in conn.execute("SELECT farm_id, animal_id, label, official_id FROM animals"):
            for x in (lab, off):
                if x:
                    labels[(fid, str(x))] = aid
    out = []
    for r in records:
        name, herd = farms.get(r["fid"], (r["fid"], None))
        d0 = datetime.strptime(r["d"][:10], "%Y-%m-%d").date()
        row = {**r, "farm": name, "herd": herd, "before": None, "after": None, "change": None, "status": "",
               "cow_before": None, "cow_after": None}
        if conn is not None:
            by = cache.setdefault(r["fid"], _health_per_day(conn, r["fid"], health))
            if d0 + timedelta(days=AFTER[1]) > today:
                row["status"] = f"효과 확인 중({(d0 + timedelta(days=AFTER[1]) - today).days}일 뒤)"
            else:
                b, a = _window(by, d0, BEFORE[0], 0, -1), _window(by, d0, AFTER[0], AFTER[1], 1)
                if herd:
                    b, a = b / herd * 100, a / herd * 100
                row["before"], row["after"] = round(b, 2), round(a, 2)
                row["change"] = round((a - b) / b * 100) if b else None
                row["status"] = "계산됨"
            tgt = r.get("target") or ""
            if tgt.startswith("개체:"):
                aid = labels.get((r["fid"], tgt[3:].strip()))
                if aid:
                    cnt = lambda s, e: sum(1 for d, ids in by.items() if s <= d <= e and aid in ids)  # noqa: E731
                    row["cow_before"] = cnt(d0 - timedelta(days=14), d0 - timedelta(days=1))
                    row["cow_after"] = cnt(d0 + timedelta(days=1), d0 + timedelta(days=14)) if d0 + timedelta(days=14) <= today else None
        out.append(row)
    return out


def build_workbook(rows: list[dict[str, Any]], out: Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = Workbook()
    B, H = Font(bold=True), PatternFill("solid", fgColor="DDEBF7")

    def sheet(ws, title: str, sub: str, headers: list[str], data: list[list[Any]], widths: list[int]):
        ws["A1"], ws["A2"] = title, sub
        ws["A1"].font, ws["A2"].font = Font(bold=True, size=13), Font(size=9, color="595959")
        for j, h in enumerate(headers, 1):
            c = ws.cell(row=4, column=j, value=h)
            c.font, c.fill, c.alignment = B, H, Alignment(wrap_text=True, horizontal="center", vertical="center")
            ws.column_dimensions[chr(64 + j)].width = widths[j - 1]
        ws.row_dimensions[4].height = 32
        for i, r in enumerate(data, 5):
            for j, v in enumerate(r, 1):
                ws.cell(row=i, column=j, value=v).alignment = Alignment(wrap_text=True, vertical="top")
        ws.freeze_panes = "A5"

    # 1) 조치별 요약
    ws = wb.active
    ws.title = "조치별 요약"
    by: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        for a in r.get("acts") or []:
            by[a].append(r)
    summ = []
    for a, rs in sorted(by.items(), key=lambda x: -len(x[1])):
        res = defaultdict(int)
        for r in rs:
            res[r.get("res") or "미입력"] += 1
        ch = [r["change"] for r in rs if r["change"] is not None]
        summ.append([a, len(rs), ", ".join(f"{k} {v}" for k, v in sorted(res.items(), key=lambda x: -x[1])),
                     len(ch), (round(statistics.median(ch)) if ch else None),
                     sum(1 for c in ch if c <= -20), sum(1 for c in ch if c >= 20)])
    sheet(ws, "조치별 요약 — 컨설팅 성과 자료",
          f"알림 변화 = 조치 전 {BEFORE[0]}일 vs 조치 후 {AFTER[0]}~{AFTER[1]}일 하루 평균 건강알림 두수(100두당)의 변화율. "
          "대조군 없는 전후 비교라 계절·분만 몰림 영향이 섞임 → 여러 건의 경향으로 판단",
          ["조치", "기록 수", "결과(입력값)", "알림 변화 계산된 건", "알림 변화 중앙값 %", "20% 이상 감소", "20% 이상 증가"],
          summ or [["기록 없음"]], [22, 9, 40, 12, 14, 11, 11])
    # 2) 전체 기록
    data = [[r["farm"], r["d"], r.get("target"), r.get("sig"), ", ".join(r.get("acts") or []), r.get("memo"),
             r.get("res") or "미입력", r.get("rd"), r["before"], r["after"], r["change"], r["status"],
             r["cow_before"], r["cow_after"]] for r in sorted(rows, key=lambda x: x["d"], reverse=True)]
    sheet(wb.create_sheet("전체 기록"), "조치·결과 전체 기록", "목장 데이터 — 외부 공유 금지",
          ["목장", "날짜", "대상", "관련 신호", "조치", "내용", "결과", "결과 입력일", "조치 전 알림\n(100두·일)",
           "조치 후 알림\n(100두·일)", "변화 %", "계산 상태", "개체 알림\n전 14일(일수)", "개체 알림\n후 14일(일수)"],
          data or [["기록 없음"]], [18, 11, 16, 16, 26, 30, 11, 11, 11, 11, 8, 16, 11, 11])
    # 3) 결과 입력 현황
    res_rows = []
    for t in ("개체", "목장·그룹"):
        rs = [r for r in rows if (r.get("target") or "").startswith("개체") == (t == "개체")]
        res = defaultdict(int)
        for r in rs:
            res[r.get("res") or "미입력"] += 1
        res_rows.append([t, len(rs)] + [f"{k} {v} ({v / len(rs) * 100:.0f}%)" for k, v in sorted(res.items(), key=lambda x: -x[1])] if rs else [t, 0])
    sheet(wb.create_sheet("결과 현황"), "결과 입력 현황", "7일 지나도 결과가 없으면 앱에 '결과 입력 필요' 표시",
          ["대상", "기록 수", "결과 1", "결과 2", "결과 3", "결과 4", "결과 5", "결과 6"], res_rows, [12, 9, 18, 18, 18, 18, 18, 18])
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out
