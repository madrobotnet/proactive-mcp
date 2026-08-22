# Integration recipes (M5)

How to wire `proactive-mcp` into a local agent so the agent speaks first: Grok CLI, Codex CLI, Hermes, and Claude Code Desktop, plus OS scheduler handoff for the CLIs that have no scheduler of their own.

Everything here targets the **private alpha**, which means the source tree. The package is not on PyPI, so every command runs the checkout through `uv run --directory <absolute path>`. If you see `uvx proactive-mcp` in older notes or in `docs/PRODUCT_PLAN.md` §12, that's the post-publication path, not today's.

**Verified on:** uv 0.11.29, grok 0.2.112, codex-cli 0.149.0, Hermes Agent v0.20.0, Linux (aarch64). Claude Code Desktop was not installed in the verification environment, so that section is marked and sourced accordingly.

## Contents

- [Scope](#scope)
- [What every platform needs](#what-every-platform-needs)
- [Step 0: prerequisites, once per machine](#step-0-prerequisites-once-per-machine)
  - [Getting the code during the private alpha](#getting-the-code-during-the-private-alpha)
  - [Google OAuth client secret, before you run setup](#google-oauth-client-secret-before-you-run-setup)
- [The session-start rule](#the-session-start-rule)
- [Grok CLI](#grok-cli)
- [Codex CLI](#codex-cli)
- [Hermes (Native Cron)](#hermes-native-cron)
- [Claude Code Desktop (documentation only)](#claude-code-desktop-documentation-only)
- [OS scheduler handoff](#os-scheduler-handoff)
  - [Linux and macOS: cron](#linux-and-macos-cron)
  - [Windows: Task Scheduler](#windows-task-scheduler)
- [Log privacy rules](#log-privacy-rules)
- [Verification checklist](#verification-checklist)

## Scope

A platform can deliver proactive messages when two things are true, per `docs/PRODUCT_PLAN.md` §5.3: it can launch a **local stdio** process, and it has a **schedule trigger**. MCP support alone isn't enough.

| Platform | Local stdio | Schedule trigger | Status in this guide |
|---|---|---|---|
| Grok CLI | Yes | None, use OS scheduler | Full recipe |
| Codex CLI | Yes | None, use OS scheduler | Full recipe |
| Hermes | Yes | Native Cron | Full recipe |
| Claude Code Desktop | Yes | Local scheduled tasks, one-minute minimum | Documentation only, not demonstrated |

### Deliberately out of scope

Don't try these; the blocker is the platform, not our code.

- **ChatGPT web and desktop.** Web supports remote HTTPS connectors only. Desktop advertises stdio but the tools don't surface in the chat session ([openai/codex#38162](https://github.com/openai/codex/issues/38162)). Blocked until V2 HTTP transport.
- **Claude cloud Routines.** They run in Anthropic's cloud, so they have no path to a local process or the local SQLite file. A Routine can fire on schedule and still never reach this server. This is *not* the same thing as a Claude Code Desktop local scheduled task, which does work. See the contrast table in the [Claude section](#claude-code-desktop-documentation-only).
- **Cursor.** Removed from the supported set by Owner decision on 2026-08-22 ([#20](https://github.com/madrobotnet/proactive-mcp/issues/20)). Its Automations run as cloud agents, and a cloud agent can't spawn the local stdio process or read the local SQLite database, so there is no scheduled path to this server. Use Grok CLI or Codex CLI with the OS scheduler instead.
- **HTTP transport.** V1 speaks stdio only. Any recipe that points an agent at a URL is wrong for this version.
- **Write actions.** V1 is read-only: `gmail.readonly` and `calendar.readonly`, nothing else. No recipe here should ask an agent to send mail, create events, or modify anything. Write actions arrive in V2 behind an approval-first contract.

## What every platform needs

Each recipe below follows the same four steps, in this order:

1. **Prerequisites and local stdio MCP registration.** Point the agent at the checkout.
2. **Watcher daemon, or the degraded-mode alternative.** The daemon is recommended, not required. Without it you lose OS notification fallback.
3. **A session-start rule** that calls `proactive_check` exactly once.
4. **A scheduled trigger**, native where the platform has one, OS scheduler where it doesn't.

Then verification and troubleshooting, which differ per platform.

## Step 0: prerequisites, once per machine

You need Python ≥3.11, [uv](https://docs.astral.sh/uv/), and a copy of this project. Pick a stable absolute path and use it everywhere; schedulers and agent config files can't expand `~` reliably.

Throughout this document:

- Linux/macOS checkout: `/home/you/src/proactive-mcp`
- Windows checkout: `C:\Users\you\src\proactive-mcp`

### Getting the code during the private alpha

The repository stays private for the whole closed-alpha period, and the package is not on PyPI (`docs/PRODUCT_PLAN.md` §12). Two supported ways to get it, and they change the commands you'll type later, so pick one now.

**Option A, git checkout.** You need collaborator access with at least Read permission on `madrobotnet/proactive-mcp`. Ask the Owner for the invitation and accept it before you clone. An unauthenticated clone of a private repository fails with `Repository not found` or a `403`, which reads like a typo but is really an access problem.

```bash
gh auth login
gh auth status
gh repo clone madrobotnet/proactive-mcp /home/you/src/proactive-mcp
cd /home/you/src/proactive-mcp
uv python install 3.11
uv sync --locked
uv run proactive-mcp --help
```

If `gh auth status` says you're logged out, or the token is missing the `repo` scope, fix it with `gh auth login --scopes repo` and try again. Plain `git clone https://github.com/madrobotnet/proactive-mcp.git` works too, once `gh auth setup-git` has installed the credential helper.

**Option B, wheel file.** `docs/PRODUCT_PLAN.md` §12 makes this the preferred tester path, precisely because it needs no repository access. The Owner hands you a `.whl`, you install it into a virtualenv you control, and no checkout ever lands on your disk.

```bash
uv venv /home/you/venvs/proactive
uv pip install --python /home/you/venvs/proactive/bin/python /path/to/proactive_mcp-<version>-py3-none-any.whl
/home/you/venvs/proactive/bin/proactive-mcp --help
```

The wheel changes every command in this guide in exactly one way: **there is no `uv run` and no `--directory`.** Wherever a recipe says

```bash
uv run --directory /home/you/src/proactive-mcp proactive-mcp serve
```

the wheel equivalent is the absolute path to the installed console script:

```bash
/home/you/venvs/proactive/bin/proactive-mcp serve
```

In an MCP registration that means `"command"` becomes that absolute path and `"args"` shrinks to `["serve"]`. For `codex mcp add` and `grok mcp add`, the part after `--` becomes that path plus `serve`. On Windows the script is `C:\Users\you\venvs\proactive\Scripts\proactive-mcp.exe`. Everything else here, rules, daemon, schedulers, verification, is unchanged. The rest of this guide is written for Option A because that's what the verification environment ran; translate as above if you're on a wheel.

### Google OAuth client secret, before you run setup

`setup` runs the Google OAuth flow, and it can't start without an installed-app OAuth client secret file. During the closed alpha, the default path is the Owner-provided client JSON delivered alongside the wheel or checkout instructions (`docs/PRODUCT_PLAN.md` §12). Keep that file local and put it where `setup` will look. Testers explicitly assigned to validate the BYO path instead create an OAuth client of type **Desktop app** in their own Google Cloud project and use that downloaded JSON.

`setup --help` on this build:

```text
proactive-mcp setup [-h] [--reauth] [--headless] [--client-secrets PATH]
```

Resolution order for the secret file:

1. `--client-secrets PATH`, when you pass it.
2. The `PROACTIVE_GOOGLE_CLIENT_SECRETS` environment variable.
3. Default: `client_secret.json` beside the state database, so `~/.proactive-mcp/client_secret.json` on Linux and macOS.

Taking the default is simplest:

```bash
mkdir -p ~/.proactive-mcp
chmod 700 ~/.proactive-mcp
cp ~/Downloads/client_secret_*.json ~/.proactive-mcp/client_secret.json
chmod 600 ~/.proactive-mcp/client_secret.json
```

On Windows, copy the delivered file to
`%USERPROFILE%\.proactive-mcp\client_secret.json`. Do not commit either the
Owner-provided file or a BYO file, and never paste one into an issue.

Or keep it wherever you like and point at it:

```bash
uv run --directory /home/you/src/proactive-mcp proactive-mcp setup \
  --client-secrets /home/you/secrets/proactive-client.json
```

Add `--headless` when the machine has no browser for the loopback authorization page, which is the usual case on a remote or server box. `--reauth` replaces an existing authorization, which is what you want after revoking access or changing scopes.

Now connect the read-only Google sources and confirm local state:

```bash
uv run --directory /home/you/src/proactive-mcp proactive-mcp setup
uv run --directory /home/you/src/proactive-mcp proactive-mcp status
```

`status` prints JSON. A fresh machine reports `"overall":"degraded"` with `not_configured` sources until `setup` completes, which is expected.

The stdio server command every agent will register is:

```bash
uv run --directory /home/you/src/proactive-mcp proactive-mcp serve
```

Note the absolute paths for `uv` and your agent binaries now, since schedulers get a minimal `PATH`:

```bash
command -v uv grok codex hermes
```

The CLI surface is small. Confirm it yourself with `uv run proactive-mcp --help`:

```text
proactive-mcp {serve,status,setup,google-smoke,daemon}
proactive-mcp daemon [--once] [--poll-interval-minutes MINUTES]
```

`proactive_check` is an **MCP tool**, not a subcommand. You can't call it from a shell; an agent has to call it.

## The session-start rule

This is the same text on every platform. Copy it verbatim into the platform's rule, system prompt, or scheduled prompt.

```text
At the start of every new session, call the MCP tool proactive_check exactly once,
before you answer the user. Call it once per session and no more, unless the user
explicitly asks for a fresh proactive check.

If it returns situations, lead your reply with a short, natural summary of them.
If it returns freshness warnings, say the result may be incomplete. Never report
"nothing to report" while a source is stale or failed.
If it returns nothing and freshness is healthy, say nothing about it and answer
the user's actual request.
```

Why once: `proactive_check` takes no arguments, and a successful call atomically claims the returned situations as `delivered` (§5.1). The server dedupes, so repeat calls won't spam the user, but they do cost a round trip on every turn for no benefit.

## Grok CLI

Verified against grok 0.2.112.

### 1. Registration: often zero config, but confirm it

Per `docs/PRODUCT_PLAN.md` §5.3 and [Issue #6](https://github.com/madrobotnet/proactive-mcp/issues/6), Grok automatically loads MCP configuration written for other harnesses, which is why the plan describes Grok setup as effectively zero extra configuration.

Treat it as a claim to check on your machine, not a guarantee. On grok 0.2.112, `grok mcp doctor` prints the config sources it consults, and the sources it visibly lists are `~/.grok/config.toml`, `~/.claude.json`, and a project-level `.mcp.json`. So an existing Claude registration, or a project-level `.mcp.json` in the directory you're working in, can be enough on its own. Anything outside those three files is not.

So always ask Grok directly instead of assuming:

```bash
grok mcp doctor
grok mcp doctor proactive
```

If `grok mcp doctor proactive` reports the server, you're done, nothing to add. If it doesn't, fall back to registering it in Grok's own config with `grok mcp add`. User scope writes `~/.grok/config.toml` and applies everywhere:

```bash
grok mcp add --scope user proactive -- \
  /home/you/.local/bin/uv run --directory /home/you/src/proactive-mcp proactive-mcp serve
```

Project scope writes `./.grok/config.toml` instead:

```bash
grok mcp add --scope project proactive -- \
  /home/you/.local/bin/uv run --directory /home/you/src/proactive-mcp proactive-mcp serve
```

Everything after `--` is the server command, per `grok mcp add --help`. Prefer user scope: repo-local servers only start in folders you've marked trusted, and user-scope servers sidestep that entirely.

### 2. Watcher daemon, or degraded mode

```bash
uv run --directory /home/you/src/proactive-mcp proactive-mcp daemon
```

Degraded alternative: skip the daemon and let `proactive_check` lazy-sync inline. Same limitation as everywhere else, no daemon means no OS notification fallback, so an uncollected critical situation never reaches you through any other channel.

### 3. Session-start rule

Put [the rule](#the-session-start-rule) in `AGENTS.md` at your project root; Grok loads project instructions from there. For a single run you can append rules on the command line with `--rules`.

### 4. Scheduled trigger: none, hand off to the OS

Grok has no scheduler. Use `-p/--single` for a one-shot headless prompt:

```bash
grok --single 'Call the proactive_check MCP tool exactly once. Report any returned situations and freshness warnings; otherwise state that there are no actionable situations.'
```

For machine-readable output add `--output-format json` (values: `plain`, `json`, `streaming-json`). Full scheduler wiring is in [OS scheduler handoff](#os-scheduler-handoff).

### Verification

```bash
grok mcp list
grok mcp doctor proactive
grok mcp doctor --json proactive
```

`doctor` should report the server healthy. Then run one interactive session and confirm a single `proactive_check` call.

### Troubleshooting

Grok writes MCP stderr to `~/.grok/logs/mcp/`. Start there when a launch fails.

| Symptom | Cause and fix |
|---|---|
| `folder untrusted (repo-local (project-scoped) server not started for an untrusted folder)` | A project-scoped server in a folder Grok doesn't trust. Trust the folder in an interactive `grok` session, or re-register with `--scope user`. |
| Server not listed at all | Run `grok mcp doctor`. It names the config sources Grok consults (`~/.grok/config.toml`, `~/.claude.json`, project `.mcp.json`) and how many servers each one contributed. If your registration lives anywhere else, Grok won't see it, so re-register with `grok mcp add --scope user`. |
| Launch fails immediately | Check `~/.grok/logs/mcp/`. Usually `uv` isn't on `PATH`; use the absolute path in the command. |
| Doctor reports auth expired | `grok login`. Config-source diagnostics still work without it. |
| Headless run produces no tool call | Make the prompt itself name the tool. Rules may not load in every headless context; the prompt always does. |

## Codex CLI

Verified against codex-cli 0.149.0.

### 1. Registration

```bash
codex mcp add proactive -- \
  uv run --directory /home/you/src/proactive-mcp proactive-mcp serve
```

The syntax is `codex mcp add [OPTIONS] <NAME> (--url <URL> | -- <COMMAND>...)`. Use the `--` form; `--url` is HTTP transport, which V1 doesn't support.

This writes `~/.codex/config.toml` (or `$CODEX_HOME/config.toml` when `CODEX_HOME` is set). You can also write it by hand:

```toml
[mcp_servers.proactive]
command = "uv"
args = [
  "run",
  "--directory",
  "/home/you/src/proactive-mcp",
  "proactive-mcp",
  "serve",
]
```

Windows, in `%USERPROFILE%\.codex\config.toml`, with escaped backslashes:

```toml
[mcp_servers.proactive]
command = "C:\\Users\\you\\.local\\bin\\uv.exe"
args = [
  "run",
  "--directory",
  "C:\\Users\\you\\src\\proactive-mcp",
  "proactive-mcp",
  "serve",
]
```

Confirm what actually landed on disk with `codex mcp get proactive`, and treat that output as authoritative over the snippet above.

### 2. Watcher daemon, or degraded mode

```bash
uv run --directory /home/you/src/proactive-mcp proactive-mcp daemon
```

Degraded alternative: no daemon, inline lazy sync on `proactive_check`, and **no OS notification fallback**. `get_status` reports `daemon.status` as `not_running` plus the warning, so the agent can tell you the fallback path is dark.

### 3. Session-start rule

Codex has no automatic session hook for this. Put [the rule](#the-session-start-rule) in `AGENTS.md` at the repository or workspace root, and repeat the instruction inside any scheduled prompt.

### 4. Scheduled trigger: none, hand off to the OS

`codex exec` runs non-interactively:

```bash
codex exec \
  --ephemeral \
  --sandbox read-only \
  --skip-git-repo-check \
  -C /home/you/src/proactive-mcp \
  'Call the proactive_check MCP tool exactly once. Report its situations, freshness, and warnings concisely. Do not modify files and do not run unrelated commands.'
```

Flags, all from `codex exec --help`: `--ephemeral` skips persisting session files, `-s/--sandbox read-only` blocks writes, `-C/--cd` sets the working root, `--skip-git-repo-check` allows non-repo directories, `--json` emits JSONL events, `-o/--output-last-message FILE` writes the final message to a file. Read-only sandbox is the right choice here, since V1 never needs to write anything.

Scheduler wiring is in [OS scheduler handoff](#os-scheduler-handoff).

### Verification

```bash
codex mcp list
codex mcp get proactive
codex doctor
```

Then start an interactive `codex` session and confirm exactly one `proactive_check` call before the first answer.

### Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Server missing from `codex mcp list` | You edited the wrong file. Check whether `CODEX_HOME` is set; that overrides `~/.codex`. |
| `config.toml` rejected | Run with `--strict-config` to surface unrecognized fields, and check TOML backslash escaping on Windows. |
| Server starts, tools never used | Name `proactive_check` explicitly in the prompt. Rule files are advisory. |
| `codex exec` fails outside a git repo | Add `--skip-git-repo-check`. |
| Agent tries to write something | Keep `--sandbox read-only`. V1 exposes no Google or external write actions, so a write attempt means the prompt drifted. |

## Hermes (Native Cron)

Verified against Hermes Agent v0.20.0. MCP registration was checked against `hermes mcp add --help`, the cron flags against `hermes cron create --help`.

### 1. Registration

```bash
hermes mcp add proactive \
  --command uv \
  --args run --directory /home/you/src/proactive-mcp proactive-mcp serve
```

`--args` must be the last option, since it consumes the rest of the line. This writes into `~/.hermes/config.yaml` under `mcp_servers`:

```yaml
mcp_servers:
  proactive:
    command: uv
    args: ["run", "--directory", "/home/you/src/proactive-mcp", "proactive-mcp", "serve"]
```

Narrow the tool surface with `hermes mcp configure proactive` if you'd rather expose only `proactive_check` and the memory tools.

### 2. Watcher daemon, or degraded mode

```bash
uv run --directory /home/you/src/proactive-mcp proactive-mcp daemon
```

Degraded alternative: run only the MCP server. `proactive_check` lazy-syncs inline when data is old, so scheduled Hermes runs still deliver. **Limitation:** no OS notification fallback, so nothing catches a critical situation that Hermes never picks up. A short cron interval softens this, because the job itself is a reliable collector, but a collector is not a fallback.

### 3. Session-start rule

Add [the rule](#the-session-start-rule) to your Hermes system prompt, or to the `AGENTS.md` of the directory you pass as `--workdir`. Hermes injects `AGENTS.md` and `CLAUDE.md` from that directory into the job.

### 4. Scheduled trigger: Native Cron

```bash
hermes cron create "every 15m" \
  "Call the MCP tool proactive_check exactly once. If it returns situations, report each one clearly in this channel, including freshness and warnings. If freshness is stale or warnings indicate a source problem, say so instead of reporting that nothing is pending. If nothing is returned and freshness is healthy, report that there are no pending situations. Do not call any write action." \
  --name "proactive check" \
  --workdir /home/you/src/proactive-mcp
```

The schedule argument accepts `30m`, `every 2h`, or plain cron syntax like `0 9 * * *`. Useful flags from `hermes cron create --help`: `--deliver` picks the delivery target (`origin`, `local`, `telegram`, `discord`, `signal`, or `platform:chat_id`), `--name` labels the job, `--model` and `--provider` pin inference. Don't use `--no-agent`: that skips the LLM and runs a script instead, and no shell command can call an MCP tool.

### Verification

```bash
hermes mcp test proactive
hermes cron list
hermes cron status
hermes cron run <JOB_ID>
hermes cron runs <JOB_ID> --limit 5
```

`hermes cron run` queues the job for the next scheduler tick; `hermes cron tick` runs due jobs once and exits, which is the fastest way to test without waiting. Then check `runs` for a successful attempt, and confirm the message actually arrived in your channel.

### Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `hermes mcp test proactive` fails | Wrong `--directory`, or `uv` missing from Hermes' `PATH`. Use the absolute `uv` path. |
| Job listed but never runs | `hermes cron status` tells you whether the scheduler is running. Also check the job isn't paused: `hermes cron resume <JOB_ID>`. |
| Job runs, no message arrives | Delivery target. Re-create with an explicit `--deliver`, or read `hermes cron runs` for the attempt outcome. |
| Tool never called | The prompt must be self-contained and name `proactive_check`. A cron job carries no conversation history. |
| Duplicate notifications | Shouldn't happen, since `proactive_check` marks situations `delivered`. If it does, you probably have two jobs, or another agent is also collecting. Check `hermes cron list`. |

## Claude Code Desktop (documentation only)

> **Not demonstrated.** Per `docs/PRODUCT_PLAN.md` §5.3 and [Issue #6](https://github.com/madrobotnet/proactive-mcp/issues/6), Claude Code Desktop is a documentation deliverable for M5. The Owner doesn't have it installed and neither did the verification environment, so nothing below was executed. File locations come from [Anthropic's local MCP guide](https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop), and the trigger steps come from the official [Desktop scheduled tasks guide](https://code.claude.com/docs/en/desktop-scheduled-tasks).

### 1. Registration

Edit the Desktop config file directly:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "proactive": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/Users/you/src/proactive-mcp",
        "proactive-mcp",
        "serve"
      ]
    }
  }
}
```

Desktop launches from the GUI, so it often doesn't inherit your shell `PATH`. Use an absolute `uv` path (`/opt/homebrew/bin/uv`, or `C:/Users/you/.local/bin/uv.exe`) if the server won't start. Restart Claude Desktop fully after editing, then confirm the `proactive` server lists `proactive_check`.

### 2. Watcher daemon, or degraded mode

```bash
uv run --directory /Users/you/src/proactive-mcp proactive-mcp daemon
```

Degraded alternative: no daemon, inline lazy sync only, and **no OS notification fallback**. Worth stating plainly here, because Desktop's local scheduled tasks bottom out at one minute, which tempts people to treat frequent tasks as a fallback substitute. They aren't. A scheduled task delivers into a conversation; the fallback is an OS toast that fires when no agent collected a critical situation within the configured window (30 minutes by default, §7).

### 3. Session-start rule

Put [the rule](#the-session-start-rule) in the project instructions or custom instructions for the project where you use this server.

### 4. Scheduled trigger: local scheduled task

Create a **local** scheduled task in Claude Code Desktop:

- Prompt: call `proactive_check` exactly once; present returned situations and freshness warnings, and do not claim an all-clear while a source is stale.
- Recurrence: your choice, minimum one minute. One minute is the floor documented in §5.3; something like 15 minutes is saner in practice.
- Execution: local, on this machine, with the `proactive` MCP server enabled.

In the **Code** tab, open **Routines**, click **New routine**, and choose
**Local**. Select the working folder that contains the MCP configuration, paste
the prompt, and choose the schedule. Desktop checks local schedules once per
minute while the app is open and the machine is awake; a sleeping or closed
machine does not run the task. Use **Run now** once to approve
`proactive_check`, then confirm future runs do not stall for permission.

### Local Desktop tasks vs. Claude cloud Routines

Two different products with the same vibe, and confusing them is the most likely mistake in this whole document.

| | Desktop local scheduled task | Claude cloud Routine |
|---|---|---|
| Where it runs | Your machine | Anthropic's cloud |
| Can reach a local stdio MCP server | Yes | No |
| Can read `~/.proactive-mcp/proactive.db` | Yes | No |
| Works with proactive-mcp V1 | Yes, in principle | No |
| Status | M5 documentation path | Blocked on V2 HTTP transport |

A Routine will happily run on schedule and accomplish nothing here, because its cloud process can't spawn or connect to a process on your laptop. Don't offer it as an alternative trigger.

### Verification

1. Restart Desktop, open a conversation, confirm `proactive` appears with `proactive_check` in the tool list.
2. Ask the agent to call `get_status`. Compare against `uv run --directory /Users/you/src/proactive-mcp proactive-mcp status` in a terminal; the database path and migration version should match.
3. Create a one-minute task, wait two minutes, confirm exactly one call per run and no duplicate delivery of the same situation.

### Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Server doesn't appear after editing config | Invalid JSON, or Desktop wasn't fully quit. Validate the file, quit completely, reopen. |
| Server appears, fails to start | GUI `PATH`. Use an absolute `uv` path. |
| Task runs but no MCP tools are available | The task is running in the cloud, not locally. Recreate it as a local task. |
| Task can't be created below one minute | That's the documented floor. Use the daemon plus fallback for anything more urgent. |

## OS scheduler handoff

Grok CLI and Codex CLI have no scheduler, so the OS supplies one. A scheduled
run must do more than claim a situation: it must make the result visible.
Otherwise `proactive_check` marks the situation `delivered`, the discarded
response becomes its only delivery, and the user never sees it.

The wrappers below use a narrow boundary:

1. Capture the agent's final stdout in process memory only.
2. Accept exactly `PROACTIVE_ATTENTION` or `PROACTIVE_NONE`.
3. Translate `PROACTIVE_ATTENTION` into a fixed, PII-free native OS
   notification. Never pass agent output into the notification.
4. Persist only a timestamp, CLI name, fixed result/reason, notification status,
   and exit code.

The scheduled prompt maps either a non-empty `situations` array or any freshness
warning to `PROACTIVE_ATTENTION`; healthy empty results map to
`PROACTIVE_NONE`. Unexpected output is a failure, not text to salvage. After an
attention or failure notification, open the named agent and inspect recent
delivered situations with `list_situations(state=delivered)` and source
freshness with `get_status`;
`proactive_check` will not return an already claimed situation.

### Linux and macOS: cron

Create `~/bin/proactive-cron-grok`:

```sh
#!/bin/sh
set -u

PATH="/home/you/.local/bin:/home/you/.grok/bin:/usr/local/bin:/usr/bin:/bin"
REPO="/home/you/src/proactive-mcp"
LOGDIR="$HOME/.proactive-mcp/logs"
LOG="$LOGDIR/grok-cron.log"
PROMPT='Call the proactive_check MCP tool exactly once and call no other tool. Your entire final response must be exactly PROACTIVE_ATTENTION if situations is non-empty or warnings is non-empty. Otherwise it must be exactly PROACTIVE_NONE. Output one token only, with no Markdown, punctuation, explanation, situation content, title, why_now, evidence, names, dates, mail, or calendar text.'

mkdir -p "$LOGDIR"
chmod 700 "$LOGDIR"

PLATFORM=$(uname -s)
case "$PLATFORM" in
  Linux)
    command -v notify-send >/dev/null 2>&1 || {
      printf '%s cli=grok result=failure reason=notifier_unavailable notify=none exit=3\n' "$(date -u +%FT%TZ)" >>"$LOG"
      exit 3
    }
    ;;
  Darwin)
    command -v osascript >/dev/null 2>&1 || {
      printf '%s cli=grok result=failure reason=notifier_unavailable notify=none exit=3\n' "$(date -u +%FT%TZ)" >>"$LOG"
      exit 3
    }
    MACOS_SCRIPT=$(uv run --directory "$REPO" python -c 'from proactive_mcp.delivery.notify import MACOS_NOTIFICATION_SCRIPT; print(MACOS_NOTIFICATION_SCRIPT)')
    [ -f "$MACOS_SCRIPT" ] || {
      printf '%s cli=grok result=failure reason=notifier_unavailable notify=none exit=3\n' "$(date -u +%FT%TZ)" >>"$LOG"
      exit 3
    }
    ;;
  *)
    printf '%s cli=grok result=failure reason=notifier_unavailable notify=none exit=3\n' "$(date -u +%FT%TZ)" >>"$LOG"
    exit 3
    ;;
esac

notify_fixed() {
  if [ "$PLATFORM" = "Linux" ]; then
    notify-send -- "$1" "$2"
  else
    osascript "$MACOS_SCRIPT" "$1" "$2"
  fi
}

RAW=$(grok --cwd "$REPO" --no-alt-screen --single "$PROMPT" 2>/dev/null)
CODE=$?

if [ "$CODE" -ne 0 ]; then
  if notify_fixed "Proactive check failed" "Open Grok to retry or inspect proactive status." >/dev/null 2>&1; then
    NOTIFY=sent
  else
    NOTIFY=failed
  fi
  printf '%s cli=grok result=failure reason=agent_failed notify=%s exit=%s\n' "$(date -u +%FT%TZ)" "$NOTIFY" "$CODE" >>"$LOG"
  exit "$CODE"
fi

case "$RAW" in
  PROACTIVE_NONE)
    printf '%s cli=grok result=none notify=none exit=0\n' "$(date -u +%FT%TZ)" >>"$LOG"
    ;;
  PROACTIVE_ATTENTION)
    if notify_fixed "Proactive alert" "Open Grok to review proactive status." >/dev/null 2>&1; then
      printf '%s cli=grok result=attention notify=sent exit=0\n' "$(date -u +%FT%TZ)" >>"$LOG"
    else
      printf '%s cli=grok result=failure reason=notify_failed notify=failed exit=3\n' "$(date -u +%FT%TZ)" >>"$LOG"
      exit 3
    fi
    ;;
  *)
    if notify_fixed "Proactive check failed" "Open Grok to retry or inspect proactive status." >/dev/null 2>&1; then
      NOTIFY=sent
    else
      NOTIFY=failed
    fi
    printf '%s cli=grok result=failure reason=invalid_token notify=%s exit=2\n' "$(date -u +%FT%TZ)" "$NOTIFY" >>"$LOG"
    exit 2
    ;;
esac
```

Create `~/bin/proactive-cron-codex`:

```sh
#!/bin/sh
set -u

PATH="/home/you/.local/bin:/usr/local/bin:/usr/bin:/bin"
REPO="/home/you/src/proactive-mcp"
LOGDIR="$HOME/.proactive-mcp/logs"
LOG="$LOGDIR/codex-cron.log"
PROMPT='Call the proactive_check MCP tool exactly once and call no other tool. Your entire final response must be exactly PROACTIVE_ATTENTION if situations is non-empty or warnings is non-empty. Otherwise it must be exactly PROACTIVE_NONE. Output one token only, with no Markdown, punctuation, explanation, situation content, title, why_now, evidence, names, dates, mail, or calendar text.'

mkdir -p "$LOGDIR"
chmod 700 "$LOGDIR"

PLATFORM=$(uname -s)
case "$PLATFORM" in
  Linux)
    command -v notify-send >/dev/null 2>&1 || {
      printf '%s cli=codex result=failure reason=notifier_unavailable notify=none exit=3\n' "$(date -u +%FT%TZ)" >>"$LOG"
      exit 3
    }
    ;;
  Darwin)
    command -v osascript >/dev/null 2>&1 || {
      printf '%s cli=codex result=failure reason=notifier_unavailable notify=none exit=3\n' "$(date -u +%FT%TZ)" >>"$LOG"
      exit 3
    }
    MACOS_SCRIPT=$(uv run --directory "$REPO" python -c 'from proactive_mcp.delivery.notify import MACOS_NOTIFICATION_SCRIPT; print(MACOS_NOTIFICATION_SCRIPT)')
    [ -f "$MACOS_SCRIPT" ] || {
      printf '%s cli=codex result=failure reason=notifier_unavailable notify=none exit=3\n' "$(date -u +%FT%TZ)" >>"$LOG"
      exit 3
    }
    ;;
  *)
    printf '%s cli=codex result=failure reason=notifier_unavailable notify=none exit=3\n' "$(date -u +%FT%TZ)" >>"$LOG"
    exit 3
    ;;
esac

notify_fixed() {
  if [ "$PLATFORM" = "Linux" ]; then
    notify-send -- "$1" "$2"
  else
    osascript "$MACOS_SCRIPT" "$1" "$2"
  fi
}

RAW=$(codex exec --ephemeral --sandbox read-only --skip-git-repo-check -C "$REPO" "$PROMPT" 2>/dev/null)
CODE=$?

if [ "$CODE" -ne 0 ]; then
  if notify_fixed "Proactive check failed" "Open Codex to retry or inspect proactive status." >/dev/null 2>&1; then
    NOTIFY=sent
  else
    NOTIFY=failed
  fi
  printf '%s cli=codex result=failure reason=agent_failed notify=%s exit=%s\n' "$(date -u +%FT%TZ)" "$NOTIFY" "$CODE" >>"$LOG"
  exit "$CODE"
fi

case "$RAW" in
  PROACTIVE_NONE)
    printf '%s cli=codex result=none notify=none exit=0\n' "$(date -u +%FT%TZ)" >>"$LOG"
    ;;
  PROACTIVE_ATTENTION)
    if notify_fixed "Proactive alert" "Open Codex to review proactive status." >/dev/null 2>&1; then
      printf '%s cli=codex result=attention notify=sent exit=0\n' "$(date -u +%FT%TZ)" >>"$LOG"
    else
      printf '%s cli=codex result=failure reason=notify_failed notify=failed exit=3\n' "$(date -u +%FT%TZ)" >>"$LOG"
      exit 3
    fi
    ;;
  *)
    if notify_fixed "Proactive check failed" "Open Codex to retry or inspect proactive status." >/dev/null 2>&1; then
      NOTIFY=sent
    else
      NOTIFY=failed
    fi
    printf '%s cli=codex result=failure reason=invalid_token notify=%s exit=2\n' "$(date -u +%FT%TZ)" "$NOTIFY" >>"$LOG"
    exit 2
    ;;
esac
```

macOS paths differ; Homebrew usually puts `uv` in `/opt/homebrew/bin`. Get the real values from `command -v uv grok codex` and substitute them.

Make the wrappers executable and install the schedule:

```bash
chmod 700 ~/bin/proactive-cron-grok ~/bin/proactive-cron-codex
crontab -e
```

Add one line, not both. Two scheduled collectors sharing a database race to
claim the same situations:

```cron
*/15 * * * * /home/you/bin/proactive-cron-grok
```

```cron
*/15 * * * * /home/you/bin/proactive-cron-codex
```

On macOS the first run triggers a permissions prompt under System Settings → Privacy & Security → Full Disk Access, for `cron` or your terminal. Approve it, or the job fails silently.

Separately, keep the watcher alive. On Linux a user systemd unit is the tidy way:

```ini
[Unit]
Description=proactive-mcp watcher

[Service]
ExecStart=/home/you/.local/bin/uv run --directory /home/you/src/proactive-mcp proactive-mcp daemon
Restart=on-failure

[Install]
WantedBy=default.target
```

```bash
systemctl --user enable --now proactive-mcp-daemon
```

Skip the daemon and everything above still delivers situations at each cron tick. You just have no OS notification fallback.

#### Verifying the cron handoff

```bash
grok mcp doctor proactive
codex mcp get proactive
~/bin/proactive-cron-grok && tail -n 1 ~/.proactive-mcp/logs/grok-cron.log
```

For a synthetic pending situation, a valid scheduled delivery requires all
three: `pending` moves to `delivered`, the marker says
`result=attention notify=sent exit=0`, and a fixed native notification appears.
A marker or state transition alone is not delivery. If the notification says
the check failed, open the named agent and inspect recent delivered situations;
do not call `proactive_check` again as a retry.

### Windows: Task Scheduler

Save `C:\Users\you\src\proactive-mcp-scripts\Invoke-ProactiveCheck.ps1`:

```powershell
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("grok", "codex")]
    [string] $Cli
)

# Continue, not Stop: the script inspects the exit code itself and must reach its logging lines.
$ErrorActionPreference = "Continue"

$Repo   = "C:\Users\you\src\proactive-mcp"
$LogDir = Join-Path $env:USERPROFILE ".proactive-mcp\logs"
$Log    = Join-Path $LogDir "$Cli-task.log"
$Uv     = (Get-Command uv).Source
$ToastScript = (& $Uv run --directory $Repo python -c `
    "from proactive_mcp.delivery.notify import WINDOWS_TOAST_SCRIPT; print(WINDOWS_TOAST_SCRIPT)").Trim()
$Prompt = "Call the proactive_check MCP tool exactly once and call no other tool. Your entire final response must be exactly PROACTIVE_ATTENTION if situations is non-empty or warnings is non-empty. Otherwise it must be exactly PROACTIVE_NONE. Output one token only, with no Markdown, punctuation, explanation, situation content, title, why_now, evidence, names, dates, mail, or calendar text."
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Send-FixedToast {
    param([string] $Title, [string] $Body)
    & powershell.exe -NoProfile -NonInteractive -File $ToastScript $Title $Body *> $null
    return ($LASTEXITCODE -eq 0)
}

if (-not (Test-Path -LiteralPath $ToastScript -PathType Leaf)) {
    Add-Content -LiteralPath $Log -Value "$(Get-Date -Format o) cli=$Cli result=failure reason=notifier_unavailable notify=none exit=3"
    exit 3
}

# Capture stdout in memory only. It is never written or passed to the notifier.
if ($Cli -eq "codex") {
    $raw = & codex exec --ephemeral --sandbox read-only --skip-git-repo-check -C $Repo $Prompt 2>$null
}
else {
    $raw = & grok --cwd $Repo --no-alt-screen --single $Prompt 2>$null
}

$code = $LASTEXITCODE
$token = (($raw | ForEach-Object { "$_" }) -join "`n").Trim()
$stamp = (Get-Date -Format o)

if ($code -ne 0) {
    $sent = Send-FixedToast "Proactive check failed" "Open $Cli to retry or inspect proactive status."
    $notify = if ($sent) { "sent" } else { "failed" }
    Add-Content -LiteralPath $Log -Value "$stamp cli=$Cli result=failure reason=agent_failed notify=$notify exit=$code"
    exit $code
}

if ($token -ceq "PROACTIVE_NONE") {
    Add-Content -LiteralPath $Log -Value "$stamp cli=$Cli result=none notify=none exit=0"
    exit 0
}

if ($token -ceq "PROACTIVE_ATTENTION") {
    $sent = Send-FixedToast "Proactive alert" "Open $Cli to review proactive status."
    if ($sent) {
        Add-Content -LiteralPath $Log -Value "$stamp cli=$Cli result=attention notify=sent exit=0"
        exit 0
    }
    Add-Content -LiteralPath $Log -Value "$stamp cli=$Cli result=failure reason=notify_failed notify=failed exit=3"
    exit 3
}

$sent = Send-FixedToast "Proactive check failed" "Open $Cli to retry or inspect proactive status."
$notify = if ($sent) { "sent" } else { "failed" }
Add-Content -LiteralPath $Log -Value "$stamp cli=$Cli result=failure reason=invalid_token notify=$notify exit=2"
exit 2
```

The token is compared by exact equality after trimming surrounding whitespace.
It is never written, echoed, substring-matched, or used as notification text.
The notifier path is checked before the agent can claim a situation. A
notification failure is non-zero and must not trigger an automatic retry,
because `proactive_check` may already have delivered a situation.

Register the Codex task as the interactive user:

```powershell
$Script = "C:\Users\you\src\proactive-mcp-scripts\Invoke-ProactiveCheck.ps1"

$Action = New-ScheduledTaskAction `
    -Execute "PowerShell.exe" `
    -Argument "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$Script`" -Cli codex" `
    -WorkingDirectory "C:\Users\you\src\proactive-mcp"

$Trigger = New-ScheduledTaskTrigger `
    -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 15)

$Principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName "proactive-mcp\codex proactive_check" `
    -Action $Action -Trigger $Trigger -Principal $Principal `
    -Description "Call proactive_check through Codex CLI every 15 minutes" `
    -Force
```

For Grok, reuse the same script and register a separate task with `-Cli grok`,
`grok` in the task name, and a Grok description. Do not enable both recurring
tasks against the same database; they would race to claim the same situations.
Reference: [Register-ScheduledTask](https://learn.microsoft.com/powershell/module/scheduledtasks/register-scheduledtask).

Run the watcher on Windows the same way, as its own task with a logon trigger:

```powershell
$Action = New-ScheduledTaskAction `
    -Execute "C:\Users\you\.local\bin\uv.exe" `
    -Argument "run --directory C:\Users\you\src\proactive-mcp proactive-mcp daemon"

Register-ScheduledTask `
    -TaskName "proactive-mcp\watcher" `
    -Action $Action `
    -Trigger (New-ScheduledTaskTrigger -AtLogOn) `
    -Principal (New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited) `
    -Force
```

Without it: degraded mode, no OS toast fallback, everything else unchanged.

#### Verifying the Windows handoff

```powershell
Get-ScheduledTask -TaskPath "\proactive-mcp\" | Format-Table TaskName, State
Start-ScheduledTask -TaskName "proactive-mcp\codex proactive_check"
Start-Sleep -Seconds 30
Get-Content "$env:USERPROFILE\.proactive-mcp\logs\codex-task.log" -Tail 5
uv run --directory C:\Users\you\src\proactive-mcp proactive-mcp status
```

`LogonType Interactive` matters: a task set to run whether or not the user is logged on gets a different session and often can't reach the agent's credentials or config. If the task reports `0x1` right away, run the script by hand in the same shell first. The cause is almost always a path.

For a synthetic pending situation, pass only when the state moves to
`delivered`, the marker says `result=attention notify=sent exit=0`, and the
fixed toast appears in the user's session. If state changes without a toast,
the task consumed rather than delivered the situation and must be treated as a
failure.

## Log privacy rules

From §9.2, non-negotiable.

- **Never persist raw `proactive_check` payloads.** Not to a cron log, not to a task log, not to a debug file. The response deliberately carries the minimum context a human summary needs: sender display names, subjects, event titles. Fine in a chat window you own, not fine on disk.
- A scheduler may capture final stdout in process memory only to compare it by exact equality with `PROACTIVE_ATTENTION` or `PROACTIVE_NONE`. Never persist, echo, parse fields from, or pass that output to another process.
- Log only fixed fields: timestamp, CLI, `result`, optional fixed `reason`, `notify`, and exit code. Nothing derived from raw agent output belongs on disk.
- Keep the log directory private: `chmod 700 ~/.proactive-mcp/logs` on Linux and macOS. At the documented Windows store path, those files already get a restricted DACL.
- Never log OAuth tokens, email bodies or addresses, calendar details, or situation `evidence` fields.
- Reporting a problem in an Issue? Attach exit codes, tool names, call counts, situation type, state, priority, and integer IDs. Don't attach the database, WAL files, config, or screenshots of tool results.
- Test end to end with synthetic memories, not real inbox data. `remember` a fake occasion a few days out and watch it flow.

## Verification checklist

Run these from the checkout. Everything should exit 0.

```bash
uv run --directory /home/you/src/proactive-mcp proactive-mcp --help
uv run --directory /home/you/src/proactive-mcp proactive-mcp daemon --help
uv run --directory /home/you/src/proactive-mcp proactive-mcp status
```

Per platform, in order:

| | Grok CLI | Codex CLI | Hermes | Claude Desktop |
|---|---|---|---|---|
| Registered | `grok mcp list` | `codex mcp list` | `hermes mcp list` | server in tool list |
| Server healthy | `grok mcp doctor proactive` | `codex mcp get proactive` | `hermes mcp test proactive` | `get_status` from a chat |
| Daemon or degraded | `status` shows daemon state, or the missing-fallback warning is understood and accepted | same | same | same |
| Session-start rule | `AGENTS.md` at project root | `AGENTS.md` at workspace root | system prompt or `--workdir` `AGENTS.md` | project instructions |
| Scheduled trigger | synthetic state transition plus visible fixed OS notification | synthetic state transition plus visible fixed OS notification | `hermes cron list` and delivered channel message | local scheduled task, ≥1 min |
| Dedupe | situation delivered once, never twice | same | same | same |
| Logs clean | no payloads on disk | no payloads on disk | no payloads on disk | no payloads on disk |

Commands that were **not** executable in the verification environment, and are therefore sourced rather than tested: everything Claude Code Desktop-specific (not installed; sourced from Anthropic's local MCP guide and §5.3), and all Windows PowerShell snippets (verified on Linux; cmdlet shapes come from Microsoft's `Register-ScheduledTask` reference). Grok, Codex, Hermes, and `proactive-mcp` commands and flags were read from the installed binaries' own `--help` output at the versions listed at the top of this file.
