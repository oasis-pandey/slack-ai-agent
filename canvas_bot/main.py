"""Slack listener for the Canvas agent (Socket Mode).

On an @mention: strip the mention, hand the question to the Groq+Canvas agent,
and post the answer back in the same thread.
"""

import asyncio
import json
import logging
import os
from datetime import datetime

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from .agent import AgentResult, run_agent
from .canvas import bridge
from .canvas import rest as canvas_rest
from .store import get_creds as get_store_creds, CanvasCreds
from .slack.blocks import (
    ACTION_REFRESH_HOME,
    ACTION_REMIND_ASSIGNMENT,
    ACTION_VIEW_ANNOUNCEMENT,
    ACTION_CONFIRM_WRITE,
    ACTION_CANCEL_WRITE,
    announcement_list_blocks,
    announcement_modal_view,
    assignment_card_blocks,
    write_confirmation_blocks,
    home_view,
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

# Slack rejects an over-long message body with `msg_too_long`. Keep any text we
# post comfortably under the limit; a minimal answer is never near this anyway.
SLACK_TEXT_LIMIT = 3800


def _fit(text: str) -> str:
    """Truncate `text` so Slack won't reject it with `msg_too_long`."""
    if len(text) <= SLACK_TEXT_LIMIT:
        return text
    return text[:SLACK_TEXT_LIMIT].rstrip() + "\n\n…(truncated)"


def _get_user_creds(user_id: str) -> CanvasCreds | None:
    creds = get_store_creds(user_id)
    if creds is not None:
        return creds
    if os.environ.get("ALLOW_ENV_CREDS") == "1":
        base = os.environ.get("CANVAS_BASE_URL")
        api = os.environ.get("CANVAS_API_URL")
        token = os.environ.get("CANVAS_API_TOKEN")
        if base and api and token:
            return CanvasCreds(canvas_base_url=base, canvas_api_url=api, canvas_token=token)
    return None


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

    user_id = event.get("user")
    creds = _get_user_creds(user_id) if user_id else None
    if not creds:
        say(text="Please connect your Canvas account first.", thread_ts=thread_ts)
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
                run_agent(history, creds, on_tool_call=notify_canvas),
                timeout=AGENT_TIMEOUT,
            )
        )
    except asyncio.TimeoutError:
        logging.warning("agent run exceeded %ss", AGENT_TIMEOUT)
        result = AgentResult("That took too long — try a more specific question?")
    except Exception:
        logging.exception("agent failed")
        result = AgentResult("Something went wrong reaching Canvas. Try again in a moment.")

    # Rich path: clickable announcement list or interactive assignment cards.
    # `text` is the notification/accessibility fallback shown when blocks render.
    blocks = None
    text = result.text or "(no response)"
    if result.pending_write:
        try:
            blocks = write_confirmation_blocks(
                summary=result.pending_write["summary"],
                tool_name=result.pending_write["tool_name"],
                args=result.pending_write["args"],
            )
            text = "Please confirm your action."
        except Exception:
            logging.exception("failed to build write confirmation blocks")
            blocks = None
            text = result.text or "I prepared to write something but couldn't format the confirmation."
    elif result.announcements:
        try:
            blocks = announcement_list_blocks(result.announcements)
            # With blocks, `text` is only the notification preview — keep it short
            # instead of echoing the model's (possibly huge) re-listing.
            text = "📢 Your Canvas announcements"
        except Exception:
            # Never let a rendering error strand the "Checking Canvas…" placeholder;
            # fall back to the plain-text answer the agent already produced.
            logging.exception("failed to build announcement blocks")
            blocks = None
            text = result.text or "I found some announcements but couldn't format them."
    elif result.assignments:
        try:
            blocks = assignment_card_blocks(result.assignments)
            text = "📋 Your Canvas assignments"
        except Exception:
            logging.exception("failed to build assignment cards")
            blocks = None
            text = result.text or "I found some assignments but couldn't format them."

    # Posting can also fail (e.g. Slack rejects the blocks). Fall back to a bare
    # text update so the placeholder always resolves to *something*.
    text = _fit(text)
    try:
        if placeholder.get("ts"):
            client.chat_update(
                channel=event["channel"], ts=placeholder["ts"], text=text, blocks=blocks
            )
        else:
            say(text=text, blocks=blocks, thread_ts=thread_ts)
    except Exception:
        logging.exception("failed to post answer (retrying without blocks)")
        fallback = _fit(result.text or "Sorry — I couldn't post that answer. Try again?")
        if placeholder.get("ts"):
            client.chat_update(channel=event["channel"], ts=placeholder["ts"], text=fallback)
        else:
            say(text=fallback, thread_ts=thread_ts)


@app.action(ACTION_VIEW_ANNOUNCEMENT)
def handle_view_announcement(ack, body, client, logger):
    """Open a modal with the full announcement body when "View" is clicked.

    `ack()` must fire within 3s and the trigger_id is short-lived, so we fetch
    the single announcement and open the modal immediately.
    """
    ack()
    creds = _get_user_creds(body["user"]["id"])
    if not creds:
        return
    try:
        course_id, topic_id = body["actions"][0]["value"].split(":", 1)
        announcement = canvas_rest.get_announcement(creds, course_id, topic_id)
        client.views_open(
            trigger_id=body["trigger_id"],
            view=announcement_modal_view(announcement),
        )
    except Exception:
        logger.exception("failed to open announcement modal")


@app.action(ACTION_REMIND_ASSIGNMENT)
def handle_remind_assignment(ack, body, client, logger):
    """Create a private planner note (to-do) when '⏰ Remind me' is clicked.

    The button's value carries the assignment title + due date; we turn that
    into a Canvas planner note and confirm privately (ephemeral) to the clicker.
    """
    ack()
    creds = _get_user_creds(body["user"]["id"])
    if not creds:
        return
    try:
        payload = json.loads(body["actions"][0]["value"])
        title = payload.get("t") or "To-do"
        due = payload.get("d") or None
        canvas_rest.create_planner_note(creds, title=f"📌 {title}", todo_date=due)
        client.chat_postEphemeral(
            channel=body["channel"]["id"],
            user=body["user"]["id"],
            text=f"⏰ Added to your Canvas to-do: *{title}*",
        )
    except Exception:
        logger.exception("failed to create reminder planner note")
        try:
            client.chat_postEphemeral(
                channel=body["channel"]["id"],
                user=body["user"]["id"],
                text="Couldn't add that reminder — try again in a moment.",
            )
        except Exception:
            pass


@app.action(ACTION_CONFIRM_WRITE)
def handle_confirm_write(ack, body, client, logger):
    """Execute the write by calling the named canvas-mcp tool once."""
    ack()
    creds = _get_user_creds(body["user"]["id"])
    if not creds:
        return
    try:
        payload = json.loads(body["actions"][0]["value"])
        tool_name = payload["tool_name"]
        args = payload["args"]
        
        result_text = asyncio.run(bridge.call_tool_once(tool_name, args, creds))
        
        client.chat_update(
            channel=body["channel"]["id"],
            ts=body["message"]["ts"],
            text=_fit(f"✅ Done: {result_text}"),
            blocks=[]
        )
    except Exception as e:
        logger.exception("failed to execute write")
        try:
            client.chat_update(
                channel=body["channel"]["id"],
                ts=body["message"]["ts"],
                text=_fit(f"❌ Failed to execute: {e}"),
                blocks=[]
            )
        except Exception:
            pass


@app.action(ACTION_CANCEL_WRITE)
def handle_cancel_write(ack, body, client, logger):
    """Cancel the write and clear the confirmation blocks."""
    ack()
    try:
        client.chat_update(
            channel=body["channel"]["id"],
            ts=body["message"]["ts"],
            text="Okay, cancelled.",
            blocks=[]
        )
    except Exception:
        logger.exception("failed to cancel write")


def _build_home_view(creds: CanvasCreds) -> dict:
    """Assemble the App Home dashboard from live Canvas data (via REST).

    Each source is fetched independently so one failure just leaves that
    section empty rather than blanking the whole dashboard.
    """
    grades, assignments, todos = [], [], []
    try:
        grades = canvas_rest.list_current_grades(creds)
    except Exception:
        logging.exception("home: failed to load grades")
    try:
        assignments = canvas_rest.list_upcoming_assignments(creds)
    except Exception:
        logging.exception("home: failed to load upcoming assignments")
    try:
        todos = canvas_rest.list_todo(creds)
    except Exception:
        logging.exception("home: failed to load to-dos")
    return home_view(grades, assignments, todos, now=datetime.now())


@app.event("app_home_opened")
def handle_home_opened(event, client, logger):
    """Publish the Canvas dashboard when the user opens the bot's Home tab."""
    if event.get("tab") != "home":
        return  # also fires for the Messages tab — ignore that
    creds = _get_user_creds(event["user"])
    if not creds:
        return
    try:
        client.views_publish(user_id=event["user"], view=_build_home_view(creds))
    except Exception:
        logger.exception("failed to publish home view")


@app.action(ACTION_REFRESH_HOME)
def handle_refresh_home(ack, body, client, logger):
    """Re-publish the dashboard when '🔄 Refresh' is clicked."""
    ack()
    creds = _get_user_creds(body["user"]["id"])
    if not creds:
        return
    try:
        client.views_publish(user_id=body["user"]["id"], view=_build_home_view(creds))
    except Exception:
        logger.exception("failed to refresh home view")


if __name__ == "__main__":
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    print("⚡️ Canvas agent is running (Socket Mode)…")
    handler.start()
