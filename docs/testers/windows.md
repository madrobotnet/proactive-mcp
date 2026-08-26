# Windows 클로즈드 알파 테스터 시트

터미널에서 MCP를 직접 등록하지 마세요. 쓰는 에이전트(Grok, Codex, Cursor 등)를 열고 아래를 붙여 넣으면 됩니다. 브라우저에서 Google 동의 화면이 뜨면 본인만 눌러 주세요.

목표는 설치부터 Gmail·Calendar가 `ok`가 될 때까지 15분입니다. 막힌 지점을 적어 주세요.

## 받은 것

Owner가 채널을 나눠 보냅니다. 파일 이름은 받은 그대로 쓰세요. 아래는 `0.1.0` 예시입니다.

1. `.whl` 파일
2. 다른 채널의 SHA-256
3. 대부분의 경우 `client_secret.json`. BYO로 지정된 분만 JSON 없이, 에이전트에게 [`SETUP_GOOGLE.md`](../SETUP_GOOGLE.md)를 따라 만들라고 하세요.

관리자 PowerShell은 쓰지 마세요. 파일을 커밋하거나 채팅에 붙여 넣지 마세요.

## 에이전트에게 붙여 넣기

```text
이 Windows PC에 proactive-mcp 클로즈드 알파를 설치하고, 지금 이 에이전트에 MCP로 등록해 주세요. 내가 명령을 외우거나 grok mcp add / codex mcp add를 직접 치우지 않게 해 주세요.

규칙:
- 저장소 clone 금지. PyPI, uvx, pip install proactive-mcp 금지. 관리자 권한 금지. PROACTIVE_DATABASE 설정 금지.
- wheel: %USERPROFILE%\Downloads\proactive_mcp-0.1.0-py3-none-any.whl
- 다른 채널로 받은 SHA-256과 비교하고, 다르면 설치를 멈추세요.
- OAuth JSON이 있으면 %USERPROFILE%\.proactive-mcp\client_secret.json으로 옮기세요. 없으면 내가 BYO인지 확인하고 SETUP_GOOGLE.md를 안내하세요.
- Python 3.11과 uv가 없으면 설치하세요. 전용 venv(%USERPROFILE%\venvs\proactive)에 wheel만 넣으세요.
- 이 에이전트에 등록하세요. 일상용은 serve, 스케줄용은 serve-scheduled입니다. 경로는 절대 경로로 넣으세요.
- setup을 실행하세요. Google 경고가 나면 내가 고급 → 계속을 누릅니다. 성공 줄은 Google read-only sources configured. 입니다.
- 그다음 google-smoke --confirm-real-account-read와 daemon --once를 돌리고, status에서 gmail과 calendar가 ok인지 보여 주세요.
- 메일 제목·본문·주소, 일정 제목, 토큰, JSON, 스크린샷, status 전체는 출력하거나 이슈에 붙이지 마세요.
- 다 되면 get_status로 database.path와 두 Google status만 말하고, receipt_token이 있으면 confirm_delivery를 한 번만 호출하세요.
```

파일 이름과 해시가 다르면 그 두 줄만 고쳐서 넣으세요.

## 성공으로 보는 것

에이전트가 보여 주는 값이 이것과 같으면 됩니다.

- `database.status`는 `healthy`, `database.migration_version`은 `9`
- `google.gmail.status`와 `google.calendar.status`는 `ok`
- `overall`은 데몬이 꺼져 있으면 `degraded`여도 됩니다
- 걸린 시간이 15분을 넘기면, 어디가 막혔는지만 적어 주세요

## 보고

보내도 되는 것은 버전, 위 상태 값, 각 `error_code`, warning 문자열, 걸린 시간, 막힌 지점입니다.

메일·일정·토큰·OAuth JSON·스크린샷·`status` 전체·데이터베이스 파일은 보내지 마세요.

## 되돌리기

지우고 싶으면 에이전트에게 이렇게 말하면 됩니다. 자격 증명을 먼저 지우고, 그다음에 폴더를 지워야 합니다.

```text
proactive-mcp를 이 PC에서 제거해 주세요. 에이전트 MCP 등록을 빼고, 자격 증명을 먼저 삭제한 뒤에만 %USERPROFILE%\.proactive-mcp와 venvs\proactive를 지우세요. 메일 내용과 토큰은 출력하지 마세요.
```
