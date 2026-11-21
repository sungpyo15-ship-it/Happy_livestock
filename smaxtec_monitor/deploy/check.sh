#!/usr/bin/env bash
# smaXtec 목장 모니터 서버 점검 (읽기 전용 — 아무것도 바꾸지 않음)
# 사용법:  curl -fsSL <이 파일 주소> | sudo bash      또는   sudo smxmon-check
APP=${SMX_APP:-/opt/smxmon/app/smaxtec_monitor}
DATA=${SMX_CLOUD_DATA:-/var/lib/smxmon}
ok(){ printf '  \033[32m[정상]\033[0m %s\n' "$1"; }
ng(){ printf '  \033[31m[확인]\033[0m %s\n' "$1"; }
echo "========== smaXtec 서버 점검 $(date '+%F %T') =========="

echo "[1] 프로그램 버전"
if grep -q '^long_days' "$APP/config/rules.toml" 2>/dev/null; then ok "최신(12개월 전환기 포함)"; else ng "예전 버전 → sudo smxmon-update"; fi
if grep -q 'monthTable' "$APP/smxmon/dashboard.py" 2>/dev/null; then ok "누적·월별 종류별 펼침 포함"; else ng "종류별 펼침 없음 → sudo smxmon-update"; fi
if [ -f "$APP/config/actions.toml" ]; then ok "조치 가이드 포함"; else ng "조치 가이드 없음 → sudo smxmon-update"; fi
if [ -f "$APP/smxmon/validate.py" ]; then ok "판정 검증 도구 포함 (sudo smxmon-validate)"; else ng "판정 검증 없음 → sudo smxmon-update 두 번"; fi
echo "    코드 설치 시각: $(stat -c %y "$APP/config/rules.toml" 2>/dev/null | cut -d. -f1)"

B=$(ls -t /var/backups/smxmon/smxmon_*.tar.gz 2>/dev/null | head -1)
if [ -n "$B" ] && [ $(( ($(date +%s) - $(stat -c %Y "$B")) / 3600 )) -lt 30 ]; then ok "백업: 최근 $(stat -c %y "$B" | cut -c1-16) · $(ls /var/backups/smxmon/smxmon_*.tar.gz | wc -l)개 보관"
elif [ -n "$B" ]; then ng "백업이 30시간 넘게 없음 → sudo smxmon-backup"; else ng "백업 없음 → sudo smxmon-update 한 번 더"; fi

echo "[2] 서비스"
for s in smxmon-web caddy; do systemctl is-active --quiet $s && ok "$s 실행 중" || ng "$s 꺼짐 → sudo systemctl restart $s"; done
systemctl is-active --quiet smxmon-collect.timer && ok "자동 수집 타이머 켜짐" || ng "자동 수집 타이머 꺼짐"
echo "    다음 자동 수집: $(systemctl list-timers smxmon-collect.timer --no-pager 2>/dev/null | awk 'NR==2{print $1,$2,$3}')"
systemctl is-active --quiet smxmon-collect.service && echo "    지금 수집 진행 중"

echo "[3] 사용자·수집 상태"
python3 - "$DATA" <<'PY'
import sqlite3, sys, glob, os
root = sys.argv[1]
def ro(p): return sqlite3.connect(f"file:{p}?mode=ro", uri=True)
try:
    c = ro(f"{root}/cloud.db")
    users = c.execute("select uid, username, status, farms, last_ok, coalesce(message,'') from users order by username").fetchall()
except Exception as e:
    print("   사용자 DB 를 읽지 못함:", e); sys.exit()
names = {"ok": "정상", "running": "수집 중", "pending": "대기", "failed": "오류", "login_failed": "다시 로그인 필요"}
for uid, u, st, farms, last_ok, msg in users:
    print(f"  - {u}: {names.get(st, st)} · 목장 {farms or '-'}곳 · 마지막 정상 {last_ok or '없음'} {msg}")
    db = f"{root}/users/{uid}/data/smaxtec.db"
    if not os.path.exists(db):
        print("      (아직 수집 데이터 없음)"); continue
    d = ro(db)
    hist = dict(d.execute("select value, count(*) from collect_state where key='history_days' group by value").fetchall())
    first = d.execute("select min(occurred_at) from alerts").fetchone()[0]
    n_alert = d.execute("select count(*) from alerts").fetchone()[0]
    calv = d.execute("select count(*) from alerts where lower(alert_type_raw) in ('calving_confirmation','calving_detection')").fetchone()[0]
    beh = d.execute("select min(date), max(date) from group_behaviour where current is not null").fetchone()
    done = hist.get("410", 0) + sum(v for k, v in hist.items() if k.isdigit() and int(k) >= 400)
    tot = sum(hist.values())
    flag = "정상" if tot and done == tot else f"보충 진행 전/중 ({done}/{tot} 목장 1년치 완료)"
    print(f"      1년치 이력: {flag} · 가장 오래된 알림 {str(first)[:10]} · 알림 {n_alert:,}건 · 분만 기록 {calv:,}건")
    print(f"      그룹행동: {beh[0] or '-'} ~ {beh[1] or '-'}")
PY

echo "[4] 최근 오류 (웹앱)"
journalctl -u smxmon-web --since "-6h" --no-pager 2>/dev/null | grep -iE "error|traceback" | tail -3 || true
echo "=========================================================="
echo "결과 화면을 캡처해 보내 주세요."
