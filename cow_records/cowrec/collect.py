"""smaXtec 메모·알림 확인 코멘트 수집 (읽기 전용).

- GET /api/v2/organisations/{org}/todos?done=true : 알림 확인 기록(할 일). data.note_id, comments 포함
- GET /api/v2/notes/{note_id}                      : 메모 본문(분류·질병·조치·메모)
※ smaXtec 공개 API 에 '메모 목록' 기능이 없어, 알림 확인 기록에 연결된 메모만 가져올 수 있음
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from smxmon.api_client import SmaxtecAPI, to_local_naive  # noqa: E402  (cowrec/__init__ 가 경로 추가)
from smxmon.common import LoginError, ScrapeError
from smxmon.config import Settings

from .store import Store

log = logging.getLogger("smxmon")


def _local(ts: str | None, tz: str) -> str:
    """시간대 없는 값은 UTC 로 보고(할 일 기록) 현지시간 문자열로."""
    if not ts:
        return ""
    s = ts if ("+" in ts[10:] or ts.endswith("Z")) else ts + "+00:00"
    try:
        return to_local_naive(s, tz).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ts[:19].replace("T", " ")


def collect_notes(s: Settings, store: Store, farm_ids: list[str] | None = None, pause: float = 0.25) -> dict:
    api = SmaxtecAPI(s)
    farms = api.list_farms()
    if farm_ids:
        farms = [f for f in farms if f.farm_id in farm_ids or any(k in f.name for k in farm_ids)]
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    stats = {"farms": 0, "new_notes": 0, "comments": 0, "errors": []}
    for f in farms:
        try:
            todos = api._request("GET", f"/organisations/{f.farm_id}/todos", params={"done": "true"},
                                 base=api.public_base) or []
        except (ScrapeError, LoginError) as e:
            stats["errors"].append(f"{f.name}: {e}")
            log.warning("목장 %s 할 일 조회 실패: %s", f.name, e)
            continue
        known = store.known_note_ids(f.farm_id)
        n_new = n_com = 0
        for t in todos:
            d = t.get("data") or {}
            aid, alert = d.get("animal_id"), d.get("sub_type") or d.get("data_type")
            for c in t.get("comments") or []:
                if (c.get("content") or "").strip():
                    store.put_comment(f.farm_id, c, aid, alert, t.get("closing_reason"), _local(c.get("created_at"), s.timezone))
                    n_com += 1
            nid = d.get("note_id")
            if nid and nid not in known:
                try:
                    n = api._request("GET", f"/notes/{nid}", base=api.public_base)
                except (ScrapeError, LoginError) as e:
                    log.warning("메모 %s 조회 실패: %s", nid, e)
                    continue
                if n and n.get("active", True) is not False:
                    store.put_note(f.farm_id, n, aid, alert, _local(n.get("note_ts") or n.get("created_at"), s.timezone), now)
                    n_new += 1
                known.add(nid)
                time.sleep(pause)
        store.mark_scan(f.farm_id, now, len(known), n_com, len(todos))
        stats["farms"] += 1
        stats["new_notes"] += n_new
        stats["comments"] += n_com
        log.info("목장 %s: 할 일 %d건, 새 메모 %d건, 코멘트 %d건", f.name, len(todos), n_new, n_com)
    return stats
