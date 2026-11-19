"""가짜 smaXtec API 서버로 api 모드 수집 전체 흐름 검증."""
import dataclasses
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from smxmon import collector
from smxmon.config import load_settings
from smxmon.db import Database

NOW_UTC = datetime.now(timezone.utc).replace(microsecond=0)


def _iso(h):
    return (NOW_UTC - timedelta(hours=h)).isoformat()


EVENTS = [
    {"_id": "e1", "animal_id": "a1", "official_id": "KR002111111111", "event_ts": _iso(3), "event_type": "health_104", "value": 40.1},
    {"_id": "e2", "animal_id": "a2", "event_ts": _iso(5), "event_type": "feeding_202"},
    {"_id": "e3", "animal_id": "a2", "event_ts": _iso(5), "event_type": "health_305"},
    {"_id": "e4", "animal_id": "a3", "event_ts": _iso(30), "event_type": "heat", "cycle_length": 21},
    {"_id": "e5", "animal_id": "a3", "event_ts": _iso(2), "event_type": "brand_new_999"},
]
ANIMALS = [
    {"_id": "a1", "official_id": "KR002111111111", "display_name": "101", "current_device_id": "d1", "lactation_status": "fresh"},
    {"_id": "a2", "official_id": "KR002222222222", "mark": "102", "current_device_id": "d2"},
    {"_id": "a3", "official_id": "KR002333333333", "name": "103", "current_device_id": None},
]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path.startswith("/users/session_token"):
            ok = body.get("user") == "me@x.kr" and body.get("password") == "pw"
            return self._send(200, {"token": "TKN", "expires_in": 86400}) if ok else self._send(401, {"message": "bad"})
        self._send(404, {})

    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer TKN":
            return self._send(401, {})
        u = urlparse(self.path)
        if u.path == "/organisations":
            return self._send(200, [{"organisation_id": "org1", "name": "가나목장", "role": "viewer", "timezone": "Asia/Seoul"}])
        if u.path == "/organisations/org1/animals":
            return self._send(200, ANIMALS)
        if u.path == "/organisations/org1/events":
            assert "min_event_ts" in parse_qs(u.query)
            return self._send(200, EVENTS)
        self._send(404, {})


@pytest.fixture(scope="module")
def api():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _settings(tmp_path, api, **kw):
    base = dict(username="me@x.kr", password="pw", mode="api", api_base=api, api_token="",
                min_delay=0, max_delay=0, max_retries=2, db_path=tmp_path / "t.db", log_dir=tmp_path / "l")
    base.update(kw)
    return dataclasses.replace(load_settings(env_file=tmp_path / "none.env"), **base)


def test_api_collect(api, tmp_path, monkeypatch):
    monkeypatch.setattr(collector, "load_farm_overrides", lambda: {})
    s = _settings(tmp_path, api)
    st = collector.collect_once(s)
    assert st["status"] == "ok", st["errors"]
    assert st["alerts_new"] == 5
    st2 = collector.collect_once(s)
    assert st2["alerts_new"] == 0  # ext_id 로 중복 제거
    with Database(s.db_path) as db:
        rows = {r["alert_type_raw"]: dict(r) for r in db.conn.execute("SELECT * FROM v_alerts")}
        assert rows["health_104"]["alert_type"] == "TEMP_HIGH"
        assert rows["health_104"]["animal_label"] == "101" and rows["health_104"]["value"] == 40.1
        assert rows["feeding_202"]["alert_type"] == "PH_LOW"
        assert rows["health_305"]["alert_type"] == "RUMINATION_DROP"
        assert rows["brand_new_999"]["alert_type"] == "OTHER"
        assert rows["heat"]["status"] == "event" and rows["heat"]["duration_hours"] is None
        assert db.conn.execute("SELECT herd_size FROM farms").fetchone()[0] == 2  # 센서 장착 두수


def test_api_bad_password(api, tmp_path, monkeypatch):
    monkeypatch.setattr(collector, "load_farm_overrides", lambda: {})
    st = collector.collect_once(_settings(tmp_path, api, password="nope"))
    assert st["status"] == "failed" and "로그인 실패" in st["errors"][0] and "nope" not in st["errors"][0]
