"""사용자별 수집·리포트 생성 (서버 타이머 또는 웹에서 '지금 새로고침')."""
from __future__ import annotations

import dataclasses
import logging
import threading
import time
from queue import Queue

from ..config import Settings
from ..logging_setup import setup_logging
from .store import CloudStore

log = logging.getLogger("smxmon")
_run_lock = threading.Lock()   # 같은 프로세스 안에서는 한 번에 한 사용자씩


def user_settings(base: Settings, store: CloudStore, uid: str) -> Settings | None:
    user, pw = store.get_user(uid), store.password(uid)
    if not user or pw is None:
        return None
    d = store.user_dir(uid)
    return dataclasses.replace(
        base, username=user.username, password=pw, api_token="", mode="api",
        db_path=d / "data" / "smaxtec.db", report_dir=d / "reports", log_dir=d / "logs",
        publish_dir=None, trials_path=d / "투입기록.xlsx", smtp_host="", mail_to="")


def run_user(base: Settings, store: CloudStore, uid: str) -> str:
    """수집 → 리포트·앱 생성. 반환: 상태(ok/failed/login_failed/skipped)."""
    from ..collector import collect_once, now_local
    from ..outputs import generate
    s = user_settings(base, store, uid)
    if s is None:
        return "skipped"
    with _run_lock:
        setup_logging(s.log_dir, s.secrets())
        store.set_status(uid, "running", "수집 중")
        try:
            st = collect_once(s)
        except RuntimeError as e:   # 다른 수집이 실행 중(잠금)
            store.set_status(uid, "failed", str(e)[:300])
            return "failed"
        errs = " ".join(st.get("errors") or [])
        if st["status"] == "failed":
            if "로그인 실패" in errs or "권한 오류" in errs:
                store.set_status(uid, "login_failed", "smaXtec 로그인 실패 — 앱에서 다시 로그인해 주세요")
                return "login_failed"
            store.set_status(uid, "failed", errs[:300] or "수집 실패")
            return "failed"
        try:
            o = generate(s, now_local(s.timezone), overrides={})
        except Exception as e:  # noqa: BLE001
            log.exception("리포트 생성 실패")
            store.set_status(uid, "failed", f"리포트 생성 실패: {type(e).__name__}")
            return "failed"
        store.set_status(uid, "ok", None if st["status"] == "ok" else f"일부 목장 실패 {st['farms_failed']}곳",
                         farms=st["farms_ok"] + st["farms_failed"], ok=True)
        log.info("사용자 %s 완료: 목장 %d, 앱 %s", uid, st["farms_ok"], o.dashboard)
        return "ok"


def run_all(base: Settings, store: CloudStore, pause_sec: float = 5.0) -> dict[str, int]:
    """모든 사용자 순차 실행 (로그인 실패 상태는 다시 로그인할 때까지 건너뜀)."""
    out: dict[str, int] = {}
    for u in store.users():
        if u.status == "login_failed":
            out["login_failed"] = out.get("login_failed", 0) + 1
            continue
        r = run_user(base, store, u.uid)
        out[r] = out.get(r, 0) + 1
        time.sleep(pause_sec)
    return out


class Runner:
    """웹 프로세스 안 백그라운드 작업 1줄 대기열 (첫 로그인·지금 새로고침·투입기록 업로드)."""

    def __init__(self, base: Settings, store: CloudStore):
        self.base, self.store = base, store
        self.q: Queue[str] = Queue()
        self.pending: set[str] = set()
        self._lk = threading.Lock()
        threading.Thread(target=self._loop, daemon=True, name="smx-runner").start()

    def submit(self, uid: str) -> bool:
        with self._lk:
            if uid in self.pending:
                return False
            self.pending.add(uid)
        self.store.set_status(uid, "running", "대기 중")
        self.q.put(uid)
        return True

    def _loop(self) -> None:
        while True:
            uid = self.q.get()
            try:
                run_user(self.base, self.store, uid)
            except Exception:  # noqa: BLE001
                log.exception("백그라운드 작업 오류")
                self.store.set_status(uid, "failed", "서버 오류")
            finally:
                with self._lk:
                    self.pending.discard(uid)
