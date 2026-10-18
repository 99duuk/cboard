# Windows 명령 설치 묶음

## 실행

1. `cboard-windows.zip` 한 개의 Windows 복사
2. ZIP 전체 압축 해제
3. `START-WINDOWS.cmd` 더블클릭
4. 설치 후 자동으로 열린 PowerShell에서 `cusage`·`chelp` 실행

- 이전 `cboard-windows-preview.zip`과 별개의 명령 설치본
- 기존 `preview-*` 폴더의 삭제·재실행 불필요
- 기존에 열려 있던 PowerShell의 수동 source 불필요, 새로 열린 준비 완료 창 사용
- 이후 실행: 바탕화면 `Cboard Accounts` 바로가기 또는 새 PowerShell
- 계정 수 0개: `No registered accounts; use cnew personal` 안내
- 계정 추가·기존 계정 가져오기·로그인: 설치 중 실행 없음
- 사용자의 명시적 계정 추가: `cnew personal`, 추가 전 실행 불필요

## 설치 범위

- 대상: Windows x64·ARM64, Python 3.11 이상
- Python 미설치: winget 사용자 범위 Python 3.12 설치
- Python 및 winget 부재: 원인 안내 및 중단
- PowerShell 7.4 이상 미발견: 공식 PowerShell 7.6.7 휴대용 ZIP 다운로드·SHA-256 확인
- Codex 네이티브 실행 파일 미발견: 공식 npm 플랫폼 패키지 0.163.0 다운로드·SHA-512 확인
- Codex 기존 설치 발견: 버전 기동 확인 및 기존 실행 파일 재사용
- 인터넷 연결 필요, 전역 npm·Node·WSL·SSH 설치 불필요
- 설치 권한: 현재 사용자, 시스템 PATH·시스템 프로필·머신 실행 정책 변경 없음
- 실행 명령: `chelp`, `cnew`, `cdel`, `clogin`, `clist`, `ccurrent`, `cuse`, `cdefault`, `crun`, `cusage`, `creset`, `cenv`, `codex`
- 독립 계정별 `CODEX_HOME`, 신규 Windows 계정의 파일 인증 저장소 지정
- 명령 소스: `%LOCALAPPDATA%\Cboard\account-manager`
- Python·보드 버전별 설치: `%LOCALAPPDATA%\Cboard\install-*`
- 셸 연결: `~/.config/codex-environment/shell.ps1` 및 Documents의 PowerShell 프로필 관리 구간
- 기존 프로필 내용·사용자 계정·인증·네이티브 설정·히스토리 보존
- 업데이트 대상: 관리자 코드, 기존 등록부·계정별 설정·공통 자산 제외
- Cboard 기본 목록: 계정 관리자의 등록부, 명시된 별도 Cboard accounts 설정의 우선 적용

## 설치 완료 판정

- 실제 설치 환경의 파일 적용·롤백·잠금 경합·파이프 RPC 자체 검사
- 실제 PowerShell에서 모든 함수 연결 및 `chelp`·`cusage --help`·`clist`·`cboard --help` 검사
- 위 검사 실패 시 종료 코드 1 및 설치 실패 표시
- 검사 통과 후 종료 코드 0, 새 PowerShell 실행 및 바로가기 생성
- 결과: 출력된 `evidence/runtime-checks.json`·`evidence/installation.json`
- 전체 G0 완료나 모든 Windows 기능 검증의 대체 불가

## 남은 검증·한계

- 개발 Mac의 회귀 테스트 및 실제 PowerShell 엔진 검사, Windows 설치 최종 실측 미수행
- Cboard Unix 소켓 기반 기존 세션의 실시간 관찰: Windows 미지원 및 화면 내 원인 표시
- Codex 실행 관리자의 강제 종료 시 모든 자손 회수 보장: 미완료
- Windows 디렉터리 변경의 정전 내구성: 보장 제외
- 명령 설치 및 계정 관리와 Cboard 실시간 관찰의 지원 범위 구분

## 묶음 생성

```sh
python3 scripts/windows/build_bundle.py
```

- 현재 추적 소스 변경 및 추가 구현 모듈의 스냅샷
- 오래된 HEAD 아카이브의 재사용 없음
- 개인 등록부·인증·배포 묶음·가상환경 제외
- 소스별 SHA-256 및 ZIP 무결성 자체 확인

- 실행 의존성의 `requirements-runtime.lock` 사용, pytest·개발 플러그인 제외
- Python venv의 고정 경로 재사용, PowerShell ZIP의 압축 해제 후 다운로드 캐시 제거
