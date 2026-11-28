"""자동 업무지시 알림 — 08:00(아침 판정 기준 전체) · 14:00(아침에 없던 목장 + 우선→긴급 올라간 목장만).

판정: 각 팀원 서버 수집(06:30·12:30)이 만든 reports/latest_state.json
받는 사람: 목장 담당자(딜러·판매과장, 팀 공용 연락처) — 담당자별로 목장을 묶어 1건
전달: 담당자가 '알림 받기'를 켠 기기에 웹 푸시(무료). 시험 모드면 관리자 기기로만.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)
TARGET = ("긴급", "우선")
G_PRI = ["group_rum", "heat_stress", "lying", "sync", "temp_high", "rumination", "ketosis", "mastitis", "milk_fever",
         "drinking", "activity", "temp_low"]
MAX_STATE_AGE = timedelta(hours=4)


def digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def _plain(t: str) -> str:
    return re.sub(r"\s*\[(문헌|관행)[^\]]*\]", "", t or "").strip()


def one_action(f: dict[str, Any], guides: dict[str, dict]) -> tuple[str, str] | None:
    ct, pri = f.get("cluster") or "", []
    if re.search(r"고온|THI", ct):
        pri.append("heat_stress")
    if re.search(r"체온|감염", ct):
        pri.append("temp_high")
    if re.search(r"SARA|반추", ct):
        pri += ["group_rum", "rumination"]
    have = f.get("guides") or []
    gid = next((g for g in pri + G_PRI if g in have and g in guides), None)
    if gid and guides[gid].get("act"):
        return guides[gid]["title"], _plain(guides[gid]["act"][0])
    c = f.get("checklist0")
    return (c["topic"], c["text"]) if c else None


def farm_lines(f: dict[str, Any], guides: dict[str, dict], note: str = "") -> list[str]:
    a = one_action(f, guides)
    cows = ", ".join(f"{x['label']}({x['types']})" for x in f.get("animals") or [])
    why = ", ".join((f.get("reasons") or [])[:2]) or ((f.get("headline") or [""])[0])
    out = [f"■ {f['name']} — {f['grade']}({f['score']}점){' ' + note if note else ''}", f"· 사유: {why}"]
    if cows:
        out.append(f"· 확인 개체: {cows}")
    if a:
        out.append(f"· 요청: {a[0]} — {a[1]}")
    return out


def message(name: str, farms: list[dict], guides: dict[str, dict], as_of: str, slot: str) -> str:
    due = "오늘 중" if any(f["grade"] == "긴급" for f in farms) else "내일까지"
    head = "추가 확인이 필요한 목장입니다." if slot == "pm" else "확인이 필요한 목장입니다."
    body = [f"[목장모니터 업무요청] {name} 님", f"{as_of[5:10].replace('-', '/')} {as_of[11:16]} 판정 기준 {head}", ""]
    for f in farms:
        body += farm_lines(f, guides, f.get("_note", "")) + [""]
    body.append(f"{due} 확인 후 결과 회신 부탁드립니다.")
    return "\n".join(body)


def load_states(paths: dict[str, Path], now: datetime, max_age: timedelta = MAX_STATE_AGE
                ) -> tuple[dict[str, dict], dict[str, list[str]], list[str], str]:
    """팀원별 latest_state.json → (목장 판정, 목장별 담당 팀원 uid, 오래된 자료 경고, 기준 시각)."""
    farms: dict[str, dict] = {}
    owners: dict[str, list[str]] = {}
    stale, as_of = [], ""
    for uid, p in paths.items():
        try:
            st = json.loads(p.read_text(encoding="utf-8"))
            t = datetime.strptime(st["asOf"], "%Y-%m-%d %H:%M")
        except (OSError, ValueError, KeyError):
            continue
        if now - t > max_age:
            stale.append(uid)
            continue
        as_of = max(as_of, st["asOf"])
        for f in st["farms"]:
            owners.setdefault(f["id"], []).append(uid)
            if f["id"] not in farms or st["asOf"] > farms[f["id"]]["_asof"]:
                farms[f["id"]] = {**f, "_asof": st["asOf"]}
    return farms, owners, stale, as_of


def plan(farms: dict[str, dict], contacts: dict[str, list[dict]], slot: str,
         sent: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    """담당자별 보낼 목장. sent = {전화(숫자): {목장: 오늘 보낸 등급}} (오후 판단용)."""
    by: dict[str, dict[str, Any]] = {}
    for fid, f in farms.items():
        if f["grade"] not in TARGET:
            continue
        for c in contacts.get(fid, []):
            ph = digits(c["phone"])
            if not ph:
                continue
            note = ""
            if slot == "pm":
                prev = sent.get(ph, {}).get(fid)
                if prev == f["grade"] or (prev == "긴급"):
                    continue                      # 아침에 같은(또는 더 높은) 등급으로 이미 보냄
                note = "(긴급으로 올라감)" if prev == "우선" else "(신규)"
            e = by.setdefault(ph, {"phone": ph, "name": c["name"], "role": c["role"], "farms": []})
            e["farms"].append({**f, "_note": note})
    for e in by.values():
        e["farms"].sort(key=lambda f: (f["grade"] != "긴급", -f["score"]))
    return sorted(by.values(), key=lambda e: e["name"])


def notice_body(farms: list[dict], guides: dict[str, dict], max_farms: int = 4) -> str:
    """알림 본문 — 알림만 봐도 목장·등급·사유·요청이 보이게(펼치면 여러 줄)."""
    lines = []
    for f in farms[:max_farms]:
        why = ((f.get("reasons") or [])[:1] or (f.get("headline") or [""])[:1])[0]
        a = one_action(f, guides)
        lines.append(f"[{f['grade']}] {f['name']}{' ' + f['_note'] if f.get('_note') else ''} · {why}"[:90])
        if a:
            lines.append(f"  → {a[0]}: {a[1]}"[:90])
    if len(farms) > max_farms:
        lines.append(f"외 {len(farms) - max_farms}곳 — 눌러서 전체 보기")
    return "\n".join(lines)


def reason(sm: dict[str, Any]) -> str:
    """보낸 것이 없을 때 이유 한 줄."""
    if sm.get("note") == "꺼짐":
        return "자동 알림이 꺼져 있음"
    if not sm.get("as_of"):
        if sm.get("stale"):
            return "판정 자료가 오래됨(수집 실패 또는 수집 전) — 계정 → 지금 새로고침 후 다시"
        return "판정 자료 없음 — 업데이트 후 수집이 한 번 끝나야 함(계정 → 지금 새로고침)"
    base = f"{sm['as_of'][5:16]} 판정 · 긴급·우선 {sm.get('targets', 0)}곳"
    if not sm.get("targets"):
        return base + " — 보낼 목장 없음"
    if sm.get("no_contact") and len(sm["no_contact"]) == sm["targets"]:
        return base + " 모두 담당자 미등록 — 목장 상세 9번 또는 관리자 엑셀로 담당자 등록"
    if sm.get("slot") == "pm" and not sm.get("farms"):
        return base + " — 아침에 이미 보낸 목장뿐(오후엔 신규·우선→긴급만)"
    if sm.get("no_device"):
        return base + f" — 담당자 {', '.join(sm['no_device'][:3])} 알림 미설정(링크 보내기)"
    return base


def run(store, slot: str, now: datetime | None = None, sender=None, vapid=None, manual: bool = False) -> dict[str, Any]:
    """서버 타이머가 08:00(am)·14:00(pm) 실행. manual=관리자 '지금 실행'(판정 자료 24시간까지 허용).
    반환: 요약(관리자 화면 표시)."""
    from .actions import guides as _guides
    from .cloud import webpush
    now = now or datetime.now()
    day = now.strftime("%Y-%m-%d")
    enabled = store.kv_get("dispatch_enabled", "0") == "1"
    test = store.kv_get("dispatch_test", "1") == "1"
    cc = store.kv_get("dispatch_cc", "0") == "1"
    summary: dict[str, Any] = {"at": now.strftime("%Y-%m-%d %H:%M"), "slot": slot, "enabled": enabled, "test": test,
                               "contacts": 0, "farms": 0, "delivered": 0, "no_device": [], "stale": [], "team": 0,
                               "targets": 0, "no_contact": [], "as_of": "", "no_state": []}
    if not enabled:
        summary["note"] = "꺼짐"
        store.kv_set("dispatch_last", json.dumps(summary, ensure_ascii=False))
        return summary
    users = store.users()
    paths = {u.uid: store.user_dir(u.uid) / "reports" / "latest_state.json" for u in users}
    farms, owners, stale, as_of = load_states(paths, now, timedelta(hours=24) if manual else MAX_STATE_AGE)
    summary["stale"] = [u.username for u in users if u.uid in stale]
    summary["no_state"] = [u.username for u in users if not paths[u.uid].exists()]
    summary["as_of"] = as_of
    guides = {g["id"]: g for g in _guides()}
    from .heat_dispatch import DEFAULT_ORDER_ROLES, roles
    orl = roles(store, "order_roles", DEFAULT_ORDER_ROLES)       # 업무요청 받는 역할(수정사·목장주 제외가 기본)
    contacts = {fid: [c for c in cs if c["role"] in orl] for fid, cs in store.contacts().items()}
    contacts = {k: v for k, v in contacts.items() if v}
    sent = {}
    if slot == "pm":
        for cs in contacts.values():
            for c in cs:
                ph = digits(c["phone"])
                sent.setdefault(ph, store.sent_today(day, ("t:" if test else "") + ph))
    plans = plan(farms, contacts, slot, sent)
    tg = [f for f in farms.values() if f["grade"] in TARGET]
    summary["targets"] = len(tg)
    summary["no_contact"] = [f["name"] for f in tg if not contacts.get(f["id"])]
    vapid = vapid or webpush.Vapid(store.root / "vapid.pem")
    sender = sender or webpush.send
    claim = store.kv_get("site_url") or "mailto:admin@example.com"
    adm = store.get_by_username(store.admin) if store.admin else None
    admin_subs = store.subs("u:" + adm.uid) if adm else []

    from urllib.parse import urlparse

    def push(subs: list[dict], data: dict) -> int:
        ok = 0
        for s in subs:
            code, msg = sender(vapid, s, data, contact=claim)
            good = 200 <= code < 300
            store.sub_result(s["endpoint"], good, gone=code in (404, 410))
            ok += good
            if not good:   # 실패는 기록(점검 화면·로그) — 404/410 = 그 기기 알림 등록이 사라짐(앱 삭제·알림 끔)
                e = {"owner": s.get("owner", ""), "host": urlparse(s["endpoint"]).netloc, "code": code, "msg": (msg or "")[:80]}
                summary.setdefault("push_errors", []).append(e)
                log.warning("알림 실패 %s %s %s %s", e["owner"], e["host"], code, e["msg"])
        return ok

    for p in plans:
        text = message(p["name"], p["farms"], guides, as_of, slot)
        flist = [{"id": f["id"], "name": f["name"], "grade": f["grade"], "score": f["score"], "note": f.get("_note", "")}
                 for f in p["farms"]]
        did = store.add_dispatch(day, slot, p["phone"], p["name"], flist, text, test)
        rcv = store.receiver_by_phone(p["phone"])
        n_urg = sum(f["grade"] == "긴급" for f in p["farms"])
        n_pri = len(p["farms"]) - n_urg
        data = {"title": f"{'[시험] ' + p['name'] + ' 님께 갈 · ' if test else ''}업무요청 "
                         + " · ".join(x for x in (f"긴급 {n_urg}" if n_urg else "", f"우선 {n_pri}" if n_pri else "") if x)
                         + (" (오후 추가)" if slot == "pm" else ""),
                "body": notice_body(p["farms"], guides), "url": f"/c/{rcv['token']}" if rcv else "/", "tag": f"order-{did}"}
        subs = admin_subs if test else (store.subs("r:" + rcv["id"]) if rcv else [])
        got = push(subs, data) if subs else 0
        if not test and cc and admin_subs:      # 관리자 사본
            summary["cc"] = summary.get("cc", 0) + push(admin_subs, {**data, "title": f"[사본] {p['name']} 님께 보냄 · " + data["title"],
                                                                      "tag": f"cc-{did}"})
        store.dispatch_delivered(did, len(subs), got)
        summary["contacts"] += 1
        summary["farms"] += len(p["farms"])
        summary["delivered"] += got > 0
        if not subs:
            summary["no_device"].append(p["name"])
        if not test:   # 담당 팀원 조치 기록에 '업무지시' 자동 기록 (완료 보고 받으면 결과 자동 입력)
            for f in p["farms"]:
                for uid in owners.get(f["id"], []):
                    store.set_note(uid, f"act:{f['id']}:d{did}", {
                        "d": day, "target": "담당자:" + p["name"], "sig": "업무지시", "acts": ["담당자 업무지시"],
                        "memo": f"자동 알림 {'08:00' if slot == 'am' else '14:00'} → {p['role']} {p['name']}"
                                + ("" if subs else " (알림 미설정 — 직접 연락 필요)"), "res": None, "rd": None, "did": did})
    # 팀원 요약 알림
    for u in users:
        mine = [farms[f] for f in farms if u.uid in owners.get(f, [])]
        tgt = [f for f in mine if f["grade"] in TARGET]
        if slot == "pm":
            new_ids = {f["id"] for p in plans for f in p["farms"]}
            tgt = [f for f in tgt if f["id"] in new_ids]
        subs = store.subs("u:" + u.uid)
        if not tgt or not subs or (test and (adm is None or u.uid != adm.uid)):
            continue
        n_u = sum(f["grade"] == "긴급" for f in tgt)
        tgt.sort(key=lambda f: (f["grade"] != "긴급", -f["score"]))
        summary["team"] += push(subs, {"title": f"오늘 할 일 — 긴급 {n_u} · 우선 {len(tgt) - n_u}" if slot == "am"
                                        else f"오후 추가 — {len(tgt)}곳", "body": notice_body(tgt, guides, 3),
                                        "url": "/", "tag": f"team-{slot}"}) > 0
    # 시험 모드·'지금 실행'인데 관리자에게 아무것도 안 갔으면 → 이유를 알림으로(조용히 끝나지 않게)
    if (test or manual) and not summary["delivered"] and not summary["team"] and admin_subs:
        push(admin_subs, {"title": f"[{'시험' if test else '실행'}] 보낼 업무요청 없음", "body": reason(summary), "url": "/account", "tag": "dispatch-none"})
    store.kv_set("dispatch_last", json.dumps(summary, ensure_ascii=False))
    log.info("자동 업무지시 %s: 담당자 %d명 · 목장 %d · 전달 %d", slot, summary["contacts"], summary["farms"], summary["delivered"])
    return summary
