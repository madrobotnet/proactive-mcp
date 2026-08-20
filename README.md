# proactive-mcp

**A proactive layer for every AI agent.**

proactive-mcp is a local-first MCP server that watches your Gmail and Google Calendar (read-only) and remembers what you tell your agents — so any MCP-capable agent can reach out to you *first* when something needs your attention: an email waiting for a reply, a calendar conflict, or your mother's birthday coming up in a week.

> **Status: planning.** Implementation has not started yet. The full product plan (in Korean) lives at [`docs/PRODUCT_PLAN.md`](docs/PRODUCT_PLAN.md), and this repository is developed AI-first following that plan.

## How it works

- A background watcher syncs Gmail/Calendar and evaluates deterministic **Situations** (reply deadline, calendar conflict, personal occasion).
- Agents call cheap MCP tools (`proactive_check`, `remember`, `recall`, ...) — via their platform's scheduler (Cursor Automations, Hermes Cron, heartbeats) or at session start — and deliver pending situations through **the channel you already use with that agent**.
- An attention policy (quiet hours, daily budget, cooldown, dedupe) keeps it silent unless speaking up is worth it.
- Everything stays on your machine in SQLite. V1 is strictly read-only; write actions will come later behind an approval-first contract.

This project is a pivot of [hermes-proactive](https://github.com/madrobotnet/hermes-proactive), rebuilt agent-neutral and MCP-first.

## License

MIT
