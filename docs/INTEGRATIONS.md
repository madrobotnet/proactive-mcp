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
- [The neutral agent directory](#the-neutral-agent-directory)
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
- **External write actions.** V1 holds `gmail.readonly` and `calendar.readonly`, nothing else, so it can't change anything in your Google account or anywhere else outside this machine. No recipe here should ask an agent to send mail or create events. External write actions arrive in V2 behind an approval-first contract. Read-only stops at the network boundary: the server still writes to its own local SQLite database, since `remember` stores a memory and `proactive_check` and `snooze_situation` change situation state.

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

## The neutral agent directory

Every CLI here loads `AGENTS.md` from its working directory. That's exactly what you want in a real project session: you put the canonical session-start rule in the repository's `AGENTS.md`, the agent merges it with whatever else that file says about the project, and `proactive_check` runs alongside your normal work. Merging is intentional there.

It's the wrong behavior for a fresh one-shot or a scheduled run. If the agent starts inside this checkout, or inside any repository, that project's `AGENTS.md` joins the scheduled prompt and can divert the run: extra tool calls, a code review it was never asked for, or a refusal that leaves the counter untouched. A scheduled check has one job.

So point one-shot and scheduled runs at a dedicated directory that holds nothing:

```bash
mkdir -p ~/.proactive-mcp/agent-cwd
chmod 700 ~/.proactive-mcp/agent-cwd
```

On Windows that's `%USERPROFILE%\.proactive-mcp\agent-cwd`. Keep it empty. No `AGENTS.md`, no `CLAUDE.md`, no `.mcp.json`, no git repository. The MCP registration is user scope, so the server still starts; only the project instructions disappear. Because the directory isn't a repository, Codex needs `--skip-git-repo-check`.

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

Grok has no scheduler. Use `-p/--single` for a one-shot headless prompt, and run it from the [neutral agent directory](#the-neutral-agent-directory) rather than the checkout:

```bash
mkdir -p ~/.proactive-mcp/agent-cwd
chmod 700 ~/.proactive-mcp/agent-cwd
grok --cwd ~/.proactive-mcp/agent-cwd --no-alt-screen --single 'Call the proactive_check MCP tool exactly once. Report any returned situations and freshness warnings; otherwise state that there are no actionable situations.'
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

This writes `~/.codex/config.toml` (or `$CODEX_HOME/config.toml` when `CODEX_HOME` is set). `codex mcp add` doesn't set the approval mode, so add that line by hand:

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
default_tools_approval_mode = "approve"
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
default_tools_approval_mode = "approve"
```

`default_tools_approval_mode` accepts `auto`, `prompt`, `writes`, or `approve` on codex-cli 0.149.0; anything else fails config load with `unknown variant`. Pick `approve` for this server, and understand exactly what you're granting.

V1 exposes **no Google or other external write actions**, so an approved tool call can't send mail, create an event, or touch anything off this machine. It is *not* read-only, though. `remember` writes a memory row, and `proactive_check` and `snooze_situation` change situation state, all in the local SQLite database at `~/.proactive-mcp/proactive.db`. Setting `approve` authorizes those local mutations to happen unattended, which is the point: a scheduled run has nobody to answer a prompt, and `proactive_check` can't claim a situation without one. Grant it because you trust this specific local server, not because the tools are harmless. The scope is one server, not the whole CLI: shell commands and every other MCP server keep their usual approval behavior, so don't copy this setting onto a server you haven't audited.

Confirm what actually landed on disk with `codex mcp get proactive`, and treat that output as authoritative over the snippet above. The `default_tools_approval_mode:` line should read `approve`.

### 2. Watcher daemon, or degraded mode

```bash
uv run --directory /home/you/src/proactive-mcp proactive-mcp daemon
```

Degraded alternative: no daemon, inline lazy sync on `proactive_check`, and **no OS notification fallback**. `get_status` reports `daemon.status` as `not_running` plus the warning, so the agent can tell you the fallback path is dark.

### 3. Session-start rule

Codex has no automatic session hook for this. Put [the rule](#the-session-start-rule) in `AGENTS.md` at the repository or workspace root, and repeat the instruction inside any scheduled prompt.

### 4. Scheduled trigger: none, hand off to the OS

`codex exec` runs non-interactively, from the [neutral agent directory](#the-neutral-agent-directory):

```bash
codex exec \
  --ephemeral \
  --sandbox read-only \
  --skip-git-repo-check \
  -c mcp_servers.proactive.default_tools_approval_mode=approve \
  -C "$HOME/.proactive-mcp/agent-cwd" \
  'Call the proactive_check MCP tool exactly once. Report its situations, freshness, and warnings concisely. Do not modify files and do not run unrelated commands.'
```

The `-c` override repeats what the config file already says. Carry it in every non-interactive example anyway, so the command works on a machine where nobody has edited `config.toml` yet, and so a scheduled run can't be silently broken by someone tidying that file. Interactive `codex` sessions don't need it; a human is there to approve. The value is bare on purpose: `-c` parses the right-hand side as TOML and falls back to the raw string, so `=approve` survives cron, `crontab`, and PowerShell 5.1 argument handling without quote gymnastics.

Flags, all from `codex exec --help`: `--ephemeral` skips persisting session files, `-s/--sandbox read-only` blocks filesystem writes by model-generated shell commands, `-C/--cd` sets the working root, `--skip-git-repo-check` allows non-repo directories, `-c/--config` overrides one config key, `--json` emits JSONL events, `-o/--output-last-message FILE` writes the final message to a file.

The sandbox and the approval mode govern two different things, and it's worth keeping them straight. `--sandbox read-only` constrains the *agent's own shell*: no editing files, no `git commit`, no scribbling in your checkout. It says nothing about MCP tools. The proactive server runs as its own process and updates its local database through its own code path, so a claim or a `remember` still lands with the sandbox at `read-only`. That's intended. The agent needs no shell access at all for this job, which is why the tightest sandbox is the right choice.

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
| Non-interactive run stalls or ends with no tool call | Approval. Confirm `codex mcp get proactive` shows `default_tools_approval_mode: approve`, and pass `-c mcp_servers.proactive.default_tools_approval_mode=approve` on the command line. |
| `unknown variant` on startup | The approval mode is misspelled. Only `auto`, `prompt`, `writes`, and `approve` are accepted. |
| `codex exec` fails outside a git repo | Add `--skip-git-repo-check`. |
| Agent tries to run a shell command or edit a file | Keep `--sandbox read-only`. This job needs no shell at all, so an attempt means the prompt drifted. Local database writes by the MCP server itself are normal and aren't affected by the sandbox. |

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

Add [the rule](#the-session-start-rule) to your Hermes system prompt. Hermes injects `AGENTS.md` and `CLAUDE.md` from the `--workdir` directory into the job, which is why scheduled jobs below point at the empty [neutral agent directory](#the-neutral-agent-directory): a repository's project instructions would ride along and can divert the run. Interactive project work is the opposite case, and there merging the rule into the repository `AGENTS.md` is the point.

### 4. Scheduled trigger: Native Cron

```bash
hermes cron create "every 15m" \
  "Call the MCP tool proactive_check exactly once. If it returns situations, report each one clearly in this channel, including freshness and warnings. If freshness is stale or warnings indicate a source problem, say so instead of reporting that nothing is pending. If nothing is returned and freshness is healthy, report that there are no pending situations. Do not call any write action." \
  --name "proactive check" \
  --workdir /home/you/.proactive-mcp/agent-cwd
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

### How the wrappers decide

The wrappers never read what the agent said. Agent stdout and stderr go to
`/dev/null`, and on Windows to `$null`. There's no token contract, no keyword,
nothing to parse. If you're coming from an earlier draft of this guide: the
two-token output contract is deleted. A model that
paraphrases, adds Markdown, or wraps a line can no longer break the run, and
agent text can no longer reach a notification or a log file.

Each run measures server state instead:

1. Preflight the OS notifier. No notifier, no run.
2. Take the scheduler lock, so nothing else measures at the same time.
3. Read `proactive-mcp status` and record `deliveries.total`.
4. Run the agent once, discarding all of its output.
5. Read `proactive-mcp status` again and compare `deliveries.total`.

That comparison is the entire contract. `deliveries.total` counts rows in the
immutable delivery-event table, so it only ever moves up, and it moves exactly
when `proactive_check` claims a situation. Higher after than before means
something was delivered into a session nobody is watching, which is precisely
when the user needs a notification. An unchanged value means nothing was
claimed.

#### Why `deliveries.total` and not `budget.used`

`budget.used` answers a different question, and it gets this one wrong twice.

- **Critical situations bypass the budget.** A critical claim is delivered and
  counted as a delivery, but it spends no budget, so `budget.used` can sit
  perfectly still through the most urgent case the product has.
- **The budget resets.** Cross midnight and `used` drops back to 0. A wrapper
  diffing that counter reads a real delivery as a decrease, so it either stays
  quiet about a genuine situation or screams about a regression on a healthy
  run.

`deliveries.total` has neither problem. It's cumulative, never reset, and it
includes critical claims. `fallback.claimed` is wrong for a third reason: it
counts what the watcher daemon picked up for OS-notification fallback, not what
an agent collected.

#### One measurement at a time

Nothing else may call `proactive_check` between the two reads. An interactive
session, a second scheduler entry, a manual test, all of them can raise the
counter, and the wrapper would credit that increase to its own agent and notify
you about a situation you already read on screen. So: register the Grok job or
the Codex job, never both, and don't run a check by hand while a scheduled one
is in flight. The wrappers enforce this with a lock directory at
`~/.proactive-mcp/scheduler.lock`. An overlap is a hard failure with
`reason=concurrent_run`, not a wait.

#### Outcomes

| Condition | Marker | Notification | Exit |
|---|---|---|---|
| Counter increased | `result=attention` | fixed alert | 0 |
| Counter decreased | `result=failure reason=counter_regressed` | fixed failure | 4 |
| Status unreadable or unparsable, either read | `result=failure reason=status_unreadable` | fixed failure | 4 |
| No delivery, agent exit nonzero | `result=failure reason=agent_failed` | fixed failure | agent's code |
| No delivery, agent exit 0, `warnings` non-empty | `result=status_warning` | fixed status-warning text | 0 |
| No delivery, agent exit 0, no warnings | `result=no_delivery` | none | 0 |
| Notifier missing at preflight | `result=failure reason=notifier_unavailable` | none possible | 3 |
| Notification attempt failed | `result=failure reason=notify_failed` | attempted, failed | 3 |
| Another measurement in flight | `result=failure reason=concurrent_run` | none | 5 |

An increase outranks everything but a regression. If the counter moved, the
user gets told, whatever the agent's exit code, whatever the priority, whatever
the budget said. The agent's own code is recorded separately as `agent_exit`,
so a flaky-but-successful run stays diagnosable without changing the verdict.

Regression and unreadable status fail loud and **never retry automatically**. A
counter that goes backwards means the database was replaced, rolled back, or
pointed somewhere else; retrying against a broken store either hides that or
claims fresh situations into another discarded session. A status read that
won't parse is the same shape of problem: the wrapper can't tell delivery from
no delivery, so it says so and stops.

The status-warning case is deliberately its own outcome. Nothing was delivered
and the agent is fine, but a source is stale or the daemon never started, so
"no news" isn't trustworthy. It gets fixed wording distinct from both the alert
and the failure text, plus its own marker value. The wording carries a count of
nothing and quotes no warning text.

After any notification, open the named agent and inspect recent delivered
situations with `list_situations(state=delivered)` and source freshness with
`get_status`. `proactive_check` won't return an already claimed situation, so
calling it again isn't a retry, it's a new claim.

### Linux and macOS: cron

Create `~/bin/proactive-cron-grok`:

```sh
#!/bin/sh
set -u

PATH="/home/you/.local/bin:/home/you/.grok/bin:/usr/local/bin:/usr/bin:/bin"
REPO="/home/you/src/proactive-mcp"
AGENT_CWD="$HOME/.proactive-mcp/agent-cwd"
LOGDIR="$HOME/.proactive-mcp/logs"
LOG="$LOGDIR/grok-cron.log"
LOCK="$HOME/.proactive-mcp/scheduler.lock"
PROMPT='Call the MCP tool proactive_check exactly once and call no other tool, then stop.'

mkdir -p "$LOGDIR" "$AGENT_CWD"
chmod 700 "$LOGDIR" "$AGENT_CWD"

mark() {
  printf '%s cli=grok %s\n' "$(date -u +%FT%TZ)" "$1" >>"$LOG"
}

PLATFORM=$(uname -s)
case "$PLATFORM" in
  Linux)
    command -v notify-send >/dev/null 2>&1 || {
      mark "result=failure reason=notifier_unavailable notify=none exit=3"
      exit 3
    }
    ;;
  Darwin)
    command -v osascript >/dev/null 2>&1 || {
      mark "result=failure reason=notifier_unavailable notify=none exit=3"
      exit 3
    }
    MACOS_SCRIPT=$(uv run --directory "$REPO" python -c 'from proactive_mcp.delivery.notify import MACOS_NOTIFICATION_SCRIPT; print(MACOS_NOTIFICATION_SCRIPT)')
    [ -f "$MACOS_SCRIPT" ] || {
      mark "result=failure reason=notifier_unavailable notify=none exit=3"
      exit 3
    }
    ;;
  *)
    mark "result=failure reason=notifier_unavailable notify=none exit=3"
    exit 3
    ;;
esac

notify_fixed() {
  if [ "$PLATFORM" = "Linux" ]; then
    notify-send -- "$1" "$2" >/dev/null 2>&1
  else
    osascript "$MACOS_SCRIPT" "$1" "$2" >/dev/null 2>&1
  fi
}

# Only one measurement may be in flight: a concurrent proactive_check would
# move deliveries.total and be misread as this run's delivery.
if ! mkdir "$LOCK" 2>/dev/null; then
  mark "result=failure reason=concurrent_run notify=none exit=5"
  exit 5
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT INT TERM

# Prints "<deliveries.total> <0|1 warnings present>", or fails.
read_state() {
  uv run --directory "$REPO" proactive-mcp status 2>/dev/null |
    uv run --directory "$REPO" python -c '
import json, sys
try:
    data = json.load(sys.stdin)
    total = int(data["deliveries"]["total"])
    warned = 1 if data["warnings"] else 0
except (json.JSONDecodeError, KeyError, TypeError, ValueError):
    sys.exit(1)
print(total, warned)
' 2>/dev/null
}

is_uint() {
  case "$1" in
    '' | *[!0-9]*) return 1 ;;
  esac
  return 0
}

fail_status() {
  if notify_fixed "Proactive check failed" "Open Grok to inspect proactive status."; then
    NOTIFY=sent
  else
    NOTIFY=failed
  fi
  mark "result=failure reason=$1 notify=$NOTIFY exit=4"
  exit 4
}

BEFORE=$(read_state) || fail_status status_unreadable
BEFORE_TOTAL=${BEFORE%% *}
is_uint "$BEFORE_TOTAL" || fail_status status_unreadable

# All agent output is discarded. Nothing it says is read, parsed, or stored.
grok --cwd "$AGENT_CWD" --no-alt-screen --single "$PROMPT" >/dev/null 2>&1
CODE=$?

AFTER=$(read_state) || fail_status status_unreadable
AFTER_TOTAL=${AFTER%% *}
AFTER_WARN=${AFTER##* }
is_uint "$AFTER_TOTAL" || fail_status status_unreadable
is_uint "$AFTER_WARN" || fail_status status_unreadable

if [ "$AFTER_TOTAL" -lt "$BEFORE_TOTAL" ]; then
  fail_status counter_regressed
fi

if [ "$AFTER_TOTAL" -gt "$BEFORE_TOTAL" ]; then
  if notify_fixed "Proactive alert" "Open Grok to review proactive status."; then
    mark "result=attention notify=sent agent_exit=$CODE exit=0"
    exit 0
  fi
  mark "result=failure reason=notify_failed notify=failed agent_exit=$CODE exit=3"
  exit 3
fi

if [ "$CODE" -ne 0 ]; then
  if notify_fixed "Proactive check failed" "Open Grok to inspect proactive status."; then
    NOTIFY=sent
  else
    NOTIFY=failed
  fi
  mark "result=failure reason=agent_failed notify=$NOTIFY agent_exit=$CODE exit=$CODE"
  exit "$CODE"
fi

if [ "$AFTER_WARN" -eq 1 ]; then
  if notify_fixed "Proactive status warning" "Open Grok to inspect proactive source freshness."; then
    mark "result=status_warning notify=sent agent_exit=0 exit=0"
    exit 0
  fi
  mark "result=failure reason=notify_failed notify=failed agent_exit=0 exit=3"
  exit 3
fi

mark "result=no_delivery notify=none agent_exit=0 exit=0"
exit 0
```

Create `~/bin/proactive-cron-codex`:

```sh
#!/bin/sh
set -u

PATH="/home/you/.local/bin:/usr/local/bin:/usr/bin:/bin"
REPO="/home/you/src/proactive-mcp"
AGENT_CWD="$HOME/.proactive-mcp/agent-cwd"
LOGDIR="$HOME/.proactive-mcp/logs"
LOG="$LOGDIR/codex-cron.log"
LOCK="$HOME/.proactive-mcp/scheduler.lock"
PROMPT='Call the MCP tool proactive_check exactly once and call no other tool, then stop.'

mkdir -p "$LOGDIR" "$AGENT_CWD"
chmod 700 "$LOGDIR" "$AGENT_CWD"

mark() {
  printf '%s cli=codex %s\n' "$(date -u +%FT%TZ)" "$1" >>"$LOG"
}

PLATFORM=$(uname -s)
case "$PLATFORM" in
  Linux)
    command -v notify-send >/dev/null 2>&1 || {
      mark "result=failure reason=notifier_unavailable notify=none exit=3"
      exit 3
    }
    ;;
  Darwin)
    command -v osascript >/dev/null 2>&1 || {
      mark "result=failure reason=notifier_unavailable notify=none exit=3"
      exit 3
    }
    MACOS_SCRIPT=$(uv run --directory "$REPO" python -c 'from proactive_mcp.delivery.notify import MACOS_NOTIFICATION_SCRIPT; print(MACOS_NOTIFICATION_SCRIPT)')
    [ -f "$MACOS_SCRIPT" ] || {
      mark "result=failure reason=notifier_unavailable notify=none exit=3"
      exit 3
    }
    ;;
  *)
    mark "result=failure reason=notifier_unavailable notify=none exit=3"
    exit 3
    ;;
esac

notify_fixed() {
  if [ "$PLATFORM" = "Linux" ]; then
    notify-send -- "$1" "$2" >/dev/null 2>&1
  else
    osascript "$MACOS_SCRIPT" "$1" "$2" >/dev/null 2>&1
  fi
}

if ! mkdir "$LOCK" 2>/dev/null; then
  mark "result=failure reason=concurrent_run notify=none exit=5"
  exit 5
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT INT TERM

read_state() {
  uv run --directory "$REPO" proactive-mcp status 2>/dev/null |
    uv run --directory "$REPO" python -c '
import json, sys
try:
    data = json.load(sys.stdin)
    total = int(data["deliveries"]["total"])
    warned = 1 if data["warnings"] else 0
except (json.JSONDecodeError, KeyError, TypeError, ValueError):
    sys.exit(1)
print(total, warned)
' 2>/dev/null
}

is_uint() {
  case "$1" in
    '' | *[!0-9]*) return 1 ;;
  esac
  return 0
}

fail_status() {
  if notify_fixed "Proactive check failed" "Open Codex to inspect proactive status."; then
    NOTIFY=sent
  else
    NOTIFY=failed
  fi
  mark "result=failure reason=$1 notify=$NOTIFY exit=4"
  exit 4
}

BEFORE=$(read_state) || fail_status status_unreadable
BEFORE_TOTAL=${BEFORE%% *}
is_uint "$BEFORE_TOTAL" || fail_status status_unreadable

# Neutral cwd, per-server approval override, and every byte of output discarded.
codex exec \
  --ephemeral \
  --sandbox read-only \
  --skip-git-repo-check \
  -c mcp_servers.proactive.default_tools_approval_mode=approve \
  -C "$AGENT_CWD" \
  "$PROMPT" >/dev/null 2>&1
CODE=$?

AFTER=$(read_state) || fail_status status_unreadable
AFTER_TOTAL=${AFTER%% *}
AFTER_WARN=${AFTER##* }
is_uint "$AFTER_TOTAL" || fail_status status_unreadable
is_uint "$AFTER_WARN" || fail_status status_unreadable

if [ "$AFTER_TOTAL" -lt "$BEFORE_TOTAL" ]; then
  fail_status counter_regressed
fi

if [ "$AFTER_TOTAL" -gt "$BEFORE_TOTAL" ]; then
  if notify_fixed "Proactive alert" "Open Codex to review proactive status."; then
    mark "result=attention notify=sent agent_exit=$CODE exit=0"
    exit 0
  fi
  mark "result=failure reason=notify_failed notify=failed agent_exit=$CODE exit=3"
  exit 3
fi

if [ "$CODE" -ne 0 ]; then
  if notify_fixed "Proactive check failed" "Open Codex to inspect proactive status."; then
    NOTIFY=sent
  else
    NOTIFY=failed
  fi
  mark "result=failure reason=agent_failed notify=$NOTIFY agent_exit=$CODE exit=$CODE"
  exit "$CODE"
fi

if [ "$AFTER_WARN" -eq 1 ]; then
  if notify_fixed "Proactive status warning" "Open Codex to inspect proactive source freshness."; then
    mark "result=status_warning notify=sent agent_exit=0 exit=0"
    exit 0
  fi
  mark "result=failure reason=notify_failed notify=failed agent_exit=0 exit=3"
  exit 3
fi

mark "result=no_delivery notify=none agent_exit=0 exit=0"
exit 0
```

macOS paths differ; Homebrew usually puts `uv` in `/opt/homebrew/bin`. Get the real values from `command -v uv grok codex` and substitute them.

Make the wrappers executable and install the schedule:

```bash
chmod 700 ~/bin/proactive-cron-grok ~/bin/proactive-cron-codex
crontab -e
```

Add one line, not both. Two scheduled collectors sharing a database race to
claim the same situations, and each would see the other's claim as its own
delivery:

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
uv run --directory /home/you/src/proactive-mcp proactive-mcp status
~/bin/proactive-cron-grok && tail -n 1 ~/.proactive-mcp/logs/grok-cron.log
```

Note `deliveries.total` in that `status` output before you start, and keep every
other agent away from the database while you test. With a synthetic pending
situation waiting, a valid scheduled delivery needs all four: `deliveries.total`
is one higher afterwards, the situation moved from `pending` to `delivered`, the
marker says `result=attention notify=sent agent_exit=0 exit=0`, and a fixed
native notification appeared. A marker, a counter bump, or a state transition
alone is not delivery.

Run the wrapper a second time with nothing pending and, assuming healthy
sources, the marker should read `result=no_delivery notify=none` with no
notification. On a machine with a stale source or no daemon you'll get
`result=status_warning` and the fixed status-warning text instead, which is the
right answer: nothing was delivered, and nothing can promise there was nothing
to deliver. If a run reports `counter_regressed` or `status_unreadable`, fix the
database or the CLI first. Don't re-run as a retry, and don't call
`proactive_check` by hand to "check"; either one claims situations into a
session you'll then have to go find.

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

$Repo     = "C:\Users\you\src\proactive-mcp"
$AgentCwd = Join-Path $env:USERPROFILE ".proactive-mcp\agent-cwd"
$LogDir   = Join-Path $env:USERPROFILE ".proactive-mcp\logs"
$Log      = Join-Path $LogDir "$Cli-task.log"
$Lock     = Join-Path $env:USERPROFILE ".proactive-mcp\scheduler.lock"
$Uv       = (Get-Command uv).Source
$ToastScript = (& $Uv run --directory $Repo python -c `
    "from proactive_mcp.delivery.notify import WINDOWS_TOAST_SCRIPT; print(WINDOWS_TOAST_SCRIPT)").Trim()
$Prompt = "Call the MCP tool proactive_check exactly once and call no other tool, then stop."
New-Item -ItemType Directory -Force -Path $LogDir, $AgentCwd | Out-Null

function Write-Marker {
    param([string] $Fields)
    Add-Content -LiteralPath $Log -Value "$(Get-Date -Format o) cli=$Cli $Fields"
}

function Send-FixedToast {
    param([string] $Title, [string] $Body)
    & powershell.exe -NoProfile -NonInteractive -File $ToastScript $Title $Body *> $null
    return ($LASTEXITCODE -eq 0)
}

# Reads deliveries.total and whether warnings are present. $null means the
# status could not be read or parsed, which is a hard failure, never a retry.
function Get-DeliveryState {
    $json = & $Uv run --directory $Repo proactive-mcp status 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    try {
        $parsed = ($json | Out-String | ConvertFrom-Json)
    }
    catch {
        return $null
    }
    if ($null -eq $parsed -or $null -eq $parsed.deliveries) { return $null }
    $total = 0
    if (-not [int]::TryParse([string] $parsed.deliveries.total, [ref] $total)) { return $null }
    if ($total -lt 0) { return $null }
    $warned = $false
    if ($null -ne $parsed.warnings -and @($parsed.warnings).Count -gt 0) { $warned = $true }
    return New-Object psobject -Property @{ Total = $total; Warned = $warned }
}

function Stop-Loud {
    param([string] $Reason)
    $sent = Send-FixedToast "Proactive check failed" "Open $Cli to inspect proactive status."
    $notify = if ($sent) { "sent" } else { "failed" }
    Write-Marker "result=failure reason=$Reason notify=$notify exit=4"
    exit 4
}

if (-not (Test-Path -LiteralPath $ToastScript -PathType Leaf)) {
    Write-Marker "result=failure reason=notifier_unavailable notify=none exit=3"
    exit 3
}

# Only one measurement at a time: a concurrent proactive_check would move
# deliveries.total and be misread as this run's delivery.
$lockDir = New-Item -ItemType Directory -Path $Lock -ErrorAction SilentlyContinue
if ($null -eq $lockDir) {
    Write-Marker "result=failure reason=concurrent_run notify=none exit=5"
    exit 5
}

try {
    $before = Get-DeliveryState
    if ($null -eq $before) { Stop-Loud "status_unreadable" }

    # All agent output is discarded. Nothing it says is read, parsed, or stored.
    if ($Cli -eq "codex") {
        & codex exec --ephemeral --sandbox read-only --skip-git-repo-check `
            -c mcp_servers.proactive.default_tools_approval_mode=approve `
            -C $AgentCwd $Prompt *> $null
    }
    else {
        & grok --cwd $AgentCwd --no-alt-screen --single $Prompt *> $null
    }
    $code = $LASTEXITCODE

    $after = Get-DeliveryState
    if ($null -eq $after) { Stop-Loud "status_unreadable" }

    if ($after.Total -lt $before.Total) { Stop-Loud "counter_regressed" }

    if ($after.Total -gt $before.Total) {
        if (Send-FixedToast "Proactive alert" "Open $Cli to review proactive status.") {
            Write-Marker "result=attention notify=sent agent_exit=$code exit=0"
            exit 0
        }
        Write-Marker "result=failure reason=notify_failed notify=failed agent_exit=$code exit=3"
        exit 3
    }

    if ($code -ne 0) {
        $sent = Send-FixedToast "Proactive check failed" "Open $Cli to inspect proactive status."
        $notify = if ($sent) { "sent" } else { "failed" }
        Write-Marker "result=failure reason=agent_failed notify=$notify agent_exit=$code exit=$code"
        exit $code
    }

    if ($after.Warned) {
        if (Send-FixedToast "Proactive status warning" "Open $Cli to inspect proactive source freshness.") {
            Write-Marker "result=status_warning notify=sent agent_exit=0 exit=0"
            exit 0
        }
        Write-Marker "result=failure reason=notify_failed notify=failed agent_exit=0 exit=3"
        exit 3
    }

    Write-Marker "result=no_delivery notify=none agent_exit=0 exit=0"
    exit 0
}
finally {
    Remove-Item -LiteralPath $Lock -Recurse -Force -ErrorAction SilentlyContinue
}
```

Written for Windows PowerShell 5.1: no ternary operator, no `??`, no
`ConvertFrom-Json -Depth`, and `New-Object psobject` instead of a class. The
approval override is passed unquoted so 5.1's native-argument handling can't
mangle it; Codex parses the value as TOML and falls back to the literal string
`approve`.

The agent's output is redirected to `$null` at the call site, so nothing it
says is ever compared, stored, or turned into toast text. Both notification
strings are constants in this file. The notifier path is checked before the
agent can claim anything, since a claim you can't announce is a lost
notification. A notification failure exits non-zero and must not trigger an
automatic retry: `proactive_check` has already delivered, and a retry would
claim the next batch instead of recovering this one.

Register the Codex task as the interactive user:

```powershell
$Script = "C:\Users\you\src\proactive-mcp-scripts\Invoke-ProactiveCheck.ps1"

$Action = New-ScheduledTaskAction `
    -Execute "PowerShell.exe" `
    -Argument "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$Script`" -Cli codex" `
    -WorkingDirectory "$env:USERPROFILE\.proactive-mcp\agent-cwd"

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

The task's working directory is the neutral agent directory, not the checkout,
for the same reason the wrapper passes `-C`: no project `AGENTS.md` should be
anywhere near a scheduled run.

For Grok, reuse the same script and register a separate task with `-Cli grok`,
`grok` in the task name, and a Grok description. Do not enable both recurring
tasks against the same database. They would race to claim the same situations,
and each would read the other's claim as its own delivery.
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

Read `deliveries.total` from that `status` output before and after the run, and
keep every other agent off the database while you test. With a synthetic pending
situation, pass only when the counter is one higher, the state moved to
`delivered`, the marker says
`result=attention notify=sent agent_exit=0 exit=0`, and the fixed toast appeared
in the user's session. A counter bump or state change without a toast means the
task consumed rather than delivered the situation, and that's a failure. A
second run with nothing pending should log `result=no_delivery`, or
`result=status_warning` with the status-warning toast when a source is stale or
the watcher has never run.

## Log privacy rules

From §9.2, non-negotiable.

- **Never persist raw `proactive_check` payloads.** Not to a cron log, not to a task log, not to a debug file. The response deliberately carries the minimum context a human summary needs: sender display names, subjects, event titles. Fine in a chat window you own, not fine on disk.
- **Never read agent output at all.** Scheduled wrappers redirect agent stdout and stderr to `/dev/null` or `$null` at the call site. Don't capture it, don't compare it, don't grep it, don't pass it to another process, and never use `-o/--output-last-message` or `--json` in a scheduled run. There's no token contract to salvage text for; the decision comes from the server's own `deliveries.total`.
- The only server data a wrapper may hold is two integers and a boolean: `deliveries.total` before, `deliveries.total` after, and whether `warnings` was non-empty. Warning strings themselves stay in memory, never in the log, never in a notification.
- Log only fixed fields: timestamp, CLI, `result`, optional fixed `reason`, `notify`, `agent_exit`, and exit code. `result` is one of `attention`, `status_warning`, `no_delivery`, or `failure`; `reason` is one of `notifier_unavailable`, `status_unreadable`, `counter_regressed`, `agent_failed`, `notify_failed`, or `concurrent_run`. Nothing outside those fixed sets, and no counter values, situation counts, titles, or warning text.
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
| Server healthy | `grok mcp doctor proactive` | `codex mcp get proactive`, and it shows `default_tools_approval_mode: approve` | `hermes mcp test proactive` | `get_status` from a chat |
| Daemon or degraded | `status` shows daemon state, or the missing-fallback warning is understood and accepted | same | same | same |
| Session-start rule | `AGENTS.md` at project root | `AGENTS.md` at workspace root | system prompt, self-contained job prompt | project instructions |
| Neutral working directory | `--cwd ~/.proactive-mcp/agent-cwd` in the one-shot and the wrapper | `-C ~/.proactive-mcp/agent-cwd`, plus `--skip-git-repo-check` | `--workdir` points at the neutral directory | n/a, tasks run in their own folder |
| Scheduled trigger | `deliveries.total` rises by one, state moves to `delivered`, fixed OS notification appears | same, and every `codex exec` carries `-c mcp_servers.proactive.default_tools_approval_mode=approve` | `hermes cron list` and delivered channel message | local scheduled task, ≥1 min |
| Quiet run | second run with nothing pending logs `result=no_delivery`, or `result=status_warning` with its own fixed text when a source is stale | same | n/a, the job reports in-channel | n/a |
| Dedupe | situation delivered once, never twice; one scheduled collector only | same | same | same |
| Logs clean | no payloads on disk, no agent output captured, only fixed marker fields | no payloads on disk, no agent output captured, only fixed marker fields | no payloads on disk | no payloads on disk |

Auditing a wrapper you inherited? Four things disqualify it: it reads or matches agent output instead of the delivery counter, it diffs `budget.used`, it starts the agent inside a repository, or it runs `codex exec` without the per-server approval override. Extract each POSIX wrapper and check it with `sh -n` before you install it.

Commands that were **not** executable in the verification environment, and are therefore sourced rather than tested: everything Claude Code Desktop-specific (not installed; sourced from Anthropic's local MCP guide and §5.3), and all Windows PowerShell snippets (verified on Linux; cmdlet shapes come from Microsoft's `Register-ScheduledTask` reference). Grok, Codex, Hermes, and `proactive-mcp` commands and flags were read from the installed binaries' own `--help` output at the versions listed at the top of this file.
