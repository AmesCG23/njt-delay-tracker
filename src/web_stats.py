"""
web_stats.py — The Static Stats Baker
-------------------------------------
Bakes each day's figures into the static site files, so crawlers that
don't run JavaScript — search engines' first-pass crawlers and nearly
all AI crawlers (GPTBot, ClaudeBot, PerplexityBot, ...) — see real
numbers instead of the "$—" placeholders the JavaScript later replaces.

What it rewrites (all inside docs/, committed by the workflow after the
pipeline exits, exactly like og-card.png):

  1. docs/index.html    — the hero figures + report-date sentence
  2. docs/graphs.html   — the three stat cards + their labels
  3. docs/data/latest.json — machine-readable daily snapshot (also used
     by index.html as a same-origin fallback if the Google Sheets fetch
     fails in the browser)
  4. docs/sitemap.xml   — <lastmod> for / and /graphs.html, so search
     engines see a credible daily-change signal

The HTML edits happen between sentinel comments like
    <!--WS:DAILY-->$1,234,567<!--/WS:DAILY-->
Do not remove those comments from the HTML — without them the bake
silently skips that figure (by design). The page JavaScript still
fetches the live Google Sheets values and overwrites the baked text,
so human visitors always see the freshest numbers; the baked values
are for everyone reading the raw HTML.

The cumulative total is passed in by daily.py — the same figure it
draws on the social card (for_web!A2 + today's run), so the baked page,
the card, and the live site all agree. If it isn't passed, this module
falls back to og_card's fetch_cumulative_total() (which reads for_web!A2,
what the website itself shows). If neither is available, the previously
baked cumulative figure simply stays in place.

Fail-safe by design, like the composer and the social card: on ANY
failure update_web_stats() returns None, the committed pages stay
as they are, and the pipeline is never blocked.

⟵ ROLLBACK: set USE_WEB_STATS=false in daily.yml — the baked figures
freeze at their last committed values (the browser JavaScript keeps
updating for human visitors regardless).

Test locally, no credentials needed (point --docs at a scratch copy):
  python src/web_stats.py --daily 55000 --hours 1250 \
      --date 2026-07-13 --cumulative 8412067 --docs /tmp/docs-copy
"""

import json
import os
import re
import sys
from datetime import date, datetime, timezone

# Feature flag — flip to "false" in daily.yml to freeze the baked stats
USE_WEB_STATS = os.environ.get("USE_WEB_STATS", "true").lower() == "true"

_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DOCS_DIR = os.path.join(_REPO_ROOT, "docs")

SITE_URL = "https://bettertrains.org/"


# ── Formatting (must match the site's JavaScript output) ─────────────────────

def _fmt_dollars(value):
    """55123.45 → '$55,123' — same as the JS Math.round().toLocaleString()."""
    return f"${round(value):,}"


def _fmt_int(value):
    return f"{round(value):,}"


def _long_date(d):
    """date(2026, 7, 13) → 'Monday, July 13' — matches JS toLocaleDateString."""
    return f"{d.strftime('%A, %B')} {d.day}"


# ── Sentinel replacement ─────────────────────────────────────────────────────

def _replace_span(text, key, new_value):
    """
    Replace the content between <!--WS:key--> and <!--/WS:key-->.
    Returns (new_text, replaced_bool). Missing sentinels are skipped
    with a log line rather than treated as errors.
    """
    pattern = re.compile(
        r"(<!--WS:%s-->)(.*?)(<!--/WS:%s-->)" % (re.escape(key), re.escape(key)),
        re.DOTALL,
    )
    if not pattern.search(text):
        print(f"[WEB-STATS] Sentinel WS:{key} not found — skipping that figure.")
        return text, False
    return pattern.sub(lambda m: m.group(1) + new_value + m.group(3), text, count=1), True


def _bake_html(path, replacements):
    """Apply {sentinel_key: new_value} to one HTML file. Returns True if changed."""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    original = text
    for key, value in replacements.items():
        text, _ = _replace_span(text, key, value)
    if text != original:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"[WEB-STATS] Baked figures into {os.path.basename(path)}")
        return True
    return False


# ── Sitemap lastmod ──────────────────────────────────────────────────────────

def _bump_sitemap(path, urls, lastmod_iso):
    """Set <lastmod> for the given <loc> URLs. Missing entries are skipped."""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    original = text
    for url in urls:
        pattern = re.compile(
            r"(<loc>%s</loc>\s*<lastmod>)[^<]*(</lastmod>)" % re.escape(url)
        )
        if pattern.search(text):
            text = pattern.sub(lambda m: m.group(1) + lastmod_iso + m.group(2), text)
        else:
            print(f"[WEB-STATS] No <lastmod> entry for {url} — skipping.")
    if text != original:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"[WEB-STATS] Bumped sitemap lastmod to {lastmod_iso}")
        return True
    return False


# ── Snapshot JSON ────────────────────────────────────────────────────────────

def _write_snapshot(path, daily_cost, person_hours, report_date, cumulative):
    """
    Write docs/data/latest.json. If the fresh cumulative read failed,
    preserve the previous snapshot's cumulative value rather than
    clobbering it with null.
    """
    cumulative_out = round(cumulative) if cumulative is not None else None
    if cumulative_out is None and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cumulative_out = json.load(f).get("cumulative_cost_usd")
        except Exception:
            pass

    snapshot = {
        "project": "NJT Delay Tracker",
        "site": SITE_URL,
        "report_date": report_date.isoformat(),
        "daily_cost_usd": round(daily_cost),
        "daily_person_hours": round(person_hours),
        "cumulative_cost_usd": cumulative_out,
        "cumulative_since": "2026-03-31",
        "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "methodology": SITE_URL + "methodology.html",
        "notes": (
            "Estimates of productive time lost to NJ Transit commuter rail "
            "delays; opportunity costs, not cash losses, and not an official "
            "accounting. Not affiliated with NJ Transit."
        ),
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2)
        f.write("\n")
    print(f"[WEB-STATS] Wrote snapshot → {path}")


# ── Entry point ──────────────────────────────────────────────────────────────

def update_web_stats(daily_cost, person_hours, report_date, cumulative=None,
                     docs_dir=DOCS_DIR):
    """
    Fail-safe entry point used by daily.py.

    daily_cost / person_hours — the combined day totals already computed
    by the pipeline. report_date — the ET date the figures describe
    (i.e. "yesterday"). cumulative — daily.py passes the figure it also
    puts on the social card (for_web!A2 + today), so page and card match;
    None falls back to fetch_cumulative_total() (for_web!A2). The CLI test
    mode passes --cumulative directly to skip the Sheets read.

    Returns the list of files updated, or None on failure/flag-off —
    in which case the previously committed pages stay in place.
    """
    if not USE_WEB_STATS:
        print("[WEB-STATS] USE_WEB_STATS=false — skipping static stats bake.")
        return None
    try:
        if cumulative is None:
            try:
                from og_card import fetch_cumulative_total
                cumulative = fetch_cumulative_total()
            except Exception as e:
                print(f"[WEB-STATS] Cumulative total unavailable — keeping previous: {e}")
                cumulative = None

        daily_str = _fmt_dollars(daily_cost)
        hours_str = _fmt_int(person_hours)
        weekday = report_date.strftime("%A")

        updated = []

        index_repl = {
            "DAILY": daily_str,
            "HOURS": hours_str,
            "DAILY_LABEL": (
                f"On {_long_date(report_date)}, New Jersey Transit delays cost "
                f"commuters and New York City employers more than:"
            ),
        }
        graphs_repl = {
            "DAILY": daily_str,
            "HOURS": hours_str,
            "DAILY_LABEL": f"{weekday}’s delay cost",
            "HOURS_LABEL": f"Person-hours lost {weekday}",
        }
        if cumulative is not None:
            index_repl["CUMULATIVE"] = _fmt_dollars(cumulative)
            graphs_repl["CUMULATIVE"] = _fmt_dollars(cumulative)

        index_path = os.path.join(docs_dir, "index.html")
        graphs_path = os.path.join(docs_dir, "graphs.html")
        if _bake_html(index_path, index_repl):
            updated.append(index_path)
        if _bake_html(graphs_path, graphs_repl):
            updated.append(graphs_path)

        snapshot_path = os.path.join(docs_dir, "data", "latest.json")
        _write_snapshot(snapshot_path, daily_cost, person_hours, report_date, cumulative)
        updated.append(snapshot_path)

        sitemap_path = os.path.join(docs_dir, "sitemap.xml")
        today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if _bump_sitemap(sitemap_path,
                         [SITE_URL, SITE_URL + "graphs.html"],
                         today_iso):
            updated.append(sitemap_path)

        return updated
    except Exception as e:
        print(f"[WEB-STATS] Static stats bake failed — committed pages stay as-is: {e}")
        return None


if __name__ == "__main__":
    # Local test, no credentials needed:
    #   python src/web_stats.py --daily 55000 --hours 1250 \
    #       --date 2026-07-13 --cumulative 8412067 [--docs /tmp/docs-copy]
    args = sys.argv[1:]
    opts = {}
    for i, a in enumerate(args):
        if a.startswith("--") and i + 1 < len(args):
            opts[a[2:]] = args[i + 1]

    if "daily" not in opts or "hours" not in opts:
        print(__doc__)
        sys.exit(1)

    result = update_web_stats(
        daily_cost=float(opts["daily"]),
        person_hours=float(opts["hours"]),
        report_date=date.fromisoformat(opts.get("date", date.today().isoformat())),
        cumulative=float(opts["cumulative"]) if "cumulative" in opts else None,
        docs_dir=opts.get("docs", DOCS_DIR),
    )
    print(f"Updated: {result}")
