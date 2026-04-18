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

Windows (EDT = UTC-4):
  Morning: 5:00am–12:00pm ET = 09:00–16:00 UTC
  Evening: 3:00pm–9:00pm ET  = 19:00–01:00 UTC (crosses midnight)
"""

import sys
import os
import traceback
from datetime import datetime, timezone, timedelta, date

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


# ── Window helpers ────────────────────────────────────────────────────────────

def get_yesterday_windows():
    """
    Return yesterday's morning and evening UTC windows.

    At 19:00 UTC (3pm EDT), the ET date equals the UTC date.
    Yesterday ET = UTC date - 1. The evening window ends at 01:00 UTC
    on the day after yesterday, which is in the past by the time we run.
    """
    now_utc = datetime.now(timezone.utc)

    # ET date at run time (EDT = UTC-4)
    et_date_now = (now_utc - timedelta(hours=4)).date()
    yesterday_et = et_date_now - timedelta(days=1)

    y = yesterday_et
    y_next = yesterday_et + timedelta(days=1)

    morning_start = datetime(y.year,      y.month,      y.day,      9,  0, tzinfo=timezone.utc)
    morning_end   = datetime(y.year,      y.month,      y.day,      16, 0, tzinfo=timezone.utc)
    evening_start = datetime(y.year,      y.month,      y.day,      19, 0, tzinfo=timezone.utc)
    evening_end   = datetime(y_next.year, y_next.month, y_next.day, 1,  0, tzinfo=timezone.utc)

    print(f"[DAILY] Yesterday (ET): {yesterday_et}")
    print(f"[DAILY] Morning window: {morning_start.strftime('%Y-%m-%d %H:%M')} – "
          f"{morning_end.strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"[DAILY] Evening window: {evening_start.strftime('%Y-%m-%d %H:%M')} – "
          f"{evening_end.strftime('%Y-%m-%d %H:%M')} UTC")

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
        f"({event_count} delay event{'s' if event_count != 1 else ''} "
        f"across {line_count} line{'s' if line_count != 1 else ''})"
    )

    post = (
        f"On {day_name}, NJ Transit delayed commuters for a total of "
        f"{time_str} across both rush hours. "
        f"City employers lost {cost_str} in productive working time. "
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
    print(f"NJT DAILY PIPELINE — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
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

    if not DRY_RUN:
        try:
            log_delay_batch(morning_events)
        except Exception as e:
            print(f"[DAILY] Sheet log failed (morning batch): {e}")
        morning_totals = calculate_totals(morning_events)
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

    if not DRY_RUN:
        try:
            log_delay_batch(evening_events)
        except Exception as e:
            print(f"[DAILY] Sheet log failed (evening batch): {e}")
        evening_totals = calculate_totals(evening_events)
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
        now = datetime.now()
        post_date = now.strftime("%Y-%m-%d")
        post_time = now.strftime("%H:%M:%S")

        # Log tweet to Tweet_log tab
        try:
            log_tweet(
                text=tweet_text,
                total_cost=totals["total_cost"],
                event_count=totals["event_count"],
                uri=uri,
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
