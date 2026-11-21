"""판정 기준 검증 (백테스트).

지난 기간의 매일 06:00 시점으로 '그날 아침 판정'(점수·등급·급증·집단신호)을 다시 계산하고,
그 뒤 N일 동안 실제로 일어난 일(농장이 smaXtec 에 입력한 진단 기록 등)과 맞춰 본다.

정답(결과) 자료 — 모두 smaXtec 이벤트(농장·수의사가 입력한 것):
  diagnosis       진단 기록 (유방염·케토시스·자궁염·파행 …)  ← 주 정답
  false_positive  알림을 '오경보'로 표시한 기록
  delete          개체 삭제(도태·판매·폐사 추정 — 사유는 확인 필요)
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
}
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
                                        s.health_d0, s.herd_size))
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
                elif raw == "false_positive":
                    res.fp.append((fid, t))
                else:
                    res.deleted.append((fid, t))
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
            "deleted": sum(1 for f, _ in res.deleted if f == fid)})
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
    return v


# ───────────────────────── 출력 ─────────────────────────
def text_report(o: dict[str, Any], res: Result) -> str:
    """콘솔·공유용 요약 (목장 이름 없음)."""
    L = [f"판정 기준 검증 — {res.start:%Y-%m-%d} ~ {res.end:%Y-%m-%d} · 목장 {o['n_farms']}곳 · 목장-일 {o['n_farmdays']}개",
         f"정답 자료: 진단 {o['n_dx']}건({o['n_dx_farms']}개 목장) · 오경보 표시 {o['n_fp']}건 · 개체 삭제 {o['n_deleted']}건", ""]
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
    names = {f["farm_id"]: (f"목장{i:02d}" if anon else f["name"]) for i, f in enumerate(o["farms"], 1)}
    table("목장별", ["목장", "센서 두수", "계산 일수", "긴급 (월 일수)", "긴급·우선 (월 일수)", "진단 기록", "오경보 표시", "개체 삭제"],
          [[names[f["farm_id"]], f["herd"], f["days"], f["urgent_per30"], f["flag_per30"], f["dx"], f["fp"], f["deleted"]] for f in o["farms"]],
          [20, 10, 10, 12, 14, 10, 10, 10])
    table("자료 점검", ["이벤트", "기록에 있는 항목(키)"], [[k, ", ".join(sorted(v))] for k, v in res.detail_keys.items()] or [["진단·오경보·삭제 기록 없음", ""]], [18, 100])
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out


def run(sources: list[tuple[str, sqlite3.Connection]], out: Path, *, days: int = 365, anon: bool = False,
        now: datetime | None = None, progress=None) -> tuple[Path, str]:
    from .config import load_farm_overrides, load_toml
    rules, mapper = load_toml("rules.toml"), AlertTypeMapper(load_toml("alert_types.toml"))
    res = backtest(sources, mapper, rules, load_farm_overrides(), days=days, now=now, progress=progress)
    o = summarize(res)
    return build_workbook(o, res, out, anon=anon), text_report(o, res)
