# Linux 클로즈드 알파 테스터 시트

## 1. 받은 것

Owner에게 Linux aarch64 archive, 다른 인증 채널의 archive SHA-256, 필요하면
OAuth JSON을 각각 받으세요. archive는
`~/Downloads/proactive-mcp-alpha-linux-aarch64-py311.tar.gz`에 저장합니다.

## 2. 에이전트에게 붙여 넣기

```text
이 Linux PC에 proactive-mcp 클로즈드 알파를 설치하고 지금 이 에이전트의 일상용 MCP로 등록해 주세요. 내가 mcp add나 설정 파일 편집을 직접 하지 않게 해 주세요.

규칙:
- 저장소 clone, PyPI, uvx, 관리자 권한, PROACTIVE_DATABASE를 사용하지 마세요. Linux aarch64와 Python 3.11만 지원합니다.
- 다른 채널의 archive SHA-256을 먼저 확인하세요. 다르면 설치하지 마세요. ~/Downloads/proactive-mcp-alpha/가 이미 있으면 삭제하거나 덮어쓰지 말고 멈추세요.
- 새 디렉터리에 푼 뒤 bundle의 SHA256SUMS를 검증하고 manifest와 wheels/ 목록이 정확히 같은지 확인하세요. bundle-metadata.json의 project_wheel이 wheels/ 아래 단일 파일이고 manifest에 정확히 한 번 있는지 확인한 뒤에만 ~/venvs/proactive를 만들고 offline/no-index로 그 정확한 wheel을 설치하세요.
- OAuth JSON이 있으면 ~/.proactive-mcp를 0700, client_secret.json을 0600으로 설치하고 확인하세요. 없으면 BYO인지 확인하고 SETUP_GOOGLE.md를 따르세요.
- 일상 대화에는 절대 경로의 proactive-mcp serve만 등록하세요. 새 일상 대화에서 proactive_check를 한 번 호출하는 host rule을 설정하세요. serve-scheduled를 같은 대화에 등록하거나 로드하지 마세요.
- reply_deadline은 보수적 후보입니다. 말하기 전에 뉴스레터, 마케팅, 자동 영수증, no-ask FYI/FYI-CC, 다른 사람이 맡은 스레드, 나에게 질문·요청·결정이 없는 행을 확신할 수 있을 때 제외하세요. 명시적 회신·RSVP·결정 요청, 내 마감, 나에게 직접 묻고 아직 답하지 않은 질문은 유지하세요. 불확실성은 알리거나 lease 전체를 미확정으로 두거나 일상 대화에서 snooze하고 조용히 버리지 마세요. 전체 행 검토 뒤 확정하기로 선택한 경우에만 보이지 않게 제외한 후보까지 포함한 전체 lease를 하나의 receipt_token으로 정확히 한 번 confirm_delivery 하세요. 결과나 token을 받지 못했으면 확정하지 마세요. MCP는 영어로 유지하고 나에게는 내 언어로 말하세요.
- proactive-mcp는 어떤 에이전트나 모델도 시작하지 않습니다. serve-scheduled는 scheduler가 아니라 restricted stdio MCP surface입니다. 호스트가 immutable dedicated per-run MCP profile로 별도 agent run을 시작할 수 있을 때만 host-owned schedule을 구성할 수 있습니다. 이 설치에서 그 격리를 증명할 수 없으면 자동 예약을 구성하지 말고 manual restricted usage만 가능하다고 보고하세요. Grok 0.2.112 unattended scheduling은 지원하지 마세요. Codex config layer도 proactive-mcp가 격리를 보증한다고 말하지 마세요. 자동 host fallback이나 selector를 만들지 마세요.
- daemon은 local sync/evaluation/queue와 documented OS fallback만 수행합니다. systemd user service에는 전용 venv의 proactive-mcp daemon만 등록하세요. Grok, Codex, Hermes, 다른 agent/model 또는 prompt를 실행하는 wrapper를 만들지 마세요. daemon을 등록할 수 없으면 periodic sync와 OS fallback이 없음을 설명하고 degraded mode 동의를 받으세요.
- Google read-only setup을 완료하고, 내 확인 뒤 실제 account read를 한 번 수행하세요. gmail.readonly와 calendar.readonly 외 scope를 요청하지 마세요.
- database.status=healthy, migration_version=10, Gmail/Calendar ok를 확인하세요. PII, OAuth JSON, token, DB, raw logs, screenshots, full status 또는 host config를 출력하지 마세요.
- 완료 시 변경과 결과, host profile isolation 여부, 자동 schedule 지원 여부, degraded 여부만 보고하세요.
```

## 3. 성공 기준

일상 대화에는 `serve`만 로드되고 전달 계약이 적용됩니다.
`database.status=healthy`, migration version 10, Gmail과 Calendar `ok`, 정확히
두 read-only scope가 필요합니다. daemon user service는 local background work만
시작해야 합니다. 자동 예약은 host가 dedicated per-run profile을 보장한 경우에만
선택 사항이며, 그렇지 않으면 구성하지 않은 것이 성공적인 fail-closed 결과입니다.

## 4. 보고와 되돌리기

막힌 단계, 경과 시간, redacted status 필드, 일상 profile, daemon, host isolation
판정을 보고합니다. 제거할 때는 host-owned agent schedule이 있으면 호스트에서
먼저 제거하고, 이어서 다음 credential-first 순서를 실행합니다.

```bash
set -euo pipefail
PROACTIVE_BIN="$HOME/venvs/proactive/bin/proactive-mcp"
"$PROACTIVE_BIN" service remove
"$PROACTIVE_BIN" disconnect
```

`{"google":"disconnected"}` 뒤에만 현재 호스트의 MCP 등록과
`~/.proactive-mcp`, `~/venvs/proactive`, 추출 bundle을 제거합니다. unrelated
host profile은 보존합니다.
