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

from .agent import AgentResult, run_agent
from .canvas import rest as canvas_rest
from .slack.blocks import (
    ACTION_VIEW_ANNOUNCEMENT,
    announcement_list_blocks,
    announcement_modal_view,
)
from .slack.helpers import (
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
        say(text="Ask me anything about your Canvas — assignments, grades, "
                 "announcements. Try *\"what's due this week?\"*",
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

    # Post a single status message the first time the agent hits Canvas, then
    # edit it in place with the final answer — so the user sees one tidy message
    # that resolves, not a "Checking…" line left dangling above the reply.
    # Plain chat ("hey") never triggers this, so it just gets a direct reply.
    placeholder = {}

    def notify_canvas():
        placeholder["ts"] = say(text=WORKING_MSG, thread_ts=thread_ts)["ts"]

    try:
        # run_agent is async; each mention gets its own short-lived event loop.
        # Hard timeout guarantees the handler always terminates.
        result = asyncio.run(
            asyncio.wait_for(
                run_agent(history, on_tool_call=notify_canvas),
                timeout=AGENT_TIMEOUT,
            )
        )
    except asyncio.TimeoutError:
        logging.warning("agent run exceeded %ss", AGENT_TIMEOUT)
        result = AgentResult("That took too long — try a more specific question?")
    except Exception:
        logging.exception("agent failed")
        result = AgentResult("Something went wrong reaching Canvas. Try again in a moment.")

    # Rich path: clickable announcement list (each "View" opens a modal). `text`
    # is the notification/accessibility fallback shown when blocks render.
    blocks = announcement_list_blocks(result.announcements) if result.announcements else None
    text = result.text or ("Here are your announcements:" if blocks else "(no response)")

    if placeholder.get("ts"):
        client.chat_update(
            channel=event["channel"], ts=placeholder["ts"], text=text, blocks=blocks
        )
    else:
        say(text=text, blocks=blocks, thread_ts=thread_ts)


@app.action(ACTION_VIEW_ANNOUNCEMENT)
def handle_view_announcement(ack, body, client, logger):
    """Open a modal with the full announcement body when "View" is clicked.

    `ack()` must fire within 3s and the trigger_id is short-lived, so we fetch
    the single announcement and open the modal immediately.
    """
    ack()
    try:
        course_id, topic_id = body["actions"][0]["value"].split(":", 1)
        announcement = canvas_rest.get_announcement(course_id, topic_id)
        client.views_open(
            trigger_id=body["trigger_id"],
            view=announcement_modal_view(announcement),
        )
    except Exception:
        logger.exception("failed to open announcement modal")


if __name__ == "__main__":
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    print("⚡️ Canvas agent is running (Socket Mode)…")
    handler.start()
