"""Standalone smoke check: reach Canvas through canvas-mcp (no Slack, no Groq).

Exercises the same bridge the agent uses — launches `canvas-mcp-server` over
stdio, lists the tools we whitelist, and calls `list_courses` — so you can
confirm the MCP layer works before wiring in the LLM. Run from the repo root:

    python -m scripts.canvas_mcp_check
"""

import asyncio
import sys

from dotenv import load_dotenv

from canvas_bot.canvas.bridge import ALLOWED_TOOLS, canvas_session, result_to_text

load_dotenv()


async def main() -> int:
    async with canvas_session() as session:
        tools = (await session.list_tools()).tools
        exposed = [t.name for t in tools if t.name in ALLOWED_TOOLS]
        print(f"✅ canvas-mcp up — exposing {len(exposed)} whitelisted tool(s):")
        for name in exposed:
            print(f"  • {name}")

        print("\nCalling list_courses…\n")
        result = await session.call_tool("list_courses", {})
        print(result_to_text(result))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as e:
        print(f"canvas-mcp check failed: {e}")
        sys.exit(1)
