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


PUSH_JS = """<script>
async function smxSub(url, hdr){
  const st = document.getElementById('pushst');
  try {
    if(!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)){
      st.textContent = '이 화면에서는 알림을 켤 수 없습니다. 아이폰은 iOS 16.4 이상에서 Safari 공유 → 홈 화면에 추가한 뒤, 홈 화면 아이콘으로 열어서 누르세요.'; return; }
    const perm = await Notification.requestPermission();
    if(perm !== 'granted'){ st.textContent = '알림이 허용되지 않았습니다. 휴대폰 설정 → 알림에서 허용한 뒤 다시 누르세요.'; return; }
    const reg = await navigator.serviceWorker.register('/sw.js', {scope:'/'}); await navigator.serviceWorker.ready;
    const key = (await (await fetch('/push/key')).json()).key;
    const raw = Uint8Array.from(atob(key.replace(/-/g,'+').replace(/_/g,'/') + '='.repeat((4 - key.length % 4) % 4)), c => c.charCodeAt(0));
    let sub = await reg.pushManager.getSubscription();
    if(!sub) sub = await reg.pushManager.subscribe({userVisibleOnly:true, applicationServerKey:raw});
    const j = sub.toJSON();
    const r = await fetch(url, {method:'POST', credentials:'same-origin', headers:Object.assign({'Content-Type':'application/json'}, hdr||{}),
      body: JSON.stringify({endpoint:j.endpoint, p256dh:j.keys.p256dh, auth:j.keys.auth, ua:navigator.userAgent})});
    st.textContent = r.ok ? '이 기기에서 알림을 받습니다 ✓' : '저장 실패 — 화면을 새로고침한 뒤 다시 누르세요';
  } catch(e) { st.textContent = '알림 켜기 실패: ' + e.message; }
}
</script>"""


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
            nav = ' · <a href="/account" style="color:#fff">계정</a> · <a href="/guide" style="color:#fff">사용법</a>'
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
          <p><b>{html.escape(user.username)}</b> · 담당 목장 {user.farms or '-'}곳 · <a href="/guide">사용 설명서</a></p>
          <p class="sub">상태: <span class="st-{user.status}">{st}</span>{' — ' + html.escape(user.message) if user.message else ''}<br>
          마지막 정상 수집: {user.last_ok or '없음'} · 자동 갱신: {schedule_text()}</p>
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
        <div class="card"><h2>알림 받기 (이 기기)</h2>
          <p class="sub">켜 두면 매일 08:00 '오늘 할 일'(긴급·우선 목장)과 14:00 추가분이 이 휴대폰에 알림으로 옵니다.
          아이폰은 iOS 16.4 이상, Safari 공유 → <b>홈 화면에 추가</b>한 앱에서 눌러야 합니다.</p>
          <p>등록된 기기: <b>{len(store.subs('u:' + user.uid))}대</b> <span id="pushst" class="sub"></span></p>
          <div class="row"><button type="button" onclick="smxSub('/push/subscribe', {{'X-CSRF': {json.dumps(csrf)}}})">이 기기에서 알림 받기</button>
          <form method="post" action="/push/test"><input type="hidden" name="csrf" value="{csrf}"><button class="ghost" type="submit">시험 알림 보내기</button></form>
          <form method="post" action="/push/off"><input type="hidden" name="csrf" value="{csrf}"><button class="ghost" type="submit">내 알림 모두 끄기</button></form></div>
          {PUSH_JS}</div>
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

    def schedule_text() -> str:
        p = store.root / "schedule.txt"
        try:
            return html.escape(p.read_text(encoding="utf-8").strip()) or "매일 06:30 · 12:30 · 18:30"
        except OSError:
            return "매일 06:30 · 12:30 · 18:30"

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
        {dispatch_html(csrf)}
        {guides_html(csrf)}
        <div class="card"><h2>관리자 — 목장 담당자(딜러·판매과장)</h2>
          <p class="sub">긴급·우선 목장 업무지시 문자에 쓰는 연락처입니다. 엑셀로 한 번에 넣거나, 각 팀원이 목장 상세에서 직접 고칠 수 있습니다.</p>
          <div class="row"><a class="btn ghost" href="/admin/contacts.xlsx">담당자 엑셀 받기</a></div>
          <form method="post" action="/admin/contacts" enctype="multipart/form-data" class="row">
            <input type="hidden" name="csrf" value="{csrf}">
            <input type="file" name="file" id="contactfile" accept=".xlsx" required>
            <button type="submit">올리기</button></form></div>
        {validate_html(user, csrf)}"""

    def dispatch_html(csrf: str) -> str:
        if request.host_url.startswith("https://"):
            store.kv_set("site_url", request.host_url.rstrip("/"))
        on, test = store.kv_get("dispatch_enabled", "0") == "1", store.kv_get("dispatch_test", "1") == "1"
        try:
            last = json.loads(store.kv_get("dispatch_last") or "{}")
        except ValueError:
            last = {}
        # 담당자 = 연락처의 전화번호별
        people: dict[str, dict] = {}
        for fid, cs in store.contacts().items():
            for c in cs:
                d = re.sub(r"\D", "", c["phone"])
                e = people.setdefault(d, {"name": c["name"], "role": c["role"], "phone": c["phone"], "farms": 0})
                e["farms"] += 1
        rows = []
        for d, e in sorted(people.items(), key=lambda x: x[1]["name"]):
            r = store.receiver_by_phone(d)
            dev = len(store.subs("r:" + r["id"])) if r else 0
            st = ("<span class='st-ok'>알림 켜짐 " + str(dev) + "대</span>" if dev else
                  "<span class='sub'>링크 보냄 · 알림 미설정</span>" if r else "<span class='sub'>링크 없음</span>")
            btn = (f'<form method="post" action="/admin/receiver"><input type="hidden" name="csrf" value="{csrf}">'
                   f'<input type="hidden" name="phone" value="{d}"><input type="hidden" name="name" value="{html.escape(e["name"])}">'
                   f'<button class="ghost" type="submit" style="padding:4px 8px;font-size:13px">{"새 링크" if r else "링크 만들기"}</button></form>')
            if r:
                btn += (f'<form method="post" action="/admin/receiver/revoke"><input type="hidden" name="csrf" value="{csrf}">'
                        f'<input type="hidden" name="phone" value="{d}"><button class="ghost" type="submit" style="padding:4px 8px;font-size:13px">끊기</button></form>')
            rows.append(f"<tr><td>{html.escape(e['role'])} {html.escape(e['name'])}<br><span class='sub'>{html.escape(e['phone'])} · 목장 {e['farms']}</span></td>"
                        f"<td>{st}</td><td><div class='row'>{btn}</div></td></tr>")
        recent = "".join(
            f"<tr><td>{x['day'][5:]} {'08시' if x['slot'] == 'am' else '14시'}{' <b>시험</b>' if x['test'] else ''}</td><td>{html.escape(x['name'] or '')}</td>"
            f"<td>{len(x['farms'])}곳</td><td>{'전달 ' + str(x['delivered']) + '/' + str(x['devices']) if x['devices'] else '<span class=sub>기기 없음</span>'}</td></tr>"
            for x in store.dispatches(limit=15)) or "<tr><td colspan=4 class=sub>아직 없음</td></tr>"
        lst = (f"마지막 실행 {last.get('at', '')} {'08시' if last.get('slot') == 'am' else '14시'} — "
               f"담당자 {last.get('contacts', 0)}명 · 목장 {last.get('farms', 0)}건 · 전달 {last.get('delivered', 0)}명"
               + (f" · 알림 미설정: {', '.join(last.get('no_device', [])[:6])}" if last.get('no_device') else "")
               + (f" · ⚠ 자료 오래됨(수집 실패): {', '.join(last.get('stale', []))}" if last.get('stale') else "")
               + (" (꺼짐)" if last.get('note') == '꺼짐' else "")) if last else "아직 실행 안 함"
        return f"""<div class="card"><h2>관리자 — 자동 업무지시 알림</h2>
          <p class="sub">매일 <b>08:00</b> 긴급·우선 목장을 담당자(딜러·판매과장)별로 묶어 알림, <b>14:00</b>엔 아침에 없던 목장과 우선→긴급으로 올라간 목장만 추가로 보냅니다(주말 포함).
          담당자는 아래 '링크 만들기'로 받은 주소를 열어 홈 화면에 추가 → 알림 받기를 한 번 해야 받습니다.</p>
          <form method="post" action="/admin/dispatch" style="display:grid;gap:8px;margin:8px 0"><input type="hidden" name="csrf" value="{csrf}">
            <label style="display:flex;gap:8px;align-items:center"><input type="checkbox" name="enabled" value="1" style="width:20px;height:20px" {'checked' if on else ''}> 자동 알림 켜기</label>
            <label style="display:flex;gap:8px;align-items:center"><input type="checkbox" name="test" value="1" style="width:20px;height:20px" {'checked' if test else ''}> 시험 모드 (모든 알림을 관리자 기기로만)</label>
            <button type="submit">저장</button></form>
          <p class="sub">지금: <b>{'켜짐' if on else '꺼짐'}</b>{' · 시험 모드' if on and test else ''} · {html.escape(lst)}</p>
          <form method="post" action="/admin/dispatch/now" class="row"><input type="hidden" name="csrf" value="{csrf}">
            <button class="ghost" type="submit" name="slot" value="am">지금 아침 알림 실행</button>
            <button class="ghost" type="submit" name="slot" value="pm">지금 오후 알림 실행</button></form>
          <h2>담당자 알림 링크</h2>
          <table><tr><th>담당자</th><th>상태</th><th></th></tr>{''.join(rows) or '<tr><td colspan=3 class=sub>먼저 목장 담당자를 등록하세요</td></tr>'}</table>
          <h2>최근 발송</h2><table><tr><th>시각</th><th>담당자</th><th>목장</th><th>전달</th></tr>{recent}</table></div>"""

    def guides_html(csrf: str) -> str:
        from ..actions import guides, is_custom
        st = (f"<b>관리자가 올린 내용</b> 사용 중 ({len(guides())}개)" if is_custom()
              else f"기본 내용 사용 중 ({len(guides())}개)")
        return f"""<div class="card"><h2>관리자 — 조치 가이드 편집</h2>
          <p class="sub">알림별 '먼저 확인 → 바로 할 조치 → 수의사 연락 기준 → 근거' 문구를 엑셀로 받아 고친 뒤 올리면 팀원 전체 앱·방문요약·엑셀에 반영됩니다.</p>
          <p>{st}</p>
          <div class="row"><a class="btn ghost" href="/admin/guides.xlsx">조치 가이드 엑셀 받기</a></div>
          <form method="post" action="/admin/guides" enctype="multipart/form-data" class="row">
            <input type="hidden" name="csrf" value="{csrf}">
            <input type="file" name="file" id="guidefile" accept=".xlsx" required>
            <button type="submit">올리기</button></form>
          {f'<form method="post" action="/admin/guides/reset"><input type="hidden" name="csrf" value="{csrf}"><button class="ghost" type="submit">기본 내용으로 되돌리기</button></form>' if is_custom() else ''}
        </div>"""

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

    def _codes() -> dict[str, str]:
        from ..config import load_toml
        from ..normalize import AlertTypeMapper
        m = AlertTypeMapper(load_toml("alert_types.toml"))
        return {c: t.label for c, t in m.types.items() if c not in ("OTHER", "MANAGEMENT")}

    @app.get("/admin/guides.xlsx")
    def admin_guides_get():
        from datetime import date
        from ..actions import export_xlsx
        user, _ = need_login()
        if not store.admin or user.username != store.admin:
            abort(403)
        p = export_xlsx(store.user_dir(user.uid) / "reports" / "조치가이드_편집.xlsx", _codes())
        return send_file(p, as_attachment=True, download_name=f"조치가이드_{date.today():%Y%m%d}.xlsx")

    @app.post("/admin/guides")
    def admin_guides_put():
        from ..actions import import_xlsx, save_override
        user = need_admin()
        f = request.files.get("file")
        if not f or not f.filename.lower().endswith(".xlsx"):
            return account(msg="엑셀(.xlsx) 파일을 선택하세요.")
        tmp = store.user_dir(user.uid) / "guides.upload.xlsx"
        f.save(tmp)
        try:
            items, errs = import_xlsx(tmp, set(_codes()))
        except Exception:  # noqa: BLE001
            items, errs = [], ["엑셀을 읽지 못했습니다(.xlsx 인지 확인)"]
        finally:
            tmp.unlink(missing_ok=True)
        if errs:
            return account(msg="반영하지 않았습니다 — " + " / ".join(errs[:6]) + (f" 외 {len(errs) - 6}건" if len(errs) > 6 else ""))
        save_override(items)
        for u in store.users():
            runner.submit(u.uid)
        return account(ok=f"조치 가이드 {len(items)}개를 반영했습니다. 팀원 화면은 새로고침이 끝나면(몇 분) 바뀝니다.")

    @app.post("/admin/guides/reset")
    def admin_guides_reset():
        from ..actions import reset_override
        need_admin()
        reset_override()
        for u in store.users():
            runner.submit(u.uid)
        return account(ok="조치 가이드를 기본 내용으로 되돌렸습니다.")

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

    @app.get("/guide")
    def guide():
        from pathlib import Path
        f = Path(__file__).resolve().parents[2] / "docs" / "manual.html"
        body = f.read_text(encoding="utf-8") if f.exists() else "<p>설명서 파일이 없습니다</p>"
        back = ('<p style="max-width:760px;margin:0 auto 12px;padding:0 16px"><a href="/" style="color:#1f4e78">‹ 앱으로</a></p>')
        return Response('<!doctype html><html lang="ko"><head><meta charset="utf-8">'
                        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
                        '</head><body style="margin:0">' + back + body + "</body></html>", mimetype="text/html")

    # ───────── 웹 푸시 ─────────
    def vapid():
        from .webpush import Vapid
        return Vapid(store.root / "vapid.pem")

    def clean_sub(d: dict) -> dict | None:
        ep, k, a = str(d.get("endpoint") or ""), str(d.get("p256dh") or ""), str(d.get("auth") or "")
        if not ep.startswith("https://") or len(ep) > 1000 or not (40 < len(k) < 200) or not (10 < len(a) < 60):
            return None
        return {"endpoint": ep, "p256dh": k, "auth": a}

    @app.get("/push/key")
    def push_key():
        return {"key": vapid().public_b64}

    @app.post("/push/subscribe")
    def push_subscribe():
        user, csrf = need_login()
        if request.headers.get("X-CSRF") != csrf:
            abort(400)
        d = request.get_json(silent=True) or {}
        sub = clean_sub(d)
        if not sub:
            abort(400)
        store.add_sub("u:" + user.uid, sub, str(d.get("ua") or ""))
        return {"ok": True}

    @app.post("/push/test")
    def push_test():
        from .webpush import send
        user, csrf = need_login()
        check_csrf(csrf)
        subs = store.subs("u:" + user.uid)
        if not subs:
            return account(msg="등록된 기기가 없습니다. 먼저 '이 기기에서 알림 받기'를 누르세요.")
        claim = store.kv_get("site_url") or "mailto:admin@example.com"
        ok = 0
        for s_ in subs:
            code, _ = send(vapid(), s_, {"title": "목장모니터 시험 알림", "body": "알림이 잘 옵니다 ✓", "url": "/", "tag": "test"}, contact=claim)
            store.sub_result(s_["endpoint"], 200 <= code < 300, gone=code in (404, 410))
            ok += 200 <= code < 300
        return account(ok=f"시험 알림을 보냈습니다 (기기 {ok}/{len(subs)}대). 몇 초~몇 분 안에 와야 합니다.") if ok else \
            account(msg="보내지 못했습니다. 알림을 다시 켠 뒤 시도하세요.")

    @app.post("/push/off")
    def push_off():
        user, csrf = need_login()
        check_csrf(csrf)
        store.del_subs("u:" + user.uid)
        return account(ok="이 계정의 알림을 모두 껐습니다.")

    @app.post("/admin/dispatch")
    def admin_dispatch():
        need_admin()
        store.kv_set("dispatch_enabled", "1" if request.form.get("enabled") else "0")
        store.kv_set("dispatch_test", "1" if request.form.get("test") else "0")
        return account(ok="자동 알림 설정을 저장했습니다.")

    @app.post("/admin/dispatch/now")
    def admin_dispatch_now():
        from ..dispatch import run as run_dispatch
        need_admin()
        slot = "pm" if request.form.get("slot") == "pm" else "am"
        if store.kv_get("dispatch_enabled", "0") != "1":
            return account(msg="자동 알림이 꺼져 있습니다. '자동 알림 켜기'를 먼저 저장하세요.")
        sm = run_dispatch(store, slot)
        return account(ok=f"{'아침' if slot == 'am' else '오후'} 알림 실행 — 담당자 {sm['contacts']}명 · 목장 {sm['farms']}건 · 전달 {sm['delivered']}명"
                          + (" (시험 모드: 관리자 기기로만)" if sm["test"] else ""))

    @app.post("/admin/receiver")
    def admin_receiver():
        need_admin()
        phone = re.sub(r"\D", "", request.form.get("phone") or "")
        if not re.fullmatch(r"0\d{8,10}", phone):
            abort(400)
        r = store.new_receiver(phone, (request.form.get("name") or "")[:20])
        link = request.host_url.rstrip("/") + "/c/" + r["token"]
        return account(ok=f"{r['name']} 님 알림 링크: {link}  ← 이 주소를 문자·카톡으로 보내 주세요(예전 링크는 끊김). "
                          "받는 분은 주소를 열어 홈 화면에 추가 → 알림 받기를 누르면 됩니다.")

    @app.post("/admin/receiver/revoke")
    def admin_receiver_revoke():
        need_admin()
        store.revoke_receiver(re.sub(r"\D", "", request.form.get("phone") or ""))
        return account(ok="링크를 끊었습니다. 그 담당자에게는 더 이상 알림이 가지 않습니다.")

    # ───────── 담당자 전용 페이지 (로그인 없이 링크로) ─────────
    def receiver_or_404(token: str) -> dict:
        r = store.receiver_by_token(token)
        if not r:
            abort(404)
        return r

    @app.get("/c/<token>/manifest.webmanifest")
    def receiver_manifest(token: str):
        receiver_or_404(token)
        m = {**MANIFEST, "name": "업무요청 — 목장모니터", "short_name": "업무요청", "start_url": f"/c/{token}",
             "scope": f"/c/{token}", "icons": [{**i, "src": "/" + i["src"]} for i in MANIFEST["icons"]]}
        return Response(json.dumps(m, ensure_ascii=False), mimetype="application/manifest+json")

    @app.get("/c/<token>")
    def receiver_page(token: str):
        r = receiver_or_404(token)
        ds = store.dispatches(phone=r["phone"], limit=6)
        blocks = []
        for d in ds:
            acked = store.acked(r["phone"], d["id"])
            fl = "".join(
                f"<li><b>{html.escape(f['name'])}</b> {html.escape(f['grade'])} {html.escape(f.get('note') or '')} "
                + ("<span class='st-ok'>완료 보고함</span>" if f["id"] in acked else
                   f"<button class='ghost' style='padding:3px 8px;font-size:13px' onclick=\"ack({d['id']},{html.escape(json.dumps(f['id']))})\">완료 보고</button>")
                + "</li>" for f in d["farms"])
            blocks.append(f"<div class='card'><h2>{d['day'][5:]} {'08시' if d['slot'] == 'am' else '14시'}"
                          f"{' <span class=sub>(시험)</span>' if d['test'] else ''}</h2><ul>{fl}</ul>"
                          f"<details><summary>요청 내용 전체</summary><pre style='white-space:pre-wrap;font-size:13px'>{html.escape(d['text'])}</pre></details></div>")
        n = len(store.subs("r:" + r["id"]))
        body = f"""<div class="card"><h1>{html.escape(r['name'] or '')} 님 업무요청</h1>
          <p class="sub">긴급·우선 목장 업무요청이 매일 08:00·14:00 이 휴대폰 알림으로 옵니다.</p>
          <p>알림 기기: <b>{n}대</b> <span id="pushst" class="sub"></span></p>
          <button type="button" onclick="smxSub('/c/{token}/subscribe')">이 휴대폰에서 알림 받기</button>
          <details style="margin-top:8px"><summary>처음 설정 방법</summary><ol class="sub">
            <li>아이폰: 이 화면을 <b>Safari</b>로 열기 → 아래 공유 버튼 → <b>홈 화면에 추가</b> → 홈 화면의 '업무요청' 아이콘으로 열기 → 위 버튼(iOS 16.4 이상)</li>
            <li>안드로이드: <b>크롬</b>으로 열기 → 위 버튼 → 알림 허용 (홈 화면 추가는 선택)</li>
            <li>이 주소는 본인 전용입니다. 다른 사람에게 보내지 마세요.</li></ol></details></div>
          {''.join(blocks) or '<div class="card sub">아직 받은 업무요청이 없습니다.</div>'}
          {PUSH_JS}
          <script>function ack(d,f){{fetch('/c/{token}/ack',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{did:d,farm_id:f}})}})
            .then(r=>{{if(r.ok)location.reload();else alert('저장 실패');}});}}</script>"""
        page = _page("업무요청", body, nav=False).replace('href="/manifest.webmanifest"', f'href="/c/{token}/manifest.webmanifest"')
        return Response(page, mimetype="text/html")

    @app.post("/c/<token>/subscribe")
    def receiver_subscribe(token: str):
        r = receiver_or_404(token)
        d = request.get_json(silent=True) or {}
        sub = clean_sub(d)
        if not sub:
            abort(400)
        if len(store.subs("r:" + r["id"])) >= 5:
            return {"ok": False}, 400
        store.add_sub("r:" + r["id"], sub, str(d.get("ua") or ""))
        return {"ok": True}

    @app.post("/c/<token>/ack")
    def receiver_ack(token: str):
        from datetime import date
        r = receiver_or_404(token)
        d = request.get_json(silent=True) or {}
        try:
            did, fid = int(d.get("did")), str(d.get("farm_id") or "")
        except (TypeError, ValueError):
            abort(400)
        if not store.ack(r["phone"], did, fid):
            abort(404)
        key = f"act:{fid}:d{did}"
        for u in store.users():      # 그 목장 담당 팀원의 조치 기록에 결과 자동 입력
            v = store.notes(u.uid).get(key)
            if isinstance(v, dict):
                store.set_note(u.uid, key, {**v, "res": "완료 보고", "rd": date.today().isoformat()})
        return {"ok": True}

    # ───────── 목장 담당자(딜러·판매과장) — 팀 공용 연락처 ─────────
    def user_farms(uid: str) -> dict[str, str]:
        import sqlite3
        p = store.user_dir(uid) / "data" / "smaxtec.db"
        if not p.exists():
            return {}
        c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            return {r[0]: r[1] for r in c.execute("SELECT farm_id, name FROM farms")}
        except sqlite3.Error:
            return {}
        finally:
            c.close()

    def clean_contact(it: dict) -> dict | None:
        name = str(it.get("name") or "").strip()[:20]
        role = str(it.get("role") or "").strip()[:12] or "담당"
        raw = it.get("phone")
        if isinstance(raw, float) and raw.is_integer():
            raw = int(raw)
        digits = re.sub(r"\D", "", str(raw or ""))
        if digits.startswith("82") and len(digits) in (11, 12):      # +82 10-… → 010-…
            digits = "0" + digits[2:]
        if re.fullmatch(r"1\d{8,9}", digits):                       # 엑셀 숫자 칸에서 앞 0 이 빠진 경우
            digits = "0" + digits
        if not name and not digits:
            return None
        if not re.fullmatch(r"0\d{8,10}", digits):
            raise ValueError(f"전화번호 확인: {it.get('phone')}")
        phone = (f"{digits[:3]}-{digits[3:-4]}-{digits[-4:]}" if digits.startswith("01")
                 else f"{digits[:-8]}-{digits[-8:-4]}-{digits[-4:]}" if len(digits) >= 10 else digits)
        return {"role": role, "name": name or role, "phone": phone}

    @app.get("/api/contacts")
    def contacts_get():
        user, _ = need_login()
        return store.contacts(list(user_farms(user.uid)))

    @app.post("/api/contacts")
    def contacts_set():
        user, csrf = need_login()
        if request.headers.get("X-CSRF") != csrf:
            abort(400)
        d = request.get_json(silent=True) or {}
        fid, items = str(d.get("farm_id") or ""), d.get("items")
        farms = user_farms(user.uid)
        if fid not in farms or not isinstance(items, list) or len(items) > 5:
            abort(400)
        try:
            clean = [c for c in (clean_contact(x) for x in items if isinstance(x, dict)) if c]
        except ValueError as e:
            return {"ok": False, "error": str(e)}, 400
        store.set_contacts(fid, clean, user.username, farms[fid])
        return {"ok": True, "items": clean}

    @app.get("/admin/contacts.xlsx")
    def admin_contacts_get():
        from datetime import date
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        user, _ = need_login()
        if not store.admin or user.username != store.admin:
            abort(403)
        farms: dict[str, str] = {}
        for u in store.users():
            for k, v in user_farms(u.uid).items():
                farms.setdefault(k, v)
        cts = store.contacts()
        wb = Workbook()
        ws = wb.active
        ws.title = "담당자"
        ws.append(["목장ID (바꾸지 마세요)", "목장", "구분 (딜러/판매과장 등)", "이름", "휴대폰"])
        for c in ws[1]:
            c.font, c.fill = Font(bold=True), PatternFill("solid", fgColor="DDEBF7")
        for fid, name in sorted(farms.items(), key=lambda x: x[1]):
            rows = cts.get(fid) or [{"role": "", "name": "", "phone": ""}]
            for r in rows:
                ws.append([fid, name, r["role"], r["name"], r["phone"]])
        for col, w in zip("ABCDE", (26, 24, 18, 14, 16)):
            ws.column_dimensions[col].width = w
        ws.freeze_panes = "C2"
        info = wb.create_sheet("쓰는 법")
        for i, t in enumerate(["목장마다 담당자를 한 줄씩 적습니다. 담당자가 둘이면 같은 목장을 한 줄 더 추가(목장ID 그대로 복사).",
                               "구분 예: 딜러, 판매과장, 수의사",
                               "휴대폰: 010-1234-5678 / 01012345678 / 010 1234 5678 모두 됩니다. 엑셀이 앞의 0을 지워 1012345678 이 되어도 자동으로 고칩니다.",
                               "올리면 파일에 있는 목장의 담당자를 이 내용으로 바꿉니다(파일에 없는 목장은 그대로).",
                               "연락처는 팀 공용 서버에 저장되고 그 목장을 담당하는 팀원 앱에만 보입니다."], 1):
            info.cell(row=i, column=1, value=t)
        info.column_dimensions["A"].width = 100
        p = store.user_dir(user.uid) / "reports" / "담당자.xlsx"
        p.parent.mkdir(parents=True, exist_ok=True)
        wb.save(p)
        return send_file(p, as_attachment=True, download_name=f"목장담당자_{date.today():%Y%m%d}.xlsx")

    @app.post("/admin/contacts")
    def admin_contacts_put():
        from openpyxl import load_workbook
        user = need_admin()
        f = request.files.get("file")
        if not f or not f.filename.lower().endswith(".xlsx"):
            return account(msg="엑셀(.xlsx) 파일을 선택하세요.")
        tmp = store.user_dir(user.uid) / "contacts.upload.xlsx"
        f.save(tmp)
        try:
            rows = list(load_workbook(tmp, read_only=True, data_only=True).worksheets[0].iter_rows(values_only=True))
        except Exception:  # noqa: BLE001
            return account(msg="엑셀을 읽지 못했습니다.")
        finally:
            tmp.unlink(missing_ok=True)
        if not rows or not str(rows[0][0] or "").startswith("목장ID"):
            return account(msg="'담당자 엑셀 받기'로 받은 파일에 작성해 올려 주세요.")
        by: dict[str, list] = {}
        names: dict[str, str] = {}
        errs = []
        for i, r in enumerate(rows[1:], 2):
            r = list(r) + [None] * 5
            fid = str(r[0] or "").strip()
            if not fid:
                continue
            names[fid] = str(r[1] or "")
            by.setdefault(fid, [])
            try:
                c = clean_contact({"role": r[2], "name": r[3], "phone": r[4]})
            except ValueError as e:
                errs.append(f"{i}행 {r[1]}: {e}")
                continue
            if c:
                by[fid].append(c)
        if errs:
            return account(msg="반영하지 않았습니다 — " + " / ".join(errs[:5]) + (f" 외 {len(errs) - 5}건" if len(errs) > 5 else ""))
        for fid, items in by.items():
            store.set_contacts(fid, items[:5], user.username, names.get(fid, ""))
        n = sum(len(v) for v in by.values())
        return account(ok=f"목장 {len(by)}곳 · 담당자 {n}명을 저장했습니다. 팀원 앱에서 바로 보입니다.")

    @app.get("/healthz")
    def health():
        return {"ok": True}

    return app

