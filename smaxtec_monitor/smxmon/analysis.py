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
class GroupBehaviour:
    """그룹 1개의 최신 일자 행동 요약 (단위: 분/일, 함께 눕는 비율은 %)."""
    farm_id: str
    farm_name: str
    group_name: str
    n_animals: int | None
    date: str
    rum: float | None = None          # 반추 = 서서반추 + 누워반추
    rum_prev: float | None = None
    rum_7d: float | None = None
    lying: float | None = None
    lying_7d: float | None = None
    standing: float | None = None
    moving: float | None = None
    rum_lying_share: float | None = None   # 누워서 반추 비율 %
    sync: float | None = None
    sync_7d: float | None = None
    lactating: bool = False
    flags: list[str] = field(default_factory=list)
    rum_flag: bool = False

    @property
    def rum_delta(self) -> float | None:
        return None if self.rum is None or self.rum_7d is None else self.rum - self.rum_7d

    @property
    def lying_delta(self) -> float | None:
        return None if self.lying is None or self.lying_7d is None else self.lying - self.lying_7d


@dataclass
class FarmSummary:
    farm_id: str
    name: str
    herd_size: int | None
    last_ok_at: datetime | None
    counts_d0: dict[str, int] = field(default_factory=dict)
    health_d0: int = 0
    health_animals_d0: int = 0     # (호환용) = health_d0
    health_events_d0: int = 0      # 참고: 24시간 알림 건수(같은 개체 반복 포함)
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
    behaviour: list[GroupBehaviour] = field(default_factory=list)
    top_animals: list[dict[str, Any]] = field(default_factory=list)   # 최근 72h 건강알림 다발 개체
    # 7일 추세(두수): {구분: {날짜: 두수}}, {구분: 7일 합산 두수(중복 제외)}. 마지막 구분 = "건강 전체"
    trend_types: dict[str, dict[date, int]] = field(default_factory=dict)
    trend_types_total: dict[str, int] = field(default_factory=dict)
    transition: Any = None   # transition.TransitionStats
    history: Any = None      # history.FarmHistory (30·90일 누적, 12개월 월별)
    counts_72h: dict[str, int] = field(default_factory=dict)

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
    behaviour: list[GroupBehaviour] = field(default_factory=list)
    transition_bench: dict[str, float] = field(default_factory=dict)
    trials: list[Any] = field(default_factory=list)
    history_bench: dict[Any, Any] = field(default_factory=dict)   # {30: {구분: 100두당 중앙값}, 90: {...}}   # trials.TrialResult


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


# smaXtec 반추감소 이벤트 코드 → 사양 단계 (API 문서 Events 표 기준)
RUMI_STAGE = {
    "health_301": "건유", "health_306": "건유", "feeding_311": "건유",
    "health_302": "분만전", "health_307": "분만전", "feeding_312": "분만전",
    "health_303": "분만전후", "health_308": "분만전후", "feeding_313": "분만전후",
    "health_304": "신선우", "health_309": "신선우", "feeding_314": "신선우",
    "health_305": "착유", "health_310": "착유", "feeding_315": "착유",
    "health_317": "육성우", "health_318": "육성우",
}
STAGE_ADVICE = {
    "육성우": "육성우 그룹 우선 확인(우사 이동·합사, 육성우 사료/조사료 변경, 급이·음수)",
    "신선우": "분만 후 전환기 문제 의심(케토시스·제4위전위·저칼슘, 비유초기 농후사료 증량 속도)",
    "분만전후": "분만 전후 관리 확인(전환기 사료, 저칼슘·난산·후산정체)",
    "분만전": "건유후기(close-up) 사료·DCAD·사조 환경 확인",
    "건유": "건유우 사료(조사료 품질·섭취량) 확인",
    "착유": "착유우 TMR(배합 변경·입자도·수분·선별채식)·급이 관리 확인",
}


def _rumination_stage_note(ev, cluster: ClusterSignal, W: timedelta) -> str:
    """집단신호 창 안 반추감소 개체를 사양 단계별로 집계해 원인 범위를 좁힘."""
    by: dict[str, set[str]] = defaultdict(set)
    for t, r in ev:
        if r["alert_type"] == "RUMINATION_DROP" and cluster.window_start <= t <= cluster.window_end:
            by[RUMI_STAGE.get((r["alert_type_raw"] or "").lower(), "미상")].add(r["animal_label"])
    if not by:
        return ""
    parts = ", ".join(f"{k} {len(v)}두" for k, v in sorted(by.items(), key=lambda x: -len(x[1])))
    top, n = max(by.items(), key=lambda x: len(x[1]))
    total = sum(len(v) for v in by.values())
    advice = f" → {STAGE_ADVICE[top]}" if top in STAGE_ADVICE and len(n) / total >= 0.6 else ""
    return f"[반추감소 단계: {parts}]{advice}"


def _sum(*xs):
    return None if any(x is None for x in xs) else sum(xs)


def load_behaviour(conn: sqlite3.Connection, farm_id: str, farm_name: str, as_of: datetime,
                   cfg: dict[str, Any]) -> list[GroupBehaviour]:
    """그룹별 최신 완료일(as_of 기준 3일 이내) 행동 지표 + 판정."""
    rows = conn.execute(
        """SELECT b.group_id, b.date, b.metric, b.current, b.prev_day, b.last_7_days, g.name, g.n_animals
           FROM group_behaviour b LEFT JOIN animal_groups g USING (farm_id, group_id)
           WHERE b.farm_id=? AND b.date <= ? AND b.date >= ?
             AND b.date = (SELECT MAX(date) FROM group_behaviour x WHERE x.farm_id=b.farm_id
                           AND x.group_id=b.group_id AND x.date <= ? AND x.current IS NOT NULL)""",
        (farm_id, as_of.strftime("%Y-%m-%d"), (as_of - timedelta(days=3)).strftime("%Y-%m-%d"),
         as_of.strftime("%Y-%m-%d"))).fetchall()
    by: dict[str, dict[str, Any]] = defaultdict(dict)
    for r in rows:
        g = by[r["group_id"]]
        g.update(_name=r["name"] or r["group_id"][-6:], _n=r["n_animals"], _date=r["date"])
        g[r["metric"]] = (r["current"], r["prev_day"], r["last_7_days"])
    kw = [k.lower() for k in cfg.get("lactating_keywords", ["착유"])]
    out = []
    for gid, g in by.items():
        get = lambda m, i: (g.get(m) or (None, None, None))[i]  # noqa: E731
        gb = GroupBehaviour(farm_id, farm_name, g["_name"], g["_n"], g["_date"])
        gb.rum = _sum(get("rum_standing", 0), get("rum_lying", 0))
        gb.rum_prev = _sum(get("rum_standing", 1), get("rum_lying", 1))
        gb.rum_7d = _sum(get("rum_standing", 2), get("rum_lying", 2))
        gb.lying, gb.lying_7d = get("lying", 0), get("lying", 2)
        gb.standing, gb.moving = get("standing", 0), get("moving", 0)
        if gb.rum and get("rum_lying", 0) is not None:
            gb.rum_lying_share = round(get("rum_lying", 0) / gb.rum * 100, 1)
        sc, s7 = get("lying_sync", 0), get("lying_sync", 2)
        gb.sync = round(sc * 100, 1) if sc is not None else None
        gb.sync_7d = round(s7 * 100, 1) if s7 is not None else None
        gb.lactating = any(k in (gb.group_name or "").lower() for k in kw)

        tot = _sum(get("moving", 0), get("standing", 0), get("lying", 0))
        if tot is None or tot < float(cfg.get("min_day_minutes", 1380)):
            gb.flags.append("데이터 불완전(판정 제외)")
            out.append(gb)
            continue
        d = gb.rum_delta
        if d is not None and gb.rum_7d and d <= -float(cfg.get("rum_drop_min", 30)) \
                and d / gb.rum_7d * 100 <= -float(cfg.get("rum_drop_pct", 7)):
            gb.rum_flag = True
            gb.flags.append(f"반추 {d:+.0f}분({d / gb.rum_7d * 100:+.0f}%)")
        ld = gb.lying_delta
        if ld is not None and ld <= -float(cfg.get("lying_drop_min", 60)):
            gb.flags.append(f"누운 시간 감소 {ld:+.0f}분")
        lim = float(cfg.get("lying_min_hours_lactating", 9))
        if gb.lactating and gb.lying is not None and gb.lying / 60 < lim:
            gb.flags.append(f"누운 시간 부족 {gb.lying / 60:.1f}h(기준 {lim:g}h)")
        if gb.sync is not None and gb.sync_7d is not None and gb.sync_7d - gb.sync >= float(cfg.get("lying_sync_drop_pp", 10)):
            gb.flags.append(f"함께 눕기 감소 {gb.sync - gb.sync_7d:+.0f}%p")
        out.append(gb)
    out.sort(key=lambda x: (not x.lactating, x.group_name or ""))
    return out


def analyze(conn: sqlite3.Connection, as_of: datetime, mapper: AlertTypeMapper, rules: dict[str, Any],
            overrides: dict[str, dict[str, Any]] | None = None, light: bool = False) -> Analysis:
    """light=True: 판정 검증(과거 날짜 반복 계산)용 — 장기 누적·전환기·최근목록 생략(전환기 가중치 미반영)."""
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

        # 모든 집계는 '두수' = 기간 안에 알림이 나온 개체 수(같은 개체 반복은 1두)
        def animals(t0, t1, types=None):
            return len({r["animal_id"] for t, r in ev if t0 < t <= t1 and (types is None or r["alert_type"] in types)})

        d0 = [r for t, r in ev if as_of - H24 < t <= as_of]
        per_type: dict[str, set] = defaultdict(set)
        for r in d0:
            per_type[r["alert_type"]].add(r["animal_id"])
        s.counts_d0 = defaultdict(int, {k: len(v) for k, v in per_type.items()})
        avail = min(7, max(0, hist - 1))
        s.health_d0 = animals(as_of - H24, as_of, health)
        s.health_animals_d0 = s.health_d0
        s.health_events_d0 = sum(1 for r in d0 if r["alert_type"] in health)
        s.health_d1 = animals(as_of - 2 * H24, as_of - H24, health)
        if avail > 0:   # 직전 avail 개 24시간 구간 각각의 두수 평균
            s.health_7d_avg = round(sum(animals(as_of - (k + 1) * H24, as_of - k * H24, health)
                                        for k in range(1, avail + 1)) / avail, 1)

        for d in trend_dates:
            s.trend[d] = len({r["animal_id"] for t, r in ev if t.date() == d and r["alert_type"] in health})
        cats = [(c["label"], set(c["types"])) for c in rules.get("report", {}).get("trend_categories", [])]
        cats.append(("건강 전체", health))
        in_range = [(t, r) for t, r in ev if t.date() in s.trend]
        for label, types in cats:
            s.trend_types[label] = {d: len({r["animal_id"] for t, r in in_range if t.date() == d and r["alert_type"] in types})
                                    for d in trend_dates}
            s.trend_types_total[label] = len({r["animal_id"] for t, r in in_range if r["alert_type"] in types})

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
        c_ev = [(t, r["animal_label"], r["alert_type"]) for t, r in ev if r["alert_type"] in cl_types]
        ph_ev = [(t, r["animal_id"]) for t, r in ev if r["alert_type"] == "PH_LOW"]
        baseline = None
        if avail > 0:  # 직전 avail일 동안 W시간당 평균 알림 건수(개체 중복 포함 → 보수적 기준)
            prev_n = sum(1 for t, _, _ in c_ev if as_of - (1 + avail) * H24 < t <= as_of - H24)
            baseline = prev_n / (avail * 24 / (W.total_seconds() / 3600))
        s.cluster = detect_cluster(c_ev, farm_id=s.farm_id, farm_name=s.name, herd_size=herd,
                                   window_end_from=as_of - H24, window_end_to=as_of, rules=rules,
                                   ph_events=ph_ev, baseline=baseline)
        if s.cluster:
            stage_note = _rumination_stage_note(ev, s.cluster, W)
            if stage_note:
                s.cluster.interpretation += " " + stage_note
            hs = sum(1 for t, r in ev if r["alert_type"] == "HEAT_STRESS"
                     and s.cluster.window_start - W <= t <= s.cluster.window_end)
            if hs and s.cluster.animals_by_type.get("TEMP_HIGH", 0):
                s.cluster.interpretation = f"[고온스트레스 경보 {hs}건 동반 → 환기·음수·차광 우선 점검] " + s.cluster.interpretation
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
            + pr.get("w_group_alarm", 15.0) * min(1, s.counts_d0.get("GROUP_TEMP", 0) + s.counts_d0.get("GROUP_RUMINATION", 0))
            + pr.get("w_ketosis", 2.0) * s.counts_d0.get("KETOSIS", 0)
            + pr["w_long_active"] * s.long_active + pr["w_calving"] * s.counts_d0.get("CALVING", 0), 1)
        s.grade = ("긴급" if s.score >= pr["urgent"] else "우선" if s.score >= pr["high"]
                   else "관찰" if s.score >= pr["watch"] else "정상")
        if s.cluster:
            span_h = (s.cluster.window_end - s.cluster.window_start).total_seconds() / 3600
            s.reasons.append(f"집단신호 {s.cluster.n_animals}두/{span_h:.0f}시간")
        if s.surge:
            s.reasons.append(f"급증({s.surge_reason})")
        if ph:
            s.reasons.append(f"pH저하/산독증위험 {ph}두")
        grp = s.counts_d0.get("GROUP_TEMP", 0) + s.counts_d0.get("GROUP_RUMINATION", 0)
        if grp:
            s.reasons.append(f"그룹경보(체온/반추) {grp}회")
        if s.counts_d0.get("KETOSIS", 0):
            s.reasons.append(f"케토시스 의심 {s.counts_d0['KETOSIS']}두")
        if s.long_active:
            s.reasons.append(f"장기미해제 {s.long_active}건")
        if s.stale:
            s.reasons.append("⚠데이터 지연")
        # 최근 72시간 개체별 건강알림 (방문 시 확인할 개체)
        by_animal: dict[str, dict[str, Any]] = {}
        set72: dict[str, set] = defaultdict(set)
        for t, r in ev:
            if t <= as_of - 3 * H24 or t > as_of:
                continue
            set72[r["alert_type"]].add(r["animal_id"])
            if r["alert_type"] not in health:
                continue
            a = by_animal.setdefault(r["animal_id"], {"label": r["animal_label"], "status": r["lactation_status"] or "",
                                                     "n": 0, "types": defaultdict(int), "last": t})
            a["n"] += 1
            a["types"][mapper.label(r["alert_type"])] += 1
            a["last"] = max(a["last"], t)
        s.top_animals = sorted(by_animal.values(), key=lambda a: (-len(a["types"]), -a["n"], -a["last"].timestamp()))[:10]
        for a in s.top_animals:
            a["types"] = ", ".join(f"{k}{'×' + str(v) if v > 1 else ''}" for k, v in a["types"].items())
        s.counts_72h = {k: len(v) for k, v in set72.items()}

        # 그룹 행동(behaviour)
        bcfg = rules.get("behaviour", {})
        if bcfg.get("enabled", True):
            s.behaviour = load_behaviour(conn, s.farm_id, s.name, as_of, bcfg)
            rum_groups = [g for g in s.behaviour if g.rum_flag]
            other = sum(len(g.flags) - g.rum_flag for g in s.behaviour if not g.flags[:1] == ["데이터 불완전(판정 제외)"])
            s.score = round(s.score + pr.get("w_behaviour_rum", 12) * min(2, len(rum_groups))
                            + pr.get("w_behaviour_other", 4) * min(3, other), 1)
            s.grade = ("긴급" if s.score >= pr["urgent"] else "우선" if s.score >= pr["high"]
                       else "관찰" if s.score >= pr["watch"] else "정상")
            flagged = [f"{g.group_name}: {', '.join(g.flags)}" for g in s.behaviour
                       if g.flags and g.flags != ["데이터 불완전(판정 제외)"]]
            if flagged:
                s.reasons.append("행동[" + " / ".join(flagged[:3]) + "]")
        s.counts_d0 = dict(s.counts_d0)
        summaries.append(s)

    # 장기 알림 지표 (30·90일 누적, 12개월 월별)
    from .history import farm_history, rank_and_bench
    hcats = [(c["label"], set(c["types"])) for c in rules.get("report", {}).get("trend_categories", [])]
    hcats.append(("건강 전체", health))
    if light:
        summaries.sort(key=lambda x: (-x.score, -x.health_d0, x.name))
        return Analysis(as_of, summaries, clusters, [], [], [], hist, [], {}, history_bench={})
    for s in summaries:
        s.history = farm_history(conn, s.farm_id, as_of, hcats, s.herd_size)
    hbench = rank_and_bench([(s, s.history, s.herd_size) for s in summaries])

    # 전환기 성적 (담당 목장 중앙값 대비 판정 → 점수 반영)
    tcfg = rules.get("transition", {})
    tbench: dict[str, float] = {}
    if tcfg.get("enabled", True):
        from .transition import benchmark, farm_transition, judge
        for s in summaries:
            s.transition = farm_transition(conn, s.farm_id, as_of, mapper, tcfg)
        tbench = benchmark([s.transition for s in summaries])
        for s in summaries:
            judge(s.transition, tbench, tcfg)
            if s.transition.flags:
                s.score = round(s.score + pr.get("w_transition", 8), 1)
                s.grade = ("긴급" if s.score >= pr["urgent"] else "우선" if s.score >= pr["high"]
                           else "관찰" if s.score >= pr["watch"] else "정상")
                s.reasons.append(s.transition.flags[0])

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
    return Analysis(as_of, summaries, clusters, recent, long_rows, runs, hist,
                    [g for f in summaries for g in f.behaviour], tbench, history_bench=hbench)
