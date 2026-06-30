"""Tests for the Groq <-> MCP bridge translation (canvas_tools)."""

from types import SimpleNamespace

from canvas_tools import ALLOWED_TOOLS, result_to_text, to_groq_tools


def _mcp_tool(name, description="", input_schema=None):
    """A stand-in for an MCP tool definition object."""
    return SimpleNamespace(
        name=name, description=description, inputSchema=input_schema
    )


def test_to_groq_tools_keeps_only_whitelisted():
    tools = [
        _mcp_tool("list_courses", "List courses"),
        _mcp_tool("delete_everything", "danger"),  # not in ALLOWED_TOOLS
    ]
    out = to_groq_tools(tools)
    names = [t["function"]["name"] for t in out]
    assert names == ["list_courses"]
    assert "delete_everything" not in names


def test_to_groq_tools_schema_shape():
    schema = {"type": "object", "properties": {"course_id": {"type": "integer"}}}
    out = to_groq_tools([_mcp_tool("list_assignments", "  List  ", schema)])
    assert len(out) == 1
    fn = out[0]
    assert fn["type"] == "function"
    assert fn["function"]["name"] == "list_assignments"
    assert fn["function"]["description"] == "List"  # stripped
    assert fn["function"]["parameters"] == schema


def test_to_groq_tools_defaults_missing_schema():
    out = to_groq_tools([_mcp_tool("get_syllabus", None, None)])
    assert out[0]["function"]["parameters"] == {"type": "object", "properties": {}}


def test_to_groq_tools_handles_none_description():
    out = to_groq_tools([_mcp_tool("get_my_todo_items", None)])
    assert out[0]["function"]["description"] == ""


def test_allowed_tools_are_read_only():
    # Guard against accidentally whitelisting a mutating tool.
    forbidden = ("create", "update", "delete", "submit", "post", "edit")
    assert not any(
        any(verb in name for verb in forbidden) for name in ALLOWED_TOOLS
    )


def test_result_to_text_joins_text_blocks():
    result = SimpleNamespace(
        content=[
            SimpleNamespace(text="line one"),
            SimpleNamespace(text="line two"),
        ]
    )
    assert result_to_text(result) == "line one\nline two"


def test_result_to_text_empty_content():
    assert result_to_text(SimpleNamespace(content=[])) == "(no content returned)"


def test_result_to_text_skips_blank_blocks():
    result = SimpleNamespace(
        content=[SimpleNamespace(text=""), SimpleNamespace(text="kept")]
    )
    assert result_to_text(result) == "kept"
