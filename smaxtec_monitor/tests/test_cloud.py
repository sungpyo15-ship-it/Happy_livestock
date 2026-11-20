"""클라우드 웹앱 통합 테스트: 가짜 smaXtec API + 실제 로그인·백그라운드 수집·앱 제공·격리·삭제."""
import dataclasses
import re
import time

import pytest
from cryptography.fernet import Fernet

from smxmon.cloud.jobs import Runner
from smxmon.cloud.store import CloudStore
from smxmon.cloud.web import create_app
from smxmon.config import load_settings

from test_api_fake import api  # noqa: F401  (가짜 API 서버 fixture 재사용)

ADMIN, USER, OTHER = "boss@x.kr", "me@x.kr", "other@x.kr"


@pytest.fixture
def env(api, tmp_path):  # noqa: F811
    store = CloudStore(tmp_path / "cloud", Fernet.generate_key(), ADMIN)
    store.allow(USER, "팀원")
    store.allow(OTHER)
    base = dataclasses.replace(load_settings(env_file=tmp_path / "none.env"), mode="api", api_base=api,
                               api_token="", username="", password="", min_delay=0, max_delay=0,
                               max_retries=1, log_dir=tmp_path / "logs")
    valid = {(USER, "pw"): 1, (ADMIN, "adminpw"): 0, (OTHER, "otherpw"): 0}

    def verify(u, pw):
        from smxmon.common import LoginError
        if (u, pw) not in valid:
            raise LoginError("bad")
        return valid[(u, pw)]
    app = create_app(store, base, secure_cookie=False, runner=Runner(base, store), verify_login=verify)
    app.testing = True
    return store, app


def _csrf(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def _wait(store, username, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        u = store.get_by_username(username)
        if u and u.status not in ("pending", "running"):
            return u
        time.sleep(0.3)
    raise AssertionError("수집이 끝나지 않음")


def test_login_collect_view_isolation_delete(env):
    store, app = env
    c = app.test_client()
    assert c.get("/").status_code == 302                                   # 로그인 필요
    assert c.post("/login", data={"username": "stranger@x.kr", "password": "x"}).status_code == 403
    assert c.post("/login", data={"username": USER, "password": "wrong"}).status_code == 401
    assert [e["result"] for e in store.login_events()][:2] == ["bad_password", "not_allowed"]
    r = c.post("/login", data={"username": USER.upper(), "password": "pw"})
    assert r.status_code == 302 and "smx_session" in r.headers.get("Set-Cookie", "")
    u = _wait(store, USER)
    assert u.status == "ok", u.message
    page = c.get("/").get_data(as_text=True)
    assert "smaXtec 목장 모니터" in page and 'href="/account"' in page and "가나목장" in page
    # 비밀번호는 암호화 저장
    raw = (store.root / "cloud.db").read_bytes()
    assert b"pw\x00" not in raw and store.password(u.uid) == "pw"
    # 계정 화면·CSRF·파일
    acc = c.get("/account").get_data(as_text=True)
    assert "관리자" not in acc and "지금 새로고침" in acc
    assert c.post("/refresh", data={}).status_code == 400                  # CSRF 없으면 거부
    assert c.get("/files/trials").status_code == 200
    assert c.get("/files/daily").status_code == 200
    # 다른 사용자는 내 목장을 볼 수 없음
    c2 = app.test_client()
    c2.post("/login", data={"username": OTHER, "password": "otherpw"})
    _wait(store, OTHER)
    other_page = c2.get("/").get_data(as_text=True)
    assert "가나목장" not in other_page
    # 연결 해제 → 비밀번호·데이터 삭제
    d = store.user_dir(u.uid)
    r = c.post("/delete", data={"csrf": _csrf(acc), "confirm": "yes"})
    assert r.status_code == 302 and store.get_by_username(USER) is None and not d.exists()
    assert c.get("/").status_code == 302


def test_admin_allow_remove(env):
    store, app = env
    c = app.test_client()
    c.post("/login", data={"username": ADMIN, "password": "adminpw"})
    acc = c.get("/account").get_data(as_text=True)
    assert "관리자 — 팀원 등록" in acc
    assert "최근 로그인 기록" in acc and "성공" in acc
    c.post("/admin/allow", data={"csrf": _csrf(acc), "username": "new@x.kr", "note": "홍길동"})
    assert store.is_allowed("new@x.kr")
    c.post("/admin/remove", data={"csrf": _csrf(acc), "username": "new@x.kr"})
    assert not store.is_allowed("new@x.kr")
    # 일반 사용자는 관리자 기능 불가
    u = app.test_client()
    u.post("/login", data={"username": OTHER, "password": "otherpw"})
    acc2 = u.get("/account").get_data(as_text=True)
    assert u.post("/admin/allow", data={"csrf": _csrf(acc2), "username": "x@y.kr"}).status_code == 403


def test_pwa_files(env):
    _, app = env
    c = app.test_client()
    assert c.get("/manifest.webmanifest").json["start_url"] == "/"
    assert c.get("/sw.js").status_code == 200 and c.get("/icon-192.png").data[:4] == b"\x89PNG"
