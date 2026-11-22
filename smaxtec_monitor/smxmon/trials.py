"""사료·첨가제 투입 전후 비교 (현장 실증용).

입력: 프로그램 폴더의 '투입기록.xlsx' (9_TRIALS.bat 로 열기)
비교: 투입 전 N일  vs  투입 후 (적응기간 제외) N일
  - 그룹 행동(일별): 반추(분), 누운시간(h), 누워반추 비율(%), 함께 눕는 비율(%), 서 있기·이동(h)
  - 건강알림 발생(100두·일당 두수): 반추감소, 체온상승, 케토시스, pH저하, 건강 전체
  - 대조: 같은 날짜 구간의 다른 담당 목장 변화(중앙값) → 순효과 = 이 목장 변화 − 대조 변화
통계: 행동 = Welch t-검정(일별 평균), 알림 = 조건부 이항검정(발생률 비교)
⚠ 무작위 대조시험이 아닌 관찰 비교. 일별 값은 서로 연관돼 있어 p값은 참고용. 인과 단정 금지.
"""
from __future__ import annotations

import math
import sqlite3
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import BASE_DIR
from .normalize import AlertTypeMapper

TRIALS_FILE = BASE_DIR / "투입기록.xlsx"
HEADERS = ["목장(이름 일부 또는 ID)", "시작일", "제품·변경 내용", "구분", "대상그룹(쉼표, 비우면 착유우)",
           "비교기간(일)", "적응기간(일)", "메모"]

# (키, 이름, 단위, 좋은 방향 +1/-1/0)
BEH_METRICS = [("rum", "반추", "분/일", 1), ("lying", "누운 시간", "시간/일", 1), ("share", "누워반추 비율", "%", 1),
               ("sync", "함께 눕는 비율", "%", 1), ("standing", "서 있기", "시간/일", 0), ("moving", "이동·활동", "시간/일", 0)]
ALERT_METRICS = [("RUMINATION_DROP", "반추감소 알림"), ("TEMP_HIGH", "체온상승 알림"), ("KETOSIS", "케토시스 의심"),
                 ("PH_LOW", "pH저하 알림"), ("_HEALTH", "건강알림 전체")]



def _ph_on() -> bool:
    from .config import load_toml
    try:
        return bool(load_toml("rules.toml", required=False).get("features", {}).get("ph", True))
    except Exception:  # noqa: BLE001
        return True


if not _ph_on():   # pH 볼루스를 안 쓰면 pH 지표 제외
    ALERT_METRICS = [m for m in ALERT_METRICS if m[0] != "PH_LOW"]


# ───────────────────────── 통계 ─────────────────────────
def _betacf(a, b, x):
    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    d = 1 / (d if abs(d) > 1e-30 else 1e-30)
    h = d
    for m in range(1, 200):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1 + aa * d
        d = 1 / (d if abs(d) > 1e-30 else 1e-30)
        c = 1 + aa / c if abs(1 + aa / c) > 1e-30 else 1e-30
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1 + aa * d
        d = 1 / (d if abs(d) > 1e-30 else 1e-30)
        c = 1 + aa / c if abs(1 + aa / c) > 1e-30 else 1e-30
        de = d * c
        h *= de
        if abs(de - 1) < 3e-12:
            break
    return h


def _betai(a, b, x):
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x))
    return bt * _betacf(a, b, x) / a if x < (a + 1) / (a + b + 2) else 1 - bt * _betacf(b, a, 1 - x) / b


def welch_p(a: list[float], b: list[float]) -> float | None:
    """두 집단 평균 차이 Welch t-검정 양측 p."""
    if len(a) < 3 or len(b) < 3:
        return None
    va, vb = statistics.variance(a), statistics.variance(b)
    se2 = va / len(a) + vb / len(b)
    if se2 <= 0:
        return 1.0 if statistics.mean(a) == statistics.mean(b) else 0.0
    t = (statistics.mean(b) - statistics.mean(a)) / math.sqrt(se2)
    df = se2 ** 2 / ((va / len(a)) ** 2 / (len(a) - 1) + (vb / len(b)) ** 2 / (len(b) - 1))
    return _betai(df / 2, 0.5, df / (df + t * t))


def rate_p(k1: int, t1: float, k2: int, t2: float, ratio0: float = 1.0) -> float | None:
    """두 기간 발생 건수 비교(포아송, 조건부 이항 정확검정) 양측 p.
    ratio0 = 귀무가설의 '후/전 발생률 비' (대조 목장의 비율을 넣으면 대조 보정 검정)."""
    k = k1 + k2
    if k == 0 or t1 <= 0 or t2 <= 0 or ratio0 <= 0:
        return None
    p0 = t2 * ratio0 / (t1 + t2 * ratio0)

    def lp(i):
        return (math.lgamma(k + 1) - math.lgamma(i + 1) - math.lgamma(k - i + 1)
                + (i * math.log(p0) if i else 0) + ((k - i) * math.log(1 - p0) if k - i else 0))
    obs = lp(k2)
    return min(1.0, sum(math.exp(lp(i)) for i in range(k + 1) if lp(i) <= obs + 1e-9))


# ───────────────────────── 입력 파일 ─────────────────────────
@dataclass
class Trial:
    row: int
    farm_key: str
    start: date
    product: str
    kind: str = ""
    groups: list[str] = field(default_factory=list)
    days: int = 21
    adapt: int = 7
    memo: str = ""
    farm_id: str | None = None
    farm_name: str = ""
    error: str = ""


def ensure_template(path: Path = TRIALS_FILE) -> Path:
    if path.exists():
        return path
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = "투입기록"
    ws.append(HEADERS)
    for c in ws[1]:
        c.font = Font(name="맑은 고딕", bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F4E78")
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.append(["예시) 봉석", "2026-11-01", "예시) 효모배양물 50g/두/일", "첨가제", "착유우", 21, 7,
               "← 이 줄처럼 아래에 입력. '예시'로 시작하는 줄은 무시됨"])
    for c in ws[2]:
        c.font = Font(name="맑은 고딕", color="808080", italic=True)
    for col, w in zip("ABCDEFGH", [22, 12, 30, 10, 26, 11, 11, 40]):
        ws.column_dimensions[col].width = w
    ws.row_dimensions[1].height = 32
    ws.freeze_panes = "A2"
    g = wb.create_sheet("작성방법")
    for line in [
        "■ 한 줄 = 투입(또는 사료 변경) 1건",
        "목장: 목장 이름 일부(예: 봉석) 또는 smaXtec 목장 ID",
        "시작일: 투입·변경을 시작한 날 (예: 2026-11-01)",
        "제품·변경 내용: 제품명, 급여량 등 (예: 보호콜린 60g/두/일)",
        "구분: 첨가제 / 사료배합 / 조사료 / 관리 등 자유롭게",
        "대상그룹: smaXtec 그룹 이름 일부, 여러 개면 쉼표 (비우면 '착유' 그룹 자동 선택)",
        "비교기간: 투입 전·후 각각 며칠씩 비교할지 (기본 21일, 7~42일 권장)",
        "적응기간: 투입 직후 제외할 일수 (기본 7일 — 반추위 적응 기간)",
        "",
        "■ 결과: 매일 리포트 만들 때 자동 계산 → 앱 '투입비교' 탭, reports\\투입비교_날짜.xlsx",
        "  투입 후 데이터가 7일 이상 쌓여야 계산됩니다 (진행 중이면 '진행중' 표시)",
        "■ 해석 주의: 같은 기간 날씨·사료 로트·우군 변동이 함께 영향을 줍니다.",
        "  '대조 목장 변화'를 뺀 순효과와 p값(0.05 미만이면 우연일 가능성 낮음)을 함께 보세요.",
    ]:
        g.append([line])
    g.column_dimensions["A"].width = 110
    wb.save(path)
    return path


def _to_date(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if v is None:
        return None
    s = str(v).strip().replace(".", "-").replace("/", "-")
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def load_trials(conn: sqlite3.Connection, path: Path = TRIALS_FILE) -> list[Trial]:
    if not path.exists():
        return []
    from openpyxl import load_workbook
    ws = load_workbook(path, data_only=True).worksheets[0]
    farms = [(r["farm_id"], r["name"]) for r in conn.execute("SELECT farm_id, name FROM farms")]
    out: list[Trial] = []
    for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        row = list(row) + [None] * 8
        key = str(row[0] or "").strip()
        if not key or key.startswith("예시"):
            continue
        t = Trial(i, key, _to_date(row[1]) or date.min, str(row[2] or "").strip(), str(row[3] or "").strip(),
                  [g.strip() for g in str(row[4] or "").replace("，", ",").split(",") if g.strip()],
                  int(row[5]) if str(row[5] or "").strip().isdigit() else 21,
                  int(row[6]) if str(row[6] or "").strip().isdigit() else 7, str(row[7] or "").strip())
        if t.start == date.min:
            t.error = "시작일을 읽을 수 없음(예: 2026-11-01)"
        t.days = max(5, min(60, t.days))
        t.adapt = max(0, min(30, t.adapt))
        hit = [f for f in farms if f[0] == key] or [f for f in farms if key in f[1]]
        if len(hit) == 1:
            t.farm_id, t.farm_name = hit[0]
        elif not hit:
            t.error = t.error or f"목장 '{key}' 을(를) 찾지 못함"
        else:
            t.error = t.error or f"'{key}' 에 해당하는 목장이 여러 곳: {', '.join(h[1] for h in hit[:4])}"
        out.append(t)
    return out


def needed_from(trials: list[Trial]) -> date | None:
    ok = [t for t in trials if not t.error]
    return min((t.start - timedelta(days=t.days + 3) for t in ok), default=None)


# ───────────────────────── 계산 ─────────────────────────
@dataclass
class MetricRow:
    key: str
    name: str
    unit: str
    good: int
    before: float | None
    after: float | None
    n_before: int = 0
    n_after: int = 0
    p: float | None = None
    ctrl: float | None = None        # 대조 목장 변화(중앙값)
    ctrl_n: int = 0

    @property
    def delta(self):
        return None if self.before is None or self.after is None else self.after - self.before

    @property
    def pct(self):
        return None if self.delta is None or not self.before else self.delta / self.before * 100

    @property
    def net(self):
        return None if self.delta is None or self.ctrl is None else self.delta - self.ctrl

    @property
    def verdict(self) -> str:
        if self.delta is None:
            return "데이터 부족"
        if self.good == 0:
            return "참고"
        eff = self.net if self.net is not None else self.delta   # p값과 같은 기준(대조 있으면 순효과)
        if eff == 0:
            return "차이 불분명"
        better = eff * self.good > 0
        if self.p is not None and self.p < 0.05:
            return "개선(유의)" if better else "악화(유의)"
        if self.p is not None and self.p < 0.2:
            return "개선 경향" if better else "악화 경향"
        return "차이 불분명"


@dataclass
class TrialResult:
    trial: Trial
    status: str = ""
    before: tuple[date, date] | None = None
    after: tuple[date, date] | None = None
    groups: list[str] = field(default_factory=list)
    herd: int | None = None
    beh: list[MetricRow] = field(default_factory=list)
    alerts: list[MetricRow] = field(default_factory=list)
    daily_rum: list[tuple[str, float | None]] = field(default_factory=list)   # 그래프용 (날짜, 반추)
    summary: list[str] = field(default_factory=list)


def _group_daily(conn, farm_id: str, d1: date, d2: date, group_filter: list[str], lact_kw: list[str],
                 min_n: int = 3) -> tuple[dict[date, dict[str, float]], list[str]]:
    """대상 그룹(두수 가중) 일별 지표."""
    groups = conn.execute("SELECT group_id, name, n_animals FROM animal_groups WHERE farm_id=?", (farm_id,)).fetchall()
    if group_filter:
        sel = [g for g in groups if any(k in (g["name"] or "") for k in group_filter)]
    else:
        sel = [g for g in groups if any(k in (g["name"] or "").lower() for k in lact_kw)] or list(groups)
    sel = [g for g in sel if (g["n_animals"] or 0) >= min_n]
    if not sel:
        return {}, []
    w = {g["group_id"]: g["n_animals"] or 1 for g in sel}
    rows = conn.execute(
        f"""SELECT group_id, date, metric, current FROM group_behaviour
            WHERE farm_id=? AND date BETWEEN ? AND ? AND group_id IN ({','.join('?' * len(w))})""",
        (farm_id, str(d1), str(d2), *w)).fetchall()
    per: dict[tuple[str, str], dict[str, float]] = {}
    for r in rows:
        if r["current"] is not None:
            per.setdefault((r["group_id"], r["date"]), {})[r["metric"]] = r["current"]
    daily: dict[date, dict[str, list]] = {}
    for (gid, ds), m in per.items():
        tot = sum(m.get(k, 0) for k in ("moving", "standing", "lying"))
        if tot < 1380 or "rum_standing" not in m or "rum_lying" not in m:
            continue   # 불완전한 날 제외
        rum = m["rum_standing"] + m["rum_lying"]
        vals = {"rum": rum, "lying": m.get("lying", 0) / 60, "share": m["rum_lying"] / rum * 100 if rum else None,
                "sync": m["lying_sync"] * 100 if m.get("lying_sync") is not None else None,
                "standing": m.get("standing", 0) / 60, "moving": m.get("moving", 0) / 60}
        acc = daily.setdefault(datetime.strptime(ds, "%Y-%m-%d").date(), {})
        for k, v in vals.items():
            if v is not None:
                acc.setdefault(k, []).append((v, w[gid]))
    out = {d: {k: sum(v * ww for v, ww in xs) / sum(ww for _, ww in xs) for k, xs in m.items()} for d, m in daily.items()}
    return out, [g["name"] for g in sel]


def _alert_counts(conn, farm_id: str, d1: date, d2: date, health: set[str]) -> dict[str, int]:
    """기간 내 (개체, 날짜) 중복 제외 알림 두수-일."""
    rows = conn.execute(
        "SELECT animal_id, alert_type, substr(occurred_at,1,10) d FROM alerts WHERE farm_id=? AND occurred_at >= ? AND occurred_at < ?",
        (farm_id, f"{d1} 00:00:00", f"{d2 + timedelta(days=1)} 00:00:00")).fetchall()
    out = {}
    for key, _ in ALERT_METRICS:
        types = health if key == "_HEALTH" else {key}
        out[key] = len({(r["animal_id"], r["d"]) for r in rows if r["alert_type"] in types})
    return out


def compute_trial(conn: sqlite3.Connection, t: Trial, as_of: datetime, mapper: AlertTypeMapper,
                  rules: dict[str, Any]) -> TrialResult:
    res = TrialResult(t)
    if t.error:
        res.status = "입력 오류: " + t.error
        return res
    last_full = as_of.date() - timedelta(days=1)
    b1, b2 = t.start - timedelta(days=t.days), t.start - timedelta(days=1)
    a1 = t.start + timedelta(days=t.adapt)
    a2 = min(a1 + timedelta(days=t.days - 1), last_full)
    res.before, res.after = (b1, b2), (a1, a2)
    if a1 > last_full or (a2 - a1).days + 1 < 7:
        have = max(0, (last_full - a1).days + 1)
        res.status = f"진행중 — 투입 후 데이터 {have}/{t.days}일 (7일 이상 쌓이면 계산, 적응기간 {t.adapt}일 제외)"
        return res
    res.status = "완료" if (a2 - a1).days + 1 >= t.days else f"진행중 — 투입 후 {(a2 - a1).days + 1}/{t.days}일 반영"
    lact = [k.lower() for k in rules.get("behaviour", {}).get("lactating_keywords", ["착유"])]
    health = set(mapper.codes("health")) - (set() if rules.get("features", {}).get("ph", True) else {"PH_LOW"})

    # 이 목장 행동
    daily, res.groups = _group_daily(conn, t.farm_id, b1, a2, t.groups, lact)
    res.daily_rum = [(f"{d:%m/%d}", daily.get(d, {}).get("rum")) for d in
                     (b1 + timedelta(days=i) for i in range((a2 - b1).days + 1))]

    # 대조: 같은 날짜의 다른 목장(착유 그룹) 값 → 일별 중앙값 시계열 (차이의 차이 = DiD)
    others = [r["farm_id"] for r in conn.execute("SELECT farm_id FROM farms WHERE farm_id != ?", (t.farm_id,))]
    herds = {r["farm_id"]: r["herd_size"] for r in conn.execute("SELECT farm_id, herd_size FROM farms")}
    nb, na = (b2 - b1).days + 1, (a2 - a1).days + 1
    pool: dict[date, dict[str, list[float]]] = {}
    ctrl_farms = 0
    cb_sum = {k: 0 for k, _ in ALERT_METRICS}
    ca_sum = {k: 0 for k, _ in ALERT_METRICS}
    ctrl_herd = 0
    for fid in others:
        od, _ = _group_daily(conn, fid, b1, a2, [], lact)
        if od:
            ctrl_farms += 1
        for d, v in od.items():
            for k, x in v.items():
                pool.setdefault(d, {}).setdefault(k, []).append(x)
        h = herds.get(fid)
        if h:
            cb, ca = _alert_counts(conn, fid, b1, b2, health), _alert_counts(conn, fid, a1, a2, health)
            for k, _ in ALERT_METRICS:
                cb_sum[k] += cb[k]
                ca_sum[k] += ca[k]
            ctrl_herd += h
    ctrl = {d: {k: statistics.median(xs) for k, xs in m.items() if len(xs) >= 3} for d, m in pool.items()}

    for k, name, unit, good in BEH_METRICS:
        bd = [d for d in daily if b1 <= d <= b2 and k in daily[d]]
        ad = [d for d in daily if a1 <= d <= a2 and k in daily[d]]
        m = MetricRow(k, name, unit, good, statistics.mean(daily[d][k] for d in bd) if bd else None,
                      statistics.mean(daily[d][k] for d in ad) if ad else None, len(bd), len(ad))
        cbd = [d for d in bd if k in ctrl.get(d, {})]
        cad = [d for d in ad if k in ctrl.get(d, {})]
        if len(cbd) >= 3 and len(cad) >= 3:
            m.ctrl = statistics.mean(ctrl[d][k] for d in cad) - statistics.mean(ctrl[d][k] for d in cbd)
            m.ctrl_n = ctrl_farms
            m.p = welch_p([daily[d][k] - ctrl[d][k] for d in cbd], [daily[d][k] - ctrl[d][k] for d in cad])
        else:   # 대조 없음 → 단순 전후 비교
            m.p = welch_p([daily[d][k] for d in bd], [daily[d][k] for d in ad])
        res.beh.append(m)

    res.herd = herds.get(t.farm_id)
    if res.herd:
        cb, ca = _alert_counts(conn, t.farm_id, b1, b2, health), _alert_counts(conn, t.farm_id, a1, a2, health)
        for k, name in ALERT_METRICS:
            m = MetricRow(k, name, "두/100두·일", -1, cb[k] / nb / res.herd * 100, ca[k] / na / res.herd * 100,
                          cb[k], ca[k])
            if ctrl_herd and cb_sum[k] > 0 and ca_sum[k] > 0:
                m.ctrl = (ca_sum[k] / na - cb_sum[k] / nb) / ctrl_herd * 100
                m.ctrl_n = ctrl_farms
                m.p = rate_p(cb[k], nb, ca[k], na, ratio0=(ca_sum[k] / na) / (cb_sum[k] / nb))
            else:
                m.p = rate_p(cb[k], nb, ca[k], na)
            res.alerts.append(m)

    # 요약 문장
    for m in res.beh + res.alerts:
        if m.verdict.startswith(("개선", "악화")) and m.delta is not None:
            d = f"{m.delta:+.1f}{'' if m.unit.startswith('두') else ' ' + m.unit.split('/')[0]}"
            net = f", 대조 보정 {m.net:+.1f}" if m.net is not None else ""
            pv = "" if m.p is None else (", p<0.001" if m.p < 0.001 else f", p={m.p:.3f}")
            res.summary.append(f"{m.name}: {m.before:.1f} → {m.after:.1f} ({d}{net}{pv}) — {m.verdict}")
    if not res.summary:
        res.summary.append("투입 전후로 뚜렷한 차이가 확인되지 않음(기간 연장 또는 대상 그룹 확인)")
    if not res.groups:
        res.summary.append("⚠ 대상 그룹 행동 데이터 없음 — 그룹 이름 확인(투입기록.xlsx '대상그룹')")
    return res


def compute_all(conn, as_of: datetime, mapper: AlertTypeMapper, rules: dict[str, Any],
                path: Path = TRIALS_FILE) -> list[TrialResult]:
    return [compute_trial(conn, t, as_of, mapper, rules) for t in load_trials(conn, path)]
