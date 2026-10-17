# Codex 계정 관리자 — Windows·macOS·Linux 공통화 명세 v3.1

- 상태: 비판 반영 개정안 · 구현 및 기술 검증 전
- 기준: 현재 macOS 구현 및 v2 계정 관리 계약
- 이번 작업 범위: 소스 조사·공식 문서 확인·명세 작성
- 이번 작업 제외: 프로그램 수정·설치 변경·실행 검증·배포본 갱신
- 우선 적용: 본 문서의 플랫폼·셸·배포 계약, 나머지 v2 계정 생명주기 계약의 유지

## 1. 목표

- 동일한 명령·계정 생명주기·데이터 보존 규칙의 세 운영체제 제공
- Windows 네이티브 사용 및 WSL 설치 강제 없음
- 임의 사용자의 임의 계정 수·이름·별칭 지원
- 현재 Mac의 기존 5개 계정·인증·대화·모델 설정·사용 명령 보존
- 공통 Python 엔진과 운영체제·셸 차이의 분리
- 핵심 관리자의 호환성과 개별 스킬·외부 도구의 호환성 구분
- OS별 전체 코드 복제·명령마다 흩어진 플랫폼 분기·백그라운드 데몬 추가 제외

## 2. 출시 범위와 지원 표시

### 필수 출시 조합

| ID | OS·아키텍처 | 셸 | 완료 문턱 |
| --- | --- | --- | --- |
| R-MAC | macOS 26.6.2 arm64, 현재 Mac | zsh 5.9·시스템 bash 3.2.57 | 기존 5개 계정 보존 및 두 셸 전체 흐름 |
| R-WIN | Windows 11 x64 | PowerShell 7.4 계열 | WSL·관리자·Developer Mode 없는 전체 흐름 |
| R-LINUX | Ubuntu 24.04 x64 | bash 5 계열·zsh 5.9 | 두 셸 전체 흐름 및 헤드리스 인증 |

- 필수 세 OS 조합의 완료 전 세 OS 지원 정식판 표시 금지
- 실제 Windows 빌드·셸 패치·Python·Codex·Node 버전의 G0 시작 전 고정 및 RELEASE-MATRIX 기록
- Python 3.11의 최소 기준, 추가 지원 Python 버전의 검증 기록별 표시
- 공식 Codex CLI 검증 버전의 고정, 구버전 기능 부족의 사전 오류
- 현재 Mac 버전·아키텍처·셸의 실제 명령 조회 근거

### 추가 검증 조합

- Windows 10: 공식 best-effort 범위 및 실제 검증 빌드에 한정한 조건부 표시
- Intel macOS·Debian 12·Windows/Linux ARM64·WSL2: 별도 수용 결과 확보 전 미검증 표시
- 추가 조합 미검증의 필수 세 조합 출시 차단 없음, 해당 조합의 정식 지원 주장 금지
- WSL2의 Windows 네이티브 설치와 별도 홈·인증·도구·상태 저장소
- CMD·fish: console entry point 기반 등록·조회·명시적 실행 지원, 현재 셸 선택·동적 별칭 통합의 초기 범위 제외
- Windows PowerShell 5.1: 파일 변경 전 종료 및 PowerShell 7 설치 안내
- PowerShell·Python·Codex의 사전 의존성, 임의 자동 설치 없음
- Windows Terminal·iTerm2·GUI 앱의 코어 필수 의존 없음

## 3. 변경 없이 유지할 사용자 계약

| 명령 | 공통 의미 |
| --- | --- |
| chelp | 항목별 한글 표·명령 설명·실행 예시 |
| cnew [이름] | 계정 환경 생성 및 공식 로그인 |
| clogin 이름 | 로그인·재로그인·취소 후 재시도 |
| clist [--online] | 로컬 목록·선택·기본값·신원 캐시, 선택적 온라인 확인 |
| ccurrent | 실제 실행 대상·홈·선택 근거 |
| cuse 이름 / --clear | 현재 셸 선택·선택 해제 |
| cdefault [이름 / --clear] | 새 셸의 기본값 조회·지정·해제 |
| codex | 현재 선택 계정으로 실행 |
| crun 이름 [인자...] | 선택 변경 없는 일회 실행 |
| cusage [이름] | 전체 또는 지정 계정 사용량 |
| creset [이름] | 리셋 정보 조회, 실제 크레딧 소비 없음 |
| cdel 이름 | 등록 해제, 인증·설정·기록·실행 중 세션 보존 |
| cenv account import | 기존 홈의 명시적 등록 |
| cenv sync / doctor / tools install / rollback | 설정 반영·진단·도구 설치·조건부 복구 |
| cenv export | 기본·공유 환경·계정 구성 배포 |

- cenv 하위 명령의 단일 구현 및 편의 명령의 얇은 연결
- 이름·별칭·기존 홈 충돌의 적용 전 감지
- 계정 ID와 다른 계정 별칭의 교차 충돌 차단
- 기본 신규 홈: 사용자 홈 아래 `.codex-<이름>`
- 기본 실행 별칭: `c<이름>`, 신규 `codex` 별칭의 예약
- 등록 취소·도구 실패·로그인 취소의 v2 보존·재시도 규칙 유지
- 기본 배포의 등록 계정 0개 및 개인 계정 자동 생성 없음
- 등록 해제 시 원격 계정 삭제·로컬 기록 영구 삭제 없음

## 4. 구조와 책임

| 영역 | 책임 | 플랫폼 정보 접근 |
| --- | --- | --- |
| 계정·설정·등록부 | 검증·선택 규칙·설정 병합·생명주기 | 아래 경계 호출만 허용 |
| 셸 연결 | 함수·별칭·환경 변수·프로필 갱신 | zsh·bash·PowerShell별 처리 |
| 파일·경로 | 사용자 디렉터리·동일 경로·권한·잠금·교체·공유 자산 배치 | POSIX·Windows별 처리 |
| 프로세스·RPC | 실행 정의·인자·파이프·제한 시간·취소·프로세스 회수 | OS별 실행·종료 처리 |
| 배포·자산 | 포함 범위·호환성·무결성·경로 매핑 | 공통 메타데이터 및 대상 OS 조건 |

- 기존 accounts·core·runtime·transaction·usage·bundle·tools의 가능한 로직 재사용
- 의미가 같은 로직의 공통 유지, 실제로 다른 작업만 경계별 분리
- Windows에서 POSIX 전용 모듈의 무조건 import 금지
- 운영체제 차이의 계정 등록·삭제 함수 내부 반복 분기 금지
- `help` 실행의 Node·Codex 설치·로그인·네트워크 불필요

## 5. 설치·저장 위치

### 5.1 프로그램과 사용자 원본의 분리

- 실행 코드·동봉 기본 자산의 프로그램 설치 영역 보관
- 등록부·계정 원본·공통 자산의 사용자 환경 원본 workspace 보관
- 머신 경로·셸 연결·기본값의 사용자 설정 영역 보관
- 적용 기록·백업·신원 캐시의 비공개 상태 영역 보관
- 프로그램 업그레이드의 사용자 원본·인증·기본값 덮어쓰기 금지
- 소스 코드 디렉터리에 계정 등록부가 있어야만 실행 가능한 구조의 제거

| 종류 | macOS 기본값 | Linux 기본값 | Windows 기본값 |
| --- | --- | --- | --- |
| 머신 설정 | `~/.config/codex-environment` | `$XDG_CONFIG_HOME/codex-environment`, 기본 `~/.config/...` | `%LOCALAPPDATA%/codex-environment/config` |
| 프로그램·새 workspace | `~/.local/share/codex-environment` | `$XDG_DATA_HOME/codex-environment`, 기본 `~/.local/share/...` | `%LOCALAPPDATA%/codex-environment/data` |
| 상태·백업·캐시 | `~/.local/state/codex-environment` | `$XDG_STATE_HOME/codex-environment`, 기본 `~/.local/state/...` | `%LOCALAPPDATA%/codex-environment/state` |
| 신규 계정 홈 | `~/.codex-<이름>` | `~/.codex-<이름>` | `%USERPROFILE%/.codex-<이름>` |

- macOS 기존 디렉터리의 호환 유지
- Linux의 절대 경로 XDG 설정 준수, 잘못된 상대 경로 설정의 진단
- Windows Roaming AppData의 기본 상태 저장소 사용 제외
- 사용자 홈 결정의 OS API·Python 기준, USERPROFILE·HOME의 임의 혼용 금지
- 기존 Mac workspace의 현재 위치 유지 및 명시적 경로 등록
- 다른 OS 설치 시 프로그램 경로·workspace 경로·상태 경로의 재계산

### 5.2 설치 전달 방식

- 공통 Python 패키지와 운영체제별 설치 진입점 제공
- POSIX: `install.sh`, Windows: `install.ps1`
- Windows PowerShell 7에서의 직접 설치 지원
- 사용자 전용 Python 실행 환경 및 로컬 배포 패키지 기반 설치
- 패키지의 cenv·편의 명령 console entry point 제공, Windows 네이티브 실행 진입점 포함
- 시스템 Python·기존 가상환경·시스템 PATH·시스템 프로필의 임의 수정 금지
- 관리자 권한·Developer Mode·WSL의 관리자 설치 필수 의존 제외
- Python·Codex·PowerShell·선택 도구의 사전 감지 및 누락 항목별 설치 안내
- 운영체제 패키지 관리자를 통한 임의 자동 설치 제외
- 설치 계획·변경 파일·충돌·선택 자산의 적용 전 표시
- 동일 버전 재설치의 중복 프로필 구간·중복 PATH·불필요 파일 재작성 방지

## 6. 셸 선택·프로필 계약

### 6.1 선택 우선순위

1. crun 또는 계정별 실행 별칭
2. 명시적 외부 CODEX_HOME
3. 현재 셸의 CENV_ACCOUNT
4. 해당 셸이 시작할 때 캡처한 기본 계정
5. 미선택 안내 및 실행 중단

- cuse의 현재 셸·이후 자식 셸 적용, 다른 터미널의 선택 불변
- cdefault의 영속 기본값 변경, 기존 셸의 기본값 캡처 불변
- cuse --clear의 현재 선택 제거 및 해당 셸 캡처 기본값 재사용
- 외부 CODEX_HOME 존재 시 cuse의 적용 중단 및 원인 안내
- 명시적 초기화 표식을 통한 기본값 캡처 여부 기록
- PowerShell 버전별 빈 환경 변수와 미정의 변수 차이에 대한 의존 제거
- 삭제한 선택·기본값의 현재 셸 정리 및 다른 셸의 새 실행 차단
- 신규 등록·취소된 로그인·등록 해제 후 현재 셸 별칭 갱신
- 외부 cenv use의 부모 셸 변경 불가 안내 및 종료 코드 2
- CMD·fish의 `cenv run 이름` 사용 지원, 셸 상태 변경 성공의 허위 표시 금지

### 6.2 프로필 적용

- `install --shell zsh|bash|powershell`의 명시 선택 지원
- 기본값의 실행 중인 설치 셸 감지, 불확실한 감지 시 변경 전 선택 안내
- 선택한 셸만 변경, 설치된 모든 셸 프로필의 무조건 수정 금지
- zsh의 ZDOTDIR 및 실제 `.zshrc` 위치 반영
- bash의 대화형 `.bashrc`와 유효 로그인 프로필의 실제 로딩 순서 반영
- 기존 `.bash_profile`·`.bash_login`·`.profile`의 우선순위 보존
- 새 프로필 생성에 따른 기존 로그인 프로필 가림 방지
- PowerShell 7의 실제 `$PROFILE.CurrentUserAllHosts` 위치 반영
- Documents 경로·OneDrive 리디렉션의 문자열 고정 금지
- 한 개의 관리 구간 및 고정 로딩 파일 연결, 사용자 나머지 구간 보존
- 첫 설치 전부터 열린 셸의 새 탭 또는 선택적 초기 로딩 안내
- 초기 로딩 이후 반복 source·dot-source 요구 없음
- PowerShell 실행 정책·기업 정책에 의해 로딩 차단 시 원인 표시 및 미완료 처리
- ExecutionPolicy 전역 완화·정책 우회·무조건 관리자 실행 안내 금지
- 지원 셸의 한글 표 도움말 유지, UTF-8 및 좁은 터미널의 가독성 제공

## 7. 실행 파일 탐색·인자·프로세스

### 실행 정의

- 문자열 경로 한 개 대신 검증된 실행 파일과 고정 앞부분 인자의 구조적 표현
- 탐색 순서: 명시적 머신 설정 → 실제 PATH의 공식 CLI → OS별 알려진 설치 위치
- 현재 셸 함수·계정 관리자 진입점의 재탐색 및 재귀 호출 차단
- Homebrew 경로·`/usr/bin`·실행 비트의 전 플랫폼 전제 제거
- Windows `.exe`와 npm `.cmd`·`.ps1` 진입점의 구분
- 검증된 네이티브 Codex 실행 파일 또는 검증된 Node·공식 JS 진입점의 직접 실행
- npm 도구 설치도 같은 실행 정의 사용
- Windows 정식 지원 설치 형태: 공식 독립 codex.exe 및 검증된 @openai/codex Node 패키지
- 실행 정의 필드: kind(native/node)·executable·prefix_args·package_root·검증 버전
- npm shim 발견 시 같은 설치의 package.json name/bin·경계 내 실제 JS 파일·Node 실행 파일 확인
- Node 기반 도구 설치의 검증된 npm-cli.js 직접 실행
- 설치 레이아웃 미확인 시 executable/prefix_args의 명시 설정 또는 공식 독립 실행 파일 설치 안내
- 임의 `.cmd` 본문의 추정 파싱·명령 문자열 조합·cmd.exe 경유 인자 전달 금지
- 안전한 실제 실행 정의 확인 불가 시 명시적 경로 설정 안내 및 중단
- PowerShell의 Invoke-Expression 및 셸 eval을 통한 사용자 인자 실행 금지
- PowerShell 전체 인자 전달 설정의 영구 변경 금지

### 실행 보존

- 공백·한글·빈 인자·따옴표·후행 역슬래시·여러 줄 프롬프트의 원형 전달
- `& | < > ^ % ! $ ;` 포함 문자열의 코드 실행 방지
- stdin·stdout·stderr·TTY의 네이티브 연결 유지
- 계정 홈의 자식 프로세스 환경에 한정한 CODEX_HOME 전달
- POSIX SIGINT·SIGTERM·SIGHUP의 지원 환경별 전달 및 자식 회수
- Windows Ctrl-C·콘솔 제어 이벤트·취소·파이프 종료의 해당 OS 처리
- Windows에 SIGHUP·POSIX 종료 코드를 그대로 강제 적용하는 동작 제외
- 사용자 취소의 관리자 종료 코드 130 및 정상 네이티브 종료 코드 보존
- Windows 네이티브 실패 코드의 원본 32비트 값·부호 표현 기록, 셸별 표시 차이 명시 및 관리자 임의 8비트 절단 금지
- 실패·취소 시 관리자가 시작한 프로세스 계열의 종료, 무관한 Codex 세션 종료 금지
- iTerm 프로필·배경색 기능의 macOS 선택 기능 유지, 다른 터미널의 escape 출력 없음

## 8. 사용량·로그인 RPC

- 공식 app-server JSONL 프로토콜·응답 ID 검증·부분 실패 처리 유지
- Windows 일반 파이프에 대한 select.select 사용 제거
- 공통 수신 경계의 바이트 스트림·UTF-8 디코딩·프레이밍·기한 처리 제공
- 지원 OS 전체에서 작동하는 유한 reader·queue 방식 또는 동등한 표준 라이브러리 방식 우선
- 빈 줄·분할 응답·동시 응답·EOF·잘못된 JSON·큰 무응답 출력 처리
- 출력 버퍼·프레임 크기의 상한 및 초과 시 해당 계정 실패
- 최대 동시 5개·계정당 조회 15초·전체 `ceil(N/5) × 18 + 2`초의 기존 목표 유지
- 계정별 종료·reader 회수 3초 및 전체 최종 정리 2초의 예산 분리
- Windows의 위 시간 예산 충족 여부에 대한 G0 선행 측정 필수
- G0 미충족 시 본 구현 진행 전 원인·대안·목표 변경 필요성 제시, 시간 상한의 임의 완화 금지
- 기한 이후 추가 응답 대기 금지, 정리 실패의 cleanup_incomplete 오류 및 비공개 진단 기록
- 정리 실패를 성공 조회·정상 취소로 표시하는 동작 금지
- 한 계정의 실패와 나머지 계정 조회의 분리
- 로그인 기본값의 공식 브라우저 로그인
- 헤드리스 Linux·원격 터미널용 공식 장치 인증 선택 지원
- `cnew`·`clogin`의 `--device-auth` 전달, 해당 CLI 기능·워크스페이스 정책의 사전 확인
- 장치 인증 불가 시 명시적 사유 표시, 임의 토큰 복사·자체 OAuth 구현 제외
- OS 자격 증명 저장소·auth.json의 공식 CLI 관리 유지
- auth.json 부재만으로 미로그인 단정 금지
- 인증·키체인·자격 증명 관리자·refresh token의 OS 간 이식 제외

## 9. 파일·공유 자산·권한·복구

### 공유 자산 배치

- macOS·Linux: 가능한 기존 심볼릭 링크 방식 유지
- Windows 기본값: 관리자 권한 없는 관리 복사본 방식
- Windows 심볼릭 링크·junction의 동작 필수 조건 제외
- 링크·복사 방식과 관계없는 동일한 변경 계획·소유권·충돌·복구 계약
- 공통 원본의 sync 시 모든 등록 계정의 관리 복사본 갱신
- 관리 대상 파일·이전 내용 해시·원본 내용 해시의 기록
- 사용자 수정 복사본 발견 시 중단, 원본 우선 자동 덮어쓰기 금지
- 원본 제거 시 변하지 않은 관리 파일만 제거, 비관리 파일 보존
- 기존 POSIX 링크 원본의 직접 편집 즉시 반영 및 Windows 복사본의 sync 후 반영 차이 명시
- 이 차이의 OS별 도움말 표시, 공통 변경 완료 판정의 sync 결과 기준
- 기존 디렉터리를 통째로 교체하는 복사 fallback 금지
- 계정 등록 해제의 네이티브 데이터·공유 자산 보존 유지

### 경로와 이름

- 전 OS 공통 계정 ID·별칭 규칙 및 Windows 장치 예약 이름 차단
- 대소문자 차이·끝 공백/마침표·드라이브 루트·보호 경로 검사
- junction·reparse point·심볼릭 링크를 고려한 실제 동일·중첩 홈 검사
- Linux의 모든 경로 소문자 변환 및 다른 경로의 임의 병합 금지
- 한글·공백·역슬래시 경로의 파서·TOML 직렬화·파일 접근 일치
- 핵심 상태·변경 대상의 로컬 파일시스템 기준
- UNC·네트워크 공유·원자 교체 불가 경로의 변경 전 지원 불가 안내
- 원드라이브 등 동기화 폴더의 핵심 상태 저장 기본값 제외

### 잠금·권한·복구

- POSIX 파일 잠금과 Windows OS 파일 잠금의 동일한 배타 의미
- 프로세스 종료 후 자동 해제, PID 파일만으로 잠금 성공 간주 금지
- 동일 볼륨 임시 파일·교체 전 원본 fingerprint 재검사
- Windows 파일 점유·백신 등 일시적 교체 오류의 제한된 재시도 및 상한
- 실패 시 기존 파일 보존 또는 복구 필요 journal 유지
- POSIX fsync·Windows flush/교체의 실제 보장 차이 문서화
- 전원 장애까지 동일한 내구성을 검증 없이 보장하는 표현 금지
- POSIX 비공개 권한과 Windows 사용자 전용 ACL의 구분
- 새 비공개 상태·백업의 ACL 확보 실패 시 비밀 데이터 기록 전 중단
- 기존 인증 저장소·샌드박스 권한의 관리자 임의 재설정 금지
- 복구 대상 파일의 내용·링크/복사 유형·해당 OS 권한 복원
- 적용 이후 사용자 변경 발견 시 조건부 복구 중단
- 다른 OS의 ACL·inode·절대 경로 journal 재생 금지

## 10. 공통 스킬·MCP·플러그인 호환성

- 공유 자산별 지원 OS·필수 셸·외부 의존성·권한·대체 경로 메타데이터
- 자산 상태: 사용 가능 / 의존성 필요 / 해당 OS 미지원 / 미검증
- 기존 자산 전체 목록의 설치 계획·doctor 표시
- 해당 OS 미지원 자산의 자동 활성화 제외 및 제외 사유 표시
- 새 OS 설치의 미확인 실행형 스킬 자동 활성화·도구 설치 제외
- 기존 Mac의 이미 활성화된 스킬·도구 상태 보존 및 미검증 경고
- 메타데이터 도입만을 근거로 한 기존 활성 자산의 일괄 비활성화 금지
- 단순한 경로 치환만으로 실행형 자산의 이식 완료 판정 금지
- 순수 지침·텍스트 자산의 공통 공유
- cbimg·macOS 클립보드·AppleScript 등의 현재 Mac 전용 기능 명시
- screenshot 등의 기능별 OS 지원 차이 및 별도 의존성 표시
- uia-e·uib-a의 Node·잠금 의존성·Playwright 브라우저 준비 상태 구분
- REA·Ponytail 등 활성 관리 도구의 대상 OS 설치·기동·기본 호출 검증
- iTerm 표시·macOS 접근 권한·Chrome 프로필 경로의 공통 필수값 제외
- 미지원 도구 하나로 계정 관리자·사용량·다른 스킬 전체 중단 금지
- 앱 제공 기능·플러그인 승인·외부 서비스 인증의 대상 환경별 별도 설정
- 특정 Mac 전용 기능의 Windows 대체 구현 자동 추가 제외, 호환성 목록에서의 명시적 한계 표시

## 11. 배포 형식·무결성·OS 간 이동

### 배포 범위

- 기본: 도구·안전 초기값, 등록 계정 0개·개인 공통 자산 없음
- include-shared: 공통 원본·자산·도구 정의·호환성 목록
- include-accounts: 공유 환경 및 계정 이름·별칭·설정·기본값
- 모든 배포의 인증·신원 캐시·세션·DB·로그·백업·머신 실행 파일·venv·도구 캐시 제외
- 규칙·권한 정책 포함 시 기존 사전 검토·명시 확인 유지
- Windows ZIP 및 POSIX tar.gz 제공, 동일한 논리 콘텐츠·매니페스트 사용
- OS별 아카이브 확장자 차이와 공통 엔진 소스의 단일 원본 유지

### 경로와 설정 이동

- 사용자 홈·workspace·data 영역의 명시적 토큰 및 대상 OS 치환
- 기존 ROOT 토큰의 workspace 의미 호환 및 새 스키마의 명시적 버전
- 임의 문자열 전체의 `/` ↔ `\` 치환 금지
- URL·정규식·셸 코드·명령 인자·경로 항목의 구분
- 홈 아래 계정의 대상 OS 사용자 홈 재계산
- 가져온 외부 절대 경로의 명시적 대상 경로 매핑
- 필요한 매핑 누락 시 파일 변경 전 중단 및 대상 목록 표시
- 대상 OS 기본 설정 → 이식 가능한 공통값 → 계정값 → 머신 경로의 우선순위
- macOS 전용 샌드박스·Windows 전용 샌드박스 설정의 무조건 전파 금지
- 알 수 없는 플랫폼 민감 키의 보존 목록·적용 제외 사유 표시
- 승인·샌드박스 정책의 자동 완화 금지
- 대상 OS의 계정별 재로그인 및 외부 서비스 재인증

### 무결성

- 아카이브 경로의 `/` 정규화 및 파일 내용 바이트 기반 해시
- 실행 필요 여부의 명시적 메타데이터, Windows 파일 실행 비트에 대한 의존 제거
- Windows의 ZIP 해제 후 POSIX 재배포에서도 동일한 논리 무결성 검증
- 파일 항목 규격: path·kind(file/directory)·executable(bool)·sha256(파일만)
- path의 NFC·`/` 구분자·상대 경로 정규화 및 정규화 후 중복 차단
- 실제 파일 내용의 줄바꿈 변환 없는 SHA256, executable의 번들 메타데이터 기준
- tree 해시의 path 정렬 항목 배열에 대한 UTF-8 canonical JSON SHA256
- canonical JSON의 키 정렬·공백 없는 구분자·ensure_ascii=false·끝 개행 없음
- timestamp·inode·소유자·원시 ACL·네이티브 chmod 값의 tree 해시 제외
- LF·CRLF·UTF-8 처리의 명시화 및 복사 과정의 임의 줄바꿈 변환 금지
- 파일명 대소문자 충돌·Windows 예약 이름·절대 경로·경로 탈출·링크 검사
- 파이프·device file·특수 파일·외부 링크의 배포 제외
- 기존 해시 형식과 새 형식의 버전 분리
- 기존 고정 플러그인의 출처 commit·버전 유지, 해시 알고리즘 변경을 공급자 업데이트로 취급하는 동작 금지
- v2 Mac 설치의 v3 업그레이드 및 새 포맷 재내보내기 지원
- 구포맷을 검증할 수 없는 환경의 명시적 재내보내기 안내, 검증 생략 수용 금지

## 12. 진단과 업그레이드

### doctor 출력

- OS·아키텍처·셸·Python·공식 CLI 실행 정의·workspace·권한·파일 배치 방식
- 셸 연결·선택/기본값·인증 확인 시각·설정 충돌·미완료 transaction
- 자산별 호환성·도구 준비 상태·실행 정책 차단
- 필수 실패·선택 기능 미지원·미검증·일반 경고의 구분
- 기본 로컬 조회, online 요청 시에만 로그인·사용량 네트워크 확인
- 해결 가능한 다음 명령의 OS별 문법 제공

### 현재 Mac 업그레이드

- 기존 registry·workspace·홈·기본값·별칭·모델·인증 위치 보존
- 기존 경로를 Windows/Linux 형태로 일괄 변환하는 작업 제외
- 기존 zsh 연결의 v3 엔진 연결로의 교체
- workspace 및 상태 스키마의 계획·백업·명시적 업그레이드 기록
- 업그레이드 후 기존 다섯 계정의 인증·미관리 값·명령 보존 비교
- 기존 journal의 버전별 복구 지원 또는 복구 완료 전 업그레이드 중단
- 같은 OS에서의 이전 버전 복구 경로 제공

## 13. 수용 기준

| ID | 시나리오 | 통과 조건 |
| --- | --- | --- |
| CP01 | Windows 네이티브 최초 설치 | WSL·관리자·Developer Mode 없이 PowerShell 7 환경에서 도구 설치·도움말·계정 0개 |
| CP02 | macOS 기존 환경 업그레이드 | 5개 계정·인증·기록·모델·기존 명령 보존 |
| CP03 | Linux 빈 환경 설치 | bash·zsh 각각의 자동 로딩·독립 계정 2개 등록 |
| CP04 | 셸 상태 | 새 터미널·같은 셸·자식 셸·외부 CODEX_HOME의 선택 규칙 일치 |
| CP05 | 등록·로그인 | OS별 계정 2개 독립 로그인·취소·중복 등록 방지·재시도 |
| CP06 | 파이프·사용량 | 세 OS에서 분할 JSONL·다중 응답·시간 상한·부분 실패·취소·회수 |
| CP07 | 인자 전달 | 한글·공백·빈 문자열·따옴표·후행 역슬래시·셸 특수문자의 원형 전달 |
| CP08 | 프로세스 종료 | POSIX 신호·Windows Ctrl-C·사용량 취소 시 시작한 자식만 종료 및 회수 |
| CP09 | Windows 복사 배치 | 심볼릭 링크 권한 없는 등록·sync·충돌 감지·보존·복구 |
| CP10 | 파일 잠금·실패 | 동시 변경 차단·프로세스 강제 종료 후 잠금 해제·점유 파일 오류·복구 |
| CP11 | 경로·권한 | 예약 이름·대소문자 충돌·junction 중첩·보호 경로·비공개 ACL 검사 |
| CP12 | 등록 해제 | 네이티브 데이터 보존·기본값/별칭 정리·다른 셸의 오래된 실행 차단 |
| CP13 | 다른 사용자 배포 | 기본 배포 계정 0개·공유 배포 개인 계정 없음·새 사용자 계정 등록 |
| CP14 | OS 간 이동 | Mac→Windows·Windows→Linux·Linux→Mac의 공유/계정 구성 이동·재로그인 |
| CP15 | 자산 무결성 | OS 간 동일 해시·실행 메타데이터·UTF-8·아카이브 경로 안전성 |
| CP16 | 스킬·도구 | 활성 도구의 대상 OS 기동·미지원 스킬의 표시·코어 기능 정상 동작 |
| CP17 | 헤드리스 Linux | 허용된 테스트 계정의 장치 인증 성공·비활성 정책/미지원 CLI의 명확한 실패·데이터 보존 |
| CP18 | 프로필·정책 | ZDOTDIR·bash 로그인 순서·PowerShell 프로필 경로·정책 차단의 무손실 처리 |
| CP19 | 도움말·기본 CLI | 모든 지원 OS의 한글 표·명령별 도움말·상세 설치/미지원 셸 안내 |
| CP20 | 기존 회귀 | 현행 111개 테스트의 의도 유지·플랫폼별 추가 검증·구포맷 이전 검증 |

- 기존 unittest·fixture 우선 재사용, 새 테스트 프레임워크 추가 없음
- 실제 OS runner 또는 VM에서의 플랫폼 기능 검증
- Windows 네이티브 실행 검증을 WSL·Mac mock 결과로 대체하는 완료 판정 금지
- fixture·실제 프로세스·실제 로그인·실기기 결과의 별도 기록
- 셸 공통 시나리오의 zsh·bash·PowerShell 반복 수행
- 필수 플랫폼 수용 실패 시 세 OS 지원 완료 표시 금지
- 설치 도구의 관리자 권한 불필요와 공식 Codex 샌드박스의 자체 정책/승인 요구 구분

## 14. 단계별 구현과 출시 문턱

| 단계 | 변경 범위 | 다음 단계 진입 조건 |
| --- | --- | --- |
| G0 선행 기술 검증 | 격리된 Windows 검증 코드·실제 설치본 조사, 운영 환경 변경 없음 | 아래 T01–T06 통과 및 정확한 설치 조합 확정 |
| G1 엔진 이식 | OS/셸 경계·Windows 최소 관리 복사 배치/충돌/복구·RPC/프로세스·bash/PowerShell 추가 | 기존 Mac 경로·v1 등록부 유지 상태의 세 OS 계정 흐름 통과 |
| G2 설치·포맷 전환 | 프로그램/workspace 분리·패키징·v2 데이터 형식·업그레이드/제거 | 설치·업그레이드·실패 복구·재설치·제거 검증 |
| G3 배포·자산·출시 | 자산 호환성 목록·복사 배치 전체 조합 검증·OS 간 배포·최종 문서 | CP01–CP24 및 필수 세 조합 통과 |

### G0 필수 확인

| ID | 검증 | 확정 산출물 |
| --- | --- | --- |
| T01 | Windows 공식 독립 실행 파일의 기동·argv·app-server | 네이티브 CLI 실행 정의·버전 |
| T02 | Node/npm 설치 형태의 직접 진입점과 npm 설치 실행 | 지원 패키지 레이아웃·탐색/검증 규칙 |
| T03 | PowerShell 7.4 함수→manager.exe→native의 인자·stdin·종료 코드 | 셸 인자 전달 규칙·빈 인자/특수문자 결과 |
| T04 | 파이프 분할·EOF·타임아웃·출력 초과·reader 회수 | RPC 방식·프레임/버퍼 상한 |
| T05 | 자식/손자·Ctrl-C·강제 종료·관리자 비정상 종료 | 프로세스 소유권 추적·종료 전략·예산 측정 |
| T06 | 일반 사용자 ACL·파일 점유·잠금·원자 교체·복구 | 권한/잠금 구현·재시도 예산·복구 범위 |

- G0 산출물의 실측 결과·실행 명령·대상 버전·실패 조건 기록
- WSL 또는 Mac mock을 Windows 선행 검증으로 대체하는 동작 금지
- 필수 검증 환경 부재 시 미검증/차단 항목 보고, 추정 구현의 선행 완료 주장 금지
- G1의 저장소 이동·패키지 구조 전면 변경 제외
- G2에서만 설치 구조·데이터 스키마 전환, 현재 Mac workspace 위치의 계속 유지
- 중간 단계의 전체 완료 보고 금지
- 기존 승인·데이터 보존·충돌 방지 기능의 포팅 편의를 위한 제거 금지

## 15. 데이터·배포·경로 매핑 규격

### 15.1 버전 규격

| 대상 | 신규 형식 | 이전 형식 처리 |
| --- | --- | --- |
| accounts.toml | version=2 | 현재 version=1의 같은 OS 업그레이드 변환 |
| BUNDLE-INFO.json·MANIFEST.json | version=2 | v1 Mac 환경의 업그레이드 후 재내보내기 |
| 자산 해시 | algorithm=sha256-tree-v2 | 현재 해시와 별도 검증·변환 기록 |
| journal | version=2·platform·workspace_id | 미완료 v1 journal의 같은 OS 복구 완료 전 전환 중단 |

- 제품 명세 버전과 저장 파일 스키마 버전의 구분
- 미지원 스키마의 읽기 오류 및 변경 전 중단
- 업그레이드 전 workspace 스냅샷·적용 후 revision 기록
- workspace_id의 머신 식별자·로그인 신원과 무관한 임의 ID

### 15.2 등록부

```toml
version = 2

[[accounts]]
id = "work"
aliases = ["cwork"]
label = "업무"
home = { base = "home", path = ".codex-work" }
origin = "created"
```

- 기존 id·aliases·label 및 알 수 없는 보존 가능 필드의 유지
- home.base: home / absolute / mapping 중 하나
- home 경로의 사용자 홈 기준 상대 경로, 경로 탈출 금지
- absolute 경로의 path·source_os 기록, 로컬 등록에 한정
- 외부 절대 홈 내보내기의 mapping 전환, 절대 원본 경로의 배포 제외
- mapping의 고정 키 `account.<id>.home`
- 환경 변수·물결표·셸 표현식의 암묵적 재귀 확장 금지

### 15.3 배포 정보와 매핑

- BUNDLE-INFO 필수값: version·kind·source_platform·manager_version·workspace_schema·minimum_cli·required_mappings·asset_capabilities
- kind: toolkit / shared / accounts
- toolkit·shared의 accounts 빈 목록, accounts의 등록부·계정 원본·기본값 포함
- required_mappings: 계정 외부 홈 및 명시적 로컬 도구 경로의 키·용도·경로 종류
- archive의 절대 로컬 실행 경로·venv·인증 자료 제외

```toml
# paths.toml — 대상 머신에서만 보관할 입력
version = 1
[paths]
"account.work.home" = "D:/CodexProfiles/work"
```

```text
cenv install --bundle 환경파일 --map-file paths.toml --shell powershell --dry-run
cenv install --bundle 환경파일 --map-file paths.toml --shell powershell
```

- install의 번들 내 manifest 검사·workspace 적용·선택 셸 연결 책임
- 최초 부트스트랩의 manager 설치 후 같은 install 계약 호출
- `--bundle` 생략 시 현재 workspace의 기존 설치/동기화 호환 동작
- 필요한 키 누락·알 수 없는 키·대상 외부 경로 충돌의 적용 전 일괄 오류
- 비대화형 환경의 누락 매핑 질문 대기 금지
- 대화형 환경의 경로 입력 후 전체 계획 재표시, 확인 전 무변경
- 서로 다른 OS에 journal·기존 managed ledger 재생 금지

### 15.4 설정 이동 분류

| 분류 | 항목 | 대상 OS 처리 |
| --- | --- | --- |
| 공통 | 모델·추론·personality·service_tier·검증된 공통 기능 | 기존 우선순위에 따른 보존 |
| 구조적 경로 | account home·agent config_file·관리 도구 실행 정의 | 토큰/매핑 및 대상 OS 생성 |
| OS 전용 | windows.*·iTerm 표시·macOS 명령·플랫폼별 도구 옵션 | 일치 OS에만 활성 적용 |
| 승인·권한 | approval_policy·approvals_reviewer·sandbox_mode·권한 규칙 | 명시 검토·지원값 검사, 자동 완화 없음 |
| 네이티브 로컬 | 인증·프로젝트 trust·앱 상태·공급자 승인 | 배포 제외 |
| 미분류 | 위 목록 밖의 계정/공통 설정 키 | 번들에 보존·대상 적용 보류·항목별 사유 표시 |

- 지원 키·값의 최소 Codex 버전별 allowlist 파일 관리
- 미분류 키의 조용한 폐기·추정 경로 변환·자동 활성 적용 금지
- 설치 계획의 적용·재생성·보류·충돌 목록 분리
- 승인·권한 의미가 불명확하거나 기존 보호 수준의 동등성을 확인할 수 없는 항목의 실행 준비 미완료 처리
- 위 항목의 명시 검토·해결 전 프로필 실행 차단, 설정 보류만으로 조용한 완료 처리 금지
- 기존 같은 OS 업그레이드의 미관리 네이티브 값 보존

## 16. 설치·업데이트·제거 계약

| 명령 | 의미 |
| --- | --- |
| cenv install [--bundle 파일] [--shell 셸] | 설치된 엔진의 workspace 적용·셸 연결 |
| cenv upgrade --package 파일 --dry-run | 새 엔진·스키마·기존 상태의 호환 계획 |
| cenv upgrade --package 파일 | 스키마 변경 없는 검증된 엔진 갱신 |
| cenv upgrade --package 파일 --migrate | 명시적 데이터 형식 전환을 포함한 갱신 |
| cenv upgrade --rollback | 이전 엔진·필요한 관리 원본의 조건부 복원 |
| cenv uninstall --dry-run | 관리자 제거 범위·잔여 보존 경로 표시 |
| cenv uninstall [--yes] | 관리자 제거, 사용자 환경·인증·기록 기본 보존 |

### 설치·업데이트

- 새 버전의 별도 프로그램 디렉터리 준비·설치 파일 검증·로컬 smoke 확인
- 경로 예시: data/app/<version> 및 data/workspace, 기존 Mac은 원래 workspace 계속 사용
- 활성 버전 연결점의 검증 완료 후 전환
- 준비 실패 시 현재 실행 버전·셸 연결·workspace 유지
- 스키마 변경 발견 시 --migrate 없는 갱신 중단
- migration 계획·백업·원본 revision 검증 후 변경
- 업데이트 중 실행 중인 Codex 세션 강제 종료 없음
- 다른 cenv 변경과의 공통 잠금 및 구버전 프로세스의 새 스키마 변경 차단
- 최소 한 개 이전 엔진 및 대응 복구 스냅샷 보존
- 원본/프로필의 후속 사용자 변경 시 rollback 중단 및 충돌 표시
- 현재 스키마를 읽을 수 없는 구버전 엔진의 단독 활성 전환 금지
- 프로그램 경로의 같은 버전 재설치와 상태 데이터 재초기화의 구분

### 관리자 제거

- 제거: 소유권 기록이 일치하는 프로그램·전용 venv·진입점·활성 연결·프로필 관리 구간
- 보존: workspace·계정 홈·인증·기록·백업·사용자 설정·외부 공통 도구
- 보존 경로·다시 연결할 workspace·재설치 방법의 결과 안내
- 전역 Python·공식 Codex·Node·사용자 설치 npm 패키지의 제거 없음
- 사용자가 수정한 프로필·진입점 충돌 시 미리 표시 및 무조건 삭제 금지
- 지원 셸에서 로딩된 관리 함수의 정리 및 원래 native codex 실행 경로 복귀
- 외부 실행 제거 후 남은 셸 함수의 관리자 없음 안내, 잘못된 계정 실행 금지
- Windows 실행 파일 점유 시 외부 호스트 Python 기반 정리 단계·제한 재시도
- 지우지 못한 항목의 removal_pending 기록·재실행 가능한 정리 명령 제공
- 부분 제거의 성공 종료·전체 제거 완료 표시 금지
- 실행 중인 다른 세션의 강제 종료를 통한 파일 점유 해소 금지

## 17. 관리 자산 동기화 규칙

- B: 이전 적용 내용, C: 현재 대상 내용, N: 새 원본 내용
- 전체 파일 계획의 먼저 계산, 충돌 시 기본 전체 적용 중단
- 동기화 소유권: workspace_id·asset_id·상대 경로·배치 방식·B 해시·원본 revision
- 신원·토큰의 소유권 ledger 기록 제외

| 상태 | 처리 |
| --- | --- |
| 신규 N 존재·C 없음 | 관리 파일 추가 |
| 신규 N 존재·미관리 C 존재 | 내용 동일 시 명시적 가져오기 외 소유권 자동 취득 없음, 차이 시 충돌 |
| C=B·N 변경 | 새 내용 적용 |
| 기존 관리 대상이며 C=N | 내용 재작성 없음, ledger만 필요한 경우 갱신 |
| C≠B·C≠N | 사용자 변경 충돌, 자동 덮어쓰기 없음 |
| N 없음·C=B | 해당 관리 파일만 제거 |
| N 없음·C≠B | 사용자 수정 파일 보존 및 충돌 |
| 관리 파일을 사용자 삭제·N 존재 | 충돌, 자동 복원 없음 |
| 관리 밖 파일 | 변경 없음 |

- 이름 변경의 구경로 제거+신경로 추가 계획, 추측 rename 없음
- 파일 계획의 디렉터리 통째 교체 금지
- 비어 있는 관리자 생성 디렉터리만 소유권 확인 후 정리
- 같은 동기화 revision의 모든 대상 계정 적용 기록
- 적용 중 실패 시 복구 시도, 복구 충돌 시 계정별 미완료 revision 표시 및 후속 변경 차단
- POSIX 링크 대상 원본 변경과 Windows 복사본 수정의 각각에 대한 충돌 검증
- 계정 해제 시 관리 복사본/링크의 네이티브 홈 내 기본 보존

## 18. 자산·인증 기능 지원표

### 자산 상태의 독립 축

| 축 | 값 |
| --- | --- |
| OS 선언 | 지원 선언 / 미지원 / 미분류 |
| 의존성 | 준비 / 누락 / 확인 불가 |
| 서비스 인증 | 필요 없음 / 확인 / 미확인 |
| 활성화 | 활성 / 비활성 |
| 검증 | 실제 기동 확인 / 정적 검토만 / 미검증 |

- asset-capabilities.toml의 asset_id·source_ref·OS별 선언·실행 의존성·서비스 의존성·검증 근거 경로
- CLI tools list 및 doctor의 위 독립 상태 표시
- 최초 목록 작성 대상: assets/skills 전체·역할·규칙·MCP·sources.toml의 모든 도구
- cbimg: 현재 macOS 전용 구현의 선언, Windows/Linux 대체 구현 자동 추가 없음
- screenshot: OS별 구현·기능 제한·GUI/화면 권한 확인
- uia-e·uib-a: Node/잠금 의존성/브라우저 준비와 OS 지원의 구분
- REA·Ponytail: 버전 고정·대상 OS 실제 설치/기동/기본 호출의 증거 확보
- 현재 Mac 활성 자산의 유지, 미분류 메타데이터를 이유로 한 회귀성 비활성화 금지
- 새 OS의 미지원·미분류 실행형 자산 기본 비활성 및 이유 표시
- 새 OS의 자동 활성 대상: 지원 선언·필수 의존성 충족·명시적 설치 선택이 모두 있는 자산
- 외부 인증 미확인의 무조건 로그인 실패 판정 금지
- 자산 단위 제외가 코어 관리자 지원 실패로 전파되는 동작 금지

### 인증과 사용량

| 인증 방식/상태 | 로그인·실행 | 신원 표시 | 사용량 |
| --- | --- | --- | --- |
| ChatGPT 브라우저 로그인 | 공식 CLI 결과 및 정책 기준 | 공식 응답의 확인된 필드만 표시 | 실제 rate-limit 응답이 있는 경우 표시 |
| ChatGPT 장치 인증 | CLI 기능·계정/워크스페이스 허용 전제 | 위와 동일 | 위와 동일 |
| API 키/기타 공급자 인증 | 공식 CLI가 허용하는 실행 범위 | 제공된 신원만 표시, 이메일 추정 없음 | 응답 미제공 시 미지원/데이터 없음 표시 |
| auth.json 없는 OS 저장소 | 파일 부재만으로 실패 판정 없음 | 공식 온라인 확인 또는 미확인 | 실제 응답 기준 |
| 장치 인증 정책 차단 | 등록 보존·정책 사유 표시 | 미확인 | 로그인 실패와 별도 사유 |

- 장치 인증의 --device-auth, API 키의 --with-api-key 옵션을 cnew/clogin에서 공식 CLI에 전달
- API 키 입력의 stdin 한정 및 인자·등록부·로그·배포 기록 제외
- 둘 이상의 인증 방식 동시 요청의 입력 오류
- 일반 로그인 기본값의 기존 브라우저 로그인 유지
- 공식 인증 성공과 사용량 미제공 상태의 분리
- cusage 결과별 상태: 조회 성공 / 인증 오류 / 조회 오류 / 사용량 미제공
- 알려진 사용량 미제공의 실패 계정 취급·임의 한도 생성 없음
- 전체 결과 정상/알려진 미제공만 존재 시 0, 실제 조회/인증 실패 포함 시 1, 취소 130
- 네트워크·CLI 기능 부족·정책 차단·사용자 취소의 구분
- 자격 증명 저장 방식의 관리자 임의 전환·인증 복사 제외

## 19. 프로세스·오류·검증 산출물 보완

### 프로세스 소유권

- 관리자가 시작한 프로세스의 핸들·생성 시각·계열 식별 기록
- Windows Job Object 또는 T05로 동등한 격리·회수가 확인된 방식
- POSIX의 소유 프로세스 그룹·자식 추적, 사용자 전체 Codex 프로세스 검색 종료 금지
- PID 재사용 검증 없는 종료·잔여 PID 파일만을 근거로 한 강제 종료 금지
- 대화형 실행의 콘솔 연결 유지와 비대화형 RPC의 제한 실행 분리
- T05에서 부모만 종료·손자 생성·파이프를 잡은 손자·Ctrl-C·강제 종료의 각각 검증
- 운영체제 정책으로 필수 소유권 추적이 불가능한 경우 프로세스 시작 전 명확한 실패

### RPC 상한과 오류

- 단일 JSONL 프레임 및 계정별 수신 버퍼의 각각 4 MiB 상한
- 프레임 상한 초과의 protocol_error 및 해당 계정 종료
- stdout 진단 혼입·잘못된 UTF-8·응답 ID 불일치의 구분
- 조회 deadline 이후 정리 시작, 정리 실패의 cleanup_incomplete 기록
- 소유 프로세스가 실제 종료되지 않은 경우 잔여 핸들/생성 시각·재시도 경로 기록
- 운영체제 외부 장애까지 무조건 무잔여를 보장하는 표현 금지
- 정상·실패 경로 모두 시간 예산과 소유권 검사, 상한 미충족의 출시 차단

### 추가 수용 기준

| ID | 시나리오 | 통과 조건 |
| --- | --- | --- |
| CP21 | 설치 생명주기 | 재설치·스키마 전환 없는 갱신·명시 migration·중간 실패·조건부 rollback |
| CP22 | 관리자 제거 | 원래 codex 복귀·workspace/인증 보존·점유 파일의 pending·정리 재시도 |
| CP23 | 동기화 처리표 | 위 모든 B/C/N 행과 다계정 부분 적용 실패의 보존·복구 |
| CP24 | 인증 기능표 | 브라우저·장치·API 키 stdin·OS 저장소·정책 차단·사용량 미제공의 구분 |

- 지원 조합별 결과 파일: OS/빌드·아키텍처·셸·Python·Codex·Node·설치 방식·원본 revision·실행 명령·종료 코드·시간·로그 위치
- 전체 111개 테스트의 단순 skip 개수 합격 처리 금지
- 공통 의미 테스트의 전 플랫폼 재사용 및 POSIX 전용 테스트의 대응 Windows 시나리오 확보
- 실제 신원 검증 계정의 별도 제공·권한 범위 준수, 자격 증명 로그 제외
- 현재 명세 작업의 G0·실제 인증·실기기 테스트 미수행

## 20. 조사 근거

### 현재 소스

- `codex_env/core.py`: Homebrew 탐색·zsh 생성·POSIX 홈 및 경로 전제
- `codex_env/accounts.py`: 계정 등록·해제와 zsh 파일 생성의 결합
- `codex_env/transaction.py`: fcntl·chmod/fchmod·POSIX directory fsync·링크 전제
- `codex_env/usage.py`: 일반 파이프 대상 select.select
- `codex_env/runtime.py`: POSIX 신호·iTerm 표시
- `codex_env/assets.py`: 심볼릭 링크·OS 구분자/실행 비트 기반 tree_hash
- `codex_env/tools.py`: npm 실행 탐색·POSIX 도구 경로
- `codex_env/bundle.py`: macOS 배포 설명·POSIX 경로 검사·현재 해시 형식

### 공식 자료

- Windows 네이티브 Codex와 샌드박스 범위: [OpenAI Docs — Windows sandbox](https://learn.chatgpt.com/docs/windows/windows-sandbox)
- 공식 브라우저·장치 인증: [OpenAI Docs — Authentication](https://learn.chatgpt.com/docs/auth)
- Windows select의 소켓 제한: [Python 3.11 — select](https://docs.python.org/3.11/library/select.html)
- PowerShell 네이티브 인자·batch 처리 차이: [Microsoft Learn — about_Parsing](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_parsing?view=powershell-7.5)
- 로컬 CLI 0.163.0의 `codex login --help`: device-auth 옵션 존재 확인

- 위 자료의 기술 제약과 본 도구의 설계 결정 구분
- 플랫폼별 구현·인증·실기기 검증의 이번 명세 작업 내 미수행
