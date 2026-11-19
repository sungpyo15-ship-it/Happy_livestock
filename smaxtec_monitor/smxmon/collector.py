"""1회 수집 실행: 로그인 → 목장 목록 → 목장별 알림 → DB 누적 → (선택) 긴급알림."""
from __future__ import annotations

import logging
import os
import random
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .api_client import ApiError, SmaxtecAPI
from .config import ConfigError, Settings, load_farm_overrides, load_toml
from .db import Database
from .normalize import AlertTypeMapper, DateParseError, parse_datetime
from .scraper import LoginError, ScrapeError, SmaxtecScraper, with_retry

log = logging.getLogger("smxmon")


def now_local(tz: str) -> datetime:
    return datetime.now(ZoneInfo(tz)).replace(tzinfo=None, microsecond=0)


@contextmanager
def run_lock(lock_path: Path, stale_seconds: int = 3 * 3600):
    """스케줄러가 겹쳐 실행돼도 동시에 2개가 돌지 않게 하는 잠금 파일."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.exists() and time.time() - lock_path.stat().st_mtime > stale_seconds:
        log.warning("오래된 잠금 파일 제거: %s", lock_path)
        lock_path.unlink(missing_ok=True)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError("다른 수집이 실행 중입니다(잠금 파일 존재). 잠시 후 다시 실행하세요.") from None
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def _collect_api(settings: Settings, db: Database, run_id: int, mapper: AlertTypeMapper,
                 overrides: dict, farm_filter: list[str] | None, stats: dict, errors: list[str]) -> None:
    """공식 API 수집: 목장목록 → (개체목록) → 이벤트. 이벤트는 시점 기록이므로 해제 판정 없음."""
    api = SmaxtecAPI(settings)
    farms = api.list_farms()
    if farm_filter:
        farms = [f for f in farms if f.farm_id in farm_filter or f.name in farm_filter]
    unknown: dict[str, int] = {}
    for farm in farms:
        ov = overrides.get(farm.farm_id, {})
        if ov.get("exclude"):
            continue
        now = now_local(settings.timezone)
        try:
            time.sleep(random.uniform(settings.min_delay, settings.max_delay) / 3)  # API 는 가볍게 간격만
            db.upsert_farm(farm.farm_id, farm.name, now, ov.get("herd_size"))
            try:
                n_sensor = db.upsert_animals(farm.farm_id, api.list_animals(farm.farm_id), now)
                if not ov.get("herd_size") and n_sensor:
                    db.conn.execute("UPDATE farms SET herd_size=? WHERE farm_id=?", (n_sensor, farm.farm_id))
            except ApiError as e:  # 개체목록은 부가정보 → 실패해도 알림 수집은 진행
                log.warning("목장 %s 개체목록 실패: %s", farm.name, e)
            back = (timedelta(days=settings.api_backfill_days) if not db.farm_has_alerts(farm.farm_id)
                    else timedelta(hours=settings.api_lookback_hours))
            events = api.list_events(farm.farm_id, now - back)
        except (ApiError, LoginError) as e:
            if isinstance(e, LoginError) and stats["farms_ok"] == 0:
                raise
            stats["farms_failed"] += 1
            errors.append(f"{farm.farm_id}: {e}")
            log.error("목장 %s(%s) 수집 실패 → 다음 목장. %s", farm.name, farm.farm_id, e)
            continue
        for ev in events:
            code = mapper.map_event(ev.event_type)
            if code is None:
                code = "OTHER"
                unknown[ev.event_type] = unknown.get(ev.event_type, 0) + 1
            r = db.upsert_alert(run_id=run_id, now=now, farm_id=farm.farm_id, animal_id=ev.animal_ref,
                                alert_type=code, alert_type_raw=ev.event_type, occurred_at=ev.event_ts,
                                detail=ev.detail or None, ext_id=ev.ext_id, point_event=True, value=ev.value)
            stats["alerts_seen"] += 1
            stats["alerts_new"] += r.action == "new"
        db.mark_farm_ok(farm.farm_id, now)
        db.commit()
        stats["farms_ok"] += 1
        log.info("목장 %s: 이벤트 %d건 (조회범위 %s)", farm.name, len(events), back)
    if unknown:
        log.info("표에 없는 이벤트 코드 → config/alert_types.toml [event_codes] 추가 검토: %s", unknown)
    log.info("API 호출 %d회", api.calls)


def collect_once(settings: Settings, *, farm_filter: list[str] | None = None) -> dict:
    settings.require_credentials()
    selectors = load_toml("selectors.toml")
    mapper = AlertTypeMapper(load_toml("alert_types.toml"))
    overrides = load_farm_overrides()
    dt_cfg = selectors.get("datetime", {})
    formats = dt_cfg.get("formats", [])
    tol_cfg = int(dt_cfg.get("dedup_tolerance_minutes", 30))

    stats = dict(farms_ok=0, farms_failed=0, alerts_seen=0, alerts_new=0, alerts_resolved=0)
    errors: list[str] = []

    with run_lock(settings.data_dir / "collect.lock"), Database(settings.db_path) as db:
        run_id = db.start_run(now_local(settings.timezone))
        log.info("수집 시작 (run_id=%d)", run_id)
        status = "failed"
        try:
            if settings.mode == "api":
                _collect_api(settings, db, run_id, mapper, overrides, farm_filter, stats, errors)
            else:
                with SmaxtecScraper(settings, selectors) as sc:
                    with_retry(sc.ensure_logged_in, what="로그인", attempts=settings.max_retries)
                    farms = with_retry(sc.list_farms, what="목장 목록", attempts=settings.max_retries)
                    if farm_filter:
                        farms = [f for f in farms if f.farm_id in farm_filter or f.name in farm_filter]

                    for farm in farms:
                        ov = overrides.get(farm.farm_id, {})
                        if ov.get("exclude"):
                            continue
                        try:
                            raws = with_retry(lambda f=farm: sc.list_alerts(f), what=f"알림 수집[{farm.name}]",
                                              attempts=settings.max_retries)
                        except ScrapeError as e:
                            stats["farms_failed"] += 1
                            errors.append(f"{farm.farm_id}: {e}")
                            log.error("목장 %s(%s) 수집 실패 → 다음 목장 진행. %s", farm.name, farm.farm_id, e)
                            continue

                        now = now_local(settings.timezone)
                        db.upsert_farm(farm.farm_id, farm.name, now, ov.get("herd_size"))
                        seen_ids, parse_fail = [], 0
                        for r in raws:
                            try:
                                occ, rel = parse_datetime(r.occurred_at_raw, formats, now)
                                res = parse_datetime(r.resolved_at_raw, formats, now)[0] if r.resolved_at_raw else None
                            except DateParseError as e:
                                parse_fail += 1
                                log.warning("목장 %s 개체 %s: %s", farm.farm_id, r.animal_id, e)
                                continue
                            result = db.upsert_alert(
                                run_id=run_id, now=now, farm_id=farm.farm_id, animal_id=r.animal_id,
                                alert_type=mapper.map(r.alert_type_raw), alert_type_raw=r.alert_type_raw,
                                occurred_at=occ, resolved_at=res, detail=r.detail or None,
                                time_is_relative=rel, tolerance_minutes=max(tol_cfg, 90 if rel else 0),
                            )
                            seen_ids.append(result.alert_id)
                            stats["alerts_seen"] += 1
                            stats["alerts_new"] += result.action in ("new", "resolved_by_site")
                            if mapper.map(r.alert_type_raw) == "OTHER":
                                log.info("미분류 알림명 '%s' → config/alert_types.toml 키워드 추가 검토", r.alert_type_raw)

                        # 일시 해석 실패가 있으면 '사라짐=해제' 판정을 하지 않음(오판 방지)
                        if parse_fail == 0:
                            stats["alerts_resolved"] += db.resolve_missing(farm.farm_id, seen_ids, now)
                            db.mark_farm_ok(farm.farm_id, now)
                            stats["farms_ok"] += 1
                        else:
                            stats["farms_failed"] += 1
                            errors.append(f"{farm.farm_id}: 일시 해석 실패 {parse_fail}건")
                        db.commit()
                        log.info("목장 %s: 알림 %d건 확인", farm.name, len(raws))

            status = "ok" if stats["farms_failed"] == 0 else ("partial" if stats["farms_ok"] else "failed")
        except (ConfigError, LoginError, ScrapeError) as e:
            db.rollback()
            status = "failed"
            errors.append(str(e))
            log.error("수집 중단: %s", e)
        except Exception as e:  # 예기치 못한 오류도 실행 기록은 남김
            db.rollback()
            status = "failed"
            errors.append(f"{type(e).__name__}: {e}")
            log.exception("예기치 못한 오류")
        finally:
            db.finish_run(run_id, now_local(settings.timezone), status,
                          error="; ".join(errors) or None, **stats)

        log.info("수집 종료 [%s] 목장 성공 %d / 실패 %d, 알림 확인 %d (신규 %d, 해제 %d)",
                 status, stats["farms_ok"], stats["farms_failed"], stats["alerts_seen"],
                 stats["alerts_new"], stats["alerts_resolved"])
        stats.update(status=status, run_id=run_id, errors=errors)
        return stats
