#!/usr/bin/env bash
# 앱 주소 바꾸기 — 사용법: sudo smxmon-domain 새주소.duckdns.org
#   예전 주소도 계속 열림(홈 화면 앱·딜러 링크·알림이 끊기지 않게). 예전 주소를 닫으려면: sudo smxmon-domain 새주소 --only
#   '.duckdns.org' 가 두 번 들어간 주소는 자동으로 하나로 정리
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "sudo smxmon-domain 새주소 로 실행하세요"; exit 1; }
NEW=$(echo "${1:-}" | tr 'A-Z' 'a-z'); ONLY=${2:-}
NEW=${NEW#https://}; NEW=${NEW#http://}; NEW=${NEW%%/*}
while [[ "$NEW" == *.duckdns.org.duckdns.org ]]; do NEW=${NEW%.duckdns.org}; done
if ! [[ "$NEW" =~ ^[a-z0-9-]+(\.[a-z0-9-]+)+$ ]]; then
  echo "주소 형식 확인: 예) sudo smxmon-domain mokjang-monitor.duckdns.org"; exit 1
fi
CF=/etc/caddy/Caddyfile
CUR=$(awk '/\{/{sub(/\{.*/,""); print; exit}' "$CF" | tr ',' ' ' | xargs)
echo "지금 주소: $CUR"
echo "새 주소  : $NEW"

# 1) DuckDNS 갱신 이름 정리(.duckdns.org 없이) → 새 주소가 이 서버를 가리키게
if [[ "$NEW" == *.duckdns.org ]] && [ -f /etc/smxmon/duckdns ]; then
  SUB=${NEW%.duckdns.org}
  sed -i "s/^SUB=.*/SUB=${SUB}/" /etc/smxmon/duckdns
  /usr/local/bin/smxmon-duckdns 2>/dev/null || true
  echo "  DuckDNS 갱신: $SUB → $(cat /var/log/smxmon-duckdns.log 2>/dev/null)"
fi

# 2) 새 주소가 이 서버 IP 인지 확인 (아니면 HTTPS 인증서를 못 받으므로 멈춤)
MYIP=$(curl -fsS -m 5 -H "Metadata-Flavor: Google" \
  http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip 2>/dev/null \
  || curl -fsS -m 5 https://api.ipify.org 2>/dev/null || true)
RIP=""
for i in 1 2 3 4 5 6; do
  RIP=$(getent ahostsv4 "$NEW" | awk 'NR==1{print $1}')
  [ -n "$RIP" ] && [ "$RIP" = "$MYIP" ] && break
  sleep 5
done
echo "  이 서버 IP: ${MYIP:-확인 불가} · 새 주소가 가리키는 IP: ${RIP:-없음}"
if [ -z "$RIP" ] || { [ -n "$MYIP" ] && [ "$RIP" != "$MYIP" ]; }; then
  echo "[멈춤] 새 주소가 아직 이 서버를 가리키지 않습니다. duckdns.org 에서 그 이름의 IP 가 ${MYIP:-서버 IP} 인지 확인한 뒤 다시 실행하세요."
  exit 1
fi

# 3) Caddy: 새 주소(+예전 주소) — 둘 다 자동 HTTPS
if [ "$ONLY" = "--only" ]; then ADDRS="$NEW"; else ADDRS=$(echo "$NEW $CUR" | tr ' ' '\n' | awk 'NF && !s[$0]++' | paste -sd, - | sed 's/,/, /g'); fi
cp "$CF" "$CF.bak.$(date +%Y%m%d_%H%M%S)"
cat > "$CF" <<CADDY
$ADDRS {
	encode gzip
	reverse_proxy 127.0.0.1:8080
}
CADDY
if command -v caddy >/dev/null && ! caddy validate --config "$CF" --adapter caddyfile >/dev/null 2>&1; then
  echo "[멈춤] 설정 오류 — 예전 설정으로 되돌림"; cp "$(ls -t $CF.bak.* | head -1)" "$CF"; exit 1
fi
systemctl reload caddy
echo "$NEW" > /etc/smxmon/domain

# 4) 새 주소로 HTTPS 열리는지 (인증서 발급에 수십 초 걸릴 수 있음)
for i in $(seq 1 12); do
  code=$(curl -s -o /dev/null -w "%{http_code}" -m 8 "https://$NEW/healthz" || true)
  [ "$code" = 200 ] && break
  sleep 5
done
if [ "$code" = 200 ]; then
  echo "완료: https://$NEW  (예전 주소도 열림: $([ "$ONLY" = "--only" ] && echo 아니오 || echo 예))"
  echo "이제 관리자 계정으로 새 주소에서 한 번 로그인하세요(딜러 링크·알림이 새 주소로 만들어짐)."
else
  echo "[확인 필요] 새 주소 HTTPS 응답: ${code:-없음} — 1~2분 뒤 브라우저로 https://$NEW 을 열어 보세요(인증서 발급 중일 수 있음)."
fi
