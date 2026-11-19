"""Playwright 기반 수집기 (선택자는 config/selectors.toml 에서 주입).

설계 원칙
- 브라우저 1개, 목장 순차 처리, 페이지 이동 사이 무작위 대기 → 서버 부담 최소화
- 로그인 세션(쿠키)을 data/auth_state.json 에 저장해 매번 로그인하지 않음
- 아이디/비밀번호는 page.fill 에만 전달. 로그·예외 메시지·스크린샷 파일명에 넣지 않음
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, TypeVar

from playwright.sync_api import Browser, BrowserContext, Error as PWError, Page, sync_playwright
from playwright.sync_api import TimeoutError as PWTimeout

from .config import ConfigError, Settings

log = logging.getLogger("smxmon")
T = TypeVar("T")
# inspect 저장 시 토큰·비밀번호 류 값 가리기
_SECRET_JSON = re.compile(
    r'"([A-Za-z_]*(?:token|password|secret|session|cookie|authorization)[A-Za-z_]*)"\s*:\s*"[^"]*"', re.I)


class ScrapeError(RuntimeError):
    pass


class LoginError(ScrapeError):
    pass


@dataclass
class FarmInfo:
    farm_id: str
    name: str


@dataclass
class RawAlert:
    """화면에서 읽은 그대로의 값 (정규화 전)."""
    farm_id: str
    animal_id: str
    alert_type_raw: str
    occurred_at_raw: str
    resolved_at_raw: str = ""
    detail: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def _is_todo(v: Any) -> bool:
    return not v or (isinstance(v, str) and v.strip().upper().startswith("TODO"))


def require(sel: dict[str, Any], section: str, *keys: str) -> None:
    missing = [k for k in keys if _is_todo(sel.get(section, {}).get(k))]
    if missing:
        raise ConfigError(
            f"config/selectors.toml [{section}] 의 {', '.join(missing)} 값을 채워주세요 "
            f"(화면 HTML 확인 후 입력. `python -m smxmon inspect` 로 HTML 저장 가능)"
        )


def with_retry(fn: Callable[[], T], *, what: str, attempts: int, base_delay: float = 5.0,
               retry_on: tuple[type[BaseException], ...] = (PWError, PWTimeout, ScrapeError)) -> T:
    """지수 백오프 재시도: 5s → 10s → 20s ... (설정오류·로그인 실패는 재시도하지 않음)."""
    last: BaseException | None = None
    for i in range(1, attempts + 1):
        try:
            return fn()
        except (ConfigError, LoginError):
            raise
        except retry_on as e:  # noqa: PERF203
            last = e
            if i == attempts:
                break
            wait = base_delay * (2 ** (i - 1)) + random.uniform(0, 2)
            log.warning("%s 실패(%d/%d): %s → %.0f초 후 재시도", what, i, attempts, _short(e), wait)
            time.sleep(wait)
    raise ScrapeError(f"{what} 최종 실패: {_short(last)}") from last


def _short(e: BaseException | None) -> str:
    s = str(e).strip().splitlines()[0] if e and str(e).strip() else type(e).__name__
    return s[:300]


class SmaxtecScraper:
    def __init__(self, settings: Settings, selectors: dict[str, Any], *, debug_dir: Path | None = None):
        self.s = settings
        self.sel = selectors
        self.debug_dir = debug_dir or settings.log_dir / "debug"
        self._pw = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    # ───────── 수명주기 ─────────
    def __enter__(self) -> "SmaxtecScraper":
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(headless=self.s.headless)
        state = self.s.auth_state_path if self.s.auth_state_path.exists() else None
        self.context = self.browser.new_context(
            storage_state=str(state) if state else None,
            locale="ko-KR",
            timezone_id=self.s.timezone,
            viewport={"width": 1440, "height": 900},
        )
        self.context.set_default_timeout(self.s.page_timeout_ms)
        self.page = self.context.new_page()
        return self

    def __exit__(self, *exc) -> None:
        for closer in (self.context, self.browser):
            try:
                if closer:
                    closer.close()
            except Exception:  # noqa: BLE001
                pass
        if self._pw:
            self._pw.stop()

    def polite_wait(self) -> None:
        time.sleep(random.uniform(self.s.min_delay, self.s.max_delay))

    def save_debug(self, tag: str) -> None:
        """실패 시 화면 캡처/HTML 저장 (logs/debug, git 제외). 목장 데이터 포함 → 외부 공유 금지."""
        if not self.page:
            return
        try:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe = re.sub(r"[^\w.-]", "_", tag)[:60]
            self.page.screenshot(path=str(self.debug_dir / f"{stamp}_{safe}.png"), full_page=True)
            (self.debug_dir / f"{stamp}_{safe}.html").write_text(self.page.content(), encoding="utf-8")
            log.info("디버그 캡처 저장: %s", self.debug_dir / f"{stamp}_{safe}.*")
        except Exception as e:  # noqa: BLE001
            log.debug("디버그 캡처 실패: %s", _short(e))

    # ───────── 로그인 ─────────
    def _is_logged_in(self, timeout_ms: int = 5000) -> bool:
        ind = self.sel["login"]["logged_in_indicator"]
        try:
            self.page.wait_for_selector(ind, state="visible", timeout=timeout_ms)
            return True
        except PWTimeout:
            return False

    def ensure_logged_in(self) -> None:
        require(self.sel, "login", "username_input", "password_input", "submit_button", "logged_in_indicator")
        start_url = self.sel["farm_list"].get("url")
        target = self.s.login_url if _is_todo(start_url) else start_url
        self.page.goto(target, wait_until="domcontentloaded")
        if self._is_logged_in():
            log.info("저장된 세션으로 로그인 상태 확인")
            return
        self.login()

    def login(self) -> None:
        lg = self.sel["login"]
        log.info("로그인 시도")
        self.page.goto(self.s.login_url, wait_until="domcontentloaded")
        self.page.fill(lg["username_input"], self.s.username)
        self.page.fill(lg["password_input"], self.s.password)
        self.page.click(lg["submit_button"])
        if not self._is_logged_in(timeout_ms=self.s.page_timeout_ms):
            msg = ""
            if lg.get("error_message"):
                loc = self.page.locator(lg["error_message"])
                if loc.count():
                    msg = loc.first.inner_text().strip()[:200]
            self.save_debug("login_failed")
            # 계정 잠금 방지: 로그인 실패는 재시도하지 않음
            raise LoginError(f"로그인 실패(아이디/비밀번호 또는 선택자 확인 필요). 화면 메시지: {msg or '없음'}")
        self.s.auth_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.context.storage_state(path=str(self.s.auth_state_path))
        log.info("로그인 성공 → 세션 저장")

    # ───────── 목장 목록 ─────────
    def list_farms(self) -> list[FarmInfo]:
        fl = self.sel["farm_list"]
        require(self.sel, "farm_list", "row")
        if not _is_todo(fl.get("url")) and self.page.url.rstrip("/") != fl["url"].rstrip("/"):
            self.page.goto(fl["url"], wait_until="domcontentloaded")
        self.page.wait_for_selector(fl["row"], timeout=self.s.page_timeout_ms)

        farms: dict[str, FarmInfo] = {}
        for page_no in range(1, int(fl.get("max_pages", 1)) + 1):
            rows = self.page.locator(fl["row"])
            for i in range(rows.count()):
                row = rows.nth(i)
                name = self._text(row, fl.get("name")) or row.inner_text().strip().splitlines()[0]
                fid = self._extract_id(row, fl)
                if not fid:
                    log.warning("목장 ID 추출 실패(행 %d, 이름=%s) → id_element/id_attribute/id_regex 확인", i, name)
                    continue
                farms.setdefault(fid, FarmInfo(fid, name.strip()))
            if not self._go_next(fl.get("next_page"), fl["row"]):
                break
            log.debug("목장 목록 %d페이지 이동", page_no + 1)
        log.info("목장 %d곳 확인", len(farms))
        return list(farms.values())

    def _extract_id(self, row, fl: dict[str, Any]) -> str | None:
        el = row.locator(fl["id_element"]).first if fl.get("id_element") else row
        try:
            raw = el.get_attribute(fl.get("id_attribute") or "href") if el.count() else None
        except PWError:
            raw = None
        if not raw:
            return None
        rx = fl.get("id_regex")
        if rx:
            m = re.search(rx, raw)
            return m.group(1) if m else None
        return raw.strip()

    # ───────── 목장별 알림 목록 ─────────
    def list_alerts(self, farm: FarmInfo) -> list[RawAlert]:
        al = self.sel["alert_list"]
        require(self.sel, "alert_list", "url_template", "row", "animal_id", "alert_type", "occurred_at")
        self.polite_wait()
        self.page.goto(al["url_template"].format(farm_id=farm.farm_id), wait_until="domcontentloaded")

        if not self._wait_rows_or_empty(al):
            # 세션 만료로 로그인 화면으로 튕긴 경우 1회 재로그인
            if not self._is_logged_in(timeout_ms=2000):
                log.info("세션 만료 감지 → 재로그인")
                self.login()
                self.page.goto(al["url_template"].format(farm_id=farm.farm_id), wait_until="domcontentloaded")
                if self._wait_rows_or_empty(al):
                    return self._read_alert_rows(farm, al)
            self.save_debug(f"alerts_timeout_{farm.farm_id}")
            raise ScrapeError(f"알림 목록 로딩 실패(목장 {farm.farm_id})")
        return self._read_alert_rows(farm, al)

    def _wait_rows_or_empty(self, al: dict[str, Any]) -> bool:
        target = al["row"] + (f", {al['empty_indicator']}" if al.get("empty_indicator") else "")
        try:
            self.page.wait_for_selector(target, timeout=int(al.get("wait_timeout_ms", 15000)))
            return True
        except PWTimeout:
            return False

    def _read_alert_rows(self, farm: FarmInfo, al: dict[str, Any]) -> list[RawAlert]:
        out: list[RawAlert] = []
        for page_no in range(1, int(al.get("max_pages", 1)) + 1):
            rows = self.page.locator(al["row"])
            for i in range(rows.count()):
                row = rows.nth(i)
                rec = RawAlert(
                    farm_id=farm.farm_id,
                    animal_id=self._text(row, al["animal_id"]),
                    alert_type_raw=self._text(row, al["alert_type"]),
                    occurred_at_raw=self._text(row, al["occurred_at"]),
                    resolved_at_raw=self._text(row, al.get("resolved_at")),
                    detail=self._text(row, al.get("detail")),
                )
                if not (rec.animal_id and rec.alert_type_raw and rec.occurred_at_raw):
                    log.warning("목장 %s 알림 행 %d: 필수값 누락(개체/종류/시각) → 건너뜀", farm.farm_id, i)
                    continue
                out.append(rec)
            if not self._go_next(al.get("next_page"), al["row"]):
                break
        return out

    # ───────── 공통 ─────────
    @staticmethod
    def _text(row, selector: str | None) -> str:
        if _is_todo(selector):
            return ""
        loc = row.locator(selector)
        return " ".join(loc.first.inner_text().split()) if loc.count() else ""

    def _go_next(self, next_sel: str | None, row_sel: str) -> bool:
        if _is_todo(next_sel):
            return False
        btn = self.page.locator(next_sel)
        if not btn.count() or not btn.first.is_enabled():
            return False
        first_before = self.page.locator(row_sel).first.inner_text() if self.page.locator(row_sel).count() else ""
        self.polite_wait()
        btn.first.click()
        try:  # 목록 내용이 바뀔 때까지 대기
            self.page.wait_for_function(
                "([s, t]) => { const e = document.querySelector(s); return !e || e.innerText !== t; }",
                arg=[row_sel, first_before], timeout=self.s.page_timeout_ms,
            )
        except PWTimeout:
            return False
        return True

    # ───────── 화면 구조 조사 (선택자 채우기용) ─────────
    def inspect(self, urls: list[str], out_dir: Path) -> list[Path]:
        """페이지 HTML·스크린샷 + 페이지가 호출한 JSON API 응답을 저장.

        smaXtec 웹은 SPA 일 가능성이 높아, 화면(DOM)보다 내부 JSON API 를 읽는 편이
        훨씬 안정적일 수 있음 → api_calls.json 을 함께 확인.
        """
        out_dir.mkdir(parents=True, exist_ok=True)
        api_log: list[dict[str, Any]] = []

        def on_response(resp):
            try:
                ctype = resp.headers.get("content-type", "")
                if "json" in ctype and resp.request.resource_type in ("xhr", "fetch"):
                    body = _SECRET_JSON.sub(r'"\1": "***"', resp.text())
                    api_log.append({"url": resp.url, "status": resp.status, "method": resp.request.method,
                                    "bytes": len(body), "sample": body[:3000]})
            except Exception:  # noqa: BLE001
                pass

        self.page.on("response", on_response)
        saved: list[Path] = []
        for idx, url in enumerate(urls, 1):
            self.polite_wait()
            self.page.goto(url, wait_until="networkidle")
            base = out_dir / f"page{idx:02d}"
            self.page.screenshot(path=str(base) + ".png", full_page=True)
            Path(str(base) + ".html").write_text(self.page.content(), encoding="utf-8")
            saved += [Path(str(base) + ".png"), Path(str(base) + ".html")]
            log.info("저장: %s (%s)", base, url)
        api_path = out_dir / "api_calls.json"
        api_path.write_text(json.dumps(api_log, ensure_ascii=False, indent=2), encoding="utf-8")
        saved.append(api_path)
        return saved
