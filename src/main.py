"""
main.py — Single-Pass Pipeline
--------------------------------
Runs once at summary time (10:30am or 9:00pm ET, triggered by GitHub Actions).

Steps:
  1. Determine the time window for this period (morning or evening rush)
  2. Fetch all qualifying delay posts from Bluesky for that window
  3. Interpret each post via Claude Haiku
  4. Calculate cost per event
  5. Deduplicate by train number (keep highest delay per train)
  6. Calculate totals
  7. Log all events to Google Sheets
  8. Post summary to Bluesky

No staging file. No collect/summarize split. No cache needed beyond
seen_alerts (which is no longer needed either — each run is self-contained).
"""

import sys
import os
import traceback
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from watcher import get_window_delays
from interpreter import interpret_alert
from calculator import calculate_cost
from logger import log_delay
from aggregator import get_utc_window, deduplicate_by_train, calculate_totals, format_summary_post

PERIOD  = os.environ.get("PERIOD", "morning").lower()
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
MIN_DELAY_MINUTES = 10


def post_to_bluesky(post_text):
    """Post a pre-formatted text string to Bluesky. Returns URI or None."""
    from atproto import Client

    handle   = os.environ.get("BLUESKY_HANDLE")
    password = os.environ.get("BLUESKY_PASSWORD")

    if not handle or not password:
        print("[POSTER] Credentials not set — skipping post.")
        return None

    try:
        client = Client()
        client.login(handle, password)
        response = client.send_post(post_text)
        return response.uri
    except Exception as e:
        print(f"[POSTER] Failed to post: {e}")
        return None


def run():
    print(f"\n{'='*60}")
    print(f"NJT DELAY TRACKER — {PERIOD.upper()} — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if DRY_RUN:
        print("*** DRY RUN — will not post or log ***")
    print(f"{'='*60}\n")

    # ── Step 1: Determine the time window ─────────────────────────────────────
    try:
        window_start, window_end = get_utc_window(PERIOD)
    except Exception as e:
        print(f"[MAIN] Could not determine time window: {e}")
        traceback.print_exc()
        return

    # ── Step 2: Fetch qualifying Bluesky posts for this window ────────────────
    try:
        raw_delays = get_window_delays(window_start, window_end,
                                       min_delay_minutes=MIN_DELAY_MINUTES)
    except Exception as e:
        print(f"[MAIN] Watcher failed: {e}")
        traceback.print_exc()
        return

    if not raw_delays:
        print(f"[MAIN] No qualifying delays found in the {PERIOD} window. Nothing to post.")
        return

    print(f"\n[MAIN] Processing {len(raw_delays)} posts...\n")

    # ── Steps 3 & 4: Interpret and calculate each event ───────────────────────
    calculated_delays = []

    for i, raw in enumerate(raw_delays, 1):
        print(f"--- {i}/{len(raw_delays)} ---")

        try:
            interpreted = interpret_alert(
                raw["text"],
                delay_minutes_hint=raw.get("delay_minutes")
            )
        except Exception as e:
            print(f"[MAIN] Interpreter failed: {e}")
            traceback.print_exc()
            continue

        if interpreted is None:
            print(f"[MAIN] Skipping — interpreter returned nothing.")
            continue

        interpreted["timestamp"] = raw.get("timestamp", datetime.now().isoformat())

        try:
            calculated = calculate_cost(interpreted)
        except Exception as e:
            print(f"[MAIN] Calculator failed: {e}")
            traceback.print_exc()
            continue

        if calculated is None:
            print(f"[MAIN] Skipping — no cost calculated.")
            continue

        calculated_delays.append(calculated)

    if not calculated_delays:
        print("[MAIN] No events survived interpretation. Nothing to post.")
        return

    # ── Step 5: Deduplicate by train ──────────────────────────────────────────
    deduplicated = deduplicate_by_train(calculated_delays)

    # ── Step 6: Calculate totals ──────────────────────────────────────────────
    totals = calculate_totals(deduplicated)
    print(f"\n[MAIN] Totals: {totals['event_count']} events | "
          f"{totals['total_person_minutes']:,} person-min | "
          f"${totals['total_cost']:,.2f}")

    # ── Step 7: Log all events to Google Sheets ───────────────────────────────
    if not DRY_RUN:
        for event in deduplicated:
            try:
                log_delay(event)
            except Exception as e:
                print(f"[MAIN] Logger failed for one event: {e}")
                traceback.print_exc()
    else:
        print(f"[MAIN] DRY RUN: would log {len(deduplicated)} events to Sheets.")

    # ── Step 8: Format and post summary ──────────────────────────────────────
    post_text = format_summary_post(PERIOD, totals)

    print(f"\n[MAIN] Summary post:\n{'-'*40}\n{post_text}\n{'-'*40}")
    print(f"[MAIN] Character count: {len(post_text)}")

    if not DRY_RUN:
        uri = post_to_bluesky(post_text)
        if uri:
            print(f"[MAIN] Posted: {uri}")
    else:
        print("[MAIN] DRY RUN: not posting.")

    print(f"\n[MAIN] Done.\n")


if __name__ == "__main__":
    run()
