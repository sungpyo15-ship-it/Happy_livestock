"""개체별 질병·치료 기록: smaXtec 메모 + 알림 확인 코멘트 + 진단 + 주요 임상 알림(케토시스·유방염·유열)."""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from typing import Any

from .codes import ACTION, CATEGORY, CLOSING, EVENT, TODO_TYPE, ko

CLINICAL = {"KETOSIS": "케토시스 의심", "MASTITIS": "유방염 의심", "MILK_FEVER": "유열 위험"}


def farm_health(cow: sqlite3.Connection, smx: sqlite3.Connection, farm_id: str,
                include_sensor: bool = True) -> dict[str, list[dict[str, Any]]]:
    """{animal_id: [기록(최신순)]}. 기록 = 날짜·출처·분류·내용·조치·메모."""
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in cow.execute("SELECT * FROM notes WHERE farm_id=?", (farm_id,)):
        acts = json.loads(r["actions"] or "[]")
        out[r["animal_id"]].append({
            "date": (r["ts"] or "")[:16], "source": "smaXtec 메모", "category": ko(CATEGORY, r["category"]),
            "what": ko(EVENT, r["event"]), "action": ", ".join(ko(ACTION, a) for a in acts),
            "memo": " / ".join(x for x in (r["event_comment"], r["action_comment"]) if x),
            "alert": ko(TODO_TYPE, r["alert_type"])})
    for r in cow.execute("SELECT * FROM comments WHERE farm_id=? AND content != ''", (farm_id,)):
        out[r["animal_id"]].append({
            "date": (r["ts"] or "")[:16], "source": "알림 확인 코멘트", "category": "", "what": ko(TODO_TYPE, r["alert_type"]),
            "action": ko(CLOSING, r["closing"]), "memo": r["content"], "alert": ""})
    for aid, ts, det in smx.execute(
            "SELECT animal_id, occurred_at, detail FROM alerts WHERE farm_id=? AND lower(alert_type_raw)='diagnosis'", (farm_id,)):
        try:
            d = json.loads(det) if det else {}
        except ValueError:
            d = {}
        out[aid].append({"date": ts[:16], "source": "진단 기록", "category": d.get("diagnosis_key_type", ""),
                         "what": ko(EVENT, d.get("diagnosis_key")), "action": "", "memo": d.get("reason", ""), "alert": ""})
    if include_sensor:
        for aid, typ, ts in smx.execute(
                "SELECT animal_id, alert_type, occurred_at FROM alerts WHERE farm_id=? AND alert_type IN ('KETOSIS','MASTITIS','MILK_FEVER')",
                (farm_id,)):
            out[aid].append({"date": ts[:16], "source": "센서 알림", "category": "", "what": CLINICAL[typ],
                             "action": "", "memo": "", "alert": ""})
    for k in out:
        out[k].sort(key=lambda x: x["date"], reverse=True)
    return dict(out)


def memo_count(recs: dict[str, list[dict]]) -> int:
    return sum(1 for v in recs.values() for x in v if x["source"] in ("smaXtec 메모", "알림 확인 코멘트", "진단 기록"))
