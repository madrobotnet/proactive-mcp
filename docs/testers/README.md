# 클로즈드 알파 테스터 안내

사용 중인 에이전트에 맞는 운영체제 시트를 열고 안내된 붙여 넣기 블록 전체를 기존 에이전트에게 보내 주세요. 설치, MCP 등록, Google 설정, 확인, 제거는 에이전트가 처리합니다. Google 동의 화면에서는 고급, 계속을 선택하고 막힌 지점을 알려 주세요.

Hermes Agent는 실제 세션에서 전달 확인이 결정적으로 재현되지 않아 이번 클로즈드 알파 지원 대상에서 제외합니다. Hermes에서는 아래 운영체제 시트를 진행하지 마세요.

- [Windows](windows.md)
- [Linux](linux.md)
- [macOS](macos.md)

공개 후에도 사람용 절차는 같습니다. 설치 파일을 받은 뒤 쓰는 에이전트에게 등록과 설정을 맡겨 주세요.

Linux aarch64 테스터는 `~/Downloads/proactive-mcp-alpha-linux-aarch64-py311.tar.gz`를 받습니다. 다른 채널의 archive SHA-256을 먼저 확인하고 `~/Downloads/proactive-mcp-alpha/`가 이미 있으면 덮어쓰거나 지우지 말고 멈춥니다. 새 디렉터리에 푼 뒤 bundle 안의 `SHA256SUMS`와 실제 `wheels/` 파일 목록이 정확히 일치하는지 확인하고 `bundle-metadata.json`이 지정한 project wheel 파일을 정확한 경로로 설치합니다. PyPI, `uvx`, 저장소 clone은 쓰지 마세요.
