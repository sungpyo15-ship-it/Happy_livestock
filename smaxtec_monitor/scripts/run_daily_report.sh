#!/usr/bin/env bash
# 매일 06:00: 최신 수집 → 리포트·방문요약·대시보드
cd "$(dirname "$0")/.." || exit 1
PY=python3; [ -x .venv/bin/python ] && PY=.venv/bin/python
"$PY" -m smxmon collect
exec "$PY" -m smxmon report --email
