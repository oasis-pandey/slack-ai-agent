# Canvas Slack Agent — Planning & Architecture

## What We're Building

A Canvas LMS AI Agent that lives inside Slack. You @mention it, ask it
questions about your Canvas (assignments, announcements, to-dos, calendar),
and it reads the data and responds — all from inside Slack.

Hackathon: Slack Agent Builder Challenge (deadline Jul 13, 2026)
Track: New Slack Agent (MCP server integration)

---

## Tech Stack (Decided)

- **Language:** Python
- **Slack:** Bolt for Python (official Slack SDK) — running in Socket Mode
  (uses SLACK_APP_TOKEN, no public webhook URL needed during dev)
- **LLM:** Groq API, Llama 3.3 70B (free tier, OpenAI-compatible tool calling)
- **Canvas tools:** [canvas-mcp](https://github.com/vishalsachdev/canvas-mcp)
  — 90 pre-built Canvas tools in MCP format (Python, MIT, last release May 2026)
- **Deployment:** Railway or Render (cloud, free tier)

### The Groq ↔ MCP Bridge
Groq does **not** natively speak MCP. We need a small manual bridge
(~30 lines, in `canvas_tools.py`) that reads canvas-mcp's tool definitions and
reformats them into Groq's tool-calling schema (same shape as OpenAI function
calling), then routes Groq's tool-call requests back to canvas-mcp. canvas-mcp
runs as a local server alongside the Slack bot on the same machine/instance.

### Groq Free Tier (plenty for a single-user bot)
- 30 RPM · 6,000 TPM · 1,000 requests/day
- Model: Llama 3.3 70B

---

## MVP Scope (What We're Shipping First)

- Single user (you) — Canvas token stored in .env file
- Read-only Canvas access
- Slack @mention triggers the agent
- Agent answers natural language questions about:
  - Assignments (due dates, status, course)
  - Announcements
  - To-dos
  - Calendar events
- Deployed on cloud, always running
- Responds in the same Slack thread

Out of scope for MVP:
- Write operations (create to-do, add calendar event)
- Multi-user auth / OAuth flow
- Course filtering preferences
- Persistent memory across conversations

---

## ASCII Architecture Diagram (MVP)

```
┌─────────────────────────────────────────────────────────────────┐
│                         SLACK (Interface)                         │
│                                                                   │
│   You: "@CanvasBot what's due this week?"                         │
│                          │                                        │
│                          ▼                                        │
│              [Slack Event / Webhook]                              │
└──────────────────────────┼────────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│                       AGENT SERVER (Cloud)                         │
│                                                                    │
│  ┌──────────────────────────────────────────────────────────┐     │
│  │                  Slack Listener                            │     │
│  │  - Receives the event from Slack                           │     │
│  │  - Extracts user message                                   │     │
│  │  - Passes to Agent                                         │     │
│  └────────────────────────┬─────────────────────────────────┘     │
│                           │                                        │
│                           ▼                                        │
│  ┌──────────────────────────────────────────────────────────┐     │
│  │                  ReAct Agent Loop                          │     │
│  │                                                            │     │
│  │   1. REASON  — understand the user's intent                │     │
│  │   2. ACT     — pick and call the right Canvas tool         │     │
│  │   3. OBSERVE — read Canvas API response                    │     │
│  │   4. REASON  — is this enough? loop again if needed        │     │
│  │   5. RESPOND — format and return answer                    │     │
│  │                                                            │     │
│  │   Powered by: Groq API (Llama 3.3 70B)                     │     │
│  │   Tools bridged from canvas-mcp → Groq tool calling        │     │
│  └───────────┬───────────────────────────┬──────────────────┘     │
│              │                           │                         │
│              ▼                           ▼                         │
│  ┌───────────────────┐    ┌────────────────────────────────┐      │
│  │   Canvas Tools    │    │         .env File              │      │
│  │   (MCP Tools)     │    │                                │      │
│  │                   │    │  CANVAS_API_TOKEN=xxxxx        │      │
│  │  get_assignments  │    │  CANVAS_BASE_URL=canvas.edu    │      │
│  │  get_announcements│    │  SLACK_BOT_TOKEN=xxxxx         │      │
│  │  get_todos        │    │  SLACK_SIGNING_SECRET=xxxxx    │      │
│  │  get_calendar     │    │  SLACK_APP_TOKEN=xxxxx         │      │
│  │  get_courses      │    │  GROQ_API_KEY=xxxxx            │      │
│  │  get_courses      │    └────────────────────────────────┘      │
│  └───────────┬───────┘                                            │
│              │                                                     │
└──────────────┼─────────────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────────┐
│                       CANVAS LMS API                               │
│                                                                    │
│   REST API endpoints:                                              │
│   GET /api/v1/courses                                              │
│   GET /api/v1/courses/:id/assignments                              │
│   GET /api/v1/courses/:id/announcements                            │
│   GET /api/v1/planner/items  (to-dos + calendar)                   │
│                                                                    │
│   Auth: Bearer token (from .env)                                   │
└──────────────────────────────────────────────────────────────────┘
               │
               │  (response goes back up through Agent)
               ▼
┌──────────────────────────────────────────────────────────────────┐
│                       SLACK (Response)                             │
│                                                                    │
│   CanvasBot: "Here's what's due this week:                         │
│   • Homework 3 — CS301 — Due Monday 11:59pm                        │
│   • Essay Draft — ENG202 — Due Wednesday 5pm                       │
│   • Quiz 4 — MATH101 — Due Friday 11:59pm"                         │
└──────────────────────────────────────────────────────────────────┘
```

---

## The Three Main Pieces

### 1. Slack Layer
- A Slack App configured with Event Subscriptions
- Listens for `app_mention` events (when you @tag the bot)
- Forwards the message payload to the agent server
- Receives the agent's response and posts it back to the thread

### 2. The Agent (ReAct Brain)
- Receives the user's raw message
- Runs a ReAct loop:
  - Uses an LLM to reason about intent
  - Decides which tool(s) to call
  - Calls the tool, reads the result
  - Decides if it needs to call another tool or is ready to answer
- Returns a clean, formatted natural language response
- The LLM is given a system prompt that defines its persona, its tools, and
  how to behave (e.g., "always confirm before writing anything")

### 3. Canvas Tools (The Agent's Hands)
Each tool is a function the agent can choose to call:

| Tool               | What It Does                                  |
|--------------------|-----------------------------------------------|
| get_courses        | Lists all enrolled courses                    |
| get_assignments    | Gets assignments, optionally filtered by date |
| get_announcements  | Gets recent announcements per course          |
| get_todos          | Gets the planner to-do list                   |
| get_calendar_events| Gets upcoming calendar events                 |

Each tool:
- Takes parameters (e.g., date range, course id)
- Calls the Canvas REST API with the stored Bearer token
- Returns structured data back to the agent

---

## Data Flow — Step by Step

1. **You type in Slack:** `@CanvasBot what assignments do I have due this week?`
2. **Slack fires an `app_mention` event** to your agent server.
3. **Agent server receives:**
   - `message`: "what assignments do I have due this week?"
   - `channel_id`, `thread_ts` (to reply in the right place)
4. **Agent starts the ReAct loop:**
   - REASON: User wants assignments due this week
   - ACT: call `get_assignments(date_from=today, date_to=end_of_week)`
   - The tool hits the Canvas API:
     `GET /api/v1/planner/items?start_date=...&end_date=...`
   - Returns: JSON list of assignments with names, due dates, course names
5. **Agent observes the result:**
   - REASON: I have enough info, no more tools needed
   - RESPOND: Format into a readable Slack message
6. **Agent sends the formatted message** back to the Slack thread.
7. **You see the response** in Slack.

---

## Inputs & Outputs

### What Goes Into the Agent (Per Request)
- User's natural language message
- Canvas Bearer token (`CANVAS_API_TOKEN` from .env)
- Canvas base URL (`CANVAS_BASE_URL` from .env, e.g. `https://canvas.instructure.com`)
- Slack channel ID + thread timestamp (to reply correctly)

### What Comes Out
- Natural language response formatted for Slack
- For reads: lists, due dates, course names, formatted cleanly
- On failure: plain error message ("Couldn't reach Canvas, try again")

---

## Edge Cases to Handle

### Auth & Security
- Canvas token stored only in .env — never logged, never sent to Slack
- If token is invalid or expired, return a clear message: 
  "Canvas connection failed — check your token"

### Ambiguous Queries
- "What's due soon?" → default to "this week", state the assumption in reply
- "My assignments" → all courses, sorted by due date, this week only by default
- "Add a reminder" (write operation) → out of scope for MVP, respond with
  "I can only read Canvas right now, writing is coming soon!"

### Canvas API Issues
- Empty results: "Nothing due this week! 🎉" — not silence, not an error
- Rate limiting: catch 429 errors, tell the user to try again in a moment
- Course with no assignments: skip it silently or mention it briefly

### Slack Edge Cases
- Bot gets messaged in DM vs. channel: should work in both
- Long responses: chunk or summarize if Canvas returns a huge list
- Bot is mentioned but message is empty or gibberish: ask for clarification

### Canvas Instance Differences
- Some schools disable certain API endpoints (e.g., planner/items)
- Base URL varies by school — must be configurable via .env

---

## What the System Prompt (Agent Persona) Should Say

- You are a Canvas assistant for [Your Name]
- You have access to these tools: [list tools]
- Always be concise and format responses for Slack (use bullet points)
- If the user asks you to write or modify anything, say it's not supported yet
- If you're unsure what the user wants, ask one clarifying question
- Never make up assignment names or due dates — only use real API data
- If a tool returns an error, say so clearly

---

## Cloud Deployment (MVP)

- Agent server runs 24/7 on a cloud platform (Railway or Render, free tier)
- Using **Socket Mode** for dev: Slack connects over a WebSocket via
  `SLACK_APP_TOKEN`, so no public HTTPS URL / ngrok is needed to test locally
- `.env` values are set as environment variables on the hosting platform
- canvas-mcp runs as a local process alongside the bot on the same instance
- No database needed for MVP — stateless, each request is independent

---

## Files to Write

| File              | Purpose                                                   |
|-------------------|-----------------------------------------------------------|
| `app.py`          | Slack listener (Bolt, Socket Mode) — handles `app_mention` |
| `agent.py`        | ReAct loop using Groq + Llama 3.3 70B                      |
| `canvas_tools.py` | Bridge between Groq tool calling and canvas-mcp            |
| `.env`            | Secrets (Canvas token + URL, Slack tokens, Groq key)      |
| `requirements.txt`| Dependencies                                              |

---

## Milestones

1. **Slack ↔ server** — @mention bot, it replies "I heard you". No AI yet.
2. **Canvas connected** — run a script, see real Canvas data printed. No Slack.
3. **Full MVP end to end** — @mention bot, get real Canvas data back via Groq agent.
4. **Deploy to cloud** — Railway/Render, always on.

---

## Current Status (as of Jun 28, 2026)

- ✅ **Milestone 1 DONE** — `app.py` (Bolt, Socket Mode) replies "I heard you"
  to @mentions. Verified live in Slack.
- ✅ **Milestone 2 DONE** — Canvas connected and verified two ways, no Slack:
  - `canvas_check.py` — direct Canvas REST call (validated token + base URL)
  - `canvas_mcp_check.py` — launches `canvas-mcp-server` over stdio, lists its
    92 tools, calls `list_courses`, prints real courses
- ✅ Dependencies installed: `slack-bolt`, `python-dotenv`, `groq`, `requests`,
  plus `canvas-mcp` (from git) and the `mcp` client SDK
- ✅ `.env` has all keys; **added `CANVAS_API_URL`** (= base URL + `/api/v1`),
  which is the var name canvas-mcp requires (distinct from our `CANVAS_BASE_URL`)
- ✅ **Milestone 3 DONE** — full MVP wired end to end (Slack → Groq agent →
  canvas-mcp → Canvas → Slack):
  - `canvas_tools.py` — the bridge: launches canvas-mcp over stdio, whitelists
    9 read-only student tools, converts MCP↔Groq tool schemas
  - `agent.py` — Groq (Llama 3.3 70B) ReAct loop; tested standalone, then wired
  - `app.py` — `app_mention` now strips the mention, posts "🔎 Checking Canvas…",
    runs the agent, replies in-thread (with error handling)
  - System prompt instructs: real data only, Slack formatting, no internal IDs
- ✅ **Post-MVP hardening DONE** (after live testing):
  - Conditional "🔎 Checking Canvas…" — posted only when the agent actually
    calls a tool (via `on_tool_call` callback), not for plain chat.
  - **Conversation memory**: `app.py` reads the thread (`conversations_replies`,
    paginated, most-recent `MAX_HISTORY=20`) and passes it as history; speaker
    names prefixed via `users_info`. Agent is stateless — Slack is the store.
  - **Reliability caps**: `GROQ_TIMEOUT=30s`, `TOOL_TIMEOUT=25s`,
    `AGENT_TIMEOUT=75s` hard ceiling, plus **retry dedupe** by `client_msg_id`.
  - System prompt upgraded: chat (no tools) for greetings; answer only what's
    asked; say "I don't see any" instead of relabeling; use speaker names.
- ⬜ Milestone 4: deploy to cloud (Railway/Render) as a background worker

### More plan corrections (Milestone 3 + hardening)
- Slack redelivers events not acked within ~3s; our handler is slow, so without
  dedupe each redelivery spawned a duplicate agent run (runaway `list_courses`).
  Fixed with `already_handled(client_msg_id)`.
- `conversations_replies` returns thread messages **oldest-first**; to get the
  freshest context we paginate and take the tail, not `limit=K`.
- Slack scopes required: `app_mentions:read`, `chat:write`, `channels:history`,
  `groups:history`, `im:history`, `mpim:history`, `users:read`. Reinstalling to
  add scopes can rotate `SLACK_BOT_TOKEN` — update `.env`.
- `groq==0.9.0` from the plan was incompatible with current `httpx`
  (`proxies` kwarg removed) — upgraded to `groq==1.5.0`.
- Groq model id: `llama-3.3-70b-versatile`.
- Known data quirk: `list_courses` returns archived + non-class enrollments
  (e.g. clubs); "active enrollment" ≠ "current term class". Tighten later by
  filtering on term / excluding archived.

### Corrections to the original plan (learned while building)
- canvas-mcp speaks MCP over **stdio** — we spawn `canvas-mcp-server` as a
  subprocess and talk over its stdin/stdout (not a network port).
- canvas-mcp requires env var **`CANVAS_API_URL`** with the `/api/v1` path,
  not `CANVAS_BASE_URL`.
- The "bridge" is two parts, not just reformatting: (a) an MCP **client**
  (`mcp` SDK) to launch + call canvas-mcp, and (b) the **schema translation**
  MCP tool defs → Groq tool-calling format, and Groq tool calls → MCP `call_tool`.
- canvas-mcp anonymizes user names (privacy feature) and returns pre-formatted
  text from tools, not raw JSON.

**Next step:** Milestone 4 — deploy to cloud (Railway/Render) as a long-running
**background worker** (NOT a web service — Socket Mode needs no port). Set all
`.env` vars as platform env vars. Note canvas-mcp installs from git, and the bot
spawns `canvas-mcp-server` as a subprocess, so it must be on PATH in the deploy
image. Optional polish before/after deploy: pre-filter `list_courses` (drop
archived / non-class enrollments), and consider reading channel-level context
(not just thread).

---

## Hackathon Submission Checklist

- [ ] Working Slack agent (app_mention → Canvas read → Slack reply)
- [ ] At least 1 MCP integration (Canvas tools as MCP server)
- [ ] Deployed and reachable (public HTTPS URL)
- [ ] ~3 min demo video showing it working end to end
- [ ] Architecture diagram (use the ASCII above, clean it up)
- [ ] Text description of features and how it works
- [ ] Slack developer sandbox URL (give access to slackhack@salesforce.com
  and testing@devpost.com)

---

## Future Features (Post-MVP)

- Write operations (create to-do, add calendar event, mark assignment done)
- Multi-user support with OAuth per user
- Persistent memory (remember user preferences across conversations)
- Proactive notifications (agent DMs you when something is due soon)
- Course filtering ("only show me CS classes")
- Submission status ("did I submit assignment X?")