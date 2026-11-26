# 현장점검 앱 Cloudflare 배포 · 로그인 보호 설정 안내

Claude 계정 없이 팀원이 링크로 앱을 쓰되, **관리자가 허용한 이메일만 들어오게** 하는 설정입니다.
Cloudflare Pages(무료 호스팅)에 앱을 올리고, Cloudflare Access(50명까지 무료)로 로그인을 막습니다.

- 팀원은 앱 주소를 열고 **자기 이메일 → 메일로 온 6자리 인증번호**를 넣어 들어옵니다. 비밀번호나 앱 설치는 필요 없습니다.
- 관리자는 Cloudflare 화면에서 **허용 이메일 목록**을 고쳐 팀원을 추가하거나 뺍니다.
- 처음 설정은 30분~1시간 정도 걸립니다. Cloudflare 화면의 메뉴 이름은 바뀔 수 있으니, 이름이 조금 달라도 같은 위치를 찾으시면 됩니다.

---

## 0단계. GitHub 저장소를 비공개로 바꾸기 (먼저)

1. GitHub → `Happy_livestock` 저장소 → **Settings** → **General** → 맨 아래 **Danger Zone**
2. **Change repository visibility** → **Make private**
3. Cloudflare Pages는 비공개 저장소에서도 무료로 배포됩니다.

## 1단계. 배포할 브랜치 정하기

앱 파일은 지금 `claude/exciting-pascal-sz2szb` 브랜치에 있습니다. 둘 중 하나를 고르세요.

- **권장**: 이 브랜치를 `main`에 합친 뒤(풀 리퀘스트 병합) `main`을 배포
- **바로 시작**: 2단계에서 Production branch를 `claude/exciting-pascal-sz2szb`로 지정

## 2단계. Cloudflare Pages에 앱 올리기

1. [dash.cloudflare.com](https://dash.cloudflare.com) 가입 후 로그인
2. 왼쪽 메뉴 **Workers & Pages** → **Create** (생성)
3. **Pages** 탭 → **Connect to Git** (Git에 연결) → GitHub 계정 연결
   - GitHub 권한 화면에서 **Only select repositories → Happy_livestock**만 허용하는 것을 권장합니다.
4. 저장소 `Happy_livestock` 선택 → **Begin setup**
5. 빌드 설정을 아래처럼 넣습니다.

   | 항목 | 값 |
   |---|---|
   | Project name | 예: `farmcheck` → 주소가 `https://farmcheck.pages.dev`가 됩니다 (이미 쓰는 이름이면 다른 이름) |
   | Production branch | `main` (또는 1단계에서 고른 브랜치) |
   | Framework preset | **None** |
   | Build command | **비워 둠** |
   | Build output directory | **`app`** |

6. **Save and Deploy** → 1~2분 뒤 `https://프로젝트이름.pages.dev` 주소가 생깁니다.
7. 이 시점에는 아직 누구나 열 수 있습니다. **바로 3단계로 넘어가세요.**

## 3단계. Zero Trust(무료 플랜) 시작

1. Cloudflare 왼쪽 메뉴 **Zero Trust** 클릭
2. 처음이면 **팀 이름**(예: `happylivestock`)을 정하고 **Free** 플랜 선택
   - Cloudflare 공식 안내상 무료 플랜도 **결제 수단 입력을 요구할 수 있습니다.** 50명 이하는 요금이 나가지 않습니다.
3. **Settings → Authentication → Login methods**에 **One-time PIN**이 켜져 있는지 확인 (기본으로 켜져 있음)

## 4단계. 앱 주소에 로그인 걸기

Cloudflare 공식 문서(Pages Known issues, "Enable Access on your *.pages.dev domain") 절차입니다.

1. **Workers & Pages** → 2단계에서 만든 프로젝트 선택
2. **Settings → General → Enable access policy** (접근 정책 사용) 켜기
   - 이것만으로는 미리보기 주소만 보호되고, 본 주소(`프로젝트이름.pages.dev`)는 아직 열려 있습니다.
3. 방금 생긴 정책의 **Manage** 선택 → Zero Trust의 **Access → Applications** 화면으로 이동
4. 프로젝트 이름의 애플리케이션 선택 → **Configure** (편집)
5. **Public hostname**의 **Subdomain** 칸에 있는 별표(`*`)를 **지우고** 저장
   - 오류가 나면 **Application name**을 조금 바꾼 뒤 다시 저장합니다. (예: `farmcheck-app`)
   - 이제 본 주소 `프로젝트이름.pages.dev`가 보호됩니다.
6. 다시 프로젝트 **Settings → General → Enable access policy**를 한 번 더 켜서 미리보기 주소도 보호합니다.
7. **Access → Applications**에 정책이 2개(본 주소용, `*.프로젝트이름.pages.dev` 미리보기용) 있는지 확인합니다.

## 5단계. 허용할 팀원 이메일 넣기 (관리자 승인)

두 애플리케이션 각각의 **Policies(정책)**를 아래처럼 맞춥니다.

| 항목 | 값 |
|---|---|
| Action | **Allow** |
| Include → **Emails** | 관리자 본인 이메일 + 팀원 이메일 (여러 개 입력) |
| Require → **Login Methods** | **One-time PIN** |
| Session duration | 예: **1 month** (한 번 로그인하면 한 달 유지) |

- **Include에 One-time PIN을 넣으면 안 됩니다.** 그러면 인증번호만 받으면 아무나 들어옵니다. 이메일은 Include, 로그인 방식은 Require에 넣으세요. (Cloudflare 공식 권장)
- 팀원이 늘면 Include의 이메일 목록에 추가만 하면 됩니다.
- 팀원 목록을 따로 관리하려면 **Reusable components → Lists**(또는 Access Groups)에 이메일 목록을 만들어 두고 정책에서 그 목록을 쓰면 편합니다.

## 6단계. 확인

1. 휴대폰이나 시크릿 창에서 `https://프로젝트이름.pages.dev` 열기
2. Cloudflare 로그인 화면 → 허용한 이메일 입력 → 메일로 온 인증번호 입력
   - 인증번호는 10분 동안만 쓸 수 있고 한 번만 쓸 수 있습니다.
   - 회사 메일 보안 시스템이 링크를 먼저 열어 "이미 사용됨"이 뜨면 **링크 대신 숫자 코드를 직접 입력**하세요.
3. 앱에서 **점검자 이름**을 한 번 넣으면 시작됩니다. 기록 탭의 팀 설정에 로그인 계정이 표시됩니다.
4. 허용하지 않은 이메일로는 들어오지 못하는지도 확인합니다.

## 팀원 관리 (관리자)

| 하고 싶은 일 | 방법 |
|---|---|
| 팀원 추가 | 정책의 Include → Emails에 이메일 추가 |
| 팀원 중지 | 이메일을 목록에서 삭제 → **My Team → Users**에서 그 사람 **Revoke session** (이미 로그인한 세션까지 즉시 차단) |
| 누가 언제 들어왔는지 | **Logs → Access** (무료 플랜은 보관 기간이 짧습니다) |

## 팀원에게 보낼 안내 (복사해서 쓰세요)

> 현장점검 앱 주소: https://프로젝트이름.pages.dev
> 1) 주소를 열고 회사 이메일을 넣으면 인증번호 메일이 옵니다. 숫자를 입력하세요.
> 2) 처음 한 번 이름을 넣으면 됩니다. 휴대폰 브라우저 메뉴의 "홈 화면에 추가"를 누르면 앱처럼 쓸 수 있습니다.
> 3) 점검이 끝나면 리포트 화면의 "관리자에게 기록 보내기"로 기록 파일을 보내 주세요.
> 4) 사용 설명서는 앱 오른쪽 위 ? 버튼에 있습니다.

## 알아 둘 점

- 앱 데이터는 각 휴대폰에 저장됩니다. 관리자에게 기록 파일(.json)을 보내면 관리자는 기록 탭 **"백업·팀원 기록 불러오기"**로 엽니다.
- 앱을 고치면(저장소 업데이트) Cloudflare가 자동으로 다시 배포합니다.
- 관리자 이메일을 앱에 고정하려면 `app/index.html`의 `TEAM_CFG.adminEmail`에 넣습니다. 저장소가 비공개일 때만 넣으세요.
- Cloudflare Access 없이 배포된 경우에만 팀 코드(기본 `HAPPY2026`)를 묻습니다.

참고: [Pages Known issues](https://developers.cloudflare.com/pages/platform/known-issues/) · [Preview deployments](https://developers.cloudflare.com/pages/configuration/preview-deployments/) · [One-time PIN](https://developers.cloudflare.com/cloudflare-one/integrations/identity-providers/one-time-pin/) · [Common Access policies](https://developers.cloudflare.com/cloudflare-one/access-controls/policies/common-policies/)
