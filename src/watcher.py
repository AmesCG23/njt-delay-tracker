"""
Station 1: The Watcher (Single-Pass Edition)
---------------------------------------------
Fetches recent posts from NJ Transit alert accounts on Bluesky
and returns those that fall within a specified time window.

Called once at summary time (10:30am and 9:00pm ET).
No continuous polling, no state file, no cache needed.

Since Bluesky stores all posts permanently, we can always reach back
and retrieve everything posted during the rush window — no need to
collect in real time.
"""

import re
from datetime import datetime, timezone, timedelta

from atproto import Client

# ── Accounts to monitor ───────────────────────────────────────────────────────
ALERT_ACCOUNTS = [
    ("njmetroalert.bsky.social", None),                    # All lines — primary
    ("njtransit--nec.bsky.social", "Northeast Corridor"),  # NEC double-coverage
    ("njtransit-me.bsky.social", "Morris & Essex"),
    ("njtransit-mobo.bsky.social", "Montclair-Boonton"),
    ("njtransit-mbpj.bsky.social", "Main/Bergen County"),
]

# Fetch this many posts per account per run.
# 100 is the API max and comfortably covers a full rush window.
POSTS_PER_ACCOUNT = 100

# ── Filters ───────────────────────────────────────────────────────────────────
DELAY_KEYWORDS = [
    "late", "delayed", "delay", "cancelled", "canceled",
    "suspended", "up to", "minutes late", "min. late", "min late",
]

RAIL_LINE_SIGNALS = [
    "northeast corridor", "nec ",
    "north jersey coast", "njcl",
    "morris & essex", "morris and essex",
    "montclair-boonton", "montclair boonton", "mobo",
    "main/bergen", "main bergen", "mbpj",
    "raritan valley", "rvl",
    "pascack valley", "pvl",
    "port jervis",
    "atlantic city rail",
    "gladstone",
    "train #", "train#",
]


def make_client():
    return Client(base_url="https://public.api.bsky.app")


def fetch_account_posts(client, handle, limit=100):
    """Fetch recent posts from a Bluesky account."""
    try:
        response = client.app.bsky.feed.get_author_feed(
            params={
                "actor": handle,
                "limit": limit,
                "filter": "posts_no_replies",
            }
        )
        posts = []
        for item in response.feed:
            post = item.post
            uri = post.uri
            text = post.record.text if hasattr(post.record, "text") else ""
            created_at = post.record.created_at
            posts.append((uri, text, created_at))
        return posts
    except Exception as e:
        print(f"[WATCHER] Could not fetch posts from @{handle}: {e}")
        return []


def parse_timestamp(ts_str):
    """Parse a Bluesky timestamp string into a UTC-aware datetime."""
    if not ts_str:
        return None
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        return None


def in_window(ts_str, window_start_utc, window_end_utc):
    """Return True if the timestamp falls within the UTC window."""
    dt = parse_timestamp(ts_str)
    if dt is None:
        return False
    return window_start_utc <= dt <= window_end_utc


def is_rail_delay(text):
    text_lower = text.lower()
    has_delay = any(kw in text_lower for kw in DELAY_KEYWORDS)
    has_rail = any(sig in text_lower for sig in RAIL_LINE_SIGNALS)
    return has_delay and has_rail


def extract_delay_minutes(text):
    patterns = [
        r"up to (\d+)\s*min",
        r"(\d+)\s*min(?:utes?)?\s*late",
        r"(\d+)\s*min(?:utes?)?\s*delay",
        r"delayed?\s*(?:by\s*)?(?:up to\s*)?(\d+)\s*min",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def identify_line(text, line_hint=None):
    text_lower = text.lower()
    line_map = {
        "northeast corridor": "Northeast Corridor",
        "nec ": "Northeast Corridor",
        "north jersey coast": "North Jersey Coast",
        "njcl": "North Jersey Coast",
        "morris & essex": "Morris & Essex",
        "morris and essex": "Morris & Essex",
        "montclair-boonton": "Montclair-Boonton",
        "montclair boonton": "Montclair-Boonton",
        "mobo": "Montclair-Boonton",
        "main/bergen": "Main/Bergen County",
        "main bergen": "Main/Bergen County",
        "mbpj": "Main/Bergen County",
        "raritan valley": "Raritan Valley",
        "rvl": "Raritan Valley",
        "pascack valley": "Pascack Valley",
        "pvl": "Pascack Valley",
        "port jervis": "Port Jervis",
        "atlantic city rail": "Atlantic City",
        "gladstone": "Gladstone Branch",
    }
    for keyword, line_name in line_map.items():
        if keyword in text_lower:
            return line_name
    return line_hint or "Unknown"


def get_window_delays(window_start_utc, window_end_utc, min_delay_minutes=10):
    """
    Fetch all qualifying rail delay posts from the configured Bluesky accounts
    that fall within the given UTC time window.

    Returns a list of delay dicts ready for the interpreter.
    No state file. No deduplication by URI — each run is self-contained.
    Cross-account text deduplication is applied so the same alert posted
    by two accounts counts once.
    """
    print(f"[WATCHER] Fetching posts from {window_start_utc.strftime('%H:%M')} "
          f"to {window_end_utc.strftime('%H:%M')} UTC...")

    client = make_client()
    candidates = []

    for handle, line_hint in ALERT_ACCOUNTS:
        posts = fetch_account_posts(client, handle, limit=POSTS_PER_ACCOUNT)
        in_window_count = 0

        for uri, text, created_at in posts:
            if not in_window(created_at, window_start_utc, window_end_utc):
                continue
            if not text or not is_rail_delay(text):
                continue

            delay_minutes = extract_delay_minutes(text)
            if delay_minutes is not None and delay_minutes < min_delay_minutes:
                continue

            line = identify_line(text, line_hint=line_hint)
            candidates.append({
                "text": text,
                "line": line,
                "delay_minutes": delay_minutes,
                "timestamp": created_at,
                "source": f"@{handle}",
            })
            in_window_count += 1

        print(f"[WATCHER] @{handle}: {in_window_count} qualifying posts in window.")

    # Deduplicate across accounts: same text from two accounts = one event
    seen_texts = set()
    deduplicated = []
    for d in candidates:
        normalized = " ".join(d["text"].lower().split())
        if normalized not in seen_texts:
            seen_texts.add(normalized)
            deduplicated.append(d)

    dupes = len(candidates) - len(deduplicated)
    if dupes:
        print(f"[WATCHER] Removed {dupes} cross-account duplicate(s).")
    print(f"[WATCHER] {len(deduplicated)} unique qualifying posts found.")

    return deduplicated
