"""발정 모아보기 — 목장마다 따로 열지 않고 담당자별로 모아서(07:00·13:00·19:00 알림 + 전용 화면).

발정 = smaXtec 발정 이벤트(heat · health_701 · health_705 · heat_dnb → alert_type HEAT).
소마다 붙이는 정보(수집 이력 안에서 계산): 분만 후 일수, 지난 발정과의 간격, 이번 산차 수정 횟수·마지막 수정일,
임신 감정 결과, 수정 금지 표시(DNB).  ⚠ 수정 적기는 smaXtec 앱의 표시를 우선(여기선 감지 후 경과 시간만).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any

HEAT_RAW = ("heat", "health_701", "health_705", "heat_dnb")
CALV = ("calving_confirmation", "calving_detection")
FMT = "%Y-%m-%d %H:%M:%S"


def _t(s: str) -> datetime:
    return datetime.strptime(s[:19], FMT)


def recent_heats(conn: sqlite3.Connection, since: datetime, now: datetime | None = None,
                 farm_ids: set[str] | None = None) -> list[dict[str, Any]]:
    """since 이후 발정(소별 1건 — 가장 최근) + 소 이력 정보."""
    now = now or datetime.now()
    conn.row_factory = sqlite3.Row
    q = ("SELECT farm_id, farm_name, animal_id, animal_label, lower(alert_type_raw) AS raw, occurred_at, detail "
         "FROM v_alerts WHERE alert_type='HEAT' AND occurred_at > ? AND occurred_at <= ? ORDER BY occurred_at DESC")
    rows = [r for r in conn.execute(q, (since.strftime(FMT), now.strftime(FMT)))
            if farm_ids is None or r["farm_id"] in farm_ids]
    latest: dict[tuple[str, str], sqlite3.Row] = {}
    for r in rows:
        latest.setdefault((r["farm_id"], r["animal_id"]), r)
    out = []
    for (fid, aid), r in latest.items():
        t = _t(r["occurred_at"])
        hist = conn.execute(
            "SELECT lower(alert_type_raw) AS raw, alert_type, occurred_at, detail FROM alerts WHERE farm_id=? AND animal_id=? "
            "AND occurred_at > ? AND occurred_at <= ? ORDER BY occurred_at",
            (fid, aid, (t - timedelta(days=420)).strftime(FMT), r["occurred_at"])).fetchall()
        calv = [_t(h["occurred_at"]) for h in hist if h["raw"] in CALV]
        last_calv = calv[-1] if calv else None
        cur = [h for h in hist if last_calv is None or _t(h["occurred_at"]) >= last_calv]
        heats = [_t(h["occurred_at"]) for h in cur if h["alert_type"] == "HEAT" and _t(h["occurred_at"]) < t - timedelta(days=4)]   # 4일 안은 같은 발정(여러 번 감지)
        ais = [_t(h["occurred_at"]) for h in cur if h["raw"] == "insemination"]
        preg = None
        for h in cur:
            if h["raw"] == "pregnancy_result":
                try:
                    preg = (json.loads(h["detail"] or "{}") or {}).get("pregnant")
                except ValueError:
                    pass
            elif h["raw"] == "abort":
                preg = None
        try:
            det = json.loads(r["detail"] or "{}") or {}
        except ValueError:
            det = {}
        out.append({
            "farm_id": fid, "farm": r["farm_name"], "animal_id": aid, "label": r["animal_label"],
            "t": r["occurred_at"][:16], "hours_ago": round((now - t).total_seconds() / 3600, 1),
            "dim": (t - last_calv).days if last_calv else None,
            "interval": (t - heats[-1]).days if heats else None,
            "n_ai": len(ais), "last_ai": ais[-1].strftime("%m/%d") if ais else None,
            "pregnant": preg, "dnb": r["raw"] == "heat_dnb", "cycle": det.get("cycle_length"),
            "key": f"{fid}|{aid}|{r['occurred_at'][:16]}"})
    out.sort(key=lambda h: h["t"], reverse=True)
    return out


def note(h: dict[str, Any]) -> str:
    """한 줄 요약: 분만 85일 · 간격 21일 · 수정 1회(10/20)."""
    p = []
    if h.get("dim") is not None:
        p.append(f"분만 {h['dim']}일")
    if h.get("interval") is not None:
        iv = h["interval"]
        p.append(f"간격 {iv}일" + ("" if 18 <= iv <= 24 or iv > 35 else "(불규칙)"))
    if h.get("n_ai"):
        p.append(f"수정 {h['n_ai']}회({h['last_ai']})")
    if h.get("pregnant") is True:
        p.append("⚠임신 감정 양성 후 발정")
    if h.get("dnb"):
        p.append("수정 금지 표시")
    return " · ".join(p)


def flags(h: dict[str, Any]) -> list[str]:
    """주의 표시."""
    f = []
    if h.get("pregnant") is True:
        f.append("임신 감정 양성 후 발정 — 감정 재확인")
    if h.get("dim") is not None and h["dim"] < 40:
        f.append(f"분만 {h['dim']}일 — 자발적 대기기간(보통 50~60일) 전")
    if h.get("interval") is not None and h["interval"] < 18:
        f.append(f"지난 발정 {h['interval']}일 전 — 짧은 간격(낭종 등 확인)")
    if h.get("n_ai", 0) >= 3:
        f.append(f"수정 {h['n_ai']}회 — 반복 수정우, 번식 검진 고려")
    if h.get("dnb"):
        f.append("smaXtec 수정 금지(DNB) 표시")
    return f


def notice_body(heats: list[dict[str, Any]], max_lines: int = 6) -> str:
    """알림 본문 — 한 줄에 한 두: '가나목장 118 · 11/28 08:10 감지 · 분만 86일·간격 21일'."""
    lines = []
    for h in heats[:max_lines]:
        info = note(h).replace(" · ", "·")
        lines.append(f"{h['farm']} {h['label']} · {h['t'][5:].replace('-', '/')} 감지" + (f" · {info}" if info else ""))
    if len(heats) > max_lines:
        lines.append(f"외 {len(heats) - max_lines}두 — 눌러서 전체 보기")
    return "\n".join(x[:100] for x in lines)
