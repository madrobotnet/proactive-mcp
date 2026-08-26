# 클로즈드 알파 테스터 안내

운영체제 시트의 붙여 넣기 블록 전체를 현재 사용 중인 에이전트에게 보내
주세요. 설치, MCP 등록, Google 설정, 확인, 제거는 그 에이전트와 호스트가
처리합니다. proactive-mcp 자체는 에이전트, 모델, 대화 또는 전달 채널을
시작하지 않습니다.

- [Windows](windows.md)
- [Linux](linux.md)
- [macOS](macos.md)

## 공통 전달 계약

`reply_deadline`은 행동 판정이 아니라 보수적 후보입니다. 호스트는 말하기
전에 뉴스레터, 마케팅, 자동 영수증, no-ask FYI/FYI-CC, 다른 사람이 맡은
스레드, 이 사용자에게 질문·요청·결정이 없는 행을 확신할 수 있을 때
제외합니다. 명시적 회신·RSVP·결정 요청, 사용자 소유 마감, 사용자에게 직접
묻고 아직 답하지 않은 질문은 유지합니다. 불확실성은 알리거나 lease 전체를
미확정으로 두거나 일상 대화에서 snooze하며 조용히 버리지 않습니다. 모든
행을 검토하고 확정하기로 선택한 경우에만, 보이지 않게 제외한 후보까지
포함한 lease 전체를 하나의 `receipt_token`으로 정확히 한 번
`confirm_delivery` 합니다. MCP는 영어를 유지하고 사용자에게는 사용자의
언어로 말합니다.

일상 대화에는 `serve`만, 별도 수동/예약 대화에는 `serve-scheduled`만
로드합니다. 한 대화에 둘을 함께 로드하지 않습니다. 이 격리는 호스트와
운영자의 책임이며 proactive-mcp는 호스트 설정을 검사하거나 호스트를
시작하지 않습니다. 전용 per-run MCP profile을 제공하지 못하는 호스트에서는
자동 예약 사용을 구성하지 않습니다. `serve-scheduled`만 실행해도 대화나
전달은 시작되지 않으며, pending 상황은 실행 중인 호스트 에이전트가 도구를
명시적으로 호출할 때까지 남습니다.

Grok 0.2.112는 병합된 여러 설정 source에서 immutable per-run 격리를 증명할
수 없으므로 unattended Grok scheduling을 지원한다고 안내하지 않습니다.
Codex config layer도 plugin이 격리되었다고 보증하지 않습니다. Hermes Native
Cron은 Owner-only이고 Hermes가 소유하는 기능입니다.

Linux aarch64 테스터는 archive와 별도 채널의 archive SHA-256을 먼저
확인합니다. 기존 추출 디렉터리를 덮어쓰지 않고 bundle 내부
`SHA256SUMS`와 wheel 목록을 모두 확인한 뒤 metadata가 지정한 wheel만
설치합니다. PyPI, `uvx`, 저장소 clone은 사용하지 않습니다.
