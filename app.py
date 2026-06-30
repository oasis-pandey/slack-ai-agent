"""Slack listener for the Canvas agent (Socket Mode).

On an @mention: strip the mention, hand the question to the Groq+Canvas agent,
and post the answer back in the same thread.
"""

import asyncio
import logging
import os

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from agent import run_agent
from slack_helpers import (
    MENTION_RE,
    WORKING_MSG,
    already_handled,
    build_history,
)

load_dotenv()
logging.basicConfig(level=logging.INFO)

app = App(token=os.environ["SLACK_BOT_TOKEN"])

# The bot's own Slack user ID, so we can label its past messages as "assistant".
BOT_USER_ID = app.client.auth_test()["user_id"]

# Hard ceiling on a single agent run, so a stuck loop can't hang forever.
AGENT_TIMEOUT = 75


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
        history = build_history(client, event["channel"], thread_ts, BOT_USER_ID)
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
