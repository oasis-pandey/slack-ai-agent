"""Direct Canvas REST helpers for the rich announcement UI.

canvas-mcp returns pre-formatted *text* with no record IDs, so its output can't
be turned into clickable Slack blocks. These functions hit the Canvas REST API
directly to get structured announcement data (id, title, HTML body, url) that we
can render as Block Kit and reopen in a modal.

Uses CANVAS_BASE_URL (no path) + CANVAS_API_TOKEN from the environment — the same
pair `canvas_check.py` validates.
"""

import datetime
import os

import requests

TIMEOUT = 15
MAX_ANNOUNCEMENTS = 20  # cap the list we render as blocks
MAX_ASSIGNMENTS = 30  # cap per-course assignment fetch
UPCOMING_DAYS = 14  # window for "what's due" cards


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


def _shape_assignment(course, course_id, a: dict) -> dict:
    """Normalize a Canvas assignment payload to the fields the cards use."""
    return {
        "course": course or "",
        "course_id": str(course_id) if course_id is not None else "",
        "id": a.get("id"),
        "title": a.get("name") or a.get("title") or "(untitled)",
        "due_at": a.get("due_at"),
        "html_url": a.get("html_url"),
    }


def list_course_assignments(course_id) -> list[dict]:
    """Return a course's assignments (structured), for interactive cards.

    Also fetches the course name once so the cards can label the course by name
    rather than a numeric id.
    """
    base, headers = _api()
    resp = requests.get(
        f"{base}/api/v1/courses/{course_id}/assignments",
        headers=headers,
        params={"per_page": MAX_ASSIGNMENTS, "order_by": "due_at"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    name = None
    try:
        c = requests.get(
            f"{base}/api/v1/courses/{course_id}", headers=headers, timeout=TIMEOUT
        )
        if c.ok:
            name = c.json().get("name")
    except requests.RequestException:
        name = None
    return [_shape_assignment(name, course_id, a) for a in resp.json()]


def list_upcoming_assignments(days: int = UPCOMING_DAYS) -> list[dict]:
    """Return upcoming assignments/quizzes across all courses (structured).

    Uses Canvas's planner, which already carries the course name (`context_name`)
    and a relative URL we absolutize.
    """
    base, headers = _api()
    today = datetime.date.today()
    resp = requests.get(
        f"{base}/api/v1/planner/items",
        headers=headers,
        params={
            "start_date": today.isoformat(),
            "end_date": (today + datetime.timedelta(days=days)).isoformat(),
            "per_page": 50,
        },
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    out: list[dict] = []
    for it in resp.json():
        if it.get("plannable_type") not in ("assignment", "quiz"):
            continue
        pl = it.get("plannable", {}) or {}
        url = it.get("html_url") or ""
        if url.startswith("/"):
            url = base + url
        out.append(
            {
                "course": it.get("context_name") or "",
                "course_id": str(it.get("course_id") or ""),
                "id": pl.get("id"),
                "title": pl.get("title") or pl.get("name") or "(untitled)",
                "due_at": pl.get("due_at") or it.get("plannable_date"),
                "html_url": url or None,
            }
        )
    return out


def create_planner_note(title: str, details=None, todo_date=None) -> dict:
    """Create a private planner note (a personal to-do) for the current user.

    canvas-mcp doesn't wrap Canvas's planner notes, so we hit the REST API
    directly. The note is visible only to the token's owner — no one else sees
    it — which makes this the safest write to exercise end to end.
    """
    base, headers = _api()
    # Canvas rejects a planner note with a blank todo_date (400), so default to
    # today when the caller has none (e.g. an assignment with no due date).
    payload: dict = {
        "title": title,
        "todo_date": todo_date or datetime.date.today().isoformat(),
    }
    if details:
        payload["details"] = details
    resp = requests.post(
        f"{base}/api/v1/planner_notes", headers=headers, json=payload, timeout=TIMEOUT
    )
    resp.raise_for_status()
    return resp.json()
