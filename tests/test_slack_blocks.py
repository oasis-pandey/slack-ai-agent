"""Tests for the pure announcement Block Kit builders."""

from canvas_bot.slack.blocks import (
    ACTION_VIEW_ANNOUNCEMENT,
    MODAL_TITLE_LIMIT,
    SECTION_TEXT_LIMIT,
    announcement_list_blocks,
    announcement_modal_view,
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
