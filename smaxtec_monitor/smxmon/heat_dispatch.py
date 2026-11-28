"""발정 알림 — 07:00·13:00·19:00 (각 수집 직후). 지난 알림 이후 새 발정만, 받는 사람별로 묶어서.

받는 사람: 팀원(자기 담당 목장) · 목장 담당자 중 '발정 받는 역할'(기본 딜러·판매과장·수정사·목장주)
켜기/시험 모드/역할: 관리자 화면 (kv: heat_enabled, heat_test, heat_roles)
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from .heat import notice_body, recent_heats

log = logging.getLogger(__name__)
WINDOW = timedelta(hours=20)       # 이 안의 발정 중 아직 안 보낸 것
DEFAULT_HEAT_ROLES = "딜러,판매과장,수정사,목장주"
DEFAULT_ORDER_ROLES = "딜러,판매과장,수의사,기타,담당"


def roles(store, key: str, default: str) -> set[str]:
    return {x.strip() for x in (store.kv_get(key) or default).split(",") if x.strip()}


def user_heats(store, now: datetime, since: datetime) -> tuple[dict[str, list[dict]], dict[str, dict]]:
    """팀원별 발정(자기 DB 목장) · 전체(중복 제거, key → 발정)."""
    per_user, allh = {}, {}
    for u in store.users():
        p = store.user_dir(u.uid) / "data" / "smaxtec.db"
        if not p.exists():
            continue
        c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            hs = recent_heats(c, since, now)
        except sqlite3.Error as e:
            log.warning("발정 조회 실패 %s: %s", u.username, e)
            hs = []
        finally:
            c.close()
        per_user[u.uid] = hs
        for h in hs:
            allh.setdefault(h["key"], h)
    return per_user, allh


def heats_for_farms(store, farm_ids: set[str], hours: int = 48) -> list[dict]:
    now = datetime.now()
    _, allh = user_heats(store, now, now - timedelta(hours=hours))
    return sorted((h for h in allh.values() if h["farm_id"] in farm_ids), key=lambda h: h["t"], reverse=True)


def run(store, now: datetime | None = None, sender=None, vapid=None, manual: bool = False) -> dict[str, Any]:
    from .cloud import webpush
    now = now or datetime.now()
    enabled = store.kv_get("heat_enabled", "0") == "1"
    test = store.kv_get("heat_test", "1") == "1" or (manual and not enabled)   # 꺼진 상태의 '지금 실행'은 항상 시험
    sm: dict[str, Any] = {"at": now.strftime("%Y-%m-%d %H:%M"), "enabled": enabled, "test": test, "heats": 0,
                          "team": 0, "contacts": 0, "delivered": 0, "no_device": [], "errors": []}
    if not enabled and not manual:
        sm["note"] = "꺼짐"
        store.kv_set("heat_last", json.dumps(sm, ensure_ascii=False))
        return sm
    per_user, allh = user_heats(store, now, now - WINDOW)
    sm["heats"] = len(allh)
    vapid = vapid or webpush.Vapid(store.root / "vapid.pem")
    sender = sender or webpush.send
    claim = store.kv_get("site_url") or "mailto:admin@example.com"
    adm = store.get_by_username(store.admin) if store.admin else None
    admin_subs = store.subs("u:" + adm.uid) if adm else []
    hh = now.strftime("%H:%M")

    def push(subs, data) -> int:
        ok = 0
        for s in subs:
            code, msg = sender(vapid, s, data, contact=claim)
            good = 200 <= code < 300
            store.sub_result(s["endpoint"], good, gone=code in (404, 410))
            ok += good
            if not good:
                sm["errors"].append(f"{s.get('owner', '')} {code} {(msg or '')[:60]}")
        return ok

    def title(hs, who=""):
        nf = len({h["farm_id"] for h in hs})
        return f"{'[시험] ' + who + ' 님께 갈 · ' if test and who else ''}발정 {len(hs)}두 · {nf}개 목장 ({hh})"

    # 1) 팀원
    for u in store.users():
        hs = [h for h in per_user.get(u.uid, []) if h["key"] not in store.heat_sent("u:" + u.uid)]
        subs = store.subs("u:" + u.uid)
        if not hs or not subs or (test and (adm is None or u.uid != adm.uid)):
            continue
        if push(subs, {"title": title(hs), "body": notice_body(hs), "url": "/heat", "tag": f"heat-u-{hh}"}):
            sm["team"] += 1
            store.heat_mark_sent("u:" + u.uid, [h["key"] for h in hs])
    # 2) 목장 담당자(발정 받는 역할)
    hr = roles(store, "heat_roles", DEFAULT_HEAT_ROLES)
    people: dict[str, dict] = {}
    for fid, cs in store.contacts().items():
        for c in cs:
            if c["role"] not in hr:
                continue
            ph = re.sub(r"\D", "", c["phone"])
            people.setdefault(ph, {"name": c["name"], "role": c["role"], "farms": set()})["farms"].add(fid)
    for ph, e in sorted(people.items(), key=lambda x: x[1]["name"]):
        who = "p:" + ph
        hs = [h for h in allh.values() if h["farm_id"] in e["farms"] and h["key"] not in store.heat_sent(who)]
        if not hs:
            continue
        hs.sort(key=lambda h: h["t"], reverse=True)
        rcv = store.receiver_by_phone(ph)
        real = store.subs("r:" + rcv["id"]) if rcv else []
        subs = admin_subs if test else real
        sm["contacts"] += 1
        if not real:
            sm["no_device"].append(e["name"])      # 실제로는 받을 기기 없음(시험 모드에서도 알려 줌)
        if not subs:
            continue
        got = push(subs, {"title": title(hs, e["name"]), "body": notice_body(hs),
                          "url": f"/c/{rcv['token']}/heat" if rcv else "/heat", "tag": f"heat-{ph}-{hh}"})
        if got:
            sm["delivered"] += 1
            if not test:
                store.heat_mark_sent(who, [h["key"] for h in hs])
    store.kv_set("heat_last", json.dumps(sm, ensure_ascii=False))
    log.info("발정 알림: 발정 %d두 · 팀원 %d · 담당자 %d(전달 %d)", sm["heats"], sm["team"], sm["contacts"], sm["delivered"])
    return sm
