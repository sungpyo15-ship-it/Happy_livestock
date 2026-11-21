#!/usr/bin/env bash
# 설치·업데이트 후 공통 설정 (여러 번 실행해도 안전) — install.sh·update.sh 가 호출
set -euo pipefail
D=/opt/smxmon/app/smaxtec_monitor/deploy
install -m 755 "$D/backup.sh" /usr/local/bin/smxmon-backup
install -m 755 "$D/restore.sh" /usr/local/bin/smxmon-restore
cat > /etc/systemd/system/smxmon-backup.service <<UNIT
[Unit]
Description=smaXtec monitor daily backup
[Service]
Type=oneshot
ExecStart=/usr/local/bin/smxmon-backup
Nice=10
UNIT
cat > /etc/systemd/system/smxmon-backup.timer <<UNIT
[Unit]
Description=smaXtec monitor daily backup (04:40)
[Timer]
OnCalendar=*-*-* 04:40:00
Persistent=true
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now smxmon-backup.timer >/dev/null 2>&1
if ! ls /var/backups/smxmon/smxmon_*.tar.gz >/dev/null 2>&1; then
  echo "첫 백업 실행..."; /usr/local/bin/smxmon-backup || echo "  ⚠ 첫 백업 실패 — sudo smxmon-backup 으로 다시 확인"
fi
