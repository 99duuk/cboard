# G0 선행 검증 및 첫 운영 코드 변경 계획

- 기준: `SPEC-CROSS-PLATFORM.md` v3.1의 G0·T01–T06
- 상태: 현재 소스의 읽기 전용 조사 완료, Windows 실측 결과 대기
- 작업 경계: 본 문서 작성만 수행, 운영 코드·테스트·인증·사용자 설정 변경 없음
- 증거 구분: 기존 Mac 테스트 / 격리 fixture / Windows 실제 프로세스 / 공식 CLI / 실제 인증의 별도 기록
- G1 진입 조건: Windows 실측 T01–T06 통과 및 설치 조합 확정

## 1. 현재 구조의 확인 사항

- `bin/*`: Python 3.11 검사와 import 기반 shebang 스크립트, Windows `.exe` launcher 부재
- Python 3.11 최소 조건: `tomllib`·타입 구문 사용 및 각 entry script 검사
- `pyproject.toml`·`setup.py`·`setup.cfg`·운영체제별 installer 부재
- TOML 편집: 동봉 `vendor/tomlkit` 사용, 새 패키지 설치 불필요
- `cli`·`runtime`·`core`·`accounts`의 `transaction` 의존 및 `transaction`의 무조건 `fcntl` import
- 결과: Windows의 기본 도움말도 현재 production import 경로에서 실패 가능
- `core.resolve_cli()`: 문자열 실행 경로 반환, Homebrew 우선, POSIX 실행 비트 검사
- `usage.receive()`: `select.select()`의 일반 파이프 적용, 바이트 pending 버퍼 1 MiB 제한
- `runtime.run()`: 직접 자식 관리, 무조건 `SIGHUP` 참조, 손자 프로세스 소유권 추적 부재
- `transaction`: `flock`·chmod·디렉터리 fsync·symlink·POSIX mode 기반 fingerprint
- `core.install_changes()`·`accounts.register/remove()`: zsh 생성 및 POSIX 링크 배치 전제
- 기존 테스트의 가짜 실행 파일: POSIX shebang·chmod 사용, Windows에서의 동일 실행 보장 없음

## 2. T01–T06의 증거와 기존 회귀 연결

| ID | 실제 코드 접점 | 기존 테스트의 재사용 범위 | 추가 Windows 필수 증거 |
| --- | --- | --- | --- |
| T01 | `core.resolve_cli`, `runtime.run`, `usage.account_request`, `cli.login/doctor`, `tools.tool_plan` | `test_core.test_cli_resolution_supports_both_homebrew_locations`, `test_runtime.test_native_arguments_environment_and_exit_preserved`, `test_usage.test_identity_read_uses_official_rpc_and_allowlists_metadata` | 공식 독립 CLI의 출처·버전·기동·격리 홈 app-server initialize, 검증된 실행 정의 |
| T02 | `core.resolve_cli`, `tools.node_path/tool_plan/install_tools`, `sources.toml` | `test_tools.test_rea_pinned_shared_install_and_portable_fragment`, `test_tools.test_plans_only_enabled_user_plugin_per_account_and_dry_run_has_no_writes` | 실제 Node/npm 설치 레이아웃, package.json name/bin·경계 내 JS·node.exe·npm-cli.js 검증, 로컬 fixture 패키지 설치 |
| T03 | `core.shell_text`, `runtime.run`, `bin/*`, 신규 PowerShell 연결 | `test_cli.test_shell_home_and_arguments_are_literal_with_metacharacters`, `test_runtime.test_native_arguments_environment_and_exit_preserved`, `test_core.test_generated_shell_quotes_args_and_does_not_leak_account_home` | PowerShell 7.4 함수→임시 console launcher exe→native helper의 argv·stdin·stderr·종료 코드 원형 전달 |
| T04 | `usage.send/receive/account_request/QueryRun/snapshot` | `test_usage`의 coalesced response·EOF·timeout·broken JSON·최대 5개·취소 사례 | Windows 익명 파이프의 분할 UTF-8·동시 응답·EOF·잘못된 JSON·과다 출력·침묵·reader 종료 및 시간 측정 |
| T05 | `runtime.run`, `usage.QueryRun.stop/account_request`, 직접 subprocess 호출 | `test_runtime`의 SIGTERM·KeyboardInterrupt·handler 복원, `test_usage`의 timeout·interrupt | 소유 자식·손자 추적, 실제 콘솔 Ctrl-C, manager 강제 종료, unrelated process 보존, deadline·reader/handle 회수 |
| T06 | `transaction` 전체, `core.private_write`, `tools.install_tools` 백업 | `test_transaction`의 잠금·crash·복구·내용 충돌·부모 보존·private mode 사례 | 표준 사용자 SID/ACL·실효 접근 범위·점유 handle·배타 잠금·프로세스 종료 후 잠금 해제·replace 실패/복구 |

- 기존 테스트명은 해당 파일의 메서드명 기준
- T01 공식 CLI와 T03 helper의 결과 분리: 공식 CLI의 임의 argv echo 기능 부재에 따른 역할 구분
- T02 로컬 fixture 설치의 범위: npm 직접 실행·설치 인자 전달, 공식 Codex 실제 레이아웃 확인의 대체 불가
- T04 fixture의 범위: 오류·기한·정리 경로, 실제 Codex app-server 호환성의 대체 불가
- T06 기존 POSIX mode assertion의 Windows ACL 검증 대체 불가

## 3. G0 harness의 현실적인 계약

- production 모듈 import 없는 독립 실행, 현재 Windows import 실패와의 분리
- 임시 로컬 디렉터리·격리 HOME/CODEX_HOME 사용, 기존 인증 파일·OS credential store 접근 제외
- 공식 CLI의 인증 불필요 initialize·help·version까지만 기본 자동 실행
- 실제 로그인·rate-limit 성공의 별도 승인된 테스트 계정 검증, G0 fixture 결과로의 대체 금지
- shell→manager.exe 검증용 임시 launcher 생성, 최종 G2 패키지 설치 완료 증거와의 구분
- 일반 사용자 실행·비관리자·Developer Mode 비활성의 실제 환경 기록
- stdout·stderr·지속 시간·PID·exit 원본 값을 포함한 결과, 비밀 값·환경 전체 dump 제외
- Windows 빌드·아키텍처·PowerShell patch·Python·CLI·Node·npm 버전 및 실행 정의 기록
- 버전 미확정·CLI 형태 하나 누락·콘솔 부재 등의 각 검증별 blocked 판정
- unavailable 검증의 자동 skip 후 전체 PASS 처리 금지

### 실제 검증 없이는 확정 불가능한 항목

- Windows 네이티브 실행·콘솔 제어 이벤트·ACL·파일 점유의 Mac mock 검증 불가
- 자동화된 비대화형 파이프의 Ctrl-C injection과 실제 콘솔 사용자 Ctrl-C의 동일시 금지
- parent 정상 cleanup만으로 manager 비정상 종료 후 손자 종료 보장 불가
- Job Object 등 종료 소유권 수단의 실제 검증 필요, 단순 `taskkill` 성공만으로 unrelated process 보존 보장 불가
- launcher helper만으로 Codex 인터랙티브 TTY 정상 동작 보장 불가
- replace 성공·crash 복구만으로 전원 장애 내구성 보장 불가
- ACL 문자열·chmod 결과만으로 타 사용자 접근 차단 보장 불가
- 파일 lock 충돌 테스트만으로 백신·동기화 프로그램의 모든 점유 상황 보장 불가
- 사전 설치 Node 패키지 확인만으로 모든 npm prefix·패키지 매니저 레이아웃 지원 보장 불가

## 4. 버전·예산의 G0 확정 항목

- Python: 3.11 기준 우선, Windows 실제 patch 기록
- PowerShell: 7.4 계열 실제 patch 고정, 5.1의 미지원 사전 오류
- CLI: 현재 Mac 검증 버전과 Windows 지원 버전의 실제 비교, 동일 버전 설치 가능성 확인
- Node/npm: 실제 버전 및 공식 `@openai/codex` 레이아웃 기록
- REA 별도 제약: 현재 6.5.0의 Node `^22.19.0 || ^24.11.0 || >=26`
- G0의 임시 launcher dependency: 로컬 사용 가능 여부·버전·오프라인 준비 필요성 기록, production dependency 자동 추가 금지
- 프레임 상한: 현행 pending 1 MiB와 신규 §19 프레임/계정별 수신 버퍼 각각 4 MiB의 구분, G0의 4 MiB 계약 검증
- 대기열 상한: 프레임 상한과 별도로 계정별 4 MiB 수신 상한 검증, newline을 반복하는 출력 폭주 포함
- 예산: 계정당 요청 15초·cleanup 3초, snapshot `ceil(N/5) × 18 + 2`초
- 모호점: 앞 batch cleanup과 최종 cleanup의 중복 계산, 최종 2초가 중복 허용인지 별도 한도인지 G0 결과에 명시
- 무한 wait·reader join·writer block의 예산 외 대기 금지
- 예산 초과 시 원인·대안 보고, 구현 편의에 따른 자동 완화 금지

## 5. G0 통과 직후의 최소 운영 코드 변경

### 첫 diff: 실행 정의와 모든 호출부의 일괄 연결

- 신규 실행 경계 모듈 한 개: 검증된 `executable`·`prefix_args`·kind·version·package_root 표현 및 argv 생성
- `core.resolve_cli()` 호환 전략: 기존 문자열 소비자의 조용한 의미 변경 금지, 구조화 resolver와 명시적 argv 함수 도입
- 변경 필수 호출부: `runtime.run`, `usage.account_request`, `cli.login/doctor`, `tools.tool_plan/install_tools`, `core.install_changes`
- 간접 호출 확인: `accounts.register/remove`의 `core.shell_text` 생성, doctor의 실행 경로 표시
- `core.shell_text`의 미사용 cli 인자 정리 또는 현재 인자 계약 유지, 함수별 별도 실행 정의 복제 금지
- Windows 알려진 설치 경로·Node package 검증은 G0 실제 레이아웃만 지원
- `shell=True`·`.cmd` 본문 추측·Invoke-Expression·환경 전체 변경 제외
- 기존 재귀 탐색 차단과 명시 경로 우선순위 유지
- 검증: 기존 CLI 탐색·런타임 argv/환경/exit·사용량 RPC·도구 plan 테스트 재사용 및 Windows 실행 정의 fixture 추가
- 한계: 이 diff 단독의 Windows 지원 완료 주장 금지

### 다음 diff: 파일 경계의 import 가능성 및 기존 Mac 보존

- `transaction`의 잠금·비공개 권한·동일 볼륨 replace·디렉터리 flush 작업만 OS 경계로 이동
- Windows ACL·잠금·replace 구현은 통과한 T06 방식으로 한정
- 운영 키·등록부 v1·Mac 경로·journal 의미 유지, G2 형식 전환 선행 금지
- `fingerprint`의 Windows mode/ACL 표현과 구 POSIX journal 호환성 명시, 임의 permission 무시 금지
- 검증: 기존 transaction 전체 의도 유지, 별도 Windows 실측 연결

### 이후 G1 범위

- T04·T05 검증 방식의 RPC reader·프로세스 소유권 공통 경계 연결
- bash·PowerShell 연결, 현재 셸 선택·기본값 캡처·삭제 후 갱신
- Windows 파일별 관리 복사·3-way 충돌·복구, 전체 디렉터리 대체 제외
- G1 완료 전 세 OS 계정 흐름 실측, G2 패키징·workspace 분리·schema v2 착수 금지

## 6. 차단 의존성과 보고 형식

- 현재 검토자의 Windows runner·VM 접근 조사 미수행, 메인 담당의 실제 환경 탐색 결과 대기
- Windows 11 x64·PowerShell 7.4·표준 사용자 콘솔의 부재 시 T01–T06 미검증 유지
- 공식 CLI standalone 및 Node 배포형의 준비 부재 시 해당 항목 차단
- ACL 실효 검증용 별도 사용자·토큰 부재 시 증거 수준 제한 및 미완료 명시
- G0 미통과 상태의 허용 산출물: harness·실행 절차·결과 schema·기존 Mac baseline·본 계획
- G0 미통과 상태의 금지 완료 보고: Windows 지원·세 OS 지원·G1 완료·최종 설치 검증
- 결과 항목: ID / pass·fail·blocked / 실행 명령 / 버전 / 관찰값 / 증거 경로 / 남은 조건

## 7. 명세 정합성 보완 제안

- §13의 CP01–CP20과 §19의 CP21–CP24를 전체 수용 목록으로 연결, §14의 CP01–CP24 참조 대상 명확화
- G0 T03의 manager.exe를 임시 launcher 계약 검증으로 명시, G2 정식 패키지 검증과 분리 필요
- G0 T01의 공식 CLI 기동 증거와 argv helper 증거의 구분 필요
- G0 T05의 manager 강제 종료 후 손자 정리 책임 및 지원하지 못하는 native detach 경계의 명시 필요
- R-WIN과 R-LINUX의 실측 환경 확보 전 가정 기반 지원판 배포 금지
