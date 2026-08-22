## Owner 재검증 결과 (수정 커밋 ade2443, 925009f)

**시나리오 6·7 재검증 통과 — merge block 해제합니다.** ✅

### 환경
- Windows 11, 수정본 체크아웃 후 `uv sync --locked`
- `uv run pytest`: **328 passed, 12 skipped** / `uv run ruff check .`: **clean**
- CI: ubuntu / windows / macos 전부 green

### 토스트 수정 (925009f) 검증
- `windows_toast.ps1`이 검증된 방식 그대로 반영됨: AUMID 명시 + `$ErrorActionPreference='Stop'` + try/catch + exit 0/1 (fail-loud)
- `test_os_fallback_security.py` 회귀 테스트가 AUMID 사용·파라미터 없는 `CreateToastNotifier()` 금지·fail-loud 패턴을 모두 고정함

### 시나리오 7 재실행 (신규 엔티티 "스모크D", `--08-25` yearly, lead 7일)
| 단계 | 결과 |
|---|---|
| pass1 (`daemon --once`) | created=1, notifications=0 — 대기 시간 미충족, 정상 보류 |
| 70초 후 pass2 | **notifications=1 — 실제 토스트 화면 표시 확인** |
| pass3 | notifications=0 — 일회성 원장에 의해 재발송 없음 |
| status | `fallback.sent` 1→**2**, `failed` 0, `failure_codes` [] |

토스트 표시는 알림 센터 이력으로도 교차 확인: 해당 AUMID(`…\WindowsPowerShell\v1.0\powershell.exe`) 채널에 이력 1건 존재 (직전에 알림 센터를 전부 비운 상태였음). 이전 라운드의 "exit 0인데 미표시" 무음 실패는 재현되지 않음.

### v8 runtime ownership (ade2443) 검증
- 기존 DB에 v8 마이그레이션 정상 적용 (`migration_version: 8`), 기존 데이터·fallback 원장 보존
- 상주 데몬 기동 → status `running/running` + heartbeat/cycle 기록 확인
- Ctrl+C → **우아한 종료 (exit code 0)** → status `not_running/stopped`
- owner_token 기반 데몬 소유권 + lazy sync 싱글턴 lease는 리뷰에서 우려한 다중 프로세스 경합을 구조적으로 막는 올바른 방향

M4 완료 기준 충족으로 판단합니다. 머지 진행해 주세요.
