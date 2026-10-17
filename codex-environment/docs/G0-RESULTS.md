# 크로스 플랫폼 구현 진행 상태

- 기준 명세: `SPEC-CROSS-PLATFORM.md` v3.1
- 현재 단계: G0 검증 도구 구현, 실제 Windows 실행 대기
- 전체 구현 완료 여부: 미완료
- G1–G3 운영 코드 구현: G0 통과 전 미진입

## 이번 변경

- `tools/g0/windows_probe.py`: 격리된 CLI·Node·파이프·잠금·교체·ACL 관측 수집
- `tools/g0/process_probe.py`: Job Object 소유 계열·손자 종료·회수 프로브
- `tools/g0/argv_probe.py`: 오프라인 임시 console launcher 생성
- `tools/g0/run.ps1`: PowerShell argv 왕복·개별 보고서·통합 gate 수집
- `tests/test_g0_*.py`: 기존 unittest 기반 로컬 회귀 검증 9개 추가
- 기존 운영 코드·설치·셸·계정 등록부 변경 없음

## 검증 결과

- 실행 환경: macOS 26.6.2 arm64, Python 3.11.9, Codex CLI 0.163.0
- 변경 전 기준 테스트: 111개 통과, 14.811초
- 변경 후 전체 테스트: 120개 통과, 17.689초
- 기존 운영 파일 비교: 25개 동일
- 기존 5개 계정: 인증 파일 inode·크기·mtime·mode 동일, config SHA-256 동일
- 계정 등록부: SHA-256 동일
- 인증 내용 비교·복제: 미수행
- Mac 실행 프로브: Windows 항목 `not_applicable`, 종료 코드 1, Windows 통과 근거 없음
- PowerShell 구문·실행: 실제 엔진 부재에 따른 미검증
- Windows PC·VM: 현재 환경에서 실행 가능 대상 미발견, 사용자 환경 정보 대기

## G0 잔여 범위

| 항목 | 잔여 근거 |
| --- | --- |
| T01 | 실제 Windows 공식 CLI 기동·argv·app-server 실행 정의 확정 |
| T02 | 실제 Node/npm 설치 조합, 격리 fixture 설치 실행 |
| T03 | 실제 PowerShell argv·stdin·stderr·종료 코드 전달, 최종 G2 launcher 별도 검증 |
| T04 | Windows 익명 파이프·기한·reader 회수 실측 |
| T05 | Windows Job 실측, 실제 콘솔 Ctrl-C·manager 비정상 종료·파이프 보유 손자·비소유 프로세스 보존 |
| T06 | Windows 잠금·점유·교체 실측, 유효 ACL·타 사용자 접근 거부·crash/journal 복구 |

- 현재 패키지 단독의 G0 전체 통과 불가
- `partial`·`not_run`·`not_applicable`의 전체 성공 승격 금지
- 실제 Windows 근거 확보 및 잔여 프로브 보완 후 G1 진입

## 전달 산출물

- `dist/codex-environment-g0-windows.zip`: 검증 스크립트·명세·계획·진행 상태만 포함
- 사용자 계정·인증·native 설정·사용 기록·공통 skills의 포함 없음
- 상세 실행: `tools/g0/README.md`
- 비공개 로컬 증거: `~/.local/state/codex-environment/cross-platform/20261017T103710Z/`
- 전체 테스트 로그: `~/.local/state/codex-environment/cross-platform/g0-tests-final.log`
