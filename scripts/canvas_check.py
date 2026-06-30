"""Standalone smoke check: hit the Canvas REST API directly (no Slack, no MCP).

Validates that CANVAS_API_TOKEN + CANVAS_BASE_URL in .env actually work by
listing your active course enrollments. Run from the repo root after .env setup:

    python -m scripts.canvas_check
"""

import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

TIMEOUT = 15


def main() -> int:
    token = os.environ.get("CANVAS_API_TOKEN")
    base = os.environ.get("CANVAS_BASE_URL")
    if not token or not base:
        print("Missing CANVAS_API_TOKEN or CANVAS_BASE_URL in .env")
        return 1

    url = f"{base.rstrip('/')}/api/v1/courses"
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            params={"enrollment_state": "active", "per_page": 100},
            timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        print(f"Could not reach Canvas at {base}: {e}")
        return 1

    if resp.status_code == 401:
        print("Canvas rejected the token (401). Check CANVAS_API_TOKEN.")
        return 1
    if resp.status_code != 200:
        print(f"Canvas returned {resp.status_code}: {resp.text[:200]}")
        return 1

    courses = resp.json()
    print(f"✅ Connected to {base} — {len(courses)} active enrollment(s):\n")
    for c in courses:
        name = c.get("name") or "(unnamed)"
        print(f"  • {name}  (id {c.get('id')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
