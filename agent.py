"""The agent: a ReAct loop powered by Groq (Llama 3.3 70B) over Canvas tools.

run_agent(user_text) -> str
  1. Open a canvas-mcp session and expose its tools to Groq.
  2. Ask Groq. If it wants a tool, run it via canvas-mcp, feed the result back,
     and loop. If it answers, return the answer.

This module knows nothing about Slack — it takes text in, returns text out, so
it can be tested standalone (see `python agent.py "<question>"`).
"""

import asyncio
import datetime
import json
import os
import sys

from dotenv import load_dotenv
from groq import Groq

from canvas_tools import canvas_session, result_to_text, to_groq_tools

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
- Prefer the `get_my_*` tools (they span all courses, no arguments). Use \
`list_courses` first if you need a course to drill into.
- You are READ-ONLY — you cannot create or change anything in Canvas yet. If \
asked to, say writing isn't supported yet.

## Formatting for Slack
- Be concise: a short intro line, then bullet points only if there's a list.
- Refer to courses/assignments by NAME. Never show internal IDs.
- Use friendly dates ("Mon Jun 30"), not raw timestamps."""


async def run_agent(history: list[dict], on_tool_call=None) -> str:
    """Run the ReAct loop over a conversation and return the final answer.

    `history` is a list of {"role": "user"|"assistant", "content": str} in
    chronological order (the last entry is the user's current message). Passing
    the whole thread gives the agent the context to handle follow-ups like
    "those are CS courses".

    `on_tool_call`, if given, is called once (with no args) the first time the
    agent decides to hit Canvas — so the caller can show a "Checking Canvas…"
    notice only when it's actually warranted, not for plain chat.
    """
    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    today = datetime.date.today().strftime("%A, %B %d, %Y")
    notified = False

    async with canvas_session() as session:
        tools = to_groq_tools((await session.list_tools()).tools)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT.format(today=today)},
            *history,
        ]

        for _ in range(MAX_STEPS):
            # Groq's SDK is synchronous; blocking here is fine (nothing else
            # runs during a single user's request).
            resp = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0.2,
                timeout=GROQ_TIMEOUT,
            )
            msg = resp.choices[0].message

            # No tool calls -> Groq has its final answer.
            if not msg.tool_calls:
                return msg.content or "(I didn't produce a response.)"

            # First time we actually reach for Canvas, let the caller know.
            if on_tool_call and not notified:
                notified = True
                try:
                    on_tool_call()
                except Exception:  # a notification failure must not break the run
                    pass

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
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                try:
                    result = await asyncio.wait_for(
                        session.call_tool(tc.function.name, args),
                        timeout=TOOL_TIMEOUT,
                    )
                    text = result_to_text(result)
                except asyncio.TimeoutError:
                    text = f"{tc.function.name} timed out after {TOOL_TIMEOUT}s."
                except Exception as e:  # surface tool failures to the model
                    text = f"Error calling {tc.function.name}: {e}"

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tc.function.name,
                        "content": text[:TOOL_RESULT_CHAR_CAP],
                    }
                )

        return (
            "I looked into that but couldn't wrap it up — try asking something "
            "more specific."
        )


if __name__ == "__main__":
    question = " ".join(sys.argv[1:]) or "What assignments do I have coming up?"
    print(f"Q: {question}\n")
    print(asyncio.run(run_agent([{"role": "user", "content": question}])))
