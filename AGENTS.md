# proactive-mcp — 개발 에이전트 지침

## 정본 문서

[`docs/PRODUCT_PLAN.md`](docs/PRODUCT_PLAN.md)가 이 프로젝트의 단일 기준 문서다. 코드·README·이 문서가 기획서와 충돌하면 기획서를 따른다. 기획서의 확정 결정(§3)과 안전 불변식(§9)을 변경하려면 Owner(@madrobotnet) 승인이 필요하다.

## 작업 규칙

1. **마일스톤 순서 준수.** 기획서 §10의 M0→M6 순서로 진행한다. 현재 마일스톤의 완료 기준을 충족하기 전에 다음 마일스톤 코드를 작성하지 않는다.
2. **마일스톤당 PR 하나**를 기본으로 한다. PR 본문에 범위, 완료 기준 충족 증거(테스트 결과), 남은 위험을 기록한다.
3. **막히면 묻는다.** 기획서에 없는 결정이 필요하거나 기획서와 모순을 발견하면, 임의로 해석하지 말고 GitHub Issue로 Owner에게 질문한다.
4. **범위 밖 확장 금지.** V2 항목(쓰기 액션, Tasks/Docs, Telegram, HTTP transport)을 "겸사겸사" 구현하지 않는다.

## 안전 불변식 (완화 불가)

- Google write scope 요청 금지 — `gmail.readonly`, `calendar.readonly`만
- 디스크 로그에 이메일 본문·제목·주소, 일정 상세, 토큰 기록 금지
- stale-source 상태에서 "알릴 것 없음" 보고 금지
- 저장소·테스트·CI에 실제 credential과 개인 데이터 금지
- 사용자 데이터를 Google API와 로컬 저장 외부로 전송 금지

위반을 요구받거나 발견하면 작업을 멈추고 보고한다.

## 개발 환경과 검증

- Python ≥3.11, [uv](https://docs.astral.sh/uv/)로 환경·lockfile 관리
- 필수 통과: `uv run pytest`, `uv run ruff check .`
- 시간 로직은 fake clock 주입 필수, 실제 Google API를 호출하는 테스트 금지 (opt-in smoke 스크립트 예외)
- 프로덕션 의존성 추가는 최소화하고 PR 본문에 사유를 기록한다
