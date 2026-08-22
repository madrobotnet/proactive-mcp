# Windows Owner smoke test

Run this on Windows with PowerShell and Cursor. Paste the commands as written. Don't install from PyPI: this repo is private and `proactive-mcp` is not published. `uvx proactive-mcp` and `pip install proactive-mcp` are the wrong path.

macOS is CI-only (`macos-latest` green). There is no Owner Mac, so don't run these steps on macOS. Real-device Mac coverage waits for a closed-alpha tester who has one.

Do not set `PROACTIVE_DATABASE`. The point of this smoke is the default file under `%USERPROFILE%\.proactive-mcp\proactive.db`.

Prep, default-path, and ACL checks still apply. The memory pass is **M2.5** (Issue #13). Do not paste the old `kind=person_fact` prompts. That kind is gone. Current kinds are `fact`, `commitment`, `preference`, and `note`. All smoke data must stay synthetic. Don't replace the copy-paste payloads with real names, dates, mail, or calendar facts.

## 준비 절차

### What you need

- Windows 10 or 11
- PowerShell 5.1 or 7
- Git, and a GitHub account that can read private `madrobotnet/proactive-mcp`
- Cursor, with Agent mode (Ask mode can't call MCP tools)

### 1. Install uv

In PowerShell:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
irm https://astral.sh/uv/install.ps1 | iex
$env:Path = "$env:USERPROFILE\.local\bin;$env:USERPROFILE\.cargo\bin;$env:Path"
uv --version
```

Success: a uv version string prints.

Failure: `uv` is not recognized. Copy the installer output and your `$env:Path`.

If this PowerShell window was already open before the install, the `Path` line above is required. Cursor may still miss `uv` until you use the full `uv.exe` path in `mcp.json` (step 6 does that).

### 2. Clone or update the private repo

Fresh clone:

```powershell
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\src" | Out-Null
Set-Location "$env:USERPROFILE\src"
git clone https://github.com/madrobotnet/proactive-mcp.git
Set-Location proactive-mcp
git checkout feat/m2-5-memory-model-v2
```

If you already cloned for an older smoke:

```powershell
Set-Location "$env:USERPROFILE\src\proactive-mcp"
git fetch origin
git checkout feat/m2-5-memory-model-v2
```

Use `feat/m2-5-memory-model-v2` while the M2.5 PR is open. After merge, check out `main` instead. If the review comment names another branch or SHA, use that.

Do not check out `m1-5-cross-platform-storage`. That branch is historical and does not have the M2.5 tools.

Success: `git status` shows the branch, and `pyproject.toml` is in the current directory.

Failure: auth error on clone. Sign in with a GitHub account that has access (`gh auth login`, or Git Credential Manager), then retry the clone. If `git` itself is missing, `winget install --id Git.Git -e --source winget`, open a new PowerShell, and start this step again.

### 3. Install the package

From the repo root:

```powershell
uv python install 3.11
uv sync --locked
```

Success: the command exits 0.

Failure: paste the full stderr. Don't drop `--locked` to "make it work."

### 4. CLI status

```powershell
uv run proactive-mcp status
```

Success, all of these:

- Exit code 0
- JSON `database.status` is `"healthy"`
- JSON `database.path` is your `%USERPROFILE%\.proactive-mcp\proactive.db` (backslashes are fine)
- JSON `database.journal_mode` is `wal`
- JSON `database.migration_version` is `4`
- JSON `overall` is `"degraded"`
- JSON `google.gmail.status` is `"not_configured"`
- JSON `google.calendar.status` is `"not_configured"`
- JSON `daemon.status` is `"not_running"`

`degraded` is expected. This smoke does not run Google setup. Treat these warnings as pass, not fail:

- `"Google Gmail is not configured; run proactive-mcp setup."`
- `"Google Calendar is not configured; run proactive-mcp setup."`
- `"Daemon is not running; status is degraded."`

Do not run `proactive-mcp setup` or `proactive-mcp google-smoke` here.

Failure: non-zero exit, no JSON, `database.status` not `healthy`, `migration_version` not `4`, or `path` pointing somewhere else. Version `3` means you are not on M2.5 code.

### 5. Print the Cursor MCP snippet

```powershell
$repo = (Join-Path $env:USERPROFILE "src\proactive-mcp") -replace '\\','/'
$uv = ((Get-Command uv).Source) -replace '\\','/'
Write-Output "uv: $uv"
Write-Output "repo: $repo"
Write-Output @"
{
  "mcpServers": {
    "proactive": {
      "command": "$uv",
      "args": [
        "run",
        "--directory",
        "$repo",
        "proactive-mcp",
        "serve"
      ]
    }
  }
}
"@
```

Keep that JSON. You'll paste it next.

### 6. Write Cursor `mcp.json`

User-level config path: `%USERPROFILE%\.cursor\mcp.json`

```powershell
$mcpDir = Join-Path $env:USERPROFILE ".cursor"
$mcp = Join-Path $mcpDir "mcp.json"
New-Item -ItemType Directory -Force -Path $mcpDir | Out-Null
Write-Output $mcp
if (Test-Path $mcp) {
  Write-Output "File exists. Add the proactive entry inside mcpServers. Don't delete other servers."
} else {
  Write-Output "File does not exist. Save the JSON from step 5 as this file."
}
notepad $mcp
```

If Notepad asks to create the file, say yes. Paste the JSON from step 5 when the file is new. When the file already has `mcpServers`, copy only the `"proactive": { ... }` object into that object.

Save and close Notepad.

### 7. Reload Cursor MCP

1. In Cursor, open the cloned folder: `%USERPROFILE%\src\proactive-mcp`
2. Go to Cursor Settings, then MCP.
3. Refresh the server list. `proactive` should appear.
4. If it stays red, fully quit Cursor and reopen it (Windows GUI apps often miss a brand-new `uv` PATH).

Success: `proactive` is enabled and lists `get_status`, `remember`, `recall`, `update`, `list_entities`, `forget`.

Failure: red server, zero tools, or spawn error. Copy the MCP error text. Confirm `command` in `mcp.json` is the `uv.exe` path from step 5. If `update` or `list_entities` is missing, you are still on pre-M2.5 code. Recheck the branch in step 2, run `uv sync --locked` again, then refresh MCP.

### 8. Agent mode connectivity check

Start a **new** Agent chat (not Ask). Paste:

```
Call the get_status tool from the proactive MCP server. Don't guess the result.
```

Success: you see a `get_status` tool call, `database.status` is `"healthy"`, `database.migration_version` is `4`, and `path` ends with `.proactive-mcp\proactive.db`.

Failure: the model answers without a tool call, the tool errors, or tools are missing. Stop here. Don't continue the memory scenarios until this passes.

## M2.5 메모리 모델 v2 (Issue #13)

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
10. Cursor Settings > MCP error text, with usernames in paths replaced by `<you>` and with no tool result payloads

Screenshots of the Agent chat are not helpful here. They usually show memory content.

If every scenario passed, comment that M2.5 scenarios 1 to 6 passed (alias, duplicate id, path-prefix, update, list_entities, forget/re-recall) plus the storage path and ACL checks. Give `migration_version` and a redacted `icacls` line such as `HOST\<you>:(F)`. Leave the DB and the memory JSON off the comment. That is enough success evidence.

## M4 전달

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
