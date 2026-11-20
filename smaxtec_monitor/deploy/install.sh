#!/usr/bin/env bash
# smaXtec 목장 모니터 — 클라우드 서버 설치 (Oracle Cloud · Ubuntu 24.04)
# 사용법:  sudo bash install.sh <내주소.duckdns.org> <관리자 smaXtec 아이디(이메일)>
set -euo pipefail
DOMAIN="${1:-}"; ADMIN="${2:-}"
if [ -z "$DOMAIN" ] || [ -z "$ADMIN" ]; then
  echo "사용법: sudo bash install.sh 내주소.duckdns.org 관리자아이디@example.com"; exit 1; fi
[ "$(id -u)" = 0 ] || { echo "sudo 를 붙여 실행하세요"; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' || {
  echo "Python 3.11 이상 필요 → Ubuntu 24.04 이미지로 서버를 만드세요"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
export DEBIAN_FRONTEND=noninteractive

echo "[1/7] 기본 패키지"
apt-get update -y -q
apt-get install -y -q python3-venv unzip curl gnupg debian-keyring debian-archive-keyring apt-transport-https iptables-persistent
timedatectl set-timezone Asia/Seoul || true

echo "[2/7] HTTPS 웹서버(Caddy)"
if ! command -v caddy >/dev/null; then
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y -q && apt-get install -y -q caddy
fi

echo "[3/7] 프로그램"
id smxmon >/dev/null 2>&1 || useradd --system --home /var/lib/smxmon --shell /usr/sbin/nologin smxmon
mkdir -p /opt/smxmon /var/lib/smxmon /etc/smxmon
[ -d /opt/smxmon/venv ] || python3 -m venv /opt/smxmon/venv
if [ -f "$HERE/fetch_code.sh" ]; then bash "$HERE/fetch_code.sh"; else
  curl -fsSL "https://raw.githubusercontent.com/${SMX_REPO:-sungpyo15-ship-it/Happy_livestock}/refs/heads/${SMX_BRANCH:-claude/practical-cori-rbbhxw}/smaxtec_monitor/deploy/fetch_code.sh" | bash; fi
install -m 755 /opt/smxmon/app/smaxtec_monitor/deploy/fetch_code.sh /opt/smxmon/fetch_code.sh
install -m 755 /opt/smxmon/app/smaxtec_monitor/deploy/update.sh /usr/local/bin/smxmon-update

echo "[4/7] 설정·암호화 키"
if [ ! -f /etc/smxmon/env ]; then
  KEY="$(/opt/smxmon/venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
  umask 077
  cat > /etc/smxmon/env <<ENV
SMX_CLOUD_DATA=/var/lib/smxmon
SMX_CLOUD_KEY=$KEY
SMX_CLOUD_ADMIN=$ADMIN
SMX_TIMEZONE=Asia/Seoul
PYTHONDONTWRITEBYTECODE=1
ENV
fi
chmod 600 /etc/smxmon/env
chown -R smxmon:smxmon /var/lib/smxmon

echo "[5/7] 자동 실행(웹앱·수집 타이머)"
APP=/opt/smxmon/app/smaxtec_monitor
cat > /etc/systemd/system/smxmon-web.service <<UNIT
[Unit]
Description=smaXtec 목장 모니터 웹앱
After=network-online.target
[Service]
User=smxmon
WorkingDirectory=$APP
EnvironmentFile=/etc/smxmon/env
ExecStart=/opt/smxmon/venv/bin/python -m smxmon.cloud web --host 127.0.0.1 --port 8080
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/var/lib/smxmon
[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/smxmon-collect.service <<UNIT
[Unit]
Description=smaXtec 목장 모니터 수집(모든 사용자)
After=network-online.target
[Service]
Type=oneshot
User=smxmon
WorkingDirectory=$APP
EnvironmentFile=/etc/smxmon/env
ExecStart=/opt/smxmon/venv/bin/python -m smxmon.cloud collect-all
TimeoutStartSec=3h
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/var/lib/smxmon
UNIT
cat > /etc/systemd/system/smxmon-collect.timer <<UNIT
[Unit]
Description=smaXtec 수집 시각 (05:30 → 06시 전 완료, 이후 08:10~20:10 2시간마다)
[Timer]
OnCalendar=*-*-* 05:30:00
OnCalendar=*-*-* 08,10,12,14,16,18,20:10:00
Persistent=true
[Install]
WantedBy=timers.target
UNIT

echo "[6/7] HTTPS 주소($DOMAIN)·방화벽"
cat > /etc/caddy/Caddyfile <<CADDY
$DOMAIN {
	encode gzip
	reverse_proxy 127.0.0.1:8080
}
CADDY
for p in 80 443; do
  iptables -C INPUT -p tcp -m state --state NEW --dport $p -j ACCEPT 2>/dev/null || \
  iptables -I INPUT -p tcp -m state --state NEW --dport $p -j ACCEPT
done
netfilter-persistent save >/dev/null 2>&1 || true

echo "[7/7] 시작"
systemctl daemon-reload
systemctl enable --now smxmon-web.service smxmon-collect.timer
systemctl restart caddy
sleep 3
systemctl is-active --quiet smxmon-web && echo "  웹앱: 실행 중" || echo "  웹앱: 실패 → journalctl -u smxmon-web -n 50"
echo
echo "================================================================"
echo " 설치 완료.  휴대폰·PC 에서  https://$DOMAIN  을 여세요."
echo " 관리자($ADMIN) 로 로그인 → [계정] 화면에서 팀원 아이디를 등록하세요."
echo " (처음 HTTPS 인증서 발급에 1~2분 걸릴 수 있습니다)"
echo " 업데이트:  sudo smxmon-update"
echo "================================================================"
