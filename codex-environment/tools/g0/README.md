# G0 Windows 선행 검증 도구

- 목적: 명세 T01–T06의 실제 Windows 근거 수집
- 범위: Python 3.11 이상 표준 라이브러리 기반 격리 프로브
- 생산 코드·계정 등록부·셸 프로필·기존 계정 홈의 변경 없음
- 로그인·신원 조회·사용량 조회·네트워크 설치의 실행 없음
- 모든 Codex 실행의 임시 `CODEX_HOME` 및 파일 인증 저장소 지정
- 기존 OpenAI/API 인증 환경 변수 제거, npm 사용자 설정·캐시의 임시 경로 지정
- 명시한 실행 파일의 신뢰성 확인 책임: 실행 전 사용자 확인
- package.json 검사의 범위: 이름·bin·실제 경로 경계 확인, 공급자 서명 검증 제외

## 실행

```powershell
pwsh -File .\tools\g0\run.ps1 -Python 'C:\Python311\python.exe' -OutputDirectory .\g0-report
```

- 사전 조건: Windows 11·PowerShell 7.4 이상·Python 3.11 이상 및 로컬 ensurepip 사용 가능 설치본
- `-Python`: 실제 설치 경로로 대체, 기본 포함 경로 아님
- 실행 결과: `powershell.json`·`windows.json`·`process.json`·통합 `gate.json`
- 임시 launcher: Python 동봉 pip의 distlib console `.exe`, 네트워크 설치 없는 임시 venv 생성 및 실행 후 제거
- 모든 Python 자식의 `-I` 지정, 상속 PYTHONPATH·사용자 site 영향 차단
- 현재 범위의 예상 종료 코드: 전체 G0 미충족에 따른 1, 실패 상세와 미수행 항목의 JSON 확인 필요
- 출력 파일: 새로운 JSON 파일만 허용, 기존 파일 덮어쓰기 차단
- 실행 위치: 실제 Windows, 관리자 권한 없는 일반 사용자
- 프로브 범위 확장: 명시한 실제 설치본에 한정한 다음 옵션

```powershell
python .\tools\g0\windows_probe.py --output .\g0-native.json --cli 'C:\tools\codex.exe'
python .\tools\g0\windows_probe.py --output .\g0-node.json --node 'C:\Program Files\nodejs\node.exe' --codex-js 'C:\tools\node_modules\@openai\codex\bin\codex.js' --npm-js 'C:\Program Files\nodejs\node_modules\npm\bin\npm-cli.js'
```

- `--shell-report PATH`: 별도 PowerShell 검증 JSON의 T03 근거 병합
- PowerShell 보고서 필수 구조: `schema_version: 1`, `environment.os: Windows`, `environment.powershell_version`, `checks.T03.status`
- 외부 보고서의 SHA-256 기록 및 데이터로만 처리
- 전체 G0 미충족의 종료 코드 1, 전체 충족의 종료 코드 0
- 현재 도구만으로 전체 G0 충족 불가, 미실행 항목의 명시적 유지

## 검증 범위

| 항목 | 현재 수집 범위 | 남은 필수 근거 |
| --- | --- | --- |
| T01 | Python 자식 argv 왕복, 선택적 실제 exe 버전·도움말·app-server initialize | 네이티브 전체 argv 전달 근거, 사용량 RPC의 별도 인증 검증 |
| T02 | Node 버전, 패키지 name/bin/경로 검사, 직접 JS 버전 실행 | 격리된 npm 설치 실행 및 실제 설치 형태 확정 |
| T03 | PowerShell 함수→임시 console exe→Python native recorder의 특수문자·빈 문자열·한글 argv 왕복 | Windows 실측, stdin·stderr·종료 코드 전달, 최종 운영 launcher의 G2 검증 |
| T04 | UTF-8 분할·EOF·불완전 프레임·잘못된 JSON·무응답·단일 프레임 4MiB 초과·미소비 raw 데이터 합계 4MiB 초과·32개 제한 queue 취소 및 reader 회수 | 실제 Windows 실행 결과 |
| T05 | suspended 생성→Job Object 할당→재개, 소유 자식·손자 확인, Job close 후 종료·회수 측정 | 실제 Windows 실행, Ctrl-C·관리자 비정상 종료·파이프 보유 손자 시나리오 |
| T06 | 임시 폴더 ACL 설정·조회, msvcrt 잠금 충돌·해제, 열린 대상 교체 거부·해제 후 교체 | 유효 ACL·다른 사용자 접근 거부·비정상 종료 및 journal 복구 |

- `pass`: 해당 프로브 범위 통과, 전체 제품 지원 선언과 별개
- `partial`: 일부 측정 성공 및 남은 필수 근거 존재
- `fail`: 측정 실패 또는 잘못된 입력
- `not_run`: 필요한 실제 환경·입력·전용 프로브 부재
- `not_applicable`: Windows 이외 OS 실행, Windows 통과 근거로 사용 금지
- 보고서 내용: OS·빌드·아키텍처·Python·선택 도구 버전·명령·종료 코드·제한된 출력·시간·누락 항목
- 출력 보호: 토큰 형태·사용자 홈·사용자 SID의 부분 마스킹, 결과 공유 전 추가 확인 필요
- 제한: 임의 악성 실행 파일의 네트워크·자식 프로세스 생성 차단을 위한 보안 샌드박스 기능 없음

## 로컬 회귀 검증

```sh
python3 -m unittest discover -s tests -p 'test_g0_*.py' -v
```

- raw 수신 예산: 미처리 프레임·대기열·공백 포함 합계 4MiB, 수신 시 대기열 바이트 차감
- 대기열의 raw bytes 보관 및 receive 시 JSON 해석, 다수 파싱 객체의 대기열 누적 방지
- 임시 Python 파이프·패키지 fixture·출력 마스킹·비Windows 통과 금지 검증
- Windows 실측·로그인·생산 RPC 검증의 대체 불가
