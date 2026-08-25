# macOS 클로즈드 알파 테스터 시트

## 1. 받은 것

Owner에게 wheel, 다른 인증 채널의 SHA-256, 필요하면 OAuth JSON을 각각 받으세요. 알파에서는 PyPI, `uvx`, 저장소 clone을 사용하지 않습니다.

## 2. 에이전트에게 붙여 넣기

아래 블록 전체를 지금 쓰는 에이전트에 붙여 넣으세요. Gatekeeper나 알림 권한 창이 나오면 에이전트가 우회하지 말고 창의 문구와 선택지를 보여 주세요. 선택은 직접 하시면 됩니다.

```text
이 macOS에 proactive-mcp 클로즈드 알파를 설치하고, 지금 이 에이전트에 MCP로 등록해 주세요. 제가 명령을 외우거나 mcp add 명령과 설정 파일 편집을 직접 하지 않게 해 주세요.

규칙:
- Hermes Agent는 테스터 지원 대상이 아닙니다. Hermes Native Cron은 Owner 전용 검증일 뿐이며 Grok CLI와 Codex CLI가 기본 경로입니다. 현재 에이전트가 Hermes라면 설치나 등록을 시작하지 말고 지원되는 에이전트로 돌아가세요.
- 저장소 clone, PyPI, uvx, pip install proactive-mcp, 관리자 권한 사용, PROACTIVE_DATABASE 설정은 금지입니다.
- wheel은 ~/Downloads/proactive_mcp-0.1.0-py3-none-any.whl에 있습니다. shasum -a 256으로 다른 채널에서 받은 SHA-256과 비교하고, 다르면 설치를 멈추세요.
- Python 3.11과 uv가 없으면 설치하세요. ~/venvs/proactive 전용 venv를 만들고 wheel만 설치하세요.
- OAuth JSON이 있으면 ~/.proactive-mcp를 mode 0700으로 만들고 client_secret.json을 mode 0600으로 설치한 뒤 권한을 확인하세요. 기존 파일을 단순히 mv해서 넓은 권한을 보존하지 마세요. 없으면 제가 BYO인지 확인한 뒤 docs/SETUP_GOOGLE.md를 안내하세요.
- 이 에이전트에 MCP를 등록하세요. Codex는 일상 대화에 serve만, 별도 예약 대화에 serve-scheduled만 로드하도록 실행별 enable override를 사용하세요. Grok 0.2.112라면 user scope를 쓰지 말고, 비공개인 서로 다른 일상용·예약용 디렉터리를 만든 뒤 각각 project scope로 proactive=serve 하나와 proactive_scheduled=serve-scheduled 하나만 등록하고 두 디렉터리를 신뢰 처리하세요. 두 디렉터리에서 각각 `grok mcp list`와 모든 source를 합친 `grok mcp doctor --json`을 확인하고, user/project Grok TOML, Claude JSON의 top-level 및 모든 `projects.*.mcpServers`, project MCP JSON 원본에서 중복도 검사하며, user Grok 설정·상속된 Claude 설정·project `.mcp.json`의 기존 proactive 등록을 명시적으로 제거하거나 일상용 project scope로 옮기세요. 예약 디렉터리에서 full proactive를 숨길 수 없으면 Grok 예약 실행을 완료했다고 하지 말고 Codex를 예약 collector로 쓰세요. 등록에는 ~/를 쓰지 말고 /Users/내-사용자명/venvs/proactive/bin/proactive-mcp 같은 절대 경로를 쓰세요.
- 이 호스트의 세션 시작 규칙에 다음 계약을 모두 넣고 새 일상 대화에서 실제 호출을 확인하세요. 새 대화마다 proactive_check를 한 번 호출합니다. reply_deadline은 행동 필요 판정이 아니라 보수적으로 뽑은 후보입니다. 사용자에게 말하기 전에 뉴스레터·마케팅·자동 영수증, 요청이 없는 FYI 또는 FYI-CC, 다른 사람이 맡은 스레드, 저에게 답해야 할 질문·요청·결정이 없는 행은 확신할 수 있을 때 제외합니다. 명시적인 회신·RSVP·결정 요청, 제가 책임진 마감, 저에게 직접 묻고 아직 답하지 않은 질문은 유지합니다.
- 불확실한 후보는 저에게 알리거나 lease 전체를 미확정 상태로 두거나 일상 대화에서 snooze하세요. 비실행 항목이라고 조용히 버리지 마세요. 모든 행을 검토한 뒤 확정하기로 선택할 때만, 보여 주지 않기로 확신한 후보까지 포함해 검토한 lease 전체를 receipt_token 하나로 정확히 한 번 confirm_delivery 하세요. 토큰이 없거나 결과를 받지 못했으면 확정하지 마세요. MCP 도구명·설명·필드·값은 영어로 유지하되 저에게는 제 언어로 말하세요.
- 이 호스트에 native scheduler가 있으면 그것을 쓰고, 없으면 cron으로 현재 에이전트의 별도 예약 대화를 시작해 serve-scheduled의 proactive_check와 위의 필터 및 전체 lease 조건부 confirm_delivery를 실행하세요. Grok wrapper는 먼저 예약용 디렉터리로 이동해 네 raw TOML/JSON source와 경로 표기가 다른 항목까지 포함한 모든 Claude project entry를 파싱하고, 이어서 `grok mcp list --json`과 all-source `grok mcp doctor --json`을 검사해야 합니다. project scope의 단일 proactive_scheduled가 `proactive-mcp serve-scheduled`로 끝나지 않거나, healthy handshake와 정확히 3 tools가 아니거나, full/중복/malformed source가 있으면 agent 호출 전에 중단해야 합니다. cron이 proactive-mcp CLI만 직접 실행하게 하지 말고, 에이전트가 MCP 도구를 호출하게 하세요.
- Google 읽기 전용 연동을 끝내세요. 경고 화면이 나오면 제가 고급, 계속을 누릅니다. Gatekeeper나 알림 권한 창은 자동으로 우회하거나 허용하지 말고, 표시된 문구와 선택지를 저에게 보여 주세요. 패키지가 정한 성공 안내가 나오는지 확인하세요.
- 실제 계정으로 메일과 일정을 한 번 읽고, 둘 다 정상인지 보여 주세요. 확인 없이 실제 계정을 읽지 마세요. 도움말에 있는 확인 플래그를 쓰세요. 이어서 감시를 한 번만 돌리세요.
- 검증이 끝나면 LaunchAgent로 상시 감시를 로그인할 때 시작하고 실패하면 다시 시작하도록 등록한 뒤, 계속 실행 중인지 확인하세요. 실행 파일은 가상환경의 절대 경로를 쓰세요. 등록할 수 없으면 주기 sync와 OS 알림 폴백이 없다는 점을 설명하고 degraded mode 사용에 대한 제 명시적 동의를 받기 전에는 완료로 보고하지 마세요.
- 상태에서 database.status=healthy, migration_version=10, gmail과 calendar가 ok인지 보여 주세요. overall=degraded는 continuous watcher를 등록하지 못한 이유와 제한을 설명하고 제가 degraded mode에 명시적으로 동의한 경우에만 허용됩니다.
- 설치와 연동에 쓰는 명령 이름은 저에게 말하지 마세요. 결과와 막힌 지점만 말하세요.
- 메일 제목, 본문, 주소, 일정 제목, 토큰, OAuth JSON, 스크린샷, status 전체를 출력하거나 이슈에 붙이지 마세요.
- 완료 후 get_status로 database.path와 두 Google status만 말하세요. proactive_check가 receipt_token을 반환하고 확정하기로 선택했다면 위 계약에 따라 검토한 lease 전체를 정확히 한 번 confirm_delivery 하세요.
```

## 3. 성공 기준

`database.status=healthy`, `migration_version=10`, `google.gmail.status=ok`, `google.calendar.status=ok`이면 성공입니다. Google 권한은 읽기 전용 `gmail.readonly`와 `calendar.readonly`만 요청합니다. 세션 시작 규칙과 예약 전달 작업에서 에이전트가 `proactive_check`를 실제 호출하고, `receipt_token`이 있고 검토 뒤 확정하기로 선택할 때만 `confirm_delivery`를 한 번 호출해야 합니다. continuous watcher도 등록되어 실행 중이어야 합니다. 등록하지 못한 경우에는 주기 sync와 OS 알림 폴백이 없다는 설명을 듣고 degraded mode에 명시적으로 동의해야 하며, 그때만 `overall=degraded`를 허용합니다.

## 4. 보고

막힌 단계, 설치에 걸린 시간, 세션 시작 규칙·예약 전달·continuous watcher를 포함한 성공 또는 실패를 알려 주세요. 상태 정보는 `overall`, `database.status`, `migration_version`, `google.gmail.status`, `google.gmail.error_code`, `google.calendar.status`, `google.calendar.error_code`, 경고 문자열만 보내세요.

## 5. 되돌리기

되돌릴 때도 에이전트에게 맡기세요. 먼저 proactive-mcp 자격 증명을 지우고 성공을 확인하도록 요청하세요. 성공한 뒤에만 MCP 등록과 스케줄 작업을 지우고 `~/.proactive-mcp/`와 `~/venvs/proactive/`를 지우도록 하세요. 자격 증명 삭제가 실패하면 상태 폴더는 그대로 두고 Google 계정 권한을 취소한 뒤 Owner에게 알려 주세요.
