"""Tests for the pure announcement Block Kit builders."""

import json
from datetime import datetime

from canvas_bot.slack.blocks import (
    ACTION_REFRESH_HOME,
    ACTION_REMIND_ASSIGNMENT,
    ACTION_VIEW_ANNOUNCEMENT,
    ACTION_CONFIRM_WRITE,
    ACTION_CANCEL_WRITE,
    MAX_CARDS,
    MAX_LIST_ITEMS,
    MODAL_TITLE_LIMIT,
    SECTION_TEXT_LIMIT,
    _due_meta,
    announcement_list_blocks,
    announcement_modal_view,
    assignment_card_blocks,
    write_confirmation_blocks,
    home_view,
    html_to_slack,
)


# --- html_to_slack ----------------------------------------------------------

def test_html_links_become_slack_links():
    out = html_to_slack('See <a href="https://x.com/a">the page</a>.')
    assert "<https://x.com/a|the page>" in out


def test_html_lists_become_bullets():
    out = html_to_slack("<ul><li>one</li><li>two</li></ul>")
    assert "• one" in out and "• two" in out


def test_html_bold_and_breaks_and_entities():
    out = html_to_slack("<strong>Hi</strong><br>A &amp; B")
    assert "*Hi*" in out
    assert "A & B" in out
    assert "\n" in out


def test_html_strips_unknown_tags():
    out = html_to_slack('<div class="x"><span>plain</span></div>')
    assert "<div" not in out and "<span" not in out
    assert "plain" in out


def test_empty_html_has_placeholder():
    assert html_to_slack("") == "_(no content)_"
    assert html_to_slack("   ") == "_(no content)_"


def test_long_body_is_truncated():
    out = html_to_slack("x" * (SECTION_TEXT_LIMIT + 500))
    assert len(out) <= SECTION_TEXT_LIMIT + 60  # plus the truncation note
    assert "truncated" in out


# --- announcement_list_blocks -----------------------------------------------

def _rec(**kw):
    base = {
        "course_id": "123",
        "id": 99,
        "title": "Exam moved",
        "html_url": "https://c/announce/99",
        "posted_at": "2026-06-30T12:00:00Z",
        "message": "<p>body</p>",
    }
    base.update(kw)
    return base


def test_list_blocks_have_header_and_one_button_per_record():
    blocks = announcement_list_blocks([_rec(), _rec(id=100, title="Project 2")])
    buttons = [
        b["accessory"]
        for b in blocks
        if b.get("type") == "section" and "accessory" in b
    ]
    assert len(buttons) == 2
    assert all(btn["action_id"] == ACTION_VIEW_ANNOUNCEMENT for btn in buttons)


def test_button_value_encodes_course_and_announcement_id():
    blocks = announcement_list_blocks([_rec(course_id="123", id=99)])
    btn = next(b["accessory"] for b in blocks if "accessory" in b)
    assert btn["value"] == "123:99"


def test_large_list_is_capped_under_slack_block_limit():
    # 112 records used to produce 113 blocks and Slack rejected the message.
    records = [_rec(id=i, title=f"Announcement {i}") for i in range(112)]
    blocks = announcement_list_blocks(records)
    assert len(blocks) <= 50  # Slack's hard per-message block limit
    sections = [b for b in blocks if b.get("type") == "section"]
    assert len(sections) == MAX_LIST_ITEMS
    # Header should tell the user the full count and that it's truncated.
    assert "112" in blocks[0]["elements"][0]["text"]


def test_list_is_sorted_newest_first():
    records = [
        _rec(id=1, title="Old", posted_at="2026-01-01T00:00:00Z"),
        _rec(id=2, title="New", posted_at="2026-06-01T00:00:00Z"),
        _rec(id=3, title="Mid", posted_at="2026-03-01T00:00:00Z"),
    ]
    titles = [
        b["text"]["text"] for b in announcement_list_blocks(records)
        if b.get("type") == "section"
    ]
    assert titles[0].startswith("*New*")
    assert titles[-1].startswith("*Old*")


def test_small_list_has_no_truncation_footer():
    blocks = announcement_list_blocks([_rec(), _rec(id=100, title="Project 2")])
    # Only the header context block, no trailing "N more" footer.
    contexts = [b for b in blocks if b.get("type") == "context"]
    assert len(contexts) == 1


# --- assignment_card_blocks -------------------------------------------------

def _asg(**kw):
    base = {
        "course": "CS301",
        "course_id": "1",
        "id": 9,
        "title": "Homework 3",
        "due_at": "2026-07-03T23:59:00Z",
        "html_url": "https://c/courses/1/assignments/9",
    }
    base.update(kw)
    return base


NOW = datetime(2026, 7, 3, 9, 0, 0)


def test_due_meta_urgency_buckets():
    assert _due_meta("2026-07-03T23:59:00", NOW)[0] == "🔴"   # today
    assert _due_meta("2026-07-06T12:00:00", NOW)[0] == "🟡"   # within a week
    assert _due_meta("2026-07-20T12:00:00", NOW)[0] == "🟢"   # later
    assert _due_meta("2026-06-30T12:00:00", NOW)[0] == "⚪"   # past
    assert _due_meta(None, NOW) == ("", "no due date")


def test_card_has_remind_button_with_title_and_due():
    blocks = assignment_card_blocks([_asg()], now=NOW)
    section = next(b for b in blocks if b.get("type") == "section")
    btn = section["accessory"]
    assert btn["action_id"] == ACTION_REMIND_ASSIGNMENT
    payload = json.loads(btn["value"])
    assert payload["t"] == "Homework 3"
    assert payload["d"] == "2026-07-03T23:59:00Z"


def test_card_includes_open_in_canvas_link_and_course():
    blocks = assignment_card_blocks([_asg()], now=NOW)
    text = next(b for b in blocks if b.get("type") == "section")["text"]["text"]
    assert "CS301" in text
    assert "<https://c/courses/1/assignments/9|Open in Canvas>" in text


def test_cards_capped_and_under_block_limit():
    records = [_asg(id=i, title=f"A{i}") for i in range(20)]
    blocks = assignment_card_blocks(records, now=NOW)
    assert len(blocks) <= 50
    sections = [b for b in blocks if b.get("type") == "section"]
    assert len(sections) == MAX_CARDS
    assert "20 assignments" in blocks[0]["elements"][0]["text"]


def test_cards_sorted_soonest_due_first():
    records = [
        _asg(id=1, title="Later", due_at="2026-07-20T12:00:00Z"),
        _asg(id=2, title="Soon", due_at="2026-07-04T12:00:00Z"),
        _asg(id=3, title="NoDate", due_at=None),
    ]
    titles = [
        b["text"]["text"] for b in assignment_card_blocks(records, now=NOW)
        if b.get("type") == "section"
    ]
    assert "Soon" in titles[0]
    assert "NoDate" in titles[-1]  # undated sorts last


# --- home_view (App Home dashboard) -----------------------------------------

def _grade(course="CS.2318.001", grade="A", score=102.49):
    return {"course": course, "grade": grade, "score": score}


def test_home_view_is_a_home_surface_with_refresh():
    view = home_view([_grade()], [], [], now=NOW)
    assert view["type"] == "home"
    btn = view["blocks"][0]["accessory"]
    assert btn["action_id"] == ACTION_REFRESH_HOME


def test_home_view_grades_render_with_letter_and_percent():
    view = home_view([_grade(grade="A", score=92.98)], [], [], now=NOW)
    text = " ".join(
        b["text"]["text"] for b in view["blocks"] if b.get("type") == "section"
    )
    assert "CS.2318.001" in text
    assert "A (93%)" in text  # score rounded


def test_home_view_empty_due_soon_shows_friendly_state():
    view = home_view([_grade()], [], [], now=NOW)
    ctx = " ".join(
        e["text"]
        for b in view["blocks"]
        if b.get("type") == "context"
        for e in b["elements"]
    )
    assert "Nothing due" in ctx


def test_home_view_shows_assignment_cards_when_present():
    view = home_view([], [_asg()], [], now=NOW)
    remind = [
        b for b in view["blocks"]
        if b.get("type") == "section"
        and b.get("accessory", {}).get("action_id") == ACTION_REMIND_ASSIGNMENT
    ]
    assert len(remind) == 1  # the assignment card, reused from the message list


def test_home_view_todos_section_only_when_present():
    without = home_view([_grade()], [], [], now=NOW)
    with_todos = home_view([_grade()], [], [{"title": "Read ch. 5", "course": "ENG202"}], now=NOW)
    headers_without = [b for b in without["blocks"] if b.get("type") == "header"]
    headers_with = [b for b in with_todos["blocks"] if b.get("type") == "header"]
    assert len(headers_with) == len(headers_without) + 1  # adds the "To-dos" header


# --- announcement_modal_view ------------------------------------------------

def test_modal_basic_shape_and_title_limit():
    view = announcement_modal_view(_rec(title="A" * 50))
    assert view["type"] == "modal"
    assert len(view["title"]["text"]) <= MODAL_TITLE_LIMIT
    assert view["title"]["text"].endswith("…")


def test_modal_includes_body_and_canvas_link():
    view = announcement_modal_view(_rec(message="<p>hello world</p>"))
    section_texts = [
        b["text"]["text"] for b in view["blocks"] if b.get("type") == "section"
    ]
    assert any("hello world" in t for t in section_texts)
    link_buttons = [
        el
        for b in view["blocks"]
        if b.get("type") == "actions"
        for el in b["elements"]
        if el.get("url")
    ]
    assert link_buttons and link_buttons[0]["url"] == "https://c/announce/99"


def test_modal_without_url_has_no_link_button():
    view = announcement_modal_view(_rec(html_url=None))
    assert not any(b.get("type") == "actions" for b in view["blocks"])


# --- write_confirmation_blocks ----------------------------------------------

def test_write_confirmation_blocks_has_summary_and_buttons():
    summary = "Post announcement 'Hello' to CS101"
    tool_name = "create_announcement"
    args = {"course_identifier": "123", "title": "Hello", "message": "World"}
    blocks = write_confirmation_blocks(summary, tool_name, args)
    
    assert len(blocks) == 2
    assert blocks[0]["type"] == "section"
    assert summary in blocks[0]["text"]["text"]
    
    actions = blocks[1]
    assert actions["type"] == "actions"
    assert len(actions["elements"]) == 2
    
    confirm_btn = actions["elements"][0]
    assert confirm_btn["action_id"] == ACTION_CONFIRM_WRITE
    
    cancel_btn = actions["elements"][1]
    assert cancel_btn["action_id"] == ACTION_CANCEL_WRITE
    
    # Payload check
    payload = json.loads(confirm_btn["value"])
    assert payload["tool_name"] == tool_name
    assert payload["args"] == args
