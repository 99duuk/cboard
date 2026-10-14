# v0.1.0 실행 검증

2026-10-14. Python 3.11.9, Codex CLI 0.162.1, Textual 8.2.8, websockets 17.2, macOS/iTerm2 환경.

## 실제 계정 조회

- 기존 5계정 서버를 사용해 root 대화 716개를 통합 조회했다: CODEX 621, CWORK 62, CCOMM 5, CSAY 19, CRIM 9.
- 실제 UI를 headless 실행했을 때 계정 전체 metadata 완료까지 약 0.49초였다. 측정은 cache가 없는 임시 state/cache에서 수행했다. 반복 측정 평균이나 일반 환경의 성능 보장은 아니다.
- CODEX의 추가 loaded 10개는 child였으며 기본 목록에서 제외됐다.
- 조회 전후 모든 계정의 loaded ID 집합이 같았다.
- 상세 패널에서 최근 사용자 메시지와 assistant 메시지를 실제로 읽었다. API name이 원래 Command Center의 명명된 작업 제목과 일치하는 표본도 확인했다.
- 증거: [live-verification.json](evidence/live-verification.json).

## 실제 iTerm2 재개

- CWORK의 앱/IDE 출처 대화: 이미 로드된 Ready 대화를 새 Cboard 탭에서 재개했다. 원래 대화의 Ready 상태와 native Codex 화면을 확인했다.
- CODEX의 CLI 출처 대화: 저장된 비활성 인사 대화를 재개해 Ready가 되는 것을 확인했다.
- 각 경우 두 번째 실행은 처음 만든 탭의 동일한 session ID로 이동했다.
- 시험으로 만든 탭만 닫았으며 새 사용자 프롬프트를 보내지 않았다.
- 증거: [resume-verification.json](evidence/resume-verification.json).

## 자동 시험

명령: `.venv/bin/python -m pytest -q`.

시험 대상에는 상태 변환, 새 active flag, 100개 초과 pagination과 중복 페이지, vscode source, loaded-only/child/ephemeral, 조회 요청 제한, timeout 후 재연결, stale 상태 유지, 현재 turn의 commentary/final 구분, 7개 중 2개 숨김·5개 선택의 재실행, 동시 state 변경, 손상·구버전 state 보존, 저장 중단, 계정별 동일 ID, 인용문자가 있는 경로, 임시 대화 재개 차단, 느린 상세 응답, 좁은 화면 전환, 필터 밖 선택, 부분 재개 실패, 2,000개 목록 검색, 계정 6개 설정 추가, 현재 행 숨김이 다른 선택을 보존하는 동작이 포함된다.

텍스트 컴파일과 `pip check`도 수행했다. 테스트 최종 개수와 명령 결과는 아래에 기록한다.

초기 구현 결과: **33개 자동 시험 통과**, Python compileall 성공, `pip check`의 `No broken requirements found` 확인. 설치된 `~/.codex/bin/cboard list --json`도 716개·5계정·모두 연결 정상으로 실행됐다.

마지막 실행 점검에서 Textual 내부 `_closing` 이름과 충돌하던 종료 플래그를 `_board_closing`으로 바꾸고, 갱신 중 `q` 종료 회귀 시험을 추가했다. [native-dashboard-verification.json](evidence/native-dashboard-verification.json)은 실제 iTerm2 실행과 종료의 별도 결과다.

수정 이후 자동 종료 시험은 통과했다. 이후 실환경 재확인에서는 iTerm2가 새 탭/창 생성·선택을 처리하지 않고 AppleEvent timeout을 반환하는 상태를 관측했다. 읽기 명령은 정상이며 GUI의 New Tab 항목도 비활성으로 관측했다. 기존 사용자 세션을 재시작하지 않았고, native 종료 재확인은 미완료로 기록한다. 재개 화면은 이 경우 구현 코드를 출력하는 대신 짧은 오류와 명령 복사를 안내한다.

새 탭이 아직 초기화 중일 때의 재사용과 오래된 비활성 표시자의 무효화도 실제 임시 탭으로 확인했다. [terminal-marker-verification.json](evidence/terminal-marker-verification.json).

## iTerm2 장애 후속 수정 — 2026-10-14

사용자가 iTerm2를 재시작했다고 알린 뒤 macOS unified log를 조회했다. 11:49:38의 AppKit Window 오류는 종료한 sheet에 하위 sheet가 남았다고 보고했다. 시험용 탭의 조기 종료 경고와 이후 새 탭/창 명령 비활성화에 부합하는 정황이지만, 내부 정지 원인이나 경고창과의 인과관계를 확정한 것은 아니다.

F05/T17/T18에 따라 다음을 변경했다.

- 자동화 timeout·AppleEvent 오류·확인할 수 없는 결과에서 현재 batch의 남은 요청을 중단한다. 같은 Cboard 실행에서는 자동 열기를 비활성화하고 선택·조회·명령 복사를 유지한다.
- 같은 launcher에서 대기하던 요청도 실패 상태를 확인해 AppleEvent를 보내지 않는다. 별도 launcher/프로세스의 자동화는 같은 state 디렉터리의 공용 file lock으로 직렬화한다. 실패 상태 자체는 별도 실행끼리 공유하지 않는다.
- AppleEvent 응답 제한 10초와 osascript 프로세스 제한 20초를 적용한다. 표시자 조회에서는 객체 소멸·값 변환 오류만 건너뛰며, 탭 이동·생성·표시자 쓰기의 실패는 무시하지 않는다. 생성 결과가 불확실한 경우 자동 재시도나 탭 닫기를 하지 않는다.
- CLI도 terminal unavailable 오류를 traceback 대신 짧은 오류로 출력한다.

전체 시험: **41개 통과**. 새 회귀 시험은 네 종류의 자동화 실패, 실패 직후 대기 요청 차단, 서로 다른 launcher의 직렬화, 중간 실패에서 후속 탭 요청 중단·선택 유지·열기 버튼 비활성화·클립보드 복사, CLI 오류 출력을 검증한다. subprocess와 clipboard는 fixture로 대체했으며 실제 iTerm2 탭을 생성하거나 닫지 않았다. `/usr/bin/osacompile`로 AppleScript 문법도 확인했다.

이 변경은 실패 이후 추가 요청을 차단한다. iTerm2 내부 문제 자체를 해결하거나 첫 요청에서의 정지를 방지한다고 보장하지 않는다. 재시작 이후 실제 GUI 재개는 반복 시험하지 않았다. 현재 세션의 표시자를 읽는 단일 조회도 3초 AppleEvent 제한에서 `-1712`를 반환해 추가 실환경 요청은 중단했다. 이 응답만으로 iTerm2가 다시 멈췄다고 단정하지 않는다.

## 최근순 정렬 수정 — 2026-10-14

이전 목록은 상태 우선 정렬 뒤 프로젝트/계정 단위로 묶어 오래된 항목이 최근 항목보다 먼저 보일 수 있었다. F01/F02/T19에 따라 기본 보기를 그룹 없는 전체 활동 시각 내림차순으로 변경했다. 상태는 정렬에 영향을 주지 않고 동률은 고유 키로 안정화한다. `g`로 프로젝트별/계정별 보기를 선택하면 각 그룹 내부는 최근순이며 그룹의 최신 항목 순서로 그룹을 배치한다. 그룹 보기 자체는 전체 날짜 내림차순이 아니므로 보기 모드를 표시한다.

전체 시험 **43개 통과**. 추가 시험은 서로 다른 상태에서의 날짜 우선·동률 순서, 실제 Textual 행의 `1h → 1d → 2d → 320d` 순서, 다른 계정·프로젝트 혼합, 갱신 후 재정렬과 현재 항목 유지, 그룹 보기 전환·내부 정렬·저장을 검증한다. 사용자 로컬 보기도 최근순으로 변경했고 숨김·선택을 보존했다. 날짜가 실제 최근 활동을 정확히 나타내는지는 기존 Codex API의 timestamp 값에 따른다. 이 수정에서는 native 탭 생성·종료를 시험하지 않았다.

## 단일 원본 대화 삭제 — 2026-10-14

F07/T20/T21을 구현했다. 기존 `h` 숨김은 유지하며 `Delete`(Mac Fn+⌫)에서 계정·대화·하위 대화 범위를 확인한다. 정확히 `DELETE`를 입력하고 삭제 버튼을 눌러야 원본 서버에 `thread/delete`를 보낸다. 삭제는 생성된 하위 대화도 포함한다. 로드된 root/하위 대화, 임시 root, 범위 변화 및 조회 실패에서는 요청을 막는다. 사용자 서버의 대화를 실제로 삭제하는 시험은 수행하지 않았다.

전체 시험 **64개 통과**. 별도 Unix WebSocket fixture에서 비보관·보관 하위 대화 pagination, ephemeral 하위 대화 부모 관계, 동일 thread ID의 계정별 socket 분리, 실행 직전 상태·제목·범위 변화, 삭제 응답 유실, 조회 RPC의 mutation 차단을 검증했다. Textual에서는 120×35와 80×24에서 확인창 취소, 대소문자를 포함한 정확한 확인어, 삭제 버튼 조작, 성공 시 선택·숨김 정리 및 실패 시 선택 유지·자동 재시도 없음까지 확인했다. iTerm2 자동화는 사용하지 않았다.

실제 CODEX 계정의 Inactive 대화 표본에서도 `prepare`만 실행해 삭제 범위 조회가 현재 서버에서 동작함을 확인했다(하위 대화 0개). 사용한 메서드는 읽기뿐이며 `delete`나 `thread/delete`를 호출하지 않았다.

읽기와 삭제 요청은 원자적 상태 비교가 아니다. 다른 클라이언트가 최종 확인 직후 다시 연결하는 경우까지 차단한다고 보장하지 않는다. 응답 유실에는 결과 불명을 표시하고 새로고침만 수행한다. 성공 후 조회가 실패하면 삭제된 대화의 이전 metadata가 stale로 보일 수 있다. 공식 동작 근거: [OpenAI App Server 문서](https://learn.chatgpt.com/docs/app-server)의 Delete a thread.

## Tasks 폭·세션 이름·재개 키보드 조작 — 2026-10-14

F01/F05/F08/T22~T24를 구현했다. Tasks는 세로 스크롤바 공간을 예약하고 실제 viewport에서 열 padding을 제외해 열 너비를 계산한다. 초기 레이아웃과 리사이즈가 완료된 TasksTable의 크기 변경 메시지에서 재계산하며 가로 스크롤은 비활성화한다.

재개 확인창은 첫 사용 가능한 버튼에 초기 포커스를 준다. ←/↑ 및 →/↓로 버튼을 순환하고 비활성 버튼은 건너뛴다. Enter는 포커스된 버튼을 한 번 실행한다. F2 이름 변경은 입력에 현재 이름을 선택한 상태로 포커스하고 Enter 저장·Esc 취소를 지원한다. 별도 원본 이름 변경 연결만 `thread/name/set`을 사용하며 조회 RPC의 mutation 차단은 유지한다. 활동 timestamp와 별도로 이름을 상세 갱신 기준에 포함했다.

전체 시험: **89개 통과**. 추가 검증은 100개 긴 한글 제목의 80/100/120/160열 왕복 리사이즈에서 가로 스크롤·virtual overflow 없음, 120×35와 80×24의 Enter 진입·네 화살표·열기/복사/취소 Enter 실행, 비활성 버튼 건너뛰기, F2 취소·빈 값·Enter 저장·서버 실패, timestamp 변화 없는 목록·상세 이름 갱신, 선택 유지다. Unix WebSocket fixture에서 Inactive/Ready/Working 이름 변경, 정확한 계정 socket 사용, 임시/다른 ID 차단, 응답 유실 시 단일 요청, 일반 조회 RPC의 이름 쓰기 차단도 검증했다.

샘플 기본 화면 SVG를 160×45, 120×35, 80×24 및 좁은 상세 화면에서 재생성했다. 실제 사용자 대화의 이름은 변경하지 않았고 iTerm2 탭 생성·닫기 시험도 수행하지 않았다. 서버가 이름을 저장하는 기능의 근거는 설치된 CLI의 ThreadSetNameParams 스키마와 [공식 OpenAI 문서](https://learn.chatgpt.com/docs/app-server)의 thread/name/set이다. 원본 이름 변경 서비스의 실제 서버 쓰기는 fixture로 검증했다.

## 첫 메시지 전 빈 세션 재개 차단 — 2026-10-14

사용자가 제시한 CODEX/CCOMM 두 ID의 no rollout found 오류를 조사했다. 원본 thread/read는 idle, ephemeral=false, historyMode=paginated를 반환했지만 thread/turns/list는 두 건 모두 `not materialized yet; ... unavailable before first user message`를 반환했다. 해당 rollout 경로의 파일이 없고, 읽기 전용 SQLite 조회에서 threads·thread_turns·thread_items의 해당 ID 행 수가 모두 0이었다. 첫 사용자 메시지 전의 빈 세션이 메모리에만 로드된 경우이며, Ready가 재개 가능한 저장 기록을 뜻하지 않는 사례다. 원본 저장소는 수정하지 않았다.

F05/T25에 따라 Task에 optional persisted metadata를 추가했다. 실제 root metadata 조회에서 turns summary로 저장 상태를 확인하고 첫 메시지 전 오류는 persisted=false로 처리한다. 목록에는 미저장, 상세에는 아직 저장 안 됨을 표시한다. 이런 항목의 재개·재개 명령 복사를 차단하고, 대상이 모두 미저장이면 확인창은 취소 버튼만 활성화한다. 미저장 상세는 history 오류 대신 빈 대화와 이유를 표시한다. 첫 메시지 후 materialized 상태가 되면 다음 갱신에서 재개를 허용한다.

전체 시험 **93개 통과**. stored 목록 포함 여부와 무관한 loaded empty 처리, root 저장 상태 전환, 조회가 thread/resume을 호출하지 않음, 순수 재개 명세 차단, 확인창 버튼 차단을 추가 검증했다. 실제 문제 두 ID도 새 source.read_task에서 persisted=false로 읽혔고 resume_spec에서 터미널 생성 전 차단됐다. 실제 thread/resume 요청, 새 사용자 메시지 전송, 파일·DB 삭제/이동 및 iTerm2 자동화는 수행하지 않았다.

## 삭제 확인창 간소화·화살표 이동 — 2026-10-14

사용자 요청에 따라 F07/T20의 DELETE 확인어 입력을 제거했다. 확인창은 취소 버튼에 초기 포커스를 두고 네 화살표로 영구 삭제/취소를 순환하며, Enter는 현재 버튼을 한 번 실행한다. Enter를 처음 누르면 취소하고, 영구 삭제를 선택한 뒤 Enter를 누르거나 클릭한 경우만 삭제 요청을 보낸다. Esc 취소, 삭제 범위 표시·경고 및 서버의 실행 직전 상태/범위 검사는 유지한다. 같은 확인창에서 Delete를 반복 입력해도 새 확인창을 추가하지 않는다.

재개/삭제 버튼 이동을 공통 ButtonDialog로 묶었다. `tests/test_ui.py` **23개 통과**로 재개 창의 화살표·Enter·비활성 건너뛰기를 포함해 검증했고, 마지막 중복 확인창 방지 변경 후 관련 삭제 시험 **3개를 추가 재확인**했다. 120×35와 80×24에서 확인어 입력창 없음, 기본 Enter 취소, Esc 취소, 네 화살표 이동 후 Enter 삭제, 선택·숨김 정리, 실패 시 선택 유지·단일 요청을 검증했다. 실제 사용자 세션 삭제는 수행하지 않았다. 이 절은 이전 64개 시험 단계의 확인어 입력 흐름을 대체한다.

## Ready 삭제 제한 수정 — 2026-10-14

사용자가 ‘대화 연결을 종료한 뒤 삭제’ 안내의 실행 방법을 물었다. 기존 정책은 idle/Ready까지 notLoaded가 아니라는 이유로 거절했다. 공식 문서상 `/exit`는 CLI 연결을 종료하지만 마지막 구독 이후 비활동 유예로 서버에 로드된 상태가 30분 유지될 수 있고, native `/delete`는 실행 중이 아닌 현재 대화를 삭제하는 흐름을 제공한다. 따라서 로드됐다는 사실만으로 거절하는 정책을 수정했다. [연결 유예](https://learn.chatgpt.com/docs/app-server), [CLI 종료·현재 대화 삭제](https://learn.chatgpt.com/docs/developer-commands).

F07/T21에서 idle(Ready)/notLoaded(Inactive) root 및 idle 하위 대화는 허용한다. 실제 실행·승인 대기·알 수 없는 상태는 거절하며 Ready로 만든 뒤 다시 삭제하라는 안내로 바꿨다. loaded 목록에 포함된 대화는 thread/read로 새로 확인하고, 삭제 대상까지 이어지는 부모 경로 전체를 검사한다. 목록에서 idle이었어도 loaded 메타데이터가 active/승인 대기로 바뀌면 중단한다. 임시 하위 대화와 범위 변경 차단은 유지한다.

관련 backend/UI 시험 **21개 통과**. 로드된 Ready root와 idle child의 삭제 허용, persisted 목록은 idle이지만 새 read에서는 승인 대기인 child 차단, 확인 직전 root의 active 전환, 다른 활성 대화의 영향 없음 및 기존 확인창 동작을 fixture로 검증했다. 실제 사용자 연결 종료·프로세스 kill·세션 삭제는 실행하지 않았다. 앞선 검증 단계의 ‘Ready도 삭제 차단’ 정책을 이 절에서 대체한다.

## 터미널 열기는 항상 새 탭 — 2026-10-14

사용자 요청에 따라 F05/T12를 변경했다. iTerm2 연동은 매번 새 탭(창이 없으면 새 창)을 생성하고 생성한 탭을 선택한다. 기존 탭을 찾거나 포커스하는 분기, 세션 표시자 읽기·쓰기, was_loaded와 생성 시각 인자 및 CLI의 OSC 표시자 출력을 제거했다. 정확한 계정·cwd·thread ID는 기존과 같이 유지하며 대화를 fork하지 않는다. 확인창 안내도 항상 새 탭에서 연다고 변경했다. 자동화 직렬화, timeout·첫 실패 이후 중단, 종료 후 일반 셸 유지 동작은 보존했다.

관련 재개/UI 시험 **22개 통과**, `/usr/bin/osacompile` 문법 확인 통과. 같은 세션을 연속으로 명시적 실행하면 각각 별도 열기 요청이 발생하는 것, 기존 탭 포커스 응답을 새 탭 생성 성공으로 받아들이지 않는 것, wrapper의 정확한 원본 ID와 인용 처리, 기존 timeout·직렬화·확인창 화살표/Enter 동작을 fixture로 검증했다. 실제 iTerm2 탭 생성·종료 반복 시험은 하지 않았다. 이전 검증의 같은 탭 재사용·표시자 시험은 당시 동작의 기록이며 현재 정책에서는 사용하지 않는다.

## 반복 갱신 성능 개선·요청/답변 순서 — 2026-10-14

F09/T26~T28과 F03을 구현했다. snapshot을 초기화 때 한 번만 읽고 task tuple이 바뀔 때만 통합 인덱스를 만든다. 빈 검색 조건의 문자열 구성도 생략한다. 표시되는 행·폭·그룹·순서·상대 시각·선택·숨김·stale가 같으면 DataTable을 그대로 두며, 개수/연결 안내도 내용이 바뀔 때만 갱신한다. 프로젝트별 보기의 같은 cwd 정규화는 한 번의 렌더 안에서 재사용한다.

관측 시각은 계속 갱신하지만 Task 의미 비교에서는 제외해 변화 없는 3초 polling의 캐시 파일 쓰기를 생략했다. 실제 변화·연결 복구·저장 실패 재시도는 즉시 저장하며 15초 전체 조회는 항상 저장한다. 저장 확인에 성공한 root만 실행 중의 반복 조회에서 summary probe를 재사용한다. 디스크 캐시나 목록의 persisted flag는 확인 성공으로 취급하지 않는다. 실패한 확인은 계속 재검사하며 세션이 사라지면 확인 기록을 제거한다. 명시적인 재개·명령 복사의 read_task는 항상 fresh probe를 실행한다.

상세 원문 순서는 마지막 요청 → 마지막 답변이다. commentary/final 구분과 첨부·조회 한도 처리는 유지한다. 모델·cwd·원본 상태는 목록이 변하지 않아도 상세를 새로 읽는 기준에 포함했다. 새로운 의존성·상주 프로세스를 추가하거나 polling 간격을 늦추지 않았다.

### 합성 측정

명령: `.venv/bin/python scripts/benchmark.py --output /tmp/cboard-performance.json`.

같은 Python/Textual 환경의 5계정, 120×35 headless UI, 변경 없는 데이터 5회. 각 source는 관측 시각만 새로 반환한다. 시작 전체 조회 이후의 반복 상태 갱신을 측정했다. 이 스크립트는 실제 계정·대화 본문·iTerm2를 사용하지 않는다.

| 측정 | 700행 변경 전 → 후 | 2,000행 변경 전 → 후 |
| --- | --- | --- |
| 상태 polling 중앙값 | 110.62 → 13.48ms | 303.14 → 21.55ms |
| 변화 없는 render 중앙값 | 8.24 → 1.51ms | 23.44 → 2.94ms |
| polling 회당 process CPU 시간 | 115.15 → 15.98ms | 310.22 → 24.08ms |
| 5회 polling의 목록 재생성 | 25 → 0회 | 25 → 0회 |
| 5회 polling의 캐시 파일 쓰기 | 25 → 0회 | 25 → 0회 |

별도 fake RPC 표본은 loaded root 20개에서 회당 41개 요청 → 21개 요청이었다. loaded/list와 20개 metadata read는 유지하고 이미 성공한 저장 확인 20개만 생략했다. CPU 값은 headless UI 처리·idle drain을 포함한 process time이며 시스템 전체 CPU 사용률이 아니다. 검색 평균은 700행 0.38 → 0.31ms, 2,000행 1.15 → 0.85ms였다.

원자료: [변경 전](evidence/performance-before.json), [변경 후](evidence/performance-after.json). 전체 사용 시나리오의 속도나 RSS 감소율을 보장하지 않는다. 메모리 사용량과 장시간 배터리 소비는 별도 측정하지 않았다.

### 실제 계정 읽기 표본과 회귀 검사

실제 5계정 root 715개를 임시 state/cache에서 읽었다. 전체 조회 255.92 → 158.69ms, 후속 loaded polling 16.57 → 2.44ms였다. 두 시점 모두 모든 계정은 정상 연결됐고, polling RPC는 loaded/list 5개 + read 5개 + summary 5개 → loaded/list 5개 + read 6개였다. 서버 상태와 실행 부하가 달라질 수 있는 단일 표본이므로 일반적인 배수 개선으로 해석하지 않는다. 임시 metadata cache만 저장했고 원본 대화 변경·재개·터미널 제어는 실행하지 않았다. 원자료: [실계정 변경 전](evidence/performance-live-before.json), [실계정 변경 후](evidence/performance-live-after.json).

전체 시험 **101개 통과(17.56초)**, compileall 및 pip check 성공. 추가 시험은 인덱스 재사용/무효화와 최신 관측 시각 유지, 의미 변화·전체 갱신·캐시 저장 실패 재시도·stale 복구 저장, 성공한 저장 확인만 재사용·직접 read의 재검사·첫 메시지 후 저장 전환·소멸 ID 제거, 실제 Textual의 변경 없는 갱신에서 재생성 없음·커서/스크롤 보존·상세만 바뀐 경로/모델 반영·상태/이름 변경 반영·요청/답변 순서를 확인한다. 160×45·120×35·80×24 및 좁은 상세 샘플 SVG를 재생성했다. native iTerm2 탭 생성·종료 반복 시험은 수행하지 않았다.

## 계정별 iTerm2 프로필·배경색 연결 — 2026-10-14

F05/T29를 구현했다. 기존 ~/.zshrc의 codex 함수는 CODEX_HOME으로 프로필과 배경색을 고른 뒤 SetProfile/SetColors를 출력한다. Cboard는 해당 함수를 거치지 않고 검증된 실행 파일을 직접 실행하며, 탭 생성도 default profile로 고정되어 있어 외형 설정이 적용되지 않았다.

Account와 ResumeSpec에 optional iterm_profile/iterm_background를 추가했다. ItermLauncher는 프로필 이름을 별도 argv로 전달해 named profile로 새 탭 또는 새 창을 만든다. 지정하지 않은 계정은 default profile을 사용한다. 배경색은 새 탭의 명령 앞에서 SetColors printf로 적용하며, 해당 프로필을 공유하는 다른 탭이나 저장된 프로필 자체는 수정하지 않는다. Codex 환경·argv·원본 thread ID·재개 전 재검사·자동화 잠금·timeout·실패 중단·종료 후 일반 셸 유지 흐름을 보존했다.

로컬 설정은 기존 셸과 같은 CODEX Alpha/001e3a, CWORK Alpha/38220b, CCOMM Charlie/28152e, CSAY Bravo/103019, CRIM Delta/3a1015로 반영했다. 저장된 iTerm2 preferences를 읽어 Alpha/Bravo/Charlie/Delta가 실제로 존재하는 것을 확인했다. 셸 함수나 iTerm2 preferences는 수정하지 않았다.

전체 시험 **115개 통과(17.45초)**. 새 시험은 optional 설정·대문자 RGB 정규화·빈 프로필/제어문자/잘못된 RGB 거절, 5개 계정의 프로필과 색 전달, 인용문자·$(...)·백틱을 포함한 프로필의 argv 분리, 실제 zsh -f에서 color printf의 정확한 OSC 바이트, 기본 프로필 및 기존 새 탭·원본 ID·종료 셸 유지 동작을 검증했다. /usr/bin/osacompile로 설치된 iTerm2 사전 기반의 AppleScript 컴파일도 성공했다. 테스트에서 osascript 실행은 fixture로 대체했고 실제 사용자 대화 재개·native 탭 생성/닫기는 하지 않았다. 실제 화면에서 색이 적용되는 최종 확인은 미실시다.

프로필별 탭/창 생성은 [iTerm2 AppleScript 문서](https://iterm2.com/documentation-scripting.html), 배경색 OSC는 [iTerm2 Escape Codes 문서](https://iterm2.com/documentation-escape-codes.html)를 확인했다. 프로필 이름/배경색 매핑은 ~/.config/cboard/config.toml에서 관리하며 셸 설정 변경을 자동 동기화하지 않는다.

## 최근 작업공간 탐색 — 2026-10-14

F10/T30~T33을 구현했다. workspaces.py의 경로 규칙으로 workspace 슬롯과 하위 폴더를 하나로 합친다. 홈 cwd 자체, Desktop/Downloads 아래, ~/Documents/workspace의 일반 프로젝트 및 다른 위치의 *-workspace/Git 저장소도 그룹으로 잡는다. 그룹 키는 절대 경로이고 선택한 그룹의 행에는 슬롯 상대 경로를 표시한다. 같은 작업공간에서 g 프로젝트 보기를 선택해도 슬롯별로 다시 나누지 않는다.

새 선택기는 최근 14일 전체 계정 root 세션 수를 표시하며 작업 목록의 ←/→로 전체와 작업공간을 순환한다. 직접 dropdown 선택도 지원한다. workspace 선택은 마지막 활동 시각이 최근 14일인 대화만 표시하고 기존 계정/상태/검색 조건을 함께 적용한다. 전체 보기는 과거 세션까지 유지한다. ranking은 필터와 독립적이고 기본 hidden/child는 제외한다. 초기 전체 조회와 r에서 정렬하고 자동 조회는 기존 순서를 보존한다. 선택한 그룹이 0개가 되어도 빈 목록으로 유지하며 다른 그룹으로 바꾸지 않는다. 마지막 작업공간을 state filters에 추가해 복구 선택·숨김과 함께 보존한다.

전체 시험 **134개 통과(23.45초)**, compileall 및 pip check 성공. 새 19개 시험은 workspace의 slot-1/slot-3/frontend/main 합산, 홈/Desktop/Downloads·일반 프로젝트·Git root·누락 경로·동명 다른 경로, 14일 경계 포함/이전 제외·다계정 합산·hidden/child 제외, 실제 Pilot의 120×35/80×24 좌우 순환·dropdown 직접 선택·상대 경로·가로 스크롤 없음·재실행 복원·필터 밖 복구 선택 유지, 자동 순위 역전/수동 갱신/0개 그룹의 선택 유지, 검색창의 원래 커서 이동·modal의 버튼 이동 및 modal 중 수동 새로고침을 검증했다. 대괄호를 포함한 폴더 이름도 Rich markup으로 해석하지 않고 원문으로 표시한다.

기존 metadata cache를 읽기만 해 실제 경로 집계를 확인했다. 당시 최근 14일 집계에서 bottling-workspace 22개, 홈 8개, mnemosyne-workspace 6개, Downloads 4개였다. 새 서버 조회에 기반한 현재 수치 보장은 아니다. 개인 작업공간별 원자료 docs/evidence/workspace-groups.json은 로컬에 보존하며 Git에는 포함하지 않는다. 사용자 state/cache를 수정하거나 원본 대화 재개·삭제·이름 변경 및 iTerm2 제어를 실행하지 않았다.

기존 5계정·700/2,000행·120×35 합성 benchmark를 5회 재실행했다. 변화 없는 polling 중앙값은 8.49/18.96ms, render는 1.05/3.10ms였고 목록 재생성·캐시 쓰기는 각각 0회였다. 환경 부하에 따른 변동을 포함한 단일 benchmark 실행이며 기능 추가가 일반적인 속도 향상을 보장한다는 의미는 아니다. [performance-workspaces.json](evidence/performance-workspaces.json). 경로 판단은 bounded cache로 반복 파일 조회를 줄이고 r에서 재검사한다.

전체/선택 작업공간 화면을 샘플 데이터로 160×45·120×35·80×24에서 생성했다. [넓은 작업공간 화면](../artifacts/screenshots/cboard-160x45-workspace.svg), [좁은 작업공간 화면](../artifacts/screenshots/cboard-80x24-workspace.svg)을 PNG로 렌더링해 슬롯들이 같은 목록에 들어가고 선택기·상대 경로·한글·기존 패널이 화면에 맞는 것을 확인했다. 샘플은 화면 크기마다 전체에서 시작한 뒤 오른쪽 한 번으로 최다 세션 그룹을 선택한다.

추가 의존성이나 백그라운드 수집 프로세스는 없다. 다른 작업공간 루트 규칙이 필요하면 workspaces.py를 수정한다. 이번 검증은 headless Textual 및 저장된 metadata에 한정하며 native iTerm2 화면에서 키 입력을 별도로 시험하지 않았다.

## 현재 작업공간 식별 강화 — 2026-10-14

F10/T34를 적용했다. 기존 작업공간 controls의 3줄 높이를 유지하면서 현재 작업공간을 별도 청록색 강조 영역에 표시한다. 첫 줄은 굵은 이름, 둘째 줄은 현재 위치/전체 공간 수·최근 세션 수, 셋째 줄은 경로다. 선택기 옵션은 이름을 먼저 보여주며 닫힌 선택기는 한 줄로 생략 표시한다. 긴 경로는 강조 영역에서 한 줄로 줄이고 tooltip에 전체 경로를 보존한다. 전체 보기와 선택한 0개 그룹도 명시한다. 반복 polling에서 요약이 같으면 Static.update를 생략한다.

UI·경로 관련 시험 **43개 통과(23.15초)**, 최종 CSS 변경 이후 120×35/80×24의 이동·직접 선택·복원 시험 **2개 재확인(3.39초)**, compileall 성공. 이름 우선 표시, 위치/개수, 전체 복귀, 0개 그룹, tooltip 경로, 좁은 화면의 강조 영역 폭을 기존 회귀 시나리오에 추가 검증했다. 홈/누락 경로 및 이름에 ‘ · ’가 포함된 경우도 경로 문자열을 표시용 label에서 다시 파싱하지 않고 별도로 구성한다.

160×45·120×35·80×24의 전체/작업공간/좁은 상세 SVG를 재생성했다. 넓은/좁은 작업공간 PNG에서 강조 이름, 분리된 경로·개수, 기존 목록 높이 및 닫힌 선택기의 불필요한 줄바꿈 제거를 확인했다. 샘플 데이터와 headless Textual만 사용했으며 native iTerm2 화면에서 키 입력을 별도로 시험하지 않았다. 원본 대화·사용자 state 및 프로필을 변경하지 않았다.

## GitHub 초기 게시 준비 — 2026-10-14

최종 전체 시험 **134개 통과(23.58초)**, compileall·pip check 성공. 소스·시험·문서·샘플 화면을 초기 main 커밋 대상으로 점검했다. staged 파일의 credential pattern 검사와 git diff --cached --check를 수행했다. 가상환경·로컬 state·인증 파일·실제 세션 기록 및 개인 작업공간별 집계 원자료는 .gitignore로 제외했다. 로컬 설정과 실행 진입점을 유지하면서 README에 GitHub 복제·설치 방법을 추가했다.

## README 애니메이션 데모 — 2026-10-14

정지 이미지 대신 실제 Textual UI를 샘플 데이터로 조작해 만든 GIF를 README에 삽입했다. scripts/demo_gif.py는 개인 계정 설정을 읽지 않고 5개 샘플 계정·2개 작업공간을 구성한다. 홈은 /Users/demo로 고정하고 state/cache/frame은 임시 디렉터리에만 저장한다. 작업공간 이동·요청/답변 확인·Space 선택·R 확인창·다른 작업공간 이동·전체 복귀의 10개 장면을 캡처한다. native 터미널 열기는 실행하지 않으며, 데모 launcher가 호출되면 즉시 실패하도록 구성했다.

생성 실행의 assert로 작업공간 순서, 상세 조회 완료, 2개 선택 확인창 및 종료 시 전체/선택 초기화를 확인했다. compileall과 diff check 성공. rsvg-convert로 SVG를 렌더링하고 ffmpeg의 가변 프레임 타이밍·공용 팔레트로 GIF를 인코딩했다. ffprobe 결과는 **1280×797, 11프레임, 17.16초, 1,093,132바이트**다. 마지막 프레임은 반복 경계용이다. 초기 중복 프레임을 제거해 약 8.8MiB에서 약 1.04MiB로 줄였다.

전체 목록·다른 슬롯의 상세·2개 선택 확인창·다음 작업공간의 4개 장면을 GIF에서 PNG로 추출해 한글과 레이아웃을 확인했다. README에는 정지 화면 링크도 남겼다. 재생성은 brew install ffmpeg librsvg 후 .venv/bin/python scripts/demo_gif.py로 수행한다. 추가 도구는 문서용 데모 제작에만 필요하며 애플리케이션 의존성은 바꾸지 않았다. native iTerm2 탭 생성이나 실제 계정 세션을 조작하는 시험은 수행하지 않았다.

## 화면 검증

샘플 데이터를 실제 Textual 앱에 넣어 160×45, 120×35, 80×24에서 SVG를 생성했다. 넓은 화면과 좁은 화면은 PNG로 렌더링해 한글, 컬럼, 계정 색, 목록·상세 전환을 시각적으로 확인했다. 화면 자료는 샘플이며 실제 계정 본문을 담지 않는다.

재생성: `.venv/bin/python scripts/visual_qa.py`.

## 확인하지 않은 범위

- 5개 계정 각각의 native CLI 재개를 모두 시험한 것은 아니다. 실제 재개는 CLI/앱 출처의 두 계정 표본으로 확인했고, 계정별 환경·argv 격리는 자동 시험했다.
- 앱 제어 권한이 차단된 실환경, 모든 remote/standalone CLI, native 탭 안에서 반복적인 `/new` 전환은 시험하지 않았다.
- 입력·출력 토큰 상세는 아직 표시하지 않는다.
- 승인/오류 상태는 실제 작업을 유발하지 않고 fixture로 시험했다.
