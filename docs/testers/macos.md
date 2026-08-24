# macOS 클로즈드 알파 테스터 시트

## 1. 받은 것

Owner에게 wheel, 다른 인증 채널의 SHA-256, 필요하면 OAuth JSON을 각각 받으세요. 알파에서는 PyPI, `uvx`, 저장소 clone을 사용하지 않습니다.

## 2. 에이전트에게 붙여 넣기

아래 블록 전체를 지금 쓰는 에이전트에 붙여 넣으세요. Gatekeeper나 알림 권한 창이 나오면 에이전트가 우회하지 말고 창의 문구와 선택지를 보여 주세요. 선택은 직접 하시면 됩니다.

```text
이 macOS에 proactive-mcp 클로즈드 알파를 설치하고, 지금 이 에이전트에 MCP로 등록해 주세요. 제가 명령을 외우거나 mcp add 명령과 설정 파일 편집을 직접 하지 않게 해 주세요.

규칙:
- 저장소 clone, PyPI, uvx, pip install proactive-mcp, 관리자 권한 사용, PROACTIVE_DATABASE 설정은 금지입니다.
- wheel은 ~/Downloads/proactive_mcp-0.1.0-py3-none-any.whl에 있습니다. shasum -a 256으로 다른 채널에서 받은 SHA-256과 비교하고, 다르면 설치를 멈추세요.
- Python 3.11과 uv가 없으면 설치하세요. ~/venvs/proactive 전용 venv를 만들고 wheel만 설치하세요.
- OAuth JSON이 있으면 ~/.proactive-mcp/client_secret.json으로 옮기고, 없으면 제가 BYO인지 확인한 뒤 docs/SETUP_GOOGLE.md를 안내하세요.
- 이 에이전트에 MCP를 등록하세요. 일상용은 serve, 스케줄용은 serve-scheduled입니다. 등록에는 ~/를 쓰지 말고 /Users/내-사용자명/venvs/proactive/bin/proactive-mcp 같은 절대 경로를 쓰세요.
- setup을 실행하세요. Google 경고가 나오면 제가 고급, 계속을 누릅니다. Gatekeeper나 알림 권한 창은 자동으로 우회하거나 허용하지 말고, 표시된 문구와 선택지를 저에게 보여 주세요.
- Google read-only sources configured.가 성공 줄인지 확인한 뒤 google-smoke --confirm-real-account-read, daemon --once, status를 실행하세요.
- status에서 database.status=healthy, migration_version=9, gmail과 calendar가 ok인지 보여 주세요. 데몬이 꺼져 있어 overall=degraded인 것은 허용됩니다.
- 메일 제목, 본문, 주소, 일정 제목, 토큰, OAuth JSON, 스크린샷, status 전체를 출력하거나 이슈에 붙이지 마세요.
- 완료 후 get_status로 database.path와 두 Google status만 말하세요. receipt_token이 있으면 결과를 받은 뒤 confirm_delivery를 정확히 한 번 호출하세요.
```

## 3. 성공 기준

`database.status=healthy`, `migration_version=9`, `google.gmail.status=ok`, `google.calendar.status=ok`이면 성공입니다. 데몬이 꺼져 있어 `overall=degraded`인 것은 허용됩니다. Google 권한은 읽기 전용 `gmail.readonly`와 `calendar.readonly`만 요청합니다.

## 4. 보고

막힌 단계, 설치에 걸린 시간, 성공 또는 실패를 알려 주세요. 상태 정보는 `overall`, `database.status`, `migration_version`, `google.gmail.status`, `google.gmail.error_code`, `google.calendar.status`, `google.calendar.error_code`, 경고 문자열만 보내세요.

## 5. 되돌리기

되돌릴 때도 에이전트에게 맡기세요. 먼저 proactive-mcp 자격 증명을 지우고 성공을 확인하도록 요청하세요. 성공한 뒤에만 MCP 등록과 스케줄 작업을 지우고 `~/.proactive-mcp/`와 `~/venvs/proactive/`를 지우도록 하세요. 자격 증명 삭제가 실패하면 상태 폴더는 그대로 두고 Google 계정 권한을 취소한 뒤 Owner에게 알려 주세요.
