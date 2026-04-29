"""
aggregator.py — Deduplicator and Summarizer
--------------------------------------------
Takes a list of calculated delay events, deduplicates by train number
(keeping highest observed delay per train per day), calculates totals,
and formats the summary post.

No file I/O. Operates purely on the list passed in.
"""

from calculator import VTTS_RATE


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
