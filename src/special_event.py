"""
special_event.py — On-Demand Special Event Pipeline
-----------------------------------------------------
Manually triggered. Scrapes any stated time window and logs results to
dedicated Special_Event_Log and Special_Event_Tweet_log tabs that are
completely separate from the daily pipeline and website running total.

Required env vars (set via workflow_dispatch inputs):
  WINDOW_START_ET  — "YYYY-MM-DD HH:MM" in Eastern Time
  WINDOW_END_ET    — "YYYY-MM-DD HH:MM" in Eastern Time
  EVENT_NAME       — short label for logs, e.g. "PATH tunnel closure"

Optional env vars:
  TWEET_TEXT       — if set, posts this text verbatim instead of
                     auto-generating from the delay stats
  DRY_RUN          — "true" to skip Sheets writes and tweet
"""

import sys
import os
import traceback
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

sys.path.insert(0, os.path.dirname(__file__))

from watcher import get_window_delays
from interpreter import interpret_alert
from calculator import (
    calculate_cost,
    calculate_system_wide_cost,
    calculate_line_suspension_cost,
)
from aggregator import deduplicate_by_train, calculate_totals
from logger import log_delay_batch_special_event, log_special_event_tweet

DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
MIN_DELAY_MINUTES = 10

RESOLUTION_PHRASES = [
    "on or close to schedule",
    "normal service",
    "service has resumed",
    "service restored",
    "service has been restored",
    "trains are running",
    "back on schedule",
    "cleared",
    "no longer",
    "resuming",
]


# ── Window helper ──────────────────────────────────────────────────────────────

def get_event_window(start_et_str, end_et_str):
    """
    Parse "YYYY-MM-DD HH:MM" ET strings and return UTC-aware datetimes.
    Uses ZoneInfo so EDT/EST offset is applied automatically.
    """
    fmt = "%Y-%m-%d %H:%M"
    start_et = datetime.strptime(start_et_str, fmt).replace(tzinfo=ET)
    end_et   = datetime.strptime(end_et_str,   fmt).replace(tzinfo=ET)
    return start_et.astimezone(timezone.utc), end_et.astimezone(timezone.utc)


# ── Processing ─────────────────────────────────────────────────────────────────

def interpret_window(raw_delays):
    """Interpret raw posts via Claude Haiku. Identical logic to daily.py."""
    interpreted = []
    for raw in raw_delays:
        if raw.get("system_wide") and not raw.get("line_suspension"):
            interpreted.append(raw)
            continue
        if raw.get("line_suspension"):
            interpreted.append(raw)
            continue

        try:
            result = interpret_alert(
                raw["text"],
                delay_minutes_hint=raw.get("delay_minutes")
            )
        except Exception as e:
            print(f"[SE] Interpreter error: {e}")
            continue

        if result is None:
            raw_text = raw.get("text", "").lower()
            if any(p in raw_text for p in RESOLUTION_PHRASES):
                continue
            watcher_line  = raw.get("line", "Unknown")
            watcher_delay = raw.get("delay_minutes")
            if watcher_line != "Unknown" and watcher_delay and watcher_delay >= 10:
                print(f"[SE] Interpreter null — watcher fallback: {watcher_line} | {watcher_delay} min")
                result = {
                    "line":            watcher_line,
                    "delay_minutes":   watcher_delay,
                    "direction":       "unknown",
                    "cause":           "unknown",
                    "train_number":    None,
                    "is_cancellation": False,
                    "time_band":       "peak",
                    "raw_text":        raw.get("text", ""),
                }
            else:
                continue

        result["timestamp"] = raw.get("timestamp", datetime.now(timezone.utc).isoformat())
        result["system_wide"] = False
        interpreted.append(result)

    return interpreted


def calculate_window(deduped_delays):
    """Route each event to the correct calculator."""
    calculated = []
    for event in deduped_delays:
        try:
            if event.get("system_wide") and not event.get("line_suspension"):
                result = calculate_system_wide_cost(event)
            elif event.get("line_suspension"):
                result = calculate_line_suspension_cost(event)
            else:
                result = calculate_cost(event)
            if result:
                calculated.append(result)
        except Exception as e:
            print(f"[SE] Calculator error: {e}")
            traceback.print_exc()
    return calculated


def process_window(start_utc, end_utc):
    """Full pipeline: fetch → interpret → deduplicate → calculate."""
    print(f"\n[SE] ── EVENT WINDOW ────────────────────────────────────────")
    print(f"[SE] {start_utc.strftime('%Y-%m-%d %H:%M')} – "
          f"{end_utc.strftime('%Y-%m-%d %H:%M')} UTC")

    try:
        raw = get_window_delays(start_utc, end_utc, min_delay_minutes=MIN_DELAY_MINUTES)
    except Exception as e:
        print(f"[SE] Fetch failed: {e}")
        traceback.print_exc()
        return [], 0

    raw_count = len(raw)
    if not raw:
        print("[SE] No qualifying posts in window.")
        return [], 0
    print(f"[SE] {raw_count} raw posts fetched.")

    interp = interpret_window(raw)
    print(f"[SE] {len(interp)} after interpretation.")

    deduped = deduplicate_by_train(interp)
    print(f"[SE] {len(deduped)} after deduplication.")

    calculated = calculate_window(deduped)
    print(f"[SE] {len(calculated)} events with costs calculated.")

    for ev in calculated:
        train  = ev.get("train_number") or "—"
        line   = ev.get("line", "Unknown")
        mins   = ev.get("delay_minutes") or "?"
        riders = ev.get("estimated_riders") or "?"
        cost   = ev.get("dollar_estimate") or 0
        print(f"[SE]   {line} | train #{train} | {mins} min | ~{riders} riders | ${cost:,.2f}")

    return calculated, raw_count


# ── Tweet formatting ───────────────────────────────────────────────────────────

def format_special_event_tweet(event_name, totals):
    """
    Auto-generate a tweet when TWEET_TEXT is not provided.

    Delays found:
      During the PATH tunnel closure, NJ Transit delayed commuters for
      2,100 person-hours. That's $92,400 in lost productive time.
      (8 delay events)

    No delays:
      NJ Transit ran smoothly during the PATH tunnel closure. No
      qualifying delays found in the event window.
    """
    if totals["event_count"] == 0:
        text = (
            f"NJ Transit ran smoothly during {event_name}. "
            f"No qualifying delays found in the event window."
        )
    else:
        person_minutes = totals["total_person_minutes"]
        cost = totals["total_cost"]
        event_count = totals["event_count"]

        time_str = (
            f"{totals['total_person_hours']:,} person-hours"
            if person_minutes >= 60_000
            else f"{person_minutes:,} person-minutes"
        )
        cost_str = (
            f"${cost / 1_000_000:.1f}M"
            if cost >= 1_000_000
            else f"${cost:,.0f}"
        )
        text = (
            f"During {event_name}, NJ Transit delayed commuters for "
            f"{time_str}. That's {cost_str} in lost productive time. "
            f"({event_count} delay event{'s' if event_count != 1 else ''})"
        )

    if len(text) > 295:
        text = text[:292] + "..."
    return text


def post_to_bluesky(text):
    """Post to Bluesky. Returns URI or None."""
    from atproto import Client
    handle   = os.environ.get("BLUESKY_HANDLE")
    password = os.environ.get("BLUESKY_PASSWORD")
    if not handle or not password:
        print("[SE] Bluesky credentials not set.")
        return None
    try:
        client = Client()
        client.login(handle, password)
        response = client.send_post(text)
        print(f"[SE] Posted: {response.uri}")
        return response.uri
    except Exception as e:
        print(f"[SE] Failed to post: {e}")
        return None


# ── Main ───────────────────────────────────────────────────────────────────────

def run():
    print(f"\n{'='*60}")
    print(f"NJT SPECIAL EVENT PIPELINE — {datetime.now(ET).strftime('%Y-%m-%d %H:%M:%S ET')}")
    if DRY_RUN:
        print("*** DRY RUN — no Sheets writes, no tweet ***")
    print(f"{'='*60}")

    window_start_str = os.environ.get("WINDOW_START_ET", "").strip()
    window_end_str   = os.environ.get("WINDOW_END_ET", "").strip()
    event_name       = os.environ.get("EVENT_NAME", "").strip()
    custom_tweet     = os.environ.get("TWEET_TEXT", "").strip()

    if not window_start_str or not window_end_str or not event_name:
        print("[SE] ERROR: WINDOW_START_ET, WINDOW_END_ET, and EVENT_NAME are required.")
        raise SystemExit(1)

    try:
        start_utc, end_utc = get_event_window(window_start_str, window_end_str)
    except ValueError as e:
        print(f"[SE] ERROR: Could not parse window times — {e}")
        print("[SE] Expected format: YYYY-MM-DD HH:MM (24-hour Eastern Time)")
        raise SystemExit(1)

    print(f"[SE] Event:  {event_name}")
    print(f"[SE] Window: {start_utc.strftime('%Y-%m-%d %H:%M')} – "
          f"{end_utc.strftime('%Y-%m-%d %H:%M')} UTC  "
          f"({window_start_str} – {window_end_str} ET)")
    if custom_tweet:
        print(f"[SE] Custom tweet provided — will post verbatim.")

    events, _raw_count = process_window(start_utc, end_utc)
    totals = calculate_totals(events)

    print(f"\n[SE] ── TOTALS ──────────────────────────────────────────────")
    print(f"[SE] {totals['event_count']} events | "
          f"{totals['total_person_minutes']:,} person-min | "
          f"${totals['total_cost']:,.2f}")

    # Use custom tweet if provided; otherwise auto-generate from stats.
    tweet_text = custom_tweet if custom_tweet else format_special_event_tweet(event_name, totals)
    tweet_was_custom = bool(custom_tweet)

    print(f"\n[SE] Tweet preview:\n{'-'*40}\n{tweet_text}\n{'-'*40}")
    print(f"[SE] Character count: {len(tweet_text)}")
    if tweet_was_custom:
        print("[SE] (custom — not auto-generated)")

    if not DRY_RUN:
        try:
            log_delay_batch_special_event(events, event_name)
        except Exception as e:
            print(f"[SE] Special_Event_Log write failed: {e}")

        uri = post_to_bluesky(tweet_text)

        try:
            log_special_event_tweet(
                text=tweet_text,
                total_cost=totals["total_cost"],
                event_count=totals["event_count"],
                uri=uri,
                person_hours=totals["total_person_hours"],
                event_name=event_name,
                window_start=window_start_str,
                window_end=window_end_str,
                tweet_was_custom=tweet_was_custom,
            )
        except Exception as e:
            print(f"[SE] Special_Event_Tweet_log write failed: {e}")
    else:
        print(f"[SE] DRY RUN: would log {len(events)} event(s) and post tweet.")

    print(f"\n[SE] Done.\n")


if __name__ == "__main__":
    run()
