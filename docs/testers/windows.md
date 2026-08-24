# Windows 클로즈드 알파 테스터 시트

## 받은 것

Owner에게 wheel, SHA-256, OAuth JSON을 각각 다른 비공개 채널로 받으세요. OAuth JSON이 없으면 BYO 여부를 확인한 뒤 `SETUP_GOOGLE.md`를 따르시면 됩니다. PyPI, `uvx`, 저장소 clone은 쓰지 마세요.

## 에이전트에게 붙여 넣기

아래 블록 전체를 지금 사용 중인 에이전트에 한 번만 붙여 넣으세요. Google 동의 화면이 열리면 고급, 계속을 직접 선택해 주세요.

```text
이 Windows PC에 proactive-mcp 클로즈드 알파를 설치하고, 지금 이 에이전트에 MCP로 등록해 주세요. 내가 명령을 외우거나 grok mcp add / codex mcp add를 직접 치우지 않게 해 주세요.

규칙:
- 저장소 clone 금지. PyPI, uvx, pip install proactive-mcp 금지. 관리자 권한 금지. PROACTIVE_DATABASE 설정 금지.
- wheel: %USERPROFILE%\Downloads\proactive_mcp-0.1.0-py3-none-any.whl
- 다른 채널로 받은 SHA-256과 비교하고, 다르면 설치를 멈추세요.
- OAuth JSON이 있으면 %USERPROFILE%\.proactive-mcp\client_secret.json으로 옮기세요. 없으면 내가 BYO인지 확인하고 SETUP_GOOGLE.md를 안내하세요.
- Python 3.11과 uv가 없으면 설치하세요. 전용 venv(%USERPROFILE%\venvs\proactive)에 wheel만 넣으세요.
- 이 에이전트에 등록하세요. 일상용은 serve, 스케줄용은 serve-scheduled입니다. 경로는 절대 경로로 넣으세요.
- 이 호스트의 세션 시작 규칙에 "새 세션마다 proactive_check를 한 번 호출하고, 결과를 받은 뒤 receipt_token이 있을 때만 confirm_delivery를 정확히 한 번 호출한다"를 넣고 새 세션에서 실제 호출을 확인하세요.
- 이 호스트에 native scheduler가 있으면 그것을 쓰고, 없으면 Windows 작업 스케줄러로 현재 에이전트를 실행해 serve-scheduled의 proactive_check와 조건부 confirm_delivery를 호출하세요. 스케줄 작업이 proactive-mcp CLI만 직접 실행하게 하지 말고, 에이전트가 MCP 도구를 호출하게 하세요.
- setup을 실행하세요. Google 경고가 나면 내가 고급 → 계속을 누릅니다. 성공 줄은 Google read-only sources configured. 입니다.
- 그다음 google-smoke --confirm-real-account-read와 daemon --once를 돌리고, status에서 gmail과 calendar가 ok인지 보여 주세요.
- 검증이 끝나면 Windows 작업 스케줄러에 절대 경로의 proactive-mcp daemon을 로그인 시 시작하고 실패 시 다시 시작하도록 등록한 뒤, 계속 실행 중인지 확인하세요. 등록할 수 없으면 주기 sync와 OS 알림 폴백이 없다는 점을 설명하고 degraded mode 사용에 대한 내 명시적 동의를 받기 전에는 완료로 보고하지 마세요.
- 메일 제목·본문·주소, 일정 제목, 토큰, JSON, 스크린샷, status 전체는 출력하거나 이슈에 붙이지 마세요.
- 다 되면 get_status로 database.path와 두 Google status만 말하고, receipt_token이 있으면 confirm_delivery를 한 번만 호출하세요.
```

## 성공 기준

- `database.status=healthy`, `migration_version=9`이면 됩니다.
- Gmail과 Calendar 상태가 모두 `ok`입니다.
- 세션 시작 규칙과 예약 전달 작업에서 에이전트가 `proactive_check`를 실제 호출하고, `receipt_token`이 있을 때만 `confirm_delivery`를 한 번 호출합니다.
- continuous watcher가 등록되어 실행 중입니다. 등록하지 못한 경우에는 주기 sync와 OS 알림 폴백이 없다는 설명을 듣고 degraded mode에 명시적으로 동의해야 합니다.
- `overall=degraded`는 continuous watcher를 등록하지 못해 degraded mode에 명시적으로 동의한 경우에만 허용됩니다.
- Google 범위는 `gmail.readonly`와 `calendar.readonly`만 사용합니다.

## 보고

막힌 단계, 설치에 걸린 시간, 세션 시작 규칙·예약 전달·continuous watcher를 포함한 성공 또는 실패만 알려 주세요. 상태값은 `overall`, `database.status`, `migration_version`, `google.gmail.status`, `google.gmail.error_code`, `google.calendar.status`, `google.calendar.error_code`, 경고 문자열만 보내세요. 메일이나 일정 내용, PII, OAuth JSON, 토큰, 스크린샷, `status` 전체는 보내지 마세요.

## 되돌리기 프롬프트

제거가 필요하면 에이전트에게 다음과 같이 요청하세요. "먼저 proactive-mcp 자격 증명을 삭제하고 성공을 확인해 주세요. 그다음 이 에이전트의 MCP 등록과 상태 폴더를 정리해 주세요. 자격 증명 삭제가 실패하면 상태 폴더는 지우지 말고 알려 주세요."
