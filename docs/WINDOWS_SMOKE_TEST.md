# Windows Owner smoke test

Run the current M5 smoke on Windows with PowerShell, Grok CLI, and Codex CLI.
The M1.5 through M4 sections below preserve historical acceptance evidence from
the client used at the time; Cursor was removed from the supported platform set
by Owner decision [#20](https://github.com/madrobotnet/proactive-mcp/issues/20).
For current support validation, start at [M5 연동 레시피 실증](#m5-연동-레시피-실증-issue-6).
Don't install from PyPI: this repo is private and `proactive-mcp` is not
published. `uvx proactive-mcp` and `pip install proactive-mcp` are the wrong
path.

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

## M5 연동 레시피 실증 (Issue #6)

M4 proved the engine can produce and deliver a situation. M5 proves an agent picks it up **first**, without you asking about it. Two things have to hold on your machine: the agent runs a local stdio server, and something triggers it (a fresh session, or a scheduler).

What you're signing off here is the Issue #6 완료 기준: "먼저 말 걸기" demonstrated on two agent platforms. Per the Owner decision in [#20](https://github.com/madrobotnet/proactive-mcp/issues/20), those two platforms are **Grok CLI and Codex CLI**, and both are required. Recipes live in [`docs/INTEGRATIONS.md`](INTEGRATIONS.md), which ships in the same PR as this section.

Both CLIs get the same treatment: a fresh interactive session has to claim its
own fresh pending situation, and a Windows Task Scheduler trigger has to claim
another one and raise a fixed, PII-free toast with nobody at the keyboard. A
state transition without that visible notification is not delivery. Four
scenarios cover the two paths, and a fifth proves a claimed situation is never
handed out twice.

Not Owner smoke targets:

- **Claude Code Desktop** is documentation only (PRODUCT_PLAN §5.3). It isn't installed on your machine and it is not part of this sign-off.
- **Hermes** Native Cron is separate Owner-only verification, not part of this smoke.
- **ChatGPT web/desktop** and **Claude cloud Routines** are out of scope until V2 HTTP transport. Don't try them.

Everything below stays synthetic. No real names, birthdays, mail, or calendar data.

**M5 runs against an isolated database, and this is the one exception to the `PROACTIVE_DATABASE` rule at the top of this document.** Every earlier section forbids setting that variable, because those sections are testing the default path. M5 is different: by now you may have real Google credentials and real mail or calendar data in `%USERPROFILE%\.proactive-mcp\proactive.db` from M2. Pointing agents and unattended scheduled tasks at that database would put real content in front of them, so M5 uses its own:

```
%USERPROFILE%\.proactive-mcp\m5-smoke\proactive.db
```

The application derives its whole state layout from the database location, so `config.toml` and the credentials directory land in `m5-smoke` too. That folder has never been through Google setup, which means Gmail and Calendar report `not_configured` and no detector can reach a real mailbox. Your production database, config, and credentials are never opened during M5 and are never modified.

Set `PROACTIVE_DATABASE` for every direct `proactive-mcp` command, and register it into both CLIs so the servers they spawn use it too. Each PowerShell window you open needs it again, since the variable dies with the window.

### M5 준비 절차

#### M5-P1. Branch and sync

Close any running daemon first.

```powershell
Set-Location "$env:USERPROFILE\src\proactive-mcp"
git fetch origin
git checkout feat/m5-integration-recipes
git pull --ff-only
uv sync --locked
git rev-parse --abbrev-ref HEAD
git rev-parse HEAD
```

Use `feat/m5-integration-recipes` while the M5 PR is open. After the PR merges, use `main` instead. If a review comment names a different branch or SHA, that comment wins.

Checkpoint: `uv sync --locked` exits 0 and the branch line prints `feat/m5-integration-recipes`.

#### M5-P2. Isolated smoke database and test-only cadence config

Create the child directory, point the environment variable at the database inside it, and write the config beside that database. Nothing here touches the parent folder's own `proactive.db` or `config.toml`.

```powershell
$smokeDir = Join-Path $env:USERPROFILE ".proactive-mcp\m5-smoke"
New-Item -ItemType Directory -Force -Path $smokeDir | Out-Null
$env:PROACTIVE_DATABASE = Join-Path $smokeDir "proactive.db"
Write-Output "PROACTIVE_DATABASE=$env:PROACTIVE_DATABASE"
```

Copy that printed absolute path. Both CLI registrations and both scheduler wrappers need it verbatim, and it must be absolute: a scheduled task starts in a different working directory than your console.

The config below is **for this smoke only**. It shortens timing and turns off the gates that would legitimately hold a situation back during a test run. It lives in `m5-smoke`, so it cannot change how your real installation behaves.

| Key | Production default | M5 test-only value | Why the test changes it |
|---|---|---|---|
| `[daemon] poll_interval_minutes` | 5 | 1 | A scheduled trigger has to see a fresh evaluation within the test window |
| `[attention] quiet_hours_start` / `quiet_hours_end` | `21:00` / `07:00` | `00:00` / `00:00` | Equal bounds disable the quiet hold so a `high` situation can deliver at any hour |
| `[attention] daily_budget` | 4 | 20 | This section delivers more than 4 situations in one day; the real budget would hold the later ones as `pending` and look like a failure |
| `[attention] cooldown_hours` | 24 | 1 | Lets you rerun a scenario the same day if you have to retry |
| `[fallback] priorities` | `["critical"]` | `["critical"]` (unchanged) | Prevent a fallback toast from competing with the fixed scheduler toast expected in scenarios 3 and 4 |
| `[fallback] wait_minutes` | 30 | 30 (unchanged) | Same reason |

```powershell
$utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText((Join-Path $smokeDir "config.toml"), @"
[daemon]
poll_interval_minutes = 1
[attention]
quiet_hours_start = "00:00"
quiet_hours_end = "00:00"
daily_budget = 20
cooldown_hours = 1
[fallback]
priorities = ["critical"]
wait_minutes = 30
"@, $utf8)

uv run proactive-mcp status
Write-Output "exit=$LASTEXITCODE"
```

Checkpoint, and the isolation gate. All of these have to hold before you go on:

- Exit 0
- **`database.path` ends with `m5-smoke\proactive.db`.** If it ends with `.proactive-mcp\proactive.db`, the variable didn't take and you are one command away from pointing an agent at your real data. Stop and set it again.
- **`google.gmail.status` is `not_configured` and `google.calendar.status` is `not_configured`.** Anything else means credentials are reachable from the smoke directory, so stop and check what you copied in there.
- `database.status` is `healthy`, `database.journal_mode` is `wal`
- **`database.migration_version` is `8`.** Version `7` means you're still on M4 code, so redo M5-P1.
- `overall` is `degraded`, `daemon.status` is `not_running`, `budget.daily_budget` is `20`

`degraded` is expected. M5 never runs Google setup, and the smoke directory has no credentials by design.

The database file appears on that first `status` call, and the application applies its protected DACL when it creates it. You verify that in the 확인 포인트 section.

#### M5-P3. Register the server with both CLIs

[`docs/INTEGRATIONS.md`](INTEGRATIONS.md) is the recipe source. Follow its Grok CLI and Codex CLI sections. The commands below are the Windows short form.

Both CLIs must expose `proactive_check`, `list_situations`, `get_situation`, `acknowledge_situation`, `snooze_situation`, `mute_situation`, and the memory tools. Register each one separately. Neither CLI is allowed to stand in for the other in this milestone.

Each registration carries `PROACTIVE_DATABASE` into the server process. Without it, the CLI spawns a server on your real default database no matter what your console environment says, because the CLI launches that process itself.

Grok CLI takes `-e KEY=value` before the `--` separator. `grok mcp add` is documented as "Add or update an MCP server", so running it again overwrites any existing `proactive` entry, which is what you want here:

```powershell
$uv = (Get-Command uv).Source
$repo = Join-Path $env:USERPROFILE "src\proactive-mcp"
grok mcp add --scope user proactive -e "PROACTIVE_DATABASE=$env:PROACTIVE_DATABASE" -- $uv run --directory $repo proactive-mcp serve
grok mcp list
grok mcp doctor proactive
```

Codex CLI takes `--env KEY=VALUE` before the `--` separator. Remove the old
entry first so the smoke-specific registration is explicit and cleanup can
restore the normal registration unambiguously:

```powershell
$uv = (Get-Command uv).Source
$repo = Join-Path $env:USERPROFILE "src\proactive-mcp"
codex mcp remove proactive 2>$null
codex mcp add proactive --env "PROACTIVE_DATABASE=$env:PROACTIVE_DATABASE" -- $uv run --directory $repo proactive-mcp serve
codex mcp list --json
```

Both flags were read from `grok mcp add --help` on `grok 0.2.112`
(`-e, --env <KEY=value>`, repeatable) and `codex mcp add --help` on
`codex-cli 0.149.0` (`--env <KEY=VALUE>`, "Only valid with stdio servers").
Both CLIs move fast. Run `grok mcp add --help`, `grok mcp doctor --help`,
`codex mcp add --help`, `codex mcp remove --help`, and `codex exec --help` on
your machine first and use your installed spelling if it differs. Record the
two version strings with your report:

```powershell
grok --version
codex --version
```

Checkpoint: `grok mcp doctor proactive` reports the server as reachable with tools listed, and `codex mcp list --json` contains a `proactive` entry pointing at your `uv` path. Both entries must show `PROACTIVE_DATABASE` set to the `m5-smoke` path.

Confirm the isolation reached the servers, not just your shell. Ask each CLI for the database path it actually sees:

```powershell
grok -p "Call get_status and report only database.path and google.gmail.status."
codex exec --ephemeral --sandbox read-only "Call get_status and report only database.path and google.gmail.status."
```

Both must answer with a path ending in `m5-smoke\proactive.db` and `not_configured`. If either one reports the parent `.proactive-mcp\proactive.db`, its registration didn't carry the variable. Fix it before running a single scenario, because that CLI is looking at your real data.

Failure: either CLI can't spawn the server. Confirm the `uv.exe` path is absolute in that CLI's config. `uv` on `PATH` for your shell doesn't mean a scheduled task can see it.

For the fresh-session scenarios, use session-scoped instructions so the smoke
does not edit the repository's operational `AGENTS.md`:

```powershell
$grokSessionRule = "At the start of this new session, call the MCP tool proactive_check exactly once before answering. Surface freshness warnings and never claim an all-clear while a source is stale."
$codexSessionRule = 'developer_instructions="At the start of this new session, call the MCP tool proactive_check exactly once before answering. Surface freshness warnings and never claim an all-clear while a source is stale."'
```

Those variables are used only for scenarios 1 and 2. Scheduled scenarios name
`proactive_check` directly in their job prompt, because a scheduled job must be
self-contained.

#### M5-P4. Fresh pending situation (no `proactive_check`)

Every claim scenario needs its own unpicked situation. Reuse would prove nothing, because a `delivered` situation is not returned again by design.

Run this before each scenario to get a unique synthetic entity and a date three days out:

```powershell
Set-Location "$env:USERPROFILE\src\proactive-mcp"
$env:PROACTIVE_DATABASE = Join-Path $env:USERPROFILE ".proactive-mcp\m5-smoke\proactive.db"
$anchor = (Get-Date).Date.AddDays(3).ToString('--MM-dd')
$tag = "M5-" + (Get-Date).ToString('HHmmss')
Write-Output "PROACTIVE_DATABASE=$env:PROACTIVE_DATABASE"
Write-Output "ANCHOR=$anchor"
Write-Output "TAG=$tag"
```

The variable line is repeated here on purpose. M5-P4 runs many times across the section, often in a window you just opened, and a missing variable would silently write the synthetic memory into your real database.

Save the memory with whichever CLI the scenario names. Substitute the printed
`TAG` and `ANCHOR`. Each scenario repeats this payload in its own CLI form, so
the plain version below is the reference wording:

```
Use the remember tool now with these exact arguments:
kind=fact
entity=스모크TAG
entity_kind=person
entity_path=테스트/스모크TAG
attribute=birthday
content=스모크 기념일
date_anchor=ANCHOR
recurrence=yearly
lead_days=7
Do not call proactive_check.
```

Then create the situation from PowerShell:

```powershell
uv run proactive-mcp daemon --once
Write-Output "exit=$LASTEXITCODE"
```

The daemon pass detects and stores the situation but never delivers it. Delivery is `proactive_check` only, so the situation sits in `pending` until an agent claims it. That gap is exactly what M5 measures.

Confirm the situation is waiting, using a CLI session and `list_situations` only. Listing does not deliver:

```
Call list_situations with state=pending. Do not call proactive_check.
```

Checkpoint: `daemon --once` exits 0 with `created` 1 and `notifications` 0. The pending list has one more `personal_occasion` than before, `priority=high`, `state=pending`. Report `items` length only, never the entries.

Failure: `created` is 0 (the anchor or `lead_days` is wrong, or you reused a `TAG`), or the list shows the situation already `delivered` (something called `proactive_check` early, so start over with a new `TAG`).

### M5 시나리오 목록

Five scenarios, and all five have to pass. Scenarios 1 and 2 are the fresh-session half, one per CLI. Scenarios 3 and 4 are the scheduled half, again one per CLI. Scenario 5 is the dedupe check.

Run M5-P4 immediately before each one, so every scenario claims a situation nobody has touched. Substitute `ANCHOR` and `TAG` with the values it printed.

#### M5 Scenario 1: Grok CLI, fresh session

Run M5-P4 first for a new `TAG`. Save the memory through Grok itself so the whole path is Grok's:

```powershell
$env:PROACTIVE_DATABASE = Join-Path $env:USERPROFILE ".proactive-mcp\m5-smoke\proactive.db"
grok -p "Use the proactive MCP remember tool with kind=fact entity=스모크TAG entity_kind=person entity_path=테스트/스모크TAG attribute=birthday content=스모크 기념일 date_anchor=ANCHOR recurrence=yearly lead_days=7. Do not call proactive_check."
uv run proactive-mcp daemon --once
Write-Output "exit=$LASTEXITCODE"
```

Now a brand-new session with the session-start convention installed per the Grok section of [`docs/INTEGRATIONS.md`](INTEGRATIONS.md):

```powershell
grok --rules $grokSessionRule -p "안녕. 오늘 뭐부터 할까?"
```

`-p` / `--single` is a single-turn prompt that prints and exits, so each invocation is a fresh session. Confirm the flag with `grok --help` on your machine.

Pass:

- Grok calls `proactive_check` before answering
- Output mentions one upcoming personal occasion it wasn't asked about
- A follow-up `grok -p "Call list_situations with state=delivered and report the items length only."` counts that situation as `delivered`

Fail:

- No tool call, or the situation stays `pending`
- Server spawn error. Rerun `grok mcp doctor proactive`. Grok writes stderr logs under `~/.grok/logs/mcp/`, which on Windows is `%USERPROFILE%\.grok\logs\mcp\`. Read them yourself and paste only the sanitized error line

#### M5 Scenario 2: Codex CLI, fresh session

Run M5-P4 first for a new `TAG`. Save the memory through Codex itself so the whole path is Codex's:

```powershell
Set-Location "$env:USERPROFILE\src\proactive-mcp"
$env:PROACTIVE_DATABASE = Join-Path $env:USERPROFILE ".proactive-mcp\m5-smoke\proactive.db"
codex exec --ephemeral --sandbox read-only "Use the proactive MCP remember tool with kind=fact entity=스모크TAG entity_kind=person entity_path=테스트/스모크TAG attribute=birthday content=스모크 기념일 date_anchor=ANCHOR recurrence=yearly lead_days=7. Do not call proactive_check."
uv run proactive-mcp daemon --once
Write-Output "exit=$LASTEXITCODE"
```

Fresh session, using the session-scoped developer instruction prepared in
M5-P3:

```powershell
codex exec -c $codexSessionRule --ephemeral --sandbox read-only "안녕. 오늘 뭐부터 할까?"
```

Each `codex exec` is its own session. If Codex refuses to run outside a git repo, add `--skip-git-repo-check`. Check `codex exec --help` for the flags your build has.

Pass:

- The transcript shows a `proactive_check` MCP call
- Codex raises the upcoming occasion unprompted
- `codex exec --ephemeral --sandbox read-only "Call list_situations with state=delivered and report only the items length."` counts it as `delivered`

Fail:

- No tool call, sandbox blocks the server spawn, or the situation stays `pending`
- `codex mcp list --json` shows no `proactive` entry

#### M5 Scenario 3: Grok CLI, Windows Task Scheduler trigger

Neither CLI has a scheduler of its own, so the OS provides one. This mirrors the Windows Task Scheduler recipe in [`docs/INTEGRATIONS.md`](INTEGRATIONS.md). Scenario 4 repeats it for Codex, and both are required.

The isolated database is what makes an unattended run safe. The task talks to `m5-smoke`, which has no credentials, so no real mail or calendar text can enter the process even though nobody is watching the screen. Your real database is not opened.

The wrapper below **never writes agent output to disk.** It asks Grok to reduce
the tool result to exactly `PROACTIVE_ATTENTION` or `PROACTIVE_NONE`, captures
that token in process memory, and translates attention into a fixed WinRT toast.
The toast contains no situation content. The task records only fixed marker
fields; proof requires both the state transition and the visible toast.

Scheduled tasks get a bare environment. They inherit nothing from your console, so the wrapper sets the database variable itself and every path is absolute:

```powershell
$repo = Join-Path $env:USERPROFILE "src\proactive-mcp"
$smokeDir = Join-Path $env:USERPROFILE ".proactive-mcp\m5-smoke"
$smokeDb = Join-Path $smokeDir "proactive.db"
$logDir = Join-Path $smokeDir "m5-logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$grok = (Get-Command grok).Source
$uv = (Get-Command uv).Source
$toastScript = (& $uv run --directory $repo python -c `
  "from proactive_mcp.delivery.notify import WINDOWS_TOAST_SCRIPT; print(WINDOWS_TOAST_SCRIPT)").Trim()
Write-Output "grok=$grok"
Write-Output "smokeDb=$smokeDb"
Write-Output "markers=$logDir"
Write-Output "toastScript=$toastScript"
```

Write the wrapper so the task has exactly one thing to run. This wrapper only ever invokes Grok, so `-p` can't reach the wrong CLI:

```powershell
$grokScript = Join-Path $logDir "m5-grok-trigger.ps1"
$utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($grokScript, @"
Set-Location "$repo"
`$env:PROACTIVE_DATABASE = "$smokeDb"
`$stamp = (Get-Date).ToString('yyyyMMdd-HHmmss')
`$marker = Join-Path "$logDir" "grok-`$stamp.marker"
`$prompt = "Call the proactive_check MCP tool exactly once and call no other tool. Your entire final response must be exactly PROACTIVE_ATTENTION if situations is non-empty or warnings is non-empty. Otherwise it must be exactly PROACTIVE_NONE. Output one token only, with no Markdown, punctuation, explanation, situation content, title, why_now, evidence, names, dates, mail, or calendar text."

function Send-FixedToast {
  param([string] `$Title, [string] `$Body)
  & powershell.exe -NoProfile -NonInteractive -File "$toastScript" `$Title `$Body *> `$null
  return (`$LASTEXITCODE -eq 0)
}

if (-not (Test-Path -LiteralPath "$toastScript" -PathType Leaf)) {
  Set-Content -Path `$marker -Value "stamp=`$stamp cli=grok result=failure reason=notifier_unavailable notify=none exit=3" -Encoding utf8
  exit 3
}

`$raw = & "$grok" -p `$prompt 2>`$null
`$code = `$LASTEXITCODE
`$token = ((`$raw | ForEach-Object { "`$_" }) -join "``n").Trim()

if (`$code -ne 0) {
  `$sent = Send-FixedToast "Proactive check failed" "Open Grok to retry or inspect proactive status."
  `$notify = if (`$sent) { "sent" } else { "failed" }
  Set-Content -Path `$marker -Value "stamp=`$stamp cli=grok result=failure reason=agent_failed notify=`$notify exit=`$code" -Encoding utf8
  exit `$code
}

if (`$token -ceq "PROACTIVE_NONE") {
  Set-Content -Path `$marker -Value "stamp=`$stamp cli=grok result=none notify=none exit=0" -Encoding utf8
  exit 0
}

if (`$token -ceq "PROACTIVE_ATTENTION") {
  `$sent = Send-FixedToast "Proactive alert" "Open Grok to review proactive status."
  if (`$sent) {
    Set-Content -Path `$marker -Value "stamp=`$stamp cli=grok result=attention notify=sent exit=0" -Encoding utf8
    exit 0
  }
  Set-Content -Path `$marker -Value "stamp=`$stamp cli=grok result=failure reason=notify_failed notify=failed exit=3" -Encoding utf8
  exit 3
}

`$sent = Send-FixedToast "Proactive check failed" "Open Grok to retry or inspect proactive status."
`$notify = if (`$sent) { "sent" } else { "failed" }
Set-Content -Path `$marker -Value "stamp=`$stamp cli=grok result=failure reason=invalid_token notify=`$notify exit=2" -Encoding utf8
exit 2
"@, $utf8)
```

That `$env:PROACTIVE_DATABASE` line is belt and braces. The Grok registration already carries the variable into the server, and this sets it for the CLI process too, so the task stays isolated even if someone later re-registers the server without `-e`.

Only stderr is discarded. Final stdout exists transiently in `$raw` solely for
exact token comparison; it is never written, echoed, or used as toast text. If a
run fails and you need the error text, rerun the same command by hand in your
own console, read it on screen, and retype one sanitized line. The wrapper
captures `$LASTEXITCODE` before any other native command.

```powershell
Register-ScheduledTask -TaskName "proactive-m5-grok" -Force -Action (
  New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$grokScript`""
) -Trigger (
  New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
    -RepetitionInterval (New-TimeSpan -Minutes 5)
) -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable)
```

Run M5-P4 for a new `TAG`. Before the trigger fires, take a baseline in a local Grok session:

```powershell
grok -p "Call list_situations with state=pending and report only the items length. Then call list_situations with state=delivered and report only the items length. Do not call proactive_check. Do not paste any item content."
```

Record the two numbers as `PENDING_BEFORE` and `DELIVERED_BEFORE`. Now wait for the trigger. Don't call `Start-ScheduledTask`. The point is that nobody touched it.

```powershell
Get-ScheduledTaskInfo -TaskName "proactive-m5-grok" |
  Format-List TaskName, LastRunTime, LastTaskResult, NextRunTime
Get-ChildItem $logDir -Filter "grok-*.marker" |
  Sort-Object LastWriteTime | Select-Object -Last 1 -ExpandProperty Name
Get-ChildItem $logDir -Filter "grok-*.marker" |
  Sort-Object LastWriteTime | Select-Object -Last 1 | Get-Content
```

The marker and fixed toast together show that the unattended delivery path ran.
Open a **separate** session after the run and read the counts again:

```powershell
grok -p "Call list_situations with state=pending and report only the items length. Then call list_situations with state=delivered and report only the items length. Do not call proactive_check. Do not paste any item content."
```

The unattended run claimed the situation if and only if pending dropped by one and delivered rose by one. That transition is only possible through `proactive_check`, so it is the evidence, and it costs you nothing on disk.

Pass:

- `LastTaskResult` is `0` and `LastRunTime` is after you planted the situation
- The newest `grok-*.marker` exists, holds one line, and says `result=attention notify=sent exit=0`
- One fixed **Proactive alert** toast appears and says to open Grok to review proactive status; it contains no situation content
- Pending is `PENDING_BEFORE - 1` and delivered is `DELIVERED_BEFORE + 1`, with nobody at the keyboard

Fail:

- Non-zero `LastTaskResult`, non-zero `exit=` in the marker, no marker at all, or `uv`/`grok`/the toast script not found
- The counts didn't move, or they moved but no fixed toast appeared
- Any file in `m5-logs` that isn't a wrapper or a one-line marker

Marker files hold only timestamp, CLI, fixed result/reason, notification status,
and exit code. Nothing else belongs in `m5-logs`.

Before scenario 4, remove the repeating Grok task so it cannot claim Codex's
fresh situation:

```powershell
Unregister-ScheduledTask -TaskName "proactive-m5-grok" -Confirm:$false -ErrorAction SilentlyContinue
Get-ScheduledTask -TaskName "proactive-m5-grok" -ErrorAction SilentlyContinue
```

The second command must print nothing.

#### M5 Scenario 4: Codex CLI, Windows Task Scheduler trigger

Same shape as scenario 3, with its own wrapper, its own task, and its own marker
prefix. It uses the same isolated database. The Grok task from scenario 3 must
already be unregistered and absent. Only the Codex task may be eligible to claim
this scenario's situation.

```powershell
$repo = Join-Path $env:USERPROFILE "src\proactive-mcp"
$smokeDir = Join-Path $env:USERPROFILE ".proactive-mcp\m5-smoke"
$smokeDb = Join-Path $smokeDir "proactive.db"
$logDir = Join-Path $smokeDir "m5-logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$codex = (Get-Command codex).Source
$uv = (Get-Command uv).Source
$toastScript = (& $uv run --directory $repo python -c `
  "from proactive_mcp.delivery.notify import WINDOWS_TOAST_SCRIPT; print(WINDOWS_TOAST_SCRIPT)").Trim()
Write-Output "codex=$codex"
Write-Output "smokeDb=$smokeDb"
Write-Output "markers=$logDir"
Write-Output "toastScript=$toastScript"
```

This wrapper only ever invokes Codex, so `-p` can't reach it by accident:

```powershell
$codexScript = Join-Path $logDir "m5-codex-trigger.ps1"
$utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($codexScript, @"
Set-Location "$repo"
`$env:PROACTIVE_DATABASE = "$smokeDb"
`$stamp = (Get-Date).ToString('yyyyMMdd-HHmmss')
`$marker = Join-Path "$logDir" "codex-`$stamp.marker"
`$prompt = "Call the proactive_check MCP tool exactly once and call no other tool. Your entire final response must be exactly PROACTIVE_ATTENTION if situations is non-empty or warnings is non-empty. Otherwise it must be exactly PROACTIVE_NONE. Output one token only, with no Markdown, punctuation, explanation, situation content, title, why_now, evidence, names, dates, mail, or calendar text."

function Send-FixedToast {
  param([string] `$Title, [string] `$Body)
  & powershell.exe -NoProfile -NonInteractive -File "$toastScript" `$Title `$Body *> `$null
  return (`$LASTEXITCODE -eq 0)
}

if (-not (Test-Path -LiteralPath "$toastScript" -PathType Leaf)) {
  Set-Content -Path `$marker -Value "stamp=`$stamp cli=codex result=failure reason=notifier_unavailable notify=none exit=3" -Encoding utf8
  exit 3
}

`$raw = & "$codex" exec --ephemeral --sandbox read-only `$prompt 2>`$null
`$code = `$LASTEXITCODE
`$token = ((`$raw | ForEach-Object { "`$_" }) -join "``n").Trim()

if (`$code -ne 0) {
  `$sent = Send-FixedToast "Proactive check failed" "Open Codex to retry or inspect proactive status."
  `$notify = if (`$sent) { "sent" } else { "failed" }
  Set-Content -Path `$marker -Value "stamp=`$stamp cli=codex result=failure reason=agent_failed notify=`$notify exit=`$code" -Encoding utf8
  exit `$code
}

if (`$token -ceq "PROACTIVE_NONE") {
  Set-Content -Path `$marker -Value "stamp=`$stamp cli=codex result=none notify=none exit=0" -Encoding utf8
  exit 0
}

if (`$token -ceq "PROACTIVE_ATTENTION") {
  `$sent = Send-FixedToast "Proactive alert" "Open Codex to review proactive status."
  if (`$sent) {
    Set-Content -Path `$marker -Value "stamp=`$stamp cli=codex result=attention notify=sent exit=0" -Encoding utf8
    exit 0
  }
  Set-Content -Path `$marker -Value "stamp=`$stamp cli=codex result=failure reason=notify_failed notify=failed exit=3" -Encoding utf8
  exit 3
}

`$sent = Send-FixedToast "Proactive check failed" "Open Codex to retry or inspect proactive status."
`$notify = if (`$sent) { "sent" } else { "failed" }
Set-Content -Path `$marker -Value "stamp=`$stamp cli=codex result=failure reason=invalid_token notify=`$notify exit=2" -Encoding utf8
exit 2
"@, $utf8)
```

Codex output follows the same two-token, in-memory-only contract. The marker
contains only fixed fields; the fixed toast is the user-visible delivery.

```powershell
Register-ScheduledTask -TaskName "proactive-m5-codex" -Force -Action (
  New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$codexScript`""
) -Trigger (
  New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
    -RepetitionInterval (New-TimeSpan -Minutes 5)
) -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable)
```

Run M5-P4 for a new `TAG`, so this scenario claims a situation scenario 3 never saw. Baseline first:

```powershell
codex exec --ephemeral --sandbox read-only "Call list_situations with state=pending and report only the items length. Then call list_situations with state=delivered and report only the items length. Do not call proactive_check. Do not paste any item content."
```

Record `PENDING_BEFORE` and `DELIVERED_BEFORE`, then wait. Don't call `Start-ScheduledTask`.

```powershell
Get-ScheduledTaskInfo -TaskName "proactive-m5-codex" |
  Format-List TaskName, LastRunTime, LastTaskResult, NextRunTime
Get-ChildItem $logDir -Filter "codex-*.marker" |
  Sort-Object LastWriteTime | Select-Object -Last 1 -ExpandProperty Name
Get-ChildItem $logDir -Filter "codex-*.marker" |
  Sort-Object LastWriteTime | Select-Object -Last 1 | Get-Content
```

Read the counts again in a separate session:

```powershell
codex exec --ephemeral --sandbox read-only "Call list_situations with state=pending and report only the items length. Then call list_situations with state=delivered and report only the items length. Do not call proactive_check. Do not paste any item content."
```

Pass:

- `LastTaskResult` is `0` and `LastRunTime` is after you planted the situation
- The newest `codex-*.marker` exists, holds one line, and says `result=attention notify=sent exit=0`
- One fixed **Proactive alert** toast appears and says to open Codex to review proactive status; it contains no situation content
- Pending is `PENDING_BEFORE - 1` and delivered is `DELIVERED_BEFORE + 1`, with nobody at the keyboard

Fail:

- Non-zero `LastTaskResult`, non-zero `exit=` in the marker, no marker at all, or `uv`/`codex`/the toast script not found
- The counts didn't move, or they moved but no fixed toast appeared
- The sandbox blocked the server spawn under the task account, which is a different account context than your console

Before scenario 5, remove the repeating Codex task and verify that neither M5
scheduler remains:

```powershell
Unregister-ScheduledTask -TaskName "proactive-m5-codex" -Confirm:$false -ErrorAction SilentlyContinue
Get-ScheduledTask -TaskName "proactive-m5-*" -ErrorAction SilentlyContinue
```

The second command must print nothing.

#### M5 Scenario 5: delivered once, not twice

This is the dedupe half of PRODUCT_PLAN §5.1, and it runs after scenarios 1 to 4. Pick either CLI and stay on it for all three commands. The Grok form is shown; for Codex, swap `grok -p` for `codex exec --ephemeral --sandbox read-only`.

Before planting the dedupe situation, defensively unregister both repeating
tasks and verify that no scheduler can claim it:

```powershell
Unregister-ScheduledTask -TaskName "proactive-m5-grok" -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "proactive-m5-codex" -Confirm:$false -ErrorAction SilentlyContinue
Get-ScheduledTask -TaskName "proactive-m5-*" -ErrorAction SilentlyContinue
```

The verification command must print nothing.

Run M5-P4 for a new `TAG`. Before claiming it:

```powershell
$env:PROACTIVE_DATABASE = Join-Path $env:USERPROFILE ".proactive-mcp\m5-smoke\proactive.db"
grok -p "Call list_situations with state=pending and report only the items length. Do not call proactive_check."
```

Record that number as `PENDING_BEFORE`. Then claim it, twice, in two separate invocations:

```powershell
grok -p "Call proactive_check. Report situation_type, priority, state, held_count, and all_clear. Do not paste title, why_now, or evidence."
grok -p "Call proactive_check. Report situation_type, priority, state, held_count, and all_clear. Do not paste title, why_now, or evidence."
```

Each invocation is its own session, so the second one has no memory of the first. Only the database can stop it handing the situation out again.

Pass:

- First `proactive_check`: the new situation is present with `state=delivered`
- Second `proactive_check`: that situation is **not** returned again
- `list_situations state=pending` now counts `PENDING_BEFORE - 1`
- `list_situations state=delivered` counts one more than before, not two
- No duplicate `personal_occasion` for the same synthetic entity anywhere in the delivered list

Fail:

- The same situation comes back on the second check
- The delivered count grows by two for one situation
- It stays `pending` after a successful `proactive_check`
- Any M5 scheduler task is still registered before the situation is planted

### M5 확인 포인트

Go through this before you write the sign-off comment. Counts and states only, no content.

1. Branch is `feat/m5-integration-recipes` (or `main` after merge), `migration_version` is `8`
2. **Every scenario ran against `m5-smoke\proactive.db`,** with `google.gmail.status` and `google.calendar.status` both `not_configured`, and your real database was never opened
3. The test-only config is the one from M5-P2, it sits in `m5-smoke`, and you know it isn't production behavior
4. Every claim used a fresh `TAG` and its own `daemon --once` pass
5. **Grok CLI passed a fresh session** (scenario 1)
6. **Codex CLI passed a fresh session** (scenario 2)
7. **Grok CLI passed a scheduled trigger** (scenario 3), including the fixed visible toast, with no human in the loop
8. **Codex CLI passed a scheduled trigger** (scenario 4), including the fixed visible toast, with no human in the loop
9. Scenario 5 passed: `pending` became `delivered`, once, with no repeat
10. `grok --version` and `codex --version` recorded, along with any flag that differed from this document
11. Artifacts sit where they're supposed to, and the DACL still holds (below)

#### M5 artifact location and DACL recheck

Everything M5 creates lives in `%USERPROFILE%\.proactive-mcp\m5-smoke`: its own `proactive.db`, the test-only `config.toml`, the `m5-logs` folder, both wrappers, and the marker files. Nothing new belongs in the parent folder. Two things to confirm here: the artifacts are where they should be, and the protected DACL covers them.

Explorer:

1. Win+R, paste `%USERPROFILE%\.proactive-mcp`, Enter.
2. Confirm the parent still holds your real `proactive.db` and `config.toml`, and that the only new item is the `m5-smoke` folder. A stray `m5-logs` here means an older wrapper wrote outside the smoke directory.
3. Open `m5-smoke`. It should hold `proactive.db`, `config.toml`, and `m5-logs`, and no credentials folder.
4. Open `m5-logs`. It should hold `m5-grok-trigger.ps1`, `m5-codex-trigger.ps1`, `grok-*.marker`, and `codex-*.marker`, and no `.log` files.
5. Right-click `m5-smoke` > Properties / 속성 > Security / 보안. The name list should be your Windows account only. `Everyone`, `Users`, and `Authenticated Users` should not be there.
6. Advanced / 고급: entries should be explicit or inherited from the protected `.proactive-mcp` parent, and no ACE should trace back above that folder.
7. Repeat steps 5 and 6 on `m5-smoke\proactive.db`, on `m5-smoke\config.toml`, and on one marker file.

PowerShell:

```powershell
$dir = Join-Path $env:USERPROFILE ".proactive-mcp"
$smokeDir = Join-Path $dir "m5-smoke"
$smokeDb = Join-Path $smokeDir "proactive.db"
$cfg = Join-Path $smokeDir "config.toml"
$logDir = Join-Path $smokeDir "m5-logs"
$marker = Get-ChildItem $logDir -Filter "*.marker" |
  Sort-Object LastWriteTime | Select-Object -Last 1 -ExpandProperty FullName

Write-Output "=== paths ==="
Get-Item $smokeDir, $smokeDb, $cfg, $logDir, $marker |
  Format-List FullName, Length, LastWriteTime

Write-Output "=== nothing new in the parent ==="
Get-ChildItem $dir | Select-Object Name, LastWriteTime

Write-Output "=== icacls ==="
icacls $smokeDir
icacls $smokeDb
icacls $cfg
icacls $marker

Write-Output "=== Get-Acl ==="
foreach ($p in @($smokeDir, $smokeDb, $cfg, $logDir, $marker)) {
  $acl = Get-Acl $p
  Write-Output "Path=$($acl.Path)"
  Write-Output "Owner=$($acl.Owner)"
  Write-Output "AreAccessRulesProtected=$($acl.AreAccessRulesProtected)"
  $acl.Access |
    Format-Table IdentityReference, FileSystemRights, AccessControlType, IsInherited -AutoSize
}
```

Success: every `FullName` is under `C:\Users\<you>\.proactive-mcp\m5-smoke`, `Owner` is your account on all of them, and no `IdentityReference` is `Everyone`, `BUILTIN\Users`, or `NT AUTHORITY\Authenticated Users`. The parent listing shows `m5-smoke` and your pre-existing files, nothing else new. Marker `Length` should be small, a hundred bytes or so. A large marker means output leaked into it.

Then rerun the storage checks in the [Explorer](#explorer) and [PowerShell ACL](#powershell-acl) sections above against the real `%USERPROFILE%\.proactive-mcp\proactive.db`, unchanged. M5 writes inside a child of that folder, so the production database DACL has to still pass afterwards, and its `LastWriteTime` should predate the smoke.

Failure: an artifact outside `m5-smoke`, a broad identity in any ACL, `AreAccessRulesProtected` false on `.proactive-mcp`, a marker file larger than a line of text, or a changed `LastWriteTime` on the real database.

**Acceptance decision.** Per [#20](https://github.com/madrobotnet/proactive-mcp/issues/20), Issue #6 is satisfied when lines 5 through 8 are all true. Both CLIs must prove a fresh session, and both must prove a scheduled trigger. One CLI doing all four is not acceptance, and two fresh sessions with no scheduled evidence is not acceptance either. State the decision in one line, for example: `M5 acceptance: Grok CLI + Codex CLI, fresh and scheduled. PASS.` Anything short of all four is a FAIL, and name which scenario fell over.

Claude Code Desktop and Hermes stay out of this decision. Their absence is not a failure.

### M5 cleanup

Do this even when everything passed. Scenarios 3 and 4 already unregister their
tasks to prevent races; the commands below are idempotent final safety cleanup.
Three things have to be undone: the repeating tasks, the pinned database in both
MCP registrations, and the smoke directory.

First the tasks:

```powershell
Unregister-ScheduledTask -TaskName "proactive-m5-grok" -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "proactive-m5-codex" -Confirm:$false -ErrorAction SilentlyContinue
Get-ScheduledTask -TaskName "proactive-m5-*" -ErrorAction SilentlyContinue
```

That last line should print nothing.

Now put both MCP registrations back on the normal default database by re-registering them with no `PROACTIVE_DATABASE`. Leaving the smoke path pinned would quietly point your everyday agents at a database you are about to delete:

```powershell
$uv = (Get-Command uv).Source
$repo = Join-Path $env:USERPROFILE "src\proactive-mcp"

grok mcp add --scope user proactive -- $uv run --directory $repo proactive-mcp serve
codex mcp remove proactive 2>$null
codex mcp add proactive -- $uv run --directory $repo proactive-mcp serve

grok mcp list
codex mcp list --json
```

Neither entry may still mention `PROACTIVE_DATABASE`. Confirm with a fresh session on each side, in a **new** PowerShell window so the variable is gone from your environment:

```powershell
grok -p "Call get_status and report only database.path."
codex exec --ephemeral --sandbox read-only "Call get_status and report only database.path."
```

Both must now report a path ending in `.proactive-mcp\proactive.db`, with no `m5-smoke` in it.

Only then delete the smoke directory. This removes the smoke database, the test-only config, both wrappers, and every marker in one go:

```powershell
$smokeDir = Join-Path $env:USERPROFILE ".proactive-mcp\m5-smoke"
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $smokeDir
Test-Path $smokeDir
Get-ChildItem (Join-Path $env:USERPROFILE ".proactive-mcp") | Select-Object Name
```

`Test-Path` must print `False`, and the listing must still show your real `proactive.db` and `config.toml`.

**Delete nothing above `m5-smoke`.** Your real `proactive.db`, its `-wal` and `-shm` sidecars, your `config.toml`, and your Google credentials all live in the parent folder and must survive untouched. If a command you are about to run names the parent directory rather than `m5-smoke`, don't run it.

Stop any daemon with Ctrl+C. Close the PowerShell windows where you set `PROACTIVE_DATABASE`, or clear it with `Remove-Item Env:\PROACTIVE_DATABASE`, so nothing later in your session inherits the smoke path. Because the test-only config lived in `m5-smoke`, your production settings were never overridden and there is nothing to restore.

### M5 문제 발생 시

Comment on Issue #6 with the M5 label. One comment per failed scenario, so each one can be fixed on its own.

Include all of this:

1. **Which scenario.** The number and name, for example `M5 Scenario 2: Codex CLI, fresh session`.
2. **The exact commands you ran,** copied from this document, with your own substitutions for `ANCHOR` and `TAG` shown as placeholders rather than real values.
3. **Exit codes.** The `exit=$LASTEXITCODE` line for every PowerShell step, and `LastTaskResult` plus the marker's `exit=` value for scenarios 3 and 4.
4. **Isolation proof.** That `database.path` ended with `m5-smoke\proactive.db` and that Gmail and Calendar were both `not_configured`. Write the path as `...\m5-smoke\proactive.db`, with your username cut off.
5. **Versions.** `grok --version`, `codex --version`, `uv --version`, and `uv run python --version`. The application revision is the Git SHA from item 7; `proactive-mcp` has no `--version` flag.
6. **OS and shell.** Windows edition and build, and `$PSVersionTable.PSVersion`.
7. **Branch and SHA.** The output of `git rev-parse --abbrev-ref HEAD` and `git rev-parse HEAD`.
8. **Redacted status fields only,** from `uv run proactive-mcp status`: `overall`, `database.status`, `database.journal_mode`, `database.migration_version`, `daemon.status`, `budget.daily_budget`, and the budget counts. Retype them. Don't paste the whole document.
9. **Tool evidence as names and numbers.** Which tools were called, how many times each, and the `items` length returned. For `proactive_check`, report `situation_type`, `priority`, `state`, `held_count`, and `all_clear`, and nothing else.
10. **Scheduler result** for scenario 3 or 4: the task name, whether the trigger fired unattended, `LastRunTime`, `NextRunTime`, `LastTaskResult`, the marker filename and fixed `result`/`reason`/`notify`/`exit` fields, and whether the fixed toast appeared. Do not include a screenshot.
11. **Registration shape, not contents.** Whether the failing CLI's entry carried `PROACTIVE_DATABASE`, as a yes or no. Never paste the registration block or the CLI config file.
12. **A sanitized error line** if a CLI failed. Rerun the command by hand, read the message on screen, and retype one line with paths shortened to `%USERPROFILE%\...` and any identifier or token replaced with `<redacted>`. For Grok, its stderr logs under `%USERPROFILE%\.grok\logs\mcp\` are for your eyes only. Read, retype the one line, attach nothing.

Never attach or paste any of the following, on any issue, in any form:

- Any `proactive.db`, smoke or real, and any `-wal`, `-shm`, or `.init.lock` sidecar
- `config.toml` from either directory, `AGENTS.md`, or any other config file, including the CLI MCP configs
- The absolute smoke path with your username in it; cut it to `...\m5-smoke\proactive.db`
- Google credentials, tokens, OAuth files, client secrets, or anything from a credentials directory
- Raw agent logs, including the Grok MCP logs and any Codex session file
- Raw stdout or stderr from Grok, Codex, or the daemon
- Situation content: `title`, `why_now`, `evidence`, `suggested_actions`, memory `content`, entity names, or dates
- Screenshots of any kind, including terminal windows and Task Scheduler windows

If you think a failure can't be explained without one of those, say so in the comment and stop. Someone will work out a safe way to get the detail. Guessing on your own is how private data ends up in a public thread.
