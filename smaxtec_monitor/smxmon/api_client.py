"""smaXtec 공식 Integration API 클라이언트 (https://api.smaxtec.com/integration/v2).

문서: https://api.smaxtec.com/documentation/integration/
- 인증: (권장) smaXtec 이 발급한 API 토큰 → SMX_API_TOKEN
        (대안) 아이디/비번으로 POST /users/session_token → 24시간 유효 토큰
- 목장 목록: GET /organisations
- 알림(이벤트): GET /organisations/{id}/events  (health_104 체온상승, feeding_202 산독증위험 등)
- 개체 목록: GET /organisations/{id}/animals   (관리번호·비유상태·센서 장착 여부)

표준 라이브러리(urllib)만 사용. 시스템 프록시 환경변수도 그대로 따름.
"""
from __future__ import annotations

import json
import logging
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .config import Settings
from .scraper import FarmInfo, LoginError, ScrapeError

log = logging.getLogger("smxmon")
USER_AGENT = "smxmon/0.2 (farm-consulting-monitor)"


class ApiError(ScrapeError):
    pass


@dataclass
class ApiEvent:
    ext_id: str
    animal_ref: str          # smaXtec 내부 animal_id (없으면 official_id)
    official_id: str
    event_type: str          # 예: health_104
    event_ts: datetime       # 현지시간(naive)
    value: float | None
    detail: str


def to_local_naive(ts: str, tz: str) -> datetime:
    """'2026-11-19 01:50:00+09:00' → 설정 시간대 naive datetime.
    시간대 표기가 없으면 목장 현지시간으로 간주(tz_mode 없이 조회 시 API 가 현지시간을 돌려줌, 2026-11 실측)."""
    s = ts.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        return dt.replace(microsecond=0)
    return dt.astimezone(ZoneInfo(tz)).replace(tzinfo=None, microsecond=0)


class SmaxtecAPI:
    def __init__(self, settings: Settings, base_url: str | None = None):
        self.s = settings
        self.base = (base_url or settings.api_base).rstrip("/")
        self._token: str | None = settings.api_token or None
        self._token_exp = float("inf") if settings.api_token else 0.0
        self.calls = 0

    # ───────── HTTP ─────────
    def _request(self, method: str, path: str, params: dict[str, Any] | None = None,
                 body: dict[str, Any] | None = None, auth: bool = True, attempts: int | None = None) -> Any:
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        headers = {"accept": "application/json", "User-Agent": USER_AGENT}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        attempts = attempts or self.s.max_retries
        for i in range(1, attempts + 1):
            if auth:
                headers["Authorization"] = "Bearer " + self._get_token()
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                self.calls += 1
                with urllib.request.urlopen(req, timeout=self.s.page_timeout_ms / 1000) as r:
                    raw = r.read()
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as e:
                if e.code == 401 and auth and not self.s.api_token and i < attempts:
                    log.info("토큰 만료 → 재발급")
                    self._token, self._token_exp = None, 0
                    continue
                if e.code in (401, 403):
                    raise LoginError(f"API 권한 오류 HTTP {e.code} ({path.split('?')[0]})") from None
                if e.code in (429, 500, 502, 503, 504) and i < attempts:
                    wait = 5 * 2 ** (i - 1) + random.uniform(0, 2)
                    log.warning("API HTTP %d (%s) → %.0f초 후 재시도", e.code, path, wait)
                    time.sleep(wait)
                    continue
                raise ApiError(f"API HTTP {e.code} ({path})") from None
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                if i < attempts:
                    time.sleep(5 * 2 ** (i - 1))
                    continue
                raise ApiError(f"API 연결 실패 ({path}): {type(e).__name__}") from None
        raise ApiError(f"API 재시도 초과 ({path})")

    # ───────── 인증 ─────────
    def _get_token(self) -> str:
        if self._token and time.time() < self._token_exp - 600:
            return self._token
        if not (self.s.username and self.s.password):
            raise LoginError(".env 에 SMX_API_TOKEN 또는 SMX_USERNAME/SMX_PASSWORD 가 필요합니다.")
        cred = {"user": self.s.username, "password": self.s.password, "integration_name": "smxmon"}
        try:  # 문서 예시(파이썬)는 JSON 본문
            res = self._request("POST", "/users/session_token", body=cred, auth=False, attempts=1)
        except (LoginError, ApiError) as first:
            # Swagger 정의는 쿼리 파라미터 → 대체 시도 (URL 은 로그에 남기지 않음)
            try:
                res = self._request("POST", "/users/session_token", params=cred, auth=False, attempts=1)
            except (LoginError, ApiError):
                raise LoginError(f"API 로그인 실패: {first}. 계정이 API 접근 권한이 없거나 "
                                 "SSO 전용 계정일 수 있음 → 리얼팜/smaXtec 에 API 토큰 발급 문의") from None
        if not isinstance(res, dict) or not res.get("token"):
            raise LoginError("API 로그인 응답에 토큰이 없음")
        self._token = res["token"]
        self._token_exp = time.time() + float(res.get("expires_in") or 20 * 3600)
        log.info("API 로그인 성공")
        return self._token

    # ───────── 조회 ─────────
    def list_farms(self) -> list[FarmInfo]:
        res = self._request("GET", "/organisations") or []
        farms = [FarmInfo(str(o["organisation_id"]), o.get("name") or str(o["organisation_id"])) for o in res]
        self.org_meta = {str(o["organisation_id"]): o for o in res}
        log.info("API: 접근 가능한 목장 %d곳", len(farms))
        return farms

    def list_animals(self, org_id: str) -> list[dict[str, Any]]:
        out, offset = [], 0
        while True:
            page = self._request("GET", f"/organisations/{org_id}/animals",
                                 params={"offset": offset, "limit": 1000, "include_archived": "false"}) or []
            out += page
            if len(page) < 1000:
                return out
            offset += 1000

    def list_events(self, org_id: str, since: datetime) -> list[ApiEvent]:
        """since(현지시간) 이후 발생 이벤트. 결과는 현지시간으로 변환."""
        since_utc = since.replace(tzinfo=ZoneInfo(self.s.timezone)).astimezone(timezone.utc)
        res = self._request("GET", f"/organisations/{org_id}/events",
                            params={"min_event_ts": since_utc.strftime("%Y-%m-%dT%H:%M:%S"), "tz_mode": "true"}) or []
        events: list[ApiEvent] = []
        for e in res:
            try:
                ts = to_local_naive(e["event_ts"], self.s.timezone)
            except (KeyError, ValueError):
                log.debug("이벤트 시각 해석 불가: %s", e.get("_id"))
                continue
            extra = {k: e[k] for k in ("reason", "days_to_calving", "cycle_length", "diagnosis_key")
                     if e.get(k) not in (None, "")}
            events.append(ApiEvent(
                ext_id=str(e.get("_id") or f"{e.get('animal_id')}:{e.get('event_type')}:{e['event_ts']}"),
                animal_ref=str(e.get("animal_id") or e.get("official_id") or "GROUP"),
                official_id=str(e.get("official_id") or ""),
                event_type=str(e.get("event_type") or "unknown"),
                event_ts=ts,
                value=e.get("value") if isinstance(e.get("value"), (int, float)) else None,
                detail=json.dumps(extra, ensure_ascii=False) if extra else "",
            ))
        return events
