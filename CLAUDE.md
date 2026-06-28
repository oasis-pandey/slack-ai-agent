# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Status

**Working MVP (Milestones 1–3 done).** A Slack bot answers Canvas questions end to
end via a Groq agent over canvas-mcp. Hackathon project (Slack Agent Builder Challenge,
deadline Jul 13 2026), built in milestones. `planning.md` is the source of truth for
decisions, milestone status, and the running log of plan corrections — read it.

Remaining: **Milestone 4 — deploy to cloud** (Railway/Render as a background worker).

## What This Is

A Canvas LMS assistant in Slack. @mention the bot in a thread, ask a natural-language
question about your Canvas (courses, assignments, to-dos, grades, announcements,
syllabus), and a ReAct agent answers with real data. Read-only, single-user MVP — the
Canvas token is in `.env`, no OAuth.

## Architecture

Three layers in one process:

1. **Slack layer (`app.py`)** — Bolt for Python in **Socket Mode**. Handles
   `app_mention`: dedupes Slack retries, reads the thread for context, runs the agent,
   posts the reply in-thread. Socket Mode (via `SLACK_APP_TOKEN`) = outbound WebSocket,
   no public URL / port.
2. **Agent (`agent.py`)** — a ReAct loop on **Groq** (`llama-3.3-70b-versatile`). Takes
   conversation history in, returns final text. Knows nothing about Slack, so it's
   testable standalone: `python agent.py "<question>"`.
3. **Bridge + Canvas tools (`canvas_tools.py`)** — launches `canvas-mcp-server` over
   **stdio** as a subprocess and translates between MCP and Groq's tool-calling format.

Standalone verification scripts (no Slack): `canvas_check.py` (direct Canvas REST),
`canvas_mcp_check.py` (canvas-mcp over MCP).

### The critical detail: the Groq ↔ MCP bridge

Groq does **not** speak MCP natively. `canvas_tools.py` does two things: (a) an MCP
**client** that spawns canvas-mcp over stdio and calls its tools, and (b) **schema
translation** — MCP tool defs → Groq `tools` schema, and an MCP tool result → plain
text. canvas-mcp exposes ~92 tools; we whitelist 9 read-only student tools
(`ALLOWED_TOOLS`) to keep the prompt small and tool selection accurate.

### Request flow

@mention → `app.py` (dedupe retry, `build_history` reads thread) → `run_agent(history)`
→ Groq picks a tool → `canvas_tools` calls canvas-mcp → Canvas REST API → result back to
Groq → loops or answers → Bolt posts in-thread. `app.py` posts "🔎 Checking Canvas…"
**only** when the agent's `on_tool_call` callback fires (i.e. it actually hits Canvas),
so plain chat ("hey") doesn't show it.

### Reliability caps (in `agent.py` / `app.py`)

- `MAX_STEPS=6` reason/act iterations; `GROQ_TIMEOUT=30s` per LLM call;
  `TOOL_TIMEOUT=25s` per Canvas call; `AGENT_TIMEOUT=75s` hard ceiling on a whole run.
- **Retry dedupe** by `client_msg_id` (`already_handled`). Essential: the handler is
  slow (>3s), so Slack redelivers the event and *without* dedupe each redelivery spawns
  a duplicate agent run + canvas-mcp subprocess (observed as `list_courses` firing in a
  loop).

### Conversation memory = Slack, not us

The agent is **stateless**. `build_history` re-reads the thread from Slack
(`conversations_replies`, paginated, most-recent `MAX_HISTORY=20`, names prefixed via
`users_info`) on every mention. No DB. Survives restarts. Only the **thread** is read —
not loose top-level channel messages. Cross-conversation memory (post-MVP) would need
real storage.

## Setup & Commands

```bash
source .venv/bin/activate
pip install -r requirements.txt

# Run the bot (long-running; Socket Mode). Stop with: pkill -f "app.py"
python app.py

# Standalone checks (no Slack):
python canvas_check.py                       # direct Canvas REST
python canvas_mcp_check.py                    # canvas-mcp over MCP
python agent.py "what's due this week?"       # full agent loop in the terminal
```

No test/lint tooling configured. **canvas-mcp is installed from git, not PyPI** (see
`requirements.txt`).

### Operational gotchas (these have bitten us)

- **Orphaned bots → duplicate replies.** Two connected Socket Mode clients make Slack
  round-robin events, so you get mixed/duplicate answers. Always fully stop the old
  process before starting a new one. `pkill -f "app.py"` (matches the real
  `/…/Python app.py` process; `pkill -f "python app.py"` can miss it due to capital-P
  and orphan the child).
- **`SSL: CERTIFICATE_VERIFY_FAILED` on startup** (macOS python.org Python): run once
  `"/Applications/Python 3.13/Install Certificates.command"`. Environment issue, not
  code/token.
- **`groq` must be ≥1.x.** `groq==0.9.0` breaks on current `httpx` (`proxies` kwarg).

## Configuration

Secrets in `.env` (gitignored):
`SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_SIGNING_SECRET`,
`CANVAS_API_TOKEN`, `CANVAS_BASE_URL`, `GROQ_API_KEY`, and **`CANVAS_API_URL`**.
Note: canvas-mcp requires `CANVAS_API_URL` *including* the `/api/v1` path (distinct from
our own `CANVAS_BASE_URL`, which has no path and is used by `canvas_check.py`).

**Slack Bot Token Scopes** needed: `app_mentions:read`, `chat:write`,
`channels:history`, `groups:history`, `im:history`, `mpim:history`, and `users:read`
(for speaker names; degrades gracefully to "Someone" without it). Adding scopes requires
**Reinstall to Workspace**, which may rotate `SLACK_BOT_TOKEN` — update `.env` if so.

## Conventions

- **Read-only.** If asked to write/modify Canvas, say writing isn't supported yet.
- **Never fabricate Canvas data.** Real tool results only; empty → friendly message.
  When asked for a subset (e.g. "math courses") and none match, say so — don't relabel
  unrelated courses. (Enforced via the system prompt in `agent.py`.)
- Keep `planning.md` current as implementation progresses.

### Known rough edges (next things to improve)

- `list_courses` returns archived + non-class enrollments (clubs, etc.); "active
  enrollment" ≠ "current-term class". Consider pre-filtering before the model sees it.
- Subject-filtering reasoning (e.g. "math courses") can still misfire on the model side;
  a cleaner pre-filtered course list helps more than prompt tweaks.
- Only thread context is read, not channel-level messages.
