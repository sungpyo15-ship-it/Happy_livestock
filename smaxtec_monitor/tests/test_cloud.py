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


def test_notes_sync_across_devices(env):
    store, app = env
    phone, pc = app.test_client(), app.test_client()
    phone.post("/login", data={"username": OTHER, "password": "otherpw"})
    pc.post("/login", data={"username": OTHER, "password": "otherpw"})
    acc = phone.get("/account").get_data(as_text=True)
    csrf = _csrf(acc)
    # CSRF 없거나 잘못된 키는 거부
    assert phone.post("/api/notes", json={"key": "memo:1001", "value": "x"}).status_code == 400
    assert phone.post("/api/notes", json={"key": "../etc", "value": "x"}, headers={"X-CSRF": csrf}).status_code == 400
    # 휴대폰에서 저장 → PC 에서 보임
    assert phone.post("/api/notes", json={"key": "memo:1001", "value": "TMR 입자도 확인"},
                      headers={"X-CSRF": csrf}).status_code == 200
    phone.post("/api/notes", json={"key": "ck:1001:2026-11-20", "value": {"0": True}}, headers={"X-CSRF": csrf})
    assert pc.get("/api/notes").json == {"memo:1001": "TMR 입자도 확인", "ck:1001:2026-11-20": {"0": True}}
    # 다른 사용자에게는 안 보임
    me = app.test_client()
    me.post("/login", data={"username": USER, "password": "pw"})
    assert me.get("/api/notes").json == {}
    # 빈 메모 = 삭제
    phone.post("/api/notes", json={"key": "memo:1001", "value": ""}, headers={"X-CSRF": csrf})
    assert "memo:1001" not in pc.get("/api/notes").json


def test_admin_validate_button(api, tmp_path, monkeypatch):  # noqa: F811
    key = Fernet.generate_key()
    store = CloudStore(tmp_path / "cloud", key, ADMIN)
    base = dataclasses.replace(load_settings(env_file=tmp_path / "none.env"), mode="api", api_base=api, api_token="",
                               username="", password="", min_delay=0, max_delay=0, max_retries=1, log_dir=tmp_path / "logs")
    store.allow(OTHER)
    valid = {(ADMIN, "adminpw"): 0, (OTHER, "otherpw"): 0}

    def verify(u, pw):
        from smxmon.common import LoginError
        if (u, pw) not in valid:
            raise LoginError("bad")
        return valid[(u, pw)]
    app = create_app(store, base, secure_cookie=False, runner=Runner(base, store), verify_login=verify)
    app.testing = True
    monkeypatch.setenv("SMX_CLOUD_KEY", key.decode())
    monkeypatch.setenv("SMX_CLOUD_DATA", str(store.root))
    monkeypatch.setenv("SMX_CLOUD_ADMIN", ADMIN)
    c = app.test_client()
    c.post("/login", data={"username": ADMIN, "password": "adminpw"})
    _wait(store, ADMIN)
    acc = c.get("/account").get_data(as_text=True)
    assert "판정 기준 검증" in acc and "아직 실행 안 함" in acc
    r = c.post("/admin/validate", data={"csrf": _csrf(acc)}).get_data(as_text=True)
    assert "검증을 시작했습니다" in r
    t0 = time.time()
    while "실행 중" in c.get("/account").get_data(as_text=True) and time.time() - t0 < 90:
        time.sleep(0.5)
    acc = c.get("/account").get_data(as_text=True)
    log = (store.user_dir(store.get_by_username(ADMIN).uid) / "reports" / "판정검증_log.txt").read_text(encoding="utf-8")
    assert "마지막 결과" in acc, log
    assert "판정 기준 검증 —" in acc                 # 요약
    assert c.get("/files/validate").status_code == 200
    # 팀원 화면에는 없음
    u = app.test_client()
    u.post("/login", data={"username": OTHER, "password": "otherpw"})
    assert "판정 기준 검증" not in u.get("/account").get_data(as_text=True)


def test_action_records_excel(env):
    store, app = env
    c = app.test_client()
    c.post("/login", data={"username": USER, "password": "pw"})
    _wait(store, USER)
    csrf = _csrf(c.get("/account").get_data(as_text=True))
    assert "조치 기록(엑셀)" not in c.get("/account").get_data(as_text=True)
    rec = {"d": "2026-10-01", "target": "개체:101", "sig": "체온 상승", "acts": ["수의사 진료 의뢰"], "memo": "", "res": "치료 후 회복",
           "rd": "2026-10-08"}
    assert c.post("/api/notes", json={"key": "act:org1:abc1", "value": rec}, headers={"X-CSRF": csrf}).status_code == 200
    assert c.post("/api/notes", json={"key": "act:org1:abc2", "value": "문자열"}, headers={"X-CSRF": csrf}).status_code == 400
    c.post("/api/notes", json={"key": "act:org1:abc3", "value": {"del": 1, "d": "2026-10-02"}}, headers={"X-CSRF": csrf})
    assert "조치 기록(엑셀)" in c.get("/account").get_data(as_text=True)
    r = c.get("/files/actions")
    assert r.status_code == 200
    import io
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(r.data))
    assert wb.sheetnames == ["조치별 요약", "전체 기록", "결과 현황"]
    rows = [x for x in wb["전체 기록"].iter_rows(min_row=5, values_only=True) if x[0]]
    assert len(rows) == 1 and rows[0][0] == "가나목장" and rows[0][6] == "치료 후 회복"   # 지운 기록 제외
    assert wb["조치별 요약"]["A5"].value == "수의사 진료 의뢰"


def test_dhi_upload_web(env, tmp_path):
    import io
    from openpyxl import Workbook
    store, app = env
    c = app.test_client()
    c.post("/login", data={"username": USER, "password": "pw"})
    _wait(store, USER)
    acc = c.get("/account").get_data(as_text=True)
    assert "검정성적 올리기" in acc and 'value="org1">가나목장' in acc
    wb = Workbook()
    ws = wb.active
    ws.append(["검정일", "이표번호", "관리번호", "산차", "비유일수", "유량", "유지방", "유단백", "체세포", "MUN"])
    ws.append(["2026-11-10", "002111111111", "101", 2, 30, 35.2, 4.1, 3.3, 90, 12])     # 이표 끝 9자리로 연결
    ws.append(["2026-11-10", "", "102", 3, 200, 28.0, 3.8, 3.4, 420, 15])               # 관리번호로 연결
    ws.append(["2026-11-10", "", "999", 1, 100, 30.0, 3.9, 3.2, 100, 14])               # 센서 없음
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    r = c.post("/dhi", data={"csrf": _csrf(acc), "farm": "org1", "file": (buf, "dhi.xlsx")},
               content_type="multipart/form-data").get_data(as_text=True)
    assert "검정일 2026-11-10 · 3두 저장 (센서 개체와 연결 2두)" in r
    assert c.get("/files/dhi_template").status_code == 200


def test_guide_page(env):
    store, app = env
    c = app.test_client()
    r = c.get("/guide")
    assert r.status_code == 200 and "목장 모니터 사용 설명서" in r.get_data(as_text=True)


def test_admin_guides_excel(env, tmp_path, monkeypatch):
    import io
    import openpyxl
    from smxmon import actions
    monkeypatch.setenv("SMX_ACTIONS_OVERRIDE", str(tmp_path / "ov.json"))
    actions._cache.clear()
    store, app = env
    c = app.test_client()
    c.post("/login", data={"username": ADMIN, "password": "adminpw"})
    acc = c.get("/account").get_data(as_text=True)
    assert "조치 가이드 편집" in acc and "기본 내용 사용 중" in acc
    r = c.get("/admin/guides.xlsx")
    assert r.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(r.data))
    ws = wb["조치가이드"]
    assert ws["A2"].value == "temp_high" and "Sheldon" in ws["G2"].value
    ws["D2"] = "직장 체온 재측정\n분만 후 일수 확인"
    buf = io.BytesIO()
    wb.save(buf)
    # 잘못된 신호 코드 → 반영 안 됨
    ws["C3"] = "TEMP_LOW, NOPE"
    bad = io.BytesIO()
    wb.save(bad)
    r = c.post("/admin/guides", data={"csrf": _csrf(acc), "file": (io.BytesIO(bad.getvalue()), "g.xlsx")},
               content_type="multipart/form-data").get_data(as_text=True)
    assert "반영하지 않았습니다" in r and "NOPE" in r
    assert not actions.is_custom()
    r = c.post("/admin/guides", data={"csrf": _csrf(acc), "file": (io.BytesIO(buf.getvalue()), "g.xlsx")},
               content_type="multipart/form-data").get_data(as_text=True)
    assert "반영했습니다" in r
    g = next(x for x in actions.guides() if x["id"] == "temp_high")
    assert g["check"] == ["직장 체온 재측정", "분만 후 일수 확인"] and g["ref"]
    # 팀원은 못 받음
    u = app.test_client()
    u.post("/login", data={"username": OTHER, "password": "otherpw"})
    assert u.get("/admin/guides.xlsx").status_code == 403
    acc = c.get("/account").get_data(as_text=True)
    c.post("/admin/guides/reset", data={"csrf": _csrf(acc)})
    assert not actions.is_custom() and next(x for x in actions.guides() if x["id"] == "temp_high")["check"][0].startswith("직장 체온으로 재확인")


def test_contacts_api_and_admin_excel(env):
    import io
    import openpyxl
    store, app = env
    c = app.test_client()
    c.post("/login", data={"username": USER, "password": "pw"})
    _wait(store, USER)
    csrf = _csrf(c.get("/account").get_data(as_text=True))
    h = {"X-CSRF": csrf}
    assert c.post("/api/contacts", json={"farm_id": "org1", "items": [{"role": "딜러", "name": "김", "phone": "12"}]},
                  headers=h).status_code == 400                                    # 번호 형식
    assert c.post("/api/contacts", json={"farm_id": "nope", "items": []}, headers=h).status_code == 400   # 담당 목장 아님
    r = c.post("/api/contacts", json={"farm_id": "org1", "items": [{"role": "딜러", "name": "김딜러", "phone": "01012345678"}]},
               headers=h)
    assert r.json["items"][0]["phone"] == "010-1234-5678"
    assert c.get("/api/contacts").json == {"org1": [{"role": "딜러", "name": "김딜러", "phone": "010-1234-5678", "farm_name": "가나목장"}]}
    other = app.test_client()
    other.post("/login", data={"username": OTHER, "password": "otherpw"})
    assert other.get("/api/contacts").json == {}                                    # 다른 팀원 목장 아님
    # 관리자 엑셀 받기·올리기
    adm = app.test_client()
    adm.post("/login", data={"username": ADMIN, "password": "adminpw"})
    acc = adm.get("/account").get_data(as_text=True)
    assert "목장 담당자" in acc
    wb = openpyxl.load_workbook(io.BytesIO(adm.get("/admin/contacts.xlsx").data))
    ws = wb["담당자"]
    assert ws["A2"].value == "org1" and ws["D2"].value == "김딜러"
    ws.append(["org1", "가나목장", "판매과장", "이과장", 1099990000])          # 엑셀 숫자 칸(앞 0 빠짐)
    ws.append(["org1", "가나목장", "수의사", "박수의", "+82 10-2222-3333"])
    buf = io.BytesIO()
    wb.save(buf)
    r = adm.post("/admin/contacts", data={"csrf": _csrf(acc), "file": (io.BytesIO(buf.getvalue()), "c.xlsx")},
                 content_type="multipart/form-data").get_data(as_text=True)
    assert "담당자 3명을 저장" in r
    got = c.get("/api/contacts").json["org1"]
    assert [(x["name"], x["phone"]) for x in got] == [("김딜러", "010-1234-5678"), ("이과장", "010-9999-0000"),
                                                      ("박수의", "010-2222-3333")]
