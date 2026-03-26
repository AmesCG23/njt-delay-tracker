"""
aggregator.py — Rush Hour Summarizer
--------------------------------------
Reads the delay log for a given time window, deduplicates by train number
(keeping the highest observed delay per train per day), calculates aggregate
totals, and formats the twice-daily summary Bluesky post.

Deduplication rule:
  If train #3876 appears three times (15 min, 25 min, 25 min),
  count it once at 25 minutes — the worst observed figure.
  This gives a fair picture of actual commuter impact.

Totals:
  - total_person_minutes: sum of (delay_minutes × riders) per deduplicated train
  - total_cost: sum of recalculated dollar_estimate per deduplicated train
"""

from staging import get_window_delays, clear_window
from calculator import VTTS_RATE

# ── Rush hour window definitions (Eastern Time, 24h) ─────────────────────────
WINDOWS = {
    "morning": {
        "start_hour": 5,
        "end_hour": 11,       # 5:00am – 10:59am ET
        "label": "morning rush",
        "greeting": "Good morning",
        "period_label": "morning",
    },
    "evening": {
        "start_hour": 15,
        "end_hour": 22,       # 3:00pm – 9:59pm ET
        "label": "evening rush",
        "greeting": "Good evening",
        "period_label": "afternoon",
    },
}


def deduplicate_by_train(delays):
    """
    Given a list of delay entries, keep only the highest observed delay
    per (train_number, date) pair.

    For delays without a train number, treat each as its own unique event
    (we can't deduplicate what we can't identify).

    Returns a list of deduplicated delay dicts.
    """
    from datetime import datetime, timezone, timedelta

    # Group by (train_number, date_string)
    best = {}   # key → entry with highest delay_minutes
    no_train = []  # entries with no train number — keep all

    for entry in delays:
        train = entry.get("train_number")
        if not train:
            no_train.append(entry)
            continue

        try:
            ts = datetime.fromisoformat(entry["timestamp"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            # Use ET date
            ts_et = ts - timedelta(hours=4)
            date_str = ts_et.strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            date_str = "unknown"

        key = (str(train).strip(), date_str)
        existing = best.get(key)

        this_mins = entry.get("delay_minutes") or 0
        existing_mins = existing.get("delay_minutes") or 0 if existing else 0

        if existing is None or this_mins > existing_mins:
            best[key] = entry

    result = list(best.values()) + no_train
    deduped_count = len(delays) - len(result)
    if deduped_count > 0:
        print(f"[AGGREGATOR] Deduplicated {deduped_count} repeat update(s), keeping highest per train.")

    return result


def calculate_totals(deduplicated_delays):
    """
    Calculate aggregate figures from a list of deduplicated delay entries.

    Returns a dict with:
      - event_count:          number of unique delay events
      - total_person_minutes: sum of (delay_minutes × riders)
      - total_person_hours:   total_person_minutes / 60, rounded
      - total_cost:           sum of recalculated dollar estimates
      - lines_affected:       sorted list of unique line names
    """
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
        riders = entry.get("riders") or 0
        line = entry.get("line", "Unknown")

        person_minutes = delay_mins * riders
        cost = riders * (delay_mins / 60) * VTTS_RATE

        total_person_minutes += person_minutes
        total_cost += cost
        if line != "Unknown":
            lines.add(line)

    return {
        "event_count": len(deduplicated_delays),
        "total_person_minutes": total_person_minutes,
        "total_person_hours": round(total_person_minutes / 60),
        "total_cost": round(total_cost, 2),
        "lines_affected": sorted(lines),
    }


def format_summary_post(period, totals):
    """
    Format the twice-daily summary Bluesky post.

    Example output:
      Good morning, fellow commuters! Today, NJ Transit delayed us
      for a total of 14,300 person-minutes during the morning rush.
      City employers lost $9,867 in productive working time.
      (12 delay events across 4 lines)
    """
    window = WINDOWS[period]
    greeting = window["greeting"]
    period_label = window["period_label"]

    person_minutes = totals["total_person_minutes"]
    cost = totals["total_cost"]
    event_count = totals["event_count"]
    line_count = len(totals["lines_affected"])

    # Format person-minutes — use hours if over 1000
    if person_minutes >= 60_000:
        time_str = f"{totals['total_person_hours']:,} person-hours"
    else:
        time_str = f"{person_minutes:,} person-minutes"

    # Format cost
    if cost >= 1_000_000:
        cost_str = f"${cost / 1_000_000:.1f}M"
    else:
        cost_str = f"${cost:,.0f}"

    # Footer line
    events_str = f"{event_count} delay event{'s' if event_count != 1 else ''}"
    lines_str = f"{line_count} line{'s' if line_count != 1 else ''}"
    footer = f"({events_str} across {lines_str})"

    post = (
        f"{greeting}, fellow commuters! Today, NJ Transit delayed us "
        f"for a total of {time_str} during the {period_label} rush. "
        f"City employers lost {cost_str} in productive working time. "
        f"{footer}"
    )

    # Safety truncation
    if len(post) > 295:
        post = post[:292] + "..."

    return post


def run_summary(period, dry_run=False):
    """
    Full summary pipeline for one period ("morning" or "evening"):
      1. Load delays from the staging log for this window
      2. Deduplicate by train
      3. Calculate totals
      4. Format and return the post text (and totals for logging)
      5. Clear the window from the staging log (unless dry_run)

    Returns (post_text, totals) or (None, None) if no delays found.
    """
    if period not in WINDOWS:
        raise ValueError(f"Unknown period '{period}'. Use 'morning' or 'evening'.")

    window = WINDOWS[period]
    start = window["start_hour"]
    end = window["end_hour"]

    print(f"[AGGREGATOR] Summarizing {period} rush ({start}h–{end}h ET)...")

    raw_delays = get_window_delays(start, end)
    print(f"[AGGREGATOR] Found {len(raw_delays)} raw delay entries in window.")

    if not raw_delays:
        print(f"[AGGREGATOR] No delays in {period} window — skipping post.")
        return None, None

    deduplicated = deduplicate_by_train(raw_delays)
    totals = calculate_totals(deduplicated)

    print(f"[AGGREGATOR] After dedup: {totals['event_count']} events | "
          f"{totals['total_person_minutes']:,} person-min | "
          f"${totals['total_cost']:,.2f}")

    post_text = format_summary_post(period, totals)

    if not dry_run:
        clear_window(start, end)

    return post_text, totals


# ── Run standalone for testing ────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    period = sys.argv[1] if len(sys.argv) > 1 else "morning"
    post, totals = run_summary(period, dry_run=True)  # dry_run so we don't clear the log

    if post:
        print(f"\n=== SUMMARY POST PREVIEW ({period.upper()}) ===")
        print(post)
        print(f"\nCharacter count: {len(post)}")
    else:
        print(f"\nNo {period} delays to summarize.")
