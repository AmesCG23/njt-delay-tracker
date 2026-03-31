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
from datetime import datetime


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


def log_delay(delay_data):
    """
    Append a delay event to the Event Log tab.

    Returns the running annual total (read from the Totals tab),
    or None if we can't read it.
    """
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise ValueError("GOOGLE_SHEET_ID environment variable not set.")

    try:
        client = get_sheet_client()
        spreadsheet = client.open_by_key(sheet_id)

        # ── Write to Event Log tab ──
        try:
            log_tab = spreadsheet.worksheet(EVENT_LOG_TAB)
        except gspread.WorksheetNotFound:
            log_tab = spreadsheet.add_worksheet(EVENT_LOG_TAB, rows=1000, cols=15)

        ensure_headers(log_tab)

        # Parse timestamp
        ts = delay_data.get("timestamp", datetime.now().isoformat())
        try:
            dt = datetime.fromisoformat(ts)
            date_str = dt.strftime("%Y-%m-%d")
            time_str = dt.strftime("%H:%M")
        except (ValueError, TypeError):
            date_str = datetime.now().strftime("%Y-%m-%d")
            time_str = datetime.now().strftime("%H:%M")

        row = [
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
            "No",  # Posted to Bluesky — updated after posting
        ]

        log_tab.append_row(row, value_input_option="USER_ENTERED")
        print(f"[LOGGER] Logged to Google Sheet: {delay_data.get('line')} | ${delay_data.get('dollar_estimate', 0):,.2f}")

        # ── Read running total from Totals tab ──
        try:
            totals_tab = spreadsheet.worksheet(TOTALS_TAB)
            # We expect the running total to be in cell B2
            # (set up the formula =SUM('Event Log'!I:I) there manually)
            running_total_str = totals_tab.acell("B2").value
            if running_total_str:
                # Remove $ and commas if present
                running_total = float(running_total_str.replace("$", "").replace(",", ""))
                print(f"[LOGGER] Running total: ${running_total:,.2f}")
                return running_total
        except (gspread.WorksheetNotFound, ValueError, AttributeError):
            print("[LOGGER] Could not read running total from Totals tab.")

        return None

    except Exception as e:
        print(f"[LOGGER] Google Sheets error: {e}")
        return None


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
        "timestamp": datetime.now().isoformat(),
    }

    running_total = log_delay(test_delay)
    print(f"Running total: ${running_total:,.2f}" if running_total else "Could not read running total.")


TWEET_LOG_TAB = "Tweet_log"


def log_tweet(text, total_cost, event_count, uri=None):
    """
    Append the daily summary tweet to the Tweet_log tab.
    Creates the tab and headers automatically if they don't exist.

    Columns: Timestamp | Tweet Text | Total Cost Estimate | Number of Delay Events | Post URI
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
            tweet_tab = spreadsheet.add_worksheet(TWEET_LOG_TAB, rows=500, cols=6)

        # Add headers if the sheet is empty
        existing = tweet_tab.row_values(1)
        expected_headers = [
            "Timestamp",
            "Tweet Text",
            "Total Cost Estimate",
            "Number of Delay Events",
            "Post URI",
        ]
        if existing != expected_headers:
            tweet_tab.update("A1", [expected_headers])

        # Append the row
        row = [
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            text,
            f"${total_cost:,.2f}",
            event_count,
            uri or "",
        ]
        tweet_tab.append_row(row, value_input_option="USER_ENTERED")
        print(f"[LOGGER] Tweet logged to {TWEET_LOG_TAB} tab.")

    except Exception as e:
        print(f"[LOGGER] Failed to log tweet: {e}")
        raise
