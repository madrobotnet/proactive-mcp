# Integration recipes

proactive-mcp is an **agent-dependent local MCP server**. It detects and queues
situations, but it does not contain an agent, model, conversation runtime, or
message-delivery channel. It never launches Grok, Codex, Hermes, another host
agent, or an LLM, and it never sends a prompt to one.

Install from PyPI with `uvx proactive-mcp`. A source checkout
(`uv run --directory …`) is for development.

## Runtime ownership

| Component | Owner | May do |
|---|---|---|
| `proactive-mcp daemon` | proactive-mcp | Local source sync, deterministic situation evaluation, queue maintenance, and the documented critical-only OS notification fallback |
| `proactive-mcp serve` | host MCP client | Expose the everyday MCP tool surface over stdio while that host conversation is running |
| `proactive-mcp serve-scheduled` | host MCP client | Expose only `get_status`, `proactive_check`, and `confirm_delivery` over stdio while that host conversation is running |
| Agent/model process and conversation | host/operator | Start a conversation, select a model, load one MCP profile, call tools, and present output through the host's channel |
| Schedule trigger | host/operator | Optionally start a dedicated host-agent run with a dedicated restricted MCP profile |

`serve-scheduled` is a restricted **server surface**, not a scheduler or
collector launcher. Starting it alone waits for a connected MCP client. It does
not call its own tools. If the host agent is off, proactive-mcp does not create a
host process, conversation, or delivery. Pending situations remain pending until
an already-running or host-scheduled agent explicitly calls `proactive_check`.

The daemon is independent background local work. It may sync, evaluate, queue,
and perform the documented critical-only OS fallback. It never invokes an
agent/LLM, sends a prompt, or turns a queued situation into an agent
conversation. An OS fallback notification is not agent delivery and does not
mark the situation delivered.

## Mandatory profile isolation

Everyday and scheduled MCP profiles must never be loaded in the same host
conversation:

- An everyday interactive conversation loads only `serve`.
- A dedicated scheduled or manually initiated restricted conversation loads only
  `serve-scheduled`.
- Profile isolation is an operator/host responsibility outside proactive-mcp.
  The plugin neither proves configuration-source isolation nor starts or
  verifies the host.
- A host-native scheduler or OS cron may start a dedicated agent run only when
  the host provides a dedicated per-run MCP profile containing only
  `serve-scheduled`.
- If that host/version cannot provide dedicated per-run MCP isolation, automated
  scheduled use is unsupported. Fail closed by not scheduling the run.
- Interactive/manual use remains possible by starting a fresh conversation with
  a known dedicated restricted profile.

Prompt text, a working directory, approval mode, merged-list output, or a
wrapper that scans config files is not an isolation boundary. Do not build a
gate-and-launch wrapper around a model CLI, inspect delivery counters to decide
what the model did, auto-select another host, or let proactive-mcp choose a
fallback agent.

## Delivery contract

The host that explicitly calls `proactive_check` must apply all of these rules:

1. Treat each `reply_deadline` as a conservative candidate, not an action
   verdict.
2. Before speaking, confidently filter newsletters, marketing, automated
   receipts, no-ask FYI/FYI-CC, threads owned by someone else, and rows with no
   question, request, or decision for this user.
3. Keep explicit reply, RSVP, or decision requests, user-owned deadlines, and
   unanswered questions directed to this user.
4. Surface uncertainty, leave the whole lease unconfirmed, or use the everyday
   interactive profile to snooze it. Never silently discard uncertainty as
   non-actionable.
5. Only after reviewing every row and choosing confirmation, call
   `confirm_delivery` exactly once with the non-null `receipt_token` for the
   entire reviewed lease, including confidently and silently filtered rows. If
   the host did not receive the result or no token exists, do not confirm.
6. Keep MCP tool names, descriptions, fields, and values in English. Speak to
   the user in the user's language.
7. Report freshness warnings. Never claim an all-clear while a source is stale
   or failed.

An unconfirmed lease expires back to pending. This is the intended fail-closed
behavior when an agent is absent, a conversation ends, or output is not
received.

## Installation command shapes

Use an absolute executable path in host MCP configuration. The public shape
is:

```bash
uvx proactive-mcp serve
```

The restricted registration is the same command with `serve-scheduled`:

```bash
uvx proactive-mcp serve-scheduled
```

A development checkout uses:

```bash
/home/you/.local/bin/uv run --directory /home/you/src/proactive-mcp proactive-mcp serve
```

Windows uses the matching absolute `uvx` path. V1 supports local stdio only;
do not configure an HTTP URL.

## Host notes

### Grok CLI 0.2.112

Grok 0.2.112 merges MCP registrations from native Grok, Claude, Cursor, and
project sources. proactive-mcp cannot prove those merged sources form an
immutable per-run profile, and project-directory conventions or pre-launch
source scans do not change that. **Do not advertise or configure unattended
Grok scheduling on 0.2.112.** There is no plugin-provided Grok gate, wrapper, or
fallback selector.

Manual use remains possible. The operator may maintain a dedicated restricted
host profile and open a fresh Grok conversation that exposes only
`proactive_scheduled` backed by `serve-scheduled`. This is an operator assertion,
not something proactive-mcp verifies. Keep the everyday `serve` profile out of
that conversation.

### Codex CLI

Codex can register both command shapes, but its config layers and `-c` overrides
are not claimed by proactive-mcp to provide immutable isolation. The plugin does
not inspect `CODEX_HOME`, approve tools, disable other servers, or verify what a
Codex run loaded.

A representative registration shape is shown only so operators can recognize
the two surfaces:

```toml
[mcp_servers.proactive]
command = "uvx"
args = ["proactive-mcp", "serve"]
enabled = true
default_tools_approval_mode = "prompt"

[mcp_servers.proactive_scheduled]
command = "uvx"
args = ["proactive-mcp", "serve-scheduled"]
enabled = false
default_tools_approval_mode = "approve"
```

A development checkout substitutes
`uv run --directory /home/you/src/proactive-mcp proactive-mcp`.

Never auto-approve the full profile. Automated scheduled use is supported only
if the installed Codex host and operator can create a dedicated per-run MCP
profile in which the full profile and all other unintended sources are absent.
If that cannot be established outside the plugin, do not schedule Codex.
Manual restricted use remains possible in a fresh dedicated profile.

### Hermes Agent

Hermes remains an Owner-only interoperability path. Hermes Native Cron is
host-owned: Hermes, not proactive-mcp, creates the job, starts the agent run,
selects the model, and delivers through its channel. The Owner may validate it
only with a dedicated scheduled host profile containing `serve-scheduled` and
without a simultaneous everyday profile. Do not add a Hermes adapter, package,
handshake, session tracker, helper, or plugin-managed cron artifact.

#### Native Cron Owner recipe

The commands below were checked against Hermes Agent v0.20.0 local `--help`.
They are an Owner-operated host recipe, not proactive-mcp scheduling. Hermes
starts its own agent for the job. `proactive-mcp` only serves the restricted
stdio surface after Hermes connects to it.

Before registration, finish any everyday Hermes conversation. The scheduled
profile must contain `proactive_scheduled` backed by `serve-scheduled`, and it
must not contain the everyday `proactive` registration. Remove the everyday
registration before the scheduled check:

```bash
hermes mcp remove proactive
hermes mcp list
```

Register and inspect the restricted scheduled surface. `--args` is the final
option required by Hermes:

```bash
hermes mcp add proactive_scheduled \
  --command /home/you/venvs/proactive/bin/proactive-mcp \
  --args serve-scheduled
hermes mcp test proactive_scheduled
hermes mcp list
```

Confirm the list contains `proactive_scheduled` and not `proactive` before
creating the job. Do not use `--script` or `--no-agent`: this is a Hermes agent
conversation that explicitly calls MCP tools. No plugin gate, counter, selector,
or host-launch wrapper is involved.

Create the private workdir before creating the Native Cron job. Hermes requires
an existing absolute directory for `--workdir`:

```bash
install -d -m 700 /home/you/.proactive-mcp/agent-cwd
```

The prompt carries the delivery contract because this is a separate scheduled
conversation:

```bash
hermes cron create \
  --name proactive-owner-check \
  --deliver local \
  --workdir /home/you/.proactive-mcp/agent-cwd \
  '*/15 * * * *' \
  'This is a separate scheduled conversation with only proactive_scheduled loaded. Call proactive_check exactly once. Treat reply_deadline as a conservative candidate, not an action verdict. Before speaking, confidently drop newsletters, marketing, automated receipts, FYI or FYI-CC with no ask, threads owned by someone else, and rows with no question, request, or decision for this user. Keep explicit reply, RSVP, or decision requests, user-owned deadlines, and unanswered questions directed to this user. Surface uncertainty, leave the whole lease unconfirmed, or defer it to an interactive conversation for snooze. Never silently discard uncertainty as non-actionable. After reviewing every row, only when choosing confirmation, confirm the entire reviewed lease exactly once with a non-null receipt_token, including confidently and silently dropped candidates. Keep MCP tool content in English, but speak the user's language. Never load the interactive profile in this conversation. Report kept or uncertain candidates and freshness warnings concisely. With healthy freshness and nothing kept, stay silent.'
```

`hermes cron create` prints the job ID. Substitute that exact value for
`JOB_ID`. `hermes cron list` is the Hermes v0.20 job-information surface; it
shows the created job's state, schedule, delivery, workdir, and execution
fields. Inspect it and the scheduler before requesting one manual run:

```bash
hermes cron list
hermes cron status
hermes cron run JOB_ID
hermes cron runs --limit 5 JOB_ID
```

`hermes cron run` requests one execution. Depending on gateway state, Hermes
may dispatch it immediately or at the next scheduler tick. Inspect the one
recorded attempt with `hermes cron runs --limit 5 JOB_ID`; do not retry it as a
suite. Inspect that one run for exactly one check, conditional whole-lease
confirmation, user-language speech, freshness warnings, and no everyday
profile.

Remove the Owner check and its restricted registration when finished:

```bash
hermes cron remove JOB_ID
hermes mcp remove proactive_scheduled
hermes cron list
hermes mcp list
```

Do not treat this Owner result as a substitute for Grok or Codex acceptance.

### Claude Code Desktop

Local Desktop tasks and cloud Routines are different. A local Desktop task may
work only if that Desktop version can start a separate conversation with a
per-task MCP profile containing only `serve-scheduled`. If it cannot, do not
schedule it. Cloud Routines cannot reach this local stdio server. Interactive
manual use remains available with one dedicated profile at a time.

### Other hosts

The same rule applies universally: local stdio is necessary but not sufficient.
The host must own the agent lifecycle and, for automation, provide a dedicated
per-run MCP profile. proactive-mcp supplies neither a model nor a generic host
launcher. Remote-only hosts wait for a future transport design; V1 does not add
HTTP, adapters, or a server-side LLM.

## Watcher daemon and degraded mode

The recommended local watcher is:

```bash
uvx proactive-mcp daemon
```

A development checkout uses that environment's `proactive-mcp daemon`
instead.

A user service, LaunchAgent, or Windows scheduled task may keep **this daemon
process** running. That service starts proactive-mcp local background work only;
it must not launch an agent command. Without the daemon, an explicit
`proactive_check` can lazy-sync and evaluate, but periodic sync and OS fallback
are unavailable. `get_status` reports that degraded state.

Do not confuse the daemon service with host scheduling:

| Trigger target | Result |
|---|---|
| `proactive-mcp daemon` | Background local sync/evaluation/queue work; no agent conversation |
| `proactive-mcp serve-scheduled` | Restricted stdio server waiting for a host; no tool call by itself |
| Host-native dedicated agent task | Host starts an agent that may explicitly call restricted MCP tools |

## Verification

### Interactive everyday

Start a fresh host conversation configured with only `serve`. Confirm the host
can call `get_status` and calls `proactive_check` once under the delivery
contract. Do not load `serve-scheduled` there.

### Manual restricted

Start a separate fresh host conversation configured with only
`serve-scheduled`. Confirm its discovered tools are exactly `get_status`,
`proactive_check`, and `confirm_delivery`. This is a manual host check, not a
plugin proof of future scheduled-run isolation.

### Agent-off behavior

1. Leave a synthetic situation pending in a non-production database.
2. Start only `proactive-mcp daemon` or `serve-scheduled`; do not start a host
   agent.
3. Verify no Grok, Codex, Hermes, other model, or conversation process appears
   and the situation remains pending.
4. Later connect a mock or real host to `serve-scheduled` and explicitly call
   `proactive_check`; verify the queued situation is returned with a lease.
5. Confirm only after the host receives and reviews the whole result.

Never use live Google or personal data for this architecture check.

## Troubleshooting

| Symptom | Meaning |
|---|---|
| `serve-scheduled` is running but nothing is delivered | Expected without a connected host-agent tool call |
| A pending situation remains while the host is off | Expected persistence and fail-closed behavior |
| Both profiles appear in one conversation | Stop that conversation; fix the host profile before trying again |
| Host cannot guarantee a dedicated per-run profile | Automated scheduling is unsupported; use manual restricted or everyday interactive delivery |
| Daemon is stopped | Explicit checks may lazy-sync, but periodic sync and OS fallback are unavailable |

Logs and reports must contain only redacted states and counters. Never attach
MCP payloads, source text, OAuth material, databases, host config, or raw agent
transcripts.
