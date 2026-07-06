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
from .store import get_creds as get_store_creds, set_creds as set_store_creds, delete_creds as delete_store_creds, CanvasCreds
from .slack.blocks import (
    ACTION_REFRESH_HOME,
    ACTION_REMIND_ASSIGNMENT,
    ACTION_VIEW_ANNOUNCEMENT,
    ACTION_CONFIRM_WRITE,
    ACTION_CANCEL_WRITE,
    ACTION_CONNECT_CANVAS,
    ACTION_DISCONNECT_CANVAS,
    ACTION_NEW_ANNOUNCEMENT,
    CALLBACK_CONNECT_MODAL,
    CALLBACK_COMPOSER_MODAL,
    announcement_list_blocks,
    announcement_modal_view,
    announcement_composer_view,
    assignment_card_blocks,
    write_confirmation_blocks,
    connect_prompt_blocks,
    connect_modal_view,
    digest_blocks,
    home_view,
    _grade_line,
    MSG_NO_GRADES,
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
    if user_id:
        import time
        now = time.time()
        # Cooldown check: max 1 request every 15 seconds per user
        if hasattr(app, "user_cooldowns"):
            last_run = app.user_cooldowns.get(user_id, 0)
            if now - last_run < 15:
                say(text="Whoa, slow down! Please wait 15 seconds between questions to conserve API limits. 🐢", thread_ts=thread_ts)
                return
            app.user_cooldowns[user_id] = now
        else:
            app.user_cooldowns = {user_id: now}

    creds = _get_user_creds(user_id) if user_id else None
    if not creds:
        say(blocks=connect_prompt_blocks(), text="Please connect Canvas.", thread_ts=thread_ts)
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

    def notify_canvas(tool_name: str):
        tool_msgs = {
            "list_courses": "Finding your courses",
            "list_upcoming_assignments": "Checking due dates",
            "get_course_details": "Reading course details",
            "get_assignment_details": "Reading assignment details",
            "list_recent_announcements": "Checking for announcements",
            "list_current_grades": "Fetching your grades",
            "list_todo": "Checking your to-do list",
            "create_announcement": "Drafting announcement",
        }
        msg = tool_msgs.get(tool_name, "Checking Canvas")
        text = WORKING_MSG + f" ({msg}…)"
        
        try:
            if "ts" not in placeholder:
                placeholder["ts"] = say(text=text, thread_ts=thread_ts)["ts"]
            else:
                app.client.chat_update(
                    channel=body["event"]["channel"],
                    ts=placeholder["ts"],
                    text=text
                )
        except Exception:
            pass

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
        try:
            client.views_publish(user_id=event["user"], view={"type": "home", "blocks": connect_prompt_blocks()})
        except Exception:
            logger.exception("failed to publish connect prompt view")
        return
    try:
        client.views_publish(user_id=event["user"], view=_build_home_view(creds))
    except Exception:
        logger.exception("failed to publish home view")


@app.action(ACTION_CONNECT_CANVAS)
def handle_connect_canvas(ack, body, client, logger):
    """Open the connect modal when the user clicks 'Connect Canvas'."""
    ack()
    try:
        client.views_open(trigger_id=body["trigger_id"], view=connect_modal_view())
    except Exception:
        logger.exception("failed to open connect modal")


@app.view(CALLBACK_CONNECT_MODAL)
def handle_connect_modal_submission(ack, body, client, view, logger):
    """Validate and store credentials when the connect modal is submitted."""
    state_values = view["state"]["values"]
    base_url = state_values["url_block"]["url_input"]["value"]
    token = state_values["token_block"]["token_input"]["value"]
    
    api_url = base_url.rstrip("/") + "/api/v1"
    creds = CanvasCreds(canvas_base_url=base_url, canvas_api_url=api_url, canvas_token=token)
    
    try:
        name = canvas_rest.validate_creds(creds)
    except Exception:
        ack(response_action="errors", errors={"token_block": "Could not connect. Is the token valid?"})
        return
        
    set_store_creds(body["user"]["id"], creds)
    ack(response_action="clear")
    try:
        client.chat_postMessage(channel=body["user"]["id"], text=f"✅ Connected as {name}")
    except Exception:
        pass


@app.action(ACTION_DISCONNECT_CANVAS)
def handle_disconnect_canvas(ack, body, client, logger):
    """Delete the user's stored Canvas credentials and refresh the Home tab."""
    ack()
    delete_store_creds(body["user"]["id"])
    try:
        client.views_publish(user_id=body["user"]["id"], view={"type": "home", "blocks": connect_prompt_blocks()})
    except Exception:
        logger.exception("failed to publish connect prompt view on disconnect")


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


@app.action(ACTION_NEW_ANNOUNCEMENT)
def handle_new_announcement(ack, body, client, logger):
    """Open the modal composer for a new announcement when the Home button is clicked."""
    ack()
    creds = _get_user_creds(body["user"]["id"])
    if not creds:
        return
    try:
        courses = canvas_rest.get_active_courses(creds)
        client.views_open(trigger_id=body["trigger_id"], view=announcement_composer_view(courses))
    except Exception:
        logger.exception("failed to open announcement composer")


@app.view(CALLBACK_COMPOSER_MODAL)
def handle_composer_modal_submission(ack, body, client, view, logger):
    """Parse the composer inputs and prompt for confirmation via a direct message."""
    ack(response_action="clear")
    user_id = body["user"]["id"]
    try:
        values = view["state"]["values"]
        course_opt = values["course_block"]["course_input"]["selected_option"]
        course_id = course_opt["value"]
        course_name = course_opt["text"]["text"]
        title = values["title_block"]["title_input"]["value"]
        message = values["body_block"]["body_input"]["value"]
        
        args = {
            "course_identifier": course_id,
            "title": title,
            "message": message,
        }
        summary = f"New announcement in {course_name}: {title}"
        
        blocks = write_confirmation_blocks(summary=summary, tool_name="create_announcement", args=args)
        client.chat_postMessage(channel=user_id, text="Please confirm your action.", blocks=blocks)
    except Exception:
        logger.exception("failed to handle composer submission")
        try:
            client.chat_postMessage(channel=user_id, text="Something went wrong while preparing the announcement.")
        except Exception:
            pass


@app.command("/canvas")
def handle_canvas_command(ack, body, respond, logger):
    """Handle /canvas slash commands for quick data access without LLM latency."""
    ack()
    user_id = body["user_id"]
    text = body.get("text", "").strip().lower()
    creds = _get_user_creds(user_id)
    
    if not creds:
        respond(blocks=connect_prompt_blocks())
        return

    try:
        if text == "due":
            assignments = canvas_rest.list_upcoming_assignments(creds)
            blocks = assignment_card_blocks(assignments)
            respond(blocks=blocks)
        elif text == "grades":
            grades = canvas_rest.list_current_grades(creds)
            if grades:
                lines = "\n".join(_grade_line(g) for g in grades)
                blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": f"*📊 Your Grades*\n{lines}"}}]
            else:
                blocks = [{"type": "context", "elements": [{"type": "mrkdwn", "text": MSG_NO_GRADES}]}]
            respond(blocks=blocks)
        elif text == "announcements":
            announcements = canvas_rest.list_recent_announcements(creds)
            blocks = announcement_list_blocks(announcements)
            respond(blocks=blocks)
        else:
            respond(text="Unknown command. Try `/canvas due`, `/canvas grades`, or `/canvas announcements`.")
    except Exception:
        logger.exception("failed to handle /canvas %s", text)
        respond(text="Something went wrong while fetching data from Canvas.")


def send_digest():
    """Scheduled job to post a Canvas digest to a configured user or channel."""
    target_id = os.environ.get("DIGEST_TARGET_USER_ID")
    if not target_id:
        return
    creds = _get_user_creds(target_id)
    if not creds:
        logging.warning("Digest skipped: no credentials found for %s", target_id)
        return
    try:
        assignments = canvas_rest.list_upcoming_assignments(creds)
    except Exception:
        assignments = []
    try:
        announcements = canvas_rest.list_recent_announcements(creds)
    except Exception:
        announcements = []
    
    blocks = digest_blocks(assignments, announcements)
    try:
        app.client.chat_postMessage(channel=target_id, text="Your Canvas Digest", blocks=blocks)
        logging.info("Sent scheduled digest to %s", target_id)
    except Exception as e:
        logging.exception("failed to send digest to %s: %s", target_id, e)


if __name__ == "__main__":
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    
    hour = int(os.environ.get("DIGEST_HOUR", 9))
    minute = int(os.environ.get("DIGEST_MINUTE", 0))
    scheduler = BackgroundScheduler()
    scheduler.add_job(send_digest, CronTrigger(hour=hour, minute=minute))
    scheduler.start()
    print(f"⏰ Scheduled digest for {hour:02d}:{minute:02d} daily.")

    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    print("⚡️ Canvas agent is running (Socket Mode)…")
    handler.start()
