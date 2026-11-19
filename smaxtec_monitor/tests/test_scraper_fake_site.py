"""가짜 로컬 사이트로 로그인 → 목장목록 → 알림수집 → DB 저장 전체 흐름 검증."""
import dataclasses
import functools
import http.server
import threading
from pathlib import Path

import pytest

from smxmon import collector
from smxmon.config import load_settings
from smxmon.db import Database

SITE = Path(__file__).parent / "fake_site"


@pytest.fixture(scope="module")
def server():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SITE))
    handler.log_message = lambda *a, **k: None
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _selectors(base):
    return {
        "login": {"username_input": "input[name=email]", "password_input": "input[name=pw]",
                  "submit_button": "button[type=submit]", "logged_in_indicator": "#logout",
                  "error_message": ".err"},
        "farm_list": {"url": f"{base}/farms.html", "row": "li.farm", "name": ".nm", "id_element": "a",
                      "id_attribute": "href", "id_regex": r"/farm/(\d+)/", "next_page": "", "max_pages": 1},
        "alert_list": {"url_template": base + "/farm/{farm_id}/alerts.html", "row": "tr.al",
                       "animal_id": ".cow", "alert_type": ".type", "occurred_at": ".t", "resolved_at": "",
                       "detail": "", "empty_indicator": ".empty", "next_page": "", "max_pages": 1,
                       "wait_timeout_ms": 5000},
        "datetime": {"formats": ["%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M"], "dedup_tolerance_minutes": 30},
    }


def test_full_collect(server, tmp_path, monkeypatch):
    s = dataclasses.replace(load_settings(env_file=tmp_path / "none.env"),
                            username="tester@example.com", password="pw-1234", mode="web",
                            login_url=f"{server}/login.html", min_delay=0, max_delay=0, max_retries=1,
                            page_timeout_ms=8000, db_path=tmp_path / "t.db", log_dir=tmp_path / "logs")
    real = collector.load_toml
    monkeypatch.setattr(collector, "load_toml",
                        lambda n, required=True: _selectors(server) if n == "selectors.toml" else real(n, required))
    monkeypatch.setattr(collector, "load_farm_overrides", lambda: {})

    st = collector.collect_once(s)
    assert st["status"] == "ok", st["errors"]
    assert (st["farms_ok"], st["alerts_new"]) == (2, 3)
    assert s.auth_state_path.exists()  # 세션 저장 → 다음 수집 시 재로그인 생략

    st2 = collector.collect_once(s)  # 재수집 → 중복 없음
    assert st2["alerts_new"] == 0
    with Database(s.db_path) as db:
        types = sorted(r[0] for r in db.conn.execute("SELECT alert_type FROM alerts"))
        assert types == ["HEAT", "PH_LOW", "RUMINATION_DROP"]


def test_wrong_password(server, tmp_path, monkeypatch):
    s = dataclasses.replace(load_settings(env_file=tmp_path / "none.env"), username="tester@example.com",
                            password="wrong", mode="web", login_url=f"{server}/login.html", min_delay=0, max_delay=0,
                            max_retries=3, page_timeout_ms=3000, db_path=tmp_path / "t.db",
                            log_dir=tmp_path / "logs")
    monkeypatch.setattr(collector, "load_toml",
                        lambda n, required=True: _selectors(server) if n == "selectors.toml"
                        else __import__("smxmon.config", fromlist=["x"]).load_toml(n, required))
    st = collector.collect_once(s)
    assert st["status"] == "failed" and "로그인 실패" in st["errors"][0]
    assert "wrong" not in st["errors"][0]
