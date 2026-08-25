# Integration recipes (M5)

Named closed-alpha testers start by pasting the OS-specific block from [docs/testers/README.md](testers/README.md) into their existing local agent. Supply the wheel, SHA-256, and OAuth JSON locations when the sheet asks for them. The agent installs the wheel, completes Google setup, registers MCP, and verifies the result. Don't run setup commands before that paste.

The closed alpha ships as a private wheel, not through PyPI. `uvx proactive-mcp` in older notes or `docs/PRODUCT_PLAN.md` §12 is the post-publication path, not today's tester path. Source-tree commands remain below only as agent and developer reference.

The detailed Grok, Codex, and Claude Desktop material is retained as an [agent reference and appendix](#agent-reference-and-appendices). The Hermes appendix is an Owner-only validation path using Hermes Native Cron. It is not a closed-alpha tester path and does not replace Grok or Codex as the primary hosts.

**Verified on:** uv 0.11.29, grok 0.2.112, codex-cli 0.149.1, Hermes Agent v0.20.0, Linux (aarch64). Hermes was checked only through local help and harmless listing commands. Claude Code Desktop was not installed in the verification environment, so that section is marked and sourced accordingly.

## Contents

- [Scope](#scope)
- [OS tester sheet: one paste for your agent](#os-tester-sheet-one-paste-for-your-agent)
- [Agent and developer setup reference](#agent-and-developer-setup-reference)
  - [What every platform needs](#what-every-platform-needs)
  - [Source checkout and wheel facts](#source-checkout-and-wheel-facts)
  - [Google OAuth client secret](#google-oauth-client-secret)
- [The session-start rule](#the-session-start-rule)
- [The neutral agent directory](#the-neutral-agent-directory)
- [Agent reference and appendices](#agent-reference-and-appendices)
  - [Grok CLI](#appendix-a-grok-cli)
  - [Codex CLI](#appendix-b-codex-cli)
  - [Hermes Agent (Owner-only validation)](#appendix-c-hermes-agent-owner-only-validation)
  - [Claude Code Desktop (documentation only)](#appendix-d-claude-code-desktop-documentation-only)
  - [OS scheduler handoff](#appendix-e-os-scheduler-handoff)
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
| Hermes | Owner validation only | Hermes Native Cron | Owner-only recipe; excluded from tester onboarding |
| Claude Code Desktop | Yes | Local scheduled tasks, one-minute minimum | Documentation only, not demonstrated |

### Deliberately out of scope

Don't try these; the blocker is the platform, not our code.

- **ChatGPT web and desktop.** Web supports remote HTTPS connectors only. Desktop advertises stdio but the tools don't surface in the chat session ([openai/codex#38162](https://github.com/openai/codex/issues/38162)). Blocked until V2 HTTP transport.
- **Claude cloud Routines.** They run in Anthropic's cloud, so they have no path to a local process or the local SQLite file. A Routine can fire on schedule and still never reach this server. This is *not* the same thing as a Claude Code Desktop local scheduled task, which does work. See the contrast table in the [Claude section](#appendix-d-claude-code-desktop-documentation-only).
- **Cursor.** Removed from the supported set by Owner decision on 2026-08-22 ([#20](https://github.com/madrobotnet/proactive-mcp/issues/20)). Its Automations run as cloud agents, and a cloud agent can't spawn the local stdio process or read the local SQLite database, so there is no scheduled path to this server. Use Grok CLI or Codex CLI with the OS scheduler instead.
- **HTTP transport.** V1 speaks stdio only. Any recipe that points an agent at a URL is wrong for this version.
- **External write actions.** V1 holds `gmail.readonly` and `calendar.readonly`, nothing else, so it can't change anything in your Google account or anywhere else outside this machine. No recipe here should ask an agent to send mail or create events. External write actions arrive in V2 behind an approval-first contract. Read-only stops at the network boundary: the server still writes to its own local SQLite database, since `remember` stores a memory, `proactive_check` creates a short lease, and `confirm_delivery` and `snooze_situation` change situation state.

## OS tester sheet: one paste for your agent

Paste first. Open [docs/testers/README.md](testers/README.md), choose Windows, Linux, or macOS, and paste that sheet's complete block into the local agent you already use. Give the agent the artifact locations it requests, including the private wheel, its separately supplied SHA-256, and the OAuth JSON when supplied. The agent performs installation, MCP registration, `setup`, Google consent handoff, smoke checks, and verification.

For named testers, the private-wheel OS sheet is the only human path. Don't clone the repository, install the wheel, place the OAuth file, run `setup`, type `mcp add`, or edit JSON or TOML before pasting. Pick one scheduled collector, not both Grok and Codex. Hermes Native Cron is Owner-only and is not a tester alternative. The OS sheet keeps `serve` and `serve-scheduled` distinct: `serve` is loaded only in an interactive everyday conversation, while a separate scheduled conversation loads only `serve-scheduled` with `get_status`, `proactive_check`, and `confirm_delivery`. Never load both profiles into one conversation.

## Agent and developer setup reference

The sections through the appendices preserve implementation facts for the agent handling a tester-sheet request and for developers. They are not named-tester instructions.

## What every platform needs

The agent-reference recipes below follow the same four steps, in this order:

1. **Prerequisites and local stdio MCP registration.** Point the agent at the checkout.
2. **Watcher daemon, or the degraded-mode alternative.** The daemon is recommended, not required. Without it you lose OS notification fallback.
3. **A session-start rule** that calls `proactive_check` exactly once, applies the host filter, leaves uncertain leases unconfirmed or snoozes them, speaks to the user in the user's language, and only when choosing confirmation after review confirms the whole reviewed lease through English MCP.
4. **A separate scheduled conversation**, native where the platform has one, OS scheduler where it doesn't. It loads only `serve-scheduled`; the interactive everyday conversation loads only `serve`. Never load both profiles into one conversation.

Then verification and troubleshooting, which differ per platform.

## Source checkout and wheel facts

**Agent and developer reference, not named-tester instructions.** The source-checkout commands in this section document the verified environment and give an agent implementation detail when it needs one. Named closed-alpha testers use the private-wheel OS sheet above instead.

A source checkout needs Python ≥3.11, [uv](https://docs.astral.sh/uv/), and a stable absolute path, because schedulers and agent config files can't expand `~` reliably.

Throughout this document:

- Linux/macOS checkout: `/home/you/src/proactive-mcp`
- Windows checkout: `C:\Users\you\src\proactive-mcp`

### Source checkout, agent and developer reference

The repository stays private for the whole closed-alpha period, and the package is not on PyPI (`docs/PRODUCT_PLAN.md` §12). The following checkout path is for agents and developers with repository access. It is not a named-tester route.

**Source checkout.** The agent or developer needs collaborator access with at least Read permission on `madrobotnet/proactive-mcp`. An unauthenticated clone of a private repository fails with `Repository not found` or a `403`, which reads like a typo but is really an access problem.

```bash
gh auth login
gh auth status
gh repo clone madrobotnet/proactive-mcp /home/you/src/proactive-mcp
cd /home/you/src/proactive-mcp
uv python install 3.11
uv sync --locked
uv run proactive-mcp --help
```

If `gh auth status` says the agent or developer is logged out, or the token is missing the `repo` scope, `gh auth login --scopes repo` fixes it. Plain `git clone https://github.com/madrobotnet/proactive-mcp.git` works too, once `gh auth setup-git` has installed the credential helper.

**Private wheel, agent reference.** `docs/PRODUCT_PLAN.md` §12 makes this the named-tester artifact because it needs no repository access. The agent receives the `.whl` location from the tester, installs it into a controlled virtualenv, and no checkout lands on the tester's disk. The human path is the [OS tester sheet](#os-tester-sheet-one-paste-for-your-agent), before installation or Google setup.

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

For agent reference, an MCP registration changes so `"command"` becomes that absolute path and `"args"` shrinks to `["serve"]`. For `codex mcp add` and `grok mcp add`, the part after `--` becomes that path plus `serve`. On Windows the script is `C:\Users\you\venvs\proactive\Scripts\proactive-mcp.exe`. Everything else here, rules, daemon, schedulers, verification, is unchanged. The reference appendices are written for Option A because that's what the verification environment ran; the agent translates commands as above for a wheel.

### Google OAuth client secret

**Agent and developer reference.** `setup` runs the Google OAuth flow, and it can't start without an installed-app OAuth client secret file. During the closed alpha, the agent receives the Owner-provided client JSON location alongside the wheel location (`docs/PRODUCT_PLAN.md` §12) and places the file where `setup` will look. Testers explicitly assigned to validate the BYO path instead create an OAuth client of type **Desktop app** in their own Google Cloud project and supply that downloaded JSON location to the agent.

`setup --help` on this build:

```text
proactive-mcp setup [-h] [--reauth] [--headless] [--client-secrets PATH]
```

Resolution order for the secret file:

1. `--client-secrets PATH`, when the agent passes it.
2. The `PROACTIVE_GOOGLE_CLIENT_SECRETS` environment variable.
3. Default: `client_secret.json` beside the state database, so `~/.proactive-mcp/client_secret.json` on Linux and macOS.

An agent using the default path runs:

```bash
mkdir -p ~/.proactive-mcp
chmod 700 ~/.proactive-mcp
cp ~/Downloads/client_secret_*.json ~/.proactive-mcp/client_secret.json
chmod 600 ~/.proactive-mcp/client_secret.json
```

On Windows, the agent copies the delivered file to
`%USERPROFILE%\.proactive-mcp\client_secret.json`. It must not commit either the
Owner-provided file or a BYO file, or paste one into an issue.

An agent can instead retain the file at its supplied location and pass it explicitly:

```bash
uv run --directory /home/you/src/proactive-mcp proactive-mcp setup \
  --client-secrets /home/you/secrets/proactive-client.json
```

The agent adds `--headless` when the machine has no browser for the loopback authorization page, which is the usual case on a remote or server box. `--reauth` replaces an existing authorization after revoking access or changing scopes.

The agent then connects the read-only Google sources and confirms local state:

```bash
uv run --directory /home/you/src/proactive-mcp proactive-mcp setup
uv run --directory /home/you/src/proactive-mcp proactive-mcp status
```

`status` prints JSON. A fresh machine reports `"overall":"degraded"` with `not_configured` sources until `setup` completes, which is expected.

The stdio server command every agent will register is:

```bash
uv run --directory /home/you/src/proactive-mcp proactive-mcp serve
```

The agent records absolute paths for `uv` and the agent binaries, since schedulers get a minimal `PATH`:

```bash
command -v uv grok codex
```

The CLI surface is small. An agent or developer can confirm it with `uv run proactive-mcp --help`:

```text
proactive-mcp {serve,serve-scheduled,status,setup,disconnect,google-smoke,daemon,service}
proactive-mcp daemon [--once] [--poll-interval-minutes MINUTES]
```

`proactive_check` is an **MCP tool**, not a subcommand. You can't call it from a shell; an agent has to call it.

## The session-start rule

This is the same host contract on every platform. Copy it verbatim into the platform's rule or system prompt. Scheduled prompts repeat the same filter and use a separate conversation with only `serve-scheduled`.

```text
At the start of every new interactive everyday conversation, load only serve and
call proactive_check exactly once before answering the user. A separate scheduled
conversation loads only serve-scheduled. Never load both profiles into one
conversation.

Treat every reply_deadline as a conservative candidate, not an action verdict.
Before speaking, confidently drop newsletters, marketing, automated receipts,
FYI or FYI-CC with no ask, threads owned by someone else, and rows with no
question, request, or decision for this user. Keep explicit reply, RSVP, or
decision requests, user-owned deadlines, and unanswered questions directed to
this user.

Surface uncertain candidates, leave the whole lease unconfirmed, or use the
interactive profile to confirm and snooze them. Never silently discard
uncertainty as non-actionable. After reviewing every row, only when choosing confirmation and the result includes
a receipt_token, pass it to confirm_delivery exactly once for the entire reviewed
lease, including candidates confidently and silently dropped. If the host didn't
receive the result, or no receipt_token exists, don't confirm it.

Keep MCP tool names, descriptions, fields, and values in English. Speak to the
user in the user's language. If freshness warnings exist, say the result may be
incomplete and never claim "nothing to report" while a source is stale or failed.
If no candidates remain and freshness is healthy, say nothing about the check and
answer the user's actual request.
```

Why once: `proactive_check` takes no arguments and atomically leases the returned candidates (§5.1). `confirm_delivery` records the entire reviewed lease as delivered only after the result reached the host. Confidently filtered rows are part of that lease. An unconfirmed lease expires back to pending, while a confirmed row is deduped, so repeat checks add round trips without benefit.

## The neutral agent directory

Every CLI here loads `AGENTS.md` from its working directory. That's exactly what you want in a real project session: you put the canonical session-start rule in the repository's `AGENTS.md`, the agent merges it with whatever else that file says about the project, and `proactive_check` runs alongside your normal work. Merging is intentional there.

It's the wrong behavior for a fresh one-shot or a scheduled run. If the agent starts inside this checkout, or inside any repository, that project's `AGENTS.md` joins the scheduled prompt and can divert the run: extra tool calls, a code review it was never asked for, or a refusal that leaves the counter untouched. A scheduled check has one job.

Codex and Hermes one-shot runs use a dedicated directory that holds nothing:

```bash
mkdir -p ~/.proactive-mcp/agent-cwd
chmod 700 ~/.proactive-mcp/agent-cwd
```

On Windows that's `%USERPROFILE%\.proactive-mcp\agent-cwd`. Keep it empty: no `AGENTS.md`, `CLAUDE.md`, `.mcp.json`, or git repository. Codex receives explicit per-run profile overrides and needs `--skip-git-repo-check` there.

Grok is different because 0.2.112 has no per-run profile override. Its scheduled directory is `~/.proactive-mcp/grok-scheduled` and intentionally contains only the project-scoped `.grok/config.toml` for `proactive_scheduled`. It contains no instruction file or other project config. The Grok wrapper changes to that exact directory and gates both the MCP list and all-source doctor result before every run.

## Agent reference and appendices

These appendices are the implementation reference for the agent handling the tester-sheet request, and for maintainers auditing the resulting setup. They are not the primary human setup path.

## Appendix A: Grok CLI

Verified against grok 0.2.112.

### 1. Registration: two project directories, never user scope

Grok 0.2.112 merges `~/.grok/config.toml`, `~/.claude.json`, project `.mcp.json`, and project `.grok/config.toml`. It has no documented per-run MCP enable override. Consequently, prompt text cannot isolate a scheduled conversation from a globally visible full server.

Create two private, distinct Grok project directories. The interactive directory owns only `proactive`; the scheduled directory owns only `proactive_scheduled`:

```bash
GROK_INTERACTIVE_CWD="$HOME/.proactive-mcp/grok-interactive"
GROK_SCHEDULED_CWD="$HOME/.proactive-mcp/grok-scheduled"
mkdir -p "$GROK_INTERACTIVE_CWD" "$GROK_SCHEDULED_CWD"
chmod 700 "$GROK_INTERACTIVE_CWD" "$GROK_SCHEDULED_CWD"
```

First audit both directories. `doctor` is authoritative for every contributing source, including inherited Claude servers; in this build `list` can omit those inherited entries, so use both:

```bash
(cd "$GROK_INTERACTIVE_CWD" && grok mcp doctor && grok mcp list)
(cd "$GROK_SCHEDULED_CWD" && grok mcp doctor && grok mcp list)
```

Before claiming isolation, inspect the raw `~/.grok/config.toml`, scheduled `.grok/config.toml`, all top-level and `projects.*.mcpServers` entries in `~/.claude.json`, and scheduled `.mcp.json`, then remove or move every inherited `proactive` or duplicate `proactive_scheduled` registration. For a Grok user-scope registration, back up the config and use `grok mcp remove --scope user NAME`. For a Claude registration, use Claude's supported removal flow or move it into the interactive directory's project config; do not blindly edit or delete unrelated Claude settings. If an inherited full registration cannot be removed, **do not schedule Grok**: use the [Codex scheduled collector](#appendix-b-codex-cli) instead. Never keep simultaneous full and scheduled user registrations.

With both all-source doctor results and lists clean, register each server from its own directory at project scope:

```bash
(
  cd "$GROK_INTERACTIVE_CWD"
  grok mcp add --scope project proactive -- \
    /home/you/.local/bin/uv run --directory /home/you/src/proactive-mcp proactive-mcp serve
)
(
  cd "$GROK_SCHEDULED_CWD"
  grok mcp add --scope project proactive_scheduled -- \
    /home/you/.local/bin/uv run --directory /home/you/src/proactive-mcp proactive-mcp serve-scheduled
)
```

Everything after `--` is the server command. Open each directory once in interactive Grok and approve its folder-trust prompt; project-scoped servers do not start until their directory is trusted. Then repeat the all-source `doctor --json` and `list` checks. The interactive directory must expose exactly `proactive`. The scheduled raw files must contain exactly one relevant registration: project-scoped `proactive_scheduled`, whose command and args end in `proactive-mcp serve-scheduled`. Its doctor result must report a healthy handshake and exactly three tools. A hidden duplicate, malformed source, command drift, wrong scope, full profile, or any other tool count is a configuration failure, not something rules or a prompt can repair.

### 2. Watcher daemon, or degraded mode

```bash
uv run --directory /home/you/src/proactive-mcp proactive-mcp daemon
```

Degraded alternative: skip the daemon and let `proactive_check` lazy-sync inline. Same limitation as everywhere else, no daemon means no OS notification fallback, so an uncollected critical situation never reaches you through any other channel.

### 3. Session-start rule

Put [the rule](#the-session-start-rule) in `AGENTS.md` inside `$GROK_INTERACTIVE_CWD`; Grok loads project instructions from there. Start everyday Grok conversations with `--cwd "$GROK_INTERACTIVE_CWD"`, where the merged configuration exposes only `proactive`. For a single run you can append rules on the command line with `--rules`.

### 4. Scheduled trigger: none, hand off to the OS

Grok has no scheduler. Use `-p/--single` for a one-shot headless prompt, always from the trusted scheduled project directory. The scheduler wrapper below performs the mandatory raw-source and runtime profile gate before this command; do not run a prompt-only substitute:

```bash
grok --cwd "$GROK_SCHEDULED_CWD" --no-alt-screen --single 'Use the available proactive_scheduled server and call proactive_check exactly once. Treat reply_deadline as a conservative candidate, not an action verdict. Before speaking, confidently drop newsletters, marketing, automated receipts, FYI or FYI-CC with no ask, threads owned by someone else, and rows with no question, request, or decision for this user. Keep explicit reply, RSVP, or decision requests, user-owned deadlines, and unanswered questions directed to this user. Surface uncertainty, leave the whole lease unconfirmed, or defer it to an interactive conversation for snooze; never silently discard uncertainty as non-actionable. After reviewing every row, only when choosing confirmation, confirm the entire reviewed lease exactly once with a non-null receipt_token, including confidently and silently dropped candidates. Keep MCP tool content in English, but speak the user language. Report kept or uncertain candidates and freshness warnings; with healthy freshness and nothing kept, stay silent.'
```

For machine-readable output add `--output-format json` (values: `plain`, `json`, `streaming-json`). Full scheduler wiring is in [OS scheduler handoff](#appendix-e-os-scheduler-handoff).

### Verification

```bash
(cd "$GROK_INTERACTIVE_CWD" && grok mcp list && grok mcp doctor proactive && grok mcp doctor --json)
(cd "$GROK_SCHEDULED_CWD" && grok mcp list && grok mcp doctor proactive_scheduled && grok mcp doctor --json)
```

Both named `doctor` checks must be healthy, and an unfiltered `doctor --json` in each directory must show no second proactive profile from another source. The first list must contain `proactive` and not `proactive_scheduled`; the second must contain `proactive_scheduled` and not `proactive`. The scheduled wrapper parses all four raw sources before every run, scanning every Claude `projects` entry without trusting path spelling or aliases, then enforces project scope, the exact `proactive-mcp serve-scheduled` command tail, a healthy handshake, and exactly three discovered tools in `list`/`doctor`. It fails closed on malformed config, hidden duplicate scheduled registrations, any inherited full registration, command drift, or tool-surface drift.

### Troubleshooting

Grok writes MCP stderr to `~/.grok/logs/mcp/`. Start there when a launch fails.

| Symptom | Cause and fix |
|---|---|
| `folder untrusted (repo-local (project-scoped) server not started for an untrusted folder)` | Open that exact directory once in interactive Grok and approve the folder-trust prompt. Never work around this by moving either profile to user scope. |
| Server not listed at all | Run `grok mcp doctor` in the intended interactive or scheduled directory. Re-register the missing server there with `--scope project`; never use user scope for these two profiles. |
| Scheduled gate reports profile isolation failure | Inspect all four raw files named above, then run `grok mcp doctor --json` and `grok mcp list --json` in the scheduled directory. Remove duplicate/inherited registrations and restore project scope, the `proactive-mcp serve-scheduled` command tail, healthy handshake, and three-tool surface. If all conditions cannot be guaranteed, switch the scheduled collector to Codex. |
| Launch fails immediately | Check `~/.grok/logs/mcp/`. Usually `uv` isn't on `PATH`; use the absolute path in the command. |
| Doctor reports auth expired | `grok login`. Config-source diagnostics still work without it. |
| Headless run produces no tool call | Make the prompt itself name the tool. Rules may not load in every headless context; the prompt always does. |

## Appendix B: Codex CLI

Verified against codex-cli 0.149.1.

### 1. Registration

```bash
codex mcp add proactive -- \
  uv run --directory /home/you/src/proactive-mcp proactive-mcp serve

codex mcp add proactive_scheduled -- \
  uv run --directory /home/you/src/proactive-mcp proactive-mcp serve-scheduled
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
enabled = true
default_tools_approval_mode = "prompt"

[mcp_servers.proactive_scheduled]
command = "uv"
args = [
  "run",
  "--directory",
  "/home/you/src/proactive-mcp",
  "proactive-mcp",
  "serve-scheduled",
]
enabled = false
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
enabled = true
default_tools_approval_mode = "prompt"

[mcp_servers.proactive_scheduled]
command = "C:\\Users\\you\\.local\\bin\\uv.exe"
args = [
  "run",
  "--directory",
  "C:\\Users\\you\\src\\proactive-mcp",
  "proactive-mcp",
  "serve-scheduled",
]
enabled = false
default_tools_approval_mode = "approve"
```

`default_tools_approval_mode` accepts `auto`, `prompt`, `writes`, or `approve` on codex-cli 0.149.1; anything else fails config load with `unknown variant`. Keep the full interactive server on `prompt`. Only the separately named `proactive_scheduled` server may use `approve`, and keep it disabled outside a separate scheduled conversation.

V1 exposes **no Google or other external write actions**, but the full server still has consequential local tools: `remember`, `update`, `forget`, `snooze_situation`, `mute_situation`, and `acknowledge_situation`. The restricted `serve-scheduled` process exposes exactly `get_status`, `proactive_check`, and `confirm_delivery`; it cannot make those broader mutations even if a scheduled prompt or inherited instruction asks it to. `proactive_check` only leases returned situations, and `confirm_delivery` records delivery after the result reaches the host, so approving this narrow profile remains a deliberate authorization.

Confirm what actually landed on disk with `codex mcp get proactive` and `codex mcp get proactive_scheduled`. The full profile must read `prompt` and be enabled for interactive everyday conversations. The scheduled profile must read `approve`, use `serve-scheduled`, and stay disabled by default. A scheduled invocation reverses those two enable flags for that new conversation only.

### 2. Watcher daemon, or degraded mode

```bash
uv run --directory /home/you/src/proactive-mcp proactive-mcp daemon
```

Degraded alternative: no daemon, inline lazy sync on `proactive_check`, and **no OS notification fallback**. `get_status` reports `daemon.status` as `not_running` plus the warning, so the agent can tell you the fallback path is dark.

### 3. Session-start rule

Codex has no automatic session hook for this. Put [the rule](#the-session-start-rule) in `AGENTS.md` at the repository or workspace root. Interactive everyday conversations load only the enabled `proactive` profile. Repeat the full host contract inside each scheduled prompt.

### 4. Scheduled trigger: none, hand off to the OS

`codex exec` runs non-interactively, from the [neutral agent directory](#the-neutral-agent-directory):

```bash
codex exec \
  --ephemeral \
  --sandbox read-only \
  --skip-git-repo-check \
  -c mcp_servers.proactive.enabled=false \
  -c mcp_servers.proactive_scheduled.enabled=true \
  -c mcp_servers.proactive_scheduled.default_tools_approval_mode=approve \
  -C "$HOME/.proactive-mcp/agent-cwd" \
  'This is a separate scheduled conversation with only proactive_scheduled loaded. Call proactive_check exactly once. Treat reply_deadline as a conservative candidate, not an action verdict. Before speaking, confidently drop newsletters, marketing, automated receipts, FYI or FYI-CC with no ask, threads owned by someone else, and rows with no question, request, or decision for this user. Keep explicit reply, RSVP, or decision requests, user-owned deadlines, and unanswered questions directed to this user. Surface uncertainty, leave the whole lease unconfirmed, or defer it to an interactive conversation for snooze; never silently discard uncertainty as non-actionable. After reviewing every row, only when choosing confirmation, confirm the entire reviewed lease exactly once with a non-null receipt_token, including confidently and silently dropped candidates. Keep MCP tool content in English, but speak the user language. Never load the interactive profile in this conversation. Report kept or uncertain candidates and freshness warnings concisely. Do not modify files or run unrelated commands.'
```

The `-c` overrides disable the full profile, enable the narrow scheduled profile, and repeat its approval setting for this new conversation. Carry all three in every non-interactive example so a scheduled run cannot load both profiles or be silently broken by a config edit. Never approve the full `proactive` profile. Interactive `codex` conversations keep only `proactive` enabled with `prompt`, because a human is there to approve consequential tools.

Flags, all from `codex exec --help`: `--ephemeral` skips persisting session files, `-s/--sandbox read-only` blocks filesystem writes by model-generated shell commands, `-C/--cd` sets the working root, `--skip-git-repo-check` allows non-repo directories, `-c/--config` overrides one config key, `--json` emits JSONL events, `-o/--output-last-message FILE` writes the final message to a file.

The sandbox and the approval mode govern two different things, and it's worth keeping them straight. `--sandbox read-only` constrains the *agent's own shell*: no editing files, no `git commit`, no scribbling in your checkout. It says nothing about MCP tools. The proactive server runs as its own process and updates its local database through its own code path, so a claim or a `remember` still lands with the sandbox at `read-only`. That's intended. The agent needs no shell access at all for this job, which is why the tightest sandbox is the right choice.

Scheduler wiring is in [OS scheduler handoff](#appendix-e-os-scheduler-handoff).

### Verification

```bash
codex mcp list
codex mcp get proactive
codex mcp get proactive_scheduled
codex doctor
```

Then start an interactive `codex` session and confirm exactly one `proactive_check` call before the first answer.

### Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Server missing from `codex mcp list` | You edited the wrong file. Check whether `CODEX_HOME` is set; that overrides `~/.codex`. |
| `config.toml` rejected | Run with `--strict-config` to surface unrecognized fields, and check TOML backslash escaping on Windows. |
| Server starts, tools never used | Name `proactive_check` explicitly in the prompt. Rule files are advisory. |
| Non-interactive run stalls or ends with no tool call | Confirm `codex mcp get proactive_scheduled` shows `serve-scheduled` and `default_tools_approval_mode: approve`, then pass the scheduled-profile override shown above. Do not approve the full profile. |
| `unknown variant` on startup | The approval mode is misspelled. Only `auto`, `prompt`, `writes`, and `approve` are accepted. |
| `codex exec` fails outside a git repo | Add `--skip-git-repo-check`. |
| Agent tries to run a shell command or edit a file | Keep `--sandbox read-only`. This job needs no shell at all, so an attempt means the prompt drifted. Local database writes by the MCP server itself are normal and aren't affected by the sandbox. |

## Appendix C: Hermes Agent (Owner-only validation)

Hermes remains outside named tester onboarding. Grok CLI and Codex CLI are the primary closed-alpha hosts. This section lets the Owner validate generic stdio interoperability and Hermes Native Cron without adding any proactive-mcp-specific package or runtime boundary. The commands below match Hermes Agent v0.20.0 local help.

Start by reading the installed command surface:

```bash
hermes mcp --help
hermes mcp add --help
hermes cron --help
hermes cron create --help
```

### Interactive Owner check

Register only the interactive server. `--args` must be the final option:

```bash
hermes mcp add proactive \
  --command /home/you/venvs/proactive/bin/proactive-mcp \
  --args serve
hermes mcp test proactive
hermes mcp list
```

Open a new Hermes conversation with only `proactive` loaded and apply [the complete session-start rule](#the-session-start-rule). That rule treats `reply_deadline` as a conservative candidate, not an action verdict. It drops newsletters, marketing, automated receipts, no-ask FYI or FYI-CC, someone else's threads, and rows with no question, request, or decision for this user. It keeps explicit reply, RSVP, or decision requests, user-owned deadlines, and unanswered questions directed to this user. Uncertainty is surfaced, left unconfirmed, or snoozed, never silently discarded as non-actionable. Only when choosing confirmation after review is the whole reviewed lease confirmed, including confidently and silently dropped candidates. MCP stays English while Hermes speaks the user's language.

Finish that conversation, then remove its registration before the Native Cron check:

```bash
hermes mcp remove proactive
hermes mcp list
```

### Hermes Native Cron Owner check

Register only the restricted scheduled server. Never keep the interactive registration beside it:

```bash
hermes mcp add proactive_scheduled \
  --command /home/you/venvs/proactive/bin/proactive-mcp \
  --args serve-scheduled
hermes mcp test proactive_scheduled
hermes mcp list
```

Create a Native Cron job with Hermes itself. Don't use `--script` or `--no-agent`; the scheduled Hermes conversation must call the MCP tools. Keep the working directory neutral and the prompt self-contained:

```bash
hermes cron create \
  --name proactive-owner-check \
  --deliver local \
  --workdir /home/you/.proactive-mcp/agent-cwd \
  '*/15 * * * *' \
  'This is a separate scheduled conversation with only proactive_scheduled loaded. Call proactive_check exactly once. Treat reply_deadline as a conservative candidate, not an action verdict. Before speaking, confidently drop newsletters, marketing, automated receipts, FYI or FYI-CC with no ask, threads owned by someone else, and rows with no question, request, or decision for this user. Keep explicit reply, RSVP, or decision requests, user-owned deadlines, and unanswered questions directed to this user. Surface uncertainty or leave the whole lease unconfirmed; never silently discard uncertainty as non-actionable. Snooze is reserved for a separate interactive conversation. After reviewing every row, only when choosing confirmation, confirm the entire reviewed lease exactly once with a non-null receipt_token, including confidently and silently dropped candidates. Keep MCP tool content in English, but speak the user language. Never load the interactive profile in this conversation. Report kept or uncertain candidates and freshness warnings; with healthy freshness and nothing kept, stay silent.'
```

`hermes cron create` prints the job ID. Use that exact ID for the harmless inspection and explicit Owner run:

```bash
hermes cron list
hermes cron status
hermes cron run JOB_ID
hermes cron runs --limit 5 JOB_ID
```

The explicit run is optional and stays Owner-only. It requests execution on the next scheduler tick; it does not justify a repeated-run reliability suite. Inspect the conversation for one check, conditional whole-lease confirmation, user-language speech, and no interactive profile. Then clean up both the job and registration:

```bash
hermes cron remove JOB_ID
hermes mcp remove proactive_scheduled
hermes cron list
hermes mcp list
```

Don't ship this job or copy it into tester sheets. Don't keep both registrations, and don't treat an Owner Hermes result as a substitute for Grok and Codex acceptance.

## Appendix D: Claude Code Desktop (documentation only)

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

Put [the rule](#the-session-start-rule) in the project instructions or custom instructions for the project where you use this server. This interactive everyday conversation loads only `serve`.

### 4. Scheduled trigger: local scheduled task

Create a **local** scheduled task in Claude Code Desktop as a separate conversation with only `serve-scheduled` enabled. Never enable the interactive `serve` profile in that task.

The JSON above is interactive-only. For a dedicated scheduled-only Desktop setup, replace that registration with one named `proactive_scheduled` whose final argument is `serve-scheduled`. Don't put both registrations in the same Desktop setup. If the installed Desktop build cannot keep them separate, use the Codex scheduled recipe instead.

- Prompt: "This is a separate scheduled conversation with only serve-scheduled loaded. Call proactive_check exactly once. Treat reply_deadline as a conservative candidate, not an action verdict. Before speaking, confidently drop newsletters, marketing, automated receipts, FYI or FYI-CC with no ask, threads owned by someone else, and rows with no question, request, or decision for this user. Keep explicit reply, RSVP, or decision requests, user-owned deadlines, and unanswered questions directed to this user. Surface uncertainty or leave the whole lease unconfirmed; never silently discard uncertainty as non-actionable. Snooze is reserved for a separate interactive conversation. After reviewing every row, only when choosing confirmation, confirm the entire reviewed lease exactly once with a non-null receipt_token, including confidently and silently dropped candidates. Keep MCP tool content in English, but speak the user language. Never load the interactive profile in this conversation. Present kept or uncertain candidates and freshness warnings, and don't claim an all-clear while a source is stale."
- Recurrence: your choice, minimum one minute. One minute is the floor documented in §5.3; something like 15 minutes is saner in practice.
- Execution: local, on this machine, with only the `proactive_scheduled` MCP server enabled.

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

## Appendix E: OS scheduler handoff

Grok CLI and Codex CLI have no scheduler, so the OS supplies one. Each trigger
starts a separate scheduled conversation with only `serve-scheduled`; everyday
conversations load only `serve`. Never load both profiles into one conversation.
The scheduled prompt repeats the full host filter, whole-lease confirmation,
uncertainty, English MCP, and user-language contract. If the host loses the
response, it must not call `confirm_delivery`; the short lease then expires and
the candidates become eligible for a later agent or the fallback path.

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
when `confirm_delivery` commits a host-received result. Higher after than before means
something was delivered into a session nobody is watching, which is precisely
when the user needs a notification. An unchanged value means nothing was
confirmed.

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
includes critical deliveries. `fallback.claimed` is wrong for a third reason: it
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
| Grok raw/merged config, command, scope, handshake, or three-tool surface fails validation | `result=failure reason=profile_isolation` | fixed failure | 4 |

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
`get_status`. A confirmed situation is not offered again; an unconfirmed lease
expires safely, so do not add an immediate retry loop.

### Linux and macOS: cron

Create `~/bin/proactive-cron-grok`:

```sh
#!/bin/sh
set -u

PATH="/home/you/.local/bin:/home/you/.grok/bin:/usr/local/bin:/usr/bin:/bin"
REPO="/home/you/src/proactive-mcp"
AGENT_CWD="$HOME/.proactive-mcp/grok-scheduled"
LOGDIR="$HOME/.proactive-mcp/logs"
LOG="$LOGDIR/grok-cron.log"
LOCK="$HOME/.proactive-mcp/scheduler.lock"
PROMPT='This is a separate scheduled conversation with only proactive_scheduled loaded. Call proactive_check exactly once. Treat reply_deadline as a conservative candidate, not an action verdict. Before speaking, confidently drop newsletters, marketing, automated receipts, FYI or FYI-CC with no ask, threads owned by someone else, and rows with no question, request, or decision for this user. Keep explicit reply, RSVP, or decision requests, user-owned deadlines, and unanswered questions directed to this user. Surface uncertainty or leave the whole lease unconfirmed; never silently discard uncertainty as non-actionable. Snooze is reserved for a separate interactive conversation. After reviewing every row, only when choosing confirmation, confirm the entire reviewed lease exactly once with a non-null receipt_token, including confidently and silently dropped candidates. Keep MCP tool content in English, but speak the user language. Never load the interactive profile in this conversation. Call no other tool, then stop.'

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

# Only one measurement may be in flight: a concurrent confirmed check would
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

# Configuration is the security boundary. Grok's merged list and doctor can
# hide a lower-precedence duplicate, so inspect every raw source as well. Every
# Claude project entry is scanned, regardless of path or alias spelling. This
# parser is embedded in the wrapper; it creates no installed helper or config.
grok_profile_gate() {
  uv run --directory "$REPO" python - "$HOME" "$AGENT_CWD" <<'PY' >/dev/null 2>&1 || return 1
import json
from pathlib import Path
import sys
import tomllib

home, project = map(Path, sys.argv[1:])
entries = []

def add_toml(path, source):
    if not path.exists():
        return
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    servers = data.get("mcp_servers", {})
    if not isinstance(servers, dict):
        raise TypeError("mcp_servers must be a table")
    for name in ("proactive", "proactive_scheduled"):
        if name in servers:
            if not isinstance(servers[name], dict):
                raise TypeError("server must be a table")
            entries.append((source, name, servers[name]))

def add_servers(servers, source):
    if not isinstance(servers, dict):
        raise TypeError("mcpServers must be an object")
    for name, server in servers.items():
        if not isinstance(server, dict):
            raise TypeError("server must be an object")
        if name in ("proactive", "proactive_scheduled"):
            entries.append((source, name, server))

def add_json(path, source, scan_projects=False):
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("config must be an object")
    add_servers(data.get("mcpServers", {}), source)
    if not scan_projects:
        return
    projects = data.get("projects", {})
    if not isinstance(projects, dict):
        raise TypeError("projects must be an object")
    for project_path, settings in projects.items():
        if not isinstance(project_path, str) or not isinstance(settings, dict):
            raise TypeError("Claude project must be an object")
        add_servers(settings.get("mcpServers", {}), "user_claude_project")

try:
    add_toml(home / ".grok" / "config.toml", "user_grok")
    add_toml(project / ".grok" / "config.toml", "project_grok")
    add_json(home / ".claude.json", "user_claude", scan_projects=True)
    add_json(project / ".mcp.json", "project_mcp")
    if len(entries) != 1:
        raise ValueError("expected one raw proactive registration")
    source, name, server = entries[0]
    if source != "project_grok" or name != "proactive_scheduled":
        raise ValueError("scheduled registration must be project-only")
    command = server.get("command")
    args = server.get("args", [])
    if not isinstance(command, str) or not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        raise TypeError("invalid command or args")
    tokens = [command, *args]
    if len(tokens) < 2 or Path(tokens[-2]).name not in {"proactive-mcp", "proactive-mcp.exe"} or tokens[-1] != "serve-scheduled":
        raise ValueError("scheduled command drift")
except (OSError, UnicodeError, json.JSONDecodeError, tomllib.TOMLDecodeError, TypeError, ValueError):
    sys.exit(1)
PY

  LIST_JSON=$(cd "$AGENT_CWD" && grok mcp list --json 2>/dev/null) || return 1
  printf '%s' "$LIST_JSON" | uv run --directory "$REPO" python -c '
import json
from pathlib import Path
import sys
try:
    servers = json.load(sys.stdin)
    if len(servers) != 1:
        raise ValueError
    server = servers[0]
    tokens = [server["command"], *server.get("args", [])]
    valid = (
        server["name"] == "proactive_scheduled"
        and server["scope"] == "project"
        and len(tokens) >= 2
        and Path(tokens[-2]).name in {"proactive-mcp", "proactive-mcp.exe"}
        and tokens[-1] == "serve-scheduled"
    )
    if not valid:
        raise ValueError
except (json.JSONDecodeError, KeyError, TypeError, ValueError):
    sys.exit(1)
' >/dev/null 2>&1 || return 1

  DOCTOR_JSON=$(cd "$AGENT_CWD" && grok mcp doctor --json 2>/dev/null || true)
  printf '%s' "$DOCTOR_JSON" | uv run --directory "$REPO" python -c '
import json, sys
try:
    servers = json.load(sys.stdin)["servers"]
    if len(servers) != 1:
        raise ValueError
    server = servers[0]
    passed = {item["label"] for item in server["checks"] if item["passed"]}
    valid = (
        server["name"] == "proactive_scheduled"
        and server["healthy"] is True
        and any(label.startswith("handshake OK") for label in passed)
        and "3 tools discovered" in passed
    )
    if not valid:
        raise ValueError
except (json.JSONDecodeError, KeyError, TypeError, ValueError):
    sys.exit(1)
' >/dev/null 2>&1
}

grok_profile_gate || fail_status profile_isolation

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
PROMPT='This is a separate scheduled conversation with only proactive_scheduled loaded. Call proactive_check exactly once. Treat reply_deadline as a conservative candidate, not an action verdict. Before speaking, confidently drop newsletters, marketing, automated receipts, FYI or FYI-CC with no ask, threads owned by someone else, and rows with no question, request, or decision for this user. Keep explicit reply, RSVP, or decision requests, user-owned deadlines, and unanswered questions directed to this user. Surface uncertainty or leave the whole lease unconfirmed; never silently discard uncertainty as non-actionable. Snooze is reserved for a separate interactive conversation. After reviewing every row, only when choosing confirmation, confirm the entire reviewed lease exactly once with a non-null receipt_token, including confidently and silently dropped candidates. Keep MCP tool content in English, but speak the user language. Never load the interactive profile in this conversation. Call no other tool, then stop.'

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
  -c mcp_servers.proactive.enabled=false \
  -c mcp_servers.proactive_scheduled.enabled=true \
  -c mcp_servers.proactive_scheduled.default_tools_approval_mode=approve \
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
(cd ~/.proactive-mcp/grok-scheduled && grok mcp list && grok mcp doctor proactive_scheduled && grok mcp doctor --json)
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
`proactive_check` by hand to "check"; a confirmed manual result changes the
counter that the wrapper is measuring.

### Windows: Task Scheduler

If Grok is the collector, first create `%USERPROFILE%\.proactive-mcp\grok-interactive` and `%USERPROFILE%\.proactive-mcp\grok-scheduled`. Audit `grok mcp doctor` and `grok mcp list` from both directories and inspect the raw Grok user/project TOML, top-level and every nested Claude project MCP entry, plus project JSON; remove or move inherited full and duplicate scheduled registrations. Register full `proactive` with `--scope project` from the interactive directory and `proactive_scheduled` with `--scope project` from the scheduled directory, trust both directories interactively, and verify each list and all-source doctor result contains only its assigned profile. If the inherited full profile cannot be removed from the scheduled merged config, schedule Codex instead.

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
$AgentCwd = if ($Cli -eq "grok") {
    Join-Path $env:USERPROFILE ".proactive-mcp\grok-scheduled"
} else {
    Join-Path $env:USERPROFILE ".proactive-mcp\agent-cwd"
}
$LogDir   = Join-Path $env:USERPROFILE ".proactive-mcp\logs"
$Log      = Join-Path $LogDir "$Cli-task.log"
$Lock     = Join-Path $env:USERPROFILE ".proactive-mcp\scheduler.lock"
$Uv       = (Get-Command uv).Source
$ToastScript = (& $Uv run --directory $Repo python -c `
    "from proactive_mcp.delivery.notify import WINDOWS_TOAST_SCRIPT; print(WINDOWS_TOAST_SCRIPT)").Trim()
$Prompt = "This is a separate scheduled conversation with only proactive_scheduled loaded. Call proactive_check exactly once. Treat reply_deadline as a conservative candidate, not an action verdict. Before speaking, confidently drop newsletters, marketing, automated receipts, FYI or FYI-CC with no ask, threads owned by someone else, and rows with no question, request, or decision for this user. Keep explicit reply, RSVP, or decision requests, user-owned deadlines, and unanswered questions directed to this user. Surface uncertainty or leave the whole lease unconfirmed; never silently discard uncertainty as non-actionable. Snooze is reserved for a separate interactive conversation. After reviewing every row, only when choosing confirmation, confirm the entire reviewed lease exactly once with a non-null receipt_token, including confidently and silently dropped candidates. Keep MCP tool content in English, but speak the user language. Never load the interactive profile in this conversation. Call no other tool, then stop."
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

function Test-GrokProfileIsolation {
    $rawCheck = @'
import json
from pathlib import Path
import sys
import tomllib

home, project = map(Path, sys.argv[1:])
entries = []

def add_toml(path, source):
    if not path.exists():
        return
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    servers = data.get("mcp_servers", {})
    if not isinstance(servers, dict):
        raise TypeError("mcp_servers must be a table")
    for name in ("proactive", "proactive_scheduled"):
        if name in servers:
            if not isinstance(servers[name], dict):
                raise TypeError("server must be a table")
            entries.append((source, name, servers[name]))

def add_servers(servers, source):
    if not isinstance(servers, dict):
        raise TypeError("mcpServers must be an object")
    for name, server in servers.items():
        if not isinstance(server, dict):
            raise TypeError("server must be an object")
        if name in ("proactive", "proactive_scheduled"):
            entries.append((source, name, server))

def add_json(path, source, scan_projects=False):
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("config must be an object")
    add_servers(data.get("mcpServers", {}), source)
    if not scan_projects:
        return
    projects = data.get("projects", {})
    if not isinstance(projects, dict):
        raise TypeError("projects must be an object")
    for project_path, settings in projects.items():
        if not isinstance(project_path, str) or not isinstance(settings, dict):
            raise TypeError("Claude project must be an object")
        add_servers(settings.get("mcpServers", {}), "user_claude_project")

try:
    add_toml(home / ".grok" / "config.toml", "user_grok")
    add_toml(project / ".grok" / "config.toml", "project_grok")
    add_json(home / ".claude.json", "user_claude", scan_projects=True)
    add_json(project / ".mcp.json", "project_mcp")
    if len(entries) != 1:
        raise ValueError("expected one raw proactive registration")
    source, name, server = entries[0]
    if source != "project_grok" or name != "proactive_scheduled":
        raise ValueError("scheduled registration must be project-only")
    command = server.get("command")
    args = server.get("args", [])
    if not isinstance(command, str) or not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        raise TypeError("invalid command or args")
    tokens = [command, *args]
    if len(tokens) < 2 or Path(tokens[-2]).name not in {"proactive-mcp", "proactive-mcp.exe"} or tokens[-1] != "serve-scheduled":
        raise ValueError("scheduled command drift")
except (OSError, UnicodeError, json.JSONDecodeError, tomllib.TOMLDecodeError, TypeError, ValueError):
    sys.exit(1)
'@
    & $Uv run --directory $Repo python -c $rawCheck $env:USERPROFILE $AgentCwd *> $null
    if ($LASTEXITCODE -ne 0) { return $false }

    Push-Location $AgentCwd
    try {
        $json = & grok mcp list --json 2>$null
        if ($LASTEXITCODE -ne 0) { return $false }
        try { $servers = @($json | Out-String | ConvertFrom-Json) }
        catch { return $false }
        if ($servers.Count -ne 1) { return $false }
        $listed = $servers[0]
        $tokens = @([string] $listed.command) + @($listed.args)
        if ($listed.name -ne "proactive_scheduled" -or $listed.scope -ne "project") { return $false }
        if ($tokens.Count -lt 2 -or (Split-Path -Leaf $tokens[-2]) -notin @("proactive-mcp", "proactive-mcp.exe") -or $tokens[-1] -ne "serve-scheduled") { return $false }

        $doctorJson = & grok mcp doctor --json 2>$null
        try { $doctorServers = @(($doctorJson | Out-String | ConvertFrom-Json).servers) }
        catch { return $false }
        if ($doctorServers.Count -ne 1) { return $false }
        $scheduled = $doctorServers[0]
        if ($scheduled.name -ne "proactive_scheduled" -or $scheduled.healthy -ne $true) { return $false }
        $passed = @($scheduled.checks | Where-Object { $_.passed -eq $true } | ForEach-Object { $_.label })
        if (@($passed | Where-Object { $_ -like "handshake OK*" }).Count -ne 1) { return $false }
        return (@($passed | Where-Object { $_ -eq "3 tools discovered" }).Count -eq 1)
    }
    finally {
        Pop-Location
    }
}

if (-not (Test-Path -LiteralPath $ToastScript -PathType Leaf)) {
    Write-Marker "result=failure reason=notifier_unavailable notify=none exit=3"
    exit 3
}

# Only one measurement at a time: a concurrent confirmed check would move
# deliveries.total and be misread as this run's delivery.
$lockDir = New-Item -ItemType Directory -Path $Lock -ErrorAction SilentlyContinue
if ($null -eq $lockDir) {
    Write-Marker "result=failure reason=concurrent_run notify=none exit=5"
    exit 5
}

try {
    if ($Cli -eq "grok" -and -not (Test-GrokProfileIsolation)) {
        Stop-Loud "profile_isolation"
    }

    $before = Get-DeliveryState
    if ($null -eq $before) { Stop-Loud "status_unreadable" }

    # All agent output is discarded. Nothing it says is read, parsed, or stored.
    if ($Cli -eq "codex") {
        & codex exec --ephemeral --sandbox read-only --skip-git-repo-check `
            -c mcp_servers.proactive.enabled=false `
            -c mcp_servers.proactive_scheduled.enabled=true `
            -c mcp_servers.proactive_scheduled.default_tools_approval_mode=approve `
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
agent can reserve anything. A notification failure exits non-zero and must not
trigger an automatic retry: `confirm_delivery` has already committed the batch,
and a retry could collect a different one instead of recovering the toast.

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
`grok` in the task name, `-WorkingDirectory "$env:USERPROFILE\.proactive-mcp\grok-scheduled"`, and a Grok description. The script changes to that directory and runs the same bounded raw TOML/JSON parser as the POSIX wrapper before checking `list` and all-source `doctor --json`. It rejects relevant MCPs in every nested Claude project entry regardless of path alias, and requires one project registration ending in `proactive-mcp(.exe) serve-scheduled`, one healthy handshake, and exactly three tools; malformed config, hidden duplicates, full registrations, wrong scope/command, and tool drift all fail before it reserves a lease. Do not enable both recurring
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
- Log only fixed fields: timestamp, CLI, `result`, optional fixed `reason`, `notify`, `agent_exit`, and exit code. `result` is one of `attention`, `status_warning`, `no_delivery`, or `failure`; `reason` is one of `notifier_unavailable`, `status_unreadable`, `counter_regressed`, `agent_failed`, `notify_failed`, `concurrent_run`, or `profile_isolation`. Nothing outside those fixed sets, and no counter values, situation counts, titles, or warning text.
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
| Registered | project scope only: interactive directory lists only `proactive`; scheduled directory only `proactive_scheduled` | `codex mcp list`, scheduled disabled by default | Owner only: `hermes mcp list`, one profile at a time | one profile in the conversation tool list |
| Server healthy | named `grok mcp doctor` succeeds in each project directory; scheduled wrapper repeats it | full `proactive` is enabled and `prompt`; scheduled is disabled, uses `serve-scheduled`, and is `approve` | Owner only: `hermes mcp test` for the one registered profile | `get_status` from a chat |
| Daemon or degraded | `status` shows daemon state, or the missing-fallback warning is understood and accepted | same | Owner decides before validation | same |
| Host contract | candidate filter, entire reviewed lease, uncertainty, English MCP, user language | same | same, Owner only | same |
| Conversation isolation | distinct trusted project directories; raw-source plus list/doctor gate rejects full, duplicates, command/scope/tool drift | scheduled overrides disable full and enable restricted; interactive does the reverse | remove interactive before Native Cron registration | select only `serve` for interactive or `serve-scheduled` for a separate task |
| Session-start rule | `AGENTS.md` at project root | `AGENTS.md` at workspace root | complete rule in a fresh Owner conversation | project instructions |
| Neutral working directory | `--cwd ~/.proactive-mcp/grok-scheduled`, whose project config contains only scheduled | `-C ~/.proactive-mcp/agent-cwd`, plus `--skip-git-repo-check` | Native Cron `--workdir` | task runs in its own folder |
| Scheduled trigger | `deliveries.total` rises by one, state moves to `delivered`, fixed OS notification appears | same, and every `codex exec` enables only `proactive_scheduled` | Owner only: `hermes cron list`, `status`, and explicit `run JOB_ID` | local scheduled task, at least 1 min |
| Quiet run | second run with nothing pending logs `result=no_delivery`, or `result=status_warning` with its own fixed text when a source is stale | same | n/a | n/a |
| Dedupe | situation delivered once, never twice; one scheduled collector only | same | n/a | same |
| Logs clean | no payloads on disk, no agent output captured, only fixed marker fields | no payloads on disk, no agent output captured, only fixed marker fields | n/a | no payloads on disk |

Auditing a wrapper you inherited? Seven things disqualify it: it reads or matches agent output instead of the delivery counter, it diffs `budget.used`, it starts the agent inside a repository, it approves the full `proactive` profile, it loads both profiles in one conversation, it runs Grok without raw-source, exact command/scope, `list`, and handshake/three-tool `doctor --json` gates, or it runs `codex exec` without all narrow scheduled-profile overrides. Extract each POSIX wrapper and check it with `sh -n` before you install it.

Commands that were **not** executable in the verification environment, and are therefore sourced rather than tested: everything Claude Code Desktop-specific (not installed; sourced from Anthropic's local MCP guide and §5.3), all Windows PowerShell snippets (checked on Linux; cmdlet shapes come from Microsoft's `Register-ScheduledTask` reference), and Hermes job creation or execution. Grok, Codex, Hermes, and `proactive-mcp` command shapes were read from the installed binaries' own `--help` output at the versions listed at the top of this file. Harmless MCP and cron listings were run locally. Hermes remains Owner-only; no job or registration was created during documentation verification.
