# 클로즈드 알파 테스터 안내

사용 중인 에이전트에 맞는 운영체제 시트를 열고 안내된 붙여 넣기 블록 전체를 기존 에이전트에게 보내 주세요. 설치, MCP 등록, Google 설정, 확인, 제거는 에이전트가 처리합니다. 각 블록은 `reply_deadline` 후보 필터, 검토한 lease 전체 확정, 불확실성 처리, 사용자 언어 전달, 일상 대화와 예약 대화의 프로필 분리를 모두 포함합니다. 일부 문장만 골라 붙여 넣지 마세요. Google 동의 화면에서는 고급, 계속을 선택하고 막힌 지점을 알려 주세요.

Hermes Agent는 아래 테스터 경로에서 제외합니다. Hermes Native Cron 연동은 Owner 전용 검증이며 Grok CLI와 Codex CLI가 기본 지원 경로입니다. Hermes에서는 아래 운영체제 시트를 진행하지 마세요.

## 공통 전달 계약

모든 시트는 같은 계약을 사용합니다. `reply_deadline`은 행동 필요 판정이 아니라 보수적으로 뽑은 후보입니다. 사용자에게 말하기 전에 뉴스레터·마케팅·자동 영수증, 요청이 없는 FYI 또는 FYI-CC, 다른 사람이 맡은 스레드, 이 사용자에게 답해야 할 질문·요청·결정이 없는 행은 확신할 수 있을 때 제외합니다. 명시적인 회신·RSVP·결정 요청, 사용자가 책임진 마감, 이 사용자에게 직접 묻고 아직 답하지 않은 질문은 유지합니다. 불확실한 후보는 사용자에게 알리거나 lease 전체를 미확정 상태로 두거나 일상 대화에서 snooze하며, 비실행 항목이라고 조용히 버리지 않습니다. 모든 행을 검토한 뒤 확정하기로 선택할 때만, 보여 주지 않기로 확신한 후보까지 포함해 검토한 lease 전체를 정확히 한 번 확정합니다. MCP 도구명·설명·필드·값은 영어로 유지하되 사용자에게는 사용자의 언어로 말합니다. 일상 대화에는 `serve`만, 별도 예약 대화에는 `serve-scheduled`만 로드하며 한 대화에 두 프로필을 함께 로드하지 않습니다. Grok은 prompt로 이 분리를 보장할 수 없으므로 두 개의 신뢰된 project 디렉터리와 project-scope 등록을 사용하고, 예약 wrapper가 네 raw config source와 모든 nested Claude project entry의 중복, project scope와 command, list, healthy handshake와 정확한 3-tool surface를 매번 검사합니다. 상속된 full 등록을 예약 디렉터리에서 없앨 수 없으면 Codex를 예약 collector로 사용합니다.

- [Windows](windows.md)
- [Linux](linux.md)
- [macOS](macos.md)

공개 후에도 사람용 절차는 같습니다. 설치 파일을 받은 뒤 쓰는 에이전트에게 등록과 설정을 맡겨 주세요.

Linux aarch64 테스터는 `~/Downloads/proactive-mcp-alpha-linux-aarch64-py311.tar.gz`를 받습니다. 다른 채널의 archive SHA-256을 먼저 확인하고 `~/Downloads/proactive-mcp-alpha/`가 이미 있으면 덮어쓰거나 지우지 말고 멈춥니다. 새 디렉터리에 푼 뒤 bundle 안의 `SHA256SUMS`와 실제 `wheels/` 파일 목록이 정확히 일치하는지 확인하고 `bundle-metadata.json`이 지정한 project wheel 파일을 정확한 경로로 설치합니다. PyPI, `uvx`, 저장소 clone은 쓰지 마세요.
