#!/usr/bin/env bash
# 백업에서 되살리기 — 사용법
#   sudo smxmon-restore                      백업 목록 보기
#   sudo smxmon-restore <파일>               데이터(사용자·메모·알림 이력) 되살리기
#   sudo smxmon-restore <파일> --with-config 새 서버로 옮길 때: 암호화 키·설정(/etc/smxmon)까지 되살리기
#  되살리기 전 현재 데이터는 /var/lib/smxmon.before-restore-<시각> 으로 보관(지우지 않음)
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "sudo smxmon-restore 로 실행하세요"; exit 1; }
DATA=${SMX_CLOUD_DATA:-/var/lib/smxmon}
ETC=${SMX_ETC:-/etc/smxmon}
OUT=${SMX_BACKUP_DIR:-/var/backups/smxmon}
if [ $# -lt 1 ]; then
  echo "백업 목록 ($OUT):"; ls -lh "$OUT"/smxmon_*.tar.gz 2>/dev/null | awk '{print "  "$9"  "$5}' || echo "  없음"
  echo "되살리기: sudo smxmon-restore <위 파일 경로>"; exit 0
fi
FILE=$1; WITH_CFG=${2:-}
[ -f "$FILE" ] || { echo "파일 없음: $FILE"; exit 1; }
WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT
tar -xzf "$FILE" -C "$WORK"
[ -d "$WORK/data" ] || { echo "백업 파일 형식이 아닙니다"; exit 1; }
if [ "$WITH_CFG" != "--with-config" ] && [ -f $ETC/env ] && [ -f "$WORK/etc_smxmon/env" ] && \
   [ "$(grep '^SMX_CLOUD_KEY=' $ETC/env)" != "$(grep '^SMX_CLOUD_KEY=' "$WORK/etc_smxmon/env")" ]; then
  echo "⚠ 백업의 암호화 키가 지금 서버와 다릅니다(새 서버?). 저장된 smaXtec 비밀번호를 쓰려면 --with-config 를 붙이세요."
fi
read -r -p "지금 데이터를 $FILE 시점으로 되돌립니다. 계속할까요? (yes 입력) " ans
[ "$ans" = "yes" ] || { echo "취소"; exit 0; }
systemctl stop smxmon-collect.timer smxmon-collect.service smxmon-web 2>/dev/null || true
KEEP="$DATA.before-restore-$(date +%Y%m%d_%H%M)"
[ -d "$DATA" ] && mv "$DATA" "$KEEP" && echo "  현재 데이터 보관: $KEEP"
mkdir -p "$DATA"; cp -a "$WORK/data/." "$DATA/"
# reports·logs 는 백업에 없음 → 빈 폴더만 (다음 수집 때 다시 생성)
chown -R smxmon:smxmon "$DATA" 2>/dev/null || true
if [ "$WITH_CFG" = "--with-config" ]; then
  [ -d "$ETC" ] && cp -a "$ETC" "$ETC.before-restore-$(date +%Y%m%d_%H%M)"
  mkdir -p "$ETC"; cp -a "$WORK/etc_smxmon/." "$ETC/"; chmod 600 $ETC/env
  echo "  설정·암호화 키 복원 (도메인이 바뀌었다면 /etc/smxmon/duckdns 확인)"
fi
systemctl start smxmon-web smxmon-collect.timer
echo "되살리기 완료 — 앱에서 '계정 → 지금 새로고침'을 누르면 화면이 다시 만들어집니다."
