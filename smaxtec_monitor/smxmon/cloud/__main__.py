"""python -m smxmon.cloud <명령>

  genkey            비밀번호 암호화 키 생성 (설치 시 1회)
  web               웹앱 실행 (waitress, 기본 127.0.0.1:8080 — 앞단 Caddy 가 HTTPS 처리)
  collect-all       모든 사용자 수집·리포트 (서버 타이머가 실행)
  allow 아이디 [메모]  팀원 허용 (관리자 화면에서도 가능)
  users             사용자·상태 목록

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
    elif args.cmd == "users":
        print("허용:", [r["username"] for r in store.allowed_list()], "관리자:", store.admin or "-")
        for u in store.users():
            print(f"  {u.username:<35} {u.status:<13} 목장 {u.farms or '-':>3}  마지막 정상 {u.last_ok or '-'}  {u.message or ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
