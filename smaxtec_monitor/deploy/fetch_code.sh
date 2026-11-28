#!/usr/bin/env bash
# GitHub(공개 저장소)에서 최신 코드를 받아 /opt/smxmon/app 에 설치. install.sh·update.sh 가 사용
set -euo pipefail
REPO="${SMX_REPO:-sungpyo15-ship-it/Happy_livestock}"
BRANCH="${SMX_BRANCH:-claude/practical-cori-rbbhxw}"
TMP="$(mktemp -d)"
# 비공개 저장소: /etc/smxmon/github_token (읽기 전용 토큰) 이 있으면 그것으로 받음
TOKEN_FILE=/etc/smxmon/github_token
if [ -s "$TOKEN_FILE" ]; then
  curl -fsSL -H "Authorization: Bearer $(tr -d ' \r\n' < "$TOKEN_FILE")" -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/${REPO}/zipball/${BRANCH}" -o "$TMP/code.zip" \
    || { echo "코드 받기 실패 — 토큰 만료·권한(Contents: Read) 확인: sudo smxmon-token"; exit 1; }
else
  curl -fsSL "https://github.com/${REPO}/archive/refs/heads/${BRANCH}.zip" -o "$TMP/code.zip" \
    || { echo "코드 받기 실패(404) — 저장소가 비공개라면 읽기 전용 토큰 필요: sudo smxmon-token"; exit 1; }
fi
python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$TMP/code.zip" "$TMP"   # 한글 파일명 보존
SRC="$(find "$TMP" -maxdepth 2 -type d -name smaxtec_monitor | head -1)"
[ -n "$SRC" ] || { echo "코드를 찾지 못했습니다"; exit 1; }
rm -rf /opt/smxmon/app.new && mkdir -p /opt/smxmon/app.new
cp -r "$SRC" /opt/smxmon/app.new/
rm -rf /opt/smxmon/app.old; [ -d /opt/smxmon/app ] && mv /opt/smxmon/app /opt/smxmon/app.old
mv /opt/smxmon/app.new /opt/smxmon/app
rm -rf "$TMP"
/opt/smxmon/venv/bin/pip install -q --upgrade pip
/opt/smxmon/venv/bin/pip install -q -r /opt/smxmon/app/smaxtec_monitor/deploy/requirements-cloud.txt
echo "코드 설치 완료: $(date '+%F %T')"
