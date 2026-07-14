"""
og_card.py — The Social Card Regenerator
----------------------------------------
Redraws docs/og-card.png with the live cumulative delay cost, so link
previews (Bluesky, iMessage, Slack, Facebook, ...) show the running
total instead of a static tagline.

How it works — no AI, no extra services:
  1. Reads the running cumulative total by summing the Event Log's
     "Dollar Estimate" column (the same figure Totals!B2's
     =SUM('Event Log'!I:I) formula computes), using the same
     service-account credentials the logger already uses. One extra
     read-only Sheets call per day. The Totals tab is a secondary
     fallback — see fetch_cumulative_total() for why.
  2. Draws the number onto assets/og-card/og-card-template.png with
     Pillow, using EB Garamond subsets committed to assets/og-card/
     (SIL OFL — see OFL.txt there).
  3. daily.py uploads the fresh card as the Bluesky link-card thumbnail,
     and the workflow commits it so GitHub Pages serves it to scrapers.

Fail-safe by design, like the composer: on ANY failure generate_card()
returns None and the previously committed card stays in place. The
pipeline and the day's post are never blocked by this feature.

⟵ ROLLBACK: set USE_OG_CARD=false in daily.yml — the card stops being
regenerated and whatever docs/og-card.png is committed stays forever.

Test locally without credentials:
  python src/og_card.py --total 8412067 --out /tmp/test-card.png
"""

import os
import re
import sys

# Feature flag — flip to "false" in daily.yml to freeze the card
USE_OG_CARD = os.environ.get("USE_OG_CARD", "true").lower() == "true"

_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_ASSETS = os.path.join(_REPO_ROOT, "assets", "og-card")

TEMPLATE_PATH = os.path.join(_ASSETS, "og-card-template.png")
FONT_NUMBER = os.path.join(_ASSETS, "EBGaramond-Bold-subset.ttf")
FONT_CONTEXT = os.path.join(_ASSETS, "EBGaramond-MediumItalic-subset.ttf")
CARD_PATH = os.path.join(_REPO_ROOT, "docs", "og-card.png")

# Sheet tabs the cumulative total is read from
EVENT_LOG_TAB = "Event Log"     # canonical accumulator, written every run
DOLLAR_COLUMN_HEADER = "Dollar Estimate"
DOLLAR_COLUMN_FALLBACK_IDX = 8  # column I (0-based) per the Event Log schema
TOTALS_TAB = "Totals"           # secondary source (=SUM('Event Log'!I:I))

# Palette — must match the website's CSS variables
INK = "#1a1a1a"
INK_LIGHT = "#4a4a4a"
GOLD = "#C8860A"

CONTEXT_TEXT = "in productive time lost to NJ Transit delays since April 2026 — and counting."

# Layout (card is 1200×630; the template's middle band is empty)
CARD_W = 1200
NUMBER_CENTER_Y = 360
CONTEXT_CENTER_Y = 462
NUMBER_SIZE = 118      # shrinks automatically if the figure grows wide
CONTEXT_SIZE = 34
MAX_TEXT_W = 1040      # keep clear of the side margins


def _parse_money(value):
    """Coerce a Sheets cell (number or formatted string) to a float, or None."""
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^0-9.]", "", str(value or ""))
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def _total_from_event_log(spreadsheet):
    """
    Sum the Event Log's "Dollar Estimate" column — the same figure
    Totals!B2 (=SUM('Event Log'!I:I)) computes, but without depending on a
    tab named exactly "Totals". The Event Log is the canonical accumulator
    and is written every run, so it's the most reliable source. Locates the
    column by header, falling back to the fixed schema index.
    """
    values = spreadsheet.worksheet(EVENT_LOG_TAB).get_all_values()
    if len(values) < 2:  # header only, or empty
        return None

    header = values[0]
    try:
        col = header.index(DOLLAR_COLUMN_HEADER)
    except ValueError:
        col = DOLLAR_COLUMN_FALLBACK_IDX

    total = 0.0
    for row in values[1:]:
        if col < len(row):
            amount = _parse_money(row[col])
            if amount:
                total += amount
    return total if total > 0 else None


def fetch_cumulative_total():
    """
    Return the running cumulative dollar total (already including today's
    events, which are logged before the tweet posts).

    Primary source: sum the Event Log's "Dollar Estimate" column. This is
    exactly what Totals!B2 (=SUM('Event Log'!I:I)) computes, but doesn't
    depend on a tab named "Totals" existing — which is what broke on
    2026-07-14, when worksheet("Totals") raised WorksheetNotFound and both
    this card and web_stats.py (which calls this function) fell back to
    their previously committed figures.

    Secondary source: Totals!B2, if that tab is present. Belt-and-suspenders
    in case the Event Log is ever renamed.

    Returns a positive float, or None if neither source yields one.
    """
    from logger import get_sheet_client  # reuses GOOGLE_CREDENTIALS_JSON auth

    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        print("[OG-CARD] GOOGLE_SHEET_ID not set.")
        return None

    client = get_sheet_client()
    spreadsheet = client.open_by_key(sheet_id)

    # Primary: sum the Event Log directly.
    try:
        total = _total_from_event_log(spreadsheet)
        if total:
            return total
        print("[OG-CARD] Event Log yielded no positive total — trying Totals!B2.")
    except Exception as e:
        print(f"[OG-CARD] Event Log sum failed ({e}) — trying Totals!B2.")

    # Secondary: the Totals tab, if it exists under that name.
    try:
        raw = spreadsheet.worksheet(TOTALS_TAB).acell("B2").value
        total = _parse_money(raw)
        if total and total > 0:
            return total
        print(f"[OG-CARD] Totals!B2 empty/zero/unparsable: {raw!r}")
    except Exception as e:
        print(f"[OG-CARD] Totals!B2 unavailable: {e}")

    return None


def render_card(total, out_path=CARD_PATH):
    """
    Draw the cumulative total onto the masthead template and save it.
    Returns out_path.
    """
    from PIL import Image, ImageDraw, ImageFont

    figure = f"${total:,.0f}"

    img = Image.open(TEMPLATE_PATH).convert("RGB")
    draw = ImageDraw.Draw(img)

    # Big figure — gold "$", ink digits. Shrink until it fits the band.
    size = NUMBER_SIZE
    while size > 40:
        font = ImageFont.truetype(FONT_NUMBER, size)
        if draw.textlength(figure, font=font) <= MAX_TEXT_W:
            break
        size -= 4
    dollar_w = draw.textlength("$", font=font)
    total_w = draw.textlength(figure, font=font)
    x = (CARD_W - total_w) / 2
    draw.text((x, NUMBER_CENTER_Y), "$", font=font, fill=GOLD, anchor="lm")
    draw.text((x + dollar_w, NUMBER_CENTER_Y), figure[1:], font=font, fill=INK, anchor="lm")

    # Context line under the figure
    size = CONTEXT_SIZE
    while size > 16:
        ctx_font = ImageFont.truetype(FONT_CONTEXT, size)
        if draw.textlength(CONTEXT_TEXT, font=ctx_font) <= MAX_TEXT_W:
            break
        size -= 2
    draw.text((CARD_W / 2, CONTEXT_CENTER_Y), CONTEXT_TEXT,
              font=ctx_font, fill=INK_LIGHT, anchor="mm")

    img.save(out_path, optimize=True)
    print(f"[OG-CARD] Rendered {figure} → {out_path}")
    return out_path


def generate_card(out_path=CARD_PATH):
    """
    Fail-safe entry point used by daily.py.
    Returns the card path on success, or None on any failure —
    in which case the previously committed card remains in place.
    """
    if not USE_OG_CARD:
        print("[OG-CARD] USE_OG_CARD=false — skipping card regeneration.")
        return None
    try:
        total = fetch_cumulative_total()
        if total is None:
            return None
        return render_card(total, out_path)
    except Exception as e:
        print(f"[OG-CARD] Card regeneration failed — leaving existing card untouched: {e}")
        return None


if __name__ == "__main__":
    # Local test:  python src/og_card.py --total 8412067 [--out /tmp/test.png]
    total_arg, out_arg = None, CARD_PATH
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--total" and i + 1 < len(args):
            total_arg = float(args[i + 1])
        if a == "--out" and i + 1 < len(args):
            out_arg = args[i + 1]

    if total_arg is not None:
        render_card(total_arg, out_arg)
    else:
        generate_card(out_arg)
