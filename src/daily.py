"""
daily.py — The Full Daily Pipeline
------------------------------------
Runs once per weekday at ~7:30–8:30am ET (12:30 UTC).

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
from composer import compose_post

DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
MIN_DELAY_MINUTES = 10

# Attach a bettertrains.org link card (with og-card.png thumbnail) to the
# daily Bluesky post. Set USE_LINK_CARD=false in daily.yml to post plain
# text again — no code change needed.
USE_LINK_CARD = os.environ.get("USE_LINK_CARD", "true").lower() == "true"

SITE_URL = "https://bettertrains.org/"
SITE_CARD_TITLE = "NJT Delay Tracker — the daily cost of late trains"
SITE_CARD_DESCRIPTION = "Running totals, charts, and methodology — updated every weekday."

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
            # Backstop: first check for resolution language. If Haiku returned
            # null because the alert says "service has resumed," trust that null
            # rather than resurrecting a stale delay figure from the watcher.
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
    if not DRY_RUN:
        try:
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

def compute_line_hours(events):
    """
    Sum person-hours by line for the per-line columns in Tweet_log (K–S).

    Penn Station system-wide events → "_penn_syswide" (column Q).
    Hoboken diversion events → "_hoboken_diversion" (column S).
    Line suspensions are attributed to their named line like normal events.
    """
    totals = {
        "Northeast Corridor":  0.0,
        "Morris & Essex":      0.0,
        "North Jersey Coast":  0.0,
        "Main/Bergen County":  0.0,
        "Raritan Valley":      0.0,
        "Montclair-Boonton":   0.0,
        "Pascack Valley":      0.0,
        "_penn_syswide":       0.0,
        "_hoboken_diversion":  0.0,
    }
    for ev in events:
        line = ev.get("line", "")
        hrs = (ev.get("estimated_riders") or 0) * (ev.get("delay_minutes") or 0) / 60
        if ev.get("system_wide", False) and not ev.get("line_suspension", False):
            if line == "System-Wide (Hoboken Diversion)":
                totals["_hoboken_diversion"] += hrs
            else:
                totals["_penn_syswide"] += hrs
        elif line in totals:
            totals[line] += hrs
    return totals


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
        f"{totals['total_person_hours']:,} hours"
        if person_minutes >= 60_000
        else f"{person_minutes:,} minutes"
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


def _build_link_card_embed(client, card_path):
    """
    Build the bettertrains.org link-card embed for the daily post.

    The thumbnail is docs/og-card.png (regenerated with the live cumulative
    total just before posting). Every failure degrades gracefully:
    thumbnail upload fails → card without a thumbnail; embed construction
    fails → caller posts plain text. The day's post is never at risk.
    """
    from atproto import models

    thumb_blob = None
    if card_path and os.path.exists(card_path):
        try:
            with open(card_path, "rb") as f:
                thumb_blob = client.upload_blob(f.read()).blob
        except Exception as e:
            print(f"[DAILY] Card thumbnail upload failed — link card without image: {e}")

    return models.AppBskyEmbedExternal.Main(
        external=models.AppBskyEmbedExternal.External(
            uri=SITE_URL,
            title=SITE_CARD_TITLE,
            description=SITE_CARD_DESCRIPTION,
            thumb=thumb_blob,
        )
    )


def post_to_bluesky(text, card_path=None):
    """
    Post to Bluesky, attaching a bettertrains.org link card when enabled.
    Returns URI or None.
    """
    from atproto import Client
    handle   = os.environ.get("BLUESKY_HANDLE")
    password = os.environ.get("BLUESKY_PASSWORD")
    if not handle or not password:
        print("[DAILY] Bluesky credentials not set.")
        return None
    try:
        client = Client()
        client.login(handle, password)

        embed = None
        if USE_LINK_CARD:
            try:
                embed = _build_link_card_embed(client, card_path)
            except Exception as e:
                print(f"[DAILY] Link card build failed — posting plain text: {e}")

        response = client.send_post(text, embed=embed)
        print(f"[DAILY] Posted: {response.uri}" + (" (with link card)" if embed else ""))
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
    #
    # ┌─ AI-COMPOSED POST — HOW TO ROLL BACK ─────────────────────────────────┐
    # │ On delay days the post is drafted by Claude (src/composer.py) from    │
    # │ the spreadsheet history, then validated. On any failure it falls back │
    # │ to the fixed template in format_tweet() just below.                   │
    # │                                                                       │
    # │ TO ROLL BACK if the composed posts go badly:                          │
    # │   • FASTEST (no code change): set USE_COMPOSER=false in               │
    # │     .github/workflows/daily.yml and re-run. Posts revert to the       │
    # │     plain template immediately.                                       │
    # │   • PERMANENT: flip USE_COMPOSER's default in composer.py, or revert  │
    # │     the PR that added it. This template path is unchanged either way. │
    # │                                                                       │
    # │ Zero-delay days always use the fixed "Good news!" template — the      │
    # │ composer is only consulted when there are delays to describe.         │
    # └───────────────────────────────────────────────────────────────────────┘
    tweet_text = None
    if totals["event_count"] > 0:
        try:
            tweet_text = compose_post(
                yesterday_et, totals, morning_totals, evening_totals, all_events
            )
        except Exception as e:
            print(f"[DAILY] Composer raised — falling back to template: {e}")
    if not tweet_text:
        tweet_text = format_tweet(yesterday_et, totals)

    print(f"\n[DAILY] Tweet preview:\n{'-'*40}\n{tweet_text}\n{'-'*40}")
    print(f"[DAILY] Character count: {len(tweet_text)}")

    if not DRY_RUN:
        # ── Social card refresh ──────────────────────────────────────────────
        # Redraw docs/og-card.png with the live cumulative total (read from
        # Totals!B2, which already includes today's Event Log rows). The
        # workflow commits the refreshed file after this script exits, so
        # GitHub Pages serves it to link scrapers. Fail-safe: on any error
        # generate_card() returns None and the committed card stays in use.
        # ⟵ ROLLBACK: USE_OG_CARD=false in daily.yml freezes the card.
        card_path = None
        try:
            from og_card import generate_card
            card_path = generate_card()
        except Exception as e:
            print(f"[DAILY] Card regeneration errored — using committed card: {e}")
        if card_path is None:
            committed = os.path.join(os.path.dirname(__file__), "..", "docs", "og-card.png")
            card_path = committed if os.path.exists(committed) else None

        uri = post_to_bluesky(tweet_text, card_path=card_path)
        now = datetime.now(ET)
        post_date = now.strftime("%Y-%m-%d")
        post_time = now.strftime("%H:%M:%S")

        # Log tweet to Tweet_log tab
        try:
            line_hours = compute_line_hours(all_events)
            log_tweet(
                text=tweet_text,
                total_cost=totals["total_cost"],
                event_count=totals["event_count"],
                uri=uri,
                person_hours=totals["total_person_hours"],
                morning_cost=morning_totals["total_cost"],
                evening_cost=evening_totals["total_cost"],
                report_date=yesterday_et.isoformat(),
                nec_hours=line_hours["Northeast Corridor"],
                me_hours=line_hours["Morris & Essex"],
                njcl_hours=line_hours["North Jersey Coast"],
                mb_hours=line_hours["Main/Bergen County"],
                rv_hours=line_hours["Raritan Valley"],
                mobo_hours=line_hours["Montclair-Boonton"],
                syswide_hours=line_hours["_penn_syswide"],
                hoboken_hours=line_hours["_hoboken_diversion"],
                pvl_hours=line_hours["Pascack Valley"],
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
