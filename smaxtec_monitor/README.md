# smaXtec 목장별 모니터링 자동 집계 (smxmon)

담당 목장들의 smaXtec 알림(체온·반추·음수·pH·발정·분만)을 주기적으로 수집해 SQLite에 누적하고,
매일 아침 **목장별 이슈 건수 / 7일 추세 / 급증 / 집단신호 / 방문 우선순위** 엑셀 리포트를 만든다.

> 현재 상태: **뼈대 완성 + 가상데이터·가짜사이트 테스트 통과.**
> 실제 사이트 URL·화면 선택자는 아직 비어 있음(`config/selectors.toml` 의 `TODO_*`). 화면 HTML을 받으면 채운다.

---

## 1. 폴더 구조

```
smaxtec_monitor/
├─ .env.example            ← 복사해서 .env 로 (아이디·비번·메일). git 제외
├─ requirements.txt
├─ config/
│  ├─ selectors.toml       ← 화면 선택자 (HTML 받으면 채울 곳)
│  ├─ alert_types.toml     ← 화면 알림명 → 표준코드 (한/영/독 키워드)
│  ├─ rules.toml           ← 급증·집단신호·우선순위 기준값
│  └─ farms.example.toml   ← 목장별 두수·표시명 (복사 → farms.toml, git 제외)
├─ smxmon/
│  ├─ cli.py               ← 명령 진입점 (python -m smxmon ...)
│  ├─ scraper.py           ← Playwright 로그인·목장목록·알림목록·inspect
│  ├─ collector.py         ← 1회 수집 흐름(재시도·잠금·해제판정)
│  ├─ db.py                ← SQLite 스키마, 중복제거 upsert
│  ├─ normalize.py         ← 알림명/일시 정규화
│  ├─ analysis.py          ← 급증·집단신호·우선순위 판정
│  ├─ report.py            ← 엑셀 리포트 / 메일 본문
│  ├─ notify.py            ← (선택) SMTP 메일
│  └─ demo.py              ← 가상데이터 생성
├─ scripts/                ← 작업스케줄러(.bat/.ps1), cron(.sh)
├─ tests/                  ← 단위테스트 + 가짜 사이트 통합테스트
├─ data/   (자동생성, git 제외)  smaxtec.db, auth_state.json(세션), inspect/
├─ logs/   (자동생성, git 제외)  smxmon.log, debug/(실패 화면캡처)
└─ reports/(자동생성, git 제외)  smaxtec_daily_YYYYMMDD_HHMM.xlsx
```

## 2. 설치 (내 PC)

```bash
cd smaxtec_monitor
python -m venv .venv
# Windows: .venv\Scripts\activate    Mac: source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
copy .env.example .env      # Mac: cp .env.example .env  → 메모장으로 열어 값 입력
python -m smxmon init       # 폴더·DB 생성, 미입력 설정 점검
python -m smxmon demo       # 가상데이터로 리포트 미리보기 → reports/DEMO_*.xlsx
```

Python 3.11 이상 필요(`tomllib`).

## 3. 선택자 채우기 절차 (다음 작업)

1. `.env` 에 `SMX_LOGIN_URL`, 아이디·비번 입력
2. `python -m smxmon inspect --manual`
   → 브라우저 창이 뜸 → **직접 로그인** → 목장 목록 화면에서 Enter
   → `data/inspect/날짜/` 에 HTML·스크린샷·`api_calls.json` 저장
3. 같은 방식으로 **알림 목록 화면**, **개체 상세 화면** 저장 (URL 을 인자로 추가 가능)
4. 저장 파일 중 필요한 부분(목록 1~2행)을 **목장명·개체번호 가린 뒤** 전달 → `selectors.toml` 채움
5. `python -m smxmon collect --farm <목장ID 1개>` 로 시험 수집 → `python -m smxmon status`

> 💡 `api_calls.json` 확인이 중요: smaXtec 웹은 화면 뒤에서 JSON API를 호출하는 구조일 가능성이 높다(추정).
> 그렇다면 화면 글자 대신 JSON을 읽는 방식이 화면 개편에 훨씬 강하다. 토큰류 값은 자동으로 `***` 처리됨.

## 4. 명령

| 명령 | 용도 |
|---|---|
| `python -m smxmon collect [--notify] [--farm ID ...]` | 1회 수집. `--notify` = 집단신호 즉시 메일 |
| `python -m smxmon report [--email] [--as-of "2026-11-19 07:00"]` | 일일 요약 엑셀 (+메일) |
| `python -m smxmon status` | 목장 ID 목록, 최근 수집 성공/실패 |
| `python -m smxmon inspect [URL ...] [--manual]` | 화면 구조 저장 |
| `python -m smxmon demo` | 가상데이터 리포트 |
| `python -m pytest -q tests` | 테스트 |

## 5. 주기 실행

- **Windows**: PowerShell 에서 `powershell -ExecutionPolicy Bypass -File scripts\register_windows_tasks.ps1`
  → 수집 06:10부터 2시간 간격, 리포트 매일 06:50
- **Mac**: `crontab -e`
  ```
  10 */2 * * * /경로/smaxtec_monitor/scripts/run_collect.sh
  50 6 * * *   /경로/smaxtec_monitor/scripts/run_daily_report.sh
  ```
- PC가 꺼져 있으면 수집이 안 됨 → 리포트의 `마지막 정상수집`·`⚠데이터 지연`으로 확인.
- 동시 실행 방지 잠금(`data/collect.lock`), 목장 사이 3~8초 무작위 대기, 실패 시 5→10→20초 재시도.
  **로그인 실패는 재시도하지 않음**(계정 잠금 방지).

## 6. 데이터 설계 핵심

| 테이블 | 내용 |
|---|---|
| `alerts` | 알림 1건=1행. **중복키 = 목장+개체+알림종류+발생시각**. `first_seen_at / last_seen_at / resolved_at / status` |
| `alert_observations` | 수집회차별로 어떤 알림이 보였는지(시점별 누적 이력) |
| `collection_runs` | 수집 실행 기록(성공/부분/실패, 건수, 오류) |
| `farms` | 목장, 두수, 마지막 정상수집 |
| `animal_metrics` | (확장용) 개체 지표 시계열 |
| `v_alerts` (뷰) | `duration_hours` = (해제 또는 마지막확인 − 발생) |

- **해제 시각**: 화면에 해제시각이 있으면 그 값(`site`), 없으면 *목록에서 사라진 수집 시각*으로 추정(`inferred`, 정밀도 = 수집간격).
  일부 실패한 목장은 해제 판정을 건너뜀(오판 방지). 해제 후 다시 나타나면 재활성(`reopen_count`).
- **상대시각('3시간 전')** 표기면 수집마다 값이 흔들리므로 ±30~90분 허용오차로 같은 알림 처리.

## 7. 이슈 판정 기준 (`config/rules.toml` 에서 조정)

- **집계창**: 리포트 시각 기준 최근 24h(D0) / 직전 24h(D-1) / 그 전 7일 평균
- **급증**: D0 ≥ 3건, 그리고 전일 대비 2배·+2건 또는 7일평균 대비 2배·+2건
- **집단신호**: 12시간 창에 서로 다른 개체 4두 이상(체온상승·반추감소) + 두수 대비 4% 이상(두수 알 때)
  + 평소 같은 창 평균의 2배 이상 (큰 목장 오탐 방지)
  - 반추감소 다발 + pH저하 → **SARA(아급성 반추위산독증) 의심**
  - 체온+반추 동시 → **집단 감염성 질병 가능성**
  - 체온만 → 감염성 질병 또는 **고온 스트레스(THI)**
  - 반추만 → **사료·급여 문제**(배합/원료 변경, 변질·곰팡이독소, 급이·음수)
- **장기 미해제**: 48시간 이상 해제 안 된 건강알림
- **우선순위 점수** → 긴급(≥40)/우선(≥20)/관찰(≥8)/정상

> 기본값은 출발점(추정)이다. 2~4주 실제 데이터를 쌓은 뒤 목장 방문 결과와 대조해서 조정할 것.
> 판정은 센서 알림 패턴에 따른 **선별**이며 진단이 아님.

## 8. 보안 원칙

- 아이디·비번은 `.env` 에만. `Settings` 출력·로그에서 자동 마스킹, 예외 메시지에도 포함하지 않음
- `data/auth_state.json`(로그인 세션 쿠키)은 비밀번호와 같은 수준으로 취급
- `data/ logs/ reports/ .env config/farms.toml` 은 `.gitignore` 로 저장소 제외
- 리포트·디버그 캡처에는 목장 데이터가 들어 있음 → 외부 공유 금지

## 9. 확인 필요 목록

- [ ] 리얼팜: 자동 수집 허용 여부 / **엑셀 내보내기·알림메일·API 제공 여부** (있으면 그 경로가 우선)
- [ ] 로그인 URL, 목장 목록·알림 목록 화면 HTML
- [ ] 실제 알림 명칭(한글/영문) → `alert_types.toml` 보강
- [ ] 알림 목록이 '진행중'만 보이는지, 해제된 이력도 보이는지 (해제 판정 방식이 달라짐)
- [ ] 실행 PC (Windows/Mac), 리포트 수신 방식(엑셀/메일/메신저)
- [ ] 회사 업무용 vs 개인 사업용 (데이터 활용 범위·겸업 규정)
