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

| Layer | Module | Role |
|-------|--------|------|
| **Slack** | `canvas_bot/main.py` | Bolt for Python in Socket Mode. Handles `app_mention`, reads the thread for context, posts/edits the reply in-thread, and opens the announcement modal. |
| **Agent** | `canvas_bot/agent.py` | A ReAct loop on Groq (`llama-3.3-70b-versatile`). Takes conversation history, returns the answer (+ any structured announcements). Knows nothing about Slack. |
| **Canvas** | `canvas_bot/canvas/` | `bridge.py` spawns `canvas-mcp-server` over stdio and translates MCP ↔ Groq tool-calling; `rest.py` hits the Canvas REST API directly for structured announcement data. |
| **Slack UI** | `canvas_bot/slack/` | `helpers.py` (pure: dedupe, thread→history); `blocks.py` (pure: Block Kit list + modal builders). |

### Project layout

```
canvas_bot/            application package
  main.py              Slack wiring + entry point (python -m canvas_bot.main)
  agent.py             ReAct loop
  canvas/bridge.py     Groq ↔ canvas-mcp bridge
  canvas/rest.py       direct Canvas REST (structured announcements)
  slack/helpers.py     dedupe + thread→history (pure)
  slack/blocks.py      Block Kit list & modal builders (pure)
scripts/               standalone smoke checks (canvas_check, canvas_mcp_check)
tests/                 pytest suite (no network/secrets)
docs/planning.md       milestone log & design decisions
```

### Request flow

```
@mention
  → main.py            dedupe Slack retry, read thread → history
  → run_agent()        Groq reasons, picks a Canvas tool
  → canvas/bridge.py   calls canvas-mcp over stdio → Canvas REST API
  → result back to Groq → loops or answers
  → main.py            edits the status message in place with the answer
```

### The Groq ↔ MCP bridge

Groq doesn't speak MCP natively. `canvas/bridge.py` does two things: (a) acts as an MCP
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
python -m canvas_bot.main
# Stop it with: pkill -f "canvas_bot.main"

# Standalone checks (no Slack), from the repo root:
python -m scripts.canvas_check               # direct Canvas REST
python -m scripts.canvas_mcp_check           # canvas-mcp over MCP
python -m canvas_bot.agent "what's due this week?"   # full agent loop in the terminal
```

## Tests

Pure logic — the Groq↔MCP bridge, retry dedupe, and thread→history building — is
covered by a `pytest` suite that needs no network or secrets (the pure Slack helpers
and Block Kit builders live in `canvas_bot/slack/` precisely so they're importable
without a live `auth_test()`). CI runs them on every push and PR.

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

No public URL, database, or open port is required.

## Conventions

- **Read-only.** If asked to write or modify Canvas, the bot says writing isn't
  supported yet.
- **Never fabricates Canvas data.** Real tool results only; empty results get a
  friendly message rather than invented data.

## Troubleshooting

- **Duplicate replies** — two connected Socket Mode clients make Slack round-robin
  events. Fully stop the old process (`pkill -f "canvas_bot.main"`) before starting a new one.
- **`SSL: CERTIFICATE_VERIFY_FAILED` on startup** (macOS python.org build) — run once:
  `"/Applications/Python 3.13/Install Certificates.command"`.
- **`groq` errors on `proxies` kwarg** — needs `groq>=1.x`; `0.9.0` breaks on current
  `httpx`.

## Status

Working MVP — Slack ↔ agent ↔ Canvas end to end, with a clickable announcement modal, a
test suite + CI, and a containerized Railway deploy. Next up: write-to-Canvas features.
See [`docs/planning.md`](docs/planning.md) for the full milestone log and design decisions.
