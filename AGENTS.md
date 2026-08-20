# proactive-mcp — 개발 에이전트 지침

## 개발 에이전트 계정

개발 에이전트는 Owner 계정이 아니라 전용 머신 계정 **`madrobot-agent`** (이 저장소의 Write collaborator)로 활동한다. 커밋·Issue·PR·댓글이 모두 이 계정으로 표시되어야 하며, 이래야 `@madrobotnet` 멘션 알림이 정상 동작한다 (자기 자신 멘션은 알림이 가지 않는다).

- GitHub 인증: `gh auth login`을 `madrobot-agent`로 수행
- git 커밋 identity:

```bash
git config --global user.name "madrobot-agent"
git config --global user.email "318970772+madrobot-agent@users.noreply.github.com"
```

## 정본 문서

[`docs/PRODUCT_PLAN.md`](docs/PRODUCT_PLAN.md)가 이 프로젝트의 단일 기준 문서다. 코드·README·이 문서가 기획서와 충돌하면 기획서를 따른다. 기획서의 확정 결정(§3)과 안전 불변식(§9)을 변경하려면 Owner(@madrobotnet) 승인이 필요하다.

## 작업 규칙

1. **마일스톤 순서 준수.** 기획서 §10의 M0→M6 순서로 진행한다. 현재 마일스톤의 완료 기준을 충족하기 전에 다음 마일스톤 코드를 작성하지 않는다.
2. **마일스톤 Issue를 작업 큐로 사용한다.** 각 마일스톤은 `milestone` 라벨이 붙은 GitHub Issue(M0~M6)로 등록되어 있다. 착수 시 해당 Issue에 착수 댓글을 남기고, 진행하며 완료 기준 체크박스를 갱신한다.
3. **마일스톤당 PR 하나**를 기본으로 한다. PR 본문에 범위, 완료 기준 충족 증거(테스트 결과), 남은 위험을 기록하고, `Closes #N`으로 해당 마일스톤 Issue를 연결해 머지 시 자동으로 닫히게 한다.
4. **막히면 묻는다.** 기획서에 없는 결정이 필요하거나 기획서와 모순을 발견하면, 임의로 해석하지 말고 아래 Owner 알림 프로토콜에 따라 GitHub Issue로 질문한다.
5. **범위 밖 확장 금지.** V2 항목(쓰기 액션, Tasks/Docs, Telegram, HTTP transport)을 "겸사겸사" 구현하지 않는다.

## Owner 알림 프로토콜

Owner는 GitHub 알림으로만 상황을 파악한다. 다음 규칙을 반드시 지킨다.

1. **차단되면 Issue를 만든다.** 개발이 막히거나 Owner 결정이 필요하면 즉시 GitHub Issue를 생성한다.
   - 제목: `[BLOCKED]` 또는 `[OWNER-DECISION]` 접두사 + 한 줄 요약
   - 라벨: `blocked` 또는 `owner-decision`을 붙인다
   - 본문 첫 줄에 반드시 `@madrobotnet` 멘션 (이것이 알림을 트리거한다)
   - 본문에 포함: 무엇이 막혔는지, 시도한 것, 선택지와 각각의 트레이드오프, 권고안
2. **마일스톤 완료 PR에도 멘션한다.** PR 본문에 `@madrobotnet`을 멘션해 완료 보고와 리뷰 요청을 알린다.
3. **기다리는 동안.** Owner 응답 대기 중에는 해당 결정에 의존하지 않는 같은 마일스톤 내 작업만 진행한다. 결정을 가정하고 코드를 먼저 쓰지 않는다.
4. **작업 재개 시 Issue부터 확인한다.** 세션을 시작하거나 재개할 때, open 상태의 `[BLOCKED]`/`[OWNER-DECISION]` Issue에 Owner 답변이 달렸는지 먼저 확인하고 반영한다.
5. **결정 반영 후 Issue를 닫는다.** Owner의 답변을 반영했으면 반영 내용을 해당 Issue에 댓글로 기록하고 Issue를 닫는다. Owner가 CLI 대화로 직접 결정을 준 경우에도 그 결정을 Issue에 기록한 뒤 닫는다 — 결정 기록은 항상 GitHub에 남긴다.
6. **조용한 실패 금지.** 오류를 우회했거나 범위를 축소했다면 그 사실을 PR 본문 또는 Issue에 명시한다. 로그에만 남기고 넘어가지 않는다.

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
