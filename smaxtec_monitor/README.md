# smaXtec 목장별 모니터링 자동 집계 (smxmon)

담당 목장들의 smaXtec 알림(체온·반추·음수·pH·발정·분만)을 주기적으로 수집해 SQLite에 누적하고,
매일 아침 **목장별 이슈 건수 / 7일 추세 / 급증 / 집단신호 / 방문 우선순위** 엑셀 리포트를 만든다.

> 현재 상태: **수집 방식 2가지 구현 + 가짜 서버 테스트 통과. 실제 계정으로는 아직 미검증.**
> - **api 모드(기본, 권장)**: smaXtec 공식 Integration API (`https://api.smaxtec.com/integration/v2`)
> - web 모드(대안): web.smaxtec.com 화면 자동조작. 로그인(SSO) 선택자는 확인 완료, 목록 화면 선택자는 미확정

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

## 3. 첫 연결 확인 (api 모드)

```
python -m smxmon api-test
```
- 성공하면: 접근 가능한 목장 목록 + 목장별 최근 24시간 이벤트 종류·건수가 나옴 → 결과를 그대로 전달
- `API 로그인 실패` 가 나오면: 일반 web 계정에 API 권한이 없을 가능성이 높음
  → 리얼팜(또는 smaXtec integrations-dev@smaxtec.com)에 **"컨설팅용 조회 API 토큰"** 발급 요청
  → 받은 토큰을 `.env` 의 `SMX_API_TOKEN=` 에 입력하면 아이디/비번 대신 사용
- 그래도 안 되면 `.env` 에서 `SMX_MODE=web` 로 바꾸고 화면 수집 방식 사용
  (`python -m smxmon inspect --manual` 로 목록 화면 HTML 저장 → 선택자 채움)

### api 모드에서 가져오는 것
| API | 용도 |
|---|---|
| `GET /organisations` | 내 계정이 볼 수 있는 목장 전체 |
| `GET /organisations/{id}/animals` | 관리번호·이표·비유상태, **센서 장착 두수 자동 산출** |
| `GET /organisations/{id}/events` | 알림 이벤트 (최근 72시간 재조회, 첫 수집 시 10일치) |

이벤트 코드 → 표준 분류는 `config/alert_types.toml [event_codes]` (예: `health_104` 체온상승,
`health_301~318` 반추감소(건유·분만전후·신선·착유 구분), `feeding_202` 산독증 위험, `clinical_condition_403` 케토시스 의심,
`health_108`/`feeding_316` smaXtec 자체 그룹경보, `health_601~606` THI 고온스트레스).

⚠ API 이벤트는 '발생 시점' 기록이라 **해제시각·지속시간이 없음** → 장기미해제·평균지속은 web 모드 또는 추후 attentions 연동 과제.

## 4. 명령

| 명령 | 용도 |
|---|---|
| `python -m smxmon api-test [--hours 24] [--limit 3]` | API 접속 점검(저장 안 함) |
| `python -m smxmon collect [--notify] [--farm ID ...]` | 1회 수집. `--notify` = 집단신호 즉시 메일 |
| `python -m smxmon report [--email] [--as-of "2026-11-19 07:00"]` | 일일 요약 엑셀 + 방문요약 + 모바일 대시보드 (+메일) |
| `python -m smxmon visit 목장이름 ...` | 지정 목장 방문용 1장 요약 |
| `python -m smxmon status` | 목장 ID 목록, 최근 수집 성공/실패 |
| `python -m smxmon inspect [URL ...] [--manual]` | 화면 구조 저장 |
| `python -m smxmon demo` | 가상데이터 리포트 |
| `python -m pytest -q tests` | 테스트 |

## 5. 매일 06:00 자동 실행 · 모바일 대시보드 · 방문요약

### 자동 실행 (Windows)
```
powershell -ExecutionPolicy Bypass -File scripts\register_windows_tasks.ps1
```
- `smxmon_daily_report`: **매일 06:00** 수집 → 일일 리포트·방문요약·모바일 대시보드 생성 (→ 공유폴더 복사·메일)
- `smxmon_collect`: 08:10부터 2시간 간격 수집 + 긴급 집단신호 메일(`--notify`)
- 절전이면 깨워서 실행, 꺼져 있었으면 켜지는 즉시 실행. **PC 전원은 켜 두거나 절전 상태**여야 함
- 시험 실행: `Start-ScheduledTask -TaskName smxmon_daily_report`

### 매일 만들어지는 파일 (`reports/`)
| 파일 | 용도 |
|---|---|
| `smaxtec_daily_날짜.xlsx` | 전체 목장 요약·추세·집단신호·그룹행동 등 7개 시트 |
| `방문요약_날짜.xlsx` | 긴급·우선 목장(최대 10곳) **A4 1장씩** — 인쇄해서 방문 |
| `dashboard_날짜.html` | **모바일 대시보드** (HTML 1개, 외부 연결 없음) |

특정 목장만: `python -m smxmon visit 정진 학운` → `방문요약_정진목장_학운목장_날짜.xlsx`

### 휴대폰에서 '앱처럼' 보기
1. `.env` 에 `SMX_PUBLISH_DIR=C:\Users\내이름\OneDrive\smaXtec` (Google Drive 동기화 폴더도 가능)
2. 매일 06:00 후 그 폴더에 `smaxtec_dashboard.html`(항상 최신본) 이 덮어써짐
3. 휴대폰 OneDrive/Drive 앱에서 열기 → 브라우저로 열어 **홈 화면에 추가**
- 체크리스트 체크·메모는 그 휴대폰에만 저장(서버로 전송 안 함)
- 공유폴더는 **본인만 접근하는 폴더**로(공유 링크 생성 금지). 목장 데이터는 농장주 소유
- 휴대폰 분실 대비: 휴대폰 화면잠금, Drive/OneDrive 앱 잠금(PIN) 설정 권장

### Mac (cron)
```
0 6 * * *    /경로/smaxtec_monitor/scripts/run_daily_report.sh
10 8-20/2 * * * /경로/smaxtec_monitor/scripts/run_collect.sh
```

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

- [x] 로그인 URL: web.smaxtec.com → sso.smaxtec.com (Keycloak)
- [ ] `api-test` 결과: 일반 계정으로 API 로그인 가능한지
- [ ] 리얼팜/smaXtec: 컨설턴트 계정의 API 사용 허용 여부·토큰 발급 (이용약관 문제도 이걸로 정리됨)
- [ ] 실제 알림 명칭(한글/영문) → `alert_types.toml` 보강
- [ ] 알림 목록이 '진행중'만 보이는지, 해제된 이력도 보이는지 (해제 판정 방식이 달라짐)
- [ ] 실행 PC (Windows/Mac), 리포트 수신 방식(엑셀/메일/메신저)
- [ ] 회사 업무용 vs 개인 사업용 (데이터 활용 범위·겸업 규정)
