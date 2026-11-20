"""번식기록지: smaXtec 번식 이벤트(분만·발정·수정·임신감정·건유·유산)로 개체별 현재 산차 기록과 목장 번식 성적.

입력: smxmon DB(alerts·animals 테이블). 이벤트 원문 코드
  calving_confirmation(산차 number) / calving_detection, heat·health_701·health_705, insemination,
  pregnancy_result(pregnant, expected_calving_date), dry_off, abort
⚠ 수집 이력(약 410일) 이전에 분만한 개체는 분만일이 비어 '분만일 미확인'으로 표시
"""
from __future__ import annotations

import json
import sqlite3
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

HEAT_RAW = {"heat", "health_701", "health_705", "heat_dnb"}
STATUS = {"fresh": "신선(분만 21일 이내)", "open": "공태(수정 전)", "bred": "수정 후 감정 전", "pregnant": "임신",
          "dry": "건유", "heifer": "미경산·분만 미확인", "open_neg": "재수정 필요(감정 음성)"}


@dataclass
class Event:
    t: datetime
    kind: str          # calving / heat / ai / pd / dry / abort
    detail: dict = field(default_factory=dict)


@dataclass
class CowRecord:
    animal_id: str
    label: str
    official_id: str = ""
    group: str = ""
    lact_status: str = ""
    parity: int | None = None
    calving: date | None = None
    prev_calving: date | None = None
    dim: int | None = None
    heats: list[date] = field(default_factory=list)          # 이번 산차 발정
    ais: list[date] = field(default_factory=list)            # 이번 산차 수정
    pd_date: date | None = None
    pd_result: bool | None = None
    conception: date | None = None
    expected_calving: date | None = None
    expected_dry: date | None = None
    dry_off: date | None = None
    abort: date | None = None
    status: str = "open"
    days_open: int | None = None
    first_ai_dim: int | None = None
    first_heat_dim: int | None = None
    calving_interval: int | None = None
    flags: list[str] = field(default_factory=list)
    timeline: list[dict[str, Any]] = field(default_factory=list)  # 최근 이벤트 전체(최신순)

    @property
    def n_ai(self) -> int:
        return len(self.ais)

    @property
    def status_ko(self) -> str:
        return STATUS.get(self.status, self.status)


def _d(t: datetime | None) -> date | None:
    return t.date() if t else None


def _merge(ts: list[datetime], days: float) -> list[datetime]:
    out: list[datetime] = []
    for t in sorted(ts):
        if not out or (t - out[-1]).total_seconds() > days * 86400:
            out.append(t)
    return out


def load_events(conn: sqlite3.Connection, farm_id: str) -> dict[str, list[Event]]:
    rows = conn.execute("SELECT animal_id, alert_type, alert_type_raw, occurred_at, detail FROM alerts WHERE farm_id=?",
                        (farm_id,)).fetchall()
    by: dict[str, list[Event]] = {}
    for aid, typ, raw, ts, det in rows:
        raw = (raw or "").lower()
        try:
            detail = json.loads(det) if det else {}
        except ValueError:
            detail = {}
        t = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        kind = ("calving" if raw in ("calving_confirmation", "calving_detection") else "heat" if raw in HEAT_RAW
                else "ai" if raw == "insemination" else "pd" if raw == "pregnancy_result" else "dry" if raw == "dry_off"
                else "abort" if raw == "abort" else None)
        if kind:
            detail["_raw"] = raw
            by.setdefault(aid, []).append(Event(t, kind, detail))
    return by


def build_records(conn: sqlite3.Connection, farm_id: str, today: date, gestation: int = 280,
                  dry_before: int = 60) -> list[CowRecord]:
    animals = {r[0]: r for r in conn.execute(
        "SELECT animal_id, COALESCE(label, official_id, animal_id), official_id, group_name, lactation_status, has_sensor "
        "FROM animals WHERE farm_id=?", (farm_id,)).fetchall()}
    ev = load_events(conn, farm_id)
    out: list[CowRecord] = []
    for aid in set(animals) | set(ev):
        a = animals.get(aid)
        if a is not None and not a[5] and aid not in ev:
            continue   # 센서 없고 기록도 없는 개체 제외
        r = CowRecord(aid, a[1] if a else aid, (a[2] or "") if a else "", (a[3] or "") if a else "", (a[4] or "") if a else "")
        es = sorted(ev.get(aid, []), key=lambda e: e.t)
        # 분만: 확인·감지가 14일 이내면 1회
        calv_ts = _merge([e.t for e in es if e.kind == "calving"], 14)
        parity_vals = [e.detail.get("number") for e in es if e.kind == "calving" and e.detail.get("number") is not None]
        if calv_ts:
            r.calving, r.dim = calv_ts[-1].date(), (today - calv_ts[-1].date()).days
            if len(calv_ts) >= 2:
                r.prev_calving = calv_ts[-2].date()
                r.calving_interval = (r.calving - r.prev_calving).days
            r.parity = int(parity_vals[-1]) if parity_vals else None
        start = calv_ts[-1] if calv_ts else datetime.min
        cur = [e for e in es if e.t >= start]
        r.heats = [t.date() for t in _merge([e.t for e in cur if e.kind == "heat"], 2)]
        r.ais = [t.date() for t in _merge([e.t for e in cur if e.kind == "ai"], 1)]
        pds = [e for e in cur if e.kind == "pd"]
        aborts = [e for e in cur if e.kind == "abort"]
        drys = [e for e in cur if e.kind == "dry"]
        if pds:
            r.pd_date, r.pd_result = pds[-1].t.date(), pds[-1].detail.get("pregnant")
        r.abort = _d(aborts[-1].t) if aborts else None
        r.dry_off = _d(drys[-1].t) if drys else None
        last_ai = r.ais[-1] if r.ais else None
        # 상태 판정
        pregnant = (r.pd_result is True and r.pd_date and (not last_ai or r.pd_date >= last_ai)
                    and not (r.abort and r.abort >= r.pd_date))
        if r.dry_off and (not r.calving or r.dry_off > r.calving):
            r.status = "dry"
            pregnant = True if r.pd_result is not False else pregnant
        elif pregnant:
            r.status = "pregnant"
        elif last_ai and (not r.pd_date or r.pd_date < last_ai):
            r.status = "bred"
        elif r.pd_result is False:
            r.status = "open_neg"
        elif not r.calving:
            r.status = "heifer"
        elif r.dim is not None and r.dim <= 21 and not r.ais:
            r.status = "fresh"
        else:
            r.status = "open"
        if pregnant and r.ais:
            pd_cut = r.pd_date or today
            r.conception = max((a for a in r.ais if a <= pd_cut), default=r.ais[-1])
        exp = next((e.detail.get("expected_calving_date") for e in reversed(pds) if e.detail.get("expected_calving_date")), None)
        if pregnant:
            if exp:
                try:
                    r.expected_calving = datetime.fromisoformat(str(exp).replace("Z", "+00:00")).date()
                except ValueError:
                    pass
            if not r.expected_calving and r.conception:
                r.expected_calving = r.conception + timedelta(days=gestation)
            if r.expected_calving:
                r.expected_dry = r.expected_calving - timedelta(days=dry_before)
        if r.calving:
            r.days_open = ((r.conception - r.calving).days if pregnant and r.conception else r.dim)
            if r.ais:
                r.first_ai_dim = (r.ais[0] - r.calving).days
            if r.heats:
                r.first_heat_dim = (r.heats[0] - r.calving).days
        # 주의 표시
        if r.status in ("open", "open_neg", "bred") and r.dim is not None and r.dim > 150:
            r.flags.append(f"분만 {r.dim}일 미임신")
        if r.status in ("open", "open_neg", "bred") and len(r.ais) >= 3:
            r.flags.append(f"수정 {len(r.ais)}회 미임신")
        if r.status in ("open", "fresh") and r.dim is not None and r.dim > 60 and not r.heats:
            r.flags.append("분만 60일 이상 발정 없음")
        if r.status == "bred" and last_ai and (today - last_ai).days > 45:
            r.flags.append(f"수정 {(today - last_ai).days}일 지남·감정 필요")
        if r.expected_calving and 0 <= (r.expected_calving - today).days <= 30:
            r.flags.append(f"분만예정 {(r.expected_calving - today).days}일 후")
        if r.expected_dry and r.status == "pregnant" and (r.expected_dry - today).days <= 7:
            r.flags.append("건유 시기")
        names = {"calving": "분만", "heat": "발정", "ai": "수정", "pd": "임신감정", "dry": "건유", "abort": "유산"}
        for e in reversed(es[-40:]):
            extra = ""
            if e.kind == "pd":
                extra = "임신" if e.detail.get("pregnant") else "비임신" if e.detail.get("pregnant") is False else ""
            elif e.kind == "calving" and e.detail.get("number") is not None:
                extra = f"{e.detail['number']}산"
            elif e.kind == "calving" and e.detail.get("_raw") == "calving_detection":
                extra = "센서 감지"
            elif e.kind == "heat" and e.detail.get("_raw", "").startswith("health_"):
                extra = "센서 발정"
            r.timeline.append({"date": f"{e.t:%Y-%m-%d}", "kind": names[e.kind], "extra": extra})
        out.append(r)
    order = {"fresh": 0, "open": 1, "open_neg": 2, "bred": 3, "pregnant": 4, "dry": 5, "heifer": 6}
    out.sort(key=lambda r: (order.get(r.status, 9), -(r.dim or 0)))
    return out


def farm_kpis(recs: list[CowRecord]) -> dict[str, Any]:
    cows = [r for r in recs if r.calving]
    preg = [r for r in cows if r.status in ("pregnant", "dry") and r.conception]
    k: dict[str, Any] = {"n": len(recs), "cows": len(cows),
                         "by_status": {STATUS[s]: sum(1 for r in recs if r.status == s) for s in STATUS}}
    k["pregnant_pct"] = round(len([r for r in cows if r.status in ("pregnant", "dry")]) / len(cows) * 100, 1) if cows else None
    k["days_open"] = round(statistics.mean(r.days_open for r in preg), 0) if preg else None
    fa = [r.first_ai_dim for r in cows if r.first_ai_dim is not None and r.first_ai_dim >= 0]
    k["first_ai_dim"] = round(statistics.mean(fa), 0) if fa else None
    k["ai_per_conception"] = round(sum(r.n_ai for r in preg) / len(preg), 2) if preg else None
    ci = [r.calving_interval for r in cows if r.calving_interval and 250 < r.calving_interval < 800]
    k["calving_interval"] = round(statistics.mean(ci), 0) if ci else None
    k["problem"] = sum(1 for r in recs if r.flags and any("미임신" in f or "발정 없음" in f for f in r.flags))
    k["calving_30d"] = sum(1 for r in recs if any(f.startswith("분만예정") for f in r.flags))
    return k
