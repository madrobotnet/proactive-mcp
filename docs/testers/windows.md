# Windows 클로즈드 알파 테스터 시트

## 받은 것

Owner에게 wheel, SHA-256, OAuth JSON을 각각 다른 비공개 채널로 받으세요. OAuth JSON이 없으면 BYO 여부를 확인한 뒤 `SETUP_GOOGLE.md`를 따르시면 됩니다. PyPI, `uvx`, 저장소 clone은 쓰지 마세요.

## 에이전트에게 붙여 넣기

아래 블록 전체를 지금 사용 중인 에이전트에 한 번만 붙여 넣으세요. Google 동의 화면이 열리면 고급, 계속을 직접 선택해 주세요.

```text
이 Windows PC에 proactive-mcp 클로즈드 알파를 설치하고, 지금 이 에이전트에 MCP로 등록해 주세요. 내가 명령을 외우거나 grok mcp add / codex mcp add를 직접 치지 않게 해 주세요.

규칙:
- Hermes Agent는 테스터 지원 대상이 아닙니다. Hermes Native Cron은 Owner 전용 검증일 뿐이며 Grok CLI와 Codex CLI가 기본 경로입니다. 현재 에이전트가 Hermes라면 설치나 등록을 시작하지 말고 지원되는 에이전트로 돌아가세요.
- 저장소 clone 금지. PyPI, uvx, pip install proactive-mcp 금지. 관리자 권한 금지. PROACTIVE_DATABASE 설정 금지.
- wheel: %USERPROFILE%\Downloads\proactive_mcp-0.1.0-py3-none-any.whl
- 다른 채널로 받은 SHA-256과 비교하고, 다르면 설치를 멈추세요.
- OAuth JSON이 있으면 %USERPROFILE%\.proactive-mcp\client_secret.json으로 옮기세요. 없으면 내가 BYO인지 확인하고 SETUP_GOOGLE.md를 안내하세요.
- Python 3.11과 uv가 없으면 설치하세요. 전용 venv(%USERPROFILE%\venvs\proactive)에 wheel만 넣으세요.
- 이 에이전트에 등록하세요. Codex는 일상 대화에 serve만, 별도 예약 대화에 serve-scheduled만 로드하도록 실행별 enable override를 사용하세요. Grok 0.2.112라면 user scope를 쓰지 말고, 비공개인 서로 다른 일상용·예약용 디렉터리를 만든 뒤 각각 project scope로 proactive=serve 하나와 proactive_scheduled=serve-scheduled 하나만 등록하고 두 디렉터리를 신뢰 처리하세요. 두 디렉터리에서 각각 `grok mcp list`와 모든 source를 합친 `grok mcp doctor --json`을 확인하고, user/project Grok TOML, Claude JSON의 top-level 및 모든 `projects.*.mcpServers`, user/project Cursor `mcp.json`, project MCP JSON 원본까지 여섯 raw file에서 중복도 검사하며, user Grok 설정·상속된 Claude/모든 Cursor 설정·project `.mcp.json`의 기존 proactive 등록을 명시적으로 제거하거나 일상용 project scope로 옮기세요. 유효한 무관 항목은 보존하세요. 예약 디렉터리에서 full proactive를 숨길 수 없으면 Grok 예약 실행을 완료했다고 하지 말고 Codex를 예약 collector로 쓰세요. 경로는 절대 경로로 넣으세요.
- 이 호스트의 세션 시작 규칙에 다음 계약을 모두 넣고 새 일상 대화에서 실제 호출을 확인하세요. 새 대화마다 proactive_check를 한 번 호출합니다. reply_deadline은 행동 필요 판정이 아니라 보수적으로 뽑은 후보입니다. 사용자에게 말하기 전에 뉴스레터·마케팅·자동 영수증, 요청이 없는 FYI 또는 FYI-CC, 다른 사람이 맡은 스레드, 나에게 답해야 할 질문·요청·결정이 없는 행은 확신할 수 있을 때 제외합니다. 명시적인 회신·RSVP·결정 요청, 내가 책임진 마감, 나에게 직접 묻고 아직 답하지 않은 질문은 유지합니다.
- 불확실한 후보는 나에게 알리거나 lease 전체를 미확정 상태로 두거나 일상 대화에서 snooze하세요. 비실행 항목이라고 조용히 버리지 마세요. 모든 행을 검토한 뒤 확정하기로 선택할 때만, 보여 주지 않기로 확신한 후보까지 포함해 검토한 lease 전체를 receipt_token 하나로 정확히 한 번 confirm_delivery 하세요. 토큰이 없거나 결과를 받지 못했으면 확정하지 마세요. MCP 도구명·설명·필드·값은 영어로 유지하되 나에게는 내 언어로 말하세요.
- 이 호스트에 native scheduler가 있으면 그것을 쓰고, 없으면 Windows 작업 스케줄러로 현재 에이전트의 별도 예약 대화를 시작해 serve-scheduled의 proactive_check와 위의 필터 및 전체 lease 조건부 confirm_delivery를 실행하세요. Grok wrapper는 먼저 예약용 디렉터리로 이동해 여섯 raw TOML/JSON file과 경로 표기가 다른 항목까지 포함한 모든 Claude project entry 및 user/project Cursor source를 파싱하고, 이어서 `grok mcp list --json`과 all-source `grok mcp doctor --json`을 검사해야 합니다. project scope의 단일 proactive_scheduled가 `proactive-mcp serve-scheduled`로 끝나지 않거나, healthy handshake와 정확히 3 tools가 아니거나, full/중복/malformed source가 있으면 agent 호출 전에 중단해야 합니다. 스케줄 작업이 proactive-mcp CLI만 직접 실행하게 하지 말고, 에이전트가 MCP 도구를 호출하게 하세요.
- Google 읽기 전용 연동을 끝내세요. 경고 화면이 나면 내가 고급 → 계속을 누릅니다. 패키지가 정한 성공 안내가 나오는지 확인하세요.
- 실제 계정으로 메일과 일정을 한 번 읽고, 둘 다 정상인지 보여 주세요. 확인 없이 실제 계정을 읽지 마세요. 도움말에 있는 확인 플래그를 쓰세요. 이어서 감시를 한 번만 돌리고 같은 상태가 유지되는지도 보여 주세요.
- 검증이 끝나면 Windows 작업 스케줄러에 상시 감시를 로그인할 때 시작하고 실패하면 다시 시작하도록 등록한 뒤, 계속 실행 중인지 확인하세요. 실행 파일은 가상환경의 절대 경로를 쓰세요. 등록할 수 없으면 주기 sync와 OS 알림 폴백이 없다는 점을 설명하고 degraded mode 사용에 대한 내 명시적 동의를 받기 전에는 완료로 보고하지 마세요.
- 설치와 연동에 쓰는 명령 이름은 나에게 말하지 마세요. 결과와 막힌 지점만 말하세요.
- 메일 제목·본문·주소, 일정 제목, 토큰, JSON, 스크린샷, status 전체는 출력하거나 이슈에 붙이지 마세요.
- 다 되면 get_status로 database.path와 두 Google status만 말하세요. proactive_check가 receipt_token을 반환하고 확정하기로 선택했다면 위 계약에 따라 검토한 lease 전체를 정확히 한 번 confirm_delivery 하세요.
```

## 성공 기준

- `database.status=healthy`, `migration_version=10`이면 됩니다.
- Gmail과 Calendar 상태가 모두 `ok`입니다.
- 세션 시작 규칙과 예약 전달 작업에서 에이전트가 `proactive_check`를 실제 호출하고, `receipt_token`이 있고 검토 뒤 확정하기로 선택할 때만 `confirm_delivery`를 한 번 호출합니다.
- continuous watcher가 등록되어 실행 중입니다. 등록하지 못한 경우에는 주기 sync와 OS 알림 폴백이 없다는 설명을 듣고 degraded mode에 명시적으로 동의해야 합니다.
- `overall=degraded`는 continuous watcher를 등록하지 못해 degraded mode에 명시적으로 동의한 경우에만 허용됩니다.
- Google 범위는 `gmail.readonly`와 `calendar.readonly`만 사용합니다.

## 보고

막힌 단계, 설치에 걸린 시간, 세션 시작 규칙·예약 전달·continuous watcher를 포함한 성공 또는 실패만 알려 주세요. 상태값은 `overall`, `database.status`, `migration_version`, `google.gmail.status`, `google.gmail.error_code`, `google.calendar.status`, `google.calendar.error_code`, 경고 문자열만 보내세요. 메일이나 일정 내용, PII, OAuth JSON, 토큰, 스크린샷, `status` 전체는 보내지 마세요.

## 되돌리기 프롬프트

제거가 필요하면 에이전트에게 다음과 같이 요청하세요. "먼저 proactive-mcp 자격 증명을 삭제하고 성공을 확인해 주세요. 그다음 이 에이전트의 MCP 등록과 상태 폴더를 정리해 주세요. 자격 증명 삭제가 실패하면 상태 폴더는 지우지 말고 알려 주세요."
