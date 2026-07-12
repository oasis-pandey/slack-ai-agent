"""The agent: a ReAct loop powered by Groq (Llama 3.3 70B) over Canvas tools.

run_agent(user_text) -> str
  1. Open a canvas-mcp session and expose its tools to Groq.
  2. Ask Groq. If it wants a tool, run it via canvas-mcp, feed the result back,
     and loop. If it answers, return the answer.

This module knows nothing about Slack — it takes text in, returns text out, so
it can be tested standalone (see `python -m canvas_bot.agent "<question>"`).
"""

import asyncio
import datetime
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Callable

from dotenv import load_dotenv
from groq import BadRequestError, Groq, RateLimitError
from .store import CanvasCreds

from .canvas import rest as canvas_rest
from .canvas.bridge import canvas_session, result_to_text, to_groq_tools
from .canvas.local_tools import (
    LOCAL_TOOL_NAMES,
    LOCAL_TOOL_SCHEMAS,
    dispatch_local_tool,
)

load_dotenv()

MODEL = "llama-3.3-70b-versatile"
MAX_STEPS = 6  # safety bound on the reason/act loop
TOOL_RESULT_CHAR_CAP = 6000  # keep big tool outputs from blowing the context
GROQ_TIMEOUT = 30  # seconds per LLM call
TOOL_TIMEOUT = 25  # seconds per Canvas tool call

SYSTEM_PROMPT = """You are a friendly Canvas assistant for a college student, \
chatting inside Slack. Today's date is {today}.

## When to use tools vs. just talk
- Only call a Canvas tool when the user actually asks for Canvas data \
(assignments, courses, grades, to-dos, announcements, syllabus).
- For greetings, small talk, thanks, or vague openers ("hey", "hi", "what's \
up"), DO NOT call any tool. Just reply warmly and briefly, e.g. "Hey! I can \
help with your Canvas — assignments, due dates, grades, announcements. What \
do you need?" Then stop.

## Answering well
- Answer ONLY what was asked. Do not dump every course or every assignment \
when the user asked a narrow question. Match the scope of the question.
- When the user asks for a subset (e.g. "my math courses", "CS assignments"), \
filter accurately by the course's actual subject/name. If NONE match, say so \
honestly: "I don't see any math courses in your current enrollments." NEVER \
relabel unrelated courses (e.g. don't call CS courses "math") to fill an answer.
- Use the conversation so far for context. If the user corrects you or refers \
to "those"/"that", look back at what was just discussed.
- This may be a group thread: user messages are prefixed with the speaker's \
name (e.g. "Alex: ..."). Use those names to follow who said what. The person \
asking is whoever sent the most recent message. Don't echo the "Name:" prefix \
in your reply.

## Data integrity
- Use tools to get REAL data. Never invent course names, assignment names, due \
dates, or grades. If a tool errors or returns nothing, say so plainly.
- Prefer the `get_my_*` tools (they span all courses, no arguments).
- Course-specific tools (`list_announcements`, `list_assignments`, \
`get_syllabus`, `get_assignment_details`) take a `course_identifier`. This MUST \
be the course's NUMERIC id, passed as a STRING (e.g. "2228696") — NEVER the \
course name or code. So always call `list_courses` FIRST, find the course the \
user means, then call the course-specific tool with that course's numeric id. \
If a course tool 404s or errors, don't keep retrying the same identifier — \
re-check the id from `list_courses` or tell the user you couldn't find it.
## Writing to Canvas
You CAN make a few changes in Canvas. Anything not listed here (submitting \
assignments, grading, editing pages, deleting) is NOT supported — say so plainly.

PRIVATE to-do (low-stakes, no confirmation needed): `create_planner_note` adds \
a personal to-do to the user's own planner, visible ONLY to them. When the user \
asks to add a reminder / to-do / planner item, just call it directly (pick a \
sensible `todo_date` if they gave one) and then say what you added. No course, \
no confirmation dance needed — it's private and reversible.

COURSE-VISIBLE writes (confirmation MANDATORY): create an announcement \
(`create_announcement`), start a discussion (`create_discussion_topic`), post a \
new entry to a discussion (`post_discussion_entry`), and reply to a discussion \
entry (`reply_to_discussion_entry`). These are seen by other people and can't be \
undone from here, so NEVER call one on the user's first request. \
Instead: resolve the details (course via `list_courses`; for posting/replying, \
find the topic via `list_discussion_topics` and the entry via \
`list_discussion_entries`), then STOP and return the pending write for confirmation \
by calling the `request_write_confirmation` tool instead of calling the write tool \
directly or asking for a typed yes. CRITICAL: the `args` you pass to \
`request_write_confirmation` must use the NUMERIC course id as a STRING for \
`course_identifier` (e.g. "2691672"), NOT the course name. Get this id from \
`list_courses` first. The `summary` argument should be a one-line human \
description (course name + action + title/message) — only `summary` uses the \
human-readable name; `args.course_identifier` must be the numeric id. \
Only after the user confirms via the UI will the write happen. Read-only \
questions never need confirmation — just answer.

- After a successful write, briefly confirm what was done. If a write tool \
errors (e.g. you lack permission in that course — you're only a teacher/TA in \
some), say so plainly; don't pretend it worked.

## Formatting for Slack — keep it minimal and clean
- Lead with the answer. No preamble ("Sure!", "Here is what I found"). Get to it.
- Use a tight bulleted list (`•`) only when there are multiple items; otherwise \
one or two short sentences. Never a wall of text.
- Bold just the key thing per line (e.g. *the assignment name*), nothing else.
- Refer to courses/assignments by NAME. Never show internal IDs.
- Friendly dates ("Mon Jun 30"), never raw timestamps. At most one emoji, and \
only if it genuinely helps. Cut anything the user didn't ask for."""


REQUEST_WRITE_CONFIRMATION_SCHEMA = {
    "type": "function",
    "function": {
        "name": "request_write_confirmation",
        "description": "Request user confirmation before making a course-visible write.",
        "parameters": {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string", "description": "The name of the write tool (e.g. create_announcement)"},
                "args": {"type": "object", "description": "The arguments for the write tool as a JSON object"},
                "summary": {"type": "string", "description": "A one-line human description of the action (course name + action + title/message)"}
            },
            "required": ["tool_name", "args", "summary"]
        }
    }
}


@dataclass
class AgentResult:
    """What run_agent returns: the natural-language answer plus any structured
    announcement records gathered along the way (so the Slack layer can render
    them as clickable blocks instead of plain text)."""

    text: str
    announcements: list = field(default_factory=list)
    assignments: list = field(default_factory=list)
    pending_write: dict | None = None


def _tool_use_failed_detail(err: BadRequestError):
    """If `err` is Groq's 'tool_use_failed' schema rejection, return its human
    message (so we can feed it back to the model); otherwise return None."""
    body = getattr(err, "body", None)
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict) and error.get("code") == "tool_use_failed":
        return error.get("message") or "arguments did not match the tool schema."
    return None


async def run_agent(
    history: list[dict],
    creds: CanvasCreds,
    on_tool_call: Callable[[str], None] | None = None,
) -> AgentResult:
    """Run the ReAct loop over a conversation and return an AgentResult.

    `history` is a list of {"role": "user"|"assistant", "content": str} in
    chronological order (the last entry is the user's current message). Passing
    the whole thread gives the agent the context to handle follow-ups like
    "those are CS courses".

    `on_tool_call`, if given, is called once per tool call (with the tool name)
    the moment the agent decides to hit Canvas.

    When the model fetches announcements, we also pull structured records (via
    Canvas REST) so the Slack layer can render them as clickable blocks; they
    ride back on AgentResult.announcements.
    """
    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    today = datetime.date.today().strftime("%A, %B %d, %Y")
    announcements: list = []
    seen_announcements: set = set()
    assignments: list = []
    seen_assignments: set = set()

    async with canvas_session(creds) as session:
        # canvas-mcp tools + our local REST-backed tools (e.g. planner notes).
        tools = to_groq_tools((await session.list_tools()).tools) + LOCAL_TOOL_SCHEMAS + [REQUEST_WRITE_CONFIRMATION_SCHEMA]
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT.format(today=today)},
            *history,
        ]

        for _ in range(MAX_STEPS):
            # Groq's SDK is synchronous; blocking here is fine (nothing else
            # runs during a single user's request).
            try:
                resp = client.chat.completions.create(
                    model=MODEL,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    temperature=0.2,
                    timeout=GROQ_TIMEOUT,
                )
            except RateLimitError:
                # Free-tier Groq quota (per-minute or per-day tokens) is spent.
                # Tell the user plainly instead of a generic failure.
                return AgentResult(
                    text=(
                        "I've hit my AI usage limit for now (Groq free tier). "
                        "Give it a few minutes and ask again. 🙏"
                    ),
                    announcements=announcements,
                    assignments=assignments,
                )
            except BadRequestError as e:
                # Groq validates the model's tool-call arguments against the
                # tool schema server-side and 400s with "tool_use_failed" when
                # they don't match (e.g. it emits a numeric course id where the
                # schema wants a string). The bad turn never lands in `messages`,
                # so we feed the error back and let the model retry — bounded by
                # MAX_STEPS. Anything else is a real error; re-raise it.
                detail = _tool_use_failed_detail(e)
                if detail is None:
                    raise
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "Your last tool call was rejected: "
                            f"{detail} Retry it with arguments that match the "
                            "tool's schema exactly. In particular, ID-like "
                            'parameters (e.g. course_identifier) must be strings '
                            '("2228696"), not numbers.'
                        ),
                    }
                )
                continue
            msg = resp.choices[0].message

            # No tool calls -> Groq has its final answer.
            if not msg.tool_calls:
                return AgentResult(
                    text=msg.content or "(I didn't produce a response.)",
                    announcements=announcements,
                    assignments=assignments,
                )

            # Record the assistant's tool-call turn, then execute each call.
            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                }
            )

            for tc in msg.tool_calls:
                if on_tool_call:
                    try:
                        on_tool_call(tc.function.name)
                    except Exception:
                        pass
                import logging
                logging.info("Agent tool call: %s(%s)", tc.function.name, tc.function.arguments)

                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                try:
                    if tc.function.name == "request_write_confirmation":
                        return AgentResult(
                            text="Please confirm this action.",
                            announcements=announcements,
                            assignments=assignments,
                            pending_write={
                                "tool_name": args.get("tool_name"),
                                "args": args.get("args"),
                                "summary": args.get("summary")
                            }
                        )
                    elif tc.function.name in LOCAL_TOOL_NAMES:
                        # Handled by us via direct Canvas REST, not canvas-mcp.
                        text = dispatch_local_tool(tc.function.name, args, creds)
                    else:
                        result = await asyncio.wait_for(
                            session.call_tool(tc.function.name, args),
                            timeout=TOOL_TIMEOUT,
                        )
                        text = result_to_text(result)
                except asyncio.TimeoutError:
                    text = f"{tc.function.name} timed out after {TOOL_TIMEOUT}s."
                except Exception as e:  # surface tool failures to the model
                    text = f"Error calling {tc.function.name}: {e}"

                # canvas-mcp's announcement text lacks IDs, so when the model
                # lists announcements we re-fetch structured records directly
                # from Canvas REST for the clickable Slack UI. Best-effort: a
                # failure here just means no rich blocks, the text answer stands.
                if tc.function.name == "list_announcements":
                    course_id = args.get("course_identifier")
                    if course_id is not None:
                        try:
                            for rec in canvas_rest.list_course_announcements(creds, course_id):
                                key = (rec["course_id"], rec["id"])
                                if key not in seen_announcements:
                                    seen_announcements.add(key)
                                    announcements.append(rec)
                        except Exception:
                            pass

                # Same idea for assignments -> interactive cards. "Upcoming"
                # spans all courses (planner); "list_assignments" is one course.
                if tc.function.name in ("get_my_upcoming_assignments", "list_assignments"):
                    try:
                        if tc.function.name == "get_my_upcoming_assignments":
                            recs = canvas_rest.list_upcoming_assignments(creds)
                        else:
                            cid = args.get("course_identifier")
                            recs = (
                                canvas_rest.list_course_assignments(creds, cid)
                                if cid is not None
                                else []
                            )
                        for rec in recs:
                            key = (rec["course_id"], rec["id"])
                            if key not in seen_assignments:
                                seen_assignments.add(key)
                                assignments.append(rec)
                    except Exception:
                        pass

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tc.function.name,
                        "content": text[:TOOL_RESULT_CHAR_CAP],
                    }
                )

        return AgentResult(
            text=(
                "I looked into that but couldn't wrap it up — try asking "
                "something more specific."
            ),
            announcements=announcements,
            assignments=assignments,
        )


if __name__ == "__main__":
    question = " ".join(sys.argv[1:]) or "What assignments do I have coming up?"
    print(f"Q: {question}\n")
    from .store import CanvasCreds
    fallback_creds = CanvasCreds(
        canvas_base_url=os.environ.get("CANVAS_BASE_URL", ""),
        canvas_api_url=os.environ.get("CANVAS_API_URL", ""),
        canvas_token=os.environ.get("CANVAS_API_TOKEN", "")
    )
    result = asyncio.run(run_agent([{"role": "user", "content": question}], fallback_creds))
    print(result.text)
    if result.announcements:
        print(f"\n[{len(result.announcements)} announcement(s) for the Slack UI]")
