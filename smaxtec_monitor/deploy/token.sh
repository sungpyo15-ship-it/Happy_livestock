#!/usr/bin/env bash
# 비공개 GitHub 저장소용 읽기 전용 토큰 저장 — 사용법: sudo smxmon-token  (화면에 보이지 않게 붙여넣기)
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "sudo smxmon-token 으로 실행하세요"; exit 1; }
REPO="${SMX_REPO:-sungpyo15-ship-it/Happy_livestock}"
read -r -s -p "GitHub 토큰 붙여넣기(화면에 안 보임): " T; echo
T=$(echo "$T" | tr -d ' \r\n')
code=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $T" "https://api.github.com/repos/$REPO")
if [ "$code" != 200 ]; then echo "[실패] 토큰으로 저장소를 볼 수 없습니다(응답 $code). 저장소 선택·Contents 읽기 권한 확인"; exit 1; fi
mkdir -p /etc/smxmon; umask 077; echo "$T" > /etc/smxmon/github_token; chmod 600 /etc/smxmon/github_token
echo "저장 완료 — 이제 sudo smxmon-update 가 비공개 저장소에서 받습니다."
