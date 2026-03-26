"""
staging.py — Delay Log Manager
--------------------------------
Manages data/delay_log.json, a running list of delay events accumulated
during each observation window (morning rush / evening rush).

The collect pipeline appends to this file.
The summarize pipeline reads from it, aggregates, then clears the window.

Each entry in the log looks like:
{
    "train_number": "3876",          # from interpreter (or None)
    "line": "Northeast Corridor",
    "delay_minutes": 25,
    "timestamp": "2026-03-25T08:15:00+00:00",
    "riders": 825,
    "dollar_estimate": 8506.25,
    "raw_text": "NEC train #3876..."
}
"""

import json
import os
from datetime import datetime, timezone

DELAY_LOG_FILE = "data/delay_log.json"


def load_delay_log():
    """Load the full delay log. Returns a list of delay dicts."""
    os.makedirs("data", exist_ok=True)
    if os.path.exists(DELAY_LOG_FILE):
        with open(DELAY_LOG_FILE, "r") as f:
            return json.load(f)
    return []


def save_delay_log(log):
    """Save the delay log to disk."""
    os.makedirs("data", exist_ok=True)
    with open(DELAY_LOG_FILE, "w") as f:
        json.dump(log, f, indent=2)


def append_delay(calculated_delay):
    """
    Append a single processed delay event to the log.
    Called by the collect pipeline after calculator.py runs.
    """
    log = load_delay_log()

    entry = {
        "train_number": calculated_delay.get("train_number"),
        "line": calculated_delay.get("line", "Unknown"),
        "delay_minutes": calculated_delay.get("delay_minutes"),
        "timestamp": calculated_delay.get("timestamp", datetime.now(timezone.utc).isoformat()),
        "riders": calculated_delay.get("estimated_riders", 0),
        "dollar_estimate": calculated_delay.get("dollar_estimate", 0.0),
        "is_cancellation": calculated_delay.get("is_cancellation", False),
        "raw_text": calculated_delay.get("raw_text", ""),
    }

    log.append(entry)
    save_delay_log(log)
    print(f"[STAGING] Logged: {entry['line']} | train {entry['train_number']} | {entry['delay_minutes']} min")


def get_window_delays(start_hour_et, end_hour_et):
    """
    Return all delay entries whose timestamps fall within the given
    Eastern Time hour range (e.g. start_hour=5, end_hour=10 for morning rush).

    Note: hours are 0-23 in ET. We use a simple UTC offset of -4 (EDT) or -5 (EST).
    GitHub Actions runs in UTC so we convert appropriately.
    """
    log = load_delay_log()
    window = []

    for entry in log:
        try:
            ts = datetime.fromisoformat(entry["timestamp"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)

            # Convert to ET (approximate — UTC-4 during EDT, UTC-5 during EST)
            # We use a simple approach: check both offsets and accept if either matches
            from datetime import timedelta
            ts_edt = ts - timedelta(hours=4)  # Eastern Daylight Time
            ts_est = ts - timedelta(hours=5)  # Eastern Standard Time

            # Use whichever puts the timestamp in a reasonable range
            # (i.e. prefer EDT in summer, EST in winter — but we just check both)
            hour_edt = ts_edt.hour
            hour_est = ts_est.hour

            in_window = (
                (start_hour_et <= hour_edt < end_hour_et) or
                (start_hour_et <= hour_est < end_hour_et)
            )

            if in_window:
                window.append(entry)

        except (ValueError, TypeError):
            continue

    return window


def clear_window(start_hour_et, end_hour_et):
    """
    Remove all entries in the given time window from the log.
    Called after a summary post has been made.
    Also removes entries older than 24 hours as cleanup.
    """
    log = load_delay_log()
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    kept = []

    for entry in log:
        try:
            ts = datetime.fromisoformat(entry["timestamp"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)

            # Drop anything older than 24 hours
            if ts < cutoff:
                continue

            # Drop anything in the window we just summarized
            ts_edt = ts - timedelta(hours=4)
            ts_est = ts - timedelta(hours=5)
            hour_edt = ts_edt.hour
            hour_est = ts_est.hour

            in_window = (
                (start_hour_et <= hour_edt < end_hour_et) or
                (start_hour_et <= hour_est < end_hour_et)
            )

            if not in_window:
                kept.append(entry)

        except (ValueError, TypeError):
            kept.append(entry)  # keep entries we can't parse

    removed = len(log) - len(kept)
    save_delay_log(kept)
    print(f"[STAGING] Cleared window {start_hour_et}h-{end_hour_et}h ET: removed {removed} entries, kept {len(kept)}.")
