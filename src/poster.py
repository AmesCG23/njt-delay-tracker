"""
Station 5: The Poster
---------------------
Formats and posts a delay cost estimate to Bluesky.

Requires: BLUESKY_HANDLE env var (e.g. yourbot.bsky.social)
          BLUESKY_PASSWORD env var (your Bluesky app password)
"""

from atproto import Client
import os


def format_post(delay_data, running_total=None):
    """
    Format the Bluesky post text for a delay event.
    Keeps it under 300 characters.

    Returns the post text string.
    """
    line = delay_data.get("line", "NJ Transit")
    delay_minutes = delay_data.get("delay_minutes")
    riders = delay_data.get("estimated_riders", 0)
    cost = delay_data.get("dollar_estimate", 0)
    cause = delay_data.get("cause", "")
    is_cancellation = delay_data.get("is_cancellation", False)

    # Format the event type
    if is_cancellation:
        event = "cancellation"
    elif delay_minutes:
        event = f"{delay_minutes}-min delay"
    else:
        event = "delay"

    # Format cost
    if cost >= 1_000_000:
        cost_str = f"${cost/1_000_000:.1f}M"
    elif cost >= 1_000:
        cost_str = f"${cost:,.0f}"
    else:
        cost_str = f"${cost:.0f}"

    # Format running total
    total_str = ""
    if running_total:
        if running_total >= 1_000_000:
            total_str = f"\n2025 total: ${running_total/1_000_000:.1f}M"
        else:
            total_str = f"\n2025 total: ${running_total:,.0f}"

    # Build post — aim for under 280 chars to leave room for link
    cause_str = f" ({cause})" if cause else ""
    riders_str = f"~{riders:,}" if riders else "unknown"

    post = (
        f"NJ Transit {line}: {event}{cause_str}.\n"
        f"{riders_str} commuters affected.\n"
        f"Estimated lost worker time: {cost_str}"
        f"{total_str}"
    )

    # Truncate if somehow over 300 chars
    if len(post) > 295:
        post = post[:292] + "..."

    return post


def post_to_bluesky(delay_data, running_total=None):
    """
    Post the delay cost estimate to Bluesky.

    Returns the post URI if successful, None if it fails.
    """
    handle = os.environ.get("BLUESKY_HANDLE")
    password = os.environ.get("BLUESKY_PASSWORD")

    if not handle or not password:
        print("[POSTER] BLUESKY_HANDLE or BLUESKY_PASSWORD not set. Skipping post.")
        return None

    post_text = format_post(delay_data, running_total)

    print(f"[POSTER] Preparing to post:\n{post_text}\n")

    try:
        client = Client()
        client.login(handle, password)
        response = client.send_post(post_text)
        uri = response.uri
        print(f"[POSTER] Posted successfully: {uri}")
        return uri

    except Exception as e:
        print(f"[POSTER] Failed to post to Bluesky: {e}")
        return None


# ── Run standalone for testing ────────────────────────────────────────────────
if __name__ == "__main__":
    import json

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
    }

    # Test formatting without actually posting
    print("=== POST PREVIEW ===")
    print(format_post(test_delay, running_total=1_243_800))
    print(f"\nCharacter count: {len(format_post(test_delay, running_total=1_243_800))}")

    # Uncomment to actually post:
    # uri = post_to_bluesky(test_delay, running_total=1_243_800)
