"""Pure Block Kit builders for the announcement UI — no network, no Slack client.

Kept side-effect-free so the HTML→Slack conversion and block shapes are unit
testable. `main.py` renders these; the interactivity handler opens the modal.
"""

import html as _html
import json
import re
from datetime import datetime

# action_ids shared between the buttons here and main.py's @app.action handlers.
ACTION_VIEW_ANNOUNCEMENT = "view_announcement"
ACTION_REMIND_ASSIGNMENT = "remind_assignment"

# Slack limits we have to respect.
SECTION_TEXT_LIMIT = 2900  # hard limit is 3000; leave room for the truncation note
MODAL_TITLE_LIMIT = 24
HEADER_TEXT_LIMIT = 150
# Slack caps a message at 50 blocks. We use 1 header + 1 section per announcement
# (+ an optional "showing N of M" note), and a 100+ item list is unreadable
# anyway, so we render only the newest few and point users at narrowing the ask.
MAX_LIST_ITEMS = 12
# One section per assignment card; keep well under the 50-block cap.
MAX_CARDS = 10
# Cap the button payload — Slack action `value` is limited to 2000 chars.
REMIND_TITLE_LIMIT = 180


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
    """A minimal header + one section-with-View-button per announcement.

    Renders only the newest `MAX_LIST_ITEMS` (Slack caps a message at 50 blocks,
    and a 100-item list is unreadable regardless) — sorted newest-first, with a
    footer noting how many were hidden and how to narrow the ask.
    """
    total = len(records)
    # Newest first. posted_at is an ISO string (or None); "" sorts last, which
    # is what we want for undated records.
    ordered = sorted(records, key=lambda r: r.get("posted_at") or "", reverse=True)
    shown = ordered[:MAX_LIST_ITEMS]

    header = (
        f"📢  *{total} announcements* — showing the {len(shown)} most recent"
        if total > len(shown)
        else f"📢  *{total} announcement{'' if total == 1 else 's'}*"
    )
    blocks: list[dict] = [
        {"type": "context", "elements": [{"type": "mrkdwn", "text": header}]}
    ]
    for r in shown:
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
    if total > len(shown):
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"_{total - len(shown)} more — ask about a specific "
                        "course to see the rest._",
                    }
                ],
            }
        )
    return blocks


def _due_meta(due_iso: str | None, now: datetime) -> tuple[str, str]:
    """Return (urgency_emoji, friendly_due_text) for an assignment due date.

    🔴 due today · 🟡 within a week · 🟢 later · ⚪ already past · '' if no/parse-fail.
    """
    if not due_iso:
        return "", "no due date"
    try:
        due = datetime.strptime(due_iso[:19], "%Y-%m-%dT%H:%M:%S")
    except (ValueError, TypeError):
        return "", ""
    time = due.strftime("%I:%M%p").lstrip("0").lower()  # '11:59pm'
    days = (due.date() - now.date()).days
    if days < 0:
        return "⚪", f"was due {due.strftime('%b %d')}"
    if days == 0:
        return "🔴", f"due today {time}"
    if days <= 7:
        return "🟡", f"due {due.strftime('%a %b %d')}"
    return "🟢", f"due {due.strftime('%b %d')}"


def _remind_value(record: dict) -> str:
    """Compact JSON payload for the 'Remind me' button (title + due date)."""
    return json.dumps(
        {"t": _truncate(record.get("title") or "To-do", REMIND_TITLE_LIMIT),
         "d": record.get("due_at") or ""}
    )


def assignment_card_blocks(records: list[dict], now: datetime | None = None) -> list[dict]:
    """Render assignments as interactive cards: urgency + course + due, an
    'Open in Canvas' link, and a '⏰ Remind me' button that creates a to-do.

    Sorted soonest-due first (undated last), capped at MAX_CARDS. `now` is
    injectable so the urgency logic is deterministically testable.
    """
    now = now or datetime.now()
    total = len(records)
    # Soonest due first; missing due dates sort last.
    ordered = sorted(records, key=lambda r: r.get("due_at") or "9999", reverse=False)
    shown = ordered[:MAX_CARDS]

    header = (
        f"📋  *{total} assignments* — showing the {len(shown)} soonest"
        if total > len(shown)
        else f"📋  *{total} assignment{'' if total == 1 else 's'}*"
    )
    blocks: list[dict] = [
        {"type": "context", "elements": [{"type": "mrkdwn", "text": header}]}
    ]
    for r in shown:
        emoji, due = _due_meta(r.get("due_at"), now)
        title = r.get("title") or "(untitled)"
        head = f"{emoji}  *{title}*" if emoji else f"*{title}*"
        ctx = [b for b in (r.get("course"), due) if b]
        if r.get("html_url"):
            ctx.append(f"<{r['html_url']}|Open in Canvas>")
        text = head + (("\n" + "  ·  ".join(ctx)) if ctx else "")
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": text},
                "accessory": {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "⏰ Remind me"},
                    "action_id": ACTION_REMIND_ASSIGNMENT,
                    "value": _remind_value(r),
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
