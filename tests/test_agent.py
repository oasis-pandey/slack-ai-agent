"""Tests for agent.py helpers (no network — Groq is never actually called)."""

from types import SimpleNamespace

from agent import _tool_use_failed_detail


def test_detects_tool_use_failed_and_returns_message():
    err = SimpleNamespace(
        body={"error": {"code": "tool_use_failed", "message": "bad args"}}
    )
    assert _tool_use_failed_detail(err) == "bad args"


def test_tool_use_failed_without_message_gets_default():
    err = SimpleNamespace(body={"error": {"code": "tool_use_failed"}})
    assert _tool_use_failed_detail(err) == "arguments did not match the tool schema."


def test_other_bad_request_returns_none():
    err = SimpleNamespace(body={"error": {"code": "context_length_exceeded"}})
    assert _tool_use_failed_detail(err) is None


def test_missing_or_malformed_body_returns_none():
    assert _tool_use_failed_detail(SimpleNamespace(body=None)) is None
    assert _tool_use_failed_detail(SimpleNamespace()) is None
    assert _tool_use_failed_detail(SimpleNamespace(body={"nope": 1})) is None
