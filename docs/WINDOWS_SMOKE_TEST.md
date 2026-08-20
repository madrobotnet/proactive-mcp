# Windows Owner smoke test (M1.5)

Run this on Windows with PowerShell and Cursor. Paste the commands as written. Don't install from PyPI: this repo is private and `proactive-mcp` is not published. `uvx proactive-mcp` and `pip install proactive-mcp` are the wrong path.

macOS is CI-only (`macos-latest` green). There is no Owner Mac, so don't run these steps on macOS. Real-device Mac coverage waits for a closed-alpha tester who has one.

Do not set `PROACTIVE_DATABASE`. The point of this smoke is the default file under `%USERPROFILE%\.proactive-mcp\proactive.db`.

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

### 2. Clone the private repo

```powershell
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\src" | Out-Null
Set-Location "$env:USERPROFILE\src"
git clone https://github.com/madrobotnet/proactive-mcp.git
Set-Location proactive-mcp
git checkout m1-5-cross-platform-storage
```

Use `m1-5-cross-platform-storage` while that PR is open. After merge, check out `main` instead. If the review comment names another branch or SHA, use that.

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
- JSON `overall` is `"degraded"`

`degraded` is the expected M1.5 result. Google sources and the daemon are not in this milestone. Treat `"Google sources are not configured."` and `"Daemon is not running; status is degraded."` as pass, not fail.

Failure: non-zero exit, no JSON, `database.status` not `healthy`, or `path` pointing somewhere else.

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

Success: `proactive` is enabled and lists `get_status`, `remember`, `recall`, `forget`.

Failure: red server, zero tools, or spawn error. Copy the MCP error text. Confirm `command` in `mcp.json` is the `uv.exe` path from step 5.

### 8. Agent mode connectivity check

Start a **new** Agent chat (not Ask). Paste:

```
Call the get_status tool from the proactive MCP server. Don't guess the result.
```

Success: you see a `get_status` tool call, `database.status` is `"healthy"`, and `path` ends with `.proactive-mcp\proactive.db`.

Failure: the model answers without a tool call, the tool errors, or tools are missing. Stop here. Don't continue the memory scenarios until this passes.

## 시나리오 목록

Use Agent mode every time. A reply with no tool call is a fail, even if the text sounds right. Write down the numeric memory `id` from scenario 1. You'll need it later.

### Scenario 1: `remember`

New Agent chat. Paste:

```
Use the remember tool now with these exact arguments:
kind=person_fact
entity=mother
content=엄마 생신
date_anchor=--07-18
recurrence=yearly
Don't skip the tool call.
```

Success:

- Cursor shows a `remember` tool call
- Result JSON has `"kind": "person_fact"`, `"entity": "mother"`, `"content": "엄마 생신"`, `"date_anchor": "--07-18"`, `"recurrence": "yearly"`, `"archived": false`
- `"id"` is a positive integer. Record it.

Failure:

- No `remember` call
- Tool error
- Different `kind` / `entity` / `content` / `date_anchor` / `recurrence`
- `"archived": true`

### Scenario 2: new-session `recall`

Close that chat. Open a **new** Agent chat. Don't continue the old thread. Paste:

```
Use the recall tool with query 엄마. Don't answer from chat history. Call the tool.
```

Success:

- You see a `recall` tool call
- JSON `items` has one entry
- That entry's `id`, `content`, and `date_anchor` match scenario 1

Failure:

- No `recall` call (the model recites the old chat instead)
- `"items": []`
- Wrong `id` or content

### Scenario 3: `forget`, then recall is empty

Still in a new Agent chat (the scenario 2 chat is fine). Replace `ID` with the number from scenario 1. Paste:

```
Use the forget tool with id=ID. Then call recall with query 엄마. Don't skip either tool.
```

Success:

- `forget` result is `{"id": ID, "archived": true}` (whitespace may differ)
- The following `recall` result has `"items": []`

Failure:

- `forget` errors
- `recall` still returns the mother item
- Only one of the two tools ran

### Scenario 4: file at the documented path

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

Comment on the M1.5 PR or on Issue #10. Don't only say "it failed." Attach:

1. Scenario number and name
2. The exact text you pasted into Cursor or PowerShell
3. What happened: tool name, tool JSON, or the error string. A screenshot of the chat with the tool call visible helps.
4. Full `uv run proactive-mcp status` stdout and stderr
5. Versions:

```powershell
uv --version
uv run python --version
$PSVersionTable.PSVersion
[System.Environment]::OSVersion.VersionString
Get-Command uv | Format-List Source, Version
```

6. `%USERPROFILE%\.cursor\mcp.json` (you can delete other MCP server entries before pasting)
7. The `icacls` / `Get-Acl` output from 확인 포인트, even if the failure was a tool call
8. Cursor MCP error text from Settings > MCP. If the server never started, also grab the newest files under `%APPDATA%\Cursor\logs` (and `%USERPROFILE%\.cursor\logs` if that folder exists). Paste the MCP-related lines, not the whole log tree.

Don't send Google OAuth tokens or mailbox contents. M1.5 has neither. You can attach `proactive.db` if it only contains this smoke data.

If every scenario passed, comment that 1 to 4 passed and include the `status` JSON `path` plus the `icacls` listing. That is enough success evidence.
