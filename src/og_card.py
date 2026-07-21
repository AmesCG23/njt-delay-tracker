"""
og_card.py — The Social Card Regenerator
----------------------------------------
Redraws docs/og-card.png with the live cumulative delay cost, so link
previews (Bluesky, iMessage, Slack, Facebook, ...) show the running
total instead of a static tagline.

How it works — no AI, no extra services:
  1. Reads the cumulative total the WEBSITE shows — for_web!A2, which
     sums the Tweet_log tab — using the same service-account credentials
     the logger already uses. daily.py adds the current run's total on
     top (Tweet_log isn't written until after the card is drawn), so the
     card matches the site once the run finishes. Summing the Tweet_log
     Total Cost column directly is the fallback. One read-only Sheets
     call per day.
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

# Where the cumulative total is read from. It MUST be the same figure the
# website shows, or the card and the site disagree: the website reads
# for_web!A2 (which sums Tweet_log), so the card reads that exact cell.
# Summing the Tweet_log Total Cost column is the fallback.
FOR_WEB_TAB = "for_web"
FOR_WEB_CUMULATIVE_CELL = "A2"   # the cell the website's JS reads (CSV rows[1])
TWEET_LOG_TAB = "Tweet_log"      # fallback source (one row per day)
TWEET_LOG_COST_COL_IDX = 2       # column C, "Total Cost Estimate" ("$X,XXX.XX")

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


def _read_for_web_cumulative(spreadsheet):
    """
    Read for_web!A2 — the exact cell the website displays as the cumulative
    total (it sums the Tweet_log tab). Reading it here keeps the social card
    and the website in lockstep. Returns a positive float, or None.
    """
    raw = spreadsheet.worksheet(FOR_WEB_TAB).acell(FOR_WEB_CUMULATIVE_CELL).value
    total = _parse_money(raw)
    return total if total and total > 0 else None


def _sum_tweet_log(spreadsheet):
    """
    Fallback: sum the Tweet_log tab's Total Cost column (one row per day) —
    the same figure for_web!A2 is built from, for when that cell can't be
    read. Every row is parsed; a header cell fails _parse_money and is
    skipped, so this is robust whether or not a header row is present.
    Returns a positive float, or None.
    """
    values = spreadsheet.worksheet(TWEET_LOG_TAB).get_all_values()
    total = 0.0
    for row in values:
        if TWEET_LOG_COST_COL_IDX < len(row):
            amount = _parse_money(row[TWEET_LOG_COST_COL_IDX])
            if amount:
                total += amount
    return total if total > 0 else None


def fetch_cumulative_total():
    """
    Return the cumulative dollar total the WEBSITE shows — for_web!A2, which
    sums the Tweet_log tab. This is the figure "through the last day already
    written to Tweet_log"; because Tweet_log isn't written until log_tweet()
    runs (after the card is drawn), daily.py adds the current run's total on
    top so the card matches what the site will show once the run finishes.

    Primary source: for_web!A2 (exactly what the website reads, so the two
    can never disagree). Fallback: sum the Tweet_log Total Cost column, in
    case that cell is unreadable.

    Returns a positive float, or None if neither source yields one.
    """
    from logger import get_sheet_client  # reuses GOOGLE_CREDENTIALS_JSON auth

    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        print("[OG-CARD] GOOGLE_SHEET_ID not set.")
        return None

    client = get_sheet_client()
    spreadsheet = client.open_by_key(sheet_id)

    # Primary: the exact cell the website displays.
    try:
        total = _read_for_web_cumulative(spreadsheet)
        if total:
            return total
        print("[OG-CARD] for_web!A2 empty/zero — trying the Tweet_log sum.")
    except Exception as e:
        print(f"[OG-CARD] for_web!A2 unavailable ({e}) — trying the Tweet_log sum.")

    # Fallback: sum Tweet_log directly (the figure for_web!A2 is built from).
    try:
        total = _sum_tweet_log(spreadsheet)
        if total:
            return total
        print("[OG-CARD] Tweet_log sum yielded no positive total.")
    except Exception as e:
        print(f"[OG-CARD] Tweet_log sum failed: {e}")

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


def generate_card(total=None, out_path=CARD_PATH):
    """
    Fail-safe entry point used by daily.py.
    Returns the card path on success, or None on any failure —
    in which case the previously committed card remains in place.

    daily.py passes `total` — the cumulative figure it has already computed
    (for_web!A2 + today's run) — so the card matches the number the website
    will show and the freshly-baked HTML. If `total` is None (e.g. a direct
    call), the figure is fetched here as a fallback; note that a bare fetch
    excludes today's run, since Tweet_log isn't written until after the card.
    """
    if not USE_OG_CARD:
        print("[OG-CARD] USE_OG_CARD=false — skipping card regeneration.")
        return None
    try:
        if total is None:
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
        generate_card(out_path=out_arg)
