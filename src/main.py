"""
main.py — The Orchestrator
---------------------------
Runs the full 5-station pipeline in sequence:

  Station 1: Watcher   → scrapes NJT alerts page
  Station 2: Interpreter → Claude parses alert text
  Station 3: Calculator  → computes dollar cost
  Station 4: Logger      → writes to Google Sheets
  Station 5: Poster      → posts to Bluesky

Called by GitHub Actions every 5 minutes.
"""

import sys
import os
import traceback
from datetime import datetime

# Make sure we can import from src/
sys.path.insert(0, os.path.dirname(__file__))

from watcher import get_new_delays
from interpreter import interpret_alert
from calculator import calculate_cost
from logger import log_delay
from poster import post_to_bluesky


# ── Configuration ─────────────────────────────────────────────────────────────
MIN_DELAY_MINUTES = 10   # Don't process delays shorter than this
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"  # Set to "true" to skip posting


def run_pipeline():
    """Run the full pipeline once."""

    print(f"\n{'='*60}")
    print(f"NJT DELAY TRACKER — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if DRY_RUN:
        print("*** DRY RUN MODE — will not post to Bluesky or log to Sheets ***")
    print(f"{'='*60}\n")

    # ── Station 1: Watch ──────────────────────────────────────────────────────
    try:
        new_delays = get_new_delays(min_delay_minutes=MIN_DELAY_MINUTES)
    except Exception as e:
        print(f"[MAIN] Station 1 (Watcher) failed: {e}")
        traceback.print_exc()
        return

    if not new_delays:
        print("[MAIN] No new delays to process. Done.")
        return

    print(f"\n[MAIN] Processing {len(new_delays)} new delay(s)...\n")

    for i, raw_delay in enumerate(new_delays, 1):
        print(f"\n--- Delay {i}/{len(new_delays)} ---")

        # ── Station 2: Interpret ──────────────────────────────────────────────
        try:
            interpreted = interpret_alert(
                raw_delay["text"],
                delay_minutes_hint=raw_delay.get("delay_minutes")
            )
        except Exception as e:
            print(f"[MAIN] Station 2 (Interpreter) failed: {e}")
            traceback.print_exc()
            continue

        if interpreted is None:
            print(f"[MAIN] Skipping — Interpreter returned nothing for: {raw_delay['text'][:60]}...")
            continue

        # Carry over timestamp from watcher
        interpreted["timestamp"] = raw_delay.get("timestamp", datetime.now().isoformat())

        # ── Station 3: Calculate ──────────────────────────────────────────────
        try:
            calculated = calculate_cost(interpreted)
        except Exception as e:
            print(f"[MAIN] Station 3 (Calculator) failed: {e}")
            traceback.print_exc()
            continue

        if calculated is None:
            print(f"[MAIN] Skipping — Calculator could not estimate cost.")
            continue

        # ── Station 4: Log ────────────────────────────────────────────────────
        running_total = None
        if not DRY_RUN:
            try:
                running_total = log_delay(calculated)
            except Exception as e:
                print(f"[MAIN] Station 4 (Logger) failed: {e}")
                traceback.print_exc()
                # Don't skip posting just because logging failed
        else:
            print("[MAIN] DRY RUN: skipping Google Sheets log.")

        # ── Station 5: Post ───────────────────────────────────────────────────
        if not DRY_RUN:
            try:
                uri = post_to_bluesky(calculated, running_total=running_total)
                if uri:
                    print(f"[MAIN] Posted: {uri}")
            except Exception as e:
                print(f"[MAIN] Station 5 (Poster) failed: {e}")
                traceback.print_exc()
        else:
            print("[MAIN] DRY RUN: would post:")
            from poster import format_post
            print(format_post(calculated, running_total=12345.67))

    print(f"\n[MAIN] Pipeline complete.\n")


if __name__ == "__main__":
    run_pipeline()
