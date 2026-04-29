"""
Station 4: The Logger
---------------------
Writes each delay event to Google Sheets (Tab 1: Event Log).
Reads the running total from Tab 2 (formula-driven, not written by code).

Requires: GOOGLE_CREDENTIALS_JSON env var (contents of service account JSON)
          GOOGLE_SHEET_ID env var (the ID from your sheet's URL)
"""

import gspread
from google.oauth2.service_account import Credentials
import json
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")


# ── Google Sheets setup ───────────────────────────────────────────────────────
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

# Tab names in your Google Sheet
EVENT_LOG_TAB = "Event Log"
TOTALS_TAB = "Totals"


def get_sheet_client():
    """Authenticate with Google Sheets using service account credentials."""
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        raise ValueError("GOOGLE_CREDENTIALS_JSON environment variable not set.")

    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)


def ensure_headers(worksheet):
    """Make sure the Event Log tab has the right column headers."""
    expected_headers = [
        "Date",
        "Time",
        "Line",
        "Train #",
        "Direction",
        "Time Band",
        "Delay Minutes",
        "Estimated Riders",
        "Dollar Estimate",
        "Cause",
        "Is Cancellation",
        "Raw Alert Text",
        "Posted to Bluesky",
    ]

    existing = worksheet.row_values(1)
    if existing != expected_headers:
        print("[LOGGER] Setting up column headers...")
        worksheet.update("A1", [expected_headers])


def _build_event_row(delay_data):
    """Build a single Event Log row from a delay dict."""
    ts = delay_data.get("timestamp", datetime.now(_ET).isoformat())
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        et = dt.astimezone(_ET)
        date_str = et.strftime("%Y-%m-%d")
        time_str = et.strftime("%H:%M")
    except (ValueError, TypeError, AttributeError):
        now_et = datetime.now(_ET)
        date_str = now_et.strftime("%Y-%m-%d")
        time_str = now_et.strftime("%H:%M")

    return [
        date_str,
        time_str,
        delay_data.get("line", "Unknown"),
        delay_data.get("train_number") or "",
        delay_data.get("direction", "unknown"),
        delay_data.get("time_band", "unknown"),
        delay_data.get("delay_minutes") or "",
        delay_data.get("estimated_riders") or "",
        delay_data.get("dollar_estimate") or "",
        delay_data.get("cause", ""),
        "Yes" if delay_data.get("is_cancellation") else "No",
        delay_data.get("raw_text", ""),
        "No",
    ]


def log_delay(delay_data):
    """
    Append a single delay event to the Event Log tab.
    Kept for backwards compatibility — prefer log_delay_batch() for
    bulk writes to avoid hitting the Sheets API rate limit.
    """
    log_delay_batch([delay_data])


def log_delay_batch(delay_list):
    """
    Write a list of delay events to the Event Log in ONE API call.
    This is the preferred method — avoids rate-limit errors when
    NJT is having a bad day with many delays.

    Called once per window (all morning events, then all evening events).
    """
    if not delay_list:
        return

    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise ValueError("GOOGLE_SHEET_ID not set.")

    try:
        client = get_sheet_client()
        spreadsheet = client.open_by_key(sheet_id)

        try:
            log_tab = spreadsheet.worksheet(EVENT_LOG_TAB)
        except gspread.WorksheetNotFound:
            log_tab = spreadsheet.add_worksheet(EVENT_LOG_TAB, rows=1000, cols=15)

        ensure_headers(log_tab)

        rows = [_build_event_row(d) for d in delay_list]
        log_tab.append_rows(rows, value_input_option="USER_ENTERED")

        print(f"[LOGGER] Wrote {len(rows)} event(s) to Event Log in one batch.")

    except Exception as e:
        print(f"[LOGGER] Google Sheets error: {e}")
        raise


def mark_as_posted(row_number):
    """Mark a row as posted to Bluesky (updates column M)."""
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    try:
        client = get_sheet_client()
        spreadsheet = client.open_by_key(sheet_id)
        log_tab = spreadsheet.worksheet(EVENT_LOG_TAB)
        # Column M (13) is "Posted to Bluesky"
        log_tab.update_cell(row_number, 13, "Yes")
    except Exception as e:
        print(f"[LOGGER] Could not mark row as posted: {e}")


# ── Run standalone for testing ────────────────────────────────────────────────
if __name__ == "__main__":
    test_delay = {
        "line": "Northeast Corridor",
        "delay_minutes": 34,
        "direction": "inbound",
        "cause": "mechanical issue",
        "train_number": "3876",
        "time_band": "peak",
        "is_cancellation": False,
        "estimated_riders": 825,
        "dollar_estimate": 11220.00,
        "raw_text": "NEC train #3876, the 9:28 PM arrival to PSNY, is up to 34 min. late due to mechanical issues.",
        "timestamp": datetime.now(_ET).isoformat(),
    }

    running_total = log_delay(test_delay)
    print(f"Running total: ${running_total:,.2f}" if running_total else "Could not read running total.")


TWEET_LOG_TAB = "Tweet_log"


def log_tweet(text, total_cost, event_count, uri=None, person_hours=0,
              morning_cost=0, evening_cost=0):
    """
    Append the daily summary tweet to the Tweet_log tab.
    Creates the tab and headers automatically if they don't exist.

    Columns A–E: Timestamp | Tweet Text | Total Cost Estimate | Number of Delay Events | Post URI
    Columns F–H: reserved for manual/formula use (Date, Person-Hours, etc.)
    Column I:    Morning Cost (post-dedup dollar total for morning window)
    Column J:    Evening Cost (post-dedup dollar total for evening window)
    """
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise ValueError("GOOGLE_SHEET_ID not set.")

    try:
        client = get_sheet_client()
        spreadsheet = client.open_by_key(sheet_id)

        # Get or create Tweet_log tab
        try:
            tweet_tab = spreadsheet.worksheet(TWEET_LOG_TAB)
        except gspread.WorksheetNotFound:
            tweet_tab = spreadsheet.add_worksheet(TWEET_LOG_TAB, rows=500, cols=10)

        # Write column I/J headers if not already present
        existing_i = tweet_tab.acell("I1").value
        if existing_i != "Morning Cost":
            tweet_tab.update("I1", [["Morning Cost", "Evening Cost"]])

        # Append the row — F/G/H left empty (user-managed columns)
        row = [
            datetime.now(_ET).strftime("%Y-%m-%d %H:%M:%S"),  # A: Timestamp
            text,                                               # B: Tweet Text
            f"${total_cost:,.2f}",                             # C: Total Cost Estimate
            event_count,                                        # D: Number of Delay Events
            uri or "",                                          # E: Post URI
            "",                                                 # F: (user-managed)
            "",                                                 # G: (user-managed)
            "",                                                 # H: (reserved)
            round(morning_cost),                               # I: Morning Cost
            round(evening_cost),                               # J: Evening Cost
        ]
        tweet_tab.append_row(row, value_input_option="USER_ENTERED")
        print(f"[LOGGER] Tweet logged to {TWEET_LOG_TAB} tab "
              f"(morning=${morning_cost:,.0f}, evening=${evening_cost:,.0f}).")

        # Write yesterday's total cost to for_web!A1 so the website updates.
        try:
            for_web = spreadsheet.worksheet("for_web")
            for_web.update("A1", [[round(total_cost)]])
            for_web.update("A3", [[round(person_hours)]])
            print(f"[LOGGER] for_web updated: A1={round(total_cost)}, A3={round(person_hours)}")
        except gspread.WorksheetNotFound:
            print("[LOGGER] for_web tab not found — create it manually and publish to web.")
        except Exception as web_err:
            print(f"[LOGGER] Could not update for_web tab: {web_err}")

    except Exception as e:
        print(f"[LOGGER] Failed to log tweet: {e}")
        raise


# ── Run Log ───────────────────────────────────────────────────────────────────
RUN_LOG_TAB = "Run Log"

RUN_LOG_HEADERS = [
    "Run Date",
    "Period",
    "Raw Posts Fetched",
    "After Dedup",
    "Total Cost",
    "Date of Post",
    "Time of Post",
    "Post URI",
]


def clear_run_log():
    """
    Wipe the Run Log tab at the start of each daily run.
    Creates the tab (with headers) if it doesn't exist yet.
    Called once at the top of daily.py before any window processing.
    """
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise ValueError("GOOGLE_SHEET_ID not set.")

    try:
        client = get_sheet_client()
        spreadsheet = client.open_by_key(sheet_id)

        try:
            run_tab = spreadsheet.worksheet(RUN_LOG_TAB)
            # Wipe everything and re-add headers
            run_tab.clear()
            run_tab.update("A1", [RUN_LOG_HEADERS])
            print(f"[LOGGER] Run Log cleared and ready.")
        except gspread.WorksheetNotFound:
            run_tab = spreadsheet.add_worksheet(RUN_LOG_TAB, rows=50, cols=len(RUN_LOG_HEADERS))
            run_tab.update("A1", [RUN_LOG_HEADERS])
            print(f"[LOGGER] Run Log tab created.")

    except Exception as e:
        print(f"[LOGGER] Could not clear Run Log: {e}")
        raise


def log_run(period, raw_count, dedup_count, total_cost, post_uri=None):
    """
    Append one row to the Run Log for a completed window.
    Called once per window (morning, evening) in daily.py.

    Args:
        period:      "morning" or "evening"
        raw_count:   number of raw posts fetched from Bluesky
        dedup_count: number of events after deduplication
        total_cost:  dollar total for this window
        post_uri:    not the tweet URI (that's logged separately) —
                     this is None per window; the tweet URI is added
                     to the summary row by log_run_summary()
    """
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise ValueError("GOOGLE_SHEET_ID not set.")

    now = datetime.now(_ET)

    try:
        client = get_sheet_client()
        spreadsheet = client.open_by_key(sheet_id)
        run_tab = spreadsheet.worksheet(RUN_LOG_TAB)

        row = [
            now.strftime("%Y-%m-%d"),       # Run Date
            period.capitalize(),             # Period
            raw_count,                       # Raw Posts Fetched
            dedup_count,                     # After Dedup
            f"${total_cost:,.2f}",           # Total Cost
            "",                              # Date of Post (filled by log_run_summary)
            "",                              # Time of Post (filled by log_run_summary)
            "",                              # Post URI (filled by log_run_summary)
        ]

        run_tab.append_row(row, value_input_option="USER_ENTERED")
        print(f"[LOGGER] Run Log: {period} — {raw_count} raw → {dedup_count} deduped → ${total_cost:,.2f}")

    except Exception as e:
        print(f"[LOGGER] Could not write to Run Log: {e}")
        raise


def log_run_summary(post_date, post_time, post_uri):
    """
    After the tweet fires, update the Run Log rows with the post details.
    Fills in Date of Post, Time of Post, and Post URI on every row
    (both morning and evening share the same post).
    """
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise ValueError("GOOGLE_SHEET_ID not set.")

    try:
        client = get_sheet_client()
        spreadsheet = client.open_by_key(sheet_id)
        run_tab = spreadsheet.worksheet(RUN_LOG_TAB)

        all_rows = run_tab.get_all_values()
        for i, row in enumerate(all_rows[1:], start=2):  # skip header
            run_tab.update(f"F{i}", post_date)
            run_tab.update(f"G{i}", post_time)
            run_tab.update(f"H{i}", post_uri or "")

        print(f"[LOGGER] Run Log updated with post details.")

    except Exception as e:
        print(f"[LOGGER] Could not update Run Log with post details: {e}")


# ── Alert Log ─────────────────────────────────────────────────────────────────
ALERT_LOG_TAB = "Alert Log"

ALERT_LOG_HEADERS = [
    "Date Seen",
    "Alert Date",
    "Alert Time",
    "Line",
    "Train #",
    "Delay Minutes",
    "Estimated Cost (pre-dedup)",
    "Raw Alert Text",
]


def clear_alert_log():
    """
    Wipe the Alert Log tab at the start of each daily run.
    Creates the tab with headers if it doesn't exist.
    """
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise ValueError("GOOGLE_SHEET_ID not set.")

    try:
        client = get_sheet_client()
        spreadsheet = client.open_by_key(sheet_id)

        try:
            alert_tab = spreadsheet.worksheet(ALERT_LOG_TAB)
            alert_tab.clear()
            alert_tab.update("A1", [ALERT_LOG_HEADERS])
            print(f"[LOGGER] Alert Log cleared.")
        except gspread.WorksheetNotFound:
            alert_tab = spreadsheet.add_worksheet(
                ALERT_LOG_TAB, rows=500, cols=len(ALERT_LOG_HEADERS)
            )
            alert_tab.update("A1", [ALERT_LOG_HEADERS])
            print(f"[LOGGER] Alert Log tab created.")

    except Exception as e:
        print(f"[LOGGER] Could not clear Alert Log: {e}")
        raise


def log_alert_batch(interpreted_events):
    """
    Log a batch of interpreted alerts (pre-dedup) to the Alert Log tab.
    Called once per window after interpretation, before deduplication.

    For each event we calculate a quick cost estimate purely for the log —
    this doesn't affect the final post-dedup cost calculation.
    """
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise ValueError("GOOGLE_SHEET_ID not set.")

    if not interpreted_events:
        return

    try:
        client = get_sheet_client()
        spreadsheet = client.open_by_key(sheet_id)
        alert_tab = spreadsheet.worksheet(ALERT_LOG_TAB)

        # Import here to avoid circular imports
        from calculator import VTTS_RATE, RIDERS_PER_TRAIN, PENN_STATION_RIDERS_PER_HOUR

        rows = []
        date_seen = datetime.now(_ET).strftime("%Y-%m-%d")

        for event in interpreted_events:
            # Parse alert timestamp, display in ET
            try:
                ts_str = event.get("timestamp", "")
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                et = ts.astimezone(_ET)
                alert_date = et.strftime("%Y-%m-%d")
                alert_time = et.strftime("%H:%M")
            except (ValueError, AttributeError):
                alert_date = ""
                alert_time = ""

            # Quick cost estimate for the log
            delay_mins = event.get("delay_minutes") or 0
            line = event.get("line", "Unknown")

            if event.get("system_wide") and not event.get("line_suspension"):
                riders = PENN_STATION_RIDERS_PER_HOUR
            else:
                riders = RIDERS_PER_TRAIN.get(line, RIDERS_PER_TRAIN["Unknown"])

            est_cost = round(riders * (delay_mins / 60) * VTTS_RATE, 2) if delay_mins else 0

            rows.append([
                date_seen,
                alert_date,
                alert_time,
                line,
                event.get("train_number") or "",
                delay_mins or "",
                f"${est_cost:,.2f}" if est_cost else "",
                event.get("raw_text") or event.get("text", ""),
            ])

        if rows:
            alert_tab.append_rows(rows, value_input_option="USER_ENTERED")
            print(f"[LOGGER] Alert Log: {len(rows)} alerts recorded.")

    except Exception as e:
        print(f"[LOGGER] Could not write to Alert Log: {e}")
        raise
