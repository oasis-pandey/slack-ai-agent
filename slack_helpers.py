"""Pure Slack-side helpers, free of import-time side effects.

These functions are split out from `app.py` so they can be unit-tested without
constructing a Slack `App` or making the live `auth_test()` call that `app.py`
does at import time. Everything here takes its Slack `client` as an argument and
talks only through the WebClient interface, so tests can pass a mock.
"""

import re
import threading
from collections import OrderedDict

# Matches Slack user mentions like "<@U012ABC>" so we can drop the bot's tag.
MENTION_RE = re.compile(r"<@[A-Z0-9]+>")

# Interim placeholder we post while working; filtered out of conversation history.
WORKING_MSG = "🔎 Checking Canvas…"

# Keep context bounded — the most recent N thread messages fed to the agent.
MAX_HISTORY = 20
# Safety cap on pagination when walking a long thread to its end.
MAX_PAGES = 5

# Slack redelivers an event if we don't ack within ~3s. Our handler is slow
# (Canvas + LLM), so we dedupe: each mention is processed once; retries (same
# client_msg_id) are dropped. Without this, every slow reply spawns duplicate runs.
_seen_lock = threading.Lock()
_seen_events: "OrderedDict[str, bool]" = OrderedDict()
SEEN_MAX = 500

# Cache of Slack user IDs -> display names (so the agent can tell speakers apart).
_user_names: dict = {}


def already_handled(key: str) -> bool:
    """Return True if this event key was seen before; otherwise record it."""
    with _seen_lock:
        if key in _seen_events:
            return True
        _seen_events[key] = True
        if len(_seen_events) > SEEN_MAX:
            _seen_events.popitem(last=False)  # evict oldest
        return False


def display_name(client, user_id) -> str:
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


def build_history(client, channel, thread_ts, bot_user_id):
    """Turn the thread into chat history for the agent — the most recent
    MAX_HISTORY messages, with each person's name prefixed so the agent can
    follow a multi-person conversation."""
    history = []
    for m in fetch_thread_messages(client, channel, thread_ts):
        text = MENTION_RE.sub("", m.get("text", "")).strip()
        if not text or text == WORKING_MSG:
            continue  # skip empty messages and our own placeholder
        if m.get("user") == bot_user_id or "bot_id" in m:
            history.append({"role": "assistant", "content": text})
        else:
            name = display_name(client, m.get("user"))
            history.append({"role": "user", "content": f"{name}: {text}"})
    # Keep only the most recent slice — the tail is the freshest context.
    return history[-MAX_HISTORY:]
