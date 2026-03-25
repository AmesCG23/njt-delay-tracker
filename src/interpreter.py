"""
Station 2: The Interpreter
--------------------------
Takes raw alert text from the Watcher and uses Claude Haiku to extract
structured data: line, delay minutes, direction, cause of delay.

This is the only part of the pipeline that calls the Claude API.
"""

import anthropic
import json
import os
from datetime import datetime

# Time bands for ridership lookup
def get_time_band():
    """Returns 'peak', 'off_peak', or 'weekend' based on current time."""
    now = datetime.now()
    hour = now.hour
    weekday = now.weekday()  # 0=Monday, 6=Sunday

    if weekday >= 5:  # Saturday or Sunday
        return "weekend"
    if (6 <= hour < 9) or (16 <= hour < 19):  # Peak hours
        return "peak"
    return "off_peak"


def interpret_alert(alert_text, delay_minutes_hint=None):
    """
    Use Claude Haiku to extract structured data from a raw NJT alert.

    Returns a dict with:
      - line: rail line name
      - delay_minutes: integer (uses hint if Claude can't parse)
      - direction: "inbound", "outbound", or "unknown"
      - cause: short description of delay cause
      - train_number: string train number if mentioned, or None
      - time_band: "peak", "off_peak", or "weekend"
      - raw_text: the original alert

    Returns None if the alert can't be parsed or isn't a real delay.
    """
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    prompt = f"""You are parsing NJ Transit service alerts to extract delay information.

Alert text:
"{alert_text}"

Extract the following and respond with ONLY valid JSON, no explanation:
{{
  "line": "full rail line name, e.g. Northeast Corridor",
  "delay_minutes": integer number of minutes delayed (null if not mentioned),
  "direction": "inbound" (toward NYC/Hoboken) or "outbound" (away from NYC) or "unknown",
  "cause": "short phrase describing cause, e.g. mechanical issue, signal problem, crew availability",
  "train_number": "train number as string if mentioned, e.g. 3876, or null",
  "is_cancellation": true if the train is cancelled/suspended, false if just delayed
}}

Rules:
- "up to X min late" → delay_minutes = X
- Inbound = arriving at Penn Station or Hoboken Terminal
- Outbound = departing Penn Station or Hoboken Terminal, going to NJ
- If the alert says "on or close to schedule" or "normal service", return null for the whole thing
- Cause should be 3-6 words maximum"""

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}]
        )

        raw = message.content[0].text.strip()

        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        parsed = json.loads(raw)

        # If Claude says this isn't actually a delay, skip it
        if parsed is None:
            return None

        # Use the hint from the Watcher if Claude couldn't parse minutes
        if parsed.get("delay_minutes") is None and delay_minutes_hint is not None:
            parsed["delay_minutes"] = delay_minutes_hint

        # Add time band and raw text
        parsed["time_band"] = get_time_band()
        parsed["raw_text"] = alert_text
        parsed["timestamp"] = datetime.now().isoformat()

        return parsed

    except (json.JSONDecodeError, KeyError, IndexError) as e:
        print(f"[INTERPRETER] Failed to parse Claude response: {e}")
        print(f"[INTERPRETER] Raw response was: {raw if 'raw' in locals() else 'N/A'}")
        return None
    except anthropic.APIError as e:
        print(f"[INTERPRETER] Claude API error: {e}")
        return None


# ── Run standalone for testing ────────────────────────────────────────────────
if __name__ == "__main__":
    # Test with some sample alert texts
    test_alerts = [
        "NEC train #3876, the 9:28 PM arrival to PSNY, is up to 25 min. late due to earlier mechanical issues.",
        "Morris & Essex Line rail service is operating on or close to schedule.",
        "MOBO train # 1000, the 7:43 AM arrival into Hoboken Terminal, is up to 15 minutes late following earlier mechanical issues.",
        "NEC train #3949, the 5:03 PM departure from PSNY, scheduled to arrive in Trenton at 6:17 PM, is cancelled due to crew availability.",
    ]

    for alert in test_alerts:
        print(f"\nAlert: {alert[:80]}...")
        result = interpret_alert(alert)
        if result:
            print(f"Result: {json.dumps(result, indent=2)}")
        else:
            print("Result: None (not a qualifying delay)")
