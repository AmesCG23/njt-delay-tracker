"""
Station 3: The Calculator
--------------------------
Takes a parsed delay dict from the Interpreter and calculates
the estimated economic cost of the delay.

Formula: riders × (delay_minutes / 60) × VTTS_RATE

Design decisions:
  - All trains use peak ridership figures. Since we only collect during
    morning and evening rush windows, every qualifying delay is a peak event.
    Time-band logic has been removed to avoid miscategorisation.
  - Cancellations are assumed to impose a 60-minute delay regardless of
    where on the line they occur. A cancellation means waiting for the next
    train, which on most lines is roughly an hour during rush hour.
"""

# ── Value of Travel Time ──────────────────────────────────────────────────────
# USDOT methodology applied to NJ median household income ($99,781, 2023 Census ACS)
# $99,781 ÷ 2,080 work hours = $47.97/hr × 50% = $23.99/hr → rounded to $24.00
VTTS_RATE = 24.00  # dollars per hour

# National USDOT default (disclosed in methodology for transparency)
VTTS_NATIONAL_DEFAULT = 18.80

# ── Riders per train (peak figures used for all events) ───────────────────────
# Built from: 62M annual rail riders (2025), per-line train frequency data,
# and RPA anchor data (63,014 daily Penn Station boardings).
# Peak figures used exclusively since all polling occurs during rush windows.
RIDERS_PER_TRAIN = {
    "Northeast Corridor": 825,
    "North Jersey Coast":  500,
    "Morris & Essex":      550,
    "Montclair-Boonton":   415,
    "Main/Bergen County":  450,
    "Raritan Valley":      450,
    "Pascack Valley":      315,
    "Port Jervis":         300,
    "Gladstone Branch":    300,
    "Atlantic City":       260,
    "Unknown":             400,   # fallback
}

# Assumed delay for cancellations (minutes).
# Reflects typical wait for next scheduled service during peak hours.
CANCELLATION_ASSUMED_MINUTES = 60


def get_riders(line):
    """Look up estimated peak riders for a given line."""
    if line in RIDERS_PER_TRAIN:
        return RIDERS_PER_TRAIN[line]

    # Fuzzy match (e.g. "Morris & Essex Lines" -> "Morris & Essex")
    for key in RIDERS_PER_TRAIN:
        if key.lower() in line.lower() or line.lower() in key.lower():
            return RIDERS_PER_TRAIN[key]

    print(f"[CALCULATOR] Warning: no rider data for line '{line}', using fallback.")
    return RIDERS_PER_TRAIN["Unknown"]


def calculate_cost(interpreted_delay):
    """
    Calculate the economic cost of a delay.

    Takes the dict from the Interpreter and adds:
      - estimated_riders: int
      - dollar_estimate: float (rounded to 2 decimal places)
      - vtts_rate_used: the rate we used (for transparency in the sheet)

    Returns the enriched dict, or None if we can't calculate.
    """
    delay_minutes = interpreted_delay.get("delay_minutes")
    line = interpreted_delay.get("line", "Unknown")
    is_cancellation = interpreted_delay.get("is_cancellation", False)

    # Cancellations: use standard assumed delay of 60 minutes.
    if is_cancellation and (delay_minutes is None or delay_minutes == 0):
        delay_minutes = CANCELLATION_ASSUMED_MINUTES
        interpreted_delay["delay_minutes"] = delay_minutes
        interpreted_delay["cancellation_assumed_delay"] = True

    if delay_minutes is None:
        print(f"[CALCULATOR] Cannot calculate — no delay duration for: {line}")
        return None

    riders = get_riders(line)
    hours_delayed = delay_minutes / 60
    dollar_estimate = round(riders * hours_delayed * VTTS_RATE, 2)

    interpreted_delay["estimated_riders"] = riders
    interpreted_delay["dollar_estimate"] = dollar_estimate
    interpreted_delay["vtts_rate_used"] = VTTS_RATE

    print(f"[CALCULATOR] {line} | {delay_minutes} min | ~{riders} riders | ${dollar_estimate:,.2f}")

    return interpreted_delay


# ── Run standalone for testing ────────────────────────────────────────────────
if __name__ == "__main__":
    import json

    test_delays = [
        {
            "line": "Northeast Corridor",
            "delay_minutes": 34,
            "direction": "inbound",
            "cause": "mechanical issue",
            "train_number": "3876",
            "is_cancellation": False,
            "raw_text": "NEC train #3876 is up to 34 min. late due to mechanical issues.",
        },
        {
            "line": "Morris & Essex",
            "delay_minutes": None,
            "direction": "outbound",
            "cause": "crew availability",
            "train_number": "6042",
            "is_cancellation": True,
            "raw_text": "M&E train #6042 is cancelled due to crew availability.",
        },
        {
            "line": "Raritan Valley",
            "delay_minutes": None,
            "direction": "inbound",
            "cause": "equipment availability",
            "train_number": "4201",
            "is_cancellation": True,
            "raw_text": "RVL train #4201 is cancelled due to equipment availability.",
        },
    ]

    for delay in test_delays:
        result = calculate_cost(delay)
        if result:
            print(json.dumps(result, indent=2))
            print()


# ── System-wide Penn Station throughput ───────────────────────────────────────
# ~40,000 inbound passengers during the 5-hour morning rush = 8,000/hr.
# Same figure used for evening outbound (comparable volume).
# Assumption: system-wide alerts last 1 hour unless text says otherwise.
# Disclosed in methodology as an estimated upper-bound figure.
PENN_STATION_RIDERS_PER_HOUR = 8000
SYSTEM_WIDE_ASSUMED_DURATION_MINUTES = 60


def calculate_system_wide_cost(raw_delay):
    """
    Calculate the cost of a system-wide Penn Station delay event.

    Uses Penn Station throughput × assumed 1-hour duration × VTTS rate,
    rather than a per-train rider count.

    Returns an enriched dict with the same shape as calculate_cost()
    so it flows cleanly into the aggregator and logger.
    """
    delay_minutes = raw_delay.get("delay_minutes")
    if delay_minutes is None:
        print("[CALCULATOR] System-wide alert has no parseable delay duration — skipping.")
        return None

    # Cost = throughput/hr × (delay_min / 60) × assumed_duration_hr × VTTS
    # Simplified: throughput × (delay_minutes / 60) × VTTS
    # (The 1-hour duration assumption means we use throughput as the rider count)
    riders = PENN_STATION_RIDERS_PER_HOUR
    hours_delayed = delay_minutes / 60
    dollar_estimate = round(riders * hours_delayed * VTTS_RATE, 2)

    result = dict(raw_delay)
    result["estimated_riders"] = riders
    result["dollar_estimate"] = dollar_estimate
    result["vtts_rate_used"] = VTTS_RATE
    result["train_number"] = None
    result["is_cancellation"] = False
    result["cause"] = "system-wide signal/infrastructure issue"
    result["direction"] = "both"
    result["time_band"] = "peak"
    result["system_wide_assumed_duration_minutes"] = SYSTEM_WIDE_ASSUMED_DURATION_MINUTES
    result["cancellation_assumed_delay"] = False

    print(f"[CALCULATOR] SYSTEM-WIDE | {delay_minutes} min | "
          f"~{riders:,} riders/hr × 1hr | ${dollar_estimate:,.2f}")

    return result


# ── Trains per hour (peak) by line ────────────────────────────────────────────
# Approximate peak-hour train frequency based on published NJT timetables.
# Used to calculate line-wide suspension impact: riders/train × trains/hr.
TRAINS_PER_HOUR_PEAK = {
    "Northeast Corridor": 8,
    "North Jersey Coast":  4,
    "Morris & Essex":      5,
    "Montclair-Boonton":   3,
    "Main/Bergen County":  4,
    "Raritan Valley":      4,
    "Pascack Valley":      2,
    "Port Jervis":         2,
    "Gladstone Branch":    2,
    "Atlantic City":       2,
    "Unknown":             3,
}

# Assumed duration for a line suspension (minutes).
LINE_SUSPENSION_ASSUMED_DURATION_MINUTES = 60


def calculate_line_suspension_cost(raw_delay):
    """
    Calculate the cost of a full line suspension.

    Uses: riders_per_train × trains_per_hour × 1hr assumed duration × VTTS_RATE
    This gives riders affected per hour, comparable to the Penn Station
    throughput approach used for system-wide Penn alerts.

    A line suspension of M&E (550 riders × 5 trains/hr × $24) = $66,000.
    An NEC suspension (825 × 8 × $24) = $158,400.
    """
    line = raw_delay.get("line", "Unknown")

    riders_per_train = RIDERS_PER_TRAIN.get(line)
    if riders_per_train is None:
        for key in RIDERS_PER_TRAIN:
            if key.lower() in line.lower() or line.lower() in key.lower():
                riders_per_train = RIDERS_PER_TRAIN[key]
                break
        if riders_per_train is None:
            riders_per_train = RIDERS_PER_TRAIN["Unknown"]

    trains_per_hour = TRAINS_PER_HOUR_PEAK.get(line, TRAINS_PER_HOUR_PEAK["Unknown"])
    riders_per_hour = riders_per_train * trains_per_hour
    dollar_estimate = round(riders_per_hour * VTTS_RATE, 2)

    result = dict(raw_delay)
    result["delay_minutes"] = LINE_SUSPENSION_ASSUMED_DURATION_MINUTES
    result["estimated_riders"] = riders_per_hour
    result["dollar_estimate"] = dollar_estimate
    result["vtts_rate_used"] = VTTS_RATE
    result["train_number"] = None
    result["is_cancellation"] = False
    result["cause"] = "full line suspension"
    result["direction"] = "both"
    result["time_band"] = "peak"
    result["line_suspension_assumed_duration_minutes"] = LINE_SUSPENSION_ASSUMED_DURATION_MINUTES
    result["cancellation_assumed_delay"] = False

    print(f"[CALCULATOR] LINE SUSPENSION: {line} | "
          f"{riders_per_train} riders/train × {trains_per_hour} trains/hr | "
          f"${dollar_estimate:,.2f}")

    return result
