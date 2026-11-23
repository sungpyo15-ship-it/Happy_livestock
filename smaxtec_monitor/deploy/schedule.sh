#!/usr/bin/env bash
# 자동 수집 간격 바꾸기 — 사용법: sudo smxmon-schedule 6h | 4h | 2h   (지금 설정 보기: sudo smxmon-schedule)
#   6h(기본): 06:30 · 12:30 · 18:30   — 하루 3번 (08:00·14:00 자동 알림 전에 판정 확정)
#   4h      : 06:30 · 10:30 · 12:30 · 16:30   — 하루 4번
#   2h      : 06:30 · 08:10~20:10 2시간마다   — 하루 8번
# 06:30 은 항상 포함(07시 전 판정 확정 → 08:00 알림). 밤에는 수집하지 않음.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "sudo smxmon-schedule 로 실행하세요"; exit 1; }
CONF=/etc/smxmon/schedule
DATA=${SMX_CLOUD_DATA:-/var/lib/smxmon}
cur=$(cat "$CONF" 2>/dev/null || echo 6h)
want=${1:-}
if [ -z "$want" ]; then echo "지금 설정: $cur  (바꾸기: sudo smxmon-schedule 6h|4h|2h)"; systemctl list-timers smxmon-collect.timer --no-pager | head -3; exit 0; fi
[ "$want" = apply ] && want=$cur
case "$want" in
  6h) cal=$'OnCalendar=*-*-* 06,12,18:30:00'; desc="매일 06:30 · 12:30 · 18:30 (알림 08:00·14:00)";;
  4h) cal=$'OnCalendar=*-*-* 06,10,12,16:30:00'; desc="매일 06:30 · 10:30 · 12:30 · 16:30 (알림 08:00·14:00)";;
  2h) cal=$'OnCalendar=*-*-* 06:30:00\nOnCalendar=*-*-* 08,10,12,14,16,18,20:10:00'; desc="매일 06:30, 08:10~20:10 2시간마다 (알림 08:00·14:00)";;
  *) echo "6h, 4h, 2h 중 하나를 적으세요"; exit 1;;
esac
echo "$want" > "$CONF"
cat > /etc/systemd/system/smxmon-collect.timer <<UNIT
[Unit]
Description=smaXtec 수집 시각 ($desc)
[Timer]
$cal
Persistent=true
[Install]
WantedBy=timers.target
UNIT
mkdir -p "$DATA"; echo "$desc" > "$DATA/schedule.txt"; chown smxmon:smxmon "$DATA/schedule.txt" 2>/dev/null || true
systemctl daemon-reload
systemctl enable smxmon-collect.timer >/dev/null 2>&1 || true
systemctl restart smxmon-collect.timer
echo "자동 수집: $desc"
echo "다음 수집: $(systemctl list-timers smxmon-collect.timer --no-pager 2>/dev/null | awk 'NR==2{print $1,$2,$3}')"
