# Next Steps — Milestones & AI Handoff Prompts

This file is a **handoff plan for another AI coding assistant**. Each milestone below
has a copy-pasteable **PROMPT** block: paste one into your AI, let it do the work, review,
move to the next. Milestones are ordered so each reuses the previous one's building blocks.

Goal of this phase: finish the UX/demo polish and ship the hackathon submission
(**Slack Agent Builder Challenge** — judged on Technological Implementation, Design,
Potential Impact, Quality of Idea; special awards for **Best UX** and **Best Technical
Implementation**).

---

## Already done (do NOT rebuild)

On branch `canvas-write-mvp` (open PR #6):
- Read Q&A end to end (Slack → Groq ReAct agent → canvas-mcp).
- **Announcement UI**: Block Kit list (capped, sorted) + click-to-open modal.
- **Interactive assignment cards**: urgency (🔴/🟡/🟢/⚪) + course + due + "Open in Canvas"
  link + **⏰ Remind me** button that creates a private Canvas planner note (to-do).
- **App Home dashboard**: `app_home_opened` → `views.publish` showing Grades, Due-soon
  cards, To-dos, and a 🔄 Refresh button (`canvas_bot/main.py::_build_home_view`).
- **Writes** (behind confirmation): create announcement / discussion, post / reply to a
  discussion (canvas-mcp `WRITE_TOOLS`), plus a private planner note (`create_planner_note`).
- Tests: `python -m pytest -q` (50 passing), GitHub Actions CI, Dockerfile + `railway.json`.

---

## How to work in this repo (READ THIS FIRST — applies to every milestone)

- **Read `CLAUDE.md` first**, then the specific files a milestone names. It documents the
  architecture, conventions, and gotchas. `docs/planning.md` is the running log; `report.md`
  is an engineering assessment (git-ignored, optional reading).
- **Architecture:** one process. `canvas_bot/main.py` = Slack layer (Bolt, Socket Mode,
  `@app.event`/`@app.action` handlers); `canvas_bot/agent.py` = Groq ReAct loop (knows
  nothing about Slack, returns an `AgentResult`); `canvas_bot/canvas/` = `bridge.py`
  (Groq↔canvas-mcp), `rest.py` (direct Canvas REST), `local_tools.py` (non-MCP agent
  tools); `canvas_bot/slack/` = `blocks.py` + `helpers.py`, **pure and side-effect-free**.
- **Keep `slack/blocks.py` pure** (no network, no Slack client) so it stays unit-testable.
  Put any time-dependent logic behind an injectable `now` param (see `assignment_card_blocks`).
- **Reuse existing patterns**, don't reinvent: structured data via `canvas/rest.py`; the
  "harvest structured records after a tool call" pattern in `agent.py`; card rendering via
  `blocks._assignment_card`; the fail-safe render/post path and `_fit()` in `main.py`.
- **Writes are limited + confirmed.** Never add a new mutating capability beyond what a
  milestone asks. Course-visible writes must stay confirmation-gated.
- **Slack output stays minimal and clean** (lead with the answer, tight lists, friendly
  dates, no internal IDs, ≤1 emoji per line). Never fabricate Canvas data.
- **Tests:** add/adjust `pytest` tests for any pure logic you change; run `python -m pytest -q`
  and make sure all pass. Tests must need no network or secrets.
- **Verify against real Canvas** where possible with a small throwaway script using
  `canvas_bot/canvas/rest.py` + `.env` (never commit `.env`; it's git-ignored). For writes,
  create-then-delete so nothing is left behind.
- **Commits:** short, single, plain-English sentence. **No `Co-Authored-By` / AI trailer.**
  Do not push or open PRs unless the user explicitly asks; work on branch `canvas-write-mvp`
  (or a new branch off it if told).
- **Local run:** `python -m canvas_bot.main` (Socket Mode; stop with
  `pkill -f "canvas_bot.main"`). Some features need Slack app-config toggles — call those
  out to the user; you can't set them.

---

## ★ PRIORITY TRACK — Multi-tenant per-user Canvas auth

**Decision (owner):** make the bot usable by *anyone*, personalized to *their own* Canvas
classes — not one shared token. This is the real scalability upgrade and the biggest
engineering story in the project. **Do this track before Milestones 1–5** (or at least before
deploying publicly). It reprioritizes ahead of the UX polish.

### Critical reality — read before starting
- **Full Canvas OAuth2** ("Log in with Canvas" redirect) needs a **Developer Key from a Canvas
  account admin** at the institution. A student usually can't obtain one → treat OAuth as
  OPTIONAL / later (Milestone E). If the owner secures an institutional developer key, do E.
- **Primary path = per-user access tokens.** Each user connects Canvas by pasting a personal
  access token (Canvas → Account → Settings → **New Access Token**) into a Slack modal; we
  validate + store it **encrypted**, keyed by Slack user id. Same outcome (personalized,
  anyone can use it), obtainable without admin approval.
- **Storage must be a real DB, not a file.** Railway's container filesystem is ephemeral
  (wiped on redeploy), so SQLite-on-disk loses data. Use **Postgres** (Railway add-on) in
  prod; SQLite is fine only for local dev.
- **Also note (don't silently ignore):** Groq uses one shared API key → multi-user increases
  token usage (per-user rate-limiting needed). Multi-*workspace* install (true Marketplace)
  is a *further* step (Slack OAuth distribution) beyond this track.

### Milestone A — Encrypted per-user credential store
**Goal:** a datastore mapping Slack user → their Canvas creds, encrypted at rest.
**Files:** new `canvas_bot/store.py`; `requirements.txt` (`cryptography`, a DB driver).

```
PROMPT:
Read CLAUDE.md and canvas_bot/canvas/rest.py (to see the CANVAS_BASE_URL/CANVAS_API_TOKEN /
CANVAS_API_URL usage you'll be replacing).

Create a per-user Canvas credential store:
1. New module canvas_bot/store.py exposing a small API:
   - a CanvasCreds dataclass {canvas_base_url, canvas_api_url, canvas_token}
   - get_creds(slack_user_id) -> CanvasCreds | None
   - set_creds(slack_user_id, creds) -> None
   - delete_creds(slack_user_id) -> None
2. Encrypt the token at rest using `cryptography` Fernet with a key from env
   (CREDS_ENC_KEY). Never store or log the plaintext token.
3. Backend: use SQLite for local dev (path from env, default ./data/creds.db) but write the
   data access so a Postgres URL (DATABASE_URL) is used when present (Railway). Keep the SQL
   minimal; a tiny abstraction is fine. Create the table on startup if missing.
4. Add pinned deps to requirements.txt (cryptography, and psycopg[binary] if you support
   Postgres). Add CREDS_ENC_KEY and DATABASE_URL to the README + .env example, with a note on
   generating a Fernet key.
5. Unit-test store.py with the SQLite backend using a temp DB (no network): set/get/delete
   round-trip, and that the stored token is not the plaintext. Run `python -m pytest -q`.
Commit with a short one-line message, no co-author. Do not push.

Done when: creds can be saved/loaded/deleted per Slack user, tokens are encrypted at rest,
and tests pass.
```

### Milestone B — Thread per-user creds through the data layer (the big refactor)
**Goal:** every Canvas call uses the *caller's* creds, not a global env token.
**Files:** `canvas_bot/canvas/rest.py`, `canvas_bot/canvas/bridge.py`, `canvas_bot/canvas/local_tools.py`,
`canvas_bot/agent.py`, `canvas_bot/main.py`.

```
PROMPT:
Read CLAUDE.md, then canvas_bot/canvas/rest.py, canvas_bot/canvas/bridge.py,
canvas_bot/canvas/local_tools.py, canvas_bot/agent.py, canvas_bot/main.py, and
canvas_bot/store.py (from Milestone A).

Refactor so Canvas credentials are passed in per request instead of read from global env:
1. rest.py: change _api() and every function to accept a CanvasCreds (or base_url+token)
   argument instead of reading os.environ. Keep signatures clean (pass creds as the first arg).
2. bridge.py: canvas_session() must spawn canvas-mcp with the USER's creds. StdioServerParameters
   takes an `env` dict — pass an env with CANVAS_API_URL/CANVAS_API_TOKEN set from the user's
   creds (merge over os.environ). Change canvas_session(creds) accordingly.
3. local_tools.py: dispatch_local_tool(name, args, creds) -> pass creds to rest.
4. agent.py: run_agent(history, creds, on_tool_call=...) -> use creds for canvas_session and for
   the announcement/assignment REST harvest calls.
5. main.py: every handler (app_mention, view_announcement, remind_assignment, refresh_home,
   app_home_opened) must look up the clicking/asking user's creds via store.get_creds(user_id)
   and pass them down. If a handler builds the Home view or runs the agent, it needs creds.
   For now, if creds are missing, short-circuit (Milestone C adds the connect prompt).
6. Keep a DEV fallback: if store has no creds AND env CANVAS_* is set, use env creds (so local
   single-user dev still works). Gate this behind an env flag like ALLOW_ENV_CREDS=1.
7. Update/rework tests that assumed global env. Run `python -m pytest -q` until green. Verify
   locally end to end with your own token via the env fallback.
Commit with a short one-line message, no co-author. Do not push.

Done when: no Canvas call reads a global token directly (except the gated dev fallback), the
app works per-user, and tests pass.
```

### Milestone C — "Connect Canvas" onboarding flow
**Goal:** a new user connects their Canvas via a Slack modal; everything then personalizes.
**Files:** `canvas_bot/main.py`, `canvas_bot/slack/blocks.py`, `canvas_bot/canvas/rest.py`, `canvas_bot/store.py`.

```
PROMPT:
Read CLAUDE.md, then canvas_bot/main.py, canvas_bot/slack/blocks.py, canvas_bot/store.py, and
canvas_bot/canvas/rest.py.

Add a Canvas connection onboarding flow:
1. In slack/blocks.py add pure builders: connect_prompt_blocks() (a message/Home section with a
   "🔗 Connect Canvas" button, action_id ACTION_CONNECT_CANVAS) and connect_modal_view() (a
   modal with a plain_text_input for Canvas base URL, e.g. https://canvas.youruniv.edu, and one
   for the personal access token, plus help text linking to Canvas → Account → Settings →
   New Access Token).
2. main.py:
   - When a user with no stored creds @mentions the bot or opens the Home tab, respond with the
     connect prompt instead of running the agent / dashboard.
   - @app.action(ACTION_CONNECT_CANVAS): open the connect modal (views_open).
   - @app.view submission: derive canvas_api_url = base_url + "/api/v1"; VALIDATE by calling a
     new rest.validate_creds(creds) that does GET /users/self/profile and returns the name (or
     raises). On success, store via store.set_creds(user_id, creds) and confirm ("✅ Connected as
     <name>"); on failure, return a Slack view error on the token field.
   - Add a "Disconnect Canvas" affordance (button or /canvas disconnect) that calls
     store.delete_creds.
3. Add rest.validate_creds(). Add tests for the pure blocks builders. Run pytest until green.
4. Tell the user which Slack app-config is needed (Interactivity already on; the modal needs no
   extra scope). Commit with a short one-line message, no co-author. Do not push.

Done when: a fresh Slack user is prompted to connect, can paste base URL + token in a modal,
gets validated + stored, and then sees personalized data; disconnect works.
```

### Milestone D — Security & multi-user hardening
```
PROMPT:
Read CLAUDE.md and the modules from Milestones A–C.

Harden for multi-user:
1. Confirm tokens are encrypted at rest and never logged anywhere (grep for logging of creds).
2. Add per-Slack-user rate limiting on agent runs (simple in-memory token bucket or a short
   cooldown) so one user can't exhaust the shared Groq quota; return a friendly message when hit.
3. Add a minimal privacy note to README: what is stored (encrypted Canvas token + base URL,
   per user), where, and how to disconnect/delete.
4. Ensure DB works on Railway (Postgres via DATABASE_URL) and document the env vars.
5. Run pytest. Commit with a short one-line message, no co-author. Do not push.

Done when: tokens are encrypted + unlogged, per-user rate limiting works, and the DB persists
across redeploys on Railway.
```

### Milestone E — (OPTIONAL) Real Canvas OAuth2
Only if the owner obtains an institutional **Developer Key** (client_id/secret + redirect URI).
Replace the token-paste modal with the OAuth2 authorization-code flow (`/login/oauth2/auth` →
`/login/oauth2/token`), store the refresh token, and refresh access tokens on expiry. Keep the
same `store.py` interface so the rest of the app is unchanged. Note: needs a public HTTPS
redirect endpoint (a tiny web route) even though the bot itself is Socket Mode.

---

## Milestone 1 — Button-based write confirmation  *(P0, do first)*

**Goal:** Replace the typed-"yes" confirmation for course-visible writes with Block Kit
**✅ Confirm / ✖️ Cancel** buttons. This is cleaner UX **and** upgrades safety from
prompt-enforced to **code-enforced** (a write only fires on a real button click).

**Why it matters:** Best UX + Best Technical Implementation. Also closes a `report.md` gap
(writes are currently only prompt-gated).

**Files & patterns:** `canvas_bot/agent.py` (system prompt + `AgentResult`), `canvas_bot/main.py`
(render + new `@app.action`), `canvas_bot/slack/blocks.py` (a confirmation-card builder),
`canvas_bot/canvas/bridge.py` (`WRITE_TOOLS`, `canvas_session`). Mirror the existing
`ACTION_REMIND_ASSIGNMENT` button flow and the `ACTION_VIEW_ANNOUNCEMENT` action handler.

```
PROMPT:
Read CLAUDE.md, then canvas_bot/agent.py, canvas_bot/main.py, canvas_bot/slack/blocks.py,
and canvas_bot/canvas/bridge.py.

Implement button-based confirmation for course-visible writes (the canvas-mcp WRITE_TOOLS:
create_announcement, create_discussion_topic, post_discussion_entry, reply_to_discussion_entry).
The private create_planner_note stays a direct write (no confirmation) — do not change it.

Design:
1. Change the agent so that when it determines the user wants a course-visible write and it
   has resolved all details, it DOES NOT call the write tool. Instead it returns a structured
   "pending write" on AgentResult: add a field like `pending_write: dict | None` containing
   {tool_name, args, summary} where summary is a one-line human description (course name +
   action + title/message). Update the system prompt: for these writes, gather details, then
   STOP and return the pending write for confirmation instead of calling the tool or asking
   for a typed yes.
2. In slack/blocks.py add a pure builder `write_confirmation_blocks(summary)` that renders a
   section with the summary and an actions block with two buttons: "✅ Confirm" (action_id
   e.g. ACTION_CONFIRM_WRITE) and "✖️ Cancel" (ACTION_CANCEL_WRITE). The Confirm button's
   `value` carries a compact JSON of {tool_name, args} (respect Slack's 2000-char value
   limit; truncate long message bodies). Add the action_id constants next to the existing ones.
3. In main.py, when result.pending_write is set, render those blocks (in the same fail-safe
   render/post path). Add @app.action handlers: on Confirm, execute the write by calling the
   named canvas-mcp tool once (add a small async helper, e.g. bridge.call_tool_once(name, args),
   that opens canvas_session(), calls the one tool, returns text) and replace the message with
   a success/failure note; on Cancel, replace the message with "Okay, cancelled." Handle the
   3s ack() rule and errors like the existing handlers do.
4. Keep everything else working. Add pytest tests for write_confirmation_blocks and the
   pending-write plumbing (pure parts). Run `python -m pytest -q` until green.
5. Verify end to end if possible against a course you own (create-then-delete or just confirm
   the flow reaches the tool). Commit with a short one-line message, no co-author. Do not push.

Done when: asking for a course-visible write shows Confirm/Cancel buttons, Confirm performs the
write, Cancel does nothing, the planner-note path is unchanged, and all tests pass.
```

---

## Milestone 2 — Scheduled daily/weekly digest  *(P1)*

**Goal:** Proactively post a Canvas summary on a schedule (e.g. every weekday morning):
what's due, new announcements, a nudge — without the user asking.

**Why it matters:** The challenge is literally about agents that **automate workflows** —
this is the strongest thematic fit and the least "chatbot" feature.

**Files & patterns:** reuse `canvas_bot/main.py::_build_home_view`'s data assembly (grades,
upcoming, todos via `canvas/rest.py`) and `slack/blocks.py`. Add a scheduler.

```
PROMPT:
Read CLAUDE.md, then canvas_bot/main.py (esp. _build_home_view) and canvas_bot/canvas/rest.py.

Add a proactive scheduled digest. Requirements:
1. Add a pure builder in slack/blocks.py, `digest_blocks(assignments, announcements, now)`,
   returning a compact summary: a headline line ("🔴 N due today · 🟡 M this week · 📢 K new"),
   then the soonest few due-soon cards (reuse _assignment_card). Keep it short.
2. Add the data assembly (reuse the same rest.py calls used by the Home dashboard; you may
   refactor _build_home_view's per-source fetch into a shared helper so both use it — but do
   not break the Home tab).
3. Add scheduling. Prefer APScheduler (add to requirements.txt, pinned) started inside
   canvas_bot/main.py alongside the Socket Mode handler; schedule a daily job (make the time
   and target Slack channel/DM configurable via env vars, documented in README + .env example).
   Post via app.client.chat_postMessage. Guard against duplicate sends.
4. Note clearly to the user any Slack scope needed (chat:write to the target) and that this is
   single-user (one Canvas token). Add pytest tests for digest_blocks. Run pytest until green.
5. Commit with a short one-line message, no co-author. Do not push.

Done when: a scheduled job posts a digest message on the configured cadence, the Home tab still
works, and tests pass. (For a demo you can temporarily set the schedule to a near-future time.)
```

---

## Milestone 3 — Modal composer for writes  *(P1)*

**Goal:** "Post an announcement" (or a discussion) opens a **modal form** — course dropdown +
title + body → Submit → confirmation → write.

**Why it matters:** Design + Technical Implementation (real form handling: `views_open` +
`view_submission`).

**Files & patterns:** `canvas_bot/main.py` (modal open + `@app.view` submission), `slack/blocks.py`
(modal view builder), `canvas/rest.py` (course list for the dropdown), and the M1 confirmation
path for the actual write.

```
PROMPT:
Read CLAUDE.md, then canvas_bot/main.py, canvas_bot/slack/blocks.py, canvas_bot/canvas/rest.py,
and whatever Milestone 1 added for executing a write.

Add a modal composer for creating an announcement (and, if easy, a discussion topic):
1. Trigger: a slash command (e.g. /canvas post) OR a button on the Home tab labeled
   "✍️ New announcement". Whichever you choose, wire the trigger to open a modal.
2. In slack/blocks.py add a pure `announcement_composer_view(courses)` returning a modal view
   with: a static_select of the user's courses (value = numeric course id), a plain_text_input
   for the title, and a multiline plain_text_input for the body. Fetch the course list via a
   rest.py helper (add list_courses_brief() -> [{id, name}] if none exists).
3. Handle @app.view submission: validate inputs, then route through the SAME confirmation +
   write execution added in Milestone 1 (do not bypass confirmation for course-visible writes).
   Post a result message.
4. Add pytest tests for the composer view builder. Run pytest until green.
5. Note any Slack app-config the user must enable (a slash command, or Interactivity — already on).
   Commit with a short one-line message, no co-author. Do not push.

Done when: the trigger opens a working modal, submitting it leads to the confirmation step and
then the write, and tests pass.
```

---

## Milestone 4 — Polish pack  *(P2, pick what time allows)*

**Goal:** Small, cheap wins that raise the perceived quality.

**Files & patterns:** `canvas_bot/main.py`, `canvas_bot/agent.py` (`on_tool_call` hook),
`canvas_bot/slack/blocks.py`.

```
PROMPT:
Read CLAUDE.md, then canvas_bot/main.py, canvas_bot/agent.py, canvas_bot/slack/blocks.py.

Implement as many of these as time allows, each as its own small commit (short message, no
co-author, do not push). Keep slack/blocks.py pure and add tests for any pure logic:

A. Slash commands: register @app.command handlers for /canvas due, /canvas grades,
   /canvas announcements that reuse existing data/agent paths and reply with the rich blocks.
   Tell the user which slash commands to create in the Slack app config.
B. Live multi-step progress: the agent already calls on_tool_call the first time it hits
   Canvas. Extend it (without breaking the current single "Checking Canvas…" behavior) so the
   status message updates with brief step text as tools run (e.g. "Found your courses →
   checking due dates…"). Keep it optional and fail-safe.
C. Empty/error-state personality: friendly copy for empty results and failures
   (e.g. "🎉 Nothing due this week — go touch grass."). Centralize the strings.
D. Grades visual: in the Home tab / grades replies, render a simple text/emoji bar per course
   (e.g. based on score %), OR generate a small chart image and upload it. Keep it optional
   and degrade gracefully if data is missing.

Run `python -m pytest -q` until green after each change.
```

---

## Milestone 5 — Ship the submission  *(required to actually win anything)*

**Goal:** Deploy always-on, set up the judge sandbox, and produce the required artifacts.
Most of this is not code — it's ops + media — so several items are for **you (the human)**.

**Files & patterns:** `Dockerfile`, `railway.json`, `README.md`, `docs/planning.md`.

```
PROMPT (for the AI parts):
Read CLAUDE.md, then Dockerfile, railway.json, requirements.txt, and README.md's Deploy section.

1. Make the build reproducible and production-ready:
   - Pin the canvas-mcp git dependency in requirements.txt to a specific commit/tag (it is
     currently unpinned → non-reproducible builds).
   - Harden the Dockerfile: add a non-root USER, and (if straightforward) a multi-stage build
     so git/build tools aren't in the runtime image. Keep canvas-mcp-server on PATH.
   - Verify `docker build` succeeds and `python -m pytest -q` passes.
2. Update README.md deploy steps if anything changed. Commit each change with a short one-line
   message, no co-author. Do not push unless asked.
```

**Human checklist (not code):**
- [ ] Deploy to Railway (uses `railway.json` + `Dockerfile`); set all `.env` keys as Railway
      service variables; confirm logs show `⚡️ Bolt app is running!`.
- [ ] Create/confirm the Slack dev **sandbox** and grant access to `slackhack@salesforce.com`
      and `testing@devpost.com`; get the sandbox URL for the submission.
- [ ] Produce a clean **architecture diagram** (the ASCII one in `docs/planning.md` → a real
      rendered diagram). Doubles as a résumé artifact.
- [ ] Record the **~3-min demo video** following the storyboard below.
- [ ] Fill in the Devpost submission (track: New Slack Agent / MCP integration).

---

## Demo storyboard (~3 min)

Arc: **read → interact → write → proactive**.
1. Open the **App Home** → dashboard renders (grades, due-soon, Refresh). *("it's an app")*
2. Ask **"what's due this week?"** → interactive urgency cards.
3. Tap **⏰ Remind me** → private to-do created instantly.
4. Ask **"post an announcement in <course>…"** → **Confirm/Cancel** buttons (M1) → it posts.
5. Cut to the **scheduled digest** (M2) arriving on its own.

Hits Design, UX, Innovation, and Technical Implementation together.

---

## Suggested order
**Priority Track A→D (per-user Canvas auth)** first — it changes core signatures everywhere,
so doing it before the UX polish avoids reworking that polish. Then:
M1 (confirmation buttons) → M2 (digest) → M3 (modal composer) → M4 (polish) → M5 (ship).
M1 before M3 because M3 reuses its write-execution path and the demo depends on it.
Milestone E (real OAuth) only if an institutional Canvas developer key is obtained.

Note: deploy (M5 ops) is intentionally deferred until the auth track lands, per the owner —
deploying a single-shared-token build publicly would be a privacy problem.
