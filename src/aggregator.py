"""
aggregator.py — Deduplicator and Summarizer
--------------------------------------------
Takes a list of calculated delay events, deduplicates by train number
(keeping highest observed delay per train per day), calculates totals,
and formats the summary post.

No file I/O. Operates purely on the list passed in.
"""

from calculator import VTTS_RATE

# ── Rush window definitions ───────────────────────────────────────────────────
WINDOWS = {
    "morning": {
        "greeting":     "Good morning",
        "period_label": "morning",
        # Fixed UTC boundaries — morning cron fires at 15:00 UTC, same day
        # 09:00–14:30 UTC = 5:00am–10:30am EDT = 4:00am–9:30am EST
        "start_utc_hour":   9,
        "start_utc_minute": 0,
        "end_utc_hour":     14,
        "end_utc_minute":   30,
        "day_offset":       0,   # same UTC day as when the job fires
    },
    "evening": {
        "greeting":     "Good evening",
        "period_label": "evening",
        # Fixed UTC boundaries — evening cron fires at 02:00 UTC, next calendar day
        # 19:00 UTC (prev day) – 01:00 UTC (today) = 3:00pm–9:00pm EDT = 2:00pm–8:00pm EST
        "start_utc_hour":   19,
        "start_utc_minute": 0,
        "end_utc_hour":     1,
        "end_utc_minute":   0,
        "day_offset":       -1,  # start is on the previous UTC day
    },
}


def get_utc_window(period):
    """
    Return (window_start_utc, window_end_utc) for the given period.

    Uses FIXED UTC boundaries anchored to the cron schedule — NOT the
    current clock time. This means GitHub Actions delays of any length
    won't shift the window.

    Morning:  09:00–14:30 UTC on today's UTC date
    Evening:  19:00 UTC yesterday – 01:00 UTC today
    """
    from datetime import datetime, timezone, timedelta

    w = WINDOWS[period]
    now_utc = datetime.now(timezone.utc)
    utc_date = now_utc.date()

    if period == "morning":
        start_date = utc_date
        end_date   = utc_date
    else:
        # Evening job is scheduled at 23:59 UTC (same day as rush).
        # If GitHub fires it on time:  hour=23 → start=today, end=tomorrow
        # If GitHub delays past midnight: hour=0-11 → start=yesterday, end=today
        # Either way the window is 19:00 UTC rush-day → 01:00 UTC next-day.
        if now_utc.hour >= 12:
            start_date = utc_date
            end_date   = utc_date + timedelta(days=1)
        else:
            start_date = utc_date - timedelta(days=1)
            end_date   = utc_date

    start_utc = datetime(
        start_date.year, start_date.month, start_date.day,
        w["start_utc_hour"], w["start_utc_minute"], 0,
        tzinfo=timezone.utc
    )
    end_utc = datetime(
        end_date.year, end_date.month, end_date.day,
        w["end_utc_hour"], w["end_utc_minute"], 0,
        tzinfo=timezone.utc
    )

    print(f"[AGGREGATOR] {period.upper()} window: "
          f"{start_utc.strftime('%Y-%m-%d %H:%M')} – "
          f"{end_utc.strftime('%Y-%m-%d %H:%M')} UTC")

    return start_utc, end_utc


def deduplicate_by_train(delays):
    """
    Keep only the highest observed delay per event key:

    - Normal events:       keyed on (train_number, date) — highest delay wins
    - System-wide events:  keyed on (line, date) — highest cost wins
      This prevents the same Penn Station or line-suspension alert from
      being counted multiple times if it escalates or fires repeatedly.
    - Events with no train number and not system-wide: kept as-is
    """
    from datetime import datetime, timezone, timedelta
    from zoneinfo import ZoneInfo

    best_trains = {}    # (train_number, date) → entry
    best_syswide = {}   # (line, date) → entry  for system-wide events
    no_id = []          # no train number, not system-wide

    for entry in delays:
        train = entry.get("train_number")
        is_system = entry.get("system_wide", False)

        try:
            ts_str = entry.get("timestamp", "")
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            date_str = ts.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        except (ValueError, TypeError, AttributeError):
            date_str = "unknown"

        if is_system:
            # Dedup system-wide events by (line, date), keep highest cost
            line = entry.get("line", "Unknown")
            key = (line, date_str)
            this_cost = entry.get("dollar_estimate") or 0
            existing_cost = (best_syswide[key].get("dollar_estimate") or 0) if key in best_syswide else 0
            if key not in best_syswide or this_cost > existing_cost:
                best_syswide[key] = entry

        elif train:
            # Normal per-train dedup: keep highest delay
            key = (str(train).strip(), date_str)
            this_mins = entry.get("delay_minutes") or 0
            existing_mins = (best_trains[key].get("delay_minutes") or 0) if key in best_trains else 0
            if key not in best_trains or this_mins > existing_mins:
                best_trains[key] = entry

        else:
            no_id.append(entry)

    result = list(best_trains.values()) + list(best_syswide.values()) + no_id
    deduped = len(delays) - len(result)
    if deduped:
        print(f"[AGGREGATOR] Deduplicated {deduped} repeat update(s), keeping highest per train/event.")
    return result


def calculate_totals(deduplicated_delays):
    """Calculate aggregate person-minutes and cost across all events."""
    if not deduplicated_delays:
        return {
            "event_count": 0,
            "total_person_minutes": 0,
            "total_person_hours": 0,
            "total_cost": 0.0,
            "lines_affected": [],
        }

    total_person_minutes = 0
    total_cost = 0.0
    lines = set()

    for entry in deduplicated_delays:
        delay_mins = entry.get("delay_minutes") or 0
        riders     = entry.get("estimated_riders") or 0
        line       = entry.get("line", "Unknown")

        total_person_minutes += delay_mins * riders
        total_cost           += riders * (delay_mins / 60) * VTTS_RATE
        if line not in ("Unknown", "System-Wide (Penn Station)"):
            lines.add(line)

    return {
        "event_count":          len(deduplicated_delays),
        "total_person_minutes": total_person_minutes,
        "total_person_hours":   round(total_person_minutes / 60),
        "total_cost":           round(total_cost, 2),
        "lines_affected":       sorted(lines),
    }


def format_summary_post(period, totals):
    """Format the twice-daily Bluesky summary post."""
    w = WINDOWS[period]

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
        f"${cost / 1_000_000:.1f}M" if cost >= 1_000_000
        else f"${cost:,.0f}"
    )

    footer = (
        f"({event_count} delay event{'s' if event_count != 1 else ''} "
        f"across {line_count} line{'s' if line_count != 1 else ''})"
    )

    post = (
        f"{w['greeting']}, fellow commuters! Today, NJ Transit delayed us "
        f"for a total of {time_str} during the {w['period_label']} rush. "
        f"City employers lost {cost_str} in productive working time. "
        f"{footer}"
    )

    if len(post) > 295:
        post = post[:292] + "..."

    return post
