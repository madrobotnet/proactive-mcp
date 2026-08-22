## Owner 리뷰 + M5 스모크 중간 보고 (Windows Owner 머신)

문서 리뷰·코드 대조·로컬 검증은 통과. Owner 스모크에서 시나리오 2·5는 PASS, **시나리오 4는 FAIL** — 아래 P1 3건 수정 후 재검증 필요. 시나리오 1·3(Grok)은 Grok CLI 재로그인 대기 중.

### 리뷰 결과 (문서)

- 정본 반영 정확: §5.3 Cursor 행 삭제, OpenClaw V2 행, #20 결정 기록, M5 실증 조합 Grok+Codex — README·INTEGRATIONS·스모크 문서까지 일관.
- 문서가 주장하는 코드 동작 대조 통과: `PROACTIVE_DATABASE`(paths.py), quiet hours 동일값 비활성화(policy.py `is_quiet_time`).
- 격리 DB(m5-smoke) 설계와 2-토큰 프라이버시 경계는 좋은 설계. 로그 프라이버시 규칙 §9.2 정합.
- 로컬(Windows): pytest 328 passed 12 skipped, ruff clean. CI 3 OS green.

### 스모크 환경

- branch `feat/m5-integration-recipes` @ a3712e6, migration_version 8
- 격리 게이트 통과: `database.path`가 `...\m5-smoke\proactive.db`, gmail/calendar 모두 `not_configured`, journal wal, overall degraded(예상)
- 버전: grok 0.2.93, codex-cli 0.149.0(스모크 중 0.142.5에서 업그레이드 — 계정 설정 모델이 신버전 요구), uv 0.11.x, Windows PowerShell 5.1
- 기존에 양쪽 CLI 모두 `proactive` 등록이 없었으므로, cleanup에서는 문서의 "기본 DB로 재등록" 대신 **등록 제거**로 원상복구 예정

### 시나리오 결과

| 시나리오 | 결과 | 비고 |
|---|---|---|
| 1. Grok fresh session | 보류 | grok.com 인증 만료 — Owner 재로그인 후 진행 |
| 2. Codex fresh session | **PASS** | 인사만 했는데 `proactive_check` 선호출 → "8월 25일 스모크 기념일 D-3" 선제 언급 + 소스 미설정 경고 명시. delivered 1/pending 0 전환 확인 |
| 3. Grok scheduled | 보류 | 동상 |
| 4. Codex scheduled | **FAIL** | 아래 P1-3. 무인 claim·마커·토스트 체인 자체는 동작 확인 |
| 5. delivered once | **PASS** | 두 독립 세션 연속 체크. 1차만 전달, 2차 미반환, delivered +1(+2 아님), pending 1→0, 중복 없음 |

### P1-1. Codex 비대화형 MCP 호출은 승인 설정 없이는 전부 실패

codex-cli 0.149.0에서 `codex exec`(approval policy never)로 MCP 도구 호출 시:

```
MCP tool call requires approval, but approval policy is never
```

`remember`·`proactive_check` 전부 실패. 해결은 서버별 승인 모드 설정([openai/codex#24135](https://github.com/openai/codex/issues/24135)):

```
codex exec -c mcp_servers.proactive.default_tools_approval_mode="approve" ...
```

(또는 `~/.codex/config.toml`의 `[mcp_servers.proactive]`에 영구 설정). INTEGRATIONS.md Codex 레시피(§Codex CLI 4, OS scheduler handoff의 두 래퍼), WINDOWS_SMOKE_TEST.md M5-P3·시나리오 2·4에 모두 반영 필요. 이 오버라이드를 적용한 뒤에야 시나리오 2·4·5를 진행할 수 있었다.

### P1-2. 래퍼의 토큰 추출이 codex stdout 형식과 불일치

`codex exec`는 stdout에 최종 메시지만이 아니라 **전체 transcript**(헤더, user echo, `mcp:` 라인, `tokens used`, 카운트, 최종 메시지)를 출력한다. 문서 래퍼의 `(-join "`n").Trim()` 전체 비교는 어떤 실행에서도 `PROACTIVE_ATTENTION`과 일치할 수 없음 → 항상 `invalid_token`. Owner 스모크에서는 마지막 줄 추출로 대체해 진행했다(codex는 최종 메시지를 마지막에 다시 출력). Linux cron 래퍼의 `RAW=$(codex exec ...)` 전체 비교도 동일 문제로 보이므로 확인 필요. grok `--single`의 stdout 형식도 실측 확인 요망.

### P1-3 (핵심). LLM 토큰 매핑이 비결정적 — 전달 가시성 계약이 성립하지 않음

무인 스케줄 실행 2회의 실측(마커 원문, 고정 필드만):

```
stamp=20260822-225559 cli=codex result=none      notify=none exit=0   ← 이 실행이 상황을 claim함 (DB delivered_at 일치)
stamp=20260822-230100 cli=codex result=attention notify=sent exit=0   ← 이 실행은 claim할 상황이 없었음
```

- 1회차: pending 상황을 claim(무인 pending→delivered 전환은 성공)하고도 최종 토큰 `PROACTIVE_NONE` → **거짓 음성**. 문서가 정의한 실패 그 자체: "the task consumed rather than delivered the situation".
- 2회차: 상황 없음(경고만 5건)인데 `PROACTIVE_ATTENTION` + 토스트 발사 → **거짓 양성**.
- 수동 재현: 경고만 있는 상태에서 동일 프롬프트 2회 모두 NONE(문구상 ATTENTION이어야 함), 상황이 있는 상태에서는 ATTENTION 1회. 모델(gpt-5.6-sol)이 "경고 non-empty → ATTENTION" 규칙을 자의적으로 무시하며, 결과가 실행마다 다르다.

제안(판정을 LLM 밖으로): 래퍼가 에이전트 실행 **전후로 `proactive-mcp status`(PII-free JSON)를 읽어 `budget.used` 증가 여부로 전달 발생을 판정**하고, 경고 유무는 status의 `warnings` 길이로 판정. 에이전트 stdout은 비교조차 하지 않고 폐기 → 프라이버시 경계가 지금보다 단순해지고(메모리 보관도 불필요), 판정이 결정론이 된다. 프롬프트는 "call proactive_check exactly once"만 남기면 된다. 다른 방식이어도 좋으나, 최종 판정이 모델 출력에 의존하지 않아야 한다는 것이 요건.

### 확인된 좋은 소식

- 무인 체인 전체(Task Scheduler → PowerShell 래퍼 → codex → MCP claim → 고정 WinRT 토스트)가 Windows에서 실제 동작. AUMID 토스트가 스케줄 세션에서도 표시됨(23:01 실행 notify=sent, 화면 표시 확인).
- 마커 파일은 전 실행에서 고정 필드 1줄만 생성 — 로그 프라이버시 경계 유지 확인.
- `m5-smoke` 밖에 새 아티팩트 없음.

P1 3건 반영되면 시나리오 4를 재실행하고, Grok 로그인 후 1·3까지 마쳐 Issue #6에 수용 체크리스트로 최종 보고하겠다. 시나리오 1·3도 P1-1~3과 같은 계열의 문제를 만나면 개별 보고 예정.
