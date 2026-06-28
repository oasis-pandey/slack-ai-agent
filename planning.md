# Canvas Slack Agent — Planning & Architecture

## What We're Building

A Canvas LMS AI Agent that lives inside Slack. You @mention it, ask it
questions about your Canvas (assignments, announcements, to-dos, calendar),
and it reads the data and responds — all from inside Slack.

Hackathon: Slack Agent Builder Challenge (deadline Jul 13, 2026)
Track: New Slack Agent (MCP server integration)

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
│  │   Powered by: LLM (Claude / GPT)                           │     │
│  └───────────┬───────────────────────────┬──────────────────┘     │
│              │                           │                         │
│              ▼                           ▼                         │
│  ┌───────────────────┐    ┌────────────────────────────────┐      │
│  │   Canvas Tools    │    │         .env File              │      │
│  │   (MCP Tools)     │    │                                │      │
│  │                   │    │  CANVAS_TOKEN=xxxxx            │      │
│  │  get_assignments  │    │  CANVAS_BASE_URL=canvas.edu    │      │
│  │  get_announcements│    │  SLACK_BOT_TOKEN=xxxxx         │      │
│  │  get_todos        │    │  SLACK_SIGNING_SECRET=xxxxx    │      │
│  │  get_calendar     │    │  LLM_API_KEY=xxxxx             │      │
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
- Canvas Bearer token (from .env)
- Canvas base URL (from .env, e.g. `https://canvas.instructure.com`)
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

- Agent server runs 24/7 on a cloud VM or serverless platform
- Slack sends webhooks to a public HTTPS URL (your server)
- .env file is set as environment variables on the hosting platform
- No database needed for MVP — stateless, each request is independent

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