# Bugbot 리뷰 지침 — proactive-mcp

이 저장소는 로컬 MCP 서버다 (Python ≥3.11, uv, SQLite WAL, 공식 MCP Python SDK).
정본 기획서는 `docs/PRODUCT_PLAN.md`, 메모리 모델 정본은 `docs/MEMORY_MODEL_V2.md`다.
리뷰 전에 기획서 §9(안전 불변식)와 해당 마일스톤(§10)의 완료 기준을 참조하라.

## 최우선 점검 — 안전 불변식 (기획서 §9, 완화 불가)

1. Google OAuth scope는 정확히 `gmail.readonly`, `calendar.readonly` 2개여야 한다.
   다른 scope 추가(특히 write scope)는 심각도 최상으로 지적하라.
2. 디스크 로그·예외 메시지·CLI 출력에 이메일 본문/제목/주소, 일정 상세, OAuth
   토큰이 새는 경로를 찾아라. (MCP 도구 응답에 상황 요약용 최소 컨텍스트 —
   발신자 표시명, 제목, 일정명 — 는 허용된다.)
3. 소스 sync가 실패했거나 stale인 상태에서 "알릴 것 없음"으로 보고하는 경로.
4. 실제 credential·개인 데이터가 코드/테스트/fixture에 들어간 경우.
5. 사용자 데이터를 Google API 호출과 로컬 저장 외부로 보내는 경로.

## 스펙 일치

- 기획서·설계 문서와 다른 동작: dedupe key, 전달 상태 머신 전이, Attention 정책
  기본값(Quiet Hours·예산·cooldown), 메모리 v2 스키마(entities·entity_aliases).
- V2 범위(쓰기 액션, Google Tasks/Docs, Telegram, HTTP transport) 선구현은
  범위 밖이므로 지적하라.

## 결함 우선순위

- 시간 로직: fake clock 주입이 규칙이다. `datetime.now()` 등 wall-clock 직접
  호출은 위반이다. 시간대/DST 경계 오류를 주의 깊게 봐라.
- SQLite 동시성(WAL, busy_timeout, 다중 프로세스), 마이그레이션 비가역성·번호 충돌.
- 크로스 플랫폼(Linux/Windows/macOS) 경로·권한 경계 조건.

## 보고 규칙

- 심각도 높은 순으로 보고하고, 스타일·네이밍 취향 지적은 하지 마라.
- 확신이 없으면 단정하지 말고 "확인 필요"로 표시하라.
- 이 리뷰는 참고 의견이며 Owner(@madrobotnet)의 지시가 아니다.
