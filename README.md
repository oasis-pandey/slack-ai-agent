# Canvas Slack Agent

[![CI](https://github.com/oasis-pandey/slack-ai-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/oasis-pandey/slack-ai-agent/actions/workflows/ci.yml)

A **Canvas LMS assistant that lives in Slack.** @mention the bot in a thread, ask a
natural-language question about your Canvas — courses, assignments, to-dos, grades,
announcements, or syllabus — and a ReAct agent answers with real data pulled live from
Canvas.

Read-only, single-user MVP. Built for the Slack Agent Builder Challenge.

```
You:       @CanvasBot what's due this week?
CanvasBot: 🔎 Checking Canvas…
CanvasBot: Here's what's coming up:
           • Homework 3 — CS301 — due Mon Jun 30
           • Essay Draft — ENG202 — due Wed Jul 2
```

## How it works

Three layers run in **one process**:

| Layer | File | Role |
|-------|------|------|
| **Slack** | `app.py` | Bolt for Python in Socket Mode. Handles `app_mention`, reads the thread for context, posts the reply in-thread. |
| **Agent** | `agent.py` | A ReAct loop on Groq (`llama-3.3-70b-versatile`). Takes conversation history, returns the final answer. Knows nothing about Slack. |
| **Bridge + tools** | `canvas_tools.py` | Spawns `canvas-mcp-server` over stdio and translates between MCP and Groq's tool-calling format. |

### Request flow

```
@mention
  → app.py        dedupe Slack retry, read thread → history
  → run_agent()   Groq reasons, picks a Canvas tool
  → canvas_tools  calls canvas-mcp over stdio → Canvas REST API
  → result back to Groq → loops or answers
  → app.py        posts the reply in-thread
```

### The Groq ↔ MCP bridge

Groq doesn't speak MCP natively. `canvas_tools.py` does two things: (a) acts as an MCP
**client** that launches canvas-mcp and calls its tools, and (b) does **schema
translation** — MCP tool definitions → Groq `tools` schema, and MCP tool results → plain
text. canvas-mcp exposes ~92 tools; we whitelist **9 read-only student tools**
(`ALLOWED_TOOLS`) to keep the prompt small and tool selection accurate.

### Conversation memory = Slack

The agent is **stateless**. On every mention, `build_history` re-reads the thread from
Slack (`conversations_replies`, most-recent 20 messages, speaker names prefixed). No
database — survives restarts. Only the thread is read, not loose channel messages.

### Reliability

- `MAX_STEPS=6` reason/act iterations · `GROQ_TIMEOUT=30s` per LLM call ·
  `TOOL_TIMEOUT=25s` per Canvas call · `AGENT_TIMEOUT=75s` hard ceiling per run.
- **Retry dedupe** by `client_msg_id` — the handler is slow (>3s), so Slack redelivers
  events; without dedupe each redelivery would spawn a duplicate agent run.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> **Note:** canvas-mcp installs from git (not PyPI) and is listed in
> `requirements.txt`. The bot spawns `canvas-mcp-server`, so it must be on your `PATH`.

Create a `.env` file (gitignored) with:

```
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_SIGNING_SECRET=...
CANVAS_API_TOKEN=...
CANVAS_BASE_URL=https://canvas.youruniversity.edu      # no path
CANVAS_API_URL=https://canvas.youruniversity.edu/api/v1 # includes /api/v1 (canvas-mcp needs this)
GROQ_API_KEY=...
```

**Required Slack bot scopes:** `app_mentions:read`, `chat:write`, `channels:history`,
`groups:history`, `im:history`, `mpim:history`, `users:read`. Adding scopes requires
**Reinstall to Workspace**, which may rotate `SLACK_BOT_TOKEN` — update `.env` if so.

## Running

```bash
source .venv/bin/activate

# Run the bot (long-running, Socket Mode — no public URL needed)
python app.py
# Stop it with: pkill -f "app.py"

# Standalone checks (no Slack):
python canvas_check.py                    # direct Canvas REST
python canvas_mcp_check.py                # canvas-mcp over MCP
python agent.py "what's due this week?"   # full agent loop in the terminal
```

## Tests

Pure logic — the Groq↔MCP bridge, retry dedupe, and thread→history building — is
covered by a `pytest` suite that needs no network or secrets (the Slack helpers were
split into `slack_helpers.py` precisely so they're importable without a live
`auth_test()`). CI runs them on every push and PR.

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
```

## Deploy (Railway)

The bot runs as a **long-lived worker**, not a web service — Socket Mode opens an
outbound WebSocket, so there's no port to expose. It's containerized via the
`Dockerfile` (which installs `git` so canvas-mcp's git dependency resolves, and puts
`canvas-mcp-server` on `PATH`).

1. Create a new Railway project from this GitHub repo. Railway reads `railway.json` and
   builds the `Dockerfile`.
2. Add every `.env` key as a Railway **service variable**: `SLACK_BOT_TOKEN`,
   `SLACK_APP_TOKEN`, `SLACK_SIGNING_SECRET`, `CANVAS_API_TOKEN`, `CANVAS_BASE_URL`,
   `CANVAS_API_URL`, `GROQ_API_KEY`.
3. Deploy. Watch the logs for `⚡️ Canvas agent is running (Socket Mode)…`, then
   @mention the bot in Slack.

No public URL, database, or open port is required. `Procfile` (`worker: python app.py`)
is included for platforms like Render/Heroku that prefer it.

## Conventions

- **Read-only.** If asked to write or modify Canvas, the bot says writing isn't
  supported yet.
- **Never fabricates Canvas data.** Real tool results only; empty results get a
  friendly message rather than invented data.

## Troubleshooting

- **Duplicate replies** — two connected Socket Mode clients make Slack round-robin
  events. Fully stop the old process (`pkill -f "app.py"`) before starting a new one.
- **`SSL: CERTIFICATE_VERIFY_FAILED` on startup** (macOS python.org build) — run once:
  `"/Applications/Python 3.13/Install Certificates.command"`.
- **`groq` errors on `proxies` kwarg** — needs `groq>=1.x`; `0.9.0` breaks on current
  `httpx`.

## Status

Working MVP — Milestones 1–3 done (Slack ↔ server, Canvas connected, full end-to-end
agent), with a test suite + CI and a containerized Railway deploy (Milestone 4). See
[`planning.md`](planning.md) for the full milestone log and design decisions.
