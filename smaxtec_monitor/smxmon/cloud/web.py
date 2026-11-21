"""클라우드 웹앱 (Flask): 로그인 → 내 목장 앱, 계정 화면, 관리자 화면(팀원 허용).

보안
- 로그인 = smaXtec 계정 확인(실제 API 로그인 시도). 허용목록(관리자가 등록)에 있는 아이디만 가능
- 세션 쿠키: HttpOnly·Secure·SameSite=Lax, 서버에는 토큰 해시만 저장
- 모든 POST 는 CSRF 토큰(로그인은 Origin 확인), 로그인 시도 횟수 제한
- 비밀번호는 화면·로그에 나오지 않음, '연결 해제' 시 비밀번호·데이터 즉시 삭제
"""
from __future__ import annotations

import dataclasses
import html
import json
import logging
import re
import time
from collections import defaultdict, deque
from urllib.parse import urlparse

from flask import Flask, Response, abort, redirect, request, send_file

from ..api_client import ApiError, SmaxtecAPI
from ..common import LoginError
from ..config import Settings
from ..pwa import MANIFEST, SERVICE_WORKER, _png
from .jobs import Runner
from .store import CloudStore, User

log = logging.getLogger("smxmon")
COOKIE = "smx_session"
STATUS_TXT = {"pending": "준비 중", "running": "수집 중", "ok": "정상", "failed": "오류", "login_failed": "다시 로그인 필요"}

CSS = """
:root{--bg:#f4f6f9;--card:#fff;--ink:#1b2430;--sub:#5d6b7a;--line:#e3e8ee;--navy:#1f4e78;--bad:#c62828;--ok:#2e7d32}
@media (prefers-color-scheme:dark){:root{--bg:#10151b;--card:#18212b;--ink:#e6edf3;--sub:#93a1b0;--line:#2a3542;--navy:#7fb2e5;--bad:#ef6b6b;--ok:#7cc38a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 -apple-system,BlinkMacSystemFont,
"Apple SD Gothic Neo","Malgun Gothic",sans-serif;word-break:keep-all}
header{background:#1f4e78;color:#fff;padding:14px 16px;padding-top:max(14px,env(safe-area-inset-top))}
header b{font-size:17px}header a{color:#fff;float:right;font-size:14px;opacity:.9}
main{max-width:560px;margin:0 auto;padding:16px 16px 40px;display:grid;gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;display:grid;gap:10px}
h1{font-size:20px;margin:0}h2{font-size:16px;margin:0;color:var(--navy)}p{margin:0}
label{font-size:14px;color:var(--sub);display:grid;gap:4px}
input[type=text],input[type=password],input[type=email],input[type=date],select{width:100%;padding:11px 12px;font-size:16px;border:1px solid var(--line);
border-radius:10px;background:var(--card);color:var(--ink)}
button,.btn{display:inline-block;text-align:center;border:0;border-radius:10px;padding:11px 14px;font-size:15px;
background:var(--navy);color:#fff;text-decoration:none;cursor:pointer}
@media (prefers-color-scheme:dark){button,.btn{color:#10151b}}
.ghost{background:transparent;color:var(--navy);border:1px solid var(--line)}
.danger{background:var(--bad)}
.msg{padding:10px 12px;border-radius:10px;background:#fdecea;color:#8a1c1c;font-size:14px}
.okmsg{padding:10px 12px;border-radius:10px;background:#e8f5e9;color:#1b5e20;font-size:14px}
.sub{color:var(--sub);font-size:14px}
table{width:100%;border-collapse:collapse;font-size:14px}td,th{padding:7px 4px;border-bottom:1px solid var(--line);text-align:left}
.row{display:flex;gap:8px;flex-wrap:wrap}.row>*{flex:1 1 auto}
.st-ok{color:var(--ok);font-weight:700}.st-failed,.st-login_failed{color:var(--bad);font-weight:700}
"""


def _page(title: str, body: str, *, nav: bool = True, refresh: int | None = None) -> str:
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    return (f'<!doctype html><html lang="ko"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
            f'<meta name="theme-color" content="#1f4e78"><meta name="apple-mobile-web-app-capable" content="yes">'
            f'<meta name="apple-mobile-web-app-title" content="목장모니터"><link rel="manifest" href="/manifest.webmanifest">'
            f'<link rel="apple-touch-icon" href="/icon-180.png">{meta}<title>{html.escape(title)}</title><style>{CSS}</style></head>'
            f'<body><header><b>smaXtec 목장 모니터</b>{"<a href=/>앱으로</a>" if nav else ""}</header><main>{body}</main></body></html>')


def create_app(store: CloudStore, base: Settings, *, secure_cookie: bool = True, runner: Runner | None = None,
               verify_login=None) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
    runner = runner or Runner(base, store)
    attempts: dict[str, deque] = defaultdict(deque)
    last_refresh: dict[str, float] = {}
    icons: dict[int, bytes] = {}

    def verify(username: str, password: str) -> int:
        """smaXtec 에 실제 로그인해서 확인. 반환: 접근 가능한 목장 수."""
        if verify_login:
            return verify_login(username, password)
        api = SmaxtecAPI(dataclasses.replace(base, username=username, password=password, api_token="", max_retries=1))
        return len(api.list_farms())

    # ───────── 공통 ─────────
    def current() -> tuple[User, str] | None:
        return store.session(request.cookies.get(COOKIE))

    def need_login():
        cur = current()
        if not cur:
            abort(redirect("/login"))
        return cur

    def check_csrf(csrf: str) -> None:
        if request.form.get("csrf") != csrf:
            abort(400, "요청이 만료되었습니다. 화면을 새로고침한 뒤 다시 시도하세요.")

    def same_origin() -> bool:
        o = request.headers.get("Origin") or request.headers.get("Referer")
        return not o or urlparse(o).netloc == request.host

    @app.after_request
    def headers(resp: Response):
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; "
                                "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
        if request.path not in ("/sw.js",) and not request.path.startswith("/icon-"):
            resp.headers.setdefault("Cache-Control", "no-cache")
        return resp

    # ───────── 홈 화면 앱 파일 ─────────
    @app.get("/manifest.webmanifest")
    def manifest():
        m = {**MANIFEST, "start_url": "/", "scope": "/",
             "icons": [{**i, "src": "/" + i["src"]} for i in MANIFEST["icons"]]}
        return Response(json.dumps(m, ensure_ascii=False), mimetype="application/manifest+json")

    @app.get("/sw.js")
    def sw():
        return Response(SERVICE_WORKER, mimetype="text/javascript", headers={"Cache-Control": "no-cache"})

    @app.get("/icon-<int:n>.png")
    def icon(n: int):
        if n not in (180, 192, 512):
            abort(404)
        if n not in icons:
            icons[n] = _png(n)
        return Response(icons[n], mimetype="image/png", headers={"Cache-Control": "max-age=604800"})

    # ───────── 로그인 ─────────
    @app.get("/login")
    def login_form(msg: str = "", user: str = ""):
        body = f"""<div class="card"><h1>로그인</h1>
        <p class="sub">평소 web.smaxtec.com 에 쓰는 <b>smaXtec 아이디·비밀번호</b>로 로그인하세요.
        본인 담당 목장만 보입니다.</p>
        {f'<div class="msg">{html.escape(msg)}</div>' if msg else ''}
        <form method="post" action="/login" class="card" style="border:0;padding:0">
          <label>smaXtec 아이디(이메일)<input type="email" name="username" id="username" autocomplete="username" required value="{html.escape(user)}"></label>
          <label>비밀번호<input type="password" name="password" id="password" autocomplete="current-password" required></label>
          <button type="submit">로그인</button>
        </form>
        <p class="sub">처음 로그인하면 지난 1년 기록을 가져오느라 15~30분 정도 걸립니다.
        비밀번호는 자동 수집에만 쓰이며 암호화해 보관하고, [계정 → 연결 해제]로 언제든 삭제할 수 있습니다.</p></div>"""
        return _page("로그인", body, nav=False)

    @app.post("/login")
    def login():
        if not same_origin():
            abort(400)
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
        q = attempts[ip]
        now = time.time()
        while q and now - q[0] > 900:
            q.popleft()
        if len(q) >= 8:
            store.log_login(request.form.get("username") or "", "rate_limited")
            return login_form("로그인 시도가 너무 많습니다. 15분 뒤 다시 시도하세요."), 429
        q.append(now)
        u = (request.form.get("username") or "").strip().lower()
        pw = request.form.get("password") or ""
        if not u or not pw:
            store.log_login(u, "empty")
            return login_form("아이디와 비밀번호를 입력하세요.", u), 400
        if not store.is_allowed(u):
            store.log_login(u, "not_allowed")
            return login_form("등록되지 않은 아이디입니다. 관리자에게 등록을 요청하세요.", u), 403
        try:
            n = verify(u, pw)
        except LoginError:
            store.log_login(u, "bad_password")
            return login_form("smaXtec 아이디 또는 비밀번호가 맞지 않습니다. web.smaxtec.com 에서 같은 아이디·비밀번호로 "
                              "로그인되는지 확인하세요. 웹에서는 되는데 여기서만 안 되면 관리자에게 알려 주세요.", u), 401
        except (ApiError, OSError):
            store.log_login(u, "api_error")
            return login_form("smaXtec 서버에 연결하지 못했습니다. 잠시 뒤 다시 시도하세요.", u), 502
        store.log_login(u, "ok")
        user, new = store.upsert_user(u, pw)
        store.set_farms(user.uid, n)
        token, _ = store.new_session(user.uid)
        has_app = (store.user_dir(user.uid) / "reports" / "smaxtec_app.html").exists()
        if new or not has_app or user.status in ("pending", "login_failed"):
            runner.submit(user.uid)
        resp = redirect("/")
        resp.set_cookie(COOKIE, token, max_age=90 * 86400, httponly=True, secure=secure_cookie, samesite="Lax")
        return resp

    @app.post("/logout")
    def logout():
        user, csrf = need_login()
        check_csrf(csrf)
        store.end_session(request.cookies.get(COOKIE))
        resp = redirect("/login")
        resp.delete_cookie(COOKIE)
        resp.headers["Clear-Site-Data"] = '"cache", "storage"'
        return resp

    # ───────── 앱 ─────────
    @app.get("/")
    def home():
        user, csrf = need_login()
        f = store.user_dir(user.uid) / "reports" / "smaxtec_app.html"
        if f.exists():
            nav = ' · <a href="/account" style="color:#fff">계정</a>'
            if user.status in ("running", "pending"):
                nav += " · 갱신 중"
            elif user.status == "login_failed":
                nav += ' · <a href="/login" style="color:#ffd0d0">다시 로그인 필요</a>'
            page = f.read_text(encoding="utf-8").replace("<!--CLOUD_NAV-->", nav, 1)
            page = page.replace("</head>", f"<script>window.SMX_CLOUD={{csrf:{json.dumps(csrf)}}};</script></head>", 1)
            return Response(page, mimetype="text/html")
        st = STATUS_TXT.get(user.status, user.status)
        body = f"""<div class="card"><h1>데이터 준비 중</h1>
          <p>{html.escape(user.username)} 계정의 담당 목장{f' {user.farms}곳' if user.farms else ''} 기록을 가져오고 있습니다.
          처음에는 <b>15~30분</b> 걸립니다. 이 화면은 자동으로 새로고침됩니다.</p>
          <p class="sub">상태: <span class="st-{user.status}">{st}</span>{' — ' + html.escape(user.message) if user.message else ''}</p>
          <a class="btn ghost" href="/account">계정</a></div>"""
        return _page("준비 중", body, nav=False, refresh=None if user.status in ("failed", "login_failed") else 20)

    # ───────── 계정 ─────────
    @app.get("/account")
    def account(msg: str = "", ok: str = ""):
        user, csrf = need_login()
        d = store.user_dir(user.uid) / "reports"
        files = [("daily", "일일 리포트(엑셀)", "smaxtec_daily_*.xlsx"), ("visit", "방문요약(엑셀)", "방문요약_*.xlsx"),
                 ("trialrep", "투입비교 보고서(엑셀)", "투입비교_*.xlsx"), ("validate", "판정검증(엑셀)", "판정검증_2*.xlsx")]
        links = "".join(f'<a class="btn ghost" href="/files/{k}">{lbl}</a>' for k, lbl, pat in files
                        if any(d.glob(pat)))
        if any(k.startswith("act:") for k in store.notes(user.uid)):
            links += '<a class="btn ghost" href="/files/actions">조치 기록(엑셀)</a>'
        st = STATUS_TXT.get(user.status, user.status)
        body = f"""
        {f'<div class="msg">{html.escape(msg)}</div>' if msg else ''}{f'<div class="okmsg">{html.escape(ok)}</div>' if ok else ''}
        <div class="card"><h1>계정</h1>
          <p><b>{html.escape(user.username)}</b> · 담당 목장 {user.farms or '-'}곳</p>
          <p class="sub">상태: <span class="st-{user.status}">{st}</span>{' — ' + html.escape(user.message) if user.message else ''}<br>
          마지막 정상 수집: {user.last_ok or '없음'} · 자동 갱신: 매일 06시 전, 낮 2시간마다</p>
          <form method="post" action="/refresh"><input type="hidden" name="csrf" value="{csrf}">
            <button type="submit">지금 새로고침</button></form></div>
        {f'<div class="card"><h2>파일 받기</h2><div class="row">{links}</div></div>' if links else ''}
        <div class="card"><h2>사료·첨가제 투입 기록</h2>
          <p class="sub">양식을 받아 엑셀에서 작성한 뒤 올리면 다음 갱신 때 앱 '투입비교'에 결과가 나옵니다.</p>
          <a class="btn ghost" href="/files/trials">투입기록 양식/현재 파일 받기</a>
          <form method="post" action="/trials" enctype="multipart/form-data" class="row">
            <input type="hidden" name="csrf" value="{csrf}">
            <input type="file" name="file" id="trialfile" accept=".xlsx" required>
            <button type="submit">올리기</button></form></div>
        <div class="card"><h2>검정성적 올리기</h2>
          <p class="sub">검정 기관·목장 프로그램에서 받은 검정성적 엑셀(.xlsx)을 그대로 올리면 머리글(관리번호·이표·유량·유지방·체세포 등)로 열을 찾아
          센서 개체와 연결합니다. 결과: 목장 상세 '4-3. 검정성적·경제성'(알림 소 유량 손실 추정).</p>
          <form method="post" action="/dhi" enctype="multipart/form-data" class="row">
            <input type="hidden" name="csrf" value="{csrf}">
            <select name="farm" required><option value="">목장 선택</option>{farm_opts(user)}</select>
            <input type="date" name="test_date" title="파일에 검정일이 없을 때만">
            <input type="file" name="file" accept=".xlsx" required>
            <button type="submit">올리기</button></form>
          <a class="btn ghost" href="/files/dhi_template">직접 입력용 양식</a></div>
        <div class="card"><h2>로그아웃 · 연결 해제</h2>
          <form method="post" action="/logout"><input type="hidden" name="csrf" value="{csrf}">
            <button class="ghost" type="submit">로그아웃 (이 기기)</button></form>
          <form method="post" action="/delete" class="card" style="border:0;padding:0">
            <input type="hidden" name="csrf" value="{csrf}">
            <label style="flex-direction:row;display:flex;gap:8px;align-items:center">
              <input type="checkbox" name="confirm" id="confirm" value="yes" required> 저장된 비밀번호와 수집 데이터를 모두 삭제합니다</label>
            <button class="danger" type="submit">연결 해제</button></form></div>
        {admin_html(user, csrf) if store.admin and user.username == store.admin else ''}"""
        return _page("계정", body)

    def farm_opts(user: User) -> str:
        import sqlite3
        dbp = store.user_dir(user.uid) / "data" / "smaxtec.db"
        if not dbp.exists():
            return ""
        conn = sqlite3.connect(f"file:{dbp}?mode=ro", uri=True)
        try:
            rows = conn.execute("SELECT farm_id, name FROM farms ORDER BY name").fetchall()
        except sqlite3.Error:
            rows = []
        finally:
            conn.close()
        return "".join(f'<option value="{html.escape(r[0])}">{html.escape(r[1])}</option>' for r in rows)

    def admin_html(user: User, csrf: str) -> str:
        allowed = store.allowed_list()
        res_txt = {"ok": "성공", "not_allowed": "등록 안 됨", "bad_password": "비밀번호 거절", "api_error": "연결 실패",
                   "rate_limited": "시도 과다", "empty": "빈 입력"}
        events = "".join(
            f"<tr><td>{e['at'][5:16]}</td><td>{html.escape(e['username'] or '-')}</td>"
            f"<td class='{'st-ok' if e['result'] == 'ok' else 'st-failed'}'>{res_txt.get(e['result'], e['result'])}</td></tr>"
            for e in store.login_events()) or "<tr><td colspan=3 class=sub>없음</td></tr>"
        rows = "".join(
            f"<tr><td>{html.escape(u.username)}</td><td class='st-{u.status}'>{STATUS_TXT.get(u.status, u.status)}</td>"
            f"<td>{u.farms or '-'}</td><td>{(u.last_ok or '-')[5:16]}</td></tr>" for u in store.users())
        al = "".join(
            f"""<tr><td>{html.escape(a['username'])}</td><td>{html.escape(a['note'] or '')}</td><td>
            <form method="post" action="/admin/remove"><input type="hidden" name="csrf" value="{csrf}">
            <input type="hidden" name="username" value="{html.escape(a['username'])}">
            <button class="ghost" type="submit" style="padding:4px 8px;font-size:13px">삭제</button></form></td></tr>"""
            for a in allowed)
        return f"""<div class="card"><h2>관리자 — 팀원 등록</h2>
          <p class="sub">여기에 등록한 smaXtec 아이디만 로그인할 수 있습니다. 삭제하면 그 팀원의 저장 비밀번호·데이터도 삭제됩니다.</p>
          <form method="post" action="/admin/allow" class="row"><input type="hidden" name="csrf" value="{csrf}">
            <input type="email" name="username" id="newuser" placeholder="팀원 smaXtec 아이디(이메일)" required>
            <input type="text" name="note" id="newnote" placeholder="이름(메모)">
            <button type="submit">등록</button></form>
          <table><tr><th>허용 아이디</th><th>메모</th><th></th></tr>{al or '<tr><td colspan=3 class=sub>없음</td></tr>'}</table>
          <h2>사용 현황</h2>
          <table><tr><th>아이디</th><th>상태</th><th>목장</th><th>마지막 수집</th></tr>{rows}</table>
          <h2>최근 로그인 기록</h2>
          <table><tr><th>시각</th><th>아이디</th><th>결과</th></tr>{events}</table>
          <p class="sub">등록 안 됨 = 허용 목록에 없음(철자 확인) · 비밀번호 거절 = smaXtec 이 거절(웹 로그인 확인, 웹은 되면 리얼팜에 API 조회 권한 문의)</p></div>
        {validate_html(user, csrf)}"""

    def validate_html(user: User, csrf: str) -> str:
        d = store.user_dir(user.uid) / "reports"
        fs = sorted(d.glob("판정검증_2*.xlsx"), key=lambda p: p.stat().st_mtime)
        summ = d / "판정검증_요약.txt"
        running = validate_job.get("p") is not None and validate_job["p"].poll() is None
        last = time.strftime("%m/%d %H:%M", time.localtime(fs[-1].stat().st_mtime)) if fs else None
        failed = (not running and validate_job.get("p") is not None and validate_job["p"].returncode not in (0, None))
        st = ("<b>실행 중</b> — 몇 분 걸립니다. 잠시 뒤 이 화면을 새로고침하세요." if running else
              "<span class='st-failed'>지난 실행 실패</span> — 서버 로그 확인(sudo journalctl -u smxmon-web -n 50)" if failed else
              f"마지막 결과: {last}" if last else "아직 실행 안 함")
        text = html.escape(summ.read_text(encoding="utf-8")) if summ.exists() and fs else ""
        return f"""<div class="card"><h2>관리자 — 판정 기준 검증</h2>
          <p class="sub">지난 1년 매일 06시 판정(긴급·우선 등)을 다시 계산해, 그 뒤 7일 동안의 질병 메모·알림 확인 처리(농장 입력)·알림 지속과 비교합니다.
          실행할 때 각 팀원 계정으로 농장의 알림 확인 기록·메모를 받아옵니다(처음엔 수십 분 걸릴 수 있음, 이후엔 새 것만).
          모든 팀원 DB 사용(목장 중복 제외). 결과 엑셀은 팀원 전체 목장 포함 → 외부 공유 금지.</p>
          <p>{st}</p>
          <div class="row"><form method="post" action="/admin/validate"><input type="hidden" name="csrf" value="{csrf}">
            <button type="submit" {'disabled' if running else ''}>검증 실행</button></form>
          {'<a class="btn ghost" href="/files/validate">결과 엑셀 받기</a><a class="btn ghost" href="/files/validate_anon">목장 이름 가린 엑셀</a>' if fs and not running else ''}</div>
          {f'<details><summary>요약 보기 (목장 이름 없음 — 그대로 복사해 공유 가능)</summary><pre style="white-space:pre-wrap;font-size:12px">{text}</pre></details>' if text else ''}
        </div>"""

    @app.post("/refresh")
    def refresh():
        user, csrf = need_login()
        check_csrf(csrf)
        last = last_refresh.get(user.uid, 0)
        if time.time() - last < 1200:
            return account(msg=f"새로고침은 20분에 한 번 가능합니다 ({int((1200 - (time.time() - last)) / 60) + 1}분 뒤)")
        last_refresh[user.uid] = time.time()
        runner.submit(user.uid)
        return account(ok="새로고침을 시작했습니다. 몇 분 뒤 앱에 반영됩니다.")

    @app.get("/files/<kind>")
    def files(kind: str):
        user, _ = need_login()
        d = store.user_dir(user.uid)
        if kind == "actions":
            import sqlite3
            from datetime import date
            from ..actions_report import build_workbook, evaluate, records_from_notes
            from ..config import load_toml
            from ..normalize import AlertTypeMapper
            recs = records_from_notes(store.notes(user.uid))
            dbp = d / "data" / "smaxtec.db"
            conn = sqlite3.connect(f"file:{dbp}?mode=ro", uri=True) if dbp.exists() else None
            try:
                rows = evaluate(recs, conn, set(AlertTypeMapper(load_toml("alert_types.toml")).codes("health")), date.today())
            finally:
                if conn is not None:
                    conn.close()
            p = build_workbook(rows, d / "reports" / "조치기록.xlsx")
            return send_file(p, as_attachment=True, download_name=f"조치기록_{date.today():%Y%m%d}.xlsx")
        if kind == "dhi_template":
            from ..dhi import template
            p = template(d / "reports" / "검정성적_양식.xlsx")
            return send_file(p, as_attachment=True, download_name="검정성적_양식.xlsx")
        if kind == "trials":
            from ..trials import ensure_template
            p = ensure_template(d / "투입기록.xlsx")
            return send_file(p, as_attachment=True, download_name="투입기록.xlsx")
        pat = {"daily": "smaxtec_daily_*.xlsx", "visit": "방문요약_*.xlsx", "trialrep": "투입비교_*.xlsx",
               "validate": "판정검증_2*.xlsx", "validate_anon": "판정검증익명_*.xlsx"}.get(kind)
        if not pat:
            abort(404)
        fs = sorted((d / "reports").glob(pat), key=lambda p: p.stat().st_mtime)
        if not fs:
            abort(404)
        return send_file(fs[-1], as_attachment=True, download_name=fs[-1].name)

    @app.post("/trials")
    def trials_upload():
        user, csrf = need_login()
        check_csrf(csrf)
        f = request.files.get("file")
        if not f or not f.filename.lower().endswith(".xlsx"):
            return account(msg="엑셀(.xlsx) 파일을 선택하세요.")
        d = store.user_dir(user.uid)
        tmp = d / "투입기록.upload.xlsx"
        f.save(tmp)
        try:
            from openpyxl import load_workbook
            ws = load_workbook(tmp, read_only=True).worksheets[0]
            first = [str(c.value or "") for c in next(ws.iter_rows(min_row=1, max_row=1))]
            if not first or "목장" not in first[0]:
                raise ValueError("양식이 아님")
        except Exception:  # noqa: BLE001
            tmp.unlink(missing_ok=True)
            return account(msg="투입기록 양식이 아닙니다. [양식 받기]로 받은 파일에 작성해 올려 주세요.")
        tmp.replace(d / "투입기록.xlsx")
        runner.submit(user.uid)
        return account(ok="투입기록을 저장했습니다. 갱신이 끝나면 앱 '투입비교'에 반영됩니다.")

    @app.post("/dhi")
    def dhi_upload():
        import sqlite3
        from ..dhi import parse, store_rows
        user, csrf = need_login()
        check_csrf(csrf)
        f, fid = request.files.get("file"), (request.form.get("farm") or "").strip()
        td = (request.form.get("test_date") or "").strip() or None
        if not f or not f.filename.lower().endswith(".xlsx"):
            return account(msg="검정성적 엑셀(.xlsx) 파일을 선택하세요. (.xls 는 엑셀에서 .xlsx 로 다시 저장)")
        dbp = store.user_dir(user.uid) / "data" / "smaxtec.db"
        if not dbp.exists():
            return account(msg="먼저 수집이 한 번 끝나야 올릴 수 있습니다.")
        tmp = store.user_dir(user.uid) / "dhi.upload.xlsx"
        f.save(tmp)
        try:
            p = parse(tmp, td)
        except Exception:  # noqa: BLE001
            return account(msg="엑셀을 읽지 못했습니다(.xlsx 인지, 암호가 걸려 있지 않은지 확인).")
        finally:
            tmp.unlink(missing_ok=True)
        if not p["rows"]:
            return account(msg="읽은 소가 없습니다. " + " ".join(p["warnings"]) + " — 머리글에 '유량'과 '관리번호' 또는 '이표'가 있어야 합니다.")
        if not p["test_date"]:
            return account(msg="파일에 검정일이 없습니다. 검정일을 입력하고 다시 올려 주세요.")
        conn = sqlite3.connect(dbp, timeout=60)
        try:
            if not conn.execute("SELECT 1 FROM farms WHERE farm_id=?", (fid,)).fetchone():
                return account(msg="목장을 선택하세요.")
            st = store_rows(conn, fid, p, td)
        finally:
            conn.close()
        runner.submit(user.uid)
        names = {"official": "이표", "label": "관리번호", "test_date": "검정일", "parity": "산차", "dim": "비유일수",
                 "milk": "유량", "fat": "유지방", "protein": "유단백", "scc": "체세포", "mun": "MUN"}
        miss = ", ".join(names[k] for k in p["missing"] if k in names)
        return account(ok=f"검정일 {p['test_date']} · {st['rows']}두 저장 (센서 개체와 연결 {st['matched']}두)"
                          + (f" · 못 찾은 열: {miss}" if miss else "") + (" · " + " ".join(p["warnings"]) if p["warnings"] else "")
                          + " — 갱신이 끝나면 목장 상세 '4-3. 검정성적'에 나옵니다.")

    @app.post("/delete")
    def delete():
        user, csrf = need_login()
        check_csrf(csrf)
        if request.form.get("confirm") != "yes":
            return account(msg="삭제 확인란을 체크하세요.")
        store.delete_user(user.uid)
        resp = redirect("/login")
        resp.delete_cookie(COOKIE)
        resp.headers["Clear-Site-Data"] = '"cache", "storage"'
        return resp

    # ───────── 관리자 ─────────
    def need_admin():
        user, csrf = need_login()
        if not store.admin or user.username != store.admin:
            abort(403)
        check_csrf(csrf)
        return user

    @app.post("/admin/allow")
    def admin_allow():
        need_admin()
        u = (request.form.get("username") or "").strip().lower()
        if "@" not in u:
            return account(msg="아이디(이메일)를 확인하세요.")
        store.allow(u, (request.form.get("note") or "").strip()[:40])
        return account(ok=f"{u} 를 등록했습니다. 이제 이 아이디로 로그인할 수 있습니다.")

    validate_job: dict = {}

    @app.post("/admin/validate")
    def admin_validate():
        import os
        import subprocess
        import sys
        from pathlib import Path
        user = need_admin()
        p = validate_job.get("p")
        if p is not None and p.poll() is None:
            return account(msg="이미 실행 중입니다.")
        d = store.user_dir(user.uid) / "reports"
        d.mkdir(parents=True, exist_ok=True)
        logf = open(d / "판정검증_log.txt", "w", encoding="utf-8")   # noqa: SIM115 (자식 프로세스가 사용)
        validate_job["p"] = subprocess.Popen(
            [sys.executable, "-m", "smxmon.cloud", "validate"], cwd=str(Path(__file__).resolve().parents[2]),
            stdout=logf, stderr=subprocess.STDOUT, start_new_session=True, preexec_fn=lambda: os.nice(10))
        return account(ok="검증을 시작했습니다. 몇 분 뒤 이 화면을 새로고침하면 결과가 보입니다.")

    @app.post("/admin/remove")
    def admin_remove():
        need_admin()
        u = (request.form.get("username") or "").strip().lower()
        store.disallow(u)
        return account(ok=f"{u} 를 삭제했습니다.")

    # ───────── 메모·체크리스트 동기화 ─────────
    NOTE_KEY = re.compile(r"^(memo:[\w.-]{1,40}|ck:[\w.-]{1,40}:\d{4}-\d{2}-\d{2}|act:[\w.-]{1,40}:[\w-]{1,20})$")

    @app.get("/api/notes")
    def notes_get():
        user, _ = need_login()
        return store.notes(user.uid)

    @app.post("/api/notes")
    def notes_set():
        user, csrf = need_login()
        if request.headers.get("X-CSRF") != csrf:
            abort(400)
        d = request.get_json(silent=True) or {}
        k, v = d.get("key", ""), d.get("value")
        if not isinstance(k, str) or not NOTE_KEY.match(k):
            abort(400)
        if k.startswith("memo:") and not (v is None or isinstance(v, str)):
            abort(400)
        if k.startswith(("ck:", "act:")) and not (v is None or isinstance(v, dict)):
            abort(400)
        if len(json.dumps(v, ensure_ascii=False)) > 10000:
            abort(413)
        store.set_note(user.uid, k, v)
        return {"ok": True}

    @app.get("/healthz")
    def health():
        return {"ok": True}

    return app

