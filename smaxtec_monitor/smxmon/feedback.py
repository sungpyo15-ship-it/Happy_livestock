"""농장 피드백(정답 자료) 수집 — 판정 검증 때만 실행 (평소 2시간 수집에는 넣지 않음).

- GET /api/v2/organisations/{org}/todos?done=true : 알림 확인 처리(할 일)
    closing_reason: user-ack(농장이 확인) / user-nack(농장이 '해당 없음' = 농장 판단 오경보) / auto-*(자동 종료)
- GET /api/v2/notes/{note_id}                      : 연결된 메모(분류·질병·조치) — 질병 메모 = 진단에 가까운 기록
한 번 받은 메모는 저장 → 다음부터 새 메모만 조회.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime
from typing import Any

from .api_client import SmaxtecAPI, to_local_naive
from .common import LoginError, ScrapeError

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS fb_todos (
    todo_id   TEXT PRIMARY KEY,
    farm_id   TEXT NOT NULL,
    animal_id TEXT,
    data_type TEXT, sub_type TEXT,
    closing   TEXT,          -- user-ack / user-nack / auto-ack / auto-nack / archived
    created   TEXT, done TEXT,   -- 현지시각 (없으면 NULL)
    note_id   TEXT,
    keys      TEXT,          -- 원문 항목 이름(구조 확인용)
    fetched_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_fb_todos_farm ON fb_todos(farm_id);
CREATE TABLE IF NOT EXISTS fb_notes (
    note_id   TEXT PRIMARY KEY,
    farm_id   TEXT NOT NULL,
    animal_id TEXT,
    ts        TEXT,
    category  TEXT, event TEXT, actions TEXT,
    fetched_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_fb_notes_farm ON fb_notes(farm_id);
"""
# 질병으로 보지 않는 메모 분류(번식 관리 등)
NON_DISEASE_CATEGORY = {"REPRODUCTION", "FERTILITY"}


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _local(ts: Any, tz: str) -> str | None:
    if not ts or not isinstance(ts, str):
        return None
    s = ts if ("+" in ts[10:] or ts.endswith("Z")) else ts + "+00:00"
    try:
        return to_local_naive(s, tz).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ts[:19].replace("T", " ")


def _first(d: dict, *keys: str) -> Any:
    for k in keys:
        if d.get(k):
            return d[k]
    return None


def fetch(api: SmaxtecAPI, conn: sqlite3.Connection, farm_ids: list[str], tz: str, pause: float = 0.25,
          progress=None) -> dict[str, int]:
    ensure_schema(conn)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    st = {"farms": 0, "todos": 0, "new_notes": 0, "errors": 0}
    for i, fid in enumerate(farm_ids, 1):
        try:
            todos = api._request("GET", f"/organisations/{fid}/todos", params={"done": "true"},
                                 base=api.public_base) or []
        except (ScrapeError, LoginError) as e:
            st["errors"] += 1
            log.warning("목장 %s 알림 확인 기록 조회 실패: %s", fid, e)
            continue
        if isinstance(todos, dict):
            todos = todos.get("data") or todos.get("items") or []
        known = {r[0] for r in conn.execute("SELECT note_id FROM fb_notes WHERE farm_id=?", (fid,))}
        for t in todos:
            if not isinstance(t, dict):
                continue
            d = t.get("data") or {}
            tid = str(t.get("_id") or t.get("id") or "")
            if not tid:
                continue
            nid = d.get("note_id")
            created = _local(_first(t, "created_at", "create_ts", "ts") or _first(d, "event_ts", "ts"), tz)
            done = _local(_first(t, "done_at", "closed_at", "updated_at", "done_ts"), tz)
            conn.execute("INSERT OR REPLACE INTO fb_todos VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (tid, fid, d.get("animal_id"), d.get("data_type"), d.get("sub_type"), t.get("closing_reason"),
                          created, done, nid, json.dumps(sorted(set(t) | {f"data.{k}" for k in d})), now))
            st["todos"] += 1
            if nid and nid not in known:
                try:
                    n = api._request("GET", f"/notes/{nid}", base=api.public_base)
                except (ScrapeError, LoginError) as e:
                    log.warning("메모 %s 조회 실패: %s", nid, e)
                    continue
                known.add(nid)
                if n and n.get("active", True) is not False:
                    conn.execute("INSERT OR REPLACE INTO fb_notes VALUES (?,?,?,?,?,?,?,?)",
                                 (nid, fid, n.get("animal_id") or d.get("animal_id"),
                                  _local(_first(n, "note_ts", "created_at"), tz), n.get("category"), n.get("note_event"),
                                  json.dumps(n.get("note_actions") or [], ensure_ascii=False), now))
                    st["new_notes"] += 1
                time.sleep(pause)
        conn.commit()
        st["farms"] += 1
        if progress and i % 10 == 0:
            progress(f"  농장 피드백 {i}/{len(farm_ids)}곳 (확인기록 {st['todos']} · 새 메모 {st['new_notes']})")
    return st
