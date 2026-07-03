# Next Steps — UX/UI & Demo Plan

Goal: stop looking like a plain Q&A chatbot and present as a **Canvas app inside Slack**.
This directly targets the hackathon's **Design** judging criterion and the **Best UX
($2,000)** and **Most Innovative ($2,000)** special awards, and reinforces **Technical
Implementation** by using more of the Slack platform (Home tab, interactivity, modals,
scheduling) than a message-in/message-out bot.

Guiding principle: give the bot **surfaces** (a Home dashboard) and **actions** (buttons,
modals, confirmations), not just replies. Fuse reads and writes so a result you *see* is
something you can *act on* in one tap.

---

## Priority ranking

| # | Feature | Impact | Effort | Judging tie-in |
|---|---------|--------|--------|----------------|
| P0 | App Home dashboard | ★★★ | Med | Design, Innovation, Technical |
| P0 | Interactive result cards (+ "Remind me") | ★★★ | Low–Med | Design, UX |
| P0 | Button-based write confirmation | ★★☆ | Low | UX, Technical (safety) |
| P1 | Scheduled morning/weekly digest | ★★★ | Med | **Theme fit** (automation), Innovation |
| P1 | Modal composer for writes | ★★☆ | Med | UX, Technical |
| P2 | Slash commands (`/canvas due`) | ★☆☆ | Low | UX polish |
| P2 | Live multi-step progress | ★☆☆ | Low | UX polish |
| P2 | Grades visual (bars / chart image) | ★★☆ | Med | Design |
| P2 | Personality in empty/error states | ★☆☆ | Low | UX polish |

Recommended demo build: **all three P0s** (they combine into one great demo and reuse
existing code), plus the **scheduled digest** (P1) if time allows for the strongest
thematic story.

---

## P0 — App Home dashboard (the "not a chatbot" centerpiece)

Clicking the bot in the sidebar opens its **Home tab**: a live Canvas dashboard, no typing.

```
📚 Your Canvas — Wed, Jul 2                         [🔄 Refresh]
──────────────────────────────────────────────────
🔴 Due today
   Essay Draft · ENG202 · 11:59pm      [Details] [⏰ Remind me]
🟡 This week
   Homework 3 · CS301 · Fri            [Details] [⏰ Remind me]
   Quiz 4 · MATH101 · Sat              [Details]
──────────────────────────────────────────────────
📢 Announcements (2 new)                       [View all]
📊 Grades   CS301 A−   ENG202 B+   MATH101 A
```

- **Slack/engineering:** subscribe to the `app_home_opened` event; build the dashboard
  with `views.publish`. New surface = new event handler, distinct from message handling.
- **Reuses:** `get_my_upcoming_assignments`, `get_my_todo_items`, `list_announcements`,
  `get_my_course_grades` (already whitelisted), and `slack/blocks.py` builders.
- **Scopes:** already have most; verify the app's Home tab is enabled in app config.
- **Notes:** cache the render briefly to avoid hammering Canvas on every open; add the
  `[🔄 Refresh]` button (an `@app.action` that re-publishes).

## P0 — Interactive result cards (+ "Remind me")

Replace bullet lists with Block Kit sections: urgency emoji (🔴 today / 🟡 this week /
🟢 later), course + due context, and action buttons. The standout: **"⏰ Remind me"**
calls the existing `create_planner_note` write — reads and writes fused in one tap.

```
🔴 Essay Draft
ENG202 · due today 11:59pm · not submitted
                              [Open in Canvas]  [⏰ Remind me]
```

- **Slack/engineering:** new Block Kit builders in `slack/blocks.py` (keep them pure);
  an `@app.action` for "Remind me" → `canvas_rest.create_planner_note(...)` → ephemeral
  "Added to your to-do ✅".
- **Reuses:** the announcement-card pattern already in `blocks.py`; the planner-note write.
- **Design detail:** relative friendly dates ("due Fri"), no internal IDs (existing rule),
  at most one emoji per line.

## P0 — Button-based write confirmation

Swap the typed-"yes" confirmation for Block Kit buttons on course-visible writes.

```
I'll post this announcement to CS301:
> Office hours moved to 3pm Friday
                              [✅ Post it]   [Cancel]
```

- **Slack/engineering:** the agent returns a pending-write intent; render Confirm/Cancel
  buttons carrying the action payload in `value` (or `private_metadata`); `@app.action`
  executes or discards. This also **hardens safety** — the write becomes *code-gated*
  behind a real button click, not just prompt-enforced (addresses a `report.md` gap).
- **Reuses:** the existing `WRITE_TOOLS` set and confirmation policy; just moves the
  gate from text to UI.

---

## P1 — Scheduled morning/weekly digest (best theme fit)

The challenge is about agents that **automate workflows**. A proactive digest posted on a
schedule is the least chatbot-like thing possible.

```
☀️ Good morning! Here's your Wednesday:
   🔴 1 due today · 🟡 2 this week · 📢 1 new announcement
                              [See details]  [Snooze reminders]
```

- **Slack/engineering:** a scheduler (APScheduler in-process, or `chat.scheduleMessage`,
  or a cron on the host) that runs the same dashboard-data path and posts to a DM/channel.
- **Reuses:** the dashboard data assembly from the Home tab.
- **Watch-outs:** timezone handling; the single-user token means "everyone" = you for now.

## P1 — Modal composer for writes

"Post an announcement" opens a **modal form**: course dropdown + title + body → Submit.

- **Slack/engineering:** `views_open` with input blocks; handle `view_submission`;
  validate then call the write tool. Shows off form handling end to end.
- **Reuses:** `views_open` plumbing already used for the announcement modal.

---

## P2 — Polish (cheap wins)

- **Slash commands:** `/canvas due`, `/canvas grades`, `/canvas announcements` — product
  feel; register in app config + `@app.command` handlers that reuse the agent/data paths.
- **Live multi-step progress:** update the "Checking Canvas…" placeholder with steps
  ("Found 6 courses → checking due dates…") via the existing `on_tool_call` hook so it
  feels alive.
- **Grades visual:** inline emoji/text bars, or generate a small chart image (matplotlib)
  and upload it to the thread for a real visual. (See the `dataviz` guidance if we chart.)
- **Personality in empty/error states:** e.g. "🎉 Nothing due this week — go touch grass."

---

## Demo storyboard (~3-min video)

Arc: **read → interact → write → proactive** — shows range fast.

1. **Open the App Home** → dashboard renders. *(hook: "it's an app, not a bot")*
2. **Ask "what's due this week?"** → rich urgency cards with buttons.
3. **Tap "⏰ Remind me"** on an assignment → planner note created instantly.
4. **Ask "post an announcement in CS301…"** → button confirmation → it posts.
5. **Cut to the morning digest** arriving on its own.

Hits Design, UX, Innovation, and Technical Implementation together.

### Other submission assets still needed (from report.md §1)
- Deploy to Railway so it's always-on for judges.
- Slack dev sandbox URL + grant access to `slackhack@salesforce.com`, `testing@devpost.com`.
- Clean rendered architecture diagram (doubles as the résumé artifact).
- The ~3-min demo video itself.

---

## Suggested build order

1. Interactive result cards + "Remind me" (fast, high impact, reuses `blocks.py`).
2. App Home dashboard (reuses the card builders from step 1).
3. Button-based write confirmation (small, big safety + UX win).
4. Scheduled digest (reuses the dashboard data path).
5. Polish (slash commands, progress, grades visual) as time permits.

Rationale: each step reuses the previous one's building blocks, so effort compounds.
