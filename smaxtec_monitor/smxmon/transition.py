"""전환기(분만 전후) 성적.

- 분만: calving_confirmation(농장 입력, 산차 포함) / calving_detection(센서 감지). 14일 이내 중복은 1회 분만
- 완료 코호트: 분만 후 post_days(21일)가 지난 소만 발생률 계산 (관찰기간이 짧으면 발생률이 낮게 나오는 왜곡 방지)
- 기간: 최근 90일(window_days, 판정·순위) + 최근 12개월(long_days, 안정된 장기 수치·월별 계절 추이)
- 발생률 = 분만 후 21일 안에 해당 알림이 1번 이상 나온 두수 ÷ 분만 두수
- 무사통과 = 21일 동안 건강알림이 한 번도 없던 소 비율
- 분만 전 반추감소 = 분만 전 21일 안에 반추감소 알림이 있던 소 비율 (건유후기 사료·관리 신호)
⚠ 알림은 센서 알고리즘의 '의심' 신호 → 문헌의 임상/준임상 발생률과 직접 비교하지 말고 담당 목장 간 상대비교로 사용
"""
from __future__ import annotations

import json
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .normalize import AlertTypeMapper, from_db_time, to_db_time

INDICATORS = [("KETOSIS", "케토시스"), ("MILK_FEVER", "유열"), ("MASTITIS", "유방염"),
              ("TEMP_HIGH", "체온↑"), ("RUMINATION_DROP", "반추↓")]
CALVING_RAW = ("calving_confirmation", "calving_detection")


@dataclass
class Calving:
    animal_id: str
    label: str
    t: datetime
    parity: int | None
    confirmed: bool


@dataclass
class TransitionStats:
    farm_id: str
    n: int = 0                      # 완료 코호트 분만 두수(기간 window_days)
    n_first: int = 0                # 초산
    rates: dict[str, float] = field(default_factory=dict)    # 지표코드 → %
    counts: dict[str, int] = field(default_factory=dict)
    healthy_pct: float | None = None
    pre_rum_pct: float | None = None
    first_dim: float | None = None  # 첫 건강알림까지 평균 일수(알림 있던 소)
    confirm_pct: float | None = None  # 분만 입력률(농장 확인 기록이 있는 분만 비율)
    n_recent: int = 0
    healthy_recent: float | None = None
    ketosis_recent: float | None = None
    rates_first: dict[str, float] = field(default_factory=dict)   # 초산우 발생률
    rates_multi: dict[str, float] = field(default_factory=dict)   # 경산우 발생률
    # 최근 12개월(long_days) 완료 분만우 — 분만 적은 목장도 안정된 숫자, 계절 영향 평균
    n_year: int = 0
    healthy_year: float | None = None
    rates_year: dict[str, float] = field(default_factory=dict)
    monthly: list[dict[str, Any]] = field(default_factory=list)
    fresh: list[dict[str, Any]] = field(default_factory=list)     # 현재 신선우(분만 후 21일 이내)
    upcoming: list[dict[str, Any]] = field(default_factory=list)  # 분만 예정
    small: bool = True
    flags: list[str] = field(default_factory=list)
    cnt: dict[str, list[int]] = field(default_factory=dict)   # 지표 → [해당 두수, 분모 두수] (healthy, rate:KETOSIS, year:… 등)
    rank: int | None = None         # 무사통과율 순위(높을수록 1)
    ranked_of: int | None = None


def _pct(a: int, b: int) -> float | None:
    return round(a / b * 100, 1) if b else None


def _detail(r) -> dict:
    try:
        return json.loads(r["detail"]) if r["detail"] else {}
    except (TypeError, ValueError):
        return {}


def _calvings(rows: list[sqlite3.Row], as_of: datetime, merge_days: int) -> list[Calving]:
    by_animal: dict[str, list] = defaultdict(list)
    for r in rows:
        if (r["alert_type_raw"] or "").lower() in CALVING_RAW:
            t = from_db_time(r["occurred_at"])
            if t <= as_of:
                by_animal[r["animal_id"]].append((t, r))
    out: list[Calving] = []
    for aid, evs in by_animal.items():
        evs.sort(key=lambda x: x[0])
        groups: list[list] = []
        for t, r in evs:
            if groups and t - groups[-1][0][0] <= timedelta(days=merge_days):
                groups[-1].append((t, r))
            else:
                groups.append([(t, r)])
        for g in groups:
            det = [t for t, r in g if r["alert_type_raw"].lower() == "calving_detection"]
            conf = [(t, r) for t, r in g if r["alert_type_raw"].lower() == "calving_confirmation"]
            parity = next((_detail(r).get("number") for _, r in conf if _detail(r).get("number") is not None), None)
            t = min(det) if det else min(t for t, _ in conf)
            out.append(Calving(aid, g[0][1]["animal_label"], t, int(parity) if parity is not None else None, bool(conf)))
    return out


def farm_transition(conn: sqlite3.Connection, farm_id: str, as_of: datetime, mapper: AlertTypeMapper,
                    cfg: dict[str, Any]) -> TransitionStats:
    post = timedelta(days=int(cfg.get("post_days", 21)))
    pre = timedelta(days=int(cfg.get("pre_days", 21)))
    win = timedelta(days=int(cfg.get("window_days", 90)))
    recent = timedelta(days=int(cfg.get("recent_days", 30)))
    long = timedelta(days=int(cfg.get("long_days", 365)))
    start = as_of - post - max(win, long) - pre - timedelta(days=int(cfg.get("calving_merge_days", 14)) + 30)
    rows = conn.execute("SELECT * FROM v_alerts WHERE farm_id=? AND occurred_at > ? AND occurred_at <= ?",
                        (farm_id, to_db_time(start), to_db_time(as_of + timedelta(days=1)))).fetchall()
    health = set(mapper.codes("health"))
    ev_by_animal: dict[str, list[tuple[datetime, str, str]]] = defaultdict(list)
    for r in rows:
        if r["alert_type"] in health:
            ev_by_animal[r["animal_id"]].append((from_db_time(r["occurred_at"]), r["alert_type"], mapper.label(r["alert_type"])))
    calvings = _calvings(rows, as_of, int(cfg.get("calving_merge_days", 14)))
    st = TransitionStats(farm_id)

    def cow(c: Calving) -> dict[str, Any]:
        evs = ev_by_animal.get(c.animal_id, [])
        postev = [(t, code) for t, code, _ in evs if c.t <= t <= c.t + post]
        return {"has": {code for _, code in postev}, "any": bool(postev),
                "first": min(((t - c.t).total_seconds() / 86400 for t, _ in postev), default=None),
                "pre_rum": any(code == "RUMINATION_DROP" and c.t - pre <= t < c.t for t, code, _ in evs)}

    done_end = as_of - post
    cohort = [c for c in calvings if done_end - win < c.t <= done_end]
    st.n = len(cohort)
    st.small = st.n < int(cfg.get("min_calvings", 8))
    nc, dc = sum(c.confirmed for c in calvings if c.t > done_end - win), sum(1 for c in calvings if c.t > done_end - win)
    st.confirm_pct = _pct(nc, dc)
    st.cnt["confirm"] = [nc, dc]
    if cohort:
        res = [(c, cow(c)) for c in cohort]
        st.n_first = sum(1 for c, _ in res if c.parity == 1)
        first = [x for c, x in res if c.parity == 1]
        multi = [x for c, x in res if c.parity and c.parity > 1]
        for code, _ in INDICATORS:
            st.counts[code] = sum(1 for _, x in res if code in x["has"])
            st.rates[code] = _pct(st.counts[code], st.n)
            nf, nm = sum(code in x["has"] for x in first), sum(code in x["has"] for x in multi)
            st.rates_first[code] = _pct(nf, len(first))
            st.rates_multi[code] = _pct(nm, len(multi))
            st.cnt[f"rate:{code}"] = [st.counts[code], st.n]
            st.cnt[f"first:{code}"] = [nf, len(first)]
            st.cnt[f"multi:{code}"] = [nm, len(multi)]
        nh, npr = sum(1 for _, x in res if not x["any"]), sum(1 for _, x in res if x["pre_rum"])
        st.healthy_pct = _pct(nh, st.n)
        st.pre_rum_pct = _pct(npr, st.n)
        st.cnt["healthy"], st.cnt["pre_rum"] = [nh, st.n], [npr, st.n]
        firsts = [x["first"] for _, x in res if x["first"] is not None]
        st.first_dim = round(statistics.mean(firsts), 1) if firsts else None
        rec = [(c, x) for c, x in res if c.t > done_end - recent]
        st.n_recent = len(rec)
        hr, kr = sum(1 for _, x in rec if not x["any"]), sum(1 for _, x in rec if "KETOSIS" in x["has"])
        st.healthy_recent, st.ketosis_recent = _pct(hr, len(rec)), _pct(kr, len(rec))
        st.cnt["healthy_recent"], st.cnt["ketosis_recent"] = [hr, len(rec)], [kr, len(rec)]
    # 최근 12개월 (완료 분만우)
    yres = [(c, cow(c)) for c in calvings if done_end - long < c.t <= done_end]
    st.n_year = len(yres)
    if yres:
        hy = sum(1 for _, x in yres if not x["any"])
        st.healthy_year = _pct(hy, len(yres))
        st.cnt["healthy_year"] = [hy, len(yres)]
        for code, _ in INDICATORS:
            k = sum(1 for _, x in yres if code in x["has"])
            st.rates_year[code] = _pct(k, len(yres))
            st.cnt[f"year:{code}"] = [k, len(yres)]
    # 분만월별 (완료 코호트, 최근 12개월 — 계절 비교)
    months: dict[str, list] = defaultdict(list)
    for c, x in yres:
        months[f"{c.t:%Y-%m}"].append(x)
    st.monthly = []
    for m, xs in sorted(months.items()):
        hn, kn, rn = (sum(not x["any"] for x in xs), sum("KETOSIS" in x["has"] for x in xs),
                      sum("RUMINATION_DROP" in x["has"] for x in xs))
        st.monthly.append({"month": m, "n": len(xs), "healthy": _pct(hn, len(xs)), "ketosis": _pct(kn, len(xs)),
                           "rum": _pct(rn, len(xs)), "healthy_n": hn, "ketosis_n": kn, "rum_n": rn})
    # 현재 신선우
    for c in sorted((c for c in calvings if c.t > as_of - post), key=lambda c: c.t, reverse=True):
        evs = [(t, lbl) for t, _, lbl in ev_by_animal.get(c.animal_id, []) if c.t <= t <= as_of]
        types: dict[str, int] = defaultdict(int)
        for _, lbl in evs:
            types[lbl] += 1
        st.fresh.append({"label": c.label, "parity": c.parity, "dim": (as_of - c.t).days,
                         "calved": f"{c.t:%m/%d}", "alerts": ", ".join(f"{k}{'×' + str(v) if v > 1 else ''}" for k, v in types.items()),
                         "n": len(evs)})
    st.fresh.sort(key=lambda x: (-x["n"], x["dim"]))
    # 분만예정: 건유·임신감정·수정 이벤트의 분만예정일 (건유는 분만 약 60일 전이라 수집이력 안에 있음)
    erows = conn.execute(
        """SELECT * FROM v_alerts WHERE farm_id=? AND occurred_at > ? AND
           (detail LIKE '%expected_calving_date%' OR lower(alert_type_raw) IN ('abort','calving_confirmation','calving_detection'))""",
        (farm_id, to_db_time(as_of - timedelta(days=320)))).fetchall()
    st.upcoming = _upcoming(erows, calvings, as_of, int(cfg.get("upcoming_days", 14)))
    imminent = {r["animal_label"] for r in rows if (r["alert_type_raw"] or "").lower() == "fertility_105"
                and from_db_time(r["occurred_at"]) > as_of - timedelta(days=3)}
    calved_recent = {c.label for c in calvings if c.t > as_of - timedelta(days=3)}
    for u in st.upcoming:
        u["imminent"] = u["label"] in imminent
    for lbl in sorted(imminent - {u["label"] for u in st.upcoming} - calved_recent):
        st.upcoming.insert(0, {"label": lbl, "due": "-", "days": 0, "imminent": True})
    return st


def _upcoming(rows, calvings: list[Calving], as_of: datetime, days: int) -> list[dict[str, Any]]:
    latest: dict[str, tuple[datetime, dict, Any]] = {}
    aborted: dict[str, datetime] = {}
    for r in rows:
        raw = (r["alert_type_raw"] or "").lower()
        t = from_db_time(r["occurred_at"])
        if raw == "abort":
            aborted[r["animal_id"]] = max(t, aborted.get(r["animal_id"], t))
        d = _detail(r)
        if d.get("expected_calving_date") and d.get("pregnant") is not False:
            if r["animal_id"] not in latest or t > latest[r["animal_id"]][0]:
                latest[r["animal_id"]] = (t, d, r)
    last_calv = {c.animal_id: c.t for c in calvings}
    out = []
    for aid, (t, d, r) in latest.items():
        try:
            ecd = datetime.fromisoformat(str(d["expected_calving_date"]).replace("Z", "+00:00")).date()
        except ValueError:
            continue
        if aid in aborted and aborted[aid] >= t:
            continue
        if aid in last_calv and last_calv[aid] >= t:
            continue
        left = (ecd - as_of.date()).days
        if -3 <= left <= days:
            out.append({"label": r["animal_label"], "due": f"{ecd:%m/%d}", "days": left})
    return sorted(out, key=lambda x: x["days"])


def benchmark(stats: list[TransitionStats]) -> dict[str, float]:
    ok = [s for s in stats if not s.small and s.healthy_pct is not None]
    if len(ok) < 3:
        return {}
    b = {"n": len(ok), "healthy": statistics.median(s.healthy_pct for s in ok),
         "pre_rum": statistics.median(s.pre_rum_pct for s in ok if s.pre_rum_pct is not None)}
    yr = [s.healthy_year for s in stats if s.healthy_year is not None and s.n_year >= 20]
    if len(yr) >= 3:
        b["healthy_year"] = statistics.median(yr)
    for code, _ in INDICATORS:
        vals = [s.rates[code] for s in ok if s.rates.get(code) is not None]
        b[code] = statistics.median(vals) if vals else 0.0
    ranked = sorted(ok, key=lambda s: -s.healthy_pct)
    for i, s in enumerate(ranked, 1):
        s.rank, s.ranked_of = i, len(ranked)
    return b


def judge(st: TransitionStats, bench: dict[str, float], cfg: dict[str, Any]) -> None:
    if st.small or not bench or st.healthy_pct is None:
        return
    if st.healthy_pct <= bench["healthy"] - float(cfg.get("healthy_gap_pp", 15)):
        st.flags.append(f"전환기 무사통과 {st.healthy_pct:.0f}%(기준 {bench['healthy']:.0f}%)")
    gap = float(cfg.get("rate_gap_pp", 10))
    for code, lbl in INDICATORS[:3] + [INDICATORS[4]]:
        r = st.rates.get(code)
        if r is not None and r >= bench.get(code, 0) + gap:
            st.flags.append(f"신선우 {lbl} {r:.0f}%(기준 {bench.get(code, 0):.0f}%)")

