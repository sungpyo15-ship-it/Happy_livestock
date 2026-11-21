#!/usr/bin/env bash
# 최신 코드로 업데이트 (사용자·데이터·설정은 그대로)  — 사용법: sudo smxmon-update
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "sudo smxmon-update 로 실행하세요"; exit 1; }
bash /opt/smxmon/fetch_code.sh
install -m 755 /opt/smxmon/app/smaxtec_monitor/deploy/fetch_code.sh /opt/smxmon/fetch_code.sh
install -m 755 /opt/smxmon/app/smaxtec_monitor/deploy/update.sh /usr/local/bin/smxmon-update
install -m 755 /opt/smxmon/app/smaxtec_monitor/deploy/check.sh /usr/local/bin/smxmon-check
bash /opt/smxmon/app/smaxtec_monitor/deploy/post_update.sh
systemctl restart smxmon-web
echo "업데이트 완료 — 웹앱 재시작됨 (다음 수집부터 새 기능 반영)"
