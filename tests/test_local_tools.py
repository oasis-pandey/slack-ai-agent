"""Tests for local (non-MCP) agent tools — the planner-note write."""

from canvas_bot.canvas import local_tools


def test_schema_is_valid_groq_tool_shape():
    (schema,) = local_tools.LOCAL_TOOL_SCHEMAS
    assert schema["type"] == "function"
    fn = schema["function"]
    assert fn["name"] == "create_planner_note"
    assert fn["parameters"]["required"] == ["title"]
    assert local_tools.LOCAL_TOOL_NAMES == {"create_planner_note"}


def test_dispatch_calls_rest_and_summarizes(monkeypatch):
    captured = {}

    def fake_create(title, details=None, todo_date=None):
        captured.update(title=title, details=details, todo_date=todo_date)
        return {"title": title, "todo_date": todo_date}

    monkeypatch.setattr(local_tools.canvas_rest, "create_planner_note", fake_create)
    out = local_tools.dispatch_local_tool(
        "create_planner_note",
        {"title": "Review ch. 5", "todo_date": "2026-07-05"},
    )
    assert captured == {"title": "Review ch. 5", "details": None, "todo_date": "2026-07-05"}
    assert "Review ch. 5" in out
    assert "2026-07-05" in out


def test_dispatch_defaults_blank_title(monkeypatch):
    monkeypatch.setattr(
        local_tools.canvas_rest,
        "create_planner_note",
        lambda title, details=None, todo_date=None: {"title": title},
    )
    out = local_tools.dispatch_local_tool("create_planner_note", {})
    assert "Untitled to-do" in out


def test_dispatch_unknown_tool():
    assert "Unknown local tool" in local_tools.dispatch_local_tool("nope", {})
