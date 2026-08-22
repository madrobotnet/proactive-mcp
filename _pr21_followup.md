## Owner 스모크 후속 — Grok 시나리오 1·3 결과 (재로그인 후)

시나리오 1은 **PASS**(단, 절차 결함 2건 발견·우회), 시나리오 3은 **FAIL**(P1-2/P1-3의 Grok 측 근거 추가). 이로써 5개 시나리오 전부 1차 실행 완료.

### 시나리오 1: Grok fresh session — PASS (2차 시도)

2차 시도에서 통과: 인사만 던졌는데 `proactive_check` 선호출 → 답변 첫머리에 "3일 뒤(8월 25일)가 스모크 기념일" 선제 언급 + 메일·캘린더 미연결 명시. 해당 상황 pending→delivered 전환 DB로 확인.

1차 시도는 실패였고, 원인은 테스트 절차 결함 2건이다. 문서 수정 필요(P2):

- **P2-1. `--cwd`가 저장소를 가리키면 repo `AGENTS.md`가 세션을 하이재킹한다.** 1차 시도에서 Grok이 `proactive_check`를 호출해 상황을 claim까지 했으나(DB 확인), 저장소 `AGENTS.md`(개발 에이전트 지침)를 프로젝트 지침으로 로드해 마일스톤 브리핑에 집중하느라 기념일을 언급하지 않았다 — 상황이 소비되고 전달은 안 된 것. 스모크 문서·INTEGRATIONS의 fresh-session 예시가 모두 `--cwd $repo`인데, Owner 머신처럼 저장소에 운영용 AGENTS.md가 있으면 세션 규칙과 경합한다. 2차 시도는 빈 중립 폴더(`m5-smoke\neutral`)를 `--cwd`로 써서 통과했다. fresh-session/스케줄 실행 모두 중립 작업 폴더 기준으로 문서를 수정할 것.
- **P2-2. M5-P3의 `$grokSessionRule`/`$codexSessionRule`이 정본 규칙의 "If it returns situations, lead your reply with a short, natural summary of them" 절을 누락.** pass 기준은 "언급"인데 규칙에는 언급 의무가 없다. INTEGRATIONS.md의 정본 세션 규칙과 동일 문구로 맞출 것. (2차 시도는 이 절을 포함한 규칙으로 실행했다.)

### 시나리오 3: Grok scheduled — FAIL (P1-2/P1-3 Grok 측 근거)

마커 원문:

```
stamp=20260822-232200 cli=grok result=failure reason=invalid_token notify=sent exit=2
```

- 무인 트리거 발화, 상황 pending→delivered 전환은 실행 중 시각으로 확인(무인 claim 성공).
- 래퍼는 규약대로 fail-loud로 동작해 "Proactive check failed" 고정 토스트를 발사(notify=sent) — 실패가 침묵하지 않았다는 점은 설계 의도대로.
- 그러나 판정은 `invalid_token`: **Grok 헤드리스 `-p` stdout은 모델의 중간 메시지와 최종 메시지를 구분자 없이 이어 붙인다**(예: "I'll look up the ... schema, then call it....7" 처럼 서두+결과가 한 줄로 붙음). 따라서 "전체 출력 == 토큰" exact-match는 모델이 서두 메시지를 한 번이라도 내면 구조적으로 실패한다. Codex의 transcript 문제(P1-2)와 원인은 다르지만 결론은 같다 — **최종 판정이 에이전트 stdout 형식/모델 순응에 의존하면 안 된다** (P1-3의 `budget.used` 기반 판정 제안 참조).

### 확인 포인트 (M5 artifact/DACL)

- 아티팩트 전부 `m5-smoke` 안: proactive.db, config.toml, m5-logs(래퍼 2 + 마커 3), neutral(Owner 추가분). 부모 폴더 신규 항목은 `m5-smoke`뿐.
- DACL: smoke 디렉터리·DB·config·마커 모두 Owner 계정 단독 ACE, Everyone/BUILTIN\Users/Authenticated Users 없음.
- 마커 3건 모두 고정 필드 1줄, 에이전트 출력 유출 없음. 실제 DB LastWriteTime은 스모크 시작 이전 그대로.

### 현재 수용 판정

기준 5~8 중: 5 (Grok fresh) PASS · 6 (Codex fresh) PASS · 7 (Grok scheduled) **FAIL** · 8 (Codex scheduled) **FAIL**.

**M5 acceptance: FAIL — scheduled half. 시나리오 3·4가 P1-1~3 수정 후 재실행 필요.** 수정 커밋이 올라오면 재검증하고 Issue #6에 최종 수용 체크리스트를 올리겠다. 스모크 환경(격리 DB·CLI 등록)은 재검증을 위해 유지 중이며, 최종 정리 시 양쪽 CLI의 `proactive` 등록은 스모크 이전 상태(둘 다 미등록)로 되돌릴 예정.

버전: grok 0.2.93, codex-cli 0.149.0, uv 0.11.x, Windows PowerShell 5.1, branch `feat/m5-integration-recipes` @ a3712e6.
