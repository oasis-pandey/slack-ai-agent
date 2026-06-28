"""Slack listener for the Canvas agent (Socket Mode).

On an @mention: strip the mention, hand the question to the Groq+Canvas agent,
and post the answer back in the same thread.
"""

import asyncio
import logging
import os
import re
import threading
from collections import OrderedDict

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from agent import run_agent

load_dotenv()
logging.basicConfig(level=logging.INFO)

app = App(token=os.environ["SLACK_BOT_TOKEN"])

# Matches Slack user mentions like "<@U012ABC>" so we can drop the bot's tag.
MENTION_RE = re.compile(r"<@[A-Z0-9]+>")

# Interim placeholder we post while working; filtered out of conversation history.
WORKING_MSG = "🔎 Checking Canvas…"

# The bot's own Slack user ID, so we can label its past messages as "assistant".
BOT_USER_ID = app.client.auth_test()["user_id"]

# Keep context bounded — the most recent N thread messages fed to the agent.
MAX_HISTORY = 20
# Safety cap on pagination when walking a long thread to its end.
MAX_PAGES = 5

# Cache of Slack user IDs -> display names (so the agent can tell speakers apart).
_user_names = {}

# Hard ceiling on a single agent run, so a stuck loop can't hang forever.
AGENT_TIMEOUT = 75

# Slack redelivers an event if we don't ack within ~3s. Our handler is slow
# (Canvas + LLM), so we dedupe: each mention is processed once; retries (same
# client_msg_id) are dropped. Without this, every slow reply spawns duplicate runs.
_seen_lock = threading.Lock()
_seen_events = OrderedDict()
SEEN_MAX = 500


def already_handled(key: str) -> bool:
    """Return True if this event key was seen before; otherwise record it."""
    with _seen_lock:
        if key in _seen_events:
            return True
        _seen_events[key] = True
        if len(_seen_events) > SEEN_MAX:
            _seen_events.popitem(last=False)  # evict oldest
        return False


def display_name(client, user_id):
    """Best-effort human name for a Slack user id (cached). Falls back to 'Someone'."""
    if not user_id:
        return "Someone"
    if user_id not in _user_names:
        try:
            info = client.users_info(user=user_id)["user"]
            profile = info.get("profile", {})
            _user_names[user_id] = (
                profile.get("display_name")
                or info.get("real_name")
                or info.get("name")
                or "Someone"
            )
        except Exception:
            _user_names[user_id] = "Someone"  # e.g. missing users:read scope
    return _user_names[user_id]


def fetch_thread_messages(client, channel, thread_ts):
    """Return the thread's messages oldest-first, paginating to the end."""
    messages, cursor = [], None
    for _ in range(MAX_PAGES):
        resp = client.conversations_replies(
            channel=channel, ts=thread_ts, limit=100, cursor=cursor
        )
        messages.extend(resp.get("messages", []))
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return messages


def build_history(client, channel, thread_ts):
    """Turn the thread into chat history for the agent — the most recent
    MAX_HISTORY messages, with each person's name prefixed so the agent can
    follow a multi-person conversation."""
    history = []
    for m in fetch_thread_messages(client, channel, thread_ts):
        text = MENTION_RE.sub("", m.get("text", "")).strip()
        if not text or text == WORKING_MSG:
            continue  # skip empty messages and our own placeholder
        if m.get("user") == BOT_USER_ID or "bot_id" in m:
            history.append({"role": "assistant", "content": text})
        else:
            name = display_name(client, m.get("user"))
            history.append({"role": "user", "content": f"{name}: {text}"})
    # Keep only the most recent slice — the tail is the freshest context.
    return history[-MAX_HISTORY:]


@app.event("app_mention")
def handle_mention(event, client, say):
    """Answer Canvas questions when the bot is @mentioned."""
    # Drop Slack retries / duplicate deliveries before doing any slow work.
    key = event.get("client_msg_id") or f"{event.get('channel')}:{event.get('ts')}"
    if already_handled(key):
        logging.info("ignoring duplicate/retry event %s", key)
        return

    thread_ts = event.get("thread_ts", event["ts"])
    question = MENTION_RE.sub("", event.get("text", "")).strip()

    if not question:
        say(text="Ask me about your Canvas — assignments, to-dos, grades, "
                 "announcements. e.g. \"what's due this week?\"",
            thread_ts=thread_ts)
        return

    # Try to read the thread for multi-turn context. If we lack the history
    # scope (or it otherwise fails), fall back to just this message so basic
    # Q&A still works — only conversational memory is lost.
    try:
        history = build_history(client, event["channel"], thread_ts)
    except Exception:
        logging.warning(
            "couldn't read thread history (need *:history scopes?) — "
            "answering without memory", exc_info=True
        )
        history = [{"role": "user", "content": question}]

    # Posted only if/when the agent actually decides to call a Canvas tool —
    # so plain chat ("hey") doesn't get a misleading "Checking Canvas…".
    def notify_canvas():
        say(text=WORKING_MSG, thread_ts=thread_ts)

    try:
        # run_agent is async; each mention gets its own short-lived event loop.
        # Hard timeout guarantees the handler always terminates.
        answer = asyncio.run(
            asyncio.wait_for(
                run_agent(history, on_tool_call=notify_canvas),
                timeout=AGENT_TIMEOUT,
            )
        )
    except asyncio.TimeoutError:
        logging.warning("agent run exceeded %ss", AGENT_TIMEOUT)
        answer = "That one's taking too long — try a more specific question?"
    except Exception:
        logging.exception("agent failed")
        answer = "Sorry — something went wrong reaching Canvas. Try again in a moment."

    say(text=answer, thread_ts=thread_ts)


if __name__ == "__main__":
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    print("⚡️ Canvas agent is running (Socket Mode)…")
    handler.start()
