#!/usr/bin/env bash
# 설치·업데이트 후 공통 설정 (여러 번 실행해도 안전) — install.sh·update.sh 가 호출
set -euo pipefail
D=/opt/smxmon/app/smaxtec_monitor/deploy
install -m 755 "$D/backup.sh" /usr/local/bin/smxmon-backup
install -m 755 "$D/restore.sh" /usr/local/bin/smxmon-restore
install -m 755 "$D/schedule.sh" /usr/local/bin/smxmon-schedule
install -m 755 "$D/domain.sh" /usr/local/bin/smxmon-domain
bash "$D/schedule.sh" apply >/dev/null   # 수집 간격(기본 6시간) 적용
cat > /usr/local/bin/smxmon-validate <<'SH'
#!/usr/bin/env bash
# 판정 기준 검증 — 사용법: sudo smxmon-validate [--days 365] [--anon]
[ "$(id -u)" = 0 ] || { echo "sudo smxmon-validate 로 실행하세요"; exit 1; }
cd /opt/smxmon/app/smaxtec_monitor
set -a; . /etc/smxmon/env; set +a
exec nice -n 10 runuser -u smxmon -- /opt/smxmon/venv/bin/python -m smxmon.cloud validate "$@"
SH
chmod 755 /usr/local/bin/smxmon-validate
cat > /usr/local/bin/smxmon-push-test <<'SH'
#!/usr/bin/env bash
# 알림 점검 — 등록된 모든 기기에 점검 알림을 보내고 기기별 결과 표시. 사용법: sudo smxmon-push-test
[ "$(id -u)" = 0 ] || { echo "sudo smxmon-push-test 로 실행하세요"; exit 1; }
cd /opt/smxmon/app/smaxtec_monitor
set -a; . /etc/smxmon/env; set +a
exec runuser -u smxmon -- /opt/smxmon/venv/bin/python -m smxmon.cloud push-test
SH
chmod 755 /usr/local/bin/smxmon-push-test
# 자동 업무지시 알림 08:00(am) · 14:00(pm) — 켜고 끄기는 관리자 화면에서
for slot in am pm; do
  t=$([ $slot = am ] && echo "08:00:00" || echo "14:00:00")
  cat > /etc/systemd/system/smxmon-dispatch-$slot.service <<UNIT
[Unit]
Description=smaXtec 자동 업무지시 알림 ($slot)
After=network-online.target
[Service]
Type=oneshot
User=smxmon
WorkingDirectory=/opt/smxmon/app/smaxtec_monitor
EnvironmentFile=/etc/smxmon/env
ExecStart=/opt/smxmon/venv/bin/python -m smxmon.cloud dispatch --slot $slot
TimeoutStartSec=20min
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/var/lib/smxmon
UNIT
  cat > /etc/systemd/system/smxmon-dispatch-$slot.timer <<UNIT
[Unit]
Description=smaXtec 자동 업무지시 알림 ($slot)
[Timer]
OnCalendar=*-*-* $t
[Install]
WantedBy=timers.target
UNIT
done
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
systemctl enable --now smxmon-backup.timer smxmon-dispatch-am.timer smxmon-dispatch-pm.timer >/dev/null 2>&1
if ! ls /var/backups/smxmon/smxmon_*.tar.gz >/dev/null 2>&1; then
  echo "첫 백업 실행..."; /usr/local/bin/smxmon-backup || echo "  ⚠ 첫 백업 실패 — sudo smxmon-backup 으로 다시 확인"
fi
