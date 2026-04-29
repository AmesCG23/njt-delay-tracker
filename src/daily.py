"""
daily.py — The Full Daily Pipeline
------------------------------------
Runs once per weekday at ~3pm-5pm ET.

Fetches yesterday's delays from Bluesky in two passes (morning, then
evening), processes each window independently, logs to Google Sheets,
then posts one daily summary tweet.

Processing order per window:
  1. Fetch raw posts from Bluesky within the time window
  2. Interpret each post via Claude Haiku (extract line, train #, delay)
  3. Deduplicate by train number (keep highest delay per train)
     System-wide alerts deduplicated by (line, window)
  4. Calculate costs on the deduplicated set
  5. Log to Google Sheets Event Log tab

After both windows:
  6. Combine totals
  7. Post one tweet
  8. Log tweet to Tweet_log tab

Windows (ET — ZoneInfo handles EST/EDT automatically):
  Morning: 5:00 AM–10:30 AM ET
  Evening: 3:00 PM– 8:30 PM ET
"""

import sys
import os
import traceback
from datetime import datetime, timezone, timedelta, date
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
from logger import log_delay_batch, log_tweet, clear_run_log, log_run, log_run_summary, clear_alert_log, log_alert_batch

DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
MIN_DELAY_MINUTES = 10

# OVERRIDE_DATE: if set (YYYY-MM-DD), use this as "yesterday" instead of computing
# from the current time. Used by the benchmark workflow to replay known dates.
OVERRIDE_DATE = os.environ.get("OVERRIDE_DATE", "").strip()


# ── Window helpers ────────────────────────────────────────────────────────────

def get_yesterday_windows():
    """
    Return yesterday's morning and evening UTC windows.

    Windows are defined in Eastern Time and converted to UTC so that
    ZoneInfo("America/New_York") handles the EST/EDT offset automatically —
    no hardcoded ±4 or ±5 anywhere.

      Morning: 5:00 AM–10:30 AM ET
      Evening: 3:00 PM– 8:30 PM ET  (same ET calendar day, no midnight crossing)

    If OVERRIDE_DATE is set (YYYY-MM-DD), that date is used as "yesterday"
    directly — used by the benchmark workflow to replay a known date.
    """
    now_et = datetime.now(ET)

    if OVERRIDE_DATE:
        try:
            yesterday_et = date.fromisoformat(OVERRIDE_DATE)
            print(f"[DAILY] OVERRIDE_DATE set — using {yesterday_et} as yesterday")
        except ValueError:
            print(f"[DAILY] Invalid OVERRIDE_DATE '{OVERRIDE_DATE}' — falling back to yesterday")
            yesterday_et = now_et.date() - timedelta(days=1)
    else:
        yesterday_et = now_et.date() - timedelta(days=1)

    y = yesterday_et

    # Build in ET — astimezone(UTC) converts correctly for whichever offset is in effect
    morning_start = datetime(y.year, y.month, y.day,  5,  0, tzinfo=ET).astimezone(timezone.utc)
    morning_end   = datetime(y.year, y.month, y.day, 10, 30, tzinfo=ET).astimezone(timezone.utc)
    evening_start = datetime(y.year, y.month, y.day, 15,  0, tzinfo=ET).astimezone(timezone.utc)
    evening_end   = datetime(y.year, y.month, y.day, 20, 30, tzinfo=ET).astimezone(timezone.utc)

    print(f"[DAILY] Yesterday (ET): {yesterday_et}")
    print(f"[DAILY] Morning window: {morning_start.strftime('%Y-%m-%d %H:%M')} – "
          f"{morning_end.strftime('%Y-%m-%d %H:%M')} UTC  (5:00–10:30 AM ET)")
    print(f"[DAILY] Evening window: {evening_start.strftime('%Y-%m-%d %H:%M')} – "
          f"{evening_end.strftime('%Y-%m-%d %H:%M')} UTC  (3:00–8:30 PM ET)")

    return yesterday_et, morning_start, morning_end, evening_start, evening_end


# ── Processing ────────────────────────────────────────────────────────────────

def interpret_window(raw_delays):
    """
    Run Claude Haiku interpretation on each raw delay post.
    Returns a list of interpreted dicts (system-wide events skip interpretation).
    """
    interpreted = []
    for raw in raw_delays:
        # System-wide Penn Station alert — skip interpreter, already structured
        if raw.get("system_wide") and not raw.get("line_suspension"):
            interpreted.append(raw)
            continue

        # Line-wide suspension — skip interpreter
        if raw.get("line_suspension"):
            interpreted.append(raw)
            continue

        # Normal per-train alert
        try:
            result = interpret_alert(
                raw["text"],
                delay_minutes_hint=raw.get("delay_minutes")
            )
        except Exception as e:
            print(f"[DAILY] Interpreter error: {e}")
            continue

        if result is None:
            # Interpreter returned null — but if the watcher already identified
            # the line and delay, don't silently drop it. Build a minimal event
            # from the watcher's data so RVL/Hoboken alerts aren't lost.
            #
            # Backstop: first check the raw text for resolution/restoration
            # language. If Haiku returned null because the alert is saying
            # "service has resumed" or "trains are back on schedule," we should
            # trust that null and drop the event — not resurrect it from
            # watcher data that may have extracted a stale delay figure.
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
            raw_text = raw.get("text", "").lower()
            is_resolution = any(p in raw_text for p in RESOLUTION_PHRASES)
            if is_resolution:
                print(f"[DAILY] Interpreter null + resolution language detected — correctly dropping.")
                continue

            watcher_line = raw.get("line", "Unknown")
            watcher_delay = raw.get("delay_minutes")
            if watcher_line != "Unknown" and watcher_delay and watcher_delay >= 10:
                print(f"[DAILY] Interpreter returned null — using watcher fallback: "
                      f"{watcher_line} | {watcher_delay} min")
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
    """
    Calculate costs for a deduplicated list of delay events.
    Returns the same list with dollar_estimate and estimated_riders filled in.
    """
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
            print(f"[DAILY] Calculator error: {e}")
            traceback.print_exc()

    return calculated


def process_window(label, start_utc, end_utc):
    """
    Full pipeline for one time window:
      fetch → interpret → deduplicate → calculate costs

    Returns a list of fully calculated delay dicts.
    """
    print(f"\n[DAILY] ── {label.upper()} WINDOW ──────────────────────────────")

    # 1. Fetch
    try:
        raw = get_window_delays(start_utc, end_utc,
                                min_delay_minutes=MIN_DELAY_MINUTES)
    except Exception as e:
        print(f"[DAILY] Fetch failed: {e}")
        traceback.print_exc()
        return [], 0

    if not raw:
        print(f"[DAILY] No qualifying posts in {label} window.")
        return [], 0

    raw_count = len(raw)
    print(f"[DAILY] {raw_count} raw posts fetched.")

    # 2. Interpret
    interp = interpret_window(raw)
    print(f"[DAILY] {len(interp)} posts after interpretation.")

    # 2b. Log all interpreted alerts before dedup (for hand-checking)
    # Imported here to avoid circular imports at module level
    import os as _os
    if _os.environ.get("DRY_RUN", "false").lower() != "true":
        try:
            from logger import log_alert_batch
            log_alert_batch(interp)
        except Exception as _e:
            print(f"[DAILY] Alert Log write failed: {_e}")

    # 3. Deduplicate
    deduped = deduplicate_by_train(interp)
    print(f"[DAILY] {len(deduped)} events after deduplication.")

    # 4. Calculate costs
    calculated = calculate_window(deduped)
    print(f"[DAILY] {len(calculated)} events with costs calculated.")

    # Log each deduplicated event for the GitHub Actions log
    for ev in calculated:
        train = ev.get("train_number") or "—"
        line  = ev.get("line", "Unknown")
        mins  = ev.get("delay_minutes") or "?"
        riders = ev.get("estimated_riders") or "?"
        cost  = ev.get("dollar_estimate") or 0
        print(f"[DAILY]   {line} | train #{train} | {mins} min | ~{riders} riders | ${cost:,.2f}")

    return calculated, raw_count


# ── Tweet formatting ──────────────────────────────────────────────────────────

def format_tweet(yesterday_et, totals):
    """
    Format the daily summary tweet.

    Normal day:
      On Monday, NJ Transit delayed commuters for a total of 8,450 person-hours
      across both rush hours. City employers lost $1.2M in productive working
      time. (64 delay events across 8 lines)

    No delays:
      Good news! Yesterday (Monday), NJ Transit commuter rail ran on time
      with no significant delays reported. 🚂
    """
    day_name = yesterday_et.strftime("%A")

    if totals["event_count"] == 0:
        return (
            f"Good news! Yesterday ({day_name}), NJ Transit commuter rail "
            f"ran on time with no significant delays reported. 🚂"
        )

    person_minutes = totals["total_person_minutes"]
    cost           = totals["total_cost"]
    event_count    = totals["event_count"]
    line_count     = len(totals["lines_affected"])

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

    footer = (
        f"(Estimate based on {event_count} delay event{'s' if event_count != 1 else ''})"
    )

    post = (
        f"On {day_name}, NJ Transit delayed commuters for a total of "
        f"{time_str} across the morning and afternoon rush hours. "
        f"City employers lost out on working time conservatively valued at {cost_str}. "
        f"{footer}"
    )

    if len(post) > 295:
        post = post[:292] + "..."

    return post


def post_to_bluesky(text):
    """Post to Bluesky. Returns URI or None."""
    from atproto import Client
    handle   = os.environ.get("BLUESKY_HANDLE")
    password = os.environ.get("BLUESKY_PASSWORD")
    if not handle or not password:
        print("[DAILY] Bluesky credentials not set.")
        return None
    try:
        client = Client()
        client.login(handle, password)
        response = client.send_post(text)
        print(f"[DAILY] Posted: {response.uri}")
        return response.uri
    except Exception as e:
        print(f"[DAILY] Failed to post: {e}")
        return None


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    print(f"\n{'='*60}")
    print(f"NJT DAILY PIPELINE — {datetime.now(ET).strftime('%Y-%m-%d %H:%M:%S ET')}")
    if DRY_RUN:
        print("*** DRY RUN — no Sheets writes, no tweet ***")
    print(f"{'='*60}")

    # Determine windows
    yesterday_et, morn_start, morn_end, eve_start, eve_end = get_yesterday_windows()

    # ── Clear logs (fresh slate for this run) ────────────────────────────────
    if not DRY_RUN:
        try:
            clear_run_log()
        except Exception as e:
            print(f"[DAILY] Could not clear Run Log: {e}")
        try:
            clear_alert_log()
        except Exception as e:
            print(f"[DAILY] Could not clear Alert Log: {e}")

    # ── Morning window ────────────────────────────────────────────────────────
    morning_events, _morning_raw_count = process_window("morning", morn_start, morn_end)
    morning_totals = calculate_totals(morning_events)

    if not DRY_RUN:
        try:
            log_delay_batch(morning_events)
        except Exception as e:
            print(f"[DAILY] Sheet log failed (morning batch): {e}")
        try:
            log_run("morning",
                    raw_count=_morning_raw_count,
                    dedup_count=len(morning_events),
                    total_cost=morning_totals["total_cost"])
        except Exception as e:
            print(f"[DAILY] Run Log write failed (morning): {e}")
    else:
        print(f"[DAILY] DRY RUN: would log {len(morning_events)} morning events to Sheets.")

    # ── Evening window ────────────────────────────────────────────────────────
    evening_events, _evening_raw_count = process_window("evening", eve_start, eve_end)
    evening_totals = calculate_totals(evening_events)

    if not DRY_RUN:
        try:
            log_delay_batch(evening_events)
        except Exception as e:
            print(f"[DAILY] Sheet log failed (evening batch): {e}")
        try:
            log_run("evening",
                    raw_count=_evening_raw_count,
                    dedup_count=len(evening_events),
                    total_cost=evening_totals["total_cost"])
        except Exception as e:
            print(f"[DAILY] Run Log write failed (evening): {e}")
    else:
        print(f"[DAILY] DRY RUN: would log {len(evening_events)} evening events to Sheets.")

    # ── Combine and summarise ─────────────────────────────────────────────────
    all_events = morning_events + evening_events
    totals = calculate_totals(all_events)

    print(f"\n[DAILY] ── TOTALS ───────────────────────────────────────────────")
    print(f"[DAILY] Morning: {len(morning_events)} events")
    print(f"[DAILY] Evening: {len(evening_events)} events")
    print(f"[DAILY] Combined: {totals['event_count']} events | "
          f"{totals['total_person_minutes']:,} person-min | "
          f"${totals['total_cost']:,.2f}")

    # ── Tweet ─────────────────────────────────────────────────────────────────
    tweet_text = format_tweet(yesterday_et, totals)

    print(f"\n[DAILY] Tweet preview:\n{'-'*40}\n{tweet_text}\n{'-'*40}")
    print(f"[DAILY] Character count: {len(tweet_text)}")

    if not DRY_RUN:
        uri = post_to_bluesky(tweet_text)
        now = datetime.now(ET)
        post_date = now.strftime("%Y-%m-%d")
        post_time = now.strftime("%H:%M:%S")

        # Log tweet to Tweet_log tab
        try:
            log_tweet(
                text=tweet_text,
                total_cost=totals["total_cost"],
                event_count=totals["event_count"],
                uri=uri,
                person_hours=totals["total_person_hours"],
                morning_cost=morning_totals["total_cost"],
                evening_cost=evening_totals["total_cost"],
            )
        except Exception as e:
            print(f"[DAILY] Tweet_log write failed: {e}")

        # Update Run Log rows with post details
        try:
            log_run_summary(post_date, post_time, uri)
        except Exception as e:
            print(f"[DAILY] Run Log summary update failed: {e}")
    else:
        print("[DAILY] DRY RUN: not posting.")

    print(f"\n[DAILY] Done.\n")


if __name__ == "__main__":
    run()
