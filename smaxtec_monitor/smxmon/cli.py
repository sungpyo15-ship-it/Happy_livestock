"""명령행 진입점:  python -m smxmon <명령>

  api-test  공식 API 로그인·목장목록·이벤트 점검 (저장 안 함)
  init      폴더·DB 생성, 설정 점검
  inspect   화면 HTML/스크린샷/API 응답 저장 (선택자 채우기용)
  collect   1회 수집 (스케줄러가 1~2시간마다 실행)
  report    일일 요약 엑셀 생성 (+ --email 메일 발송)
  visit     목장 방문용 1장 요약 (예: visit 정진 학운)
  serve     아이폰 앱용 PC 내부 웹서버 (Tailscale 과 함께 사용)
  status    목장 ID·최근 수집 상태 확인
  demo      가상 데이터로 리포트 미리보기
"""
from __future__ import annotations

import argparse
import dataclasses
import logging
import sys
from datetime import datetime, timedelta

from .config import BASE_DIR, ConfigError, load_farm_overrides, load_settings, load_toml
from .logging_setup import setup_logging

log = logging.getLogger("smxmon")


def _parse_as_of(v: str | None, tz: str) -> datetime:
    from .collector import now_local
    if not v:
        return now_local(tz)
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            pass
    raise SystemExit("--as-of 형식: 'YYYY-MM-DD HH:MM'")


def cmd_init(s, args) -> int:
    for d in (s.data_dir, s.report_dir, s.log_dir):
        d.mkdir(parents=True, exist_ok=True)
    from .db import Database
    Database(s.db_path).close()
    env = BASE_DIR / ".env"
    print(f"DB 준비: {s.db_path}")
    print(f".env: {'있음' if env.exists() else '없음 → .env.example 을 복사해 .env 로 저장 후 값 입력'}")
    print(f"로그인 정보: {'입력됨' if s.username and s.password else '미입력'}")
    sel = load_toml("selectors.toml")
    todo = [f"[{sec}] {k}" for sec, d in sel.items() if isinstance(d, dict)
            for k, v in d.items() if isinstance(v, str) and v.upper().startswith("TODO")]
    print(f"선택자 미확정 {len(todo)}개" + (":\n  " + "\n  ".join(todo) if todo else " (모두 입력됨)"))
    return 0


def cmd_inspect(s, args) -> int:
    from .scraper import SmaxtecScraper
    out = s.data_dir / "inspect" / datetime.now().strftime("%Y%m%d_%H%M%S")
    sel = load_toml("selectors.toml")
    if args.manual:  # 브라우저 창을 띄워 사용자가 직접 로그인 → 이후 페이지 저장
        s = dataclasses.replace(s, headless=False)
    with SmaxtecScraper(s, sel) as sc:
        if args.manual:
            sc.page.goto(s.login_url)
            input("브라우저에서 직접 로그인한 뒤, 저장할 화면까지 이동하고 Enter 를 누르세요...")
            sc.context.storage_state(path=str(s.auth_state_path))
            urls = [sc.page.url] + list(args.urls)
        else:
            sc.ensure_logged_in()
            urls = list(args.urls) or [sc.page.url]
        files = sc.inspect(urls, out)
    print(f"저장 위치: {out}")
    print("※ 저장된 HTML/스크린샷/JSON 에는 목장·개체 정보가 포함됩니다. 공유 전 목장명·개체번호를 가리거나,"
          " 필요한 부분(목록 1~2행)만 잘라서 주세요.")
    for f in files:
        print("  -", f.name)
    return 0


def cmd_api_test(s, args) -> int:
    """API 접속 점검: 로그인 → 목장 목록 → 최근 24시간 이벤트 종류별 건수. DB 에 저장하지 않음."""
    from collections import Counter

    from .api_client import SmaxtecAPI
    from .collector import now_local
    from .normalize import AlertTypeMapper
    s.require_credentials()
    mapper = AlertTypeMapper(load_toml("alert_types.toml"))
    api = SmaxtecAPI(s)
    print(f"API: {api.base}  인증방식: {'API 토큰' if s.api_token else '아이디/비밀번호'}")
    farms = api.list_farms()
    print(f"\n✔ 로그인 성공 / 접근 가능한 목장 {len(farms)}곳")
    for f in farms:
        m = api.org_meta.get(f.farm_id, {})
        print(f"  {f.farm_id}  {f.name}  (권한: {m.get('role', '-')}, 시간대: {m.get('timezone', '-')})")
    now = now_local(s.timezone)
    shown = 0
    for f in farms:
        if shown >= args.limit:
            break
        if not api.list_animals(f.farm_id):
            continue  # 빈 조직(개인 계정 등)은 건너뜀
        shown += 1
        ev = api.list_events(f.farm_id, now - timedelta(hours=args.hours))
        c = Counter(ev.event_type for ev in ev)
        print(f"\n[{f.name}] 최근 {args.hours}시간 이벤트 {len(ev)}건")
        for code, n in c.most_common():
            std = mapper.map_event(code)
            print(f"  {code:<24} {n:>4}건  → {mapper.label(std) if std else '❓ 미분류'}")
    print(f"\nAPI 호출 {api.calls}회. 이 결과(목장명 포함)를 그대로 보내주시면 됩니다.")
    return 0


def cmd_collect(s, args) -> int:
    from .collector import collect_once
    stats = collect_once(s, farm_filter=args.farm)
    if args.notify and stats["status"] != "failed":
        _notify_urgent(s)
    return 0 if stats["status"] in ("ok", "partial") else 2


def _notify_urgent(s) -> None:
    """수집 직후 최근 3시간 안에 새로 생긴 집단신호만 즉시 알림(같은 건 1회만)."""
    from .analysis import analyze
    from .collector import now_local
    from .db import Database
    from .normalize import AlertTypeMapper
    from .notify import send_email
    rules, mapper = load_toml("rules.toml"), AlertTypeMapper(load_toml("alert_types.toml"))
    now = now_local(s.timezone)
    with Database(s.db_path) as db:
        an = analyze(db.conn, now, mapper, rules, load_farm_overrides())
        for c in an.clusters:
            if c.window_end < now - timedelta(hours=3) or db.was_notified(c.key()):
                continue
            subject = f"[긴급] {c.farm_name} {c.n_animals}두 집단 알림"
            body = (f"{c.farm_name}: {c.window_start:%m/%d %H:%M}~{c.window_end:%H:%M} 사이 {c.n_animals}두\n"
                    f"유형별: {c.animals_by_type}, pH저하 {c.ph_low_count}건\n\n{c.interpretation}\n\n"
                    "※ 센서 알림 기반 의심 신호(진단 아님)")
            log.warning("긴급 신호: %s", subject)
            if send_email(s, subject, body) or not s.email_enabled:
                db.mark_notified(c.key(), now, subject)


def cmd_report(s, args) -> int:
    from .notify import send_email
    from .outputs import generate
    as_of = _parse_as_of(args.as_of, s.timezone)
    o = generate(s, as_of, db_path=args.db, out=args.out, trials_path=getattr(args, "trials", None))
    an = o.an
    print(o.text)
    print(f"\n엑셀 저장: {o.daily}")
    if o.visit:
        print(f"방문요약: {o.visit}")
    if o.trials:
        print(f"투입비교({len(an.trials)}건): {o.trials}")
    print(f"목장 모니터 앱: {o.dashboard.parent / 'smaxtec_app.html'}  (5_OPEN_APP.bat 로 열기)")
    _publish(s, o.files)
    if args.email:
        send_email(s, f"[smaXtec 일일요약] {as_of:%m/%d} 방문우선 "
                      f"{sum(f.grade in ('긴급', '우선') for f in an.farms)}곳", o.text, o.files)
    return 0


def _publish(s, files) -> None:
    """SMX_PUBLISH_DIR(예: OneDrive/Google Drive 동기화 폴더)로 복사 → 휴대폰 앱에서 열람.
    앱(대시보드)은 항상 같은 이름(smaxtec_app.html)으로도 덮어써 즐겨찾기 1개로 최신본 확인."""
    import shutil
    if not s.publish_dir:
        return
    s.publish_dir.mkdir(parents=True, exist_ok=True)
    for f in files:
        shutil.copy2(f, s.publish_dir / f.name)
        if f.suffix == ".html":
            shutil.copy2(f, s.publish_dir / "smaxtec_app.html")
    log.info("공유폴더로 복사: %s (%d개)", s.publish_dir, len(files))


def cmd_visit(s, args) -> int:
    """지정 목장(이름 일부·ID) 방문요약 1장 생성."""
    from .analysis import analyze
    from .db import Database
    from .normalize import AlertTypeMapper
    from .visit import build_visit_workbook
    db_path = args.db or s.db_path
    as_of = _parse_as_of(args.as_of, s.timezone)
    rules, mapper = load_toml("rules.toml"), AlertTypeMapper(load_toml("alert_types.toml"))
    with Database(db_path) as db:
        an = analyze(db.conn, as_of, mapper, rules, load_farm_overrides())
    picked = [f for f in an.farms if any(k == f.farm_id or k in f.name for k in args.farms)]
    if not picked:
        raise ConfigError(f"목장을 찾지 못함: {args.farms} (python -m smxmon status 로 이름 확인)")
    out = s.report_dir / f"방문요약_{'_'.join(f.name.split('(')[0] for f in picked)[:40]}_{as_of:%Y%m%d}.xlsx"
    build_visit_workbook(an, picked, mapper, out)
    print(f"방문요약 {len(picked)}곳: {out}")
    return 0


def cmd_serve(s, args) -> int:
    """아이폰 앱용 PC 내부 웹서버 (기본 127.0.0.1 → Tailscale serve 로만 외부 기기 접속)."""
    from .pwa import serve
    serve(s.report_dir / "app", args.host, args.port)
    return 0


def cmd_trials_file(s, args) -> int:
    """투입기록.xlsx 열기(없으면 양식 생성)."""
    import os
    from .trials import TRIALS_FILE, ensure_template
    p = ensure_template(TRIALS_FILE)
    print(f"투입기록 파일: {p}")
    if hasattr(os, "startfile"):
        os.startfile(p)  # noqa: S606  (Windows: 엑셀로 열기)
    return 0


def cmd_status(s, args) -> int:
    from .db import Database
    with Database(s.db_path) as db:
        print("목장 ID / 이름 / 두수 / 마지막 정상수집")
        for r in db.conn.execute("SELECT * FROM farms ORDER BY name"):
            print(f"  {r['farm_id']:>10}  {r['name']}  {r['herd_size'] or '-'}  {r['last_ok_at'] or '없음'}")
        print("\n최근 수집")
        for r in db.conn.execute("SELECT * FROM collection_runs ORDER BY run_id DESC LIMIT 10"):
            print(f"  #{r['run_id']} {r['started_at']} {r['status']:>7} 목장 {r['farms_ok']}/{r['farms_ok'] + r['farms_failed']}"
                  f" 알림 {r['alerts_seen']} 신규 {r['alerts_new']} 해제 {r['alerts_resolved']} {r['error'] or ''}")
    return 0


def cmd_demo(s, args) -> int:
    from .collector import now_local
    from .demo import build_demo
    as_of = now_local(s.timezone).replace(minute=0, second=0) - timedelta(minutes=1)
    db_path = s.data_dir / "demo.db"
    build_demo(db_path, as_of)
    from .demo import build_demo_trials
    args.trials = build_demo_trials(s.data_dir / "demo_투입기록.xlsx", as_of)
    print(f"가상 DB 생성: {db_path}")
    args.db, args.as_of, args.email = db_path, as_of.strftime("%Y-%m-%d %H:%M"), False
    args.out = s.report_dir / f"DEMO_smaxtec_daily_{as_of:%Y%m%d}.xlsx"
    return cmd_report(s, args)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="smxmon", description="smaXtec 목장별 알림 자동 집계")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    pi = sub.add_parser("inspect")
    pi.add_argument("urls", nargs="*", help="저장할 페이지 URL(여러 개 가능)")
    pi.add_argument("--manual", action="store_true", help="브라우저 창에서 직접 로그인 (선택자 입력 전)")
    pa = sub.add_parser("api-test", help="공식 API 접속 점검(저장 안 함)")
    pa.add_argument("--hours", type=int, default=24)
    pa.add_argument("--limit", type=int, default=3, help="이벤트를 조회할 목장 수")
    pc = sub.add_parser("collect")
    pc.add_argument("--farm", nargs="*", help="특정 목장 ID/이름만 수집(테스트용)")
    pc.add_argument("--notify", action="store_true", help="집단신호 발생 시 즉시 메일")
    pr = sub.add_parser("report")
    pr.add_argument("--as-of", help="기준시각 'YYYY-MM-DD HH:MM' (기본: 지금)")
    pr.add_argument("--email", action="store_true")
    pr.add_argument("--db", type=lambda x: __import__("pathlib").Path(x))
    pr.add_argument("--out", type=lambda x: __import__("pathlib").Path(x))
    pv = sub.add_parser("visit", help="목장 방문용 1장 요약")
    pv.add_argument("farms", nargs="+", help="목장 이름 일부 또는 ID (여러 개 가능)")
    pv.add_argument("--as-of")
    pv.add_argument("--db", type=lambda x: __import__("pathlib").Path(x))
    ps = sub.add_parser("serve", help="아이폰 앱용 PC 내부 웹서버")
    ps.add_argument("--host", default="127.0.0.1", help="기본 127.0.0.1 (바꾸지 않는 것을 권장)")
    ps.add_argument("--port", type=int, default=8765)
    sub.add_parser("trials-file", help="투입기록.xlsx 열기(사료·첨가제 투입 전후 비교 입력)")
    sub.add_parser("status")
    pd = sub.add_parser("demo")
    pd.set_defaults(db=None, out=None)
    args = p.parse_args(argv)

    s = load_settings()
    setup_logging(s.log_dir, s.secrets(), args.verbose)
    handler = {"api-test": cmd_api_test, "init": cmd_init, "inspect": cmd_inspect, "collect": cmd_collect, "report": cmd_report,
               "status": cmd_status, "visit": cmd_visit, "serve": cmd_serve,
               "trials-file": cmd_trials_file, "demo": cmd_demo}[args.cmd]
    try:
        return handler(s, args)
    except ConfigError as e:
        log.error("설정 확인 필요: %s", e)
        return 1
    except RuntimeError as e:
        log.error("%s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
