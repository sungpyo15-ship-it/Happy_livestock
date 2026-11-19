"""SQLite 저장소: 스키마, 알림 중복제거(upsert), 해제 판정."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .normalize import to_db_time

SCHEMA = """
PRAGMA foreign_keys = ON;

-- 수집 1회 = 1행. 성공/실패·건수 기록 → 리포트의 '수집현황' 근거
CREATE TABLE IF NOT EXISTS collection_runs (
    run_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    status         TEXT NOT NULL DEFAULT 'running',   -- running / ok / partial / failed
    farms_ok       INTEGER NOT NULL DEFAULT 0,
    farms_failed   INTEGER NOT NULL DEFAULT 0,
    alerts_seen    INTEGER NOT NULL DEFAULT 0,
    alerts_new     INTEGER NOT NULL DEFAULT 0,
    alerts_resolved INTEGER NOT NULL DEFAULT 0,
    error          TEXT
);

CREATE TABLE IF NOT EXISTS farms (
    farm_id        TEXT PRIMARY KEY,           -- 사이트 내부 목장 ID
    name           TEXT NOT NULL,
    herd_size      INTEGER,                    -- farms.toml 로 덮어씀
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL,
    last_ok_at     TEXT                         -- 마지막 '정상' 수집 시각(데이터 신선도)
);

-- 알림 1건 = 1행 (중복 제거 키: 목장+개체+알림종류+발생시각)
CREATE TABLE IF NOT EXISTS alerts (
    alert_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    farm_id        TEXT NOT NULL REFERENCES farms(farm_id),
    animal_id      TEXT NOT NULL,
    alert_type     TEXT NOT NULL,              -- 표준코드 (alert_types.toml)
    alert_type_raw TEXT,                       -- 화면 원문
    occurred_at    TEXT NOT NULL,              -- 발생 시각(사이트 표기)
    first_seen_at  TEXT NOT NULL,              -- 이 프로그램이 처음 본 시각
    last_seen_at   TEXT NOT NULL,              -- 마지막으로 목록에 있던 시각
    resolved_at    TEXT,                       -- 해제 시각
    resolved_source TEXT,                      -- site(화면표기) / inferred(목록에서 사라짐)
    status         TEXT NOT NULL DEFAULT 'active',  -- active / resolved
    reopen_count   INTEGER NOT NULL DEFAULT 0,
    time_is_relative INTEGER NOT NULL DEFAULT 0,
    detail         TEXT,
    UNIQUE (farm_id, animal_id, alert_type, occurred_at)
);
CREATE INDEX IF NOT EXISTS ix_alerts_farm_time ON alerts(farm_id, occurred_at);
CREATE INDEX IF NOT EXISTS ix_alerts_status ON alerts(farm_id, status);

-- 수집 시점별 스냅샷(어느 수집에서 어떤 알림이 보였는지) → 시점별 누적 이력
CREATE TABLE IF NOT EXISTS alert_observations (
    run_id   INTEGER NOT NULL REFERENCES collection_runs(run_id),
    alert_id INTEGER NOT NULL REFERENCES alerts(alert_id),
    PRIMARY KEY (run_id, alert_id)
);

-- (확장용) 개체 지표 시계열: 체온·반추·음수·활동·pH
CREATE TABLE IF NOT EXISTS animal_metrics (
    farm_id      TEXT NOT NULL,
    animal_id    TEXT NOT NULL,
    metric       TEXT NOT NULL,     -- temp / rumination / drinking / activity / ph
    measured_at  TEXT NOT NULL,
    value        REAL,
    collected_at TEXT NOT NULL,
    PRIMARY KEY (farm_id, animal_id, metric, measured_at)
);

-- 긴급알림 중복발송 방지
CREATE TABLE IF NOT EXISTS notifications (
    notify_key  TEXT PRIMARY KEY,
    sent_at     TEXT NOT NULL,
    summary     TEXT
);

-- 지속시간(시간): 해제됐으면 해제시각, 아니면 마지막 확인시각까지
CREATE VIEW IF NOT EXISTS v_alerts AS
SELECT a.*,
       f.name AS farm_name,
       ROUND((julianday(COALESCE(a.resolved_at, a.last_seen_at)) - julianday(a.occurred_at)) * 24, 1)
           AS duration_hours
FROM alerts a JOIN farms f USING (farm_id);
"""


@dataclass
class UpsertResult:
    alert_id: int
    action: str  # new / seen / reopened / resolved_by_site


class Database:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.init_schema()

    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ───────── 수집 실행 기록 ─────────
    def start_run(self, now: datetime) -> int:
        cur = self.conn.execute("INSERT INTO collection_runs(started_at) VALUES (?)", (to_db_time(now),))
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, now: datetime, status: str, *, farms_ok=0, farms_failed=0,
                   alerts_seen=0, alerts_new=0, alerts_resolved=0, error: str | None = None) -> None:
        self.conn.execute(
            """UPDATE collection_runs SET finished_at=?, status=?, farms_ok=?, farms_failed=?,
                   alerts_seen=?, alerts_new=?, alerts_resolved=?, error=? WHERE run_id=?""",
            (to_db_time(now), status, farms_ok, farms_failed, alerts_seen, alerts_new,
             alerts_resolved, (error or "")[:2000] or None, run_id),
        )
        self.conn.commit()

    # ───────── 목장 ─────────
    def upsert_farm(self, farm_id: str, name: str, now: datetime, herd_size: int | None = None) -> None:
        t = to_db_time(now)
        self.conn.execute(
            """INSERT INTO farms(farm_id, name, herd_size, first_seen_at, last_seen_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(farm_id) DO UPDATE SET
                   name=excluded.name, last_seen_at=excluded.last_seen_at,
                   herd_size=COALESCE(excluded.herd_size, farms.herd_size)""",
            (farm_id, name, herd_size, t, t),
        )

    def mark_farm_ok(self, farm_id: str, now: datetime) -> None:
        self.conn.execute("UPDATE farms SET last_ok_at=? WHERE farm_id=?", (to_db_time(now), farm_id))

    # ───────── 알림 ─────────
    def upsert_alert(self, *, run_id: int, now: datetime, farm_id: str, animal_id: str, alert_type: str,
                     occurred_at: datetime, alert_type_raw: str | None = None,
                     resolved_at: datetime | None = None, detail: str | None = None,
                     time_is_relative: bool = False, tolerance_minutes: int = 0) -> UpsertResult:
        """중복 제거 저장.

        1) 키(목장+개체+종류+발생시각) 정확히 일치 → 같은 알림
        2) 없으면, 같은 목장·개체·종류의 '진행중' 알림 중 발생시각이 허용오차 이내 → 같은 알림
           (사이트가 '3시간 전' 같은 상대시각을 쓰거나 분 단위 반올림이 달라지는 경우 대비)
        3) 둘 다 없으면 신규
        """
        t_now, t_occ = to_db_time(now), to_db_time(occurred_at)
        t_res = to_db_time(resolved_at) if resolved_at else None
        row = self.conn.execute(
            "SELECT alert_id, status FROM alerts WHERE farm_id=? AND animal_id=? AND alert_type=? AND occurred_at=?",
            (farm_id, animal_id, alert_type, t_occ),
        ).fetchone()
        if row is None and tolerance_minutes > 0:
            row = self.conn.execute(
                """SELECT alert_id, status FROM alerts
                   WHERE farm_id=? AND animal_id=? AND alert_type=? AND status='active'
                     AND ABS(julianday(occurred_at) - julianday(?)) * 1440 <= ?
                   ORDER BY ABS(julianday(occurred_at) - julianday(?)) LIMIT 1""",
                (farm_id, animal_id, alert_type, t_occ, tolerance_minutes, t_occ),
            ).fetchone()

        if row is None:
            cur = self.conn.execute(
                """INSERT INTO alerts(farm_id, animal_id, alert_type, alert_type_raw, occurred_at,
                       first_seen_at, last_seen_at, resolved_at, resolved_source, status,
                       time_is_relative, detail)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (farm_id, animal_id, alert_type, alert_type_raw, t_occ, t_now, t_now, t_res,
                 "site" if t_res else None, "resolved" if t_res else "active",
                 int(time_is_relative), detail),
            )
            alert_id, action = int(cur.lastrowid), ("resolved_by_site" if t_res else "new")
        else:
            alert_id = int(row["alert_id"])
            if t_res:
                action = "resolved_by_site" if row["status"] == "active" else "seen"
                self.conn.execute(
                    """UPDATE alerts SET last_seen_at=?, resolved_at=?, resolved_source='site',
                           status='resolved', detail=COALESCE(?, detail) WHERE alert_id=?""",
                    (t_now, t_res, detail, alert_id),
                )
            elif row["status"] == "resolved":
                # 해제로 판정했던 알림이 다시 목록에 나타남 → 재활성
                action = "reopened"
                self.conn.execute(
                    """UPDATE alerts SET last_seen_at=?, resolved_at=NULL, resolved_source=NULL,
                           status='active', reopen_count=reopen_count+1, detail=COALESCE(?, detail)
                       WHERE alert_id=?""",
                    (t_now, detail, alert_id),
                )
            else:
                action = "seen"
                self.conn.execute(
                    "UPDATE alerts SET last_seen_at=?, detail=COALESCE(?, detail) WHERE alert_id=?",
                    (t_now, detail, alert_id),
                )

        self.conn.execute("INSERT OR IGNORE INTO alert_observations(run_id, alert_id) VALUES (?,?)",
                          (run_id, alert_id))
        return UpsertResult(alert_id, action)

    def resolve_missing(self, farm_id: str, seen_alert_ids: Iterable[int], now: datetime) -> int:
        """이번 수집에서 목록에 없던 '진행중' 알림 → 해제(inferred) 처리.

        ⚠ 해당 목장 수집이 '완전히 성공'했을 때만 호출할 것 (일부 실패 시 오판).
        해제시각 정밀도 = 수집 간격(예: 2시간). 실제 해제는 last_seen_at ~ now 사이.
        """
        ids = set(seen_alert_ids)
        active = self.conn.execute(
            "SELECT alert_id FROM alerts WHERE farm_id=? AND status='active'", (farm_id,)
        ).fetchall()
        to_close = [r["alert_id"] for r in active if r["alert_id"] not in ids]
        t = to_db_time(now)
        self.conn.executemany(
            "UPDATE alerts SET status='resolved', resolved_at=?, resolved_source='inferred' WHERE alert_id=?",
            [(t, a) for a in to_close],
        )
        return len(to_close)

    def upsert_metric(self, farm_id: str, animal_id: str, metric: str, measured_at: datetime,
                      value: float | None, now: datetime) -> None:
        self.conn.execute(
            """INSERT INTO animal_metrics(farm_id, animal_id, metric, measured_at, value, collected_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT DO UPDATE SET value=excluded.value, collected_at=excluded.collected_at""",
            (farm_id, animal_id, metric, to_db_time(measured_at), value, to_db_time(now)),
        )

    # ───────── 긴급알림 중복방지 ─────────
    def was_notified(self, key: str) -> bool:
        return self.conn.execute("SELECT 1 FROM notifications WHERE notify_key=?", (key,)).fetchone() is not None

    def mark_notified(self, key: str, now: datetime, summary: str) -> None:
        self.conn.execute("INSERT OR IGNORE INTO notifications VALUES (?,?,?)", (key, to_db_time(now), summary))
        self.conn.commit()

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()
