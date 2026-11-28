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
if grep -q 'dispatch_cc' "$APP/smxmon/cloud/web.py" 2>/dev/null; then ok "최신(보낸 업무요청 보기·사본 받기·아이폰 알림 이동 포함)"; else ng "이전 버전 → sudo smxmon-update"; fi
if [ -f "$APP/smxmon/validate.py" ]; then ok "판정 검증 도구 포함 (sudo smxmon-validate)"; else ng "판정 검증 없음 → sudo smxmon-update 두 번"; fi
echo "    코드 설치 시각: $(stat -c %y "$APP/config/rules.toml" 2>/dev/null | cut -d. -f1)"

B=$(ls -t /var/backups/smxmon/smxmon_*.tar.gz 2>/dev/null | head -1)
if [ -n "$B" ] && [ $(( ($(date +%s) - $(stat -c %Y "$B")) / 3600 )) -lt 30 ]; then ok "백업: 최근 $(stat -c %y "$B" | cut -c1-16) · $(ls /var/backups/smxmon/smxmon_*.tar.gz | wc -l)개 보관"
elif [ -n "$B" ]; then ng "백업이 30시간 넘게 없음 → sudo smxmon-backup"; else ng "백업 없음 → sudo smxmon-update 한 번 더"; fi

echo "    자동 알림 설정: $(python3 - <<'PY' 2>/dev/null
import sqlite3
try:
    c = sqlite3.connect("file:/var/lib/smxmon/cloud.db?mode=ro", uri=True)
    kv = dict(c.execute("SELECT key, value FROM kv WHERE key LIKE 'dispatch_%' AND key != 'dispatch_last'").fetchall())
    subs = c.execute("SELECT substr(owner,1,1), COUNT(*) FROM push_subs GROUP BY 1").fetchall()
    print(f"켜짐={kv.get('dispatch_enabled','0')} 시험={kv.get('dispatch_test','1')} 사본={kv.get('dispatch_cc','0')} · 알림 기기 " +
          ", ".join(f"{'팀원' if o=='u' else '담당자'} {n}대" for o, n in subs))
except Exception as e:
    print("확인 불가", e)
PY
)"

echo "    앱 주소: $(awk '/\{/{sub(/\{.*/,""); print; exit}' /etc/caddy/Caddyfile 2>/dev/null) — 바꾸기: sudo smxmon-domain 새주소.duckdns.org"
echo "[2] 서비스"
for s in smxmon-web caddy; do systemctl is-active --quiet $s && ok "$s 실행 중" || ng "$s 꺼짐 → sudo systemctl restart $s"; done
systemctl is-active --quiet smxmon-collect.timer && ok "자동 수집 타이머 켜짐" || ng "자동 수집 타이머 꺼짐"
if systemctl is-active --quiet smxmon-dispatch-am.timer; then ok "자동 업무지시 알림 타이머(08:00·14:00) 켜짐 — 실제 발송 여부는 관리자 화면 설정"; else ng "알림 타이머 없음 → sudo smxmon-update 한 번 더"; fi
echo "    수집 간격: $(cat /etc/smxmon/schedule 2>/dev/null || echo '6h(기본)') — 바꾸기: sudo smxmon-schedule 6h|4h|2h"
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
    done = sum(v for k, v in hist.items() if str(k).isdigit() and int(k) >= 400)
    tot = sum(hist.values())
    flag = "정상" if tot and done == tot else f"보충 진행 전/중 ({done}/{tot} 목장 1년치 완료)"
    print(f"      1년치 이력: {flag} · 가장 오래된 알림 {str(first)[:10]} · 알림 {n_alert:,}건 · 분만 기록 {calv:,}건")
    print(f"      그룹행동: {beh[0] or '-'} ~ {beh[1] or '-'}")
PY

echo "[4] 최근 오류 (웹앱)"
journalctl -u smxmon-web --since "-6h" --no-pager 2>/dev/null | grep -iE "error|traceback" | tail -3 || true
echo "[5] 자동 업무지시 알림 진단"
python3 - "$DATA" <<'PY'
import json, os, sqlite3, sys
from datetime import date, datetime
root = sys.argv[1]
try:
    c = sqlite3.connect(f"file:{root}/cloud.db?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    kv = dict(c.execute("SELECT key, value FROM kv").fetchall())
except Exception as e:
    print("  확인 불가:", e); sys.exit()
last = json.loads(kv.get("dispatch_last") or "{}")
if last:
    print(f"  마지막 실행: {last.get('at')} {'08시' if last.get('slot') == 'am' else '14시'} · {'시험' if last.get('test') else '실제'}"
          f"{' · 꺼짐' if last.get('note') == '꺼짐' else ''} · 판정 {last.get('as_of') or '없음'} · 긴급·우선 {last.get('targets', '?')}곳"
          f" · 보낸 담당자 {last.get('contacts', 0)}명/목장 {last.get('farms', 0)}건 · 전달 {last.get('delivered', 0)}명")
    if "cc" in last:
        print(f"    관리자 사본 전달: {last['cc']}건")
    for e in (last.get("push_errors") or [])[:6]:
        print(f"    ⚠ 알림 실패: {e['owner']} {e['host']} 코드 {e['code']} {e['msg']}")
    for k, lbl in (("no_contact", "담당자 미등록"), ("no_device", "알림 미설정 담당자"), ("stale", "판정 오래됨(팀원)"), ("no_state", "판정 자료 없음(팀원)")):
        if last.get(k):
            print(f"    {lbl}: {', '.join(map(str, last[k][:10]))}{' …' if len(last[k]) > 10 else ''}")
else:
    print("  아직 한 번도 실행 안 됨")
today = date.today().isoformat()
rows = c.execute("SELECT slot, test, name, devices, delivered, farms FROM dispatches WHERE day=? ORDER BY id", (today,)).fetchall()
print(f"  오늘({today}) 발송 기록 {len(rows)}건" + ("" if rows else " — 없음"))
for r in rows[:12]:
    print(f"    {'08시' if r['slot'] == 'am' else '14시'} {'시험' if r['test'] else '실제'} · {r['name']} · 목장 {len(json.loads(r['farms'] or '[]'))} · 기기 {r['devices']} · 전달 {r['delivered']}")
n_ct = c.execute("SELECT COUNT(DISTINCT farm_id) FROM contacts").fetchone()[0]
print(f"  담당자 등록 목장 {n_ct}곳 · 담당자 링크 {c.execute('SELECT COUNT(*) FROM receivers WHERE revoked=0').fetchone()[0]}명")
for u in c.execute("SELECT uid, username FROM users"):
    p = f"{root}/users/{u['uid']}/reports/latest_state.json"
    if os.path.exists(p):
        st = json.load(open(p, encoding="utf-8"))
        g = [f["grade"] for f in st.get("farms", [])]
        print(f"  판정 자료 {u['username']}: {st.get('asOf')} · 긴급 {g.count('긴급')} · 우선 {g.count('우선')}")
    else:
        print(f"  판정 자료 {u['username']}: 없음(업데이트 후 수집 전)")
PY
if systemctl is-active --quiet smxmon-heat.timer; then echo "  [정상] 발정 알림 타이머(07·13·19시) 켜짐"; else echo "  [확인] 발정 알림 타이머 없음 → sudo smxmon-update 한 번 더"; fi
for slot in am pm; do
  echo "  · 발송 작업 로그($slot) 최근:"
  journalctl -u smxmon-dispatch-$slot --since "-2d" --no-pager -o short 2>/dev/null | grep -vE "^-- |Starting|Started|Deactivated|Finished|Consumed" | tail -3 | sed 's/^/      /'
  systemctl list-timers smxmon-dispatch-$slot.timer --no-pager 2>/dev/null | awk 'NR==2{print "      다음 실행: "$1" "$2" "$3"  · 지난 실행: "$5" "$6" "$7}'
done
echo "=========================================================="
echo "결과 화면을 캡처해 보내 주세요."
