"""python -m cowrec <명령>

  demo                 가상 데이터로 개체 기록부(엑셀·앱) 만들기
  collect [목장...]    smaXtec 메모·알림 코멘트 수집 (+ --events: 번식 이벤트도 smxmon 으로 수집)
  build [목장...]      목장별 엑셀 + 개체 기록부 앱 (reports/)   --memo-only: 메모 쓰는 목장만
설정·로그인은 smaxtec_monitor/.env 를 그대로 사용
"""
from __future__ import annotations

import argparse
import dataclasses
from datetime import datetime

from . import ROOT


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="cowrec")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("demo")
    c = sub.add_parser("collect")
    c.add_argument("farms", nargs="*")
    c.add_argument("--events", action="store_true", help="번식·알림 이벤트도 수집(smxmon collect)")
    b = sub.add_parser("build")
    b.add_argument("farms", nargs="*")
    b.add_argument("--memo-only", action="store_true")
    a = p.parse_args(argv)

    from smxmon.config import load_settings
    from smxmon.logging_setup import setup_logging
    s = load_settings()
    data, reports = ROOT / "data", ROOT / "reports"
    setup_logging(ROOT / "logs", s.secrets())
    from .build import build_all
    if a.cmd == "demo":
        from .demo import build as demo_build
        as_of = datetime.now().replace(minute=0, second=0, microsecond=0)
        demo_build(data / "demo_smx.db", data / "demo_cow.db", as_of)
        r = build_all(data / "demo_smx.db", data / "demo_cow.db", reports, as_of.date())
        print(f"데모: 목장 {r['farms']}곳 · 앱 {r['app']}")
        return 0
    if a.cmd == "collect":
        s.require_credentials()
        if a.events:
            from smxmon.collector import collect_once
            print("이벤트:", collect_once(s, farm_filter=a.farms or None)["status"])
        from .collect import collect_notes
        from .store import Store
        with Store(data / "cowrec.db") as st:
            print("메모:", collect_notes(s, st, a.farms or None))
        return 0
    if a.cmd == "build":
        r = build_all(s.db_path, data / "cowrec.db", reports, datetime.now().date(), a.farms or None, a.memo_only)
        print(f"목장 {r['farms']}곳 · 엑셀 {len(r['files'])}개 · 앱 {r['app']}")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
