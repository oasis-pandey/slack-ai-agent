"""Local (non-MCP) agent tools backed by direct Canvas REST.

canvas-mcp exposes ~92 tools but none for Canvas's personal *planner notes* (the
user's private to-do list). We add that here as a first-class agent tool that
sits alongside the MCP tools: `agent.py` merges these schemas into the Groq tool
list and routes calls whose name is in `LOCAL_TOOL_NAMES` to `dispatch_local_tool`
instead of canvas-mcp.

A planner note is visible only to its owner, so unlike the course-visible writes
it's low-stakes — the agent may create one directly (no confirmation dance).
"""

from . import rest as canvas_rest

# Groq/OpenAI tool schemas for tools we implement locally (not via canvas-mcp).
LOCAL_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "create_planner_note",
            "description": (
                "Add a private to-do item (a Canvas planner note) to the user's "
                "own planner. Visible ONLY to the user — no one else sees it. Use "
                "for personal reminders like 'remind me to review chapter 5 on "
                "Friday' or 'add finish the lab report to my to-do list'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Short to-do title, e.g. 'Review chapter 5'.",
                    },
                    "details": {
                        "type": "string",
                        "description": "Optional longer note / description.",
                    },
                    "todo_date": {
                        "type": "string",
                        "description": (
                            "Optional date (or datetime) the to-do is for, in ISO "
                            "8601, e.g. '2026-07-05' or '2026-07-05T17:00:00Z'. "
                            "Defaults to today if omitted."
                        ),
                    },
                },
                "required": ["title"],
            },
        },
    }
]

LOCAL_TOOL_NAMES = {s["function"]["name"] for s in LOCAL_TOOL_SCHEMAS}


def dispatch_local_tool(name: str, args: dict) -> str:
    """Run a local tool by name and return a plain-text result for the model."""
    if name == "create_planner_note":
        note = canvas_rest.create_planner_note(
            title=args.get("title") or "Untitled to-do",
            details=args.get("details"),
            todo_date=args.get("todo_date"),
        )
        when = note.get("todo_date")
        return (
            f"Created planner note '{note.get('title')}'"
            + (f" for {when}" if when else "")
            + " (private to-do, visible only to you)."
        )
    return f"Unknown local tool: {name}"
