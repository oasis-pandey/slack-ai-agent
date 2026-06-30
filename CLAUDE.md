# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Status

**Working MVP, deployed-ready, tested.** A Slack bot answers Canvas questions end to
end via a Groq agent over canvas-mcp, with a clickable announcement UI (Block Kit list →
modal). Hackathon project (Slack Agent Builder Challenge, deadline Jul 13 2026).
`docs/planning.md` is the source of truth for milestone status and the running log of
plan corrections — read it.

Done: Slack ↔ agent ↔ canvas-mcp end to end; pytest suite + GitHub Actions CI;
containerized (Dockerfile + `railway.json`), build run + validated locally against a real
Canvas. **Next: write-to-Canvas features** (currently read-only).

## What This Is

A Canvas LMS assistant in Slack. @mention the bot in a thread, ask a natural-language
question about your Canvas (courses, assignments, to-dos, grades, announcements,
syllabus), and a ReAct agent answers with real data. Announcements come back as a
clickable list; clicking **View** opens a Slack modal with the full post. Read-only,
single-user MVP — the Canvas token is in `.env`, no OAuth.

## Architecture

One process, organized as the `canvas_bot/` package:

1. **Slack layer (`canvas_bot/main.py`)** — Bolt for Python in **Socket Mode**. Handles
   `app_mention` (dedupe retries, read thread, run agent) and the `view_announcement`
   action (open the modal). Posts a "Checking Canvas…" status message and then **edits it
   in place** (`chat_update`) with the final answer. Socket Mode (via `SLACK_APP_TOKEN`) =
   outbound WebSocket, no public URL / port.
2. **Agent (`canvas_bot/agent.py`)** — a ReAct loop on **Groq**
   (`llama-3.3-70b-versatile`). Takes conversation history, returns an `AgentResult`
   (final text + any structured announcements). Knows nothing about Slack; testable
   standalone: `python -m canvas_bot.agent "<question>"`.
3. **Canvas (`canvas_bot/canvas/`)** — `bridge.py` launches `canvas-mcp-server` over
   **stdio** and translates MCP ↔ Groq tool-calling; `rest.py` hits the Canvas REST API
   directly for structured announcement data (ids + HTML body) the bridge's text can't
   provide.
4. **Slack UI (`canvas_bot/slack/`)** — `helpers.py` (pure: dedupe, thread→history,
   name lookup) and `blocks.py` (pure: Block Kit announcement list + modal builders).
   Both are side-effect-free so they're unit-testable without a live Slack client.

Standalone checks (no Slack), run from the repo root: `python -m scripts.canvas_check`
(direct Canvas REST), `python -m scripts.canvas_mcp_check` (canvas-mcp over MCP).

### The critical detail: the Groq ↔ MCP bridge

Groq does **not** speak MCP natively. `canvas_bot/canvas/bridge.py` does two things: (a)
an MCP **client** that spawns canvas-mcp over stdio and calls its tools, and (b) **schema
translation** — MCP tool defs → Groq `tools` schema, and an MCP tool result → plain text.
canvas-mcp exposes ~92 tools; we whitelist 9 read-only student tools (`ALLOWED_TOOLS`) to
keep the prompt small and tool selection accurate.

### Groq tool-call gotcha (caused real bugs)

Groq validates the model's tool-call **arguments against the tool schema server-side** and
returns a 400 `tool_use_failed` if they don't match (e.g. the model emits a numeric course
id where canvas-mcp's schema wants a string). The agent catches this and feeds the error
back so the model self-corrects (bounded by `MAX_STEPS`). The system prompt also requires
`list_courses` first and passing the **numeric course id as a string** to course-specific
tools — otherwise the model guesses the course name and Canvas 404s.

### Announcement modal flow

`list_announcements` (canvas-mcp) returns formatted text with no ids, so when the model
lists announcements the agent **also** fetches structured records via `canvas/rest.py` and
returns them on `AgentResult.announcements`. `main.py` renders them as a Block Kit list
with a **View** button per item (value = `course_id:announcement_id`). Clicking fires the
`view_announcement` action: re-fetch that one announcement, convert its HTML body to Slack
mrkdwn, and `views_open` a modal. **Requires Slack Interactivity to be enabled** in the app
config (Socket Mode needs no Request URL — just the toggle).

### Reliability caps (in `canvas_bot/agent.py` / `main.py`)

- `MAX_STEPS=6` reason/act iterations; `GROQ_TIMEOUT=30s` per LLM call;
  `TOOL_TIMEOUT=25s` per Canvas call; `AGENT_TIMEOUT=75s` hard ceiling on a whole run.
- **Retry dedupe** by `client_msg_id` (`already_handled`). Essential: the handler is
  slow (>3s), so Slack redelivers the event and *without* dedupe each redelivery spawns
  a duplicate agent run + canvas-mcp subprocess.

### Conversation memory = Slack, not us

The agent is **stateless**. `build_history` re-reads the thread from Slack
(`conversations_replies`, paginated, most-recent `MAX_HISTORY=20`, names prefixed via
`users_info`) on every mention. No DB. Survives restarts. Only the **thread** is read.

## Setup & Commands

```bash
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

# Run the bot (long-running; Socket Mode). Stop with: pkill -f "canvas_bot.main"
python -m canvas_bot.main

# Tests (no network/secrets):
python -m pytest -q

# Standalone checks (no Slack), from the repo root:
python -m scripts.canvas_check                    # direct Canvas REST
python -m scripts.canvas_mcp_check                # canvas-mcp over MCP
python -m canvas_bot.agent "what's due this week?"  # full agent loop in the terminal

# Container (what Railway runs):
docker build -t canvas-slack-agent . && docker run --env-file .env canvas-slack-agent
```

**canvas-mcp is installed from git, not PyPI** (see `requirements.txt`); the Dockerfile
installs `git` so it resolves and puts `canvas-mcp-server` on PATH.

### Operational gotchas (these have bitten us)

- **Orphaned bots → duplicate replies.** Two connected Socket Mode clients make Slack
  round-robin events. Always fully stop the old process first: `pkill -f "canvas_bot.main"`.
- **`SSL: CERTIFICATE_VERIFY_FAILED` on startup** (macOS python.org Python): run once
  `"/Applications/Python 3.13/Install Certificates.command"`. Environment issue, not code.
- **`groq` must be ≥1.x.** `groq==0.9.0` breaks on current `httpx` (`proxies` kwarg).

## Configuration

Secrets in `.env` (gitignored):
`SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_SIGNING_SECRET`,
`CANVAS_API_TOKEN`, `CANVAS_BASE_URL`, `GROQ_API_KEY`, and **`CANVAS_API_URL`**.
canvas-mcp requires `CANVAS_API_URL` *including* the `/api/v1` path; `CANVAS_BASE_URL`
(no path) is used by `canvas_bot/canvas/rest.py` and the check scripts.

**Slack Bot Token Scopes**: `app_mentions:read`, `chat:write`, `channels:history`,
`groups:history`, `im:history`, `mpim:history`, `users:read`. Also enable **Interactivity**
(for the announcement modal). Adding scopes requires **Reinstall to Workspace**, which may
rotate `SLACK_BOT_TOKEN` — update `.env` if so.

## Conventions

- **Read-only.** If asked to write/modify Canvas, say writing isn't supported yet.
- **Never fabricate Canvas data.** Real tool results only; empty → friendly message.
  Don't relabel unrelated courses to fill a subset answer. (Enforced via the system prompt
  in `canvas_bot/agent.py`.)
- **Slack output stays minimal.** Lead with the answer, tight bullets only for lists, bold
  just the key term, friendly dates, no internal IDs, at most one emoji.
- Keep pure logic in `canvas_bot/slack/` (no import-time side effects) so it stays testable.
- Keep `docs/planning.md` current as implementation progresses.

### Known rough edges (next things to improve)

- `list_courses` returns archived + non-class enrollments (clubs, etc.); "active
  enrollment" ≠ "current-term class". Consider pre-filtering before the model sees it.
- The model occasionally tries a course *name* before resolving the numeric id (one wasted
  step); it self-corrects but a pre-resolved course list would be cleaner.
- Only thread context is read, not channel-level messages.
- HTML→Slack conversion in `slack/blocks.py` is best-effort (handles links, lists,
  bold/italics); exotic Canvas HTML may render plainly.
