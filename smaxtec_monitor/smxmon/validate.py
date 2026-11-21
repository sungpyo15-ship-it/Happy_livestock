"""판정 기준 검증 (백테스트).

지난 기간의 매일 06:00 시점으로 '그날 아침 판정'(점수·등급·급증·집단신호)을 다시 계산하고,
그 뒤 N일 동안 실제로 일어난 일(농장이 smaXtec 에 입력한 진단 기록 등)과 맞춰 본다.

정답(결과) 자료 — 농장·수의사가 smaXtec 에 남긴 것:
  질병 메모(fb_notes)     알림 확인 때 남긴 메모의 질병(유방염·케토시스 …)  ← 국내 농장 주 정답 (feedback.py 로 수집)
  알림 확인 처리(fb_todos) 확인함(user-ack) / 해당 없음(user-nack = 농장 판단 오경보)
  diagnosis·false_positive·delete 이벤트(있으면)
보조 지표(정답 아님): 판정 후 7일간 건강알림 두수가 그 목장 평소보다 높게 유지됐는지(일시적 잡음 vs 지속 문제)

⚠ 진단 기록은 농장마다 입력 습관이 달라 '기록 없음 ≠ 질병 없음'. 기록이 적으면 결론을 내리지 않음.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .analysis import analyze
from .normalize import AlertTypeMapper

log = logging.getLogger(__name__)
H24 = timedelta(hours=24)
GRADES = ["긴급", "우선", "관찰", "정상"]
DX_KO = {
    "MASTITIS": "유방염", "MASTITIS_CLINICAL": "유방염", "MASTITIS_SUBCLINICAL": "유방염(준임상)",
    "KETOSIS": "케토시스", "KETOSIS_SUBCLINICAL": "케토시스(준임상)", "MILK_FEVER": "유열", "HYPOCALCEMIA": "유열",
    "DISPLACED_ABOMASUM": "제4위 전위", "LDA": "제4위 전위", "RDA": "제4위 전위", "ACIDOSIS": "산독증", "SARA": "산독증",
    "BLOAT": "고창증", "INDIGESTION": "소화불량", "METRITIS": "자궁염", "ENDOMETRITIS": "자궁염",
    "RETAINED_PLACENTA": "후산정체", "LAMENESS": "파행", "DIGITAL_DERMATITIS": "파행", "SOLE_ULCER": "파행",
    "PNEUMONIA": "폐렴·호흡기", "RESPIRATORY_DISEASE": "폐렴·호흡기", "DIARRHEA": "설사", "FEVER": "발열",
    "MILK_FEVER_ACUTE": "유열", "WHITE_LINE_DISEASE": "파행", "INJURY": "외상",
}
CAT_KO = {"UDDER": "유방", "METABOLISM": "대사", "CLAW": "발굽", "LIMBS": "다리·발굽", "LOCOMOTION": "보행",
          "DIGESTION": "소화기", "DIGESTIVE": "소화기", "RESPIRATORY": "호흡기", "INFECTIOUS": "전염성",
          "GENERAL": "일반", "CALF": "송아지", "SKIN": "피부", "PARASITES": "기생충", "INJURY": "외상"}
CLOSING_KO = {"user-ack": "확인함", "user-nack": "해당 없음", "auto-ack": "자동 확인", "auto-nack": "자동 종료",
              "archived": "보관"}
TODO_KO = {"401_mastitis": "유방염 의심", "402_milk_fever": "유열 위험", "403_ketosis": "케토시스 의심"}
# 개선안(검증 비교용) — 실제 판정에는 아직 반영 안 함
ALT_SURGE_HERD_PCT = 4.0     # 급증 최소 두수 = max(3, 센서두수 × 4%)
ALT_SURGE_MIN_INCREASE = 3   # 증가폭 3두 이상
VARIANTS = [("현재", "cur"), ("A 급증 강화", "A"), ("B 급증 강화 + 100두당 혼합", "B")]
REPRO_DX = {"CYSTIC_OVARY", "ANOESTRUS", "ABORTION", "DYSTOCIA"}   # 번식 진단은 건강 판정 검증에서 제외


@dataclass
class FarmDay:
    farm_id: str
    day: datetime
    grade: str
    score: float
    surge: bool
    cluster: bool
    health_d0: int
    herd: int | None
    d1: int = 0
    avg7: float | None = None
    vgrade: dict[str, str] = field(default_factory=dict)
    vsurge: dict[str, bool] = field(default_factory=dict)
    dx7: int = 0            # 이후 7일 진단 두수(건강 진단)
    fwd_ratio: float | None = None   # 이후 7일 건강알림 두수 평균 / 목장 평소


@dataclass
class Result:
    start: datetime
    end: datetime
    horizon: int
    rules: dict[str, Any]
    days: list[FarmDay] = field(default_factory=list)
    names: dict[str, str] = field(default_factory=dict)
    dx: list[tuple[str, str, datetime, str]] = field(default_factory=list)      # farm, animal, t, 진단명
    fp: list[tuple[str, datetime]] = field(default_factory=list)
    deleted: list[tuple[str, datetime]] = field(default_factory=list)
    detail_keys: dict[str, set] = field(default_factory=dict)
    animal_alert_before_dx: list[bool] = field(default_factory=list)
    dx_by_name: dict[str, list[bool]] = field(default_factory=dict)
    dx_source: dict[str, int] = field(default_factory=dict)
    todos: list[tuple[str, str, str]] = field(default_factory=list)   # farm, 알림종류, 처리
    todo_keys: set = field(default_factory=set)


def _t(s: str) -> datetime:
    return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")


def backtest(sources: list[tuple[str, sqlite3.Connection]], mapper: AlertTypeMapper, rules: dict[str, Any],
             overrides: dict | None = None, days: int = 365, horizon: int = 7, now: datetime | None = None,
             progress=None) -> Result:
    """sources: [(이름, DB 연결)] — 여러 사용자 DB(목장이 겹치면 먼저 나온 것만)."""
    now = now or datetime.now()
    health = set(mapper.codes("health"))
    end = datetime.combine((now - timedelta(days=horizon)).date(), datetime.min.time()) + timedelta(hours=6)
    res = Result(end, end, horizon, rules)
    taken: set[str] = set()
    for label, conn in sources:
        conn.row_factory = sqlite3.Row
        first = conn.execute("SELECT MIN(occurred_at) FROM alerts").fetchone()[0]
        if not first:
            continue
        start = max(_t(first) + timedelta(days=9), end - timedelta(days=days))
        start = datetime.combine(start.date(), datetime.min.time()) + timedelta(hours=6)
        farms = [r[0] for r in conn.execute("SELECT farm_id FROM farms")]
        mine = [f for f in farms if f not in taken]
        if not mine:
            continue
        taken.update(mine)
        res.start = min(res.start, start)
        res.names.update({r[0]: r[1] for r in conn.execute("SELECT farm_id, name FROM farms")})
        ov = dict(overrides or {})
        for f in farms:                      # 다른 사용자 DB 에서 이미 계산한 목장은 제외
            if f not in mine:
                ov[f] = {**ov.get(f, {}), "exclude": True}
        # 1) 매일 06:00 판정 다시 계산
        d, n_days, k = start, (end - start).days + 1, 0
        while d <= end:
            an = analyze(conn, d, mapper, rules, ov, light=True)
            for s in an.farms:
                res.days.append(FarmDay(s.farm_id, d, s.grade, s.score, s.surge, s.cluster is not None,
                                        s.health_d0, s.herd_size, s.health_d1, s.health_7d_avg))
            d += H24
            k += 1
            if progress and k % 30 == 0:
                progress(f"  {label}: {k}/{n_days}일 계산")
        # 2) 결과 자료
        q = f"SELECT farm_id, animal_id, alert_type, lower(alert_type_raw), occurred_at, detail FROM alerts WHERE farm_id IN ({','.join('?' * len(mine))})"
        health_by_day: dict[tuple[str, str], set] = defaultdict(set)
        health_by_animal: dict[tuple[str, str], list[datetime]] = defaultdict(list)
        for fid, aid, typ, raw, ts, det in conn.execute(q, mine):
            t = _t(ts)
            if typ in health:
                health_by_day[(fid, (t - timedelta(hours=6)).date().isoformat())].add(aid)
                health_by_animal[(fid, aid)].append(t)
            if raw in ("diagnosis", "false_positive", "delete"):
                try:
                    dd = json.loads(det) if det else {}
                except ValueError:
                    dd = {}
                res.detail_keys.setdefault(raw, set()).update(dd.keys() if isinstance(dd, dict) else [])
                if raw == "diagnosis":
                    key = str(dd.get("diagnosis_key") or dd.get("key") or "OTHER").upper()
                    if key in REPRO_DX:
                        continue
                    res.dx.append((fid, aid, t, DX_KO.get(key, "기타")))
                    res.dx_source["진단 이벤트"] = res.dx_source.get("진단 이벤트", 0) + 1
                elif raw == "false_positive":
                    res.fp.append((fid, t))
                else:
                    res.deleted.append((fid, t))
        _load_feedback(conn, mine, res)
        # 3) 판정별 이후 결과 붙이기
        dx_by_farm: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
        for fid, aid, t, _ in res.dx:
            if fid in mine:
                dx_by_farm[fid].append((t, aid))
        base: dict[str, float] = {}
        for fid in mine:
            vals = [len(health_by_day.get((fid, (start + timedelta(days=i)).date().isoformat()), ()))
                    for i in range((end - start).days + horizon)]
            base[fid] = statistics.mean(vals) if vals else 0
        for fd in res.days:
            if fd.farm_id not in mine:
                continue
            fd.dx7 = len({a for t, a in dx_by_farm[fd.farm_id] if fd.day < t <= fd.day + timedelta(days=horizon)})
            nxt = [len(health_by_day.get((fd.farm_id, (fd.day + timedelta(days=i)).date().isoformat()), ()))
                   for i in range(1, horizon + 1)]
            fd.fwd_ratio = round(statistics.mean(nxt) / base[fd.farm_id], 2) if base[fd.farm_id] else None
        # 4) 개체 수준: 진단 전 7일 안에 그 개체에 건강알림이 있었나 (센서 민감도)
        for fid, aid, t, name in res.dx:
            if fid not in mine:
                continue
            hit = any(t - timedelta(days=7) <= x <= t for x in health_by_animal.get((fid, aid), []))
            res.animal_alert_before_dx.append(hit)
            res.dx_by_name.setdefault(name, []).append(hit)
    return res


def _load_feedback(conn: sqlite3.Connection, mine: list[str], res: Result) -> None:
    """feedback.py 가 받아 둔 질병 메모·알림 확인 처리."""
    from .feedback import NON_DISEASE_CATEGORY
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    ph = ",".join("?" * len(mine))
    if "fb_notes" in have:
        for fid, aid, ts, cat, ev in conn.execute(
                f"SELECT farm_id, animal_id, ts, category, event FROM fb_notes WHERE farm_id IN ({ph})", mine):
            cat, ev = (cat or "").upper(), (ev or "").upper()
            if not ts or not aid or (not cat and not ev) or cat in NON_DISEASE_CATEGORY or ev in REPRO_DX:
                continue
            name = DX_KO.get(ev) or (CAT_KO.get(cat, "기타") + "(분류)")
            res.dx.append((fid, aid, _t(ts), name))
            res.dx_source["질병 메모"] = res.dx_source.get("질병 메모", 0) + 1
    if "fb_todos" in have:
        for fid, sub, dt, closing, keys in conn.execute(
                f"SELECT farm_id, sub_type, data_type, closing, keys FROM fb_todos WHERE farm_id IN ({ph})", mine):
            res.todos.append((fid, TODO_KO.get(sub or "", sub or dt or "기타"), CLOSING_KO.get(closing or "", closing or "-")))
            if keys:
                try:
                    res.todo_keys.update(json.loads(keys))
                except ValueError:
                    pass


def apply_variants(res: Result) -> None:
    """개선안 A·B 로 등급 다시 매기기 (저장된 점수에서 급증·건강알림 항목만 바꿔 계산)."""
    import math
    pr, sg = res.rules["priority"], res.rules["surge"]
    herds = [d.herd for d in res.days if d.herd]
    hmed = statistics.median(herds) if herds else 100

    def grade(sc: float) -> str:
        return "긴급" if sc >= pr["urgent"] else "우선" if sc >= pr["high"] else "관찰" if sc >= pr["watch"] else "정상"
    for d in res.days:
        mn = max(int(sg["min_count"]), math.ceil((d.herd or 0) * ALT_SURGE_HERD_PCT / 100))
        inc = ALT_SURGE_MIN_INCREASE
        alt = d.health_d0 >= mn and (
            (d.health_d0 >= d.d1 * sg["ratio_vs_prev_day"] and d.health_d0 - d.d1 >= inc)
            or (d.avg7 is not None and d.health_d0 >= d.avg7 * sg["ratio_vs_7d_avg"] and d.health_d0 - d.avg7 >= inc))
        sa = d.score - pr["w_surge"] * d.surge + pr["w_surge"] * alt
        sb = sa
        if d.herd:
            sb = sa - pr["w_health_alert"] * d.health_d0 + pr["w_health_alert"] * (0.5 * d.health_d0 + 0.5 * d.health_d0 / d.herd * hmed)
        d.vgrade = {"cur": d.grade, "A": grade(sa), "B": grade(sb)}
        d.vsurge = {"cur": d.surge, "A": alt, "B": alt}


# ───────────────────────── 집계 ─────────────────────────
def _stats(rows: list[FarmDay]) -> dict[str, Any]:
    n = len(rows)
    if not n:
        return {"n": 0}
    per100 = [r.dx7 / r.herd * 100 for r in rows if r.herd]
    fw = [r.fwd_ratio for r in rows if r.fwd_ratio is not None]
    return {"n": n, "any_dx": sum(1 for r in rows if r.dx7) / n * 100,
            "dx100": statistics.mean(per100) if per100 else None,
            "fwd": statistics.median(fw) if fw else None}


def summarize(res: Result) -> dict[str, Any]:
    out: dict[str, Any] = {"n_farmdays": len(res.days), "n_farms": len({d.farm_id for d in res.days}),
                           "n_dx": len(res.dx), "n_dx_farms": len({f for f, *_ in res.dx}),
                           "n_fp": len(res.fp), "n_deleted": len(res.deleted)}
    out["by_grade"] = {g: _stats([d for d in res.days if d.grade == g]) for g in GRADES}
    out["surge"] = {k: _stats([d for d in res.days if d.surge == v]) for k, v in (("급증 O", True), ("급증 X", False))}
    out["cluster"] = {k: _stats([d for d in res.days if d.cluster == v]) for k, v in (("집단신호 O", True), ("집단신호 X", False))}
    # 등급 비율·목장별 긴급 빈도
    n = len(res.days) or 1
    out["grade_share"] = {g: sum(1 for d in res.days if d.grade == g) / n * 100 for g in GRADES}
    per_farm: dict[str, list[FarmDay]] = defaultdict(list)
    for d in res.days:
        per_farm[d.farm_id].append(d)
    out["farms"] = []
    for fid, ds in per_farm.items():
        out["farms"].append({
            "farm_id": fid, "name": res.names.get(fid, fid), "days": len(ds), "herd": ds[-1].herd,
            "urgent_per30": sum(1 for d in ds if d.grade == "긴급") / len(ds) * 30,
            "flag_per30": sum(1 for d in ds if d.grade in ("긴급", "우선")) / len(ds) * 30,
            "dx": sum(1 for f, *_ in res.dx if f == fid), "fp": sum(1 for f, _ in res.fp if f == fid),
            "deleted": sum(1 for f, _ in res.deleted if f == fid),
            "todos": sum(1 for f, *_ in res.todos if f == fid),
            "nack": sum(1 for f, _, c in res.todos if f == fid and c == "해당 없음")})
    out["farms"].sort(key=lambda x: -x["urgent_per30"])
    # 재현율: 진단 전 7일 안에 그 목장이 긴급·우선이었나
    idx = {(d.farm_id, d.day.date()): d for d in res.days}

    def caught(fid: str, t: datetime, pred) -> bool | None:
        ds = [idx.get((fid, (t - timedelta(days=k)).date())) for k in range(0, res.horizon + 1)]
        ds = [d for d in ds if d is not None and d.day < t]
        return any(pred(d) for d in ds) if ds else None

    def recall(pred) -> float | None:
        hits = [caught(f, t, pred) for f, _, t, _ in res.dx]
        hits = [h for h in hits if h is not None]
        return sum(hits) / len(hits) * 100 if hits else None

    out["recall"] = {"긴급": recall(lambda d: d.grade == "긴급"), "긴급·우선": recall(lambda d: d.grade in ("긴급", "우선")),
                     "급증": recall(lambda d: d.surge), "집단신호": recall(lambda d: d.cluster)}
    a = res.animal_alert_before_dx
    out["animal_recall"] = sum(a) / len(a) * 100 if a else None
    out["animal_recall_by_dx"] = {k: (len(v), sum(v) / len(v) * 100) for k, v in sorted(res.dx_by_name.items(), key=lambda x: -len(x[1]))}
    # 기준값 시험: 점수 ≥ c 를 '경보'로 볼 때
    pr = res.rules["priority"]
    cuts = sorted({round(c, 1) for c in (pr["watch"], pr["high"], pr["urgent"], pr["high"] * 0.75, pr["urgent"] * 0.75,
                                          pr["urgent"] * 1.25, pr["urgent"] * 1.5, pr["urgent"] * 2)})
    base = _stats([d for d in res.days if d.score < pr["watch"]])
    grid = []
    for c in cuts:
        flag = [d for d in res.days if d.score >= c]
        st = _stats(flag)
        rc = recall(lambda d, c=c: d.score >= c)
        grid.append({"cut": c, "share": len(flag) / n * 100,
                     "any_dx": st.get("any_dx"), "fwd": st.get("fwd"), "recall": rc,
                     "lift": (st["dx100"] / base["dx100"]) if st.get("n") and st.get("dx100") is not None and base.get("dx100") else None,
                     "current": c in (pr["urgent"], pr["high"])})
    out["grid"], out["base"] = grid, base
    # 개선안 비교
    if res.days and not res.days[0].vgrade:
        apply_variants(res)
    bands = [(0, 50), (50, 80), (80, 120), (120, 10 ** 6)]
    out["variants"] = []
    for label, key in VARIANTS:
        vs = {g: _stats([d for d in res.days if d.vgrade.get(key) == g]) for g in ("긴급", "정상")}
        pf = defaultdict(list)
        for d in res.days:
            pf[d.farm_id].append(d)
        u30 = {fid: sum(1 for d in ds if d.vgrade.get(key) == "긴급") / len(ds) * 30 for fid, ds in pf.items()}
        herd = {fid: ds[-1].herd for fid, ds in pf.items()}
        band = {f"{lo}~{hi if hi < 10 ** 6 else ''}두": (statistics.median(x) if (x := [u30[f] for f in u30 if herd[f] and lo <= herd[f] < hi]) else None)
                for lo, hi in bands}
        sv = [d for d in res.days if d.vsurge.get(key)]
        lift = (vs["긴급"]["dx100"] / vs["정상"]["dx100"]) if vs["긴급"].get("dx100") is not None and vs["정상"].get("dx100") else None
        out["variants"].append({
            "label": label, "urgent_share": sum(1 for d in res.days if d.vgrade.get(key) == "긴급") / n * 100,
            "flag_share": sum(1 for d in res.days if d.vgrade.get(key) in ("긴급", "우선")) / n * 100,
            "urgent_med": statistics.median(u30.values()) if u30 else None, "bands": band,
            "surge_share": len(sv) / n * 100, "surge_fwd": _stats(sv).get("fwd"),
            "fwd_urgent": vs["긴급"].get("fwd"), "fwd_normal": vs["정상"].get("fwd"), "lift": lift,
            "recall": recall(lambda d, k=key: d.vgrade.get(k) in ("긴급", "우선"))})
    # 농장 알림 확인 처리 (알림 종류별)
    tt: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for _, typ, cl in res.todos:
        tt[typ][cl] += 1
    out["todos"] = {k: dict(v) for k, v in sorted(tt.items(), key=lambda x: -sum(x[1].values()))}
    out["n_todos"] = len(res.todos)
    out["todo_farms"] = len({f for f, *_ in res.todos})
    out["dx_source"] = dict(res.dx_source)
    out["verdict"] = verdict(out, res)
    return out


def _f(v, k=1, suf=""):
    return "-" if v is None else f"{v:.{k}f}{suf}"


def verdict(o: dict[str, Any], res: Result) -> list[str]:
    """자동 해석 (가설 수준 — 숫자를 보고 사람이 최종 판단)."""
    v = []
    gs = o["grade_share"]
    urgent_freq = statistics.median([f["urgent_per30"] for f in o["farms"]]) if o["farms"] else 0
    v.append(f"[경보 빈도] 목장 하루 기준 긴급 {_f(gs['긴급'])}% · 우선 {_f(gs['우선'])}% · 목장당 긴급 월 {_f(urgent_freq)}일(중앙값).")
    if urgent_freq > 8:
        v.append("  → 긴급이 너무 자주 뜸(월 8일 초과). 팀원이 경보에 무뎌질 위험 → 긴급 기준 상향 검토.")
    elif urgent_freq < 0.5:
        v.append("  → 긴급이 거의 안 뜸(월 0.5일 미만). 놓치는 문제가 없는지 재현율 확인.")
    else:
        v.append("  → 빈도는 운영 가능한 범위(경험적 기준 월 0.5~8일, 가설).")
    fw = {g: o["by_grade"][g].get("fwd") for g in GRADES}
    if fw["긴급"] is not None and fw["정상"] is not None:
        v.append(f"[지속성] 판정 뒤 7일 건강알림 두수(평소=1.0): 긴급 {_f(fw['긴급'], 2)} · 우선 {_f(fw['우선'], 2)} · 정상 {_f(fw['정상'], 2)}.")
        if fw["긴급"] < 1.15:
            v.append("  → 긴급 뒤에도 평소 수준으로 바로 돌아감 = 하루짜리 잡음을 많이 잡는 중일 수 있음.")
        else:
            v.append("  → 긴급 판정 뒤 문제가 며칠 이어짐 = 일시적 잡음보다는 지속 문제를 잡고 있음.")
    if o["n_dx"] < 30 or o["n_dx_farms"] < 3:
        v.append(f"[진단 기록] {o['n_dx']}건·{o['n_dx_farms']}개 목장 — 정답 자료가 부족해 '맞혔는지'는 결론 내리지 않음. "
                 "농장이 smaXtec 에 진단을 입력하도록 하면 다음 검증부터 판단 가능.")
    else:
        bg = o["by_grade"]
        lift = (bg["긴급"]["dx100"] / bg["정상"]["dx100"]) if bg["긴급"].get("n") and bg["정상"].get("dx100") else None
        v.append(f"[진단 기록 {o['n_dx']}건] 판정 뒤 7일 진단 두수(100두당): 긴급 {_f(bg['긴급'].get('dx100'), 2)} · "
                 f"우선 {_f(bg['우선'].get('dx100'), 2)} · 정상 {_f(bg['정상'].get('dx100'), 2)} → 긴급은 정상의 {_f(lift, 1, '배')}.")
        if lift is not None:
            v.append("  → 긴급 판정이 실제 진단과 뚜렷이 연결됨(2배 이상)." if lift >= 2 else
                     "  → 연결은 있으나 보통(1.3~2배) → 가중치 조정으로 개선 여지." if lift >= 1.3 else
                     "  → 긴급과 진단의 연결이 약함(1.3배 미만) → 점수 구성 재검토 필요.")
        r = o["recall"]
        v.append(f"[놓침] 진단 전 7일 안에 그 목장이 긴급 {_f(r['긴급'])}% · 긴급·우선 {_f(r['긴급·우선'])}% 로 떠 있었음.")
        if o["animal_recall"] is not None:
            v.append(f"[개체] 진단받은 소의 {_f(o['animal_recall'])}% 는 진단 전 7일 안에 센서 건강알림이 있었음(센서 민감도 참고치).")
    if o["n_fp"]:
        v.append(f"[오경보 표시] 농장이 '오경보'로 표시한 기록 {o['n_fp']}건 — 목장별 표 참고.")
    if o.get("n_todos"):
        tot = defaultdict(int)
        for d in o["todos"].values():
            for k, c in d.items():
                tot[k] += c
        n = sum(tot.values())
        v.append(f"[농장 알림 확인] {o['todo_farms']}개 목장 {n}건 — 확인함 {tot['확인함'] / n * 100:.0f}% · "
                 f"해당 없음 {tot['해당 없음'] / n * 100:.0f}% · 자동 종료 {(tot['자동 종료'] + tot['자동 확인']) / n * 100:.0f}%"
                 " (해당 없음 = 농장이 본 오경보. 알림 종류별 표 참고)")
    va = {x["label"][0]: x for x in o.get("variants", [])}
    if "현" in va and "A" in va:
        c, a, b = va["현"], va["A"], va.get("B")
        v.append(f"[개선안 A 급증 강화] 급증 {_f(c['surge_share'])}% → {_f(a['surge_share'])}% 의 날, "
                 f"긴급 목장당 월 {_f(c['urgent_med'], 2)} → {_f(a['urgent_med'], 2)}일, 긴급 뒤 알림지속 {_f(c['fwd_urgent'], 2)} → {_f(a['fwd_urgent'], 2)}.")
        if b:
            cb, bb = c["bands"], b["bands"]
            v.append("[개선안 B 100두당 혼합] 긴급(월 일수, 두수 구간별) " +
                     " · ".join(f"{k}: {_f(cb[k], 2)}→{_f(bb[k], 2)}" for k in cb) +
                     f" · 긴급 뒤 알림지속 {_f(b['fwd_urgent'], 2)}.")
    return v


# ───────────────────────── 출력 ─────────────────────────
def text_report(o: dict[str, Any], res: Result) -> str:
    """콘솔·공유용 요약 (목장 이름 없음)."""
    L = [f"판정 기준 검증 — {res.start:%Y-%m-%d} ~ {res.end:%Y-%m-%d} · 목장 {o['n_farms']}곳 · 목장-일 {o['n_farmdays']}개",
         f"정답 자료: 진단·질병메모 {o['n_dx']}건({o['n_dx_farms']}개 목장) · 농장 알림 확인 {o.get('n_todos', 0)}건 · 오경보 표시 {o['n_fp']}건 · 개체 삭제 {o['n_deleted']}건", ""]
    L += o["verdict"] + ["", "등급별 (이후 7일)  | 목장-일 | 진단있음% | 진단두수/100두 | 알림지속(평소=1)"]
    for g in GRADES:
        s = o["by_grade"][g]
        L.append(f"  {g:4} | {s['n']:6} | {_f(s.get('any_dx')):>6} | {_f(s.get('dx100'), 2):>6} | {_f(s.get('fwd'), 2):>5}")
    for k in ("surge", "cluster"):
        for lbl, s in o[k].items():
            L.append(f"  {lbl:8} | {s['n']:6} | {_f(s.get('any_dx')):>6} | {_f(s.get('dx100'), 2):>6} | {_f(s.get('fwd'), 2):>5}")
    L += ["", "점수 기준값 시험 (점수 ≥ 기준을 경보로 볼 때) | 경보 비율% | 진단있음% | 진단두수 정상대비 | 놓치지 않은 비율% | 알림지속"]
    for gr in o["grid"]:
        L.append(f"  {gr['cut']:6}{' ←현재' if gr['current'] else '      '} | {_f(gr['share']):>5} | {_f(gr['any_dx']):>5} | "
                 f"{_f(gr['lift'], 1, '배'):>5} | {_f(gr['recall']):>5} | {_f(gr['fwd'], 2):>5}")
    L += ["", "개선안 비교 | 긴급 % | 긴급·우선 % | 목장당 긴급 월일수 | 급증 % | 급증 뒤 지속 | 긴급 뒤 지속 | 정상 뒤 지속 | 진단 정상대비 | 놓치지 않은 %"]
    for x in o.get("variants", []):
        L.append(f"  {x['label']} | {_f(x['urgent_share'])} | {_f(x['flag_share'])} | {_f(x['urgent_med'], 2)} | {_f(x['surge_share'])} | "
                 f"{_f(x['surge_fwd'], 2)} | {_f(x['fwd_urgent'], 2)} | {_f(x['fwd_normal'], 2)} | {_f(x['lift'], 1, '배')} | {_f(x['recall'])}")
        L.append("     두수 구간별 긴급 월일수: " + " · ".join(f"{k} {_f(v, 2)}" for k, v in x["bands"].items()))
    if o.get("todos"):
        L += ["", "농장 알림 확인 처리 (알림 종류별 건수)"]
        for typ, d in list(o["todos"].items())[:12]:
            L.append(f"  {typ}: " + ", ".join(f"{k} {c}" for k, c in sorted(d.items(), key=lambda x: -x[1])))
    if o.get("dx_source"):
        L.append("정답 자료 출처: " + ", ".join(f"{k} {c}건" for k, c in o["dx_source"].items()))
    if res.todo_keys:
        L.append("알림 확인 기록 항목: " + ", ".join(sorted(res.todo_keys)))
    L += ["", "자료 구조(키): " + "; ".join(f"{k}={sorted(v)}" for k, v in res.detail_keys.items()) if res.detail_keys else ""]
    return "\n".join(L)


def build_workbook(o: dict[str, Any], res: Result, out: Path, anon: bool = False) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = Workbook()
    B, H = Font(bold=True), PatternFill("solid", fgColor="DDEBF7")
    ws = wb.active
    ws.title = "결론"
    ws["A1"] = "판정 기준 검증 (백테스트)"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = (f"{res.start:%Y-%m-%d} ~ {res.end:%Y-%m-%d} 매일 06:00 판정을 다시 계산 → 이후 {res.horizon}일 결과와 비교 · "
                f"목장 {o['n_farms']}곳 · 진단 {o['n_dx']}건 · 오경보 표시 {o['n_fp']}건 · 개체 삭제 {o['n_deleted']}건")
    for i, line in enumerate(o["verdict"], 4):
        ws.cell(row=i, column=1, value=line).alignment = Alignment(wrap_text=True, vertical="top")
    r = 5 + len(o["verdict"])
    notes = ["읽는 법",
             "· 진단두수/100두 = 그 판정 뒤 7일 안에 진단 기록(농장 입력)이 생긴 개체 수 ÷ 센서 두수 × 100. '정상 대비 배율'이 클수록 판정이 실제 문제와 연결됨(주 지표).",
             "· 진단있음% = 그 판정 뒤 7일 안에 진단이 1두라도 있었던 날의 비율(진단 입력이 많은 목장은 100%에 가까워져 차이가 작아짐).",
             "· 알림지속 = 판정 뒤 7일간 하루 건강알림 두수 ÷ 그 목장 평소 값. 1.0 = 평소, 1.3 = 30% 높게 유지. 진단 기록이 없어도 볼 수 있는 보조 지표(정답 아님).",
             "· 놓치지 않은 비율 = 진단이 있었던 경우 중, 진단 전 7일 안에 그 목장이 경보(긴급 등)로 떠 있던 비율.",
             "· 한계: 진단 기록은 농장마다 입력 습관이 달라 '기록 없음 ≠ 질병 없음'. 전환기 가중치·그룹행동(과거 자료 없는 기간)은 계산에서 빠짐.",
             "· 결론 문구는 숫자 기준의 자동 해석(가설). 최종 판단은 표를 보고 사람이 함."]
    for i, t in enumerate(notes):
        c = ws.cell(row=r + i, column=1, value=t)
        c.font = B if i == 0 else Font(size=9, color="595959")
    ws.column_dimensions["A"].width = 150

    def table(name: str, headers: list[str], rows: list[list[Any]], widths: list[int] | None = None):
        w = wb.create_sheet(name)
        for j, h in enumerate(headers, 1):
            c = w.cell(row=1, column=j, value=h)
            c.font, c.fill = B, H
            c.alignment = Alignment(wrap_text=True, horizontal="center", vertical="center")
        w.row_dimensions[1].height = 32
        for i, row in enumerate(rows, 2):
            for j, v in enumerate(row, 1):
                w.cell(row=i, column=j, value=round(v, 2) if isinstance(v, float) else v)
        for j, wd in enumerate(widths or [14] * len(headers), 1):
            w.column_dimensions[chr(64 + j)].width = wd
        return w

    def srow(lbl, s):
        return [lbl, s["n"], s.get("any_dx"), s.get("dx100"), s.get("fwd")]
    hdr = ["구분", "목장-일 수", "이후 7일 진단있음 %", "이후 7일 진단두수 /100두", "알림지속 (평소=1)"]
    table("등급·신호별", hdr, [srow(g, o["by_grade"][g]) for g in GRADES] + [[]] +
          [srow(k, s) for k, s in o["surge"].items()] + [[]] + [srow(k, s) for k, s in o["cluster"].items()], [14, 12, 16, 18, 14])
    table("기준값 시험", ["점수 기준 (≥)", "현재 기준", "경보 비율 %", "진단있음 %", "진단두수 정상 대비 (배)", "놓치지 않은 비율 %", "알림지속"],
          [[g["cut"], "현재" if g["current"] else "", g["share"], g["any_dx"], g["lift"], g["recall"], g["fwd"]] for g in o["grid"]],
          [14, 10, 12, 12, 14, 18, 12])
    table("놓침(재현율)", ["진단 전 7일 안에 떠 있던 판정", "비율 %"],
          [[k, v] for k, v in o["recall"].items()] + [[], ["개체: 진단 전 7일 안에 센서 건강알림", o["animal_recall"]], [],
                                                     ["진단명", "건수", "센서 알림 있었던 %"]] +
          [[k, n, p] for k, (n, p) in o["animal_recall_by_dx"].items()], [34, 12, 18])
    table("개선안 비교", ["안", "긴급 %", "긴급·우선 %", "목장당 긴급 (월 일수)"] + list(o["variants"][0]["bands"]) +
          ["급증 %", "급증 뒤 알림지속", "긴급 뒤 알림지속", "정상 뒤 알림지속", "진단 정상 대비 (배)", "놓치지 않은 %"],
          [[x["label"], x["urgent_share"], x["flag_share"], x["urgent_med"], *x["bands"].values(), x["surge_share"],
            x["surge_fwd"], x["fwd_urgent"], x["fwd_normal"], x["lift"], x["recall"]] for x in o["variants"]] +
          [[], [f"A = 급증 최소 두수를 max(3, 센서두수×{ALT_SURGE_HERD_PCT:g}%)·증가폭 {ALT_SURGE_MIN_INCREASE}두 이상으로 강화"],
           ["B = A + 점수의 건강알림 항목을 '두수 절반 + 100두당 비율 절반'으로 (목장 크기 영향 완화)"],
           ["두수 구간 열 = 그 센서두수 구간 목장들의 긴급 월 일수 중앙값"], ["※ 비교용 시험 계산 — 실제 판정에는 아직 반영하지 않음"]],
          [26, 9, 11, 14, 11, 11, 11, 11, 9, 13, 13, 13, 14, 13]) if o.get("variants") else None
    if o.get("todos"):
        cls = ["확인함", "해당 없음", "자동 확인", "자동 종료", "보관"]
        rows = []
        for typ, d in o["todos"].items():
            n = sum(d.values())
            rows.append([typ, n] + [d.get(c, 0) / n * 100 for c in cls])
        table("농장 확인 처리", ["알림 종류", "건수"] + [f"{c} %" for c in cls], rows +
              [[], ["확인함 = 농장이 보고 처리 · 해당 없음 = 농장이 문제없다고 닫음(농장 판단 오경보) · 자동 = 농장이 처리하지 않아 자동 종료"]],
              [22, 10, 12, 12, 12, 12, 10])
    names = {f["farm_id"]: (f"목장{i:02d}" if anon else f["name"]) for i, f in enumerate(o["farms"], 1)}
    table("목장별", ["목장", "센서 두수", "계산 일수", "긴급 (월 일수)", "긴급·우선 (월 일수)", "진단·질병메모", "알림 확인 처리",
                    "해당 없음 %", "오경보 표시", "개체 삭제"],
          [[names[f["farm_id"]], f["herd"], f["days"], f["urgent_per30"], f["flag_per30"], f["dx"], f["todos"],
            (f["nack"] / f["todos"] * 100) if f["todos"] else None, f["fp"], f["deleted"]] for f in o["farms"]],
          [20, 10, 10, 12, 14, 12, 12, 11, 10, 10])
    table("자료 점검", ["이벤트", "기록에 있는 항목(키)"], [[k, ", ".join(sorted(v))] for k, v in res.detail_keys.items()] or [["진단·오경보·삭제 기록 없음", ""]], [18, 100])
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out


def run(sources: list[tuple[str, sqlite3.Connection]], out: Path, *, days: int = 365, anon: bool = False,
        now: datetime | None = None, progress=None, anon_copy: Path | None = None) -> tuple[Path, str]:
    from .config import load_farm_overrides, load_toml
    rules, mapper = load_toml("rules.toml"), AlertTypeMapper(load_toml("alert_types.toml"))
    res = backtest(sources, mapper, rules, load_farm_overrides(), days=days, now=now, progress=progress)
    o = summarize(res)
    if anon_copy:
        build_workbook(o, res, anon_copy, anon=True)
    return build_workbook(o, res, out, anon=anon), text_report(o, res)
