# macOS 클로즈드 알파 테스터 시트

## 1. 받은 것

Owner에게 wheel, 다른 인증 채널의 SHA-256, 필요하면 OAuth JSON을 각각
받으세요. PyPI, `uvx`, 저장소 clone은 사용하지 않습니다.

## 2. 에이전트에게 붙여 넣기

```text
이 macOS에 proactive-mcp 클로즈드 알파를 설치하고 지금 이 에이전트의 일상용 MCP로 등록해 주세요. 내가 mcp add나 설정 파일 편집을 직접 하지 않게 해 주세요.

규칙:
- wheel SHA-256을 별도 채널 값과 비교하고 다르면 중단하세요. Python 3.11 전용 ~/venvs/proactive에 그 wheel만 설치하세요. 관리자 권한과 PROACTIVE_DATABASE를 쓰지 마세요.
- OAuth JSON이 있으면 ~/.proactive-mcp를 0700, client_secret.json을 0600으로 설치하세요. 없으면 BYO인지 확인하고 SETUP_GOOGLE.md를 따르세요. Gatekeeper와 consent 선택은 우회하지 말고 나에게 넘기세요.
- 일상 대화에는 절대 경로의 proactive-mcp serve만 등록하세요. serve-scheduled를 같은 대화에 로드하지 마세요. 새 대화마다 proactive_check를 한 번 호출하는 host rule을 설정하세요.
- reply_deadline은 보수적 후보입니다. 말하기 전에 뉴스레터, 마케팅, 자동 영수증, no-ask FYI/FYI-CC, 다른 사람 소유 thread, 나에게 질문·요청·결정이 없는 행을 확신할 수 있을 때 제외하세요. 명시적 회신·RSVP·결정 요청, 내 마감, 나에게 직접 묻고 아직 답하지 않은 질문은 유지하세요. 불확실성은 알리거나 lease 전체를 미확정으로 두거나 일상 대화에서 snooze하고 조용히 버리지 마세요. 전체 검토 뒤 확정하기로 선택한 경우에만 보이지 않게 제외한 후보까지 포함한 lease 전체를 하나의 receipt_token으로 정확히 한 번 confirm_delivery 하세요. 결과나 token이 없으면 확정하지 마세요. MCP는 영어로 유지하고 나에게는 내 언어로 말하세요.
- proactive-mcp는 agent/model/conversation을 시작하지 않습니다. serve-scheduled는 restricted stdio surface일 뿐 scheduler가 아닙니다. host가 dedicated per-run MCP profile에 serve-scheduled만 넣어 별도 agent run을 시작할 수 있을 때만 host-owned scheduling을 구성할 수 있습니다. 불가능하면 자동 예약을 구성하지 마세요. Grok 0.2.112 unattended scheduling은 지원하지 마세요. Codex config layer도 plugin이 격리를 보증한다고 말하지 마세요. fallback host나 selector를 만들지 마세요.
- LaunchAgent에는 전용 venv의 proactive-mcp daemon만 등록하세요. daemon은 local sync/evaluation/queue와 documented OS fallback만 수행하며 agent나 prompt를 실행하지 않습니다. daemon 등록 불가 시 periodic sync와 OS fallback이 없음을 설명하고 degraded mode 동의를 받으세요.
- Google read-only setup과 내 확인 뒤 한 번의 real-account read를 완료하세요. gmail.readonly와 calendar.readonly 외 scope를 요청하지 마세요.
- database healthy, migration 10, Gmail/Calendar ok를 확인하세요. PII, OAuth JSON, token, DB, raw logs, screenshot, full status, host config를 출력하지 마세요.
- 변경과 결과, host isolation 판정, 자동 schedule 지원 여부, degraded 여부만 보고하세요.
```

## 3. 성공 기준과 되돌리기

일상 profile은 `serve` 하나입니다. 자동 schedule은 dedicated per-run isolation이
있을 때만 선택 사항이며, 없으면 구성하지 않는 것이 fail-closed 성공입니다.
Daemon LaunchAgent는 proactive-mcp local background process만 시작합니다.
상태는 database healthy, migration 10, Google sources `ok`여야 합니다.

되돌릴 때 host-owned schedule이 있으면 호스트에서 먼저 제거하고 daemon을
중지한 뒤 `proactive-mcp disconnect` 성공을 확인합니다. 그 뒤에만 MCP 등록,
state directory, venv를 제거합니다. credential 삭제 실패 시 state를 보존하고
Google 권한을 취소한 뒤 Owner에게 보고합니다.
