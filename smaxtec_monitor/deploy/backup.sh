#!/usr/bin/env bash
# smaXtec 목장 모니터 서버 백업 — 매일 04:40 자동 (sudo smxmon-backup 으로 수동 실행도 가능)
#  대상: /var/lib/smxmon 의 DB(사용자·메모·알림 이력)·투입기록 + /etc/smxmon(암호화 키·설정)
#  제외: reports·logs (수집 때 다시 만들어짐)
#  보관: /var/backups/smxmon  최근 14일 + 일요일분 8주  (root 만 읽기 가능)
#  ⚠ 같은 서버 디스크에 저장 → 서버 자체 손상 대비는 Google Cloud '스냅샷 일정'으로 따로 (docs/cloud_setup.html)
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "sudo smxmon-backup 로 실행하세요"; exit 1; }
DATA=${SMX_CLOUD_DATA:-/var/lib/smxmon}
OUT=${SMX_BACKUP_DIR:-/var/backups/smxmon}
ETC=${SMX_ETC:-/etc/smxmon}
KEEP_DAYS=${SMX_BACKUP_KEEP_DAYS:-14}
KEEP_WEEKS=${SMX_BACKUP_KEEP_WEEKS:-8}
umask 077
mkdir -p "$OUT"; chmod 700 "$OUT"
STAMP=$(date +%Y%m%d_%H%M)
WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT

# 디스크 여유 확인 (데이터 크기의 2배 이상)
NEED=$(( $(du -sk --exclude=reports --exclude=logs "$DATA" | cut -f1) * 2 ))
FREE=$(df -Pk "$OUT" | awk 'NR==2{print $4}')
if [ "$FREE" -lt "$NEED" ]; then echo "[백업 실패] 디스크 여유 부족 (필요 ${NEED}KB, 여유 ${FREE}KB)"; exit 1; fi

# DB는 SQLite 온라인 백업(수집 중이어도 안전), 나머지 파일은 복사
python3 - "$DATA" "$WORK/data" <<'PY'
import os, shutil, sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
n_db = 0
for root, dirs, files in os.walk(src):
    dirs[:] = [d for d in dirs if d not in ("reports", "logs", "__pycache__")]
    rel = os.path.relpath(root, src)
    for f in files:
        if f.endswith(("-wal", "-shm", "-journal")):
            continue
        s, d = os.path.join(root, f), os.path.join(dst, rel, f)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        if f.endswith(".db"):
            a, b = sqlite3.connect(s, timeout=60), sqlite3.connect(d)
            a.backup(b); b.close(); a.close()
            chk = sqlite3.connect(d).execute("PRAGMA quick_check").fetchone()[0]
            if chk != "ok":
                sys.exit(f"[백업 실패] 무결성 검사 실패: {s} ({chk})")
            n_db += 1
        else:
            shutil.copy2(s, d)
print(f"  DB {n_db}개 백업·무결성 확인")
PY
mkdir -p "$WORK/etc_smxmon"; [ -d "$ETC" ] && cp -a "$ETC/." "$WORK/etc_smxmon/"
FILE="$OUT/smxmon_$STAMP.tar.gz"
tar -czf "$FILE.part" -C "$WORK" data etc_smxmon && mv "$FILE.part" "$FILE"
chmod 600 "$FILE"
echo "  백업 완료: $FILE ($(du -h "$FILE" | cut -f1))"

# 보관 정리: 최근 KEEP_DAYS일은 전부, 그 이전은 일요일분만 KEEP_WEEKS주
now=$(date +%s)
for f in "$OUT"/smxmon_*.tar.gz; do
  [ -e "$f" ] || continue
  d=$(basename "$f" | sed -E 's/smxmon_([0-9]{8})_.*/\1/')
  ts=$(date -d "$d" +%s 2>/dev/null) || continue
  age=$(( (now - ts) / 86400 ))
  if [ "$age" -ge "$KEEP_DAYS" ]; then
    if [ "$(date -d "$d" +%u)" != 7 ] || [ "$age" -ge $(( KEEP_WEEKS * 7 )) ]; then rm -f "$f"; fi
  fi
done
# 같은 날 여러 개면 그날 마지막 것만 (수동 백업 반복 대비, 오늘 제외)
ls "$OUT"/smxmon_*.tar.gz 2>/dev/null | sed -E 's/.*smxmon_([0-9]{8})_.*/\1/' | sort | uniq -d | while read -r d; do
  [ "$d" = "$(date +%Y%m%d)" ] && continue
  ls "$OUT"/smxmon_"$d"_*.tar.gz | sort | head -n -1 | xargs -r rm -f
done
echo "  보관 중: $(ls "$OUT"/smxmon_*.tar.gz 2>/dev/null | wc -l)개 · $(du -sh "$OUT" | cut -f1)"
