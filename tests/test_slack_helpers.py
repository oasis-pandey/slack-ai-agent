"""Tests for the Slack-side helpers: dedupe and thread -> history building."""

import slack_helpers
from slack_helpers import already_handled, build_history


def setup_function():
    # Reset module-level caches so tests don't leak state into each other.
    slack_helpers._seen_events.clear()
    slack_helpers._user_names.clear()


# --- dedupe -----------------------------------------------------------------

def test_already_handled_first_time_false_then_true():
    assert already_handled("evt-1") is False
    assert already_handled("evt-1") is True


def test_already_handled_distinct_keys_independent():
    assert already_handled("a") is False
    assert already_handled("b") is False
    assert already_handled("a") is True


def test_already_handled_evicts_oldest_past_cap():
    cap = slack_helpers.SEEN_MAX
    for i in range(cap + 5):
        already_handled(f"k{i}")
    # The oldest keys were evicted, so they read as "not seen" again.
    assert already_handled("k0") is False
    assert len(slack_helpers._seen_events) <= cap + 1


# --- history building -------------------------------------------------------

class FakeSlackClient:
    """Minimal stand-in for the Slack WebClient used by build_history."""

    def __init__(self, pages, users=None):
        self._pages = pages  # list of conversations_replies responses
        self._users = users or {}
        self._call = 0

    def conversations_replies(self, **kwargs):
        page = self._pages[self._call]
        self._call += 1
        return page

    def users_info(self, user):
        return {"user": self._users.get(user, {"name": user})}


def _page(messages, next_cursor=None):
    meta = {"next_cursor": next_cursor} if next_cursor else {}
    return {"messages": messages, "response_metadata": meta}


def test_build_history_labels_roles_and_prefixes_names():
    client = FakeSlackClient(
        pages=[_page([
            {"user": "UALICE", "text": "<@UBOT> what's due?"},
            {"user": "UBOT", "text": "Here's your list"},
        ])],
        users={"UALICE": {"profile": {"display_name": "Alice"}}},
    )
    history = build_history(client, "C1", "111.0", bot_user_id="UBOT")
    assert history == [
        {"role": "user", "content": "Alice: what's due?"},
        {"role": "assistant", "content": "Here's your list"},
    ]


def test_build_history_strips_mentions_and_skips_placeholder():
    client = FakeSlackClient(
        pages=[_page([
            {"user": "UALICE", "text": "<@UBOT> hi"},
            {"user": "UBOT", "text": slack_helpers.WORKING_MSG},  # dropped
            {"user": "UALICE", "text": "   "},  # empty -> dropped
        ])],
        users={"UALICE": {"name": "alice"}},
    )
    history = build_history(client, "C1", "111.0", bot_user_id="UBOT")
    assert len(history) == 1
    assert history[0]["content"] == "alice: hi"


def test_build_history_treats_bot_id_messages_as_assistant():
    client = FakeSlackClient(
        pages=[_page([{"bot_id": "B123", "text": "automated reply"}])],
    )
    history = build_history(client, "C1", "111.0", bot_user_id="UBOT")
    assert history == [{"role": "assistant", "content": "automated reply"}]


def test_build_history_paginates_then_truncates_to_max():
    # Two pages of many messages; result is capped at MAX_HISTORY (the tail).
    msgs_p1 = [{"user": "U", "text": f"m{i}"} for i in range(15)]
    msgs_p2 = [{"user": "U", "text": f"m{i}"} for i in range(15, 30)]
    client = FakeSlackClient(
        pages=[_page(msgs_p1, next_cursor="CUR"), _page(msgs_p2)],
        users={"U": {"name": "u"}},
    )
    history = build_history(client, "C1", "111.0", bot_user_id="UBOT")
    assert len(history) == slack_helpers.MAX_HISTORY
    # Keeps the freshest tail, not the head.
    assert history[-1]["content"] == "u: m29"


def test_display_name_falls_back_when_lookup_fails():
    class Boom(FakeSlackClient):
        def users_info(self, user):
            raise RuntimeError("missing users:read scope")

    client = Boom(pages=[_page([{"user": "UX", "text": "hello"}])])
    history = build_history(client, "C1", "111.0", bot_user_id="UBOT")
    assert history[0]["content"] == "Someone: hello"
