"""이슈 판정: 목장별 집계, 급증, 단시간 다두수(집단) 신호, 장기 미해제, 방문 우선순위.

⚠ 여기서 나오는 '의심 신호'는 센서 알림 패턴에 근거한 선별(screening) 결과이며 진단이 아님.
   현장 확인(사료·급여·환경) 및 수의사 판단이 필요.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from .normalize import AlertTypeMapper, from_db_time, to_db_time

H24 = timedelta(hours=24)


@dataclass
class ClusterSignal:
    farm_id: str
    farm_name: str
    window_start: datetime
    window_end: datetime
    animals: list[str]
    animals_by_type: dict[str, int]
    ph_low_count: int
    herd_pct: float | None
    interpretation: str

    @property
    def n_animals(self) -> int:
        return len(self.animals)

    def key(self) -> str:
        """긴급알림 중복발송 방지 키(목장 + 창 시작 시각 3시간 단위)."""
        bucket = self.window_start.replace(minute=0, second=0)
        bucket = bucket.replace(hour=bucket.hour - bucket.hour % 3)
        return f"cluster:{self.farm_id}:{bucket:%Y%m%d%H}"


@dataclass
class FarmSummary:
    farm_id: str
    name: str
    herd_size: int | None
    last_ok_at: datetime | None
    counts_d0: dict[str, int] = field(default_factory=dict)
    health_d0: int = 0
    health_d1: int = 0
    health_7d_avg: float | None = None
    surge: bool = False
    surge_reason: str = ""
    cluster: ClusterSignal | None = None
    long_active: int = 0
    avg_duration_h: float | None = None
    stale: bool = False
    trend: dict[date, int] = field(default_factory=dict)
    score: float = 0.0
    grade: str = "정상"
    reasons: list[str] = field(default_factory=list)

    @property
    def per100(self) -> float | None:
        return round(self.health_d0 / self.herd_size * 100, 1) if self.herd_size else None


@dataclass
class Analysis:
    as_of: datetime
    farms: list[FarmSummary]
    clusters: list[ClusterSignal]
    recent_alerts: list[dict[str, Any]]
    long_active_alerts: list[dict[str, Any]]
    runs: list[dict[str, Any]]
    history_days: int


def _alerts_between(conn: sqlite3.Connection, start: datetime, end: datetime) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM v_alerts WHERE occurred_at > ? AND occurred_at <= ? ORDER BY occurred_at",
        (to_db_time(start), to_db_time(end)),
    ).fetchall()


def _history_days(conn: sqlite3.Connection, as_of: datetime) -> int:
    """수집 이력 일수(7일평균 계산 시 데이터 없는 기간을 0으로 치지 않기 위함)."""
    row = conn.execute("SELECT MIN(started_at) FROM collection_runs WHERE status IN ('ok','partial')").fetchone()
    first = from_db_time(row[0]) if row and row[0] else None
    # 알림 발생시각이 첫 수집보다 이전일 수 있으므로(목록에 과거 알림 표시) 알림 기준도 함께 확인
    row2 = conn.execute("SELECT MIN(occurred_at) FROM alerts").fetchone()
    first_alert = from_db_time(row2[0]) if row2 and row2[0] else None
    cands = [d for d in (first, first_alert) if d]
    if not cands:
        return 0
    return max(0, int((as_of - min(cands)) / H24))


def detect_cluster(events: list[tuple[datetime, str, str]], *, farm_id: str, farm_name: str,
                   herd_size: int | None, window_end_from: datetime, window_end_to: datetime,
                   rules: dict[str, Any], ph_events: list[tuple[datetime, str]],
                   baseline: float | None = None) -> ClusterSignal | None:
    """슬라이딩 윈도우: (t-W, t] 안의 서로 다른 개체 수가 최대인 창을 찾음.

    events: (발생시각, 개체, 알림코드) — 체온상승/반추감소 등 cluster.types
    """
    c = rules["cluster"]
    W = timedelta(hours=float(c.get("window_hours", 12)))
    min_animals = int(c.get("min_animals", 3))
    min_pct = float(c.get("min_herd_pct", 0))
    events = sorted(events)
    best: tuple[int, int, int] | None = None  # (n, i, j)
    i = 0
    for j, (tj, _, _) in enumerate(events):
        while events[i][0] <= tj - W:
            i += 1
        if not (window_end_from < tj <= window_end_to):
            continue
        n = len({a for _, a, _ in events[i:j + 1]})
        if best is None or n > best[0]:
            best = (n, i, j)
    if best is None:
        return None
    n, i, j = best
    pct = round(n / herd_size * 100, 1) if herd_size else None
    if n < min_animals or (pct is not None and pct < min_pct):
        return None
    # 평소(직전 7일) 같은 길이 창에 나오던 개체 수의 N배 이상이어야 '집단'으로 인정
    ratio = float(c.get("min_ratio_vs_baseline", 0))
    if baseline is not None and ratio > 0 and n < baseline * ratio:
        return None

    win = events[i:j + 1]
    w_start, w_end = win[0][0], win[-1][0]
    by_type: dict[str, set[str]] = defaultdict(set)
    for _, a, t in win:
        by_type[t].add(a)
    counts = {t: len(s) for t, s in by_type.items()}
    ph_n = sum(1 for t, _ in ph_events if w_end - W - timedelta(hours=6) < t <= w_end + timedelta(hours=6))
    return ClusterSignal(farm_id, farm_name, w_start, w_end, sorted({a for _, a, _ in win}), counts,
                         ph_n, pct, interpret_cluster(counts, ph_n, min_animals, rules))


def interpret_cluster(counts: dict[str, int], ph_n: int, min_animals: int, rules: dict[str, Any]) -> str:
    t, r = counts.get("TEMP_HIGH", 0), counts.get("RUMINATION_DROP", 0)
    ph_need = int(rules["cluster"].get("ph_low_for_sara", 1))
    if r >= min_animals and ph_n >= ph_need:
        return ("반추 감소 다발 + pH 저하 → 아급성 반추위산독증(SARA) 의심. "
                "농후사료 비율·급여량 변화, TMR 입자도/선별채식, 사료 전환 시점, 급이 간격 확인")
    if t >= min_animals and r >= min_animals:
        return ("체온 상승·반추 감소 동시 다발 → 집단 질병(감염성) 가능성. "
                "수의사 진료 우선, 발병우 분리, 음수·사료 위생 점검")
    if t >= min_animals:
        return ("체온 상승 다발 → 감염성 질병 또는 고온 스트레스(THI) 의심. "
                "기상·환기·음수 확인 후 수의사 상담")
    if r >= min_animals:
        return ("반추 감소 다발 → 사료·급여 문제 의심. 최근 배합/원료 변경, 사료 변질·곰팡이독소, "
                "급이 시간·잔량, 음수 확인")
    return "체온·반추 알림 개체 다발 → 원인 확인 필요(분만 몰림·환경 변화 포함)"


def analyze(conn: sqlite3.Connection, as_of: datetime, mapper: AlertTypeMapper, rules: dict[str, Any],
            overrides: dict[str, dict[str, Any]] | None = None) -> Analysis:
    overrides = overrides or {}
    health = set(mapper.codes("health"))
    trend_days = int(rules.get("report", {}).get("trend_days", 7))
    cl_types = set(rules["cluster"].get("types", ["TEMP_HIGH", "RUMINATION_DROP"]))
    W = timedelta(hours=float(rules["cluster"].get("window_hours", 12)))
    hist = _history_days(conn, as_of)

    farms_rows = conn.execute("SELECT * FROM farms ORDER BY name").fetchall()
    lookback_start = min(as_of - 8 * H24, datetime.combine(as_of.date() - timedelta(days=trend_days - 1),
                                                           datetime.min.time()) - timedelta(seconds=1))
    lookback_start = min(lookback_start, as_of - H24 - W)
    rows = _alerts_between(conn, lookback_start, as_of)
    by_farm: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        by_farm[r["farm_id"]].append(r)

    stale_h = float(rules.get("freshness", {}).get("stale_hours", 6))
    long_h = float(rules.get("long_active", {}).get("hours", 48))
    pr = rules["priority"]
    trend_dates = [as_of.date() - timedelta(days=k) for k in range(trend_days - 1, -1, -1)]

    summaries: list[FarmSummary] = []
    clusters: list[ClusterSignal] = []
    for f in farms_rows:
        ov = overrides.get(f["farm_id"], {})
        if ov.get("exclude"):
            continue
        herd = ov.get("herd_size") or f["herd_size"]
        s = FarmSummary(f["farm_id"], ov.get("alias") or f["name"], herd, from_db_time(f["last_ok_at"]))
        fa = by_farm.get(f["farm_id"], [])
        ev = [(from_db_time(r["occurred_at"]), r) for r in fa]

        d0 = [r for t, r in ev if as_of - H24 < t <= as_of]
        s.counts_d0 = defaultdict(int)
        for r in d0:
            s.counts_d0[r["alert_type"]] += 1
        avail = min(7, max(0, hist - 1))
        s.health_d0 = sum(1 for r in d0 if r["alert_type"] in health)
        s.health_d1 = sum(1 for t, r in ev if as_of - 2 * H24 < t <= as_of - H24 and r["alert_type"] in health)
        if avail > 0:
            prev = sum(1 for t, r in ev if as_of - (1 + avail) * H24 < t <= as_of - H24 and r["alert_type"] in health)
            s.health_7d_avg = round(prev / avail, 1)

        for d in trend_dates:
            s.trend[d] = sum(1 for t, r in ev if t.date() == d and r["alert_type"] in health)

        # 급증
        sg = rules["surge"]
        if s.health_d0 >= sg["min_count"]:
            inc_prev = s.health_d0 - s.health_d1
            if s.health_d0 >= s.health_d1 * sg["ratio_vs_prev_day"] and inc_prev >= sg["min_increase"]:
                s.surge, s.surge_reason = True, f"전일 {s.health_d1}→{s.health_d0}"
            elif (s.health_7d_avg is not None and s.health_d0 >= s.health_7d_avg * sg["ratio_vs_7d_avg"]
                  and s.health_d0 - s.health_7d_avg >= sg["min_increase"]):
                s.surge, s.surge_reason = True, f"7일평균 {s.health_7d_avg}→{s.health_d0}"

        # 집단 신호
        c_ev = [(t, r["animal_id"], r["alert_type"]) for t, r in ev if r["alert_type"] in cl_types]
        ph_ev = [(t, r["animal_id"]) for t, r in ev if r["alert_type"] == "PH_LOW"]
        baseline = None
        if avail > 0:  # 직전 avail일 동안 W시간당 평균 알림 건수(개체 중복 포함 → 보수적 기준)
            prev_n = sum(1 for t, _, _ in c_ev if as_of - (1 + avail) * H24 < t <= as_of - H24)
            baseline = prev_n / (avail * 24 / (W.total_seconds() / 3600))
        s.cluster = detect_cluster(c_ev, farm_id=s.farm_id, farm_name=s.name, herd_size=herd,
                                   window_end_from=as_of - H24, window_end_to=as_of, rules=rules,
                                   ph_events=ph_ev, baseline=baseline)
        if s.cluster:
            clusters.append(s.cluster)

        # 장기 미해제 / 평균 지속시간
        s.long_active = conn.execute(
            f"""SELECT COUNT(*) FROM alerts WHERE farm_id=? AND status='active'
                AND alert_type IN ({','.join('?' * len(health))})
                AND (julianday(?) - julianday(occurred_at)) * 24 >= ?""",
            (s.farm_id, *health, to_db_time(as_of), long_h),
        ).fetchone()[0]
        dur = [r["duration_hours"] for t, r in ev
               if r["status"] == "resolved" and r["alert_type"] in health and t > as_of - 7 * H24]
        s.avg_duration_h = round(sum(dur) / len(dur), 1) if dur else None

        s.stale = s.last_ok_at is None or (as_of - s.last_ok_at) > timedelta(hours=stale_h)

        # 우선순위
        ph = s.counts_d0.get("PH_LOW", 0)
        s.score = round(
            pr["w_health_alert"] * s.health_d0 + pr["w_ph_low"] * ph
            + (pr["w_surge"] if s.surge else 0) + (pr["w_cluster"] if s.cluster else 0)
            + pr["w_long_active"] * s.long_active + pr["w_calving"] * s.counts_d0.get("CALVING", 0), 1)
        s.grade = ("긴급" if s.score >= pr["urgent"] else "우선" if s.score >= pr["high"]
                   else "관찰" if s.score >= pr["watch"] else "정상")
        if s.cluster:
            span_h = (s.cluster.window_end - s.cluster.window_start).total_seconds() / 3600
            s.reasons.append(f"집단신호 {s.cluster.n_animals}두/{span_h:.0f}시간")
        if s.surge:
            s.reasons.append(f"급증({s.surge_reason})")
        if ph:
            s.reasons.append(f"pH저하 {ph}건")
        if s.long_active:
            s.reasons.append(f"장기미해제 {s.long_active}건")
        if s.stale:
            s.reasons.append("⚠데이터 지연")
        s.counts_d0 = dict(s.counts_d0)
        summaries.append(s)

    summaries.sort(key=lambda x: (-x.score, -x.health_d0, x.name))

    recent = [dict(r) for r in conn.execute(
        "SELECT * FROM v_alerts WHERE occurred_at > ? AND occurred_at <= ? ORDER BY farm_name, occurred_at DESC",
        (to_db_time(as_of - H24), to_db_time(as_of))).fetchall()]
    long_rows = [dict(r) for r in conn.execute(
        f"""SELECT *, ROUND((julianday(?) - julianday(occurred_at)) * 24, 1) AS open_hours
            FROM v_alerts WHERE status='active' AND alert_type IN ({','.join('?' * len(health))})
              AND (julianday(?) - julianday(occurred_at)) * 24 >= ?
            ORDER BY open_hours DESC""",
        (to_db_time(as_of), *health, to_db_time(as_of), long_h)).fetchall()]
    runs = [dict(r) for r in conn.execute(
        "SELECT * FROM collection_runs ORDER BY run_id DESC LIMIT 30").fetchall()]
    return Analysis(as_of, summaries, clusters, recent, long_rows, runs, hist)
