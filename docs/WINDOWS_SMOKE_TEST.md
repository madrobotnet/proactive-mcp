# Windows Owner smoke test

This is an Owner-only low-level diagnostic sheet. Named alpha testers use
[`docs/testers/windows.md`](testers/windows.md). Use synthetic data unless a
section explicitly requires Owner-approved live access. Never attach PII, OAuth
material, databases, host configuration, raw logs, screenshots, or MCP payloads.

## Architecture boundary

proactive-mcp is agent-dependent. It never launches Grok, Codex, Hermes, another
host agent, a model, or a conversation, and it never sends prompts. The daemon
performs local sync/evaluation/queue work and the documented critical-only OS
fallback. `serve-scheduled` is only a restricted stdio MCP server surface.
Neither process initiates an agent delivery.

Everyday and restricted profiles must never be loaded in the same host
conversation. The host/operator owns this isolation and the agent lifecycle;
proactive-mcp does not inspect or verify host configuration. A host-native
scheduler may start a dedicated agent run only if that host/version provides a
dedicated per-run MCP profile containing only `serve-scheduled`. Otherwise leave
automated scheduling unconfigured. Manual restricted usage remains possible.

Grok 0.2.112 cannot prove immutable per-run isolation across merged configuration
sources, so unattended Grok scheduling is unsupported. Codex config layers are
likewise not claimed isolated by this plugin. Hermes Native Cron is Owner-only
and host-owned. Do not create gate-and-launch wrappers, counter-owned child
launchers, auto selectors, or fallback host proposals.

## Preparation

Use PowerShell as a normal user. Confirm the exact checkout and Python floor:

```powershell
Set-Location C:\Users\you\src\proactive-mcp
git status --short
git rev-parse HEAD
uv run python --version
uv run proactive-mcp --help
```

The expected CLI includes `serve`, `serve-scheduled`, `status`, `setup`,
`disconnect`, `google-smoke`, `daemon`, and `service`. The expected migration is
10. `status` may be degraded before Google setup or while the daemon is stopped.

## Everyday host connectivity

Configure one fresh interactive host conversation with only the full `serve`
registration. Do not load `serve-scheduled`. Ask the running host to call
`get_status`; verify database health and migration 10. Then verify one
`proactive_check` under the delivery contract:

- `reply_deadline` is a conservative candidate, not an action verdict.
- Filter newsletters, marketing, automated receipts, no-ask FYI/FYI-CC,
  someone else's threads, and rows with no request, question, or decision for
  this user when confident.
- Keep explicit reply/RSVP/decision requests, user-owned deadlines, and
  unanswered questions directed to the user.
- Surface uncertainty, leave the whole lease unconfirmed, or snooze from the
  everyday profile; never silently discard uncertainty.
- Confirm the whole reviewed lease exactly once only after receipt and review,
  including confidently filtered rows. Do not confirm without a token.
- Keep MCP fields in English and speak the user's language. Never claim all-clear
  while a source is stale or failed.

## Manual restricted connectivity

End the everyday conversation. In a separate host profile, configure only the
absolute command ending in `proactive-mcp serve-scheduled`. Start a fresh manual
conversation and verify exactly these tools: `get_status`, `proactive_check`,
and `confirm_delivery`. Do not infer that this manual result proves future
automated per-run isolation.

For Grok 0.2.112 this is manual only. For Codex, the operator must inspect the
effective host profile outside proactive-mcp; this document does not claim its
config layers are immutable. Do not put both profiles in one conversation for a
convenience check.

## Daemon watcher

The one-shot local watcher command is:

```powershell
uv run proactive-mcp daemon --once
uv run proactive-mcp status
```

A continuous Windows task may run the absolute `proactive-mcp.exe daemon`
command. Its action must not invoke a model CLI or contain a prompt. Without the
daemon, explicit tool calls may lazy-sync; periodic sync and OS fallback are
unavailable.

## Agent-off check

Follow the M5 section below with an isolated synthetic database. Starting only
the daemon or restricted MCP must create no host agent/model/conversation and
must leave pending state available for a later explicit MCP call.

## M2.5 메모리 모델 v2 (Issue #13), historical record

> **Historical, Cursor-era. Do not use as instructions.** This section is kept
> verbatim as the acceptance evidence for M2.5, captured when Cursor was still a
> supported client. Cursor left the supported set on 2026-08-22
> ([#20](https://github.com/madrobotnet/proactive-mcp/issues/20)), so its
> Agent-mode wording, `mcp.json` steps, and "Cursor shows a ... tool call" checks
> describe what was run then, not what to run now. The tool names, argument
> names, JSON fields, and expected values are retained as historical evidence.
> For a current manual rerun, use one host conversation containing only the
> everyday `serve` profile; do not infer scheduled-profile isolation from it.

Use Agent mode every time. A reply with no tool call is a fail, even if the text sounds right. Write down the numeric memory `id` values the tools return. Later scenarios compare those ids.

Argument names below are the MCP schema names. For `update` and `forget` the id field is `id`, not `memory_id`.

| Tool | Arguments |
|---|---|
| `remember` | `kind`, `content`, `entity`, `entity_kind`, `entity_path`, `attribute`, `date_anchor`, `recurrence`, `lead_days` |
| `recall` | `query`, `kind`, `entity_kind`, `path_prefix`, `limit` |
| `update` | `id`, `kind`, `content`, `entity`, `entity_kind`, `entity_path`, `attribute`, `date_anchor`, `recurrence`, `lead_days` |
| `list_entities` | `kind`, `path_prefix` |
| `forget` | `id` |

`remember` / `update` / `recall` item JSON uses: `id`, `kind`, `entity_id`, `entity`, `entity_kind`, `entity_path`, `attribute`, `content`, `date_anchor`, `recurrence`, `lead_days`, `source`, `created_at`, `updated_at`, `archived`, `is_contradictory`. `recall` wraps those in `{"items": [...]}`. `list_entities` returns `{"items": [...]}` where each entity has `id`, `kind`, `path`, `label`, `status`, `created_at`, `updated_at`. `forget` returns only `id` and `archived` (`true`).

If you already ran the M1.5 smoke, quit Cursor fully, then clear the default DB so leftover `mother` / `person_fact` rows don't blur the counts:

```powershell
$dir = Join-Path $env:USERPROFILE ".proactive-mcp"
Remove-Item -Force -ErrorAction SilentlyContinue @(
  (Join-Path $dir "proactive.db"),
  (Join-Path $dir "proactive.db-wal"),
  (Join-Path $dir "proactive.db-shm")
)
```

Reopen Cursor, refresh MCP, and rerun the step 8 `get_status` check. `migration_version` should still be `4`.

### M2.5 Scenario 1: alias save (`엄마`) and recall (`어머니`)

Save under alias `엄마` with canonical path leaf `어머니`. Then, in a new chat, recall `어머니`.

New Agent chat. Paste:

```
Use the remember tool now with these exact arguments:
kind=fact
entity=엄마
entity_kind=person
entity_path=가족/어머니
attribute=birthday
content=엄마 생신
date_anchor=--07-18
recurrence=yearly
Don't skip the tool call.
```

Success:

- Cursor shows a `remember` tool call
- Result JSON has `"kind": "fact"`, `"entity": "엄마"`, `"entity_kind": "person"`, `"entity_path": "가족/어머니"`, `"attribute": "birthday"`, `"content": "엄마 생신"`, `"date_anchor": "--07-18"`, `"recurrence": "yearly"`, `"archived": false`, `"is_contradictory": false`
- `"id"` is a positive integer. Record it as `MEMORY_ID`.
- `"entity_id"` is a positive integer. Record it as `ENTITY_ID`.

Failure:

- No `remember` call
- Tool error (including any `person_fact` rejection)
- Different `kind` / `entity` / `entity_kind` / `entity_path` / `attribute` / `content` / `date_anchor` / `recurrence`
- `"archived": true`

Close that chat. Open a **new** Agent chat. Don't continue the old thread. Paste:

```
Use the recall tool with query 어머니. Don't answer from chat history. Call the tool.
```

Success:

- You see a `recall` tool call
- JSON `items` has an entry whose `id` is `MEMORY_ID`
- That entry's `content` is `엄마 생신`, `entity` is `엄마`, `entity_path` is `가족/어머니`, `date_anchor` is `--07-18`
- If this DB only has this smoke data, `items` has exactly one entry

Failure:

- No `recall` call (the model recites the old chat instead)
- `"items": []`
- The mother row is missing, or `id` / `content` / `entity_path` don't match

### M2.5 Scenario 2: duplicate re-save, same id

Same dated fact again. The tool must return the existing row, not a new one. You prove that with the returned `id`, not with SQL.

New Agent chat. Paste:

```
Use the remember tool now with these exact arguments:
kind=fact
entity=엄마
entity_kind=person
entity_path=가족/어머니
attribute=birthday
content=엄마 생신
date_anchor=--07-18
recurrence=yearly
Don't skip the tool call.
```

Success:

- Cursor shows a `remember` tool call
- Result `"id"` equals `MEMORY_ID` from scenario 1
- `"entity_id"` equals `ENTITY_ID`
- `"entity_path"` is still `"가족/어머니"`
- `"archived": false`

Failure:

- No `remember` call
- Tool error
- A new `"id"` (row grew)
- `"entity_id"` changed

Optional check in the same chat:

```
Use the recall tool with query 엄마 and kind=fact. Call the tool.
```

Success: among `items`, there is still one row with `id=MEMORY_ID` and `date_anchor=--07-18`. You should not see a second active birthday row for that date.

### M2.5 Scenario 3: cross-kind path-prefix query

`path_prefix` matches across entity kinds. `가족력` must not match the `가족` prefix.

New Agent chat. Paste:

```
Use the remember tool now with these exact arguments:
kind=note
entity=프로젝트
entity_kind=activity
entity_path=가족/프로젝트
content=프로젝트 메모
Don't skip the tool call.
Then use remember again with these exact arguments:
kind=note
entity=가족력
entity_kind=thing
entity_path=가족력
content=제외되는 메모
Don't skip that second tool call either.
```

Write down the `프로젝트` remember `id` as `PROJECT_ID`, and the `가족력` remember `id` as `THING_ID`.

Same chat is fine. Paste:

```
Use the recall tool now with these exact arguments:
query=""
path_prefix=가족
query is the empty string. Keep query present. Leave kind, entity_kind, and limit unset.
Don't skip the tool call.
```

Success:

- Two `remember` calls, then a `recall` call
- The first remember JSON has `"entity": "프로젝트"`, `"entity_kind": "activity"`, `"entity_path": "가족/프로젝트"`
- The second remember JSON has `"entity": "가족력"`, `"entity_kind": "thing"`, `"entity_path": "가족력"`
- `recall` JSON `items` includes `MEMORY_ID` (`entity_kind` `person`, `entity_path` `가족/어머니`) and `PROJECT_ID` (`entity_kind` `activity`, `entity_path` `가족/프로젝트`)
- `items` does not include `THING_ID` and has no row with `"entity_path": "가족력"`
- The two matching rows have different `entity_kind` values (`person` and `activity`)

Failure:

- Any of the three tools skipped
- `path_prefix` omitted, or `query` omitted
- `가족력` appears in the recall `items`
- Only one kind comes back (for example only `person`)

### M2.5 Scenario 4: `update`

`update` keeps the same memory `id` and replaces the mutable fields. Pass the full payload. This is not a partial patch.

New Agent chat. Paste:

```
Use the remember tool now with these exact arguments:
kind=commitment
entity=proactive
entity_kind=activity
entity_path=개발/proactive-mcp
attribute=deadline
content=M2.5 완료
date_anchor=2026-08-21
Don't skip the tool call.
```

Record that `"id"` as `COMMIT_ID`. Then paste, replacing `COMMIT_ID` with the number:

```
Use the update tool now with these exact arguments:
id=COMMIT_ID
kind=commitment
entity=proactive
entity_kind=activity
entity_path=개발/proactive-mcp
attribute=deadline
content=M2.5 검토
date_anchor=2026-08-22
The id argument name is id, not memory_id. Don't skip the tool call.
```

Success:

- `remember` JSON has `"kind": "commitment"`, `"entity": "proactive"`, `"entity_kind": "activity"`, `"entity_path": "개발/proactive-mcp"`, `"attribute": "deadline"`, `"content": "M2.5 완료"`, `"date_anchor": "2026-08-21"`, `"archived": false`
- `update` JSON has the same `"id"` as `COMMIT_ID`
- After update, `"content"` is `"M2.5 검토"` and `"date_anchor"` is `"2026-08-22"`
- `"entity_path"` is still `"개발/proactive-mcp"`
- `"archived": false`

Failure:

- No `update` call, or the call uses `memory_id` and errors
- `"id"` changed
- `content` or `date_anchor` unchanged
- Entity fields dropped (`entity` null, `attribute` back to `free`)

### M2.5 Scenario 5: `list_entities`

The scenario 4 chat is fine. Paste:

```
Use the list_entities tool with these exact arguments:
kind=activity
path_prefix=개발
Don't skip the tool call.
```

Success:

- Cursor shows a `list_entities` tool call
- JSON has `items`
- An entry has `"kind": "activity"`, `"path": "개발/proactive-mcp"`, `"label": "proactive"`, `"status": "active"`
- No entry has `"path": "가족/프로젝트"` (that activity is outside prefix `개발`)
- If this DB only has this smoke data, `items` has exactly one entry

Failure:

- No `list_entities` call
- `"items": []`
- The `개발/proactive-mcp` entity is missing
- `가족/프로젝트` or `가족/어머니` appears under prefix `개발`

### M2.5 Scenario 6: `forget`, then re-recall (item absent)

Archive one synthetic row, then prove a fresh `recall` does not return it. `forget` is a soft archive: it keeps the same `id` and returns `"archived": true`. `recall` lists active rows only.

New Agent chat. Paste:

```
Use the remember tool now with these exact arguments:
kind=note
entity=스모크
entity_kind=thing
entity_path=테스트/스모크
content=forget-smoke-item
Don't skip the tool call.
```

Record that `"id"` as `FORGET_ID`. Then paste, replacing `FORGET_ID` with the number:

```
Use the forget tool now with these exact arguments:
id=FORGET_ID
The id argument name is id, not memory_id. Don't skip the tool call.
```

Success:

- Cursor shows a `remember` tool call, then a `forget` tool call
- `remember` JSON has `"kind": "note"`, `"entity": "스모크"`, `"entity_kind": "thing"`, `"entity_path": "테스트/스모크"`, `"content": "forget-smoke-item"`, `"archived": false`
- `"id"` is a positive integer. That value is `FORGET_ID`.
- `forget` JSON has `"id"` equal to `FORGET_ID` and `"archived": true`
- Those are the only `forget` fields (`id` and `archived`)

Failure:

- Either tool skipped
- The `forget` call uses `memory_id` and errors
- Tool error
- `forget` JSON `"archived"` is not `true`, or `"id"` is not `FORGET_ID`

Close that chat. Open a **new** Agent chat. Don't continue the old thread. Paste:

```
Use the recall tool with query forget-smoke-item. Don't answer from chat history. Call the tool.
```

Success:

- You see a `recall` tool call
- JSON `items` has no entry whose `id` is `FORGET_ID`
- If this DB only has this smoke data, `items` is `[]`

Failure:

- No `recall` call (the model recites the old chat instead)
- `FORGET_ID` is still in `items`
- A row with `"content": "forget-smoke-item"` comes back

## 시나리오 목록 (storage)

### Scenario: file at the documented path

PowerShell:

```powershell
$db = Join-Path $env:USERPROFILE ".proactive-mcp\proactive.db"
Get-Item $db | Format-List FullName, Length, LastWriteTime
```

Success:

- `FullName` is `C:\Users\<you>\.proactive-mcp\proactive.db`
- `Length` is greater than 0

Failure: file not found, or a different directory (for example a repo-local `proactive.db`).

## 확인 포인트

Confirm the file you just used, then confirm the protected DACL by eye and in PowerShell.

### Explorer

1. Win+R, paste `%USERPROFILE%\.proactive-mcp`, Enter.
2. You should see `proactive.db`. You may also see `proactive.db-wal`, `proactive.db-shm`, and `.proactive.db.init.lock`.
3. Right-click `proactive.db` > Properties / 속성 > Security / 보안.
4. The name list should be your Windows account. `Everyone`, `Users`, and `Authenticated Users` should not be there.
5. Advanced / 고급: inheritance should be disabled (상속 사용 안 함). Entries should be explicit, not inherited.
6. Repeat steps 3 to 5 on the `.proactive-mcp` folder.

Folder ACEs may show object/container inherit flags. That's expected on the directory. Inherited `(I)` entries are not.

### PowerShell ACL

```powershell
$dir = Join-Path $env:USERPROFILE ".proactive-mcp"
$db = Join-Path $dir "proactive.db"

Write-Output "=== paths ==="
Get-Item $dir, $db | Format-List FullName, Length, LastWriteTime

Write-Output "=== icacls ==="
icacls $dir
icacls $db

Write-Output "=== Get-Acl ==="
foreach ($p in @($dir, $db)) {
  $acl = Get-Acl $p
  Write-Output "Path=$($acl.Path)"
  Write-Output "Owner=$($acl.Owner)"
  Write-Output "AreAccessRulesProtected=$($acl.AreAccessRulesProtected)"
  $acl.Access |
    Format-Table IdentityReference, FileSystemRights, AccessControlType, IsInherited -AutoSize
}
```

Success:

- `AreAccessRulesProtected` is `True` on the directory and on `proactive.db` (protected DACL, inheritance blocked)
- `IsInherited` is `False` on every listed ACE
- The only Allow principal is your current user
- Rights are FullControl, or an equivalent that includes read+write (Windows may print `FullControl`)
- `icacls` on the file looks like `HOST\you:(F)` with no `(I)`
- `icacls` on the directory looks like `HOST\you:(OI)(CI)(F)` or `HOST\you:(F)`, still with no `(I)`

Failure: `AreAccessRulesProtected` is `False`, inherited ACEs, or extra principals such as `BUILTIN\Users` / `Everyone`.

If WAL/SHM/lock files exist, `icacls` on those should also be current-user only. Missing sidecars are fine.

## 문제 발생 시

Comment on the M2.5 PR or on Issue #13. Saying "it failed" with no structure is not enough.

Smoke data in this file is synthetic on purpose. Keep it that way. Don't save real people, birthdays, mail, or calendar events.

Do not attach `proactive.db`, `proactive.db-wal`, `proactive.db-shm`, or `.proactive.db.init.lock`. GitHub comments must not include memory `content`, entity labels, entity paths, or dates. MCP JSON that carries `content`, `entity`, `entity_path`, `path`, or `date_anchor` stays off the issue. Tokens, credentials, mailbox data, calendar data, and other personal data stay off GitHub too.

Paste only redacted structure:

1. Synthetic scenario number (`1` alias, `2` duplicate, `3` path-prefix, `4` update, `5` list_entities, `6` forget/re-recall, storage path, or ACL)
2. Command exit code for any PowerShell step that failed
3. Tool name (`get_status`, `remember`, `recall`, `update`, `list_entities`, or `forget`)
4. Counts: how many tool calls you saw, and `items` length when the tool returns `items`
5. Non-sensitive integer ids only (`MEMORY_ID`, `ENTITY_ID`, `PROJECT_ID`, `THING_ID`, `COMMIT_ID`, `FORGET_ID`)
6. Sanitized error code or message, with content, entity, path, date, tokens, and credentials stripped
7. Versions:

```powershell
uv --version
uv run python --version
$PSVersionTable.PSVersion
[System.Environment]::OSVersion.VersionString
Get-Command uv | Format-List Source, Version
git -C "$env:USERPROFILE\src\proactive-mcp" rev-parse --abbrev-ref HEAD
git -C "$env:USERPROFILE\src\proactive-mcp" rev-parse HEAD
```

8. From `get_status` or `uv run proactive-mcp status`: exit code, `database.status`, `database.migration_version`, `database.journal_mode`, `overall`. Redact `database.path` to `.proactive-mcp\proactive.db`. Skip the rest of that JSON.
9. ACL failures only: `AreAccessRulesProtected` (`True`/`False`), whether extra principals showed up, and `icacls` with the account name replaced by `<you>`
10. One sanitized MCP error line from the CLI that failed, from `grok mcp doctor proactive` or `codex mcp get proactive`, with usernames in paths replaced by `<you>` and no tool result payloads. Grok's raw stderr logs under `%USERPROFILE%\.grok\logs\mcp\` are for your eyes only: read them, retype one line, attach nothing.

Screenshots of the Agent chat are not helpful here. They usually show memory content.

If every scenario passed, comment that M2.5 scenarios 1 to 6 passed (alias, duplicate id, path-prefix, update, list_entities, forget/re-recall) plus the storage path and ACL checks. Give `migration_version` and a redacted `icacls` line such as `HOST\<you>:(F)`. Leave the DB and the memory JSON off the comment. That is enough success evidence.

## M4 전달, historical record

> **Historical, Cursor-era. Do not use as instructions.** Same standing as the
> M2.5 section above: this is the M4 delivery evidence as captured, before the
> 2026-08-22 platform decision ([#20](https://github.com/madrobotnet/proactive-mcp/issues/20))
> removed Cursor. Cursor and `mcp.json` references below are historical and have
> no prescribed current host equivalent. Migration version 7, the tool list,
> and checkpoint values stand only as evidence of that release. Current
> architecture validation lives in
> [M5 agent-owned scheduling contract](#m5-agent-owned-scheduling-contract).

Watcher daemon, Cursor situation tools, degraded no-daemon check, shared SQLite, and one-shot WinRT fallback. Finish the opening 준비 절차 (uv, clone, `mcp.json`) first. Keep the default DB. Do not set `PROACTIVE_DATABASE`. Do not run `setup` or `google-smoke`. All memories stay synthetic. Do not paste real names, dates, mail, credentials, DB files, or `evidence`.

CLI help advertises `daemon --once` and `--poll-interval-minutes`. Tools: `proactive_check`, `list_situations(state?)`, `get_situation(id)`, `acknowledge_situation(id)`, `snooze_situation(id, until)`, `mute_situation(id, scope instance|type)`. Config keys: `[daemon] poll_interval_minutes`, `[fallback] priorities` / `wait_minutes`. Opening the DB on this branch applies **migration 7**.

### M4 준비

Quit Cursor. From the clone:

```powershell
Set-Location "$env:USERPROFILE\src\proactive-mcp"
git fetch origin
git checkout feat/m4-delivery
uv sync --locked

$dir = Join-Path $env:USERPROFILE ".proactive-mcp"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText((Join-Path $dir "config.toml"), @"
[daemon]
poll_interval_minutes = 1
[fallback]
priorities = ["high"]
wait_minutes = 1
[attention]
quiet_hours_start = "00:00"
quiet_hours_end = "00:00"
"@, $utf8)

uv run proactive-mcp status
Write-Output "exit=$LASTEXITCODE"
Write-Output "ANCHOR=$((Get-Date).Date.AddDays(3).ToString('--MM-dd'))"
```

Use `feat/m4-delivery` while the M4 PR is open; after merge, `main`. Equal quiet-hour bounds turn off the default 21:00–07:00 hold so `high` `personal_occasion` can deliver during this smoke.

Checkpoint: exit 0; `database.status` is `healthy`; `database.journal_mode` is `wal`; **`database.migration_version` is `7`**; `overall` is `degraded`; `google.gmail.status` and `google.calendar.status` are `not_configured`; `daemon.status` is `not_running`. Version `4` is M2.5 code. `degraded` is expected.

Reopen Cursor on the clone and refresh MCP. `proactive` must list `get_status`, `proactive_check`, `list_situations`, `get_situation`, `acknowledge_situation`, `snooze_situation`, `mute_situation`, `remember`, `recall`, `update`, `list_entities`, `forget`.

New Agent chat: call `get_status`. Same checkpoint. Redact `database.path` to `.proactive-mcp\proactive.db`.

| Tool | Arguments |
|---|---|
| `proactive_check` | none |
| `list_situations` | optional `state`: `pending`, `delivered`, `acknowledged`, `snoozed`, `muted`, `resolved`, `expired` |
| `get_situation` | `id` |
| `acknowledge_situation` | `id` |
| `snooze_situation` | `id`, `until` (ISO-8601 with a UTC offset, in the future) |
| `mute_situation` | `id`, `scope` = `instance` or `type` (default `instance`) |

Do not call `scope=type` here. That mutes every `personal_occasion` and blocks scenario 7.

### M4 Scenario 1: `daemon --once`

```powershell
Set-Location "$env:USERPROFILE\src\proactive-mcp"
uv run proactive-mcp daemon --once
Write-Output "exit=$LASTEXITCODE"
uv run proactive-mcp status
```

Checkpoint: exit 0. Once-JSON `gmail`, `calendar`, and `sources` are `not_configured`; `warning_count` > 0; `notifications` is 0. Status: `daemon.status=not_running`, `daemon.liveness=stopped`, `daemon.cycle_count=1`, `migration_version=7`. No traceback.

Failure: non-zero exit, hang, traceback, or `migration_version` not 7.

### M4 Scenario 2: continuous daemon

Window A (leave running):

```powershell
Set-Location "$env:USERPROFILE\src\proactive-mcp"
uv run proactive-mcp daemon --poll-interval-minutes 1
```

Window B:

```powershell
uv run proactive-mcp status
```

Checkpoint: `daemon.status=running`, `daemon.liveness=running`, `pid` set, `cycle_count` >= 1, `heartbeat_at` set. Ctrl+C in A. Status then: `status=not_running`, `liveness=stopped`.

Failure: immediate exit, liveness stays `never_started`, or a lock error (see scenario 5).

### M4 Scenario 3: Cursor `proactive_check` / list / get / acknowledge / snooze / mute

```powershell
(Get-Date).ToUniversalTime().AddHours(2).ToString("yyyy-MM-ddTHH:mm:ss+00:00")
```

New Agent chat. Substitute prep `ANCHOR` and that `until`. Do not paste `evidence`, `title`, or `why_now`.

```
Call these tools in order. Don't skip. Don't invent argument names.

1. remember kind=fact entity=스모크A entity_kind=person entity_path=테스트/스모크A attribute=birthday content=스모크 기념 A date_anchor=ANCHOR recurrence=yearly lead_days=7
2. proactive_check
3. list_situations with no state
4. list_situations state=delivered
5. get_situation id=<id from step 2>
6. acknowledge_situation id=<that id>
7. remember kind=fact entity=스모크B entity_kind=person entity_path=테스트/스모크B attribute=birthday content=스모크 기념 B date_anchor=ANCHOR recurrence=yearly lead_days=7
8. proactive_check
9. snooze_situation id=<new id> until=<PowerShell until>
10. remember kind=fact entity=스모크C entity_kind=person entity_path=테스트/스모크C attribute=birthday content=스모크 기념 C date_anchor=ANCHOR recurrence=yearly lead_days=7
11. proactive_check
12. mute_situation id=<new id> scope=instance
```

Checkpoint: three `remember`, three `proactive_check`, both lists, `get`, `acknowledge`, `snooze`, `mute`. Each check: one `personal_occasion`, `priority=high`, `state=delivered`, `all_clear=false`. After 6: `acknowledged`. After 9: `snoozed` with `snoozed_until` set. After 12: `muted`, `scope=instance`, `muted_types=[]`. Report `items` length only.

Failure: skipped tool, `all_clear=true` with Google unset, ack/snooze/mute on `pending`, `scope=type`, or `until` without an offset.

### M4 Scenario 4: degraded no-daemon check

Daemon stopped (`liveness` is `stopped` or `never_started`). New Agent chat:

```
Call proactive_check. Don't guess.
```

Checkpoint: the tool runs with no daemon. `all_clear=false`. `freshness.gmail.status` and `freshness.calendar.status` are `not_configured`. `warnings` is non-empty.

Failure: tool missing, hang, or `all_clear=true`.

### M4 Scenario 5: simultaneous daemon + Cursor SQLite

Start scenario 2 window A again. New Agent chat:

```
Call get_status, then list_situations with no state. Don't guess.
```

Window B: `uv run proactive-mcp status`

Checkpoint: both sides return. Cursor `get_status` shows `daemon.status=running`, `database.journal_mode=wal`, `migration_version=7`. `list_situations` returns `items` (no error). CLI exit 0. No `SQLITE_BUSY`.

Ctrl+C the daemon.

Failure: red MCP, locked DB, or one side errors while the other is open.

### M4 Scenario 6: real Windows WinRT toast

Run scenario 7, then confirm the toast here.

Checkpoint: one Action Center toast from WinRT `ToastNotificationManager` (not a MessageBox). The OS payload is a fixed PII-free label: `Reply needed`, `Calendar conflict`, or `Upcoming personal occasion`. Mother's Birthday is `Upcoming personal occasion`. No mail, calendar text, names, dates, or `evidence`. A later `--once` must not send a second toast.

Failure: no toast, console-only text, two toasts, or any payload that is not one of those three labels.

### M4 Scenario 7: shortened fallback trigger

Config must already have `[fallback] priorities = ["high"]` and `wait_minutes = 1`. Create a synthetic Mother's Birthday. Run one daemon evaluation. **Leave the situation pending — do not `proactive_check`.** Cross the 1-minute boundary. Run another pass. Expect one toast and no resend.

If `fallback.sent` is already > 0, use a different synthetic entity than `엄마` and repeat.

```powershell
Set-Location "$env:USERPROFILE\src\proactive-mcp"
Write-Output "ANCHOR=$((Get-Date).Date.AddDays(3).ToString('--MM-dd'))"
```

New Agent chat. Remember only:

```
Use remember with kind=fact entity=엄마 entity_kind=person entity_path=가족/어머니 attribute=birthday content=엄마 생신 date_anchor=ANCHOR recurrence=yearly lead_days=7
Don't call proactive_check.
```

Record the memory `id` only. Then:

```powershell
uv run proactive-mcp daemon --once
Write-Output "pass1=$LASTEXITCODE"
```

List only (same or new chat):

```
Call list_situations state=pending. Do not call proactive_check.
```

Checkpoint after pass 1: exit 0; `created` is 1 if this occasion is new (0 if it already existed); `notifications=0`. List has one `personal_occasion`, `state=pending`, `priority=high`. No toast yet.

```powershell
Start-Sleep -Seconds 70
uv run proactive-mcp daemon --once
Write-Output "pass2=$LASTEXITCODE"
uv run proactive-mcp daemon --once
Write-Output "pass3=$LASTEXITCODE"
uv run proactive-mcp status
```

Checkpoint after the wait: pass 2 `notifications=1` and one toast (scenario 6). Pass 3 `notifications=0`. Status `fallback.sent=1`, `fallback.failed=0`, `fallback.claimed=0`, `failure_codes=[]`, `migration_version=7`. A later `list_situations state=pending` still shows it (`pending`; fallback does not deliver).

Failure: toast on pass 1, `notifications=0` on pass 2, resend on pass 3, any `proactive_check`, `fallback.failed>0`, or a prior `scope=type` mute.

### M4 cleanup

Ctrl+C any daemon. Do not attach `proactive.db`, `-wal`, `-shm`, `.proactive.db.init.lock`, `config.toml`, or credential files.

Optional reset (quit Cursor first):

```powershell
$dir = Join-Path $env:USERPROFILE ".proactive-mcp"
Remove-Item -Force -ErrorAction SilentlyContinue @(
  (Join-Path $dir "proactive.db"),
  (Join-Path $dir "proactive.db-wal"),
  (Join-Path $dir "proactive.db-shm")
)
```

### M4 문제 발생 시

Comment on the M4 PR. No DB files, no memory/situation `content`, no entity labels, no `evidence`, no mail, no credentials, no real names or dates.

Collect:

1. Scenario number (prep, 1 `--once`, 2 continuous, 3 Cursor tools, 4 no-daemon, 5 simultaneous, 6 WinRT toast, 7 fallback)
2. Commands and each exit code
3. `generated_at` (when you captured this dump, ISO)
4. PowerShell version and Windows build:

```powershell
$PSVersionTable.PSVersion
[System.Environment]::OSVersion.VersionString
uv --version
git -C "$env:USERPROFILE\src\proactive-mcp" rev-parse --abbrev-ref HEAD
git -C "$env:USERPROFILE\src\proactive-mcp" rev-parse HEAD
```

5. From `uv run proactive-mcp status` or `get_status`: `overall`, `database.status`, `database.journal_mode`, `database.migration_version`, `database.busy_timeout`, `google.gmail.status`, `google.calendar.status`, `google.*.error_code`, `daemon.status`, `daemon.liveness`, `daemon.cycle_count`, `fallback.claimed`, `fallback.sent`, `fallback.failed`, `fallback.failure_codes`, `budget.used`, `budget.remaining`, `budget.daily_budget`, `warnings`. Redact `database.path` to `.proactive-mcp\proactive.db`. Never include memory rows or situation `evidence` / `title` / `why_now`.
6. From `--once`: exit code plus `created`, `notifications`, `gmail`, `calendar`, `sources`, `warning_count`
7. Tool names and counts only (`items` length, `situations` length, `held_count`, `all_clear`, `state`, `priority`, `situation_type`, `scope`, `muted_types`)
8. Redacted `icacls` on `%USERPROFILE%\.proactive-mcp` and `proactive.db` (`HOST\<you>:(F)`, no username)

If every M4 scenario passed: scenarios 1 to 7 passed, `migration_version=7`, `fallback.sent=1` after scenario 7, one WinRT toast, no resend. Leave the DB and evidence off the comment.

## M5 agent-owned scheduling contract

This section supersedes the historical M5 host-launch wrappers. Do not recreate
them. proactive-mcp is agent-dependent and never starts Grok, Codex, Hermes,
another host agent, a model, or a conversation. It never sends a prompt.

`serve-scheduled` is only a restricted stdio MCP server exposing `get_status`,
`proactive_check`, and `confirm_delivery`. Starting that process alone does not
call a tool or deliver a situation. The daemon is a separate local background
process: it may sync, evaluate, maintain the queue, and perform the documented
critical-only OS fallback, but it never invokes an agent/LLM.

### Required host boundary

- Everyday conversations load only `serve`.
- A separate manual or scheduled conversation loads only `serve-scheduled`.
- The host/operator owns profile isolation and the agent lifecycle. The plugin
  neither verifies host config nor launches the host.
- A host-native scheduler or Windows Task Scheduler may start a dedicated agent
  run only when the host provides an immutable dedicated per-run MCP profile
  containing only `serve-scheduled`.
- If the installed host cannot provide that profile, automated scheduled use is
  unsupported and must fail closed by leaving the task unregistered. Manual
  restricted use remains possible.
- Grok 0.2.112 cannot prove immutable per-run isolation across merged Grok,
  Claude, Cursor, and project sources. Do not test or advertise unattended Grok
  scheduling.
- Codex configuration layers and command-line overrides are not claimed isolated
  by proactive-mcp. Do not schedule Codex unless the host/operator establishes
  the dedicated profile independently of the plugin.
- Hermes Native Cron remains Owner-only and host-owned. It is not a tester path
  and no proactive-mcp wrapper, adapter, package, handshake, or session tracker
  is permitted.

There is no automatic host selector or fallback host. Never diff
`deliveries.total` around a child model process, parse model output, scan merged
host config as a pre-launch gate, or emit a notification based on a wrapper-owned
agent run. Pending situations persist until a running host agent explicitly calls
the MCP tools.

### Windows owner smoke

Use only synthetic state. Do not set up or read a live Google account for this
architecture check.

1. Create a synthetic pending situation using the existing fixture procedure
   from M4. Do not call `proactive_check`.
2. Start the proactive-mcp daemon watcher task, if desired, and separately start
   `serve-scheduled` from a mock MCP host process. Do not start Grok, Codex,
   Hermes, or any model.
3. Inspect processes and the synthetic database. No host agent/model/conversation
   process may have appeared, and the situation must still be pending. An OS
   fallback toast, when its documented critical policy is deliberately exercised,
   is not an agent delivery and must not change that state.
4. Later connect the mock host to a fresh `serve-scheduled` process and explicitly
   call `proactive_check`. The queued situation must be returned with a non-null
   receipt token and remain pending under an active lease.
5. Apply the candidate filter, uncertainty, English-MCP/user-language, and
   whole-lease rules. Only after the mock host has received and reviewed every row
   may it conditionally call `confirm_delivery` exactly once.
6. Confirm that the scheduled surface exposed exactly the three restricted tools
   and that neither `serve` nor both profiles were loaded in that conversation.

Pass means agent-off produced no host process or conversation, pending state was
preserved, and a later explicit MCP call returned the queued state. A reassuring
model sentence is not evidence.

### Closed-alpha tester path

Named testers use [`docs/testers/windows.md`](testers/windows.md). The tester
sheet installs an everyday profile and the local daemon watcher. Optional
automated host scheduling is not an onboarding requirement and is configured
only where the host independently guarantees a dedicated per-run MCP profile.
The complete cross-platform ownership contract is in
[`docs/INTEGRATIONS.md`](INTEGRATIONS.md).

### Evidence and privacy

Record only the revision, tool names/counts, redacted states and counters,
whether a host process appeared while agent-off, and whether the later explicit
call returned a lease. Do not attach host configuration, agent transcripts,
MCP payload content, situation evidence, databases, OAuth material, PII, raw
logs, screenshots, or process command lines containing private paths.
