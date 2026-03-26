"""
main.py — The Orchestrator
---------------------------
Two operating modes, set by the MODE environment variable:

  MODE=collect   (default, runs every 5 minutes)
    Station 1: Watcher   → polls Bluesky alert accounts
    Station 2: Interpreter → Claude parses alert text
    Station 3: Calculator  → computes dollar cost
    Station 4: Logger      → writes to Google Sheets
               Staging     → appends to delay_log.json for later aggregation

  MODE=summarize  (runs at 10:30am and 9:00pm ET)
    Reads delay_log.json for the relevant time window
    Deduplicates by train number (highest delay wins)
    Calculates total person-minutes and total cost
    Posts the summary to Bluesky

The PERIOD environment variable controls which summary to post:
  PERIOD=morning  → summarizes 5am–10:30am ET
  PERIOD=evening  → summarizes 3:30pm–9pm ET
"""

import sys
import os
import traceback
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from watcher import get_new_delays
from interpreter import interpret_alert
from calculator import calculate_cost
from logger import log_delay
from staging import append_delay
from aggregator import run_summary


# ── Configuration ─────────────────────────────────────────────────────────────
MODE = os.environ.get("MODE", "collect").lower()
PERIOD = os.environ.get("PERIOD", "morning").lower()
MIN_DELAY_MINUTES = 10
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"


# ─────────────────────────────────────────────────────────────────────────────
# COLLECT MODE — runs every 5 minutes
# ─────────────────────────────────────────────────────────────────────────────

def run_collect():
    """
    Poll Bluesky alert accounts for new delays.
    Process each one and append to the staging log.
    Does NOT post to Bluesky — that's handled by summarize mode.
    """
    print(f"\n{'='*60}")
    print(f"NJT DELAY TRACKER — COLLECT — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if DRY_RUN:
        print("*** DRY RUN — will not write to Sheets or staging log ***")
    print(f"{'='*60}\n")

    # Station 1: Watch
    try:
        new_delays = get_new_delays(min_delay_minutes=MIN_DELAY_MINUTES)
    except Exception as e:
        print(f"[MAIN] Watcher failed: {e}")
        traceback.print_exc()
        return

    if not new_delays:
        print("[MAIN] Nothing new. Done.")
        return

    print(f"[MAIN] Processing {len(new_delays)} new delay(s)...\n")

    for i, raw_delay in enumerate(new_delays, 1):
        print(f"\n--- Delay {i}/{len(new_delays)} ---")

        # Station 2: Interpret
        try:
            interpreted = interpret_alert(
                raw_delay["text"],
                delay_minutes_hint=raw_delay.get("delay_minutes")
            )
        except Exception as e:
            print(f"[MAIN] Interpreter failed: {e}")
            traceback.print_exc()
            continue

        if interpreted is None:
            print(f"[MAIN] Skipping — interpreter returned nothing.")
            continue

        interpreted["timestamp"] = raw_delay.get("timestamp", datetime.now().isoformat())

        # Station 3: Calculate
        try:
            calculated = calculate_cost(interpreted)
        except Exception as e:
            print(f"[MAIN] Calculator failed: {e}")
            traceback.print_exc()
            continue

        if calculated is None:
            print(f"[MAIN] Skipping — no cost calculated.")
            continue

        if not DRY_RUN:
            # Station 4: Log to Google Sheets
            try:
                log_delay(calculated)
            except Exception as e:
                print(f"[MAIN] Logger failed: {e}")
                traceback.print_exc()

            # Append to staging log for later aggregation
            try:
                append_delay(calculated)
            except Exception as e:
                print(f"[MAIN] Staging failed: {e}")
                traceback.print_exc()
        else:
            print(f"[MAIN] DRY RUN: would stage: "
                  f"{calculated.get('line')} | "
                  f"{calculated.get('delay_minutes')} min | "
                  f"${calculated.get('dollar_estimate', 0):,.2f}")

    print(f"\n[MAIN] Collect complete.\n")


# ─────────────────────────────────────────────────────────────────────────────
# SUMMARIZE MODE — runs at 10:30am and 9:00pm ET
# ─────────────────────────────────────────────────────────────────────────────

def post_to_bluesky_text(post_text):
    """Post pre-formatted text to Bluesky. Returns URI or None."""
    from atproto import Client

    handle = os.environ.get("BLUESKY_HANDLE")
    password = os.environ.get("BLUESKY_PASSWORD")

    if not handle or not password:
        print("[POSTER] BLUESKY_HANDLE or BLUESKY_PASSWORD not set.")
        return None

    try:
        client = Client()
        client.login(handle, password)
        response = client.send_post(post_text)
        return response.uri
    except Exception as e:
        print(f"[POSTER] Failed to post: {e}")
        return None


def run_summarize():
    """
    Read the staging log for the current rush hour window,
    aggregate, deduplicate, and post the summary to Bluesky.
    """
    print(f"\n{'='*60}")
    print(f"NJT TRACKER — SUMMARIZE ({PERIOD.upper()}) — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if DRY_RUN:
        print("*** DRY RUN — will not post or clear staging log ***")
    print(f"{'='*60}\n")

    try:
        post_text, totals = run_summary(PERIOD, dry_run=DRY_RUN)
    except Exception as e:
        print(f"[MAIN] Aggregator failed: {e}")
        traceback.print_exc()
        return

    if post_text is None:
        print(f"[MAIN] No {PERIOD} delays to report. Skipping post.")
        return

    print(f"\n[MAIN] Summary post preview:\n{'-'*40}\n{post_text}\n{'-'*40}")
    print(f"[MAIN] Character count: {len(post_text)}")

    if not DRY_RUN:
        uri = post_to_bluesky_text(post_text)
        if uri:
            print(f"[MAIN] Posted successfully: {uri}")
    else:
        print("[MAIN] DRY RUN: not posting.")

    print(f"\n[MAIN] Summarize complete.\n")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if MODE == "summarize":
        run_summarize()
    else:
        run_collect()
