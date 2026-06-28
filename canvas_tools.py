"""The Groq <-> canvas-mcp bridge.

Two responsibilities:
  1. Launch `canvas-mcp-server` as a stdio subprocess and hold an MCP session.
  2. Translate between MCP and Groq's (OpenAI-style) tool-calling format:
       - MCP tool definitions  -> Groq `tools` schema
       - an MCP tool result    -> plain text Groq can read

canvas-mcp exposes ~92 tools. We expose only a focused, read-only subset to
keep the prompt small and tool selection accurate (see ALLOWED_TOOLS).
"""

import os
import shutil
from contextlib import asynccontextmanager

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Read-only, student-facing subset of canvas-mcp's tools. The `get_my_*` tools
# work across all courses with no arguments; the rest let the agent drill into
# a specific course once it has an ID from list_courses.
ALLOWED_TOOLS = {
    "list_courses",
    "get_my_upcoming_assignments",
    "get_my_todo_items",
    "get_my_course_grades",
    "get_my_submission_status",
    "list_assignments",
    "list_announcements",
    "get_assignment_details",
    "get_syllabus",
}


@asynccontextmanager
async def canvas_session():
    """Spawn canvas-mcp over stdio and yield an initialized MCP session.

    The subprocess inherits our env, so it sees CANVAS_API_TOKEN /
    CANVAS_API_URL. Env is read here (not at import) so load_dotenv() has run.
    """
    server = StdioServerParameters(
        command=shutil.which("canvas-mcp-server") or "canvas-mcp-server",
        args=[],
        env=os.environ.copy(),
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def to_groq_tools(mcp_tools):
    """Convert MCP tool definitions into Groq's tool-calling schema."""
    groq_tools = []
    for t in mcp_tools:
        if t.name not in ALLOWED_TOOLS:
            continue
        groq_tools.append(
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": (t.description or "").strip(),
                    # MCP's inputSchema is already JSON Schema, which is exactly
                    # what Groq's "parameters" field expects.
                    "parameters": t.inputSchema or {"type": "object", "properties": {}},
                },
            }
        )
    return groq_tools


def result_to_text(result):
    """Flatten an MCP CallToolResult into a single text string for the LLM."""
    parts = [getattr(block, "text", str(block)) for block in result.content]
    return "\n".join(p for p in parts if p) or "(no content returned)"
