#!/usr/bin/env bash
# GitHub(공개 저장소)에서 최신 코드를 받아 /opt/smxmon/app 에 설치. install.sh·update.sh 가 사용
set -euo pipefail
REPO="${SMX_REPO:-sungpyo15-ship-it/Happy_livestock}"
BRANCH="${SMX_BRANCH:-claude/practical-cori-rbbhxw}"
TMP="$(mktemp -d)"
curl -fsSL "https://github.com/${REPO}/archive/refs/heads/${BRANCH}.zip" -o "$TMP/code.zip"
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
