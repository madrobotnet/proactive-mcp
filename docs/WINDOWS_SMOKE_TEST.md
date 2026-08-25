# Windows Owner smoke test

This file separates current tester guidance from historical Owner evidence.

- **Current path:** closed-alpha testers use only
  [부록: 클로즈드 알파 테스터 경로 (M6)](#부록-클로즈드-알파-테스터-경로-m6),
  which points to the single paste block in
  [`docs/testers/windows.md`](testers/windows.md).
- **Historical record, don't run:** [준비 절차](#준비-절차) through the end of
  [M5 문제 발생 시](#m5-문제-발생-시) records completed checkout-based Owner
  smoke work. This includes M2.5, M4, and M5. The superseded Owner A1 through A8
  workflow inside the collapsed M6 details block is historical too. Those
  commands, prompts, outputs, and acceptance statements remain unchanged as
  evidence.

Grok CLI and Codex CLI remain the primary supported hosts. Cursor was removed
by Owner decision [#20](https://github.com/madrobotnet/proactive-mcp/issues/20).
Hermes Native Cron is an Owner-only validation path documented in
[`docs/INTEGRATIONS.md`](INTEGRATIONS.md), not a Windows tester path.

Don't install from PyPI: the repo is private and `proactive-mcp` is not
published (`docs/PRODUCT_PLAN.md` §12). `uvx proactive-mcp` and
`pip install proactive-mcp` are the wrong path. The wheel the Owner hands you
is not the same thing as a PyPI release.

macOS is CI-only (`macos-latest` green). There is no Owner Mac, so don't run these steps on macOS. Real-device Mac coverage waits for a closed-alpha tester who has one.

Do not set `PROACTIVE_DATABASE`. The point of this smoke is the default file under `%USERPROFILE%\.proactive-mcp\proactive.db`. M5 is the one exception, and it says so where it starts.

Every "don't run `setup`" instruction in the Owner sections is scoped to those sections, which deliberately test the unconfigured, degraded state. The tester appendix at the end is the opposite case: there, running `setup` is the job.

Prep, default-path, and ACL checks still apply. The memory pass is **M2.5** (Issue #13). Do not paste the old `kind=person_fact` prompts. That kind is gone. Current kinds are `fact`, `commitment`, `preference`, and `note`. All smoke data must stay synthetic. Don't replace the copy-paste payloads with real names, dates, mail, or calendar facts.

## 준비 절차

### What you need

- Windows 10 or 11
- PowerShell 5.1 or 7
- Git, and a GitHub account that can read private `madrobotnet/proactive-mcp`
- Grok CLI, signed in (`grok --version`, `grok login` if needed)
- Codex CLI, signed in (`codex --version`)

Both CLIs, not one. The M5 sign-off needs each of them separately, and the
earlier steps are where you find out that a registration is broken.

### 1. Install uv

In PowerShell:

```powershell
winget show --id astral-sh.uv --exact --source winget
winget install --id astral-sh.uv --exact --source winget
$env:Path = "$env:USERPROFILE\.local\bin;$env:USERPROFILE\.cargo\bin;$env:Path"
uv --version
```

The winget manifest pins and verifies the installer hash; do not pipe a mutable
web response into PowerShell. Record the installed uv version with the alpha
report. If the Owner named an approved version for this handoff, add
`--version <that-version>` to both winget commands and stop if it is
unavailable.

Success: a uv version string prints.

Failure: `uv` is not recognized. Copy the installer output and your `$env:Path`.

If this PowerShell window was already open before the install, the `Path` line above is required. A CLI that launched earlier, or a scheduled task with its own bare environment, can still miss `uv` entirely, so every registration below names the absolute `uv.exe` path instead of trusting `PATH`.

### 2. Clone or update the private repo

Fresh clone:

```powershell
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\src" | Out-Null
Set-Location "$env:USERPROFILE\src"
git clone https://github.com/madrobotnet/proactive-mcp.git
Set-Location proactive-mcp
git checkout main
git pull --ff-only
git rev-parse --short HEAD
```

If you already cloned for an older smoke:

```powershell
Set-Location "$env:USERPROFILE\src\proactive-mcp"
git fetch origin
git checkout main
git pull --ff-only
git rev-parse --short HEAD
```

Use current `main`. If the review comment you're working from names a specific SHA or a release branch, check that out instead (`git checkout <sha>`) and record the short SHA in your report. Never run a current smoke from `feat/m2-5-memory-model-v2` or `m1-5-cross-platform-storage`. Both are historical, and their database schema is many migrations behind, so every expectation below would be wrong.

Success: `git rev-parse --short HEAD` prints the SHA you meant to test.

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
- JSON `database.migration_version` is `9`
- JSON `overall` is `"degraded"`
- JSON `google.gmail.status` is `"not_configured"`
- JSON `google.calendar.status` is `"not_configured"`
- JSON `daemon.status` is `"not_running"`

`degraded` is expected. This smoke does not run Google setup. Treat these warnings as pass, not fail:

- `"Google Gmail is not configured; run proactive-mcp setup."`
- `"Google Calendar is not configured; run proactive-mcp setup."`
- `"Daemon has never run; OS notification fallback is unavailable."`

Retype the warnings you see rather than assuming these three. The daemon line reads `"Daemon is stopped; OS notification fallback is unavailable."` once a daemon has run and exited on this machine, and that's a pass too. `overall` has only two values, `ok` and `degraded`, so don't look for `healthy` there. Only `database.status` is `healthy`.

Do not run `proactive-mcp setup` or `proactive-mcp google-smoke` here.

Failure: non-zero exit, no JSON, `database.status` not `healthy`, `migration_version` not `9`, or `path` pointing somewhere else. A lower `migration_version` means you're on older code, so redo step 2 and rerun `uv sync --locked`.

### 5. Note the two absolute paths

Every registration and every scheduled wrapper needs these verbatim:

```powershell
$repo = Join-Path $env:USERPROFILE "src\proactive-mcp"
$uv = (Get-Command uv).Source
$neutral = Join-Path $env:USERPROFILE ".proactive-mcp\agent-cwd"
New-Item -ItemType Directory -Force -Path $neutral | Out-Null
Write-Output "uv=$uv"
Write-Output "repo=$repo"
Write-Output "neutral=$neutral"
Get-ChildItem $neutral
```

Success: both paths print as absolute paths, and the `neutral` listing prints nothing.

`neutral` is an empty folder that agent calls run from. Both CLIs load `AGENTS.md` from their working directory, so an agent started inside this checkout inherits the repository's development instructions and can wander off into a milestone briefing instead of doing the one thing you asked. Keep it empty: no `AGENTS.md`, no `.mcp.json`, no git repository. See [`docs/INTEGRATIONS.md`](INTEGRATIONS.md) for the longer version.

### 6. Register the server with Grok CLI

Grok takes the server command after `--`, per `grok mcp add --help`. User scope writes `%USERPROFILE%\.grok\config.toml` and applies in every folder:

```powershell
grok mcp add --scope user proactive -- $uv run --directory $repo proactive-mcp serve
grok mcp add --scope user proactive_scheduled -- $uv run --directory $repo proactive-mcp serve-scheduled
grok mcp list
grok mcp doctor proactive
grok mcp doctor proactive_scheduled
```

Success: `grok mcp list` shows both profiles. The full `proactive` doctor lists
all interactive tools; `proactive_scheduled` lists only `get_status`,
`proactive_check`, and `confirm_delivery`.

Failure: the server won't spawn. Confirm the `uv.exe` path in the entry is absolute. `uv` on your shell's `PATH` says nothing about what a GUI app or a scheduled task can find. Grok writes MCP stderr under `%USERPROFILE%\.grok\logs\mcp\`; read those yourself and retype one sanitized line if you need to report it.

Already have a `proactive` entry from your own day to day setup? `grok mcp add` overwrites it, and there's no way to read the old one back afterwards. Write down the command that recreates it, keep that note private to your machine, and restore it when you're done.

### 7. Register the server with Codex CLI

Codex uses the same `--` form. Remove any old entry first so this one is unambiguous:

```powershell
codex mcp remove proactive 2>$null
codex mcp remove proactive_scheduled 2>$null
codex mcp add proactive -- $uv run --directory $repo proactive-mcp serve
codex mcp add proactive_scheduled -- $uv run --directory $repo proactive-mcp serve-scheduled
codex mcp list --json
codex mcp get proactive
codex mcp get proactive_scheduled
```

Success: the listing has both entries using your absolute `uv.exe` path, and
`proactive_scheduled` launches `serve-scheduled`.

Codex needs one more thing. `codex exec` runs with approval policy `never`, and on codex-cli 0.149.0 an MCP tool call under that policy fails outright with `MCP tool call requires approval, but approval policy is never`. Every non-interactive Codex command in this document carries a per-server override ([openai/codex#24135](https://github.com/openai/codex/issues/24135)):

```
-c 'mcp_servers.proactive.enabled=false'
-c 'mcp_servers.proactive_scheduled.default_tools_approval_mode="approve"'
```

The full profile stays disabled for that non-interactive command, and only the
three-tool scheduled profile is approved. Drop the scheduled override and the
command fails in a way that looks like a broken registration. Never put
`approve` on the full `proactive` profile. Interactive `codex` sessions can
keep prompt approval because you're there to review each call.

Failure: `codex mcp list --json` has no `proactive` entry, or config load dies with `unknown variant`. Only `auto`, `prompt`, `writes`, and `approve` are accepted for the approval mode. Check whether `CODEX_HOME` is set, since it overrides `%USERPROFILE%\.codex`.

Record both version strings with any report you file:

```powershell
grok --version
codex --version
```

Both CLIs move fast. Run `grok mcp add --help`, `codex mcp add --help`, and `codex exec --help` on your own machine and use your installed spelling if a flag differs.

### 8. Connectivity check, one CLI at a time

Ask each CLI for `get_status` from the empty folder. Neither may stand in for the other:

```powershell
grok --cwd $neutral -p "Call get_status from proactive_scheduled and report database.status, database.migration_version, and database.path. Don't guess."
codex exec -c 'mcp_servers.proactive.enabled=false' -c 'mcp_servers.proactive_scheduled.default_tools_approval_mode="approve"' --ephemeral --sandbox read-only --skip-git-repo-check -C $neutral "Call get_status from proactive_scheduled and report database.status, database.migration_version, and database.path. Don't guess."
```

Success, from both CLIs: a visible `get_status` tool call, `database.status` is `"healthy"`, `database.migration_version` is `9`, and `path` ends with `.proactive-mcp\proactive.db`.

Failure: a CLI answers without calling the tool, the tool errors, the
scheduled tool list is anything other than the three named tools, or the
reported path is somewhere else. A `migration_version` under 9 means the CLI
is spawning older code. A missing `update` or `list_entities` from the full
Grok doctor means the same. Recheck the checkout in step 2 and run
`uv sync --locked` again. Stop here either way. Don't start the scenarios until
both CLIs pass.

`--skip-git-repo-check` is required because `neutral` isn't a git repository, and `--sandbox read-only` blocks the agent's own shell without stopping the MCP server from writing its own database.

## M2.5 메모리 모델 v2 (Issue #13), historical record

> **Historical, Cursor-era. Do not use as instructions.** This section is kept
> verbatim as the acceptance evidence for M2.5, captured when Cursor was still a
> supported client. Cursor left the supported set on 2026-08-22
> ([#20](https://github.com/madrobotnet/proactive-mcp/issues/20)), so its
> Agent-mode wording, `mcp.json` steps, and "Cursor shows a ... tool call" checks
> describe what was run then, not what to run now. The tool names, argument
> names, JSON fields, and expected values are all still current; if you're
> re-running these scenarios, drive them through Grok CLI or Codex CLI as
> registered in steps 6 and 7 and read "Cursor shows" as "the CLI transcript
> shows".

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
> removed Cursor. Wherever it says Cursor, the current equivalent is a Grok CLI
> or Codex CLI session from steps 6 through 8, and `mcp.json` becomes the
> `grok mcp add` or `codex mcp add` registration. Migration version 7, the tool
> list, and every checkpoint value stand as written. Current platform validation
> lives in [M5 연동 레시피 실증](#m5-연동-레시피-실증-issue-6).

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

The scheduled half judges delivery from the database, not from what the model
said. `proactive-mcp status` carries `deliveries.total`, a cumulative count of
immutable delivery events that only `confirm_delivery` can raise after
`proactive_check` creates a lease. The wrappers read that number before and
after the agent runs and compare the two. Agent output is discarded unread.

Not Owner smoke targets:

- **Claude Code Desktop** is documentation only (PRODUCT_PLAN §5.3). It isn't installed on your machine and it is not part of this sign-off.
- **Hermes** is excluded from closed-alpha support because live receipt confirmation was not deterministic. Do not test or configure it here.
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
git checkout main
git pull --ff-only
uv sync --locked
git rev-parse --abbrev-ref HEAD
git rev-parse --short HEAD
```

Use current `main`. M5 is merged, so there's no feature branch to chase. If a review comment names a specific SHA or a release branch, that comment wins: check it out and record the short SHA with your results.

Checkpoint: `uv sync --locked` exits 0, and the branch and SHA lines match what you meant to test.

#### M5-P2. Isolated smoke database and test-only cadence config

Create the child directory, point the environment variable at the database inside it, and write the config beside that database. Nothing here touches the parent folder's own `proactive.db` or `config.toml`.

```powershell
$repo = Join-Path $env:USERPROFILE "src\proactive-mcp"
$smokeDir = Join-Path $env:USERPROFILE ".proactive-mcp\m5-smoke"
$neutral = Join-Path $smokeDir "neutral"
New-Item -ItemType Directory -Force -Path $smokeDir | Out-Null
New-Item -ItemType Directory -Force -Path $neutral | Out-Null
$env:PROACTIVE_DATABASE = Join-Path $smokeDir "proactive.db"
Write-Output "PROACTIVE_DATABASE=$env:PROACTIVE_DATABASE"
Write-Output "NEUTRAL=$neutral"
Get-ChildItem $neutral
```

Copy both printed paths. Both CLI registrations and both scheduler wrappers need the database path verbatim, and it must be absolute: a scheduled task starts in a different working directory than your console.

`neutral` is the working directory every agent call in M5 uses, and it stays empty. That `Get-ChildItem` must print nothing, now and at the end of the smoke.

Here's why it exists. Grok and Codex both load project instructions, `AGENTS.md` above all, from their working directory. Point an agent at `%USERPROFILE%\src\proactive-mcp` and it inherits this repository's development instructions. An Owner run hit exactly that: the session called `proactive_check` and claimed its situation, then spent the whole reply on a milestone briefing and never mentioned the occasion. The situation was consumed and nothing was delivered, which is the precise failure this milestone exists to catch. An empty folder has no instructions to compete with the session rule.

So: pass `--cwd $neutral` to every Grok call, pass `-C $neutral --skip-git-repo-check` to every `codex exec` call, and never `Set-Location` to the repository before an agent call. The MCP registration is user scope and names an absolute `uv` path, so the server still starts from an empty folder. `--skip-git-repo-check` is needed because `neutral` isn't a git repository.

Plain `uv run proactive-mcp ...` commands are a different matter. They aren't agent calls, so they can run from anywhere as long as `PROACTIVE_DATABASE` points at the smoke database. This document passes `--directory $repo`, which lets `uv` find the checkout without moving your shell into it.

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
- **`database.migration_version` is `9`.** Anything lower means the checkout predates the security-hardening migration, so redo M5-P1 and run this command again.
- `overall` is `degraded`, `daemon.status` is `not_running`, `budget.daily_budget` is `20`

`degraded` is expected. M5 never runs Google setup, and the smoke directory has no credentials by design.

The database file appears on that first `status` call, and the application applies its protected DACL when it creates it. You verify that in the 확인 포인트 section.

#### M5-P3. Register the server with both CLIs

[`docs/INTEGRATIONS.md`](INTEGRATIONS.md) is the recipe source. Follow its Grok CLI and Codex CLI sections. The commands below are the Windows short form.

Both CLIs must expose `proactive_check`, `list_situations`, `get_situation`, `acknowledge_situation`, `snooze_situation`, `mute_situation`, and the memory tools. Register each one separately. Neither CLI is allowed to stand in for the other in this milestone.

Each registration carries `PROACTIVE_DATABASE` into the server process. Without it, the CLI spawns a server on your real default database no matter what your console environment says, because the CLI launches that process itself.

Stop here and record your baseline first. The next commands overwrite whatever
`proactive` entry each CLI already has, and once they run there's no way to read
the old one back. Look at both CLIs and write down, for each, whether a
`proactive` entry exists:

```powershell
grok mcp list
codex mcp list --json
```

The documented Owner baseline is that neither CLI has a `proactive` or
`proactive_scheduled` entry. If that matches what you see, note "no entry in
either CLI" and carry on.

If either CLI does have one, it belongs to your day to day setup and M5-P3 is
about to replace it. Before you go any further, write the exact commands that
recreate both profiles and keep that note private to your own machine. Your
restore sequence will first remove the smoke entries, then run those saved
commands.
Confirm you have both steps before proceeding. Cleanup at the end branches on
what you recorded here, so a missing note means you cannot put your machine
back. Never paste that command, or any part of `~/.grok` or
`%USERPROFILE%\.codex\config.toml`, into an issue, a PR, or a comment.

Grok CLI takes `-e KEY=value` before the `--` separator. `grok mcp add` is documented as "Add or update an MCP server", so running it again overwrites any existing `proactive` entry, which is what you want here:

```powershell
$uv = (Get-Command uv).Source
$repo = Join-Path $env:USERPROFILE "src\proactive-mcp"
grok mcp add --scope user proactive -e "PROACTIVE_DATABASE=$env:PROACTIVE_DATABASE" -- $uv run --directory $repo proactive-mcp serve
grok mcp add --scope user proactive_scheduled -e "PROACTIVE_DATABASE=$env:PROACTIVE_DATABASE" -- $uv run --directory $repo proactive-mcp serve-scheduled
grok mcp list
grok mcp doctor proactive
grok mcp doctor proactive_scheduled
```

Codex CLI takes `--env KEY=VALUE` before the `--` separator. Remove the old
entry first so the smoke-specific registration is explicit and cleanup can
restore the normal registration unambiguously:

```powershell
$uv = (Get-Command uv).Source
$repo = Join-Path $env:USERPROFILE "src\proactive-mcp"
codex mcp remove proactive 2>$null
codex mcp remove proactive_scheduled 2>$null
codex mcp add proactive --env "PROACTIVE_DATABASE=$env:PROACTIVE_DATABASE" -- $uv run --directory $repo proactive-mcp serve
codex mcp add proactive_scheduled --env "PROACTIVE_DATABASE=$env:PROACTIVE_DATABASE" -- $uv run --directory $repo proactive-mcp serve-scheduled
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

Checkpoint: both Grok doctors report reachable servers, and
`codex mcp list --json` contains `proactive` and `proactive_scheduled` entries
pointing at your `uv` path. All four entries must show `PROACTIVE_DATABASE`
set to the `m5-smoke` path. The scheduled entries must launch
`serve-scheduled`, not `serve`.

Codex needs one more thing before non-interactive proactive checks work.
`codex exec` runs with approval policy `never`, and on codex-cli 0.149.0 an
MCP tool call under that policy fails outright:

```
MCP tool call requires approval, but approval policy is never
```

The fix is a per-server approval mode
([openai/codex#24135](https://github.com/openai/codex/issues/24135)), but it
belongs only on the restricted scheduled profile. Every non-interactive Codex
delivery command in this document disables the full profile and carries:

```
-c 'mcp_servers.proactive.enabled=false'
-c 'mcp_servers.proactive_scheduled.default_tools_approval_mode="approve"'
```

The full `proactive` profile keeps prompt approval and is never approved for an
unattended run. The scheduled profile exposes exactly `get_status`,
`proactive_check`, and `confirm_delivery`; it cannot call memory or situation
mutation tools. Fixture creation and post-run situation listing remain
interactive Grok steps in this synthetic database.

Windows PowerShell 5.1 passes the inner quotes through to `codex` unescaped, so
the CLI may see either `="approve"` or `=approve`. Both work, because `-c`
parses the right side as TOML and falls back to the raw string. You can instead
set `default_tools_approval_mode = "approve"` under
`[mcp_servers.proactive_scheduled]` in
`%USERPROFILE%\.codex\config.toml`, but never put that value under the full
`[mcp_servers.proactive]` profile.

Confirm the isolation reached the servers, not just your shell. Ask each CLI, from the neutral folder, for the database path it actually sees:

```powershell
grok --cwd $neutral -p "Call get_status and report only database.path and google.gmail.status."
codex exec -c 'mcp_servers.proactive.enabled=false' -c 'mcp_servers.proactive_scheduled.default_tools_approval_mode="approve"' --ephemeral --sandbox read-only --skip-git-repo-check -C $neutral "Call get_status from proactive_scheduled and report only database.path and google.gmail.status."
```

Both must answer with a path ending in `m5-smoke\proactive.db` and `not_configured`. If either one reports the parent `.proactive-mcp\proactive.db`, its registration didn't carry the variable. Fix it before running a single scenario, because that CLI is looking at your real data.

Failure: either CLI can't spawn the server. Confirm the `uv.exe` path is absolute in that CLI's config. `uv` on `PATH` for your shell doesn't mean a scheduled task can see it.

For the fresh-session scenarios, use session-scoped instructions so the smoke
never edits the repository's operational `AGENTS.md`. Both variables carry the
canonical session-start rule from [`docs/INTEGRATIONS.md`](INTEGRATIONS.md),
clause for clause, flattened onto one line:

```powershell
$sessionRule = "At the start of every new session, call the MCP tool proactive_check exactly once, before you answer the user. Call it once per session and no more, unless the user explicitly asks for a fresh proactive check. If it returns a receipt_token, call confirm_delivery with that token exactly once before presenting the situations. If it returns situations, lead your reply with a short, natural summary of them. If it returns freshness warnings, say the result may be incomplete. Never report that there is nothing to report while a source is stale or failed. If it returns nothing and freshness is healthy, say nothing about it and answer the user's actual request."
$grokSessionRule = $sessionRule
$codexSessionRule = 'developer_instructions="' + ($sessionRule -replace '"', '\"') + '"'
Write-Output $grokSessionRule
Write-Output $codexSessionRule
```

Don't shorten this. An earlier Owner run used a trimmed rule that asked for the
tool call and for freshness warnings but never asked the agent to lead with a
summary of what came back. The session called `proactive_check`, claimed the
situation, then answered the greeting as if nothing had happened. Rule followed,
scenario failed. Two clauses do the work: lead the reply with a short natural
summary of returned situations, and never claim an all-clear while a source is
stale or failed. Both CLIs get the same text, so neither one is judged against a
weaker instruction than the other.

Those variables are used only for scenarios 1 and 2. Scheduled scenarios name
`proactive_check` directly in their job prompt, because a scheduled job must be
self-contained.

#### M5-P4. Fresh pending situation (no `proactive_check`)

Every claim scenario needs its own unpicked situation. Reuse would prove nothing, because a `delivered` situation is not returned again by design.

Run this before each scenario to get a unique synthetic entity and a date three days out:

```powershell
$repo = Join-Path $env:USERPROFILE "src\proactive-mcp"
$neutral = Join-Path $env:USERPROFILE ".proactive-mcp\m5-smoke\neutral"
$env:PROACTIVE_DATABASE = Join-Path $env:USERPROFILE ".proactive-mcp\m5-smoke\proactive.db"
$anchor = (Get-Date).Date.AddDays(3).ToString('--MM-dd')
$tag = "M5-" + (Get-Date).ToString('HHmmss')
Write-Output "PROACTIVE_DATABASE=$env:PROACTIVE_DATABASE"
Write-Output "ANCHOR=$anchor"
Write-Output "TAG=$tag"
Write-Output "NEUTRAL=$neutral"
```

The variable lines are repeated here on purpose. M5-P4 runs many times across the section, often in a window you just opened, and a missing variable would silently write the synthetic memory into your real database.

Save the synthetic memory through the interactive Grok profile. Fixture
creation is not the behavior under test; using Grok here keeps Codex's
non-interactive path restricted to `proactive_scheduled`. Substitute the
printed `TAG` and `ANCHOR`:

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
uv run --directory $repo proactive-mcp daemon --once
Write-Output "exit=$LASTEXITCODE"
```

The daemon pass detects and stores the situation but never delivers it.
`proactive_check` only leases the pending situation; `confirm_delivery` records
delivery after the host receives that lease. Until both calls happen, the
situation remains `pending`. That gap is exactly what M5 measures.

Confirm the situation is waiting through the interactive Grok profile and
`list_situations` only. Listing does not deliver:

```powershell
grok --cwd $neutral -p "Call list_situations with state=pending and report only the items length. Do not call proactive_check. Do not paste any item content."
```

Checkpoint: `daemon --once` exits 0 with `created` 1 and `notifications` 0. The pending list has one more `personal_occasion` than before, `priority=high`, `state=pending`. Report `items` length only, never the entries.

Failure: `created` is 0 (the anchor or `lead_days` is wrong, or you reused a
`TAG`), or the list shows the situation already `delivered` (something called
`proactive_check` and `confirm_delivery` early, so start over with a new
`TAG`).

### M5 시나리오 목록

Five scenarios, and all five have to pass. Scenarios 1 and 2 are the fresh-session half, one per CLI. Scenarios 3 and 4 are the scheduled half, again one per CLI. Scenario 5 is the dedupe check.

Run M5-P4 immediately before each one, so every scenario claims a situation nobody has touched. Substitute `ANCHOR` and `TAG` with the values it printed.

#### M5 Scenario 1: Grok CLI, fresh session

Run M5-P4 first for a new `TAG`. Save the memory through Grok itself so the whole path is Grok's:

```powershell
$env:PROACTIVE_DATABASE = Join-Path $env:USERPROFILE ".proactive-mcp\m5-smoke\proactive.db"
grok --cwd $neutral -p "Use the proactive MCP remember tool with kind=fact entity=스모크TAG entity_kind=person entity_path=테스트/스모크TAG attribute=birthday content=스모크 기념일 date_anchor=ANCHOR recurrence=yearly lead_days=7. Do not call proactive_check."
uv run --directory $repo proactive-mcp daemon --once
Write-Output "exit=$LASTEXITCODE"
```

Now a brand-new session, from the neutral folder, carrying the canonical session rule prepared in M5-P3:

```powershell
grok --cwd $neutral --rules $grokSessionRule -p "안녕. 오늘 뭐부터 할까?"
```

`-p` / `--single` is a single-turn prompt that prints and exits, so each invocation is a fresh session. `--cwd $neutral` keeps this repository's `AGENTS.md` out of it. Confirm both flags with `grok --help` on your machine.

Pass:

- Grok calls `proactive_check` before answering
- The reply leads with one upcoming personal occasion it wasn't asked about, and says the picture may be incomplete because the sources aren't configured
- A follow-up `grok --cwd $neutral -p "Call list_situations with state=delivered and report the items length only."` counts that situation as `delivered`

Fail:

- No tool call, or the situation stays `pending`
- Server spawn error. Rerun `grok mcp doctor proactive`. Grok writes stderr logs under `~/.grok/logs/mcp/`, which on Windows is `%USERPROFILE%\.grok\logs\mcp\`. Read them yourself and paste only the sanitized error line

#### M5 Scenario 2: Codex CLI, fresh session

Run M5-P4 first for a new `TAG`. Seed the synthetic input through Grok so the
Codex run can expose only the restricted scheduled tool surface:

```powershell
$env:PROACTIVE_DATABASE = Join-Path $env:USERPROFILE ".proactive-mcp\m5-smoke\proactive.db"
grok --cwd $neutral -p "Use the proactive MCP remember tool with kind=fact entity=스모크TAG entity_kind=person entity_path=테스트/스모크TAG attribute=birthday content=스모크 기념일 date_anchor=ANCHOR recurrence=yearly lead_days=7. Do not call proactive_check."
uv run --directory $repo proactive-mcp daemon --once
Write-Output "exit=$LASTEXITCODE"
```

Fresh session, using the session-scoped developer instruction prepared in
M5-P3:

```powershell
codex exec -c $codexSessionRule -c 'mcp_servers.proactive.enabled=false' -c 'mcp_servers.proactive_scheduled.default_tools_approval_mode="approve"' --ephemeral --sandbox read-only --skip-git-repo-check -C $neutral "안녕. 오늘 뭐부터 할까?"
```

Each `codex exec` is its own session. The overrides carry the session rule,
disable the full profile, and approve only `proactive_scheduled`. `-C $neutral`
with `--skip-git-repo-check` keeps the repository's `AGENTS.md` out of the
session and lets Codex run outside a git repo. Check `codex exec --help` for
the flag spellings your build has.

Pass:

- The transcript shows a `proactive_check` MCP call
- Codex leads with the upcoming occasion unprompted, and notes that the sources aren't configured
- `grok --cwd $neutral -p "Call list_situations with state=delivered and report only the items length."` counts it as `delivered`

Fail:

- No tool call, sandbox blocks the server spawn, or the situation stays `pending`
- `codex mcp list --json` shows no `proactive_scheduled` entry
- `MCP tool call requires approval, but approval policy is never`, which means the approval override didn't reach this command

#### M5 Scenario 3: Grok CLI, Windows Task Scheduler trigger

Neither CLI has a scheduler of its own, so the OS provides one. This mirrors the Windows Task Scheduler recipe in [`docs/INTEGRATIONS.md`](INTEGRATIONS.md). Scenario 4 repeats it for Codex, and both are required.

The isolated database is what makes an unattended run safe. The task talks to `m5-smoke`, which has no credentials, so no real mail or calendar text can enter the process even though nobody is watching the screen. Your real database is not opened.

The wrapper below **never reads agent output.** It reads `deliveries.total` from
`proactive-mcp status` before and after the agent runs, and that delta is the
whole verdict. The complete `status` JSON is situation-content-free but not
PII-free because `database.path` can contain the Windows username. The wrapper
parses it in memory, retains only `deliveries.total` and the warning count, and
discards the JSON. Grok's stdout and stderr go straight to `$null`, so no
transcript, path, token, or model wording enters the decision or the disk. The
task records fixed marker fields only, and delivery still requires the visible
toast as well as the counter movement.

That design comes from a failed Owner run. The old wrapper asked the model to
answer with one of two fixed words and matched on them. One scheduled run
claimed a situation and answered as if nothing was pending, a false negative
that consumed the situation without telling anyone. The next run had nothing to
claim, answered as if something was, and fired a toast about nothing. Same
prompt, different answers. A delivery contract can't rest on that.

Scheduled tasks get a bare environment. They inherit nothing from your console, so the wrapper sets the database variable itself and every path is absolute:

```powershell
$repo = Join-Path $env:USERPROFILE "src\proactive-mcp"
$smokeDir = Join-Path $env:USERPROFILE ".proactive-mcp\m5-smoke"
$smokeDb = Join-Path $smokeDir "proactive.db"
$neutral = Join-Path $smokeDir "neutral"
$logDir = Join-Path $smokeDir "m5-logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
New-Item -ItemType Directory -Force -Path $neutral | Out-Null
$grok = (Get-Command grok).Source
$uv = (Get-Command uv).Source
$toastScript = (& $uv run --directory $repo python -c `
  "from proactive_mcp.delivery.notify import WINDOWS_TOAST_SCRIPT; print(WINDOWS_TOAST_SCRIPT)").Trim()
Write-Output "grok=$grok"
Write-Output "smokeDb=$smokeDb"
Write-Output "neutral=$neutral"
Write-Output "markers=$logDir"
Write-Output "toastScript=$toastScript"
```

Write the wrapper so the task has exactly one thing to run. This wrapper only ever invokes Grok, so `-p` can't reach the wrong CLI:

```powershell
$grokScript = Join-Path $logDir "m5-grok-trigger.ps1"
$utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($grokScript, @"
Set-Location "$neutral"
`$env:PROACTIVE_DATABASE = "$smokeDb"
`$stamp = (Get-Date).ToString('yyyyMMdd-HHmmss')
`$marker = Join-Path "$logDir" "grok-`$stamp.marker"
`$prompt = "Call proactive_check from proactive_scheduled exactly once. If it returns a receipt_token, call confirm_delivery from proactive_scheduled with that token exactly once. Call no other tool. Do not repeat any situation content, title, why_now, evidence, name, date, mail, or calendar text."

function Write-Marker {
  param([string] `$Fields)
  Set-Content -Path `$marker -Value "stamp=`$stamp cli=grok `$Fields" -Encoding utf8
}

function Send-FixedToast {
  param([string] `$Title, [string] `$Body)
  & powershell.exe -NoProfile -NonInteractive -File "$toastScript" `$Title `$Body *> `$null
  return (`$LASTEXITCODE -eq 0)
}

function Read-Counters {
  `$json = & "$uv" run --directory "$repo" proactive-mcp status 2>`$null
  if (`$LASTEXITCODE -ne 0) { return `$null }
  try { `$doc = `$json | ConvertFrom-Json } catch { return `$null }
  if (`$null -eq `$doc.deliveries) { return `$null }
  `$field = `$doc.deliveries.total
  if (`$null -eq `$field) { return `$null }
  `$total = 0
  if (-not [int]::TryParse([string] `$field, [ref] `$total)) { return `$null }
  if (`$total -lt 0) { return `$null }
  return [pscustomobject]@{
    Deliveries = `$total
    Warnings = @(`$doc.warnings).Count
  }
}

if (-not (Test-Path -LiteralPath "$toastScript" -PathType Leaf)) {
  Write-Marker "result=failure reason=notifier_unavailable notify=none delivered_delta=0 warnings=0 agent_exit=none exit=3"
  exit 3
}

`$before = Read-Counters
if (`$null -eq `$before) {
  `$sent = Send-FixedToast "Proactive check failed" "Open Grok to retry or inspect proactive status."
  `$notify = if (`$sent) { "sent" } else { "failed" }
  Write-Marker "result=failure reason=status_unreadable notify=`$notify delivered_delta=0 warnings=0 agent_exit=none exit=3"
  exit 3
}

& "$grok" --cwd "$neutral" --no-alt-screen -p `$prompt *> `$null
`$code = `$LASTEXITCODE

`$after = Read-Counters
if (`$null -eq `$after) {
  `$sent = Send-FixedToast "Proactive check failed" "Open Grok to retry or inspect proactive status."
  `$notify = if (`$sent) { "sent" } else { "failed" }
  Write-Marker "result=failure reason=status_unreadable notify=`$notify delivered_delta=0 warnings=0 agent_exit=`$code exit=3"
  exit 3
}

`$delta = `$after.Deliveries - `$before.Deliveries
`$warn = `$after.Warnings

if (`$delta -lt 0) {
  `$sent = Send-FixedToast "Proactive check failed" "Open Grok to retry or inspect proactive status."
  `$notify = if (`$sent) { "sent" } else { "failed" }
  Write-Marker "result=failure reason=counter_regressed notify=`$notify delivered_delta=`$delta warnings=`$warn agent_exit=`$code exit=3"
  exit 3
}

if (`$delta -gt 0) {
  `$sent = Send-FixedToast "Proactive alert" "Open Grok to review proactive status."
  if (`$sent) {
    Write-Marker "result=attention notify=sent delivered_delta=`$delta warnings=`$warn agent_exit=`$code exit=0"
    exit 0
  }
  Write-Marker "result=failure reason=notify_failed notify=failed delivered_delta=`$delta warnings=`$warn agent_exit=`$code exit=3"
  exit 3
}

if (`$code -ne 0) {
  `$sent = Send-FixedToast "Proactive check failed" "Open Grok to retry or inspect proactive status."
  `$notify = if (`$sent) { "sent" } else { "failed" }
  Write-Marker "result=failure reason=agent_failed notify=`$notify delivered_delta=0 warnings=`$warn agent_exit=`$code exit=`$code"
  exit `$code
}

if (`$warn -gt 0) {
  `$sent = Send-FixedToast "Proactive status warning" "Open Grok to inspect proactive source status."
  if (`$sent) {
    Write-Marker "result=status_warning notify=sent delivered_delta=0 warnings=`$warn agent_exit=`$code exit=0"
    exit 0
  }
  Write-Marker "result=failure reason=notify_failed notify=failed delivered_delta=0 warnings=`$warn agent_exit=`$code exit=3"
  exit 3
}

Write-Marker "result=no_delivery notify=none delivered_delta=0 warnings=0 agent_exit=`$code exit=0"
exit 0
"@, $utf8)
```

Two things in that script deserve a second look before you run it. The working
directory is `neutral`, not the checkout, so the scheduled session can't pick up
this repository's `AGENTS.md`. And `$env:PROACTIVE_DATABASE` is belt and braces:
the Grok registration already carries the variable into the server, and setting
it here keeps the task isolated even if someone later re-registers the server
without `-e`.

Both agent streams are discarded, so there is nothing to sanitize and nothing to
leak. If a run fails and you want the error text, rerun the same command by hand
in your own console, read it on screen, and retype one sanitized line. The
wrapper captures `$LASTEXITCODE` immediately after the agent call and before any
other native command, and every marker written after that point carries it as
`agent_exit`. Markers written before the agent runs say `agent_exit=none`. A
claim that landed while the CLI still died shows up as `result=attention` with a
non-zero `agent_exit`, so it can't pass unnoticed.

How the wrapper decides:

| Measurement | Marker `result` | Toast | Exit |
|---|---|---|---|
| `deliveries.total` rose | `attention` | **Proactive alert** | 0 when the toast was sent, 3 when it failed |
| `deliveries.total` fell | `failure reason=counter_regressed` | **Proactive check failed** | 3 |
| `status` missing, unparsable, or not a non-negative integer, either read | `failure reason=status_unreadable` | **Proactive check failed** | 3 |
| No change, agent exited non-zero | `failure reason=agent_failed` | **Proactive check failed** | the agent's exit code |
| No change, warnings present | `status_warning` | **Proactive status warning** | 0 when the toast was sent, 3 when it failed |
| No change, no warnings | `no_delivery` | none | 0 |

Nothing in that table reads the agent's output. Any increase in the counter is
attention, because only a successful `confirm_delivery` after the
`proactive_check` lease can move it. A decrease is impossible against an
append-only table, so it means something is wrong with the database and the
wrapper says so out loud. A run that delivered nothing and failed is never
silent either. The one quiet outcome is the honest one: no delivery, no
warnings, nothing to tell you.

```powershell
Register-ScheduledTask -TaskName "proactive-m5-grok" -Force -Action (
  New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$grokScript`""
) -Trigger (
  New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
    -RepetitionInterval (New-TimeSpan -Minutes 5)
) -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable)
```

Run M5-P4 for a new `TAG`. Then take your own baseline from the same PII-free field the wrapper uses. No agent is involved, which is the point:

```powershell
$env:PROACTIVE_DATABASE = $smokeDb
$before = (& $uv run --directory $repo proactive-mcp status | ConvertFrom-Json)
Write-Output "DELIVERIES_BEFORE=$($before.deliveries.total)"
Write-Output "WARNINGS_BEFORE=$(@($before.warnings).Count)"
```

While a measurement window is open, nothing else may call `proactive_check`.
That means no interactive Grok or Codex session, no second scheduled task, and
no `proactive_check` typed into another window between the wrapper's two
`status` reads. A concurrent claim would raise `deliveries.total` for reasons
this run can't see, and the counter can't tell you who moved it. `daemon --once`
is safe: it detects and stores, it never delivers. `list_situations` is safe
too. If you think something else touched the database mid-window, throw the run
away, plant a fresh `TAG`, and measure again.

Now wait for the trigger. Don't call `Start-ScheduledTask`. The point is that nobody touched it.

```powershell
Get-ScheduledTaskInfo -TaskName "proactive-m5-grok" |
  Format-List TaskName, LastRunTime, LastTaskResult, NextRunTime
Get-ChildItem $logDir -Filter "grok-*.marker" |
  Sort-Object LastWriteTime | Select-Object -Last 1 -ExpandProperty Name
Get-ChildItem $logDir -Filter "grok-*.marker" |
  Sort-Object LastWriteTime | Select-Object -Last 1 | Get-Content
```

Then read the counter yourself, once the run is over:

```powershell
$after = (& $uv run --directory $repo proactive-mcp status | ConvertFrom-Json)
Write-Output "DELIVERIES_AFTER=$($after.deliveries.total)"
Write-Output "DELTA=$($after.deliveries.total - $before.deliveries.total)"
```

A delta of exactly 1 means the unattended run leased and confirmed the
situation. Only `confirm_delivery` can produce that delivery event. Your
number and the marker's `delivered_delta` have to agree.

Pass:

- `LastTaskResult` is `0` and `LastRunTime` is after you planted the situation
- `DELTA` is exactly `1`
- The newest `grok-*.marker` exists, holds one line, and says `result=attention notify=sent delivered_delta=1 agent_exit=0 exit=0`
- One fixed **Proactive alert** toast appears and says to open Grok to review proactive status; it contains no situation content
- All of it happened with nobody at the keyboard

Fail:

- `DELTA` is `0`, or the marker says `no_delivery`, `status_warning`, or any `result=failure`
- `DELTA` is `1` but no fixed toast appeared, or the marker says `notify=failed`
- `DELTA` disagrees with the marker's `delivered_delta`, which means something else called `proactive_check` during the window
- The marker says `agent_exit` is anything but `0`, which means the CLI failed after the claim landed
- Non-zero `LastTaskResult`, no marker at all, or `uv`/`grok`/the toast script not found
- Any file in `m5-logs` that isn't a wrapper or a one-line marker

A `result=status_warning` marker is a real outcome, not a crash: the agent ran,
nothing was delivered, and the warning toast told you the source picture is
incomplete. For this scenario it's still a fail, because a fresh situation was
waiting and nothing claimed it. Plant a new `TAG` and look at why the claim
didn't happen.

Marker files hold only timestamp, CLI, fixed result and reason, notification
status, the delivery delta, the warning count, the agent's exit code, and the
wrapper's own exit code. Every one of those is a fixed word or a number. Nothing else belongs in `m5-logs`.


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

It reads `deliveries.total` before and after the Codex run, exactly as scenario
3 does, and discards Codex's stdout and stderr. That matters more here than it
looks: `codex exec` prints a whole transcript, headers, tool lines, token counts
and all, so any attempt to match a final answer against a fixed string is
guesswork. The counter isn't.

```powershell
$repo = Join-Path $env:USERPROFILE "src\proactive-mcp"
$smokeDir = Join-Path $env:USERPROFILE ".proactive-mcp\m5-smoke"
$smokeDb = Join-Path $smokeDir "proactive.db"
$neutral = Join-Path $smokeDir "neutral"
$logDir = Join-Path $smokeDir "m5-logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
New-Item -ItemType Directory -Force -Path $neutral | Out-Null
$codex = (Get-Command codex).Source
$uv = (Get-Command uv).Source
$toastScript = (& $uv run --directory $repo python -c `
  "from proactive_mcp.delivery.notify import WINDOWS_TOAST_SCRIPT; print(WINDOWS_TOAST_SCRIPT)").Trim()
Write-Output "codex=$codex"
Write-Output "smokeDb=$smokeDb"
Write-Output "neutral=$neutral"
Write-Output "markers=$logDir"
Write-Output "toastScript=$toastScript"
```

This wrapper only ever invokes Codex, so `-p` can't reach it by accident:

```powershell
$codexScript = Join-Path $logDir "m5-codex-trigger.ps1"
$utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($codexScript, @"
Set-Location "$neutral"
`$env:PROACTIVE_DATABASE = "$smokeDb"
`$stamp = (Get-Date).ToString('yyyyMMdd-HHmmss')
`$marker = Join-Path "$logDir" "codex-`$stamp.marker"
`$prompt = "Call proactive_check from proactive_scheduled exactly once. If it returns a receipt_token, call confirm_delivery from proactive_scheduled with that token exactly once. Call no other tool. Do not repeat any situation content, title, why_now, evidence, name, date, mail, or calendar text."

function Write-Marker {
  param([string] `$Fields)
  Set-Content -Path `$marker -Value "stamp=`$stamp cli=codex `$Fields" -Encoding utf8
}

function Send-FixedToast {
  param([string] `$Title, [string] `$Body)
  & powershell.exe -NoProfile -NonInteractive -File "$toastScript" `$Title `$Body *> `$null
  return (`$LASTEXITCODE -eq 0)
}

function Read-Counters {
  `$json = & "$uv" run --directory "$repo" proactive-mcp status 2>`$null
  if (`$LASTEXITCODE -ne 0) { return `$null }
  try { `$doc = `$json | ConvertFrom-Json } catch { return `$null }
  if (`$null -eq `$doc.deliveries) { return `$null }
  `$field = `$doc.deliveries.total
  if (`$null -eq `$field) { return `$null }
  `$total = 0
  if (-not [int]::TryParse([string] `$field, [ref] `$total)) { return `$null }
  if (`$total -lt 0) { return `$null }
  return [pscustomobject]@{
    Deliveries = `$total
    Warnings = @(`$doc.warnings).Count
  }
}

if (-not (Test-Path -LiteralPath "$toastScript" -PathType Leaf)) {
  Write-Marker "result=failure reason=notifier_unavailable notify=none delivered_delta=0 warnings=0 agent_exit=none exit=3"
  exit 3
}

`$before = Read-Counters
if (`$null -eq `$before) {
  `$sent = Send-FixedToast "Proactive check failed" "Open Codex to retry or inspect proactive status."
  `$notify = if (`$sent) { "sent" } else { "failed" }
  Write-Marker "result=failure reason=status_unreadable notify=`$notify delivered_delta=0 warnings=0 agent_exit=none exit=3"
  exit 3
}

& "$codex" exec -c 'mcp_servers.proactive.enabled=false' -c 'mcp_servers.proactive_scheduled.default_tools_approval_mode="approve"' --ephemeral --sandbox read-only --skip-git-repo-check -C "$neutral" `$prompt *> `$null
`$code = `$LASTEXITCODE

`$after = Read-Counters
if (`$null -eq `$after) {
  `$sent = Send-FixedToast "Proactive check failed" "Open Codex to retry or inspect proactive status."
  `$notify = if (`$sent) { "sent" } else { "failed" }
  Write-Marker "result=failure reason=status_unreadable notify=`$notify delivered_delta=0 warnings=0 agent_exit=`$code exit=3"
  exit 3
}

`$delta = `$after.Deliveries - `$before.Deliveries
`$warn = `$after.Warnings

if (`$delta -lt 0) {
  `$sent = Send-FixedToast "Proactive check failed" "Open Codex to retry or inspect proactive status."
  `$notify = if (`$sent) { "sent" } else { "failed" }
  Write-Marker "result=failure reason=counter_regressed notify=`$notify delivered_delta=`$delta warnings=`$warn agent_exit=`$code exit=3"
  exit 3
}

if (`$delta -gt 0) {
  `$sent = Send-FixedToast "Proactive alert" "Open Codex to review proactive status."
  if (`$sent) {
    Write-Marker "result=attention notify=sent delivered_delta=`$delta warnings=`$warn agent_exit=`$code exit=0"
    exit 0
  }
  Write-Marker "result=failure reason=notify_failed notify=failed delivered_delta=`$delta warnings=`$warn agent_exit=`$code exit=3"
  exit 3
}

if (`$code -ne 0) {
  `$sent = Send-FixedToast "Proactive check failed" "Open Codex to retry or inspect proactive status."
  `$notify = if (`$sent) { "sent" } else { "failed" }
  Write-Marker "result=failure reason=agent_failed notify=`$notify delivered_delta=0 warnings=`$warn agent_exit=`$code exit=`$code"
  exit `$code
}

if (`$warn -gt 0) {
  `$sent = Send-FixedToast "Proactive status warning" "Open Codex to inspect proactive source status."
  if (`$sent) {
    Write-Marker "result=status_warning notify=sent delivered_delta=0 warnings=`$warn agent_exit=`$code exit=0"
    exit 0
  }
  Write-Marker "result=failure reason=notify_failed notify=failed delivered_delta=0 warnings=`$warn agent_exit=`$code exit=3"
  exit 3
}

Write-Marker "result=no_delivery notify=none delivered_delta=0 warnings=0 agent_exit=`$code exit=0"
exit 0
"@, $utf8)
```

The wrapper disables the full profile and approves only
`proactive_scheduled`. Without that narrow override the scheduled run ends
with `MCP tool call requires approval, but approval policy is never`, the
counter never moves, and the wrapper reports `agent_failed` with a failure
toast. `-C "$neutral"` plus `--skip-git-repo-check` keeps the task out of any
repository, so no project instructions ride along.

The decision table is the same one scenario 3 uses:

| Measurement | Marker `result` | Toast | Exit |
|---|---|---|---|
| `deliveries.total` rose | `attention` | **Proactive alert** | 0 when the toast was sent, 3 when it failed |
| `deliveries.total` fell | `failure reason=counter_regressed` | **Proactive check failed** | 3 |
| `status` missing, unparsable, or not a non-negative integer, either read | `failure reason=status_unreadable` | **Proactive check failed** | 3 |
| No change, agent exited non-zero | `failure reason=agent_failed` | **Proactive check failed** | the agent's exit code |
| No change, warnings present | `status_warning` | **Proactive status warning** | 0 when the toast was sent, 3 when it failed |
| No change, no warnings | `no_delivery` | none | 0 |

Nothing in that table reads the agent's output. Any increase in the counter is
attention, because only a successful `confirm_delivery` after the
`proactive_check` lease can move it. A decrease is impossible against an
append-only table, so it means something is wrong with the database and the
wrapper says so out loud. A run that delivered nothing and failed is never
silent either. The one quiet outcome is the honest one: no delivery, no
warnings, nothing to tell you.

```powershell
Register-ScheduledTask -TaskName "proactive-m5-codex" -Force -Action (
  New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$codexScript`""
) -Trigger (
  New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
    -RepetitionInterval (New-TimeSpan -Minutes 5)
) -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable)
```

Run M5-P4 for a new `TAG`, so this scenario claims a situation scenario 3 never saw. Baseline from `status`, with no agent in the loop:

```powershell
$env:PROACTIVE_DATABASE = $smokeDb
$before = (& $uv run --directory $repo proactive-mcp status | ConvertFrom-Json)
Write-Output "DELIVERIES_BEFORE=$($before.deliveries.total)"
Write-Output "WARNINGS_BEFORE=$(@($before.warnings).Count)"
```

While a measurement window is open, nothing else may call `proactive_check`.
That means no interactive Grok or Codex session, no second scheduled task, and
no `proactive_check` typed into another window between the wrapper's two
`status` reads. A concurrent claim would raise `deliveries.total` for reasons
this run can't see, and the counter can't tell you who moved it. `daemon --once`
is safe: it detects and stores, it never delivers. `list_situations` is safe
too. If you think something else touched the database mid-window, throw the run
away, plant a fresh `TAG`, and measure again.

Then wait. Don't call `Start-ScheduledTask`.

```powershell
Get-ScheduledTaskInfo -TaskName "proactive-m5-codex" |
  Format-List TaskName, LastRunTime, LastTaskResult, NextRunTime
Get-ChildItem $logDir -Filter "codex-*.marker" |
  Sort-Object LastWriteTime | Select-Object -Last 1 -ExpandProperty Name
Get-ChildItem $logDir -Filter "codex-*.marker" |
  Sort-Object LastWriteTime | Select-Object -Last 1 | Get-Content
```

Read the counter again once the run is over:

```powershell
$after = (& $uv run --directory $repo proactive-mcp status | ConvertFrom-Json)
Write-Output "DELIVERIES_AFTER=$($after.deliveries.total)"
Write-Output "DELTA=$($after.deliveries.total - $before.deliveries.total)"
```

Pass:

- `LastTaskResult` is `0` and `LastRunTime` is after you planted the situation
- `DELTA` is exactly `1`
- The newest `codex-*.marker` exists, holds one line, and says `result=attention notify=sent delivered_delta=1 agent_exit=0 exit=0`
- One fixed **Proactive alert** toast appears and says to open Codex to review proactive status; it contains no situation content
- All of it happened with nobody at the keyboard

Fail:

- `DELTA` is `0`, or the marker says `no_delivery`, `status_warning`, or any `result=failure`
- `DELTA` is `1` but no fixed toast appeared, or the marker says `notify=failed`
- `DELTA` disagrees with the marker's `delivered_delta`
- The marker says `agent_exit` is anything but `0`, which means the CLI failed after the claim landed
- Non-zero `LastTaskResult`, no marker at all, or `uv`/`codex`/the toast script not found
- `result=failure reason=agent_failed`, which under the task account usually means either the approval override didn't reach the wrapper or the sandbox blocked the server spawn; the task account is a different context than your console

Marker fields here are the same fixed set as scenario 3, with `cli=codex`.


Before scenario 5, remove the repeating Codex task and verify that neither M5
scheduler remains:

```powershell
Unregister-ScheduledTask -TaskName "proactive-m5-codex" -Confirm:$false -ErrorAction SilentlyContinue
Get-ScheduledTask -TaskName "proactive-m5-*" -ErrorAction SilentlyContinue
```

The second command must print nothing.

#### M5 Scenario 5: delivered once, not twice

This is the dedupe half of PRODUCT_PLAN §5.1, and it runs after scenarios 1 to
4. Use the Grok form shown for fixture inspection and the two delivery
attempts. The restricted Codex profile intentionally has no
`list_situations`, so mixing it into this three-command assertion would require
re-approving the unrestricted profile and defeat the permission boundary.

Before planting the dedupe situation, defensively unregister both repeating
tasks and verify that no scheduler can claim it:

```powershell
Unregister-ScheduledTask -TaskName "proactive-m5-grok" -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "proactive-m5-codex" -Confirm:$false -ErrorAction SilentlyContinue
Get-ScheduledTask -TaskName "proactive-m5-*" -ErrorAction SilentlyContinue
```

The verification command must print nothing.

Run M5-P4 for a new `TAG`. Before claiming it, take both baselines. The counter comes from `status`, with no agent involved:

```powershell
$repo = Join-Path $env:USERPROFILE "src\proactive-mcp"
$neutral = Join-Path $env:USERPROFILE ".proactive-mcp\m5-smoke\neutral"
$env:PROACTIVE_DATABASE = Join-Path $env:USERPROFILE ".proactive-mcp\m5-smoke\proactive.db"
$before = (& uv run --directory $repo proactive-mcp status | ConvertFrom-Json)
Write-Output "DELIVERIES_BEFORE=$($before.deliveries.total)"
grok --cwd $neutral -p "Call list_situations with state=pending and report only the items length. Do not call proactive_check."
```

Record those as `DELIVERIES_BEFORE` and `PENDING_BEFORE`. Then claim it, twice, in two separate invocations:

```powershell
grok --cwd $neutral -p "Call proactive_check exactly once. If it returns receipt_token, call confirm_delivery with that token exactly once. Report situation_type, priority, the state returned by proactive_check, held_count, all_clear, and delivered_count. Do not paste title, why_now, or evidence."
grok --cwd $neutral -p "Call proactive_check. Report situation_type, priority, state, held_count, and all_clear. Do not paste title, why_now, or evidence."
```

Each invocation is its own session, so the second one has no memory of the first. Only the database can stop it handing the situation out again. Read the counter once both have finished:

```powershell
$after = (& uv run --directory $repo proactive-mcp status | ConvertFrom-Json)
Write-Output "DELTA=$($after.deliveries.total - $before.deliveries.total)"
```

Pass:

- `DELTA` is exactly `1` across both checks, not `2`
- First `proactive_check`: the new situation is present with `state=pending`,
  returns a receipt token, and `confirm_delivery` reports
  `delivered_count=1`
- Second `proactive_check`: that situation is **not** returned again
- `list_situations state=pending` now counts `PENDING_BEFORE - 1`
- No duplicate `personal_occasion` for the same synthetic entity anywhere in the delivered list

Fail:

- `DELTA` is `2` for one situation, or `0` after a successful first check
- The same situation comes back on the second check
- `confirm_delivery` is skipped or does not report `delivered_count=1`
- Any M5 scheduler task is still registered before the situation is planted

### M5 확인 포인트

Go through this before you write the sign-off comment. Counts and states only, no content.

1. Branch is current `main`, or the SHA a review comment named, and `migration_version` is `9`
2. **Every scenario ran against `m5-smoke\proactive.db`,** with `google.gmail.status` and `google.calendar.status` both `not_configured`, and your real database was never opened
3. The test-only config is the one from M5-P2, it sits in `m5-smoke`, and you know it isn't production behavior
4. Every claim used a fresh `TAG` and its own `daemon --once` pass
5. **Grok CLI passed a fresh session** (scenario 1)
6. **Codex CLI passed a fresh session** (scenario 2)
7. **Grok CLI passed a scheduled trigger** (scenario 3): `deliveries.total` rose by exactly 1, the marker said `result=attention notify=sent`, and the fixed toast appeared, with no human in the loop
8. **Codex CLI passed a scheduled trigger** (scenario 4), on the same three counts
9. Scenario 5 passed: `pending` became `delivered`, once, with `deliveries.total` up by 1 and not 2
10. `grok --version` and `codex --version` recorded, along with any flag that differed from this document
11. Every agent call ran from `m5-smoke\neutral`, and every Codex delivery run
    disabled the full profile and approved only `proactive_scheduled`
12. No other `proactive_check` ran inside any measurement window
13. Artifacts sit where they're supposed to, the DACL still holds, and cleanup put both CLI registrations back where they started (below)

#### M5 artifact location and DACL recheck

Everything M5 creates lives in `%USERPROFILE%\.proactive-mcp\m5-smoke`: its own `proactive.db`, the test-only `config.toml`, the empty `neutral` folder, the `m5-logs` folder, both wrappers, and the marker files. Nothing new belongs in the parent folder. Two things to confirm here: the artifacts are where they should be, and the protected DACL covers them.

Explorer:

1. Win+R, paste `%USERPROFILE%\.proactive-mcp`, Enter.
2. Confirm the parent still holds your real `proactive.db` and `config.toml`, and that the only new item is the `m5-smoke` folder. A stray `m5-logs` here means an older wrapper wrote outside the smoke directory.
3. Open `m5-smoke`. It should hold `proactive.db`, `config.toml`, `neutral`, and `m5-logs`, and no credentials folder.
4. Open `neutral`. It must still be empty. An `AGENTS.md` or a `.git` folder in there means an agent call ran somewhere else, or something wrote into the folder that is supposed to stay bare.
5. Open `m5-logs`. It should hold `m5-grok-trigger.ps1`, `m5-codex-trigger.ps1`, `grok-*.marker`, and `codex-*.marker`, and no `.log` files.
6. Right-click `m5-smoke` > Properties / 속성 > Security / 보안. The name list should be your Windows account only. `Everyone`, `Users`, and `Authenticated Users` should not be there.
7. Advanced / 고급: entries should be explicit or inherited from the protected `.proactive-mcp` parent, and no ACE should trace back above that folder.
8. Repeat steps 6 and 7 on `m5-smoke\proactive.db`, on `m5-smoke\config.toml`, on `m5-smoke\neutral`, and on one marker file.

PowerShell:

```powershell
$dir = Join-Path $env:USERPROFILE ".proactive-mcp"
$smokeDir = Join-Path $dir "m5-smoke"
$smokeDb = Join-Path $smokeDir "proactive.db"
$cfg = Join-Path $smokeDir "config.toml"
$neutral = Join-Path $smokeDir "neutral"
$logDir = Join-Path $smokeDir "m5-logs"
$marker = Get-ChildItem $logDir -Filter "*.marker" |
  Sort-Object LastWriteTime | Select-Object -Last 1 -ExpandProperty FullName

Write-Output "=== paths ==="
Get-Item $smokeDir, $smokeDb, $cfg, $neutral, $logDir, $marker |
  Format-List FullName, Length, LastWriteTime

Write-Output "=== neutral stays empty ==="
Get-ChildItem $neutral -Force

Write-Output "=== nothing new in the parent ==="
Get-ChildItem $dir | Select-Object Name, LastWriteTime

Write-Output "=== icacls ==="
icacls $smokeDir
icacls $smokeDb
icacls $cfg
icacls $neutral
icacls $marker

Write-Output "=== Get-Acl ==="
foreach ($p in @($smokeDir, $smokeDb, $cfg, $neutral, $logDir, $marker)) {
  $acl = Get-Acl $p
  Write-Output "Path=$($acl.Path)"
  Write-Output "Owner=$($acl.Owner)"
  Write-Output "AreAccessRulesProtected=$($acl.AreAccessRulesProtected)"
  $acl.Access |
    Format-Table IdentityReference, FileSystemRights, AccessControlType, IsInherited -AutoSize
}
```

Success: every `FullName` is under `C:\Users\<you>\.proactive-mcp\m5-smoke`, `Owner` is your account on all of them, and no `IdentityReference` is `Everyone`, `BUILTIN\Users`, or `NT AUTHORITY\Authenticated Users`. The `neutral` listing prints nothing. The parent listing shows `m5-smoke` and your pre-existing files, nothing else new. Marker `Length` should be small, well under two hundred bytes, since a marker is one line of fixed words and small integers. A large marker means output leaked into it, which shouldn't be possible now that the wrappers discard agent streams.

Then rerun the storage checks in the [Explorer](#explorer) and [PowerShell ACL](#powershell-acl) sections above against the real `%USERPROFILE%\.proactive-mcp\proactive.db`, unchanged. M5 writes inside a child of that folder, so the production database DACL has to still pass afterwards, and its `LastWriteTime` should predate the smoke.

Failure: an artifact outside `m5-smoke`, anything at all inside `neutral`, a broad identity in any ACL, `AreAccessRulesProtected` false on `.proactive-mcp`, a marker file larger than a line of text, or a changed `LastWriteTime` on the real database.

**Acceptance decision.** Per [#20](https://github.com/madrobotnet/proactive-mcp/issues/20), Issue #6 is satisfied when lines 5 through 8 are all true. For the scheduled half, "true" means the delivery counter moved and the fixed toast appeared, not that an agent said something reassuring. Both CLIs must prove a fresh session, and both must prove a scheduled trigger. One CLI doing all four is not acceptance, and two fresh sessions with no scheduled evidence is not acceptance either. State the decision in one line, for example: `M5 acceptance: Grok CLI + Codex CLI, fresh and scheduled. PASS.` Anything short of all four is a FAIL, and name which scenario fell over.

Claude Code Desktop and Hermes stay out of this decision. Their absence is not a failure.

### M5 cleanup

Do this even when everything passed. Scenarios 3 and 4 already unregister their
tasks to prevent races; the commands below are idempotent final safety cleanup.
Three things have to be undone: the repeating tasks, the smoke registration in
both CLIs, and the smoke directory.

First the tasks:

```powershell
Unregister-ScheduledTask -TaskName "proactive-m5-grok" -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "proactive-m5-codex" -Confirm:$false -ErrorAction SilentlyContinue
Get-ScheduledTask -TaskName "proactive-m5-*" -ErrorAction SilentlyContinue
```

That last line should print nothing.

Now undo the registrations. Cleanup restores each CLI to the state it was in **before** the smoke, and nothing else. Leaving the smoke path pinned would quietly point your everyday agents at a database you're about to delete.

Go back to the baseline you recorded in M5-P3 and treat each CLI separately.

**If that CLI had no `proactive` or `proactive_scheduled` entry before the
smoke**, which is the documented Owner baseline for both, remove both smoke
entries:

```powershell
grok mcp remove --scope user proactive 2>$null
grok mcp remove --scope user proactive_scheduled 2>$null
codex mcp remove proactive 2>$null
codex mcp remove proactive_scheduled 2>$null

grok mcp list
codex mcp list --json
```

For a CLI that started with no entry, its listing must show neither profile
afterwards. Confirm the flag spellings with `grok mcp remove --help` and
`codex mcp remove --help` first; both CLIs move fast.

**If that CLI did have an entry**, remove that CLI's smoke entry, then run the
exact restore command you kept privately. Check the listing shows your own
registration and not the smoke database path. Restoring it is your job and
yours alone. Never paste your registration block, your `~/.grok` or
`%USERPROFILE%\.codex\config.toml` contents, or any part of that config into an
issue, a PR, or a comment. Nobody reviewing this smoke needs it, and it can
carry paths and other identifiers.

Don't register a default entry just to have one. If you had no entry before,
having none after is correct, and a fresh "default" registration would be a
change this smoke made to your machine without asking. For an absent baseline,
the empty CLI listing is the verification.

Only testers who restored a pre-existing entry need the runtime path check
below. Run it in a **new** PowerShell window, so the smoke variable is gone from
the environment:

```powershell
$neutral = Join-Path $env:USERPROFILE ".proactive-mcp\m5-smoke\neutral"
grok --cwd $neutral -p "Call get_status and report only database.path."
codex mcp get proactive
codex mcp get proactive_scheduled 2>$null
```

The Grok runtime path and every restored Codex command/environment listing must
match your private baseline, with no `m5-smoke` anywhere in them. Do not
temporarily auto-approve the full Codex profile just to perform this cleanup
check. Whether the baseline was absent or restored, no CLI may still resolve
to the smoke database.

Only then delete the smoke directory. This removes the smoke database, the test-only config, the neutral folder, both wrappers, and every marker in one go:

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
   Add the delivery evidence as three integers: `DELIVERIES_BEFORE`, `DELIVERIES_AFTER`, and the delta. They're counts, so they're safe to paste as they are.
4. **Isolation proof.** That `database.path` ended with `m5-smoke\proactive.db` and that Gmail and Calendar were both `not_configured`. Write the path as `...\m5-smoke\proactive.db`, with your username cut off.
5. **Versions.** `grok --version`, `codex --version`, `uv --version`, and `uv run python --version`. The application revision is the Git SHA from item 7; `proactive-mcp` has no `--version` flag.
6. **OS and shell.** Windows edition and build, and `$PSVersionTable.PSVersion`.
7. **Branch and SHA.** The output of `git rev-parse --abbrev-ref HEAD` and `git rev-parse HEAD`.
8. **Redacted status fields only,** from `uv run proactive-mcp status`: `overall`, `database.status`, `database.journal_mode`, `database.migration_version`, `daemon.status`, `budget.daily_budget`, and the budget counts. Retype them. Don't paste the whole document.
9. **Tool evidence as names and numbers.** Which tools were called, how many times each, and the `items` length returned. For `proactive_check`, report `situation_type`, `priority`, `state`, `held_count`, and `all_clear`, and nothing else. Don't quote what the agent said; the scheduled scenarios don't judge on it, and a quoted sentence is how situation content escapes.
10. **Scheduler result** for scenario 3 or 4: the task name, whether the trigger fired unattended, `LastRunTime`, `NextRunTime`, `LastTaskResult`, the marker filename and its fixed `result`/`reason`/`notify`/`delivered_delta`/`warnings`/`agent_exit`/`exit` fields, your own `deliveries.total` before and after, and whether the fixed toast appeared. Every one of those is a fixed word or an integer, so the whole marker line can be pasted as it stands. Do not include a screenshot.
11. **Registration shape, not contents.** Whether the failing CLI's entry carried `PROACTIVE_DATABASE`, and whether the Codex command carried the approval override, as yes or no. Never paste the registration block or the CLI config file.
12. **Working directory,** as one word: `neutral`, or wherever the call actually ran. A scenario that ran from the checkout is a procedure error, not a product bug.
13. **A sanitized error line** if a CLI failed. Rerun the command by hand, read the message on screen, and retype one line with paths shortened to `%USERPROFILE%\...` and any identifier or token replaced with `<redacted>`. For Grok, its stderr logs under `%USERPROFILE%\.grok\logs\mcp\` are for your eyes only. Read, retype the one line, attach nothing.

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

## 부록: 클로즈드 알파 테스터 경로 (M6)

> [!IMPORTANT]
> **Windows testers use one path only:** open
> [`docs/testers/windows.md`](testers/windows.md) and paste its complete
> **에이전트에게 붙여 넣기** block into the local agent you already use. Do
> not execute A1–A8 below, install the wheel yourself, run `setup` in
> PowerShell, or edit MCP configuration. The agent performs installation,
> registration, setup, session-start and scheduled delivery wiring, continuous
> watcher setup, verification, and rollback. You only handle consent prompts
> and report the sanitized fields named in the sheet.

The sheet is the canonical tester procedure. Its success, reporting, and
credential-first rollback sections replace the older appendix workflow.
[`docs/RELEASE_ALPHA.md`](RELEASE_ALPHA.md) remains the Owner handoff record.

현재 붙여 넣기 블록은 다음 계약을 모두 포함합니다. `reply_deadline`은 행동
필요 판정이 아니라 보수적으로 뽑은 후보입니다. 사용자에게 말하기 전에
뉴스레터·마케팅·자동 영수증, 요청이 없는 FYI 또는 FYI-CC, 다른 사람이 맡은
스레드, 이 사용자에게 답해야 할 질문·요청·결정이 없는 행은 확신할 수 있을 때
제외합니다. 명시적인 회신·RSVP·결정 요청, 사용자가 책임진 마감, 이 사용자에게
직접 묻고 아직 답하지 않은 질문은 유지합니다. 불확실한 후보는 사용자에게
알리거나 lease 전체를 미확정 상태로 두거나 일상 대화에서 snooze하며, 비실행
항목이라고 조용히 버리지 않습니다. 모든 행을 검토한 뒤 확정하기로 선택할 때만,
보여 주지 않기로 확신한 후보까지 포함해 검토한 lease 전체를 정확히 한 번 확정합니다.
MCP 도구명·설명·필드·값은 영어로 유지하되 사용자에게는 사용자의 언어로
말합니다.
일상 대화에는 `serve`만, 별도 예약 대화에는 `serve-scheduled`만 로드하며 한
대화에 두 프로필을 함께 로드하지 않습니다.

<details>
<summary>Owner/maintainer reference: superseded manual A1–A8 workflow</summary>

The material below is retained only for diagnosing an agent-led onboarding
failure or reconstructing earlier alpha evidence. It is not a tester entry
point and must not be sent as installation instructions.

### Owner reference A1. Install the wheel

```powershell
winget show --id astral-sh.uv --exact --source winget
winget install --id astral-sh.uv --exact --source winget
$env:Path = "$env:USERPROFILE\.local\bin;$env:USERPROFILE\.cargo\bin;$env:Path"
uv --version
```

The winget manifest pins and verifies the installer hash; do not pipe a mutable
web response into PowerShell. Record the installed uv version with the alpha
report. If the Owner named an approved version for this handoff, add
`--version <that-version>` to both winget commands and stop if it is
unavailable.

Then create a virtualenv of your own and install the file you were given.
Substitute the real filename; the version in it is whatever the Owner sent:

```powershell
$wheel = Join-Path $env:USERPROFILE "Downloads\proactive_mcp-0.1.0-py3-none-any.whl"
$venv = Join-Path $env:USERPROFILE "venvs\proactive"
uv venv $venv --python 3.11
uv pip install --python (Join-Path $venv "Scripts\python.exe") $wheel
$pm = Join-Path $venv "Scripts\proactive-mcp.exe"
Write-Output "pm=$pm"
& $pm --help
```

Success: `--help` prints the subcommand list, which is
`{serve,serve-scheduled,status,setup,google-smoke,daemon}`.

Failure: `uv pip install` rejects the file, or `proactive-mcp.exe` never appears.
Paste the full stderr and the exact wheel filename. Don't fall back to
`pip install proactive-mcp`; the package isn't published, so that command
installs nothing of ours.

Keep `$pm` handy. Every command below starts with it, and a new PowerShell
window needs it set again.

### Owner reference A2. Put the client secret where `setup` looks

`setup` runs the Google OAuth flow and can't start without an installed-app
client secret file. During the closed alpha that's the JSON the Owner delivered
alongside the wheel. Copy it to the default location:

```powershell
$dir = Join-Path $env:USERPROFILE ".proactive-mcp"
$client = Join-Path $dir "client_secret.json"
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
New-Item -ItemType Directory -Force -Path $dir | Out-Null
Copy-Item (Join-Path $env:USERPROFILE "Downloads\client_secret.json") $client
icacls $client /inheritance:r /grant:r "*${sid}:(F)"
Get-Item $client | Format-List FullName, Length
Get-Acl $client | Format-List Owner, AreAccessRulesProtected, Access
```

`AreAccessRulesProtected` must be `True`, and the only Allow identity must be
your current-user SID. `setup` reads this input file but does not harden it, so
stop before A3 if a broad identity such as `Everyone`, `Users`, or
`Authenticated Users` remains.

Resolution order is `--client-secrets PATH` first, then the
`PROACTIVE_GOOGLE_CLIENT_SECRETS` variable, then `client_secret.json` beside the
database. Taking the default is simplest. Prefer to keep the file somewhere
else? Pass `--client-secrets` on the next command instead.

Testers assigned to validate the bring-your-own path create their own OAuth
client of type **Desktop app** in their own Google Cloud project and use that
download here. Everyone else uses the delivered file.

Never commit either file, and never paste one into an issue.

### Owner reference A3. Run `setup`

```powershell
& $pm setup
Write-Output "exit=$LASTEXITCODE"
```

A browser opens on a loopback authorization page. The Owner's OAuth client is
published but unverified, so Google shows a warning screen: choose Advanced,
then continue. Grant both read-only scopes. Tokens and data stay on your machine
and are never sent to the client owner.

Add `--headless` when the machine has no browser to open that page. Use
`--reauth` to replace an existing authorization after revoking access or
changing scopes.

Success: exit 0, and the final success line is:

```
Google read-only sources configured.
```

In headless mode, the expected authorization URL and prompt may appear before
that final line. `setup` stores the authorization; it doesn't read your mail or
your calendar, so it can't and doesn't report per-source counts or freshness.
The final success line is required.

Failure: paste the exit code and one sanitized error line. `invalid_client` or
`redirect_uri_mismatch` almost always means the wrong JSON landed in A2.

### Owner reference A4. Confirm state with `status`

```powershell
& $pm status
Write-Output "exit=$LASTEXITCODE"
```

Success, all of these:

- Exit code 0
- `database.status` is `"healthy"`, and `database.path` ends with
  `.proactive-mcp\proactive.db`
- `database.journal_mode` is `wal`
- `database.migration_version` is `9`
- `google.gmail.status` and `google.calendar.status` are both `"never_synced"`
- `overall` is `"degraded"`
- `warnings` contains `"Google Gmail has not completed a read sync."` and
  `"Google Calendar has not completed a read sync."`

This is the confusing part of onboarding, so read it twice. There is no
`configured` source status. The values are `ok`, `stale`, `never_synced`,
`not_configured`, `needs_reauth`, and `error`. Authorization alone gets you to
`never_synced`, because `status` reports persisted state and reads nothing, and
nothing has read your account yet. `overall` is likewise only `ok` or
`degraded`, and any warning at all forces `degraded`, so a freshly set up
install with no daemon is `degraded` by design. You reach `ok` in A4b below.

Failure: a source still reading `not_configured` after A3 printed its success
line, a database path somewhere unexpected, a `migration_version` under 9, or a
non-zero exit. A `daemon.status` of `not_running` on its own is fine. The daemon
is recommended, not required, and skipping it costs you only the OS notification
fallback.

### Owner reference A4b. First real read, then `ok`

Confirm the read path reaches your real account. The confirmation flag isn't
optional; without it the command refuses to touch the account and exits 2:

```powershell
& $pm google-smoke --confirm-real-account-read
Write-Output "exit=$LASTEXITCODE"
```

Success: exit 0 and one JSON line holding `gmail`, `calendar`, and
`credential_cleanup_failed`. Each source carries a `count` and an `error_code`
that should be `null`. That output is redacted by construction, so there are no
subjects, addresses, or event titles in it, and the counts are safe to report as
is.

Failure: exit 2 with `error:` on stderr. `GoogleReadSmokeDisabledError` means
you left the flag off. A missing-credentials error means A3 never finished on
this database.

Don't treat `google-smoke` as the thing that makes your sources `ok`. It records
success only for a source whose read came back complete, and a partial read
stores the `degraded` error code instead, which shows up as status `error`. Use
the watcher for a clean, repeatable pass:

```powershell
& $pm daemon --once
Write-Output "exit=$LASTEXITCODE"
& $pm status
```

Success: `daemon --once` exits 0, and `status` now reports
`google.gmail.status` and `google.calendar.status` as `"ok"` with a numeric
`age_seconds` on each. `overall` stays `"degraded"` while the only remaining
warning is the daemon one, since `--once` runs a single pass and exits rather
than staying live. That single daemon warning is the expected end state for A4b.

Failure: a source still `never_synced` after a clean pass, or a status of
`error` with an `error_code`. Report the exit code and the `error_code` string.
A `needs_reauth` here means the authorization was revoked, so rerun A3 with
`--reauth`.

Now run the ACL checks in the [Explorer](#explorer) and
[PowerShell ACL](#powershell-acl) sections against
`%USERPROFILE%\.proactive-mcp`. Same expectations as the Owner smoke: protected
DACL, no inherited entries, your account as the only Allow principal.

### Owner reference A5. Register the wheel with both CLIs

Follow the agent-led instructions in
[`docs/testers/windows.md`](testers/windows.md). Copy its **에이전트에게 붙여
넣기** block into the agent you are using for this alpha. It installs the wheel,
runs setup and the real-read check, and registers `serve` plus
`serve-scheduled` with that agent.

Don't run `grok mcp add`, `codex mcp add`, or edit an MCP JSON or config file
yourself. Registration belongs to the agent because it knows its own MCP
configuration and approval flow. The paths it records must be absolute.

Success: the agent confirms its registration, `google-smoke` and the one-shot
daemon pass, and `status` reports healthy database state with both Google
sources `ok`. Keep approval prompts on the full profile. Only the restricted
scheduled profile may be auto-approved.

Failure: stop at the failed step and report the sanitized error and exit code.
Don't work around a headless, credential, or server-spawn failure by editing
the agent's config. The tester sheet covers the supported recovery path.

### Owner reference A6. Agent validation

The agent-led sheet ends by asking the registered agent for `get_status`. Check
that its reply names only the redacted `.proactive-mcp\proactive.db` path and
the Gmail and Calendar statuses. A `receipt_token` must be confirmed once
before the agent presents any situations.

Success: the agent makes the tool call, the database is healthy at migration 9,
and Gmail and Calendar are `ok`. `overall=degraded` is still acceptable when
the daemon is not running.

This closed-alpha path validates the agent you use every day, not an Owner M5
two-CLI sign-off. Don't create synthetic memories, run unattended scheduler
wrappers, or try to register a second CLI. The Owner-only smoke above retains
the two-platform, headless delivery checks.

Failure: no tool call, a server-spawn error, a source other than `ok`, or any
credential prompt that cannot be completed interactively. Report only the
sanitized error and status fields listed in the tester sheet.

### Owner reference A7. What to report, and what to leave out

Time the run from A1 to the end of A6, A4b included, and report that number. Fifteen minutes is
the target; a slower run is useful information, not a failure.

Send the Owner:

1. Which step failed, if any, and its exit code
2. Windows edition and build, plus `$PSVersionTable.PSVersion`
3. `uv --version`, `grok --version`, `codex --version`, and the wheel filename
4. Redacted `status` fields: `overall`, `database.status`,
   `database.journal_mode`, `database.migration_version`, `google.gmail.status`,
   `google.calendar.status`, `daemon.status`, and the `warnings` strings
   verbatim. Cut `database.path` down to `.proactive-mcp\proactive.db`
5. The `google-smoke` line from A4b: both counts, both `error_code` values, and
   `credential_cleanup_failed`
6. Tool names and counts only, plus `items` length wherever a tool returns
   `items`
7. One sanitized error line per failure, with paths shortened to
   `%USERPROFILE%\...` and any identifier or token replaced by `<redacted>`
8. Which onboarding step confused you, and where you had to guess. That part
   only a tester can tell us

Leave all of this out, every time: the database and its `-wal`, `-shm`, and
`.init.lock` sidecars, `config.toml`, `client_secret.json`, tokens or anything
else from a credentials directory, mail and calendar content of any kind, memory
`content`, entity names, dates, situation `title` / `why_now` / `evidence`, raw
CLI logs, your CLI MCP config files, and screenshots.

If a problem seems impossible to describe without one of those, say exactly that
and stop. Someone will work out a safe way to get the detail.

### Owner reference A8. Putting the machine back

None of this is destructive on its own, but do undo what you changed:

```powershell
grok mcp remove --scope user proactive 2>$null
grok mcp remove --scope user proactive_scheduled 2>$null
codex mcp remove proactive 2>$null
codex mcp remove proactive_scheduled 2>$null
grok mcp list
codex mcp list --json
```

If a CLI had neither profile before you started, an empty listing now is the
correct end state. Don't add a default entry just to have one. If it did have
one, run the private restore command you saved in A5, then check that the
listing points at your own path again.

Keeping the alpha install is fine and expected. To remove only the code, delete
the virtualenv at `%USERPROFILE%\venvs\proactive`.

For full removal, delete the stored credential before deleting its authority
marker. A keyring credential lives outside `.proactive-mcp`; deleting the
directory first can make a stale keyring value look like a legacy credential
on reinstall.

```powershell
$python = Join-Path $env:USERPROFILE "venvs\proactive\Scripts\python.exe"
& $python -c 'from pathlib import Path; from proactive_mcp.sources.credentials import CredentialStore; CredentialStore(Path.home() / ".proactive-mcp").delete()'
if ($LASTEXITCODE -ne 0) { throw "Credential deletion failed; keep the state directory." }
Remove-Item -Recurse -Force (Join-Path $env:USERPROFILE ".proactive-mcp")
Remove-Item -Recurse -Force (Join-Path $env:USERPROFILE "venvs\proactive")
```

If credential deletion fails, leave the state directory and tombstone in
place, revoke the app in Google Account permissions, and report the failure to
the Owner. Do not remove the tombstone and expose a stale keyring entry to
legacy migration.

</details>
