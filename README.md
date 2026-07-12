# Canvas Slack Agent

[![CI](https://github.com/oasis-pandey/slack-ai-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/oasis-pandey/slack-ai-agent/actions/workflows/ci.yml)

A **Canvas LMS assistant that lives in Slack.** @mention the bot in a thread, ask a
natural-language question about your Canvas — courses, assignments, to-dos, grades,
announcements, or syllabus — and a ReAct agent answers with real data pulled live from
Canvas.

Single-user MVP. Reads are the core; a small, confirmation-gated set of write actions
(create announcements/discussions, post/reply, add a private to-do) is also supported.
Built for the Slack Agent Builder Challenge.

```
You:       @CanvasBot what's due this week?
CanvasBot: 🔎 Checking Canvas…
CanvasBot: Here's what's coming up:
           • Homework 3 — CS301 — due Mon Jun 30
           • Essay Draft — ENG202 — due Wed Jul 2
```

## Privacy Note
This bot stores your Canvas Personal Access Token and Base URL securely to personalize your dashboard and answer your questions.
- **Storage:** Tokens are encrypted at rest using AES (Fernet) and stored in a database (local SQLite or cloud Postgres), on a per-user basis. They are never logged or stored in plain text.
- **Disconnect:** You can completely remove your credentials and delete your data from the database at any time by clicking the **Disconnect Canvas** button at the bottom of the App Home dashboard.

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
  canvas/rest.py       direct Canvas REST (structured announcements, planner notes)
  canvas/local_tools.py  non-MCP agent tools (private to-do / planner note)
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
text. canvas-mcp exposes ~92 tools; we whitelist **15** in `ALLOWED_TOOLS` (11 read-only +
4 write) to keep the prompt small and tool selection accurate, plus one local
`create_planner_note` tool defined in `canvas/local_tools.py` (canvas-mcp has no planner
write). Writes are covered in [Conventions](#conventions).

### Conversation memory = Slack

The agent is **stateless**. On every mention, `build_history` re-reads the thread from
Slack (`conversations_replies`, most-recent 20 messages, speaker names prefixed). No
database — survives restarts. Only the thread is read, not loose channel messages.

### Reliability

- `MAX_STEPS=6` reason/act iterations · `GROQ_TIMEOUT=30s` per LLM call ·
  `TOOL_TIMEOUT=25s` per Canvas call · `AGENT_TIMEOUT=75s` hard ceiling per run.
- **Retry dedupe** by `client_msg_id` — the handler is slow (>3s), so Slack redelivers
  events; without dedupe each redelivery would spawn a duplicate agent run.

## Bring CanvasBot to Your Workspace

Want to run CanvasBot in your own Slack workspace? Follow these steps:

### 1. Create a Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**.
2. Under **Socket Mode**, enable it — this generates your `SLACK_APP_TOKEN` (`xapp-…`).
3. Under **OAuth & Permissions**, add these **Bot Token Scopes**:
   - `app_mentions:read`, `chat:write`, `channels:history`, `groups:history`,
     `im:history`, `mpim:history`, `users:read`
4. Under **Event Subscriptions**, enable events and subscribe to:
   - `app_mention`, `app_home_opened`
5. Under **Interactivity & Shortcuts**, toggle **Interactivity** on (no Request URL needed for Socket Mode).
6. **Install to Workspace** — copy the `SLACK_BOT_TOKEN` (`xoxb-…`) and `SLACK_SIGNING_SECRET` from **Basic Information**.

### 2. Get a Groq API Key

Sign up at [console.groq.com](https://console.groq.com), create an API key, and copy it.

### 3. Clone & Configure

```bash
git clone https://github.com/oasis-pandey/slack-ai-agent.git
cd slack-ai-agent
```

Create a `.env` file with your keys:

```
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_SIGNING_SECRET=...
GROQ_API_KEY=...
CREDS_ENC_KEY=...    # Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 4. Run

```bash
# Option A: Local
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m canvas_bot.main

# Option B: Docker
docker build -t canvas-bot .
docker run --env-file .env canvas-bot
```

You should see `⚡️ Canvas agent is running (Socket Mode)…` in the logs.

### 5. Connect Your Canvas

1. Open the bot's **App Home** tab in Slack.
2. Click **🔗 Connect Canvas**.
3. Enter your Canvas URL (e.g. `https://canvas.youruniv.edu`) and a
   [Personal Access Token](https://community.canvaslms.com/t5/Student-Guide/How-do-I-manage-API-access-tokens-as-a-student/ta-p/273) from Canvas → Account → Settings → New Access Token.
4. Done — ask the bot anything about your courses, grades, or assignments!

To disconnect at any time, click **Disconnect Canvas** at the bottom of the App Home dashboard.

---

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
GROQ_API_KEY=...
CREDS_ENC_KEY=...                                      # Generate using: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
DATABASE_URL=...                                       # Optional Postgres URL (uses local SQLite if omitted)
DIGEST_TARGET_USER_ID=...                              # (Optional) Slack user ID or channel for scheduled digests
DIGEST_HOUR=9                                          # (Optional) Hour for the scheduled digest (24h format, default 9)
DIGEST_MINUTE=0                                        # (Optional) Minute for the scheduled digest (default 0)
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
   `SLACK_APP_TOKEN`, `SLACK_SIGNING_SECRET`, `GROQ_API_KEY`, `CREDS_ENC_KEY`,
   and `DATABASE_URL` (if using Railway Postgres).
3. Deploy. Watch the logs for `⚡️ Canvas agent is running (Socket Mode)…`, then
   @mention the bot in Slack.

No public URL, database, or open port is required.

## Conventions

- **Limited, confirmation-gated writes.** The only supported writes are creating an
  announcement or discussion, posting/replying to a discussion, and adding a private
  planner note (to-do). For the course-visible writes the bot restates the action and
  waits for explicit confirmation before doing anything; the private to-do is created
  directly. Anything else (submitting assignments, grading, deleting) is unsupported.
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

Working MVP — Slack ↔ agent ↔ Canvas end to end, with a clickable announcement modal,
confirmation-gated write actions, a test suite + CI, and a containerized Railway build.
Next up: cloud deployment and the hackathon submission (demo + architecture diagram).
See [`docs/planning.md`](docs/planning.md) for the full milestone log and design decisions.
