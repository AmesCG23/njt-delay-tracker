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
# Hours in ET (24h). Used to calculate the UTC window for Bluesky fetching.
WINDOWS = {
    "morning": {
        "start_hour_et": 5,
        "end_hour_et":   10,
        "end_minute_et": 30,
        "greeting":      "Good morning",
        "period_label":  "morning",
    },
    "evening": {
        "start_hour_et": 15,
        "end_hour_et":   21,
        "end_minute_et": 0,
        "greeting":      "Good evening",
        "period_label":  "afternoon",
    },
}

# ET offset. We use UTC-4 (EDT) as the base; in EST the posts will fire
# an hour early but the UTC window calculation below accounts for both.
ET_UTC_OFFSET_EDT = 4   # EDT = UTC-4
ET_UTC_OFFSET_EST = 5   # EST = UTC-5


def get_utc_window(period):
    """
    Return (window_start_utc, window_end_utc) for the given period.
    Uses the current UTC time to determine whether EDT or EST is in effect,
    by checking the US DST rules (second Sunday in March through first Sunday
    in November).
    """
    from datetime import datetime, timezone, timedelta

    w = WINDOWS[period]

    # Determine current ET offset using Python's DST-aware approach
    now_utc = datetime.now(timezone.utc)

    # Use the America/New_York timezone via timedelta approximation:
    # DST starts second Sunday of March, ends first Sunday of November.
    # Python's calendar module makes this easy.
    import calendar

    year = now_utc.year

    # Second Sunday in March
    march_days = [d for d in range(1, 32)
                  if datetime(year, 3, d).weekday() == 6]
    dst_start = datetime(year, 3, march_days[1], 2, 0, 0, tzinfo=timezone.utc) + timedelta(hours=5)

    # First Sunday in November
    nov_days = [d for d in range(1, 8)
                if datetime(year, 11, d).weekday() == 6]
    dst_end = datetime(year, 11, nov_days[0], 2, 0, 0, tzinfo=timezone.utc) + timedelta(hours=6)

    is_edt = dst_start <= now_utc < dst_end
    offset = ET_UTC_OFFSET_EDT if is_edt else ET_UTC_OFFSET_EST

    print(f"[AGGREGATOR] Timezone: {'EDT' if is_edt else 'EST'} (UTC-{offset})")

    # Build the UTC window
    today = (now_utc - timedelta(hours=offset)).date()
    start_et_naive = datetime(today.year, today.month, today.day,
                               w["start_hour_et"], 0, 0)
    end_et_naive   = datetime(today.year, today.month, today.day,
                               w["end_hour_et"], w["end_minute_et"], 0)

    start_utc = start_et_naive.replace(tzinfo=timezone.utc) + timedelta(hours=offset)
    end_utc   = end_et_naive.replace(tzinfo=timezone.utc)   + timedelta(hours=offset)

    print(f"[AGGREGATOR] Window: {start_utc.strftime('%H:%M')}–{end_utc.strftime('%H:%M')} UTC "
          f"({w['start_hour_et']}:00–{w['end_hour_et']}:{w['end_minute_et']:02d} ET)")

    return start_utc, end_utc


def deduplicate_by_train(delays):
    """
    Keep only the highest observed delay per (train_number, date) pair.
    Trains without a number are kept as-is.
    """
    from datetime import datetime, timezone, timedelta

    best = {}
    no_train = []

    for entry in delays:
        train = entry.get("train_number")
        if not train:
            no_train.append(entry)
            continue

        try:
            ts = datetime.fromisoformat(
                entry["timestamp"].replace("Z", "+00:00")
            )
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            date_str = (ts - timedelta(hours=4)).strftime("%Y-%m-%d")
        except (ValueError, TypeError, AttributeError):
            date_str = "unknown"

        key = (str(train).strip(), date_str)
        this_mins = entry.get("delay_minutes") or 0
        existing_mins = (best[key].get("delay_minutes") or 0) if key in best else 0

        if key not in best or this_mins > existing_mins:
            best[key] = entry

    result = list(best.values()) + no_train
    deduped = len(delays) - len(result)
    if deduped:
        print(f"[AGGREGATOR] Deduplicated {deduped} repeat update(s), keeping highest per train.")
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
        if line != "Unknown":
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
