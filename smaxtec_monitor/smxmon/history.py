"""목장별 장기 알림 지표: 30일·90일 누적, 최근 12개월 월별 추이.

- 두수 = 기간 안에 해당 알림이 1번 이상 나온 개체 수(같은 개체 중복 제외)
- 100두당 = 두수 ÷ 현재 센서 두수 × 100  (과거 두수 변동은 반영 못 함 → 큰 증감 목장은 해석 주의)
- 순위 = 100두당 건강알림이 적은 순(1위 = 가장 적음), 센서 두수 정보가 있는 목장끼리
"""
from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

WINDOWS = (30, 90)
HEALTH_ALL = "건강 전체"


@dataclass
class FarmHistory:
    cum: dict[int, dict[str, int]] = field(default_factory=dict)          # {30: {구분: 두수}}
    per100: dict[int, dict[str, float | None]] = field(default_factory=dict)
    rank: dict[int, int | None] = field(default_factory=dict)             # 건강 전체 100두당 순위
    ranked_of: int | None = None
    months: list[str] = field(default_factory=list)                       # ["2025-12", ...]
    monthly: dict[str, list[int]] = field(default_factory=dict)           # {구분: [월별 두수]}
    partial_month: bool = True


def month_keys(as_of: datetime, n: int = 12) -> list[str]:
    y, m = as_of.year, as_of.month
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out[::-1]


def farm_history(conn: sqlite3.Connection, farm_id: str, as_of: datetime, cats: list[tuple[str, set[str]]],
                 herd: int | None) -> FarmHistory:
    months = month_keys(as_of)
    start = min(as_of - timedelta(days=max(WINDOWS)), datetime.strptime(months[0] + "-01", "%Y-%m-%d"))
    rows = conn.execute(
        "SELECT animal_id, alert_type, occurred_at FROM alerts WHERE farm_id=? AND occurred_at >= ? AND occurred_at <= ?",
        (farm_id, start.strftime("%Y-%m-%d %H:%M:%S"), as_of.strftime("%Y-%m-%d %H:%M:%S"))).fetchall()
    h = FarmHistory(months=months)
    for w in WINDOWS:
        since = (as_of - timedelta(days=w)).strftime("%Y-%m-%d %H:%M:%S")
        h.cum[w] = {lbl: len({r[0] for r in rows if r[2] > since and r[1] in types}) for lbl, types in cats}
        h.per100[w] = {k: (round(v / herd * 100, 1) if herd else None) for k, v in h.cum[w].items()}
    idx = {m: i for i, m in enumerate(months)}
    sets: dict[str, list[set]] = {lbl: [set() for _ in months] for lbl, _ in cats}
    for aid, typ, ts in rows:
        i = idx.get(ts[:7])
        if i is None:
            continue
        for lbl, types in cats:
            if typ in types:
                sets[lbl][i].add(aid)
    h.monthly = {lbl: [len(s) for s in v] for lbl, v in sets.items()}
    return h


def rank_and_bench(items: list[tuple[Any, FarmHistory, int | None]]) -> dict[str, Any]:
    """items: (요약객체, 이력, 센서두수). 순위 부여하고 담당목장 중앙값(100두당) 반환."""
    bench: dict[str, Any] = {}
    for w in WINDOWS:
        ok = [(s, h) for s, h, herd in items if herd and h.per100.get(w, {}).get(HEALTH_ALL) is not None]
        ranked = sorted(ok, key=lambda x: x[1].per100[w][HEALTH_ALL])
        for i, (_, h) in enumerate(ranked, 1):
            h.rank[w] = i
            h.ranked_of = len(ranked)
        if len(ok) >= 3:
            labels = list(ok[0][1].per100[w].keys())
            bench[w] = {lbl: round(statistics.median(h.per100[w][lbl] for _, h in ok), 1) for lbl in labels}
    return bench


