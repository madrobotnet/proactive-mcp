## M4 Windows Owner 스모크 결과: 시나리오 1~5·7 통과, **시나리오 6 실패 (수정 필요)**

환경: Windows 11 (NT 10.0.26200), PowerShell 5.1.26100.9168, uv 0.12.5, `feat/m4-delivery` @ `adffb04`, generated_at 2026-08-22T19:26+09:00

### 코드 리뷰 (사전)
- Windows 로컬: `uv run pytest -q` **305 passed, 12 skipped**(Linux 전용 보안 테스트), `uv run ruff check .` 통과. CI 3-OS 6체크 green 확인.
- PII 경계 설계 확인: `NotificationSource` 프로토콜이 `situation_type` 단일 필드만 노출해 토스트 빌더가 title/evidence에 구조적으로 접근 불가. `test_os_fallback_security.py`의 canary·argv·주입 테스트는 PR #18 리뷰에서 요구한 경계를 충실히 고정함.
- 폴백 정책 기본값(§7·Issue #14): priorities `["critical"]`, wait 30분, 둘 다 설정 가능 — 확인.
- claim-before-send 일회성 설계, `proactive_check`의 `all_clear` 3중 조건(클레임 0 + 보류 0 + 경고 0) — 확인.

### 준비
- 기존 v4 DB(M2.5 스모크 잔재)가 첫 open에서 **migration_version=7**로 in-place 마이그레이션, 레거시 행 보존 확인.
- config.toml: `[daemon] poll_interval_minutes=1`, `[fallback] priorities=["high"] wait_minutes=1`, quiet hours 동일값(비활성).
- stdio 도구 발견: 12종 전부 노출 확인.
- **이탈 1**: 레거시 memory 행 1건(date_anchor가 스모크 당일인 commitment)이 계획 외 `personal_occasion`을 만들어 시나리오 7 기대값을 오염시키므로 시작 전 아카이브 처리함. → 문서 M4 준비 절차에 "이전 스모크의 dated 행 정리" 단계를 추가할 것을 제안.
- **이탈 2**: 시나리오 3~5·7의 채팅 단계는 Cursor UI 대신 **직접 stdio MCP 클라이언트**(동일 프로토콜·동일 서버·동일 DB)로 수행함(이 세션의 UI 자동화 승인 제약). 도구 스키마·호출 경로는 동일하며, Cursor UI 경유는 M2.5에서 검증된 바 있음.

### 시나리오 결과

| # | 시나리오 | 결과 |
|---|---|---|
| prep | status | PASS — exit 0, healthy, wal, migration 7, daemon not_running |
| 1 | `daemon --once` | PASS — exit 0, once-JSON not_configured×3, warning_count=5, notifications=0, cycle_count=1, liveness stopped |
| 2 | 상시 데몬 | PASS — running/running, pid 기록, cycle_count 증가, heartbeat 갱신. Ctrl+C 후 not_running/stopped |
| 3 | 도구 12단계 | PASS — remember×3(memory id 6·7·8), check×3 각 1건 delivered(situation id 1·2·3, 전부 personal_occasion high), budget 1→2→3/4, ack→acknowledged, snooze→snoozed(+until), mute instance→muted·muted_types=[], all_clear 내내 false |
| 4 | 데몬 없는 check | PASS — 도구 정상 동작, situations=[], all_clear=false, freshness not_configured, warnings 5건 |
| 5 | 데몬+클라이언트 동시 | PASS — 양측 동시 접근 정상, SQLITE_BUSY 없음, wal 유지 |
| 6 | 실제 WinRT 토스트 | **FAIL — 토스트 미표시 + 조용한 실패** (아래 상세) |
| 7 | 단축 폴백 트리거 | 파이프라인은 PASS — pass1 notifications=0·pending 유지, 경계 후 pass2 notifications=1, pass3 notifications=0(재발송 없음), status `fallback.sent=1 failed=0 claimed=0 failure_codes=[]`, 상황은 여전히 pending(delivered_at 없음). 단, sent=1은 아래 결함으로 **허위 성공** |

### 시나리오 6 상세: 두 가지 결함

**결함 A — 무인자 `CreateToastNotifier()`는 이 환경에서 항상 실패.**
데몬과 동일한 argv로 `windows_toast.ps1`을 직접 실행하면:

```
"CreateToastNotifier" 호출 예외: "요소가 없습니다. (HRESULT: 0x80070490)"
→ $notifier가 null → Show() 실패
```

원인: 무인자 오버로드는 호출 프로세스에 앱 identity(AppUserModelID)가 있어야 하는데, `uv → python → powershell.exe` 체인은 미등록(unpackaged)이라 항상 `0x80070490`. 이 머신(Win11 26200)에서 100% 재현.

**결함 B — 실패해도 exit 0 (조용한 실패, AGENTS.md 위반 소지).**
스크립트에 `$ErrorActionPreference='Stop'`/try-catch/exit 전파가 없어 위 오류에도 exit 0 → 러너가 성공 처리 → `fallback.sent=1` 허위 기록. 시나리오 7의 sent=1이 실제로는 이것. 사용자는 critical 폴백이 "발송됨"으로 기록됐지만 아무 알림도 못 받는 상태가 됨 — 폴백의 존재 이유를 무효화하는 결함.

**검증된 수정안 (이 머신에서 실물 확인, Owner 스크린샷 확보):**
PowerShell의 등록된 AUMID로 notifier를 생성하면 정상 표시됨 (winotify와 동일 기법):

```powershell
$ErrorActionPreference = 'Stop'
try {
    # ... 기존 template 구성 동일 ...
    $aumid = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
    $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($aumid)
    $notifier.Show([Windows.UI.Notifications.ToastNotification]::new($template))
    exit 0
} catch { exit 1 }
```

실행 결과 exit 0 + 토스트 실물 표시 확인: 헤더 "Windows PowerShell", 본문 고정 문구 `Upcoming personal occasion` / `personal_occasion` — PII 없음 (§9.2 충족).

**요청 사항:**
1. `windows_toast.ps1`에 AUMID 지정 + fail-loud(exit 1) 적용. 실패 시 러너가 `nonzero_exit`로 기록해 `fallback.failed`가 증가해야 함.
2. `test_os_fallback_security.py`류에 회귀 고정 추가: 패키징된 ps1이 (a) AUMID 인자를 사용하고 (b) `$ErrorActionPreference='Stop'`+exit 전파를 포함하는지 hermetic하게 검증 (실 WinRT는 CI 불가이므로 스크립트 내용 검증으로).
3. macOS `osascript` 경로도 동일한 "조용한 실패" 여지가 있는지 점검 (`display notification`은 identity 불요라 낮은 위험이지만 exit 전파 확인).
4. 수정 후 이 머신에서 시나리오 6·7 재검증하겠음 — 수정 커밋 푸시 후 멘션 바람.

### 저장·ACL (변동 없음, 통과)
`AreAccessRulesProtected=True`, ACE 전부 비상속, 현재 사용자 전용. icacls (redacted): 디렉터리 `HOST\<you>:(F)` + `HOST\<you>:(OI)(CI)(IO)(F)`, 파일 `HOST\<you>:(F)`.

### 결론
M4 핵심 acceptance(E2E·다중 인스턴스·degraded)는 테스트·실기기 모두 건전하고, 도구 6종·데몬·클레임 상태 머신도 실기기에서 정상. 단 **Windows 실토스트 미표시 + 허위 sent 기록은 머지 차단 사유**로 판단함. 위 수정 반영 후 재검증하겠다.
