"""Direct Canvas REST helpers for the rich announcement UI.

canvas-mcp returns pre-formatted *text* with no record IDs, so its output can't
be turned into clickable Slack blocks. These functions hit the Canvas REST API
directly to get structured announcement data (id, title, HTML body, url) that we
can render as Block Kit and reopen in a modal.

Uses CANVAS_BASE_URL (no path) + CANVAS_API_TOKEN from the environment — the same
pair `canvas_check.py` validates.
"""

import os

import requests

TIMEOUT = 15
MAX_ANNOUNCEMENTS = 20  # cap the list we render as blocks


def _api():
    base = os.environ["CANVAS_BASE_URL"].rstrip("/")
    token = os.environ["CANVAS_API_TOKEN"]
    return base, {"Authorization": f"Bearer {token}"}


def _shape(course_id, a: dict) -> dict:
    """Normalize a Canvas discussion_topic payload to the fields we use."""
    return {
        "course_id": str(course_id),
        "id": a.get("id"),
        "title": a.get("title") or "(untitled)",
        "html_url": a.get("html_url"),
        "posted_at": a.get("posted_at"),
        "message": a.get("message") or "",
    }


def list_course_announcements(course_id) -> list[dict]:
    """Return announcements for a course, newest-first, as structured records."""
    base, headers = _api()
    resp = requests.get(
        f"{base}/api/v1/courses/{course_id}/discussion_topics",
        headers=headers,
        params={"only_announcements": "true", "per_page": MAX_ANNOUNCEMENTS},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return [_shape(course_id, a) for a in resp.json()]


def get_announcement(course_id, topic_id) -> dict:
    """Return a single announcement (including its full HTML body)."""
    base, headers = _api()
    resp = requests.get(
        f"{base}/api/v1/courses/{course_id}/discussion_topics/{topic_id}",
        headers=headers,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return _shape(course_id, resp.json())
