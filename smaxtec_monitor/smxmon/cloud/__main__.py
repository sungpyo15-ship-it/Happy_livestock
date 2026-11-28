"""python -m smxmon.cloud <명령>

  genkey            비밀번호 암호화 키 생성 (설치 시 1회)
  web               웹앱 실행 (waitress, 기본 127.0.0.1:8080 — 앞단 Caddy 가 HTTPS 처리)
  collect-all       모든 사용자 수집·리포트 (서버 타이머가 실행)
  allow 아이디 [메모]  팀원 허용 (관리자 화면에서도 가능)
  users             사용자·상태 목록
  dispatch --slot am|pm   자동 업무지시 알림 (서버 타이머가 08:00·14:00 실행)
  push-test         등록된 모든 알림 기기에 점검 알림을 보내고 기기별 결과 표시
  heat              발정 알림 (서버 타이머가 07:00·13:00·19:00 실행)
  validate [--days N] [--anon]  판정 기준 검증(모든 사용자 DB, 목장 중복 제외) → 관리자 '파일 받기'에 판정검증 엑셀

환경변수 (/etc/smxmon/env)
  SMX_CLOUD_DATA   데이터 폴더 (기본 /var/lib/smxmon)
  SMX_CLOUD_KEY    암호화 키 (genkey 결과) — 잃어버리면 저장된 비밀번호를 풀 수 없음(다시 로그인하면 됨)
  SMX_CLOUD_ADMIN  관리자 smaXtec 아이디 (관리자 화면·팀원 등록 권한)
"""
from __future__ import annotations

import argparse
import dataclasses
import os
import sys
from pathlib import Path

from ..config import load_settings
from ..logging_setup import setup_logging


def _store():
    from .store import CloudStore
    key = os.getenv("SMX_CLOUD_KEY", "").strip()
    if not key:
        sys.exit("SMX_CLOUD_KEY 가 없습니다 (python -m smxmon.cloud genkey 로 만들어 /etc/smxmon/env 에 저장)")
    return CloudStore(Path(os.getenv("SMX_CLOUD_DATA", "/var/lib/smxmon")), key, os.getenv("SMX_CLOUD_ADMIN", ""))


def _base():
    s = load_settings()
    root = Path(os.getenv("SMX_CLOUD_DATA", "/var/lib/smxmon"))
    return dataclasses.replace(s, mode="api", username="", password="", api_token="", log_dir=root / "logs",
                               min_delay=1.0, max_delay=2.5)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="smxmon.cloud")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("genkey")
    w = sub.add_parser("web")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8080)
    w.add_argument("--insecure-cookie", action="store_true", help="HTTPS 없이 시험할 때만")
    sub.add_parser("collect-all")
    a = sub.add_parser("allow")
    a.add_argument("username")
    a.add_argument("note", nargs="?", default="")
    sub.add_parser("users")
    sub.add_parser("push-test")
    sub.add_parser("heat")
    dp = sub.add_parser("dispatch")
    dp.add_argument("--slot", choices=["am", "pm"], required=True)
    v = sub.add_parser("validate")
    v.add_argument("--days", type=int, default=365)
    v.add_argument("--anon", action="store_true", help="엑셀 목장 이름을 목장01… 로")
    v.add_argument("--no-fetch", action="store_true", help="농장 피드백(알림 확인·메모) 새로 받지 않음")
    args = p.parse_args(argv)

    if args.cmd == "genkey":
        from cryptography.fernet import Fernet
        print(Fernet.generate_key().decode())
        return 0
    store, base = _store(), _base()
    setup_logging(base.log_dir, [])
    if args.cmd == "web":
        from waitress import serve
        from .web import create_app
        serve(create_app(store, base, secure_cookie=not args.insecure_cookie), host=args.host, port=args.port,
              threads=8, trusted_proxy="127.0.0.1", trusted_proxy_headers={"x-forwarded-for", "x-forwarded-proto",
                                                                           "x-forwarded-host"}, clear_untrusted_proxy_headers=True)
    elif args.cmd == "collect-all":
        from .jobs import run_all
        print(run_all(base, store))
    elif args.cmd == "allow":
        store.allow(args.username, args.note)
        print("허용:", args.username.lower())
    elif args.cmd == "heat":
        from ..heat_dispatch import run as run_heat
        print(run_heat(store))
    elif args.cmd == "push-test":
        import sqlite3
        from urllib.parse import urlparse
        from .webpush import Vapid, send
        c = sqlite3.connect(store.path)
        c.row_factory = sqlite3.Row
        rows = [dict(r) for r in c.execute("SELECT * FROM push_subs ORDER BY owner, created")]
        users = {"u:" + u.uid: u.username for u in store.users()}
        rcv = {"r:" + r["id"]: f"담당자 {r['name']}" for r in c.execute("SELECT id, name FROM receivers WHERE revoked=0")}
        c.close()
        if not rows:
            print("등록된 알림 기기가 없습니다. 앱 → 계정 → '이 기기에서 알림 받기'")
            return 0
        v, claim = Vapid(store.root / "vapid.pem"), store.kv_get("site_url") or "mailto:admin@example.com"
        print(f"알림 기기 {len(rows)}대 (보낸 쪽 주소 {claim})")
        for r in rows:
            who = users.get(r["owner"]) or rcv.get(r["owner"]) or f"(끊긴 링크 {r['owner']})"
            ua = r.get("ua") or ""
            dev = "아이폰" if "iPhone" in ua else "아이패드" if "iPad" in ua else "안드로이드" if "Android" in ua else "PC/기타"
            code, msg = send(v, r, {"title": "[점검] 목장모니터 알림", "body": f"{who} · 이 알림이 보이면 정상입니다", "url": "/",
                                    "tag": "pushtest"}, contact=claim)
            store.sub_result(r["endpoint"], 200 <= code < 300, gone=code in (404, 410))
            res = ("보냄(정상)" if 200 <= code < 300 else "등록 사라짐 → 앱에서 알림 다시 켜기(이 기기 기록 삭제함)" if code in (404, 410)
                   else f"실패 {code} {msg[:60]}")
            print(f"  - {who:<28} {dev:<6} 등록 {str(r.get('created') or '')[:16]} · 마지막 성공 {str(r.get('last_ok') or '-')[:16]}"
                  f" · {urlparse(r['endpoint']).netloc} → {res}")
        print("휴대폰에 '[점검] 목장모니터 알림'이 왔는지 확인하세요. '보냄(정상)'인데 안 오면 휴대폰 알림 설정·집중 모드 확인.")
    elif args.cmd == "dispatch":
        from ..dispatch import run as run_dispatch
        print(run_dispatch(store, args.slot))
    elif args.cmd == "validate":
        import sqlite3
        from datetime import datetime
        from ..validate import run
        users = store.users()
        adm = store.get_by_username(store.admin) if store.admin else None
        users.sort(key=lambda u: (adm is None or u.uid != adm.uid, u.username))   # 관리자 DB 먼저
        sources = []
        fetched: set[str] = set()
        for u in users:
            p = store.user_dir(u.uid) / "data" / "smaxtec.db"
            if not p.exists():
                continue
            if not args.no_fetch:       # 농장 피드백(알림 확인 처리·질병 메모) 받기 — 이 사용자 계정으로
                from ..api_client import SmaxtecAPI
                from ..feedback import fetch
                from .jobs import user_settings
                s = user_settings(base, store, u.uid)
                w = sqlite3.connect(p, timeout=120)
                farms = [r[0] for r in w.execute("SELECT farm_id FROM farms") if r[0] not in fetched]
                if s is not None and farms:
                    try:
                        print(f"사용자{len(sources) + 1}: 농장 피드백 받는 중 (목장 {len(farms)}곳)")
                        st = fetch(SmaxtecAPI(s), w, farms, s.timezone, progress=print)
                        print(f"  완료: 알림 확인 {st['todos']}건 · 새 메모 {st['new_notes']}건 · 실패 {st['errors']}곳")
                        fetched.update(farms)
                    except Exception as e:  # noqa: BLE001  (로그인 실패 등 → 받아 둔 자료로 계속)
                        print(f"  농장 피드백 받기 실패(받아 둔 자료로 계속): {e}")
                w.close()
            sources.append((f"사용자{len(sources) + 1}", sqlite3.connect(f"file:{p}?mode=ro", uri=True)))
        if not sources:
            print("DB 가 없습니다")
            return 1
        out_dir = store.user_dir((adm or users[0]).uid) / "reports"
        stamp = f"{datetime.now():%Y%m%d_%H%M}"
        for old in list(out_dir.glob("판정검증_2*.xlsx")) + list(out_dir.glob("판정검증익명_*.xlsx")):
            old.unlink()          # 지난 결과는 지움(최신 1개만)
        out, text = run(sources, out_dir / f"판정검증_{stamp}.xlsx", days=args.days, anon=args.anon, progress=print,
                        anon_copy=out_dir / f"판정검증익명_{stamp}.xlsx")
        print(text)
        (out_dir / "판정검증_요약.txt").write_text(text, encoding="utf-8")
        print(f"\n엑셀: {out}\n앱 → 계정 → '판정검증(엑셀)' 에서 받을 수 있습니다 (관리자 계정).")
    elif args.cmd == "users":
        print("허용:", [r["username"] for r in store.allowed_list()], "관리자:", store.admin or "-")
        for u in store.users():
            print(f"  {u.username:<35} {u.status:<13} 목장 {u.farms or '-':>3}  마지막 정상 {u.last_ok or '-'}  {u.message or ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
