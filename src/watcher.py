"""
Station 1: The Watcher (Bluesky Edition)
-----------------------------------------
Polls NJ Transit alert bot accounts on Bluesky for new delay posts.
No API key required — reading public Bluesky posts is unauthenticated.

Primary source:  @njmetroalert.bsky.social  (all lines)
Secondary source: @njtransit--nec.bsky.social (NEC only, double-coverage
                  on the busiest/costliest line)

Why Bluesky instead of scraping NJT's website:
  - Stable versioned API, won't break when NJT redesigns their page
  - No HTML parsing needed — post text arrives as clean strings
  - No authentication required for reading public posts
  - Same alert text that njtranshit.com and the Twitter accounts use
"""

import json
import os
import re
from datetime import datetime, timezone

from atproto import Client

# ── Accounts to monitor ───────────────────────────────────────────────────────
# Each entry is (handle, line_hint)
# line_hint helps the interpreter when a post doesn't name the line explicitly
ALERT_ACCOUNTS = [
    ("njmetroalert.bsky.social", None),                    # All lines — primary
    ("njtransit--nec.bsky.social", "Northeast Corridor"),  # NEC double-coverage
    ("njtransit-me.bsky.social", "Morris & Essex"),
    ("njtransit-mobo.bsky.social", "Montclair-Boonton"),
    ("njtransit-mbpj.bsky.social", "Main/Bergen County"),
]

# How many recent posts to fetch per account each run
POSTS_PER_ACCOUNT = 30

# ── Keywords that indicate a real delay or cancellation ───────────────────────
DELAY_KEYWORDS = [
    "late",
    "delayed",
    "delay",
    "cancelled",
    "canceled",
    "suspended",
    "up to",
    "minutes late",
    "min. late",
    "min late",
]

# ── Rail line signals (filters out bus/light rail posts from njmetroalert) ────
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
    "train #", "train#",   # train number = rail
]

# ── State file ────────────────────────────────────────────────────────────────
SEEN_ALERTS_FILE = "data/seen_alerts.json"


def load_seen_alerts():
    """Load previously processed post URIs. Purges entries older than 48 hours."""
    os.makedirs("data", exist_ok=True)
    if os.path.exists(SEEN_ALERTS_FILE):
        with open(SEEN_ALERTS_FILE, "r") as f:
            data = json.load(f)
        cutoff = datetime.now(timezone.utc).timestamp() - (48 * 60 * 60)
        return {k: v for k, v in data.items() if v > cutoff}
    return {}


def save_seen_alerts(seen):
    """Persist the seen post URIs to disk."""
    os.makedirs("data", exist_ok=True)
    with open(SEEN_ALERTS_FILE, "w") as f:
        json.dump(seen, f, indent=2)


def make_client():
    """
    Create a Bluesky client pointed at the public read-only API.
    No login needed for reading public accounts.
    """
    return Client(base_url="https://public.api.bsky.app")


def fetch_account_posts(client, handle, limit=30):
    """
    Fetch the most recent posts from a Bluesky account.
    Returns a list of (uri, text, created_at) tuples.
    """
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


def is_rail_delay(text):
    """
    Returns True if this post is about a qualifying rail delay.
    Must contain a delay keyword AND reference a rail line or train number.
    """
    text_lower = text.lower()
    has_delay = any(kw in text_lower for kw in DELAY_KEYWORDS)
    has_rail = any(sig in text_lower for sig in RAIL_LINE_SIGNALS)
    return has_delay and has_rail


def extract_delay_minutes(text):
    """
    Try to extract delay duration from the post text.
    Returns integer minutes, or None if unparseable.
    """
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
    """
    Identify the rail line from post text.
    Falls back to line_hint (from which account we fetched it) if needed.
    """
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

    if line_hint:
        return line_hint

    return "Unknown"


def get_new_delays(min_delay_minutes=10):
    """
    Main entry point: poll all configured Bluesky accounts, return new delays.

    SEED_ONLY mode: if the environment variable SEED_ONLY=true is set, this
    function marks all current posts as seen but returns an empty list,
    processing nothing. Use this on first run to avoid a spam blast of old posts.

    Returns a list of dicts, each containing:
      - text:          raw post text
      - line:          rail line name
      - delay_minutes: integer (or None if unparseable)
      - timestamp:     ISO format string
      - source:        which Bluesky account this came from
    """
    # ── Check for seed mode ───────────────────────────────────────────────────
    seed_mode = os.environ.get("SEED_ONLY", "false").lower() == "true"
    if seed_mode:
        print("[WATCHER] SEED MODE — marking all current posts as seen, returning nothing.")

    print(f"[WATCHER] Polling Bluesky alert accounts at {datetime.now().strftime('%H:%M:%S')}...")

    client = make_client()
    seen = load_seen_alerts()
    new_delays = []

    for handle, line_hint in ALERT_ACCOUNTS:
        posts = fetch_account_posts(client, handle, limit=POSTS_PER_ACCOUNT)

        for uri, text, created_at in posts:
            # Skip already-processed posts
            if uri in seen:
                continue

            # Mark as seen regardless of whether it qualifies
            seen[uri] = datetime.now(timezone.utc).timestamp()

            # In seed mode: mark as seen but skip all processing
            if seed_mode:
                continue

            if not text:
                continue

            if not is_rail_delay(text):
                continue

            delay_minutes = extract_delay_minutes(text)

            # Skip below-threshold delays (but keep unparseable ones for Claude)
            if delay_minutes is not None and delay_minutes < min_delay_minutes:
                print(f"[WATCHER] Skipping ({delay_minutes} min): {text[:60]}...")
                continue

            line = identify_line(text, line_hint=line_hint)

            new_delays.append({
                "text": text,
                "line": line,
                "delay_minutes": delay_minutes,
                "timestamp": created_at or datetime.now(timezone.utc).isoformat(),
                "source": f"@{handle}",
            })
            print(f"[WATCHER] NEW: {line} | {delay_minutes} min | {text[:80]}...")

    save_seen_alerts(seen)

    if seed_mode:
        print(f"[WATCHER] Seed complete — {len(seen)} post URIs marked as seen.")
        return []

    # ── Deduplicate: same alert from multiple accounts = one event ────────────
    seen_texts = set()
    deduplicated = []
    for d in new_delays:
        normalized = " ".join(d["text"].lower().split())
        if normalized not in seen_texts:
            seen_texts.add(normalized)
            deduplicated.append(d)
        else:
            print(f"[WATCHER] Deduped cross-account duplicate: {d['text'][:60]}...")

    if not deduplicated:
        print("[WATCHER] No new qualifying delays found.")
    else:
        print(f"[WATCHER] Found {len(deduplicated)} new unique delay(s).")

    return deduplicated


# ── Run standalone for testing ────────────────────────────────────────────────
if __name__ == "__main__":
    delays = get_new_delays()
    if delays:
        print(f"\n=== {len(delays)} DELAY(S) FOUND ===")
        for d in delays:
            print(json.dumps(d, indent=2))
    else:
        print("\nNo new delays — service may be running normally.")
