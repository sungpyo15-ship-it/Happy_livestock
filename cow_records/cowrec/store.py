"""개체 기록부 저장소: smaXtec 메모·알림 확인 코멘트 (번식·알림 이벤트는 smxmon DB 를 읽음)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (          -- smaXtec 메모(구조화: 분류·질병·조치·메모)
    note_id    TEXT PRIMARY KEY,
    farm_id    TEXT NOT NULL,
    animal_id  TEXT,                        -- smaXtec 내부 개체 ID
    ts         TEXT,                        -- 메모 기준 시각(현지)
    category   TEXT, event TEXT, actions TEXT,   -- actions = JSON 배열
    event_comment TEXT, action_comment TEXT,
    source     TEXT, alert_type TEXT,       -- 연결된 알림 종류(예: 401_mastitis)
    fetched_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_notes_farm ON notes(farm_id, animal_id);
CREATE TABLE IF NOT EXISTS comments (       -- 알림 확인(할 일) 코멘트 (자유 텍스트)
    comment_id TEXT PRIMARY KEY,
    farm_id    TEXT NOT NULL,
    animal_id  TEXT,
    ts         TEXT,
    content    TEXT,
    alert_type TEXT,
    closing    TEXT
);
CREATE INDEX IF NOT EXISTS ix_comments_farm ON comments(farm_id, animal_id);
CREATE TABLE IF NOT EXISTS farm_scan (      -- 목장별 마지막 메모 조회
    farm_id TEXT PRIMARY KEY, scanned_at TEXT, n_notes INTEGER, n_comments INTEGER, n_todos INTEGER
);
"""


class Store:
    def __init__(self, path: Path | str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def known_note_ids(self, farm_id: str) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT note_id FROM notes WHERE farm_id=?", (farm_id,))}

    def put_note(self, farm_id: str, n: dict, animal_id: str | None, alert_type: str | None, ts: str, now: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO notes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (n["_id"], farm_id, animal_id, ts, n.get("category"), n.get("note_event"),
             json.dumps(n.get("note_actions") or [], ensure_ascii=False), n.get("note_event_comment") or "",
             n.get("note_action_comment") or "", n.get("source"), alert_type, now))

    def put_comment(self, farm_id: str, c: dict, animal_id: str | None, alert_type: str | None, closing: str | None, ts: str):
        self.conn.execute("INSERT OR REPLACE INTO comments VALUES (?,?,?,?,?,?,?)",
                          (c["_id"], farm_id, animal_id, ts, (c.get("content") or "").strip(), alert_type, closing))

    def mark_scan(self, farm_id: str, now: str, n_notes: int, n_comments: int, n_todos: int):
        self.conn.execute("INSERT OR REPLACE INTO farm_scan VALUES (?,?,?,?,?)", (farm_id, now, n_notes, n_comments, n_todos))
        self.conn.commit()

    def farm_counts(self) -> dict[str, tuple[int, int]]:
        out = {}
        for r in self.conn.execute("SELECT farm_id, COUNT(*) FROM notes GROUP BY farm_id"):
            out[r[0]] = (r[1], 0)
        for r in self.conn.execute("SELECT farm_id, COUNT(*) FROM comments WHERE content != '' GROUP BY farm_id"):
            out[r[0]] = (out.get(r[0], (0, 0))[0], r[1])
        return out
