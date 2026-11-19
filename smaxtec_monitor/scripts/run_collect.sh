#!/usr/bin/env bash
# Mac/Linux cron 용
cd "$(dirname "$0")/.." || exit 1
PY=python3; [ -x .venv/bin/python ] && PY=.venv/bin/python
exec "$PY" -m smxmon collect --notify
