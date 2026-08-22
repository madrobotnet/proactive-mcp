# proactive-mcp

**A proactive layer for every AI agent.**

proactive-mcp is a local-first MCP server that watches your Gmail and Google Calendar (read-only) and remembers what you tell your agents — so any MCP-capable agent can reach out to you *first* when something needs your attention: an email waiting for a reply, a calendar conflict, or your mother's birthday coming up in a week.

> **Status:** M1.5. Conversation memory is stored in local SQLite. Google sync is not in this milestone. The product plan (in Korean) lives at [`docs/PRODUCT_PLAN.md`](docs/PRODUCT_PLAN.md).

## How it works

- A background watcher syncs Gmail/Calendar and evaluates deterministic **Situations** (reply deadline, calendar conflict, personal occasion).
- Agents call cheap MCP tools (`proactive_check`, `remember`, `recall`, ...) — via OS scheduling for Grok/Codex, Hermes Native Cron, Claude Code Desktop local tasks, or at session start — and deliver pending situations through **the channel you already use with that agent**.
- An attention policy (quiet hours, daily budget, cooldown, dedupe) keeps it silent unless speaking up is worth it.
- Everything stays on your machine in SQLite. V1 is strictly read-only; write actions will come later behind an approval-first contract.

This project is a pivot of [hermes-proactive](https://github.com/madrobotnet/hermes-proactive), rebuilt agent-neutral and MCP-first.

## Data location

The SQLite database stays on your machine.

- Linux and macOS: `~/.proactive-mcp/proactive.db`
- Windows: `%USERPROFILE%\.proactive-mcp\proactive.db`

On Linux and macOS the store directory is mode `0700` and the database, lock, and WAL sidecar files are mode `0600`. At the documented default Windows path, those same files get a protected DACL: inheritance is blocked, and only the current user is granted access. A custom `PROACTIVE_DATABASE` path must remain inside a directory owned by and private to the current user.

Windows Owner smoke steps (PowerShell, Grok CLI, and Codex CLI) are in [`docs/WINDOWS_SMOKE_TEST.md`](docs/WINDOWS_SMOKE_TEST.md).
Platform registration, daemon, session-start, and scheduler recipes are in
[`docs/INTEGRATIONS.md`](docs/INTEGRATIONS.md).

## License

MIT
