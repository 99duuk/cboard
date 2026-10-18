# Cboard 통합 계정 관리자

- 상위 저장소: [Cboard](../README.md)
- 이 폴더의 역할: 계정 관리·공통 환경·크로스 플랫폼 이식 소스의 독립 모듈
- 저장소 기본 계정: 0개, `cnew` 또는 `account import`를 통한 사용자별 등록
- 기존 개인 공통 자산: 로컬 원본 유지, 공개 저장소 제외
- Windows 통합 설치본의 `cboard`: 관리자 등록부의 직접 조회, 명시한 별도 Cboard 계정 설정 우선

## macOS Codex 다중 계정 관리자

- 대상: macOS·zsh·Python 3.11 이상·Codex CLI
- 용도: 계정 등록·로그인·선택·실행·사용량·등록 해제 및 공통 환경 관리
- 계정 이름과 개수의 자유, 같은 로그인 계정의 복수 프로필 허용
- 관리 단위: 이름·별칭·독립 `CODEX_HOME`·설정으로 구성한 실행 프로필
- 기본 배포: 등록 계정 0개 및 개인 공통 자산 제외
- TOML 편집 의존성: 동봉 tomlkit 0.13.3 및 라이선스

## 설치

```sh
# 배포 묶음 압축 해제 후 해당 폴더에서 실행
./bin/cenv install --dry-run
./bin/cenv install
```

- 설치 시 `~/.zshrc`의 관리 구간 및 고정 셸 연결점 구성
- 새 터미널의 자동 로딩
- 설치 전부터 열려 있던 터미널의 즉시 사용: 새 탭 또는 아래 명령 중 선택

```sh
source ~/.config/codex-environment/shell.zsh
```

- 최초 셸 로딩 이후 계정 추가·삭제·선택 시 반복 `source` 불필요
- 기존 관리 환경의 최초 업그레이드 시 기존 기본 `codex` 선택 보존
- 빈 환경의 기본 계정 자동 지정 없음
- 기존 사용자 셸 코드·다른 스킬·프로젝트 신뢰·앱 상태 보존

## 시작 예시

```sh
chelp
cnew personal
cnew business --alias cbiz
clist
cdefault personal
cuse business
ccurrent
codex
crun personal "작업 내용"
cusage
```

- `cnew`의 기본 동작: 등록·설정 연결·도구 활성화·공식 로그인
- 이름 없는 `cnew`의 대화형 이름·별칭 입력
- 기본 계정 홈: `~/.codex-<이름>`
- 기본 별칭: `c<이름>`, 이름 `codex`의 신규 홈·별칭: `~/.codex-codex`·`ccodex`
- 신규 별칭 `codex`의 예약, 기존 등록의 `codex` 별칭 호환 보존
- 로그인 취소 시 등록 보존 및 `clogin <이름>` 재시도
- 동일 이름 재등록의 변경 없는 중단 및 `clogin` 안내
- 등록 시 기존 다른 계정의 모델·승인 정책 복사 없음
- 공급자 도구 미완료 시 등록 유지 및 `cenv tools install` 재시도

## 명령

| 편의 명령 | 정식 명령 | 용도 |
| --- | --- | --- |
| `chelp` | `cenv help` | 사용법 |
| `cnew` | `cenv account add` | 등록·로그인 |
| `clogin` | `cenv account login` | 로그인·재로그인 |
| `clist` | `cenv account list` | 등록 목록 |
| `ccurrent` | `cenv current` | 실제 실행 대상·선택 근거 |
| `cuse` | 셸 연동 `cenv use` | 현재 셸 선택 |
| `cdefault` | `cenv default` | 새 셸의 기본값 |
| `crun` | `cenv run` | 특정 계정으로 일회 실행 |
| `cdel` | `cenv account remove` | 데이터 보존 및 등록 해제 |
| `cusage` | `cenv usage` | 사용량 조회 |
| `creset` | `cenv usage --resets` | 리셋 정보 조회 |

```sh
cnew personal --dry-run
cnew personal --no-login
clogin personal
clist --online

cuse personal
cuse --clear
cdefault personal
cdefault --clear

crun personal "작업 내용"
crun personal -- --help
cpersonal --help

cusage personal
creset personal

cenv account import old --home ~/.codex-existing --dry-run
cenv account import old --home ~/.codex-existing
# 기존 관리 설정 충돌의 명시적 보존
cenv account import old --home ~/.codex-existing --adopt-existing

cdel personal --dry-run
cdel personal
# 비대화형 등록 해제
cdel personal --yes
```

- `clist` 기본값: 네트워크 없는 로컬 등록·마지막 확인 신원·확인 시각 조회
- `clist --online`: 공식 로그인 상태 재확인
- 로그인 자료 존재와 유효 로그인 확인의 구분
- `cusage`의 일부 실패 개별 표시 및 나머지 결과 유지
- `creset`의 조회 전용 동작, 크레딧 소비·계정 초기화 없음
- `crun` 뒤의 인자 전달, Codex 옵션 전달의 명확한 구분자 `--` 지원
- 실행 인자·표준 입출력·종료 코드의 보존

## 계정 선택 규칙

1. `crun <계정>` 또는 계정별 별칭
2. 외부에서 명시한 `CODEX_HOME`
3. `cuse`로 선택한 계정
4. `cdefault` 기본값
5. 미선택 안내 및 실행 중단

- `cuse`: 현재 셸·이후 자식 셸 적용, 다른 터미널 영향 없음
- `cuse --clear`: 셸 선택 해제 및 기본값 재사용
- `cdefault`: 새 셸 적용, 기존 셸 선택 유지
- 외부 `CODEX_HOME` 존재 시 `cuse`의 충돌 중단
- `ccurrent`: 대상·홈·선택 근거 표시
- 외부 실행 파일의 부모 셸 변경 불가, 직접 실행한 `cenv use`의 종료 코드 2
- 현재 셸 변경 시 설치된 `cuse` 함수 사용

## 등록 해제와 가져오기

- `cdel`: 등록·관리 별칭·해당 관리 원본의 해제
- 인증·네이티브 설정·세션·기록·기존 홈의 기본 보존
- 기본 계정 삭제 시 기본값 해제
- 현재 셸 선택 해제 및 다른 터미널의 오래된 선택·별칭 실행 차단
- 이미 실행 중인 세션의 유지
- 공유 자산·다른 계정의 삭제 없음
- 해제 결과의 보존 경로·가져오기 명령 안내
- `account import`: 기존 홈의 등록, 강제 재로그인 없음
- 기존 설정과 공통 설정의 충돌 시 기본 적용 중단
- `--adopt-existing`: 충돌한 기존 관리 값을 계정별 원본에 명시적으로 보존
- 머신 생성 도구 경로 충돌의 채택 제외 및 로컬 경로 해소 후 재시도
- 기존 스킬·규칙 등 자산 경로 충돌 시 보존·이동 후 재시도, 자동 덮어쓰기 없음
- 원격 계정 삭제·로컬 계정 데이터 영구 삭제 기능 제외

## 설정·진단·복구

```sh
cenv sync --dry-run
cenv sync
cenv account adopt personal --dry-run
cenv account adopt personal
cenv doctor
cenv doctor --online
cenv tools install --dry-run
cenv tools install
cenv rollback <적용-ID> --dry-run
cenv rollback <적용-ID>
```

| 위치 | 역할 |
| --- | --- |
| `accounts.toml` | 등록 이름·홈·별칭 |
| `config/defaults.toml` | 독립 신규 계정 기본값 |
| `config/common.toml` | 선택한 공유 환경의 공통 설정 |
| `config/accounts/` | 계정별 설정 |
| `assets/` | 선택한 공유 지침·스킬·규칙·역할 |
| `sources.toml` | 외부 도구 출처·고정 버전·의존성 |
| `~/.config/codex-environment/` | 머신 경로·셸 연결·기본값·`identities.json` 신원 캐시 |
| `~/.local/state/codex-environment/` | 적용 기록·비공개 백업 |

- 일반 실행·셸 로딩·목록·사용량 조회의 자동 설정 동기화 없음
- 관리 키만 변경, 미관리 키·주석·앱 및 공급자 상태 보존
- 관리 키의 외부 변경 감지 시 충돌 중단
- `adopt`: 네이티브 모델·추론·성격·서비스 등급의 원본 채택
- 승인·샌드박스 정책의 `adopt` 대상 제외
- 조건부 복구: 적용 이후 외부 변경 발견 시 덮어쓰기 중단
- 백업의 비밀 값 포함 가능성 및 로컬 전용 비공개 보관
- 기본 `doctor`: 로컬 검사, `--online`: 계정·사용량 확인
- 공급자 도구 설치물·원격 인증의 자동 삭제·복구 제외

## 다른 사람용 배포와 본인 이식

```sh
# 다른 사람용 도구만 포함: 계정 0개, 개인 공통 환경 제외
cenv export --output toolkit.tar.gz

# 선택한 공유 지침·스킬·설정 포함: 규칙·권한 정책 포함 시 확인
cenv export --include-shared --output shared.tar.gz

# 계정 이름·별칭·선호 설정·기본값 및 공유 환경 포함
cenv export --include-accounts --output migration.tar.gz

# 비대화형 규칙·권한 정책 내보내기의 명시적 확인
cenv export --include-shared --yes --output shared.tar.gz
```

- 모든 배포의 인증·세션·기록·DB·로그·캐시·백업·머신 설정 제외
- `--include-accounts`의 공유 환경 포함, 계정별 대상 Mac 재로그인 필요
- 공유 배포의 자산·의존성·권한 관련 설정 사전 목록
- 알려진 개인 경로·비밀 값 검사, 완전한 비밀 탐지 보장 제외
- 규칙·권한 정책 포함 시 명시적 확인 및 비대화형 `--yes` 필수
- 비정책 공유 자산의 `--include-shared` 명시적 선택
- 대상 Mac의 Python·Codex CLI 필요
- REA 등 선택 도구의 Node·npm·외부 서비스 인증 별도 필요
- 앱 제공 기능의 앱 설치·로그인·macOS 접근 권한 별도 필요
- 새 계정의 플러그인 hook 등 네이티브 승인 절차 유지

## 검증과 지원 범위

- 상세 명세: `SPEC.md`
- 실행 근거·구버전 실적·현행 수용 상태: `VALIDATION.md`
- 자동 검증 명령: `python3 -m unittest discover -s tests -v`
- 실제 다른 Mac·Intel 하드웨어·브라우저 로그인은 해당 근거 확보 전 미검증

## 선택 스킬 의존성

- `uia-e`·`uib-a`: 동봉 package-lock 기반 `npm ci` 및 Node 22 이상
- Playwright 브라우저: 캡처 기능 사용 시 해당 스킬 지침에 따른 별도 설치
- `sources.toml`의 `skill_npm` 항목: 선택 스킬의 최초 사용 의존성 및 설치 명령
- 공식 로그인 참고: [OpenAI Docs 인증 안내](https://learn.chatgpt.com/docs/auth)
