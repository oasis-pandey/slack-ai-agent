"""Pure Block Kit builders for the announcement UI — no network, no Slack client.

Kept side-effect-free so the HTML→Slack conversion and block shapes are unit
testable. `main.py` renders these; the interactivity handler opens the modal.
"""

import html as _html
import re
from datetime import datetime

# action_id shared between the "View" button and main.py's @app.action handler.
ACTION_VIEW_ANNOUNCEMENT = "view_announcement"

# Slack limits we have to respect.
SECTION_TEXT_LIMIT = 2900  # hard limit is 3000; leave room for the truncation note
MODAL_TITLE_LIMIT = 24
HEADER_TEXT_LIMIT = 150


def _fmt_date(iso: str | None) -> str:
    """'2026-06-30T21:00:00Z' -> 'Jun 30, 2026' (best effort, empty on failure)."""
    if not iso:
        return ""
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%b %d, %Y")
    except (ValueError, TypeError):
        return ""


def html_to_slack(raw: str, limit: int = SECTION_TEXT_LIMIT) -> str:
    """Convert Canvas's HTML announcement body into Slack mrkdwn.

    Best-effort: links become <url|text>, lists get bullets, bold/italics map to
    Slack's, and everything else is stripped and entity-decoded. Long bodies are
    truncated with a pointer to open the full post in Canvas.
    """
    if not raw or not raw.strip():
        return "_(no content)_"

    text = raw
    # <a href="x">y</a> -> <x|y>
    text = re.sub(
        r'<a\b[^>]*\bhref="([^"]+)"[^>]*>(.*?)</a>',
        r"<\1|\2>",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<li\b[^>]*>", "\n• ", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(
        r"</(p|div|h[1-6]|li|ul|ol|tr)\s*>", "\n", text, flags=re.IGNORECASE
    )
    text = re.sub(r"</?(strong|b)\s*>", "*", text, flags=re.IGNORECASE)
    text = re.sub(r"</?(em|i)\s*>", "_", text, flags=re.IGNORECASE)
    # Strip remaining HTML tags, but NOT the <url|text> Slack links built above
    # (real tags have no "|"; Slack links do, so excluding "|" protects them).
    text = re.sub(r"<(/?)[a-zA-Z][^>|]*>", "", text)
    text = _html.unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    if len(text) > limit:
        text = (
            text[:limit].rstrip()
            + "\n\n…(truncated — open in Canvas for the full post)"
        )
    return text or "_(no content)_"


def _truncate(s: str, limit: int) -> str:
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def announcement_list_blocks(records: list[dict]) -> list[dict]:
    """A minimal header + one section-with-View-button per announcement."""
    count = len(records)
    blocks: list[dict] = [
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"📢  *{count} announcement{'' if count == 1 else 's'}*",
                }
            ],
        }
    ]
    for r in records:
        date = _fmt_date(r.get("posted_at"))
        meta = f"   ·   {date}" if date else ""
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*{r['title']}*{meta}"},
                "accessory": {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "View"},
                    "action_id": ACTION_VIEW_ANNOUNCEMENT,
                    # "course_id:announcement_id" — small, re-fetched on click.
                    "value": f"{r['course_id']}:{r['id']}",
                },
            }
        )
    return blocks


def announcement_modal_view(announcement: dict) -> dict:
    """A modal showing one announcement's full body + an Open-in-Canvas link."""
    title = announcement.get("title") or "Announcement"
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": _truncate(title, HEADER_TEXT_LIMIT)},
        }
    ]
    date = _fmt_date(announcement.get("posted_at"))
    if date:
        blocks.append(
            {"type": "context", "elements": [{"type": "mrkdwn", "text": f"Posted {date}"}]}
        )
    blocks.append(
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": html_to_slack(announcement.get("message"))},
        }
    )
    if announcement.get("html_url"):
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Open in Canvas"},
                        "url": announcement["html_url"],
                    }
                ],
            }
        )
    return {
        "type": "modal",
        "title": {"type": "plain_text", "text": _truncate(title, MODAL_TITLE_LIMIT)},
        "close": {"type": "plain_text", "text": "Close"},
        "blocks": blocks,
    }
