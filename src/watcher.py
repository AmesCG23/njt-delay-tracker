"""
Station 1: The Watcher
----------------------
Scrapes NJ Transit's public travel alerts page every 5 minutes.
Finds new rail delay alerts and passes them downstream.

No API key required. Uses only public web data.
"""

import requests
from bs4 import BeautifulSoup
import re
import json
import os
from datetime import datetime

# ── The public URL we scrape ──────────────────────────────────────────────────
ALERTS_URL = "https://www.njtransit.com/travel-alerts-to"

# ── Rail line names we care about (bus/light rail excluded) ───────────────────
RAIL_LINES = [
    "Northeast Corridor",
    "North Jersey Coast",
    "Morris & Essex",
    "Montclair-Boonton",
    "Main/Bergen County",
    "Main Bergen County",
    "Raritan Valley",
    "Pascack Valley",
    "Port Jervis",
    "Atlantic City",
    "Gladstone",
    "NEC",  # common abbreviation in alerts
    "NJCL",
    "MOBO",
    "MBPJ",
    "RVL",
    "ME ",  # Morris & Essex abbreviation (space to avoid false matches)
    "PVL",
]

# ── Words that indicate a real service disruption ─────────────────────────────
DELAY_KEYWORDS = [
    "late",
    "delayed",
    "delay",
    "cancelled",
    "canceled",
    "suspended",
    "up to",       # "up to 25 min. late"
    "minutes late",
    "min. late",
]

# ── File where we track alerts we've already processed (prevents duplicates) ──
SEEN_ALERTS_FILE = "data/seen_alerts.json"

# Create the data directory and file if they don't exist yet
os.makedirs("data", exist_ok=True)
if not os.path.exists(SEEN_ALERTS_FILE):
    with open(SEEN_ALERTS_FILE, "w") as f:
        json.dump({}, f)


def load_seen_alerts():
    """Load the list of alert texts we've already processed."""
    if os.path.exists(SEEN_ALERTS_FILE):
        with open(SEEN_ALERTS_FILE, "r") as f:
            data = json.load(f)
            # Keep only alerts from the last 24 hours to prevent the file growing forever
            cutoff = datetime.now().timestamp() - (24 * 60 * 60)
            return {k: v for k, v in data.items() if v > cutoff}
    return {}


def save_seen_alerts(seen):
    """Save the updated seen alerts list."""
    os.makedirs("data", exist_ok=True)
    with open(SEEN_ALERTS_FILE, "w") as f:
        json.dump(seen, f, indent=2)


def fetch_alerts():
    """
    Fetch the NJT travel alerts page and return the raw HTML.
    Returns None if the request fails.
    """
    headers = {
        # Identify ourselves politely — good web citizenship
        "User-Agent": "NJT-Delay-Cost-Tracker/1.0 (public accountability project)"
    }
    try:
        response = requests.get(ALERTS_URL, headers=headers, timeout=15)
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        print(f"[WATCHER] Failed to fetch alerts page: {e}")
        return None


def parse_alerts(html):
    """
    Parse the NJT alerts page HTML and extract individual alert text strings.
    Returns a list of alert text strings.
    """
    soup = BeautifulSoup(html, "html.parser")
    alerts = []

    # NJT's alert page renders alerts in list items and div elements.
    # We cast a wide net: grab any text block that looks like an alert.
    # This is intentionally broad — we filter for relevance next.
    candidates = []

    # Strategy 1: look for list items (the most common pattern)
    for li in soup.find_all("li"):
        text = li.get_text(separator=" ", strip=True)
        if len(text) > 20:  # ignore tiny/empty items
            candidates.append(text)

    # Strategy 2: look for paragraphs and divs with alert-like content
    for tag in soup.find_all(["p", "div"]):
        text = tag.get_text(separator=" ", strip=True)
        # Only grab leaf-level text (not containers that repeat child text)
        if 30 < len(text) < 500 and tag.find() is None:
            candidates.append(text)

    # Deduplicate while preserving order
    seen_texts = set()
    for text in candidates:
        clean = " ".join(text.split())  # normalize whitespace
        if clean not in seen_texts:
            seen_texts.add(clean)
            alerts.append(clean)

    return alerts


def is_rail_delay(text):
    """
    Returns True if this alert text is about a rail delay we should track.
    Must mention a rail line AND contain a delay keyword.
    """
    text_lower = text.lower()

    has_rail = any(line.lower() in text_lower for line in RAIL_LINES)
    has_delay = any(keyword in text_lower for keyword in DELAY_KEYWORDS)

    return has_rail and has_delay


def extract_delay_minutes(text):
    """
    Try to extract a delay duration in minutes from the alert text.
    Returns an integer if found, or None if we can't parse it.

    Handles patterns like:
      "up to 25 min. late"
      "up to 25 minutes late"
      "is up to 45 min late"
      "30 minutes late"
    """
    # Pattern: "up to N min" or "N minutes late"
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


def identify_line(text):
    """
    Try to identify which rail line this alert is about.
    Returns the full line name, or "Unknown" if we can't tell.
    """
    text_lower = text.lower()

    line_map = {
        "northeast corridor": "Northeast Corridor",
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
        "atlantic city": "Atlantic City",
        "gladstone": "Gladstone Branch",
        "nec ": "Northeast Corridor",  # space to avoid partial matches
    }

    for keyword, line_name in line_map.items():
        if keyword in text_lower:
            return line_name

    return "Unknown"


def get_new_delays(min_delay_minutes=10):
    """
    Main function: fetch the alerts page, find new qualifying delays.

    Returns a list of delay dicts, each with:
      - text: the raw alert text
      - line: the rail line name
      - delay_minutes: integer delay duration (or None if unparseable)
      - timestamp: when we found it

    Only returns alerts we haven't seen before and that meet the
    minimum delay threshold.
    """
    print(f"[WATCHER] Checking for new delays at {datetime.now().strftime('%H:%M:%S')}...")

    html = fetch_alerts()
    if html is None:
        print("[WATCHER] Could not fetch alerts page. Will retry next run.")
        return []

    all_alerts = parse_alerts(html)
    seen = load_seen_alerts()

    new_delays = []

    for alert_text in all_alerts:
        # Skip if we've already processed this alert
        if alert_text in seen:
            continue

        # Skip if it's not a rail delay
        if not is_rail_delay(alert_text):
            continue

        # Extract delay duration
        delay_minutes = extract_delay_minutes(alert_text)

        # Skip if delay is below our threshold (or if we can't parse it, be
        # conservative and include it — Claude will filter further downstream)
        if delay_minutes is not None and delay_minutes < min_delay_minutes:
            print(f"[WATCHER] Skipping (only {delay_minutes} min): {alert_text[:60]}...")
            # Still mark as seen so we don't keep re-evaluating it
            seen[alert_text] = datetime.now().timestamp()
            continue

        line = identify_line(alert_text)

        delay = {
            "text": alert_text,
            "line": line,
            "delay_minutes": delay_minutes,
            "timestamp": datetime.now().isoformat(),
        }

        new_delays.append(delay)
        seen[alert_text] = datetime.now().timestamp()

        print(f"[WATCHER] NEW DELAY FOUND: {line} | {delay_minutes} min | {alert_text[:80]}...")

    # Save updated seen list
    save_seen_alerts(seen)

    if not new_delays:
        print("[WATCHER] No new qualifying delays found.")
    else:
        print(f"[WATCHER] Found {len(new_delays)} new delay(s).")

    return new_delays


# ── Run standalone for testing ────────────────────────────────────────────────
if __name__ == "__main__":
    delays = get_new_delays()
    if delays:
        print("\n=== DELAYS FOUND ===")
        for d in delays:
            print(json.dumps(d, indent=2))
    else:
        print("\nNo new delays at this time.")
