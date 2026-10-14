# 사전 조사 기록

조사일: 2026-10-14, Asia/Seoul. 제품 구현·설정 변경 없이 접근 가능성을 확인했다.

## 확인한 환경

- 실제 CLI: `/opt/homebrew/bin/codex`, `codex-cli 0.162.1`.
- iTerm2 사용. 기존 zsh 함수와 alias가 계정별 CODEX_HOME을 설정한다.
- 5개 home 모두 기존 app-server control socket과 state_5.sqlite를 가진다.
- 현행 `cusage`도 계정별 CODEX_HOME으로 app-server를 사용한다. 다만 사용량 조회용 독립 서버 방식을 실시간 thread 상태 조회에 그대로 적용하지 않는다.
- Python 3.14.7. 조사한 기본 Python에는 Textual/websockets/iterm2/pytest가 설치돼 있지 않았다. 이번에는 설치하지 않았다.

## 실제 조회 결과

기존 계정 서버에 WebSocket으로 연결해 initialize → initialized → thread/loaded/list → thread/list pagination → thread/read → thread/turns/list → thread/loaded/list 순서로 읽었다. 읽은 내용은 모델에 새 작업으로 전달하지 않았다.

| 계정 | 비보관 interactive 대화 | 페이지 | active | idle | notLoaded | loaded 전/후 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| CODEX | 621 | 7 | 0 | 1 | 620 | 11 / 11 |
| CWORK | 62 | 1 | 0 | 1 | 61 | 1 / 1 |
| CCOMM | 5 | 1 | 1 | 2 | 2 | 3 / 3 |
| CSAY | 19 | 1 | 0 | 1 | 18 | 1 / 1 |
| CRIM | 9 | 1 | 0 | 0 | 9 | 0 / 0 |
| 합계 | 716 | 11 | 1 | 5 | 710 | 16 / 16 |

숫자는 조회 당시의 관측치다. 이후 대화 생성·보관·상태 변경으로 달라질 수 있다. 5계정 모두 조회 전후 **loaded ID 집합 자체가 동일**했다. 이것은 이번 조회에서 thread를 추가로 로드하지 않았다는 증거이며 모든 부작용의 일반적 부재를 증명하는 시험은 아니다.

각 계정의 최신 대화에서 metadata와 최근 3개 turn을 읽었다. 모든 표본은 historyMode=paginated였고 userMessage와 agentMessage 항목이 있었다. 본문, thread ID, 프로젝트 경로, 인증정보는 저장하는 증거에서 제외했다. [결과 JSON](evidence/discovery-summary.json)

## 설계에 영향을 준 사실

### 1. 기존 서버에 연결해야 한다

계정별 소켓은 `<CODEX_HOME>/app-server-control/app-server-control.sock`이다. 소켓 전송은 JSONL이 아니라 HTTP Upgrade 뒤 WebSocket frame을 사용하는 방식이었다.

처음 `codex app-server proxy`에 JSONL을 바로 보내는 탐색은 initialize timeout으로 실패했다. 공식 전송 문서를 확인한 뒤 기존 Unix socket에 WebSocket으로 연결하여 5계정 모두 성공했다. 실제 제품에서는 검증된 websockets 라이브러리를 사용한다.

### 2. CLI source만 조회하면 계정이 사라진다

CCOMM 5개와 CRIM 9개는 모두 source=vscode였다. CODEX/CWORK/CSAY에도 cli와 vscode가 섞였다. 따라서 최초 목록은 interactive 기본 범위로 읽으며, 도구 이름이나 터미널 사용 여부로 cli만 필터링하지 않는다.

### 3. stored 목록과 loaded 목록은 같지 않다

CODEX는 loaded 11개 중 1개만 기본 interactive 저장 목록에 들어 있었다. 나머지의 세부 source·parent·ephemeral 분류는 이번 탐색에서 확인하지 않았다. 구현 때 loaded 메타데이터를 합치고 하위 에이전트를 분류해야 한다.

### 4. 상태 정보가 공식 응답에 있다

로컬 생성 schema의 ThreadStatus는 notLoaded, idle, systemError, active를 가진다. activeFlags에는 waitingOnApproval, waitingOnUserInput이 있다. 정상 조회에서 active/idle/notLoaded를 실제 관측했다. 승인 대기·오류 상태는 이번 탐색에서 만들지 않았다.

### 5. 목록만으로 마지막 메시지를 알 수 없다

Thread는 name, preview, model, reasoningEffort, cwd, updatedAt, status 등을 포함하지만, preview는 보통 첫 사용자 메시지다. 마지막 대화는 선택한 thread의 paginated history에서 찾아야 한다. 모델 응답에는 commentary와 final 구분도 있으므로 가장 뒤의 문자열을 무조건 최종 답변이라고 표시하지 않는다.

### 6. 토큰 상세는 아직 별도 확인이 필요하다

표본 thread/read의 extra는 비어 있었다. schema에는 thread/tokenUsage/updated 알림이 있지만 관찰 전용 연결에서 기존 누적 입력·출력 토큰을 얻는 경로는 이번에 확인하지 못했다. state_5.sqlite에는 tokens_used 필드가 있으나 이것만으로 입력·출력 값을 나눠 만들 수 없다.

## 이번에 수행하지 않은 것

- thread/start, thread/resume, turn/start, archive/delete, 승인 처리.
- 기존 daemon 시작·종료·재시작, Codex 설정·alias·hook 변경.
- 라이브러리 설치, iTerm2 API 활성화·탭 생성·포커스.
- 원본 Command Center와 항목별 개수·상태의 1:1 대조.
- 상태 변경 알림의 전체 구독 범위, thread/items/list 호출, 입력·출력 토큰 복원.
- daemon 없는 계정, standalone CLI, 원격 세션, ephemeral 재개 검증.

## 근거와 참고 자료

- [OpenAI App Server 공식 문서](https://learn.chatgpt.com/docs/app-server): 조회 API와 전송 방식. 문서 검색 결과와 실제 페이지에서 experimental 표기가 달랐으므로 설치 버전 schema·응답을 우선 확인했다.
- 로컬 `codex app-server generate-json-schema --experimental`: 조사 당시 설치 버전의 프로토콜. 임시 위치 `/private/tmp/cboard-discovery-20261014-00vfr58z/`에 생성했으며 보존을 보장하는 위치는 아니다. 필요한 fixture는 구현 때 최소 범위로 다시 생성·익명화한다.
- [websockets Unix socket client](https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html#websockets.asyncio.client.unix_connect): 제품의 기존 서버 연결 후보.
- [Textual workers](https://textual.textualize.io/guide/workers/), [Textual testing](https://textual.textualize.io/guide/testing/): 비동기 UI와 조작 시나리오 시험.
- [iTerm2 App](https://iterm2.com/python-api/app.html), [Window](https://iterm2.com/python-api/window.html), [Session](https://iterm2.com/python-api/session.html): 선택적인 탭 제어 경계.
- [agent-deck](https://github.com/asheshgoplani/agent-deck), [codex-dash 소스](https://github.com/ArnabCodes/codex-dash/blob/main/codex_board.py): 목록·분류·상태 저장의 참고. 현재 명세는 기존 Codex API를 우선하며 두 프로젝트 코드는 복사하지 않았다.
