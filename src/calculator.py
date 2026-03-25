"""
Station 3: The Calculator
--------------------------
Takes a parsed delay dict from the Interpreter and calculates
the estimated economic cost of the delay.

Formula: riders × (delay_minutes / 60) × VTTS_RATE
"""

# ── Value of Travel Time ──────────────────────────────────────────────────────
# USDOT methodology applied to NJ median household income ($99,781, 2023 Census ACS)
# $99,781 ÷ 2,080 work hours = $47.97/hr × 50% = $23.99/hr → rounded to $24.00
VTTS_RATE = 24.00  # dollars per hour

# National USDOT default (disclosed in methodology for transparency)
VTTS_NATIONAL_DEFAULT = 18.80

# ── Riders per train by line and time band ────────────────────────────────────
# Built from: 62M annual rail riders (2025), per-line train frequency data,
# and RPA anchor data (63,014 daily Penn Station boardings).
# These are averages — actual trains vary. Disclosed as estimates on the website.
RIDERS_PER_TRAIN = {
    "Northeast Corridor": {
        "peak": 825, "off_peak": 250, "weekend": 180
    },
    "North Jersey Coast": {
        "peak": 500, "off_peak": 150, "weekend": 120
    },
    "Morris & Essex": {
        "peak": 550, "off_peak": 175, "weekend": 130
    },
    "Montclair-Boonton": {
        "peak": 415, "off_peak": 110, "weekend": 90
    },
    "Main/Bergen County": {
        "peak": 450, "off_peak": 120, "weekend": 95
    },
    "Raritan Valley": {
        "peak": 450, "off_peak": 125, "weekend": 100
    },
    "Pascack Valley": {
        "peak": 315, "off_peak": 85, "weekend": 65
    },
    "Port Jervis": {
        "peak": 300, "off_peak": 75, "weekend": 60
    },
    "Gladstone Branch": {
        "peak": 300, "off_peak": 80, "weekend": 60
    },
    "Atlantic City": {
        "peak": 260, "off_peak": 100, "weekend": 90
    },
    # Fallback for any line we can't identify
    "Unknown": {
        "peak": 400, "off_peak": 120, "weekend": 90
    },
}


def get_riders(line, time_band):
    """Look up estimated riders for a given line and time band."""
    # Try exact match first, then fuzzy match
    if line in RIDERS_PER_TRAIN:
        return RIDERS_PER_TRAIN[line][time_band]

    # Try partial match (e.g. "Morris & Essex Lines" → "Morris & Essex")
    for key in RIDERS_PER_TRAIN:
        if key.lower() in line.lower() or line.lower() in key.lower():
            return RIDERS_PER_TRAIN[key][time_band]

    # Fall back to Unknown
    print(f"[CALCULATOR] Warning: no rider data for line '{line}', using fallback.")
    return RIDERS_PER_TRAIN["Unknown"][time_band]


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
    time_band = interpreted_delay.get("time_band", "off_peak")
    is_cancellation = interpreted_delay.get("is_cancellation", False)

    # For cancellations, we use a standard 45-minute equivalent
    # (average wait for next train during peak). This is disclosed in methodology.
    if is_cancellation and (delay_minutes is None or delay_minutes == 0):
        delay_minutes = 45
        interpreted_delay["delay_minutes"] = delay_minutes
        interpreted_delay["cancellation_assumed_delay"] = True

    if delay_minutes is None:
        print(f"[CALCULATOR] Cannot calculate — no delay duration for: {line}")
        return None

    riders = get_riders(line, time_band)
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
            "time_band": "peak",
            "is_cancellation": False,
            "raw_text": "NEC train #3876 is up to 34 min. late due to mechanical issues.",
        },
        {
            "line": "Morris & Essex",
            "delay_minutes": None,
            "direction": "outbound",
            "cause": "crew availability",
            "train_number": "6042",
            "time_band": "peak",
            "is_cancellation": True,
            "raw_text": "M&E train #6042 is cancelled due to crew availability.",
        },
    ]

    for delay in test_delays:
        result = calculate_cost(delay)
        if result:
            print(json.dumps(result, indent=2))
