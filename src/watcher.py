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

Each returned delay dict has a "system_wide" boolean flag.
System-wide Penn Station alerts are routed to a different cost
calculation in main.py — see calculator.calculate_system_wide_cost().
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
    "morris & essex", "morris and essex", "m and e", "m&e",
    "montclair-boonton", "montclair boonton", "mobo",
    "main/bergen", "main bergen", "mbpj",
    "raritan valley", "rvl",
    "pascack valley", "pvl",
    "port jervis",
    "atlantic city rail",
    "gladstone",
    "train #", "train#",
    # System-wide alerts don't name a line but do name Penn Station
    "penn station",
    "psny",
    "rail service",
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


def extract_delay_minutes(text):
    """Try to extract delay duration from post text. Returns int or None."""
    patterns = [
        r"up to (\d+)[- ]min",
        r"(\d+)\s*min(?:utes?)?\s*late",
        r"(\d+)\s*min(?:utes?)?\s*delay",
        r"delayed?\s*(?:by\s*)?(?:up to\s*)?(\d+)\s*min",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def is_system_wide_alert(text):
    """
    Returns True if this post describes a system-wide Penn Station delay
    affecting all inbound/outbound trains — not a single named train.

    These are routed to calculate_system_wide_cost() instead of the
    normal per-train calculator. Only fires for delays >= 15 minutes.

    Examples that match:
      "NJ TRANSIT rail service is subject to up to 20-minute delays
       into and out of Penn Station New York."
      "Due to Amtrak signal issues, service is subject to up to 15-min
       delays into and out of PSNY."
    """
    text_lower = text.lower()

    # Must mention Penn Station
    has_penn = any(sig in text_lower for sig in [
        "penn station", "psny", "penn station new york"
    ])
    if not has_penn:
        return False

    # Must describe a system-wide condition, not a specific train
    system_patterns = [
        r"rail service is subject to",
        r"service is subject to",
        r"subject to up to",
        r"delays into and out",
        r"delays in and out",
        r"into and out of penn",
        r"in and out of penn",
        r"all (nj transit )?trains",
        r"rail service.{0,40}delay",
        r"service.{0,40}delay.{0,40}penn",
        r"service.{0,30}suspend",
        r"suspend.{0,30}service",
        r"trains are suspended",
        r"no (rail )?service",
    ]
    has_system = any(re.search(p, text_lower) for p in system_patterns)
    if not has_system:
        return False

    # For suspension language, delay_minutes may not be parseable —
    # treat as a 60-minute event (same as cancellation assumption).
    # For delay language, must be 15 minutes or more to qualify.
    delay_minutes = extract_delay_minutes(text)
    is_suspension = any(re.search(p, text_lower) for p in [
        r"suspend", r"no (rail )?service"
    ])
    if is_suspension:
        return True   # always qualifies; duration assumed in calculator
    if delay_minutes is None or delay_minutes < 15:
        return False

    return True


def is_line_suspension_alert(text):
    """
    Returns True if this post describes a full line suspension —
    meaning an entire rail line has been suspended, not just a single train.

    These are treated as catastrophic line-level events: we use that
    line's riders/hour × 1 hour assumed duration instead of per-train ridership.

    Examples that match:
      "Morris & Essex service is suspended in both directions due to Portal Bridge."
      "NEC service has been suspended between Newark and New York."
      "North Jersey Coast Line service is suspended due to flooding."
    """
    text_lower = text.lower()

    # Must mention suspension (not just a delay)
    has_suspension = any(re.search(p, text_lower) for p in [
        r"service is suspended",
        r"service has been suspended",
        r"service suspended",
        r"trains? (are|have been) suspended",
        r"suspended.{0,50}(both directions|all service|rail service)",
        r"(both directions|all service|rail service).{0,50}suspended",
    ])
    if not has_suspension:
        return False

    # Must mention a specific rail line (not a Penn-wide alert — those
    # are handled by is_system_wide_alert)
    line_signals = [
        "northeast corridor", "nec ",
        "north jersey coast", "njcl",
        "morris & essex", "morris and essex", "m and e", "m&e", "m and e", "m&e",
        "montclair-boonton", "montclair boonton", "mobo",
        "main/bergen", "main bergen", "mbpj",
        "raritan valley", "rvl",
        "pascack valley", "pvl",
        "port jervis",
        "atlantic city rail",
        "gladstone",
    ]
    has_line = any(sig in text_lower for sig in line_signals)
    if not has_line:
        return False

    # Exclude if it's actually a Penn-wide alert (handled separately)
    if any(s in text_lower for s in ["penn station", "psny"]):
        return False

    return True


def is_rail_delay(text):
    """Returns True if this post is about a qualifying rail delay."""
    text_lower = text.lower()
    has_delay = any(kw in text_lower for kw in DELAY_KEYWORDS)
    has_rail = any(sig in text_lower for sig in RAIL_LINE_SIGNALS)
    return has_delay and has_rail


def identify_line(text, line_hint=None):
    """Identify the rail line from post text."""
    text_lower = text.lower()
    line_map = {
        "northeast corridor": "Northeast Corridor",
        "nec ": "Northeast Corridor",
        "north jersey coast": "North Jersey Coast",
        "njcl": "North Jersey Coast",
        "morris & essex": "Morris & Essex",
        "morris and essex": "Morris & Essex",
        "m and e": "Morris & Essex",
        "m&e": "Morris & Essex",
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

    Returns a list of delay dicts. Each dict has a "system_wide" boolean:
      - system_wide=True:  Penn Station system-wide alert >= 15 min
                           → routed to calculate_system_wide_cost()
      - system_wide=False: normal per-train alert
                           → routed to interpret_alert() + calculate_cost()
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
            if not text:
                continue

            # Check for Penn Station system-wide alert first
            if is_system_wide_alert(text):
                delay_minutes = extract_delay_minutes(text)
                candidates.append({
                    "text": text,
                    "line": "System-Wide (Penn Station)",
                    "delay_minutes": delay_minutes,
                    "timestamp": created_at,
                    "source": f"@{handle}",
                    "system_wide": True,
                })
                in_window_count += 1
                print(f"[WATCHER] PENN SYSTEM-WIDE: {delay_minutes} min | {text[:80]}...")
                continue

            # Check for line-level suspension
            if is_line_suspension_alert(text):
                line = identify_line(text, line_hint=line_hint)
                candidates.append({
                    "text": text,
                    "line": line,
                    "delay_minutes": None,   # no duration — assumed in calculator
                    "timestamp": created_at,
                    "source": f"@{handle}",
                    "system_wide": True,
                    "line_suspension": True,
                })
                in_window_count += 1
                print(f"[WATCHER] LINE SUSPENSION: {line} | {text[:80]}...")
                continue

            # Normal per-train rail delay
            if not is_rail_delay(text):
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
                "system_wide": False,
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

    system_wide_count = sum(1 for d in deduplicated if d["system_wide"])
    normal_count = len(deduplicated) - system_wide_count
    print(f"[WATCHER] {normal_count} normal + {system_wide_count} system-wide = "
          f"{len(deduplicated)} unique qualifying posts.")

    return deduplicated
