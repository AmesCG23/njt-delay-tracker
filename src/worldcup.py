"""
worldcup.py — World Cup Game-Day Pipeline
------------------------------------------
Runs once after each FIFA World Cup 2026 match at MetLife Stadium.

Covers a single 12-hour window: kickoff − 3 h → kickoff + 9 h.
Results are written to separate Google Sheet tabs (WC_Event_Log,
WC_Tweet_log) and do NOT affect the website's running total or the
daily Event Log / Tweet_log tabs.

Scheduled crons in worldcup.yml fire 30 minutes after each window
closes. For manual reruns or knockout rounds, set OVERRIDE_DATE,
OVERRIDE_KICKOFF, and OVERRIDE_LABEL environment variables.
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
from logger import log_delay_batch_worldcup, log_worldcup_tweet

DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
MIN_DELAY_MINUTES = 10

# How long after a game window closes the scheduled run can still detect it.
# GitHub Actions cron is best-effort: every group-stage run in June 2026
# started 2h49m–4h49m late, which silently missed all six games under the
# original 2-hour grace. MetLife games are at least 48 hours apart, so a
# 12-hour lookback can never match more than one game.
DETECTION_GRACE = timedelta(hours=12)

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

# All FIFA World Cup 2026 matches at MetLife Stadium (East Rutherford, NJ).
# kickoff_et: HH:MM in 24-hour Eastern Time.
# label: used in the Bluesky post. Update knockout-round labels once the
# bracket is set — or pass OVERRIDE_LABEL at dispatch time.
METLIFE_GAMES = [
    {"date": "2026-06-13", "kickoff_et": "18:00", "label": "Brazil vs Morocco (Group C)"},
    {"date": "2026-06-16", "kickoff_et": "15:00", "label": "France vs Senegal (Group I)"},
    {"date": "2026-06-22", "kickoff_et": "20:00", "label": "Norway vs Senegal (Group I)"},
    {"date": "2026-06-25", "kickoff_et": "16:00", "label": "Ecuador vs Germany (Group E)"},
    {"date": "2026-06-27", "kickoff_et": "17:00", "label": "Panama vs England (Group L)"},
    {"date": "2026-06-30", "kickoff_et": "17:00", "label": "Round of 32"},
    {"date": "2026-07-05", "kickoff_et": "16:00", "label": "Round of 16"},
    {"date": "2026-07-19", "kickoff_et": "15:00", "label": "Final"},
]


# ── Window helpers ─────────────────────────────────────────────────────────────

def get_game_window(game_date_str, kickoff_et_str):
    """
    Return UTC window for a game: kickoff − 3 h through kickoff + 9 h.

    Uses ZoneInfo("America/New_York") so EDT/EST is handled automatically.
    """
    game_date = date.fromisoformat(game_date_str)
    hour, minute = map(int, kickoff_et_str.split(":"))
    kickoff_et = datetime(game_date.year, game_date.month, game_date.day,
                          hour, minute, tzinfo=ET)
    kickoff_utc = kickoff_et.astimezone(timezone.utc)
    return kickoff_utc - timedelta(hours=3), kickoff_utc + timedelta(hours=9)


def find_active_game():
    """
    For scheduled runs: return the game whose window closed within the last
    DETECTION_GRACE (12 hours).

    The crons in worldcup.yml are timed 30 minutes after each window closes,
    but GitHub queue delays of several hours are normal — the wide grace
    absorbs them. Games are far enough apart that at most one window can
    have closed within the grace period.
    Returns None if no match is found — the script will exit cleanly.
    """
    now_utc = datetime.now(timezone.utc)
    for game in METLIFE_GAMES:
        _, window_end = get_game_window(game["date"], game["kickoff_et"])
        age = now_utc - window_end
        if timedelta(0) <= age <= DETECTION_GRACE:
            return game
    return None


# ── Processing ─────────────────────────────────────────────────────────────────

def interpret_window(raw_delays):
    """
    Run Claude Haiku interpretation on each raw Bluesky post.
    Mirrors the logic in daily.py — system-wide events skip interpretation,
    and the watcher-fallback prevents RVL / Hoboken alerts from being dropped.
    """
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
            print(f"[WC] Interpreter error: {e}")
            continue

        if result is None:
            raw_text = raw.get("text", "").lower()
            if any(p in raw_text for p in RESOLUTION_PHRASES):
                continue
            watcher_line = raw.get("line", "Unknown")
            watcher_delay = raw.get("delay_minutes")
            if watcher_line != "Unknown" and watcher_delay and watcher_delay >= 10:
                print(f"[WC] Interpreter null — watcher fallback: {watcher_line} | {watcher_delay} min")
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
    """Route each event to the correct calculator. Mirrors daily.py logic."""
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
            print(f"[WC] Calculator error: {e}")
            traceback.print_exc()
    return calculated


def process_window(start_utc, end_utc):
    """Full pipeline: fetch → interpret → deduplicate → calculate."""
    print(f"\n[WC] ── GAME WINDOW ──────────────────────────────────────────")
    print(f"[WC] {start_utc.strftime('%Y-%m-%d %H:%M')} – "
          f"{end_utc.strftime('%Y-%m-%d %H:%M')} UTC")

    try:
        raw = get_window_delays(start_utc, end_utc, min_delay_minutes=MIN_DELAY_MINUTES)
    except Exception as e:
        print(f"[WC] Fetch failed: {e}")
        traceback.print_exc()
        return [], 0

    raw_count = len(raw)
    if not raw:
        print("[WC] No qualifying posts in window.")
        return [], 0
    print(f"[WC] {raw_count} raw posts fetched.")

    interp = interpret_window(raw)
    print(f"[WC] {len(interp)} after interpretation.")

    deduped = deduplicate_by_train(interp)
    print(f"[WC] {len(deduped)} after deduplication.")

    calculated = calculate_window(deduped)
    print(f"[WC] {len(calculated)} events with costs calculated.")

    for ev in calculated:
        train  = ev.get("train_number") or "—"
        line   = ev.get("line", "Unknown")
        mins   = ev.get("delay_minutes") or "?"
        riders = ev.get("estimated_riders") or "?"
        cost   = ev.get("dollar_estimate") or 0
        print(f"[WC]   {line} | train #{train} | {mins} min | ~{riders} riders | ${cost:,.2f}")

    return calculated, raw_count


# ── Tweet formatting ───────────────────────────────────────────────────────────

def format_worldcup_tweet(game_label, game_date_str, totals):
    """
    Format the game-day summary post.

    Delays found:
      Around Saturday's World Cup match at MetLife (Brazil vs Morocco), NJ
      Transit delayed commuters for 4,200 person-hours. That's $184,800 in
      lost productive time. (12 delay events)

    No delays:
      NJ Transit ran smoothly around Saturday's World Cup match at MetLife
      (Brazil vs Morocco). No qualifying delays in the 12-hour match window.
    """
    game_date = date.fromisoformat(game_date_str)
    day_name = game_date.strftime("%A")

    if totals["event_count"] == 0:
        text = (
            f"NJ Transit ran smoothly around {day_name}'s World Cup match "
            f"at MetLife ({game_label}). No qualifying delays in the "
            f"12-hour match window."
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
            f"Around {day_name}'s World Cup match at MetLife ({game_label}), "
            f"NJ Transit delayed commuters for {time_str}. "
            f"That's {cost_str} in lost productive time. "
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
        print("[WC] Bluesky credentials not set.")
        return None
    try:
        client = Client()
        client.login(handle, password)
        response = client.send_post(text)
        print(f"[WC] Posted: {response.uri}")
        return response.uri
    except Exception as e:
        print(f"[WC] Failed to post: {e}")
        return None


# ── Main ───────────────────────────────────────────────────────────────────────

def run():
    print(f"\n{'='*60}")
    print(f"NJT WORLD CUP PIPELINE — {datetime.now(ET).strftime('%Y-%m-%d %H:%M:%S ET')}")
    if DRY_RUN:
        print("*** DRY RUN — no Sheets writes, no tweet ***")
    print(f"{'='*60}")

    # Resolve which game to process.
    # For scheduled runs: auto-detect from METLIFE_GAMES.
    # For manual dispatch: OVERRIDE_DATE + OVERRIDE_KICKOFF take precedence.
    override_date    = os.environ.get("OVERRIDE_DATE", "").strip()
    override_kickoff = os.environ.get("OVERRIDE_KICKOFF", "").strip()
    override_label   = os.environ.get("OVERRIDE_LABEL", "").strip()

    if override_date and override_kickoff:
        game = {
            "date":       override_date,
            "kickoff_et": override_kickoff,
            "label":      override_label or f"Match on {override_date}",
        }
        print(f"[WC] Manual override: {game['label']} on {game['date']} at {game['kickoff_et']} ET")
    else:
        game = find_active_game()
        if game is None:
            grace_hours = int(DETECTION_GRACE.total_seconds() // 3600)
            print(f"[WC] No active game found within the {grace_hours}-hour detection window. Exiting.")
            print("[WC] For manual runs, set OVERRIDE_DATE, OVERRIDE_KICKOFF, OVERRIDE_LABEL.")
            return
        print(f"[WC] Auto-detected game: {game['label']} on {game['date']}")

    start_utc, end_utc = get_game_window(game["date"], game["kickoff_et"])
    print(f"[WC] Game:   {game['label']}")
    print(f"[WC] Date:   {game['date']}")
    print(f"[WC] Window: {start_utc.strftime('%Y-%m-%d %H:%M')} – "
          f"{end_utc.strftime('%Y-%m-%d %H:%M')} UTC  "
          f"(kickoff {game['kickoff_et']} ET, ±3 h / +9 h)")

    events, _raw_count = process_window(start_utc, end_utc)
    totals = calculate_totals(events)

    print(f"\n[WC] ── TOTALS ──────────────────────────────────────────────")
    print(f"[WC] {totals['event_count']} events | "
          f"{totals['total_person_minutes']:,} person-min | "
          f"${totals['total_cost']:,.2f}")

    tweet_text = format_worldcup_tweet(game["label"], game["date"], totals)
    print(f"\n[WC] Tweet preview:\n{'-'*40}\n{tweet_text}\n{'-'*40}")
    print(f"[WC] Character count: {len(tweet_text)}")

    if not DRY_RUN:
        try:
            log_delay_batch_worldcup(events, game["label"])
        except Exception as e:
            print(f"[WC] WC_Event_Log write failed: {e}")

        uri = post_to_bluesky(tweet_text)

        try:
            log_worldcup_tweet(
                text=tweet_text,
                total_cost=totals["total_cost"],
                event_count=totals["event_count"],
                uri=uri,
                person_hours=totals["total_person_hours"],
                game_label=game["label"],
                game_date=game["date"],
            )
        except Exception as e:
            print(f"[WC] WC_Tweet_log write failed: {e}")
    else:
        print(f"[WC] DRY RUN: would log {len(events)} event(s) and post tweet.")

    print(f"\n[WC] Done.\n")


if __name__ == "__main__":
    run()
