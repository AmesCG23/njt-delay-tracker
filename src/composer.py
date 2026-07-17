"""
composer.py — AI-composed daily post
------------------------------------
Drafts the daily Bluesky post with Claude instead of the fixed template.

It reads the running history from the Tweet_log tab, computes a fact sheet
of numbers in plain Python (never the model), classifies the day into one
scenario, hands Claude only the matching sample posts plus the fact sheet,
and asks for one new post in the same voice. The composed post is then
validated mechanically. If ANY step fails — the flag is off, a red-line
keyword is present, the API errors, or validation fails twice — this module
returns None and daily.py falls back to the fixed template in
format_tweet(). The composer can improve the post but can never cost a post.

╔══════════════════════════════════════════════════════════════════════════╗
║  ROLLBACK — if the composed posts go badly:                              ║
║                                                                          ║
║  FASTEST (no code change, no deploy):                                    ║
║      set   USE_COMPOSER=false   in .github/workflows/daily.yml           ║
║      Posts revert to the fixed template on the next run.                 ║
║                                                                          ║
║  PERMANENT:                                                              ║
║      flip the USE_COMPOSER default below to "false", or revert the PR    ║
║      that added this feature. The template path (format_tweet) is        ║
║      untouched and fully self-sufficient.                                ║
╚══════════════════════════════════════════════════════════════════════════╝

Only the model call (anthropic) and the history read (gspread, via logger)
touch the network; both are imported lazily so this module's pure logic can
be imported and tested without those packages installed.
"""

import os
import re
import json
from datetime import date

# ── Configuration ──────────────────────────────────────────────────────────────

# Master on/off switch. Defaults ON; set USE_COMPOSER=false in the workflow
# env to instantly roll back to the fixed template — see the box above.
USE_COMPOSER = os.environ.get("USE_COMPOSER", "true").lower() == "true"

# Ames chose Sonnet for the writing quality. One call/day ≈ a few cents/month.
MODEL = "claude-sonnet-5"

# Records, averages, streaks, and milestones are only claimed once at least
# this many prior days exist. Before then, posts stick to yesterday's own
# numbers with no comparisons.
MIN_HISTORY_DAYS = 30

# Hard character cap for a composed post (Bluesky's limit is 300; the style
# brief asks for ≤280). A post over this is rejected, not truncated.
MAX_POST_CHARS = 295

_LIBRARY_PATH = os.path.join(os.path.dirname(__file__), "post_library.json")

_RETRY_SUFFIX = (
    "\n\nYour previous attempt was rejected. Write a fresh version: one "
    "paragraph under 280 characters, containing both required figures exactly, "
    "plain text only (no hashtags, links, emojis, or the word \"person-hours\"), "
    "with a distinctly different opening and wording from your recent posts, and "
    "none of the banned phrases."
)

# How many of the most recent posts to show the model (for anti-repetition)
# and to check new drafts against for near-duplication.
RECENT_POSTS_SHOWN = 6


# ── Library ────────────────────────────────────────────────────────────────────

def load_library():
    with open(_LIBRARY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ── History (read-only; safe in DRY_RUN) ────────────────────────────────────────

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_cost(cell):
    """Parse a '$12,345.67' Tweet_log cost cell into a float, or None."""
    if not cell:
        return None
    try:
        return float(str(cell).replace("$", "").replace(",", "").strip())
    except ValueError:
        return None


def _parse_int(cell):
    try:
        return int(float(str(cell).replace(",", "").strip()))
    except (ValueError, TypeError):
        return None


def fetch_history(before_date=None):
    """
    Read prior daily rows from the Tweet_log tab.

    Returns a list of {"date", "hours", "cost"} dicts. Rows are validated by
    shape (column F looks like a date, column G parses as an int), so this is
    robust whether or not the tab has a header row. Any Sheets failure returns
    an empty list — the composer then simply has no history to compare against.
    """
    from logger import get_sheet_client  # lazy: pulls in gspread

    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        return []

    try:
        client = get_sheet_client()
        spreadsheet = client.open_by_key(sheet_id)
        tab = spreadsheet.worksheet("Tweet_log")
        rows = tab.get_all_values()
    except Exception as e:  # noqa: BLE001 — degrade gracefully, never block a post
        print(f"[COMPOSER] Could not read Tweet_log history: {e}")
        return []

    out = []
    for r in rows:
        report_date = r[5].strip() if len(r) > 5 else ""
        if not _DATE_RE.match(report_date):
            continue  # skips header rows and blanks
        if before_date and report_date >= before_date:
            continue
        hours = _parse_int(r[6]) if len(r) > 6 else None
        if hours is None:
            continue
        out.append({
            "date": report_date,
            "hours": hours,
            "cost": _parse_cost(r[2]) if len(r) > 2 else None,
            "text": r[1].strip() if len(r) > 1 else "",  # column B: the post itself
        })
    return out


# ── Fact sheet ──────────────────────────────────────────────────────────────────

def _fmt_cost(c):
    """$742,040 below $1M, $1.2M at or above — matches the style brief."""
    return f"${c / 1_000_000:.1f}M" if c >= 1_000_000 else f"${round(c):,}"


def _fmt_hours(h):
    return f"{round(h):,} hours"


def _worst_line(all_events):
    """Human-readable name of the line with the most lost hours, or None."""
    tally = {}
    for ev in all_events:
        line = ev.get("line", "Unknown")
        if ev.get("system_wide") and not ev.get("line_suspension"):
            line = ("Hoboken diversions"
                    if line == "System-Wide (Hoboken Diversion)"
                    else "Penn Station")
        hrs = (ev.get("estimated_riders") or 0) * (ev.get("delay_minutes") or 0) / 60
        tally[line] = tally.get(line, 0.0) + hrs
    if not tally:
        return None
    return max(tally, key=tally.get)


def _heavier_rush(morning_totals, evening_totals):
    m = morning_totals.get("total_cost", 0)
    e = evening_totals.get("total_cost", 0)
    if m == 0 and e == 0:
        return None
    if abs(m - e) <= 0.15 * max(m, e):
        return "evenly split between the two rushes"
    return "morning rush" if m > e else "evening rush"


def compute_stats(yesterday_et, totals, morning_totals, evening_totals,
                  all_events, history):
    """
    Build the deterministic fact sheet handed to the model. Every number the
    post is allowed to use originates here; the model only phrases them.
    """
    today = yesterday_et.isoformat()
    cost = totals["total_cost"]
    hours = totals["total_person_hours"]

    cost_str = _fmt_cost(cost)
    hours_str = _fmt_hours(hours)

    prior = sorted(
        (h for h in history if h["date"] and h["date"] < today),
        key=lambda r: r["date"],
    )
    sufficient = len(prior) >= MIN_HISTORY_DAYS

    recent = prior[-30:]
    avg_recent = sum(r["hours"] for r in recent) / len(recent) if recent else 0
    max_hours = max((r["hours"] for r in prior), default=0)
    is_record = sufficient and hours > max_hours

    # Streaks include today. Computed against the recent average (which
    # excludes today, so a day can't skew its own baseline).
    seq = [r["hours"] for r in prior] + [hours]
    streak_above = streak_below = 0
    for v in reversed(seq):
        if avg_recent and v > avg_recent:
            streak_above += 1
        else:
            break
    for v in reversed(seq):
        if avg_recent and v < avg_recent:
            streak_below += 1
        else:
            break

    cum_before = sum(r["cost"] for r in prior if r["cost"] is not None)
    cum_now = cum_before + cost
    milestone_amt = None
    for boundary in (25e6, 50e6, 75e6, 100e6, 125e6, 150e6, 175e6, 200e6):
        if cum_before < boundary <= cum_now:
            milestone_amt = boundary

    # Scenario + the single comparison fact (if any) the model may cite.
    facts = []
    if not sufficient:
        # No baseline yet: classify by absolute size, allow no comparisons.
        if hours >= 8000:
            scenario = "bad_day"
        elif hours <= 2000:
            scenario = "quiet_day"
        else:
            scenario = "typical_day"
    else:
        ratio = hours / avg_recent if avg_recent else 1.0
        if is_record:
            scenario = "record_day"
            facts.append("This is the worst single day for delays on record.")
        elif milestone_amt is not None:
            scenario = "milestone"
            facts.append(
                f"The running total of measured delay costs since launch has "
                f"just passed {_fmt_cost(milestone_amt)} (now about {_fmt_cost(cum_now)})."
            )
        elif streak_above >= 3:
            scenario = "trend_rising"
            facts.append(f"That's {streak_above} straight days above the 30-day average.")
        elif streak_below >= 3:
            scenario = "trend_falling"
            facts.append(f"That's {streak_below} straight days below the 30-day average.")
        elif ratio >= 1.5:
            scenario = "bad_day"
            facts.append(f"Yesterday was about {ratio:.1f}x the recent daily average.")
        elif ratio <= 0.5:
            scenario = "quiet_day"
            facts.append("Yesterday was well below the recent daily average.")
        else:
            scenario = "typical_day"
            facts.append("That's about a normal day by recent standards.")

    return {
        "date": today,
        "day_name": yesterday_et.strftime("%A"),
        "cost_str": cost_str,
        "hours_str": hours_str,
        "event_count": totals["event_count"],
        "line_count": len(totals.get("lines_affected", [])),
        "worst_line": _worst_line(all_events),
        "heavier_rush": _heavier_rush(morning_totals, evening_totals),
        "scenario": scenario,
        "comparison_facts": facts,
        "must_include": [hours_str, cost_str],
        "sufficient_history": sufficient,
    }


# ── Prompt + model call ─────────────────────────────────────────────────────────

def build_task_prompt(stats, samples, library, recent_posts=()):
    rules = list(library.get("hard_rules", []))
    rules.append(
        "Vary your opening: do not start with the same word or construction as "
        "your recent posts below."
    )
    banned = library.get("banned_phrases", [])
    if banned:
        rules.append(
            "Never use these words or phrases: " + ", ".join(f'"{b}"' for b in banned) + "."
        )
    rules_block = "\n".join(f"- {r}" for r in rules)

    sample_block = "\n".join(f"- {s}" for s in samples)
    facts_block = "\n".join(f"- {f}" for f in stats["comparison_facts"]) or (
        "- (none — do NOT compare to averages, records, streaks, or past days)"
    )
    recent_block = "\n".join(f"- {p}" for p in recent_posts) or "- (none yet)"

    return f"""Write ONE Bluesky post summarizing yesterday's NJ Transit delays.

Yesterday was {stats['day_name']}.

USE THESE FIGURES EXACTLY — copy the strings verbatim, do not recompute:
- Cost: {stats['cost_str']}
- Time lost: {stats['hours_str']}
- Delay events: {stats['event_count']}
- Lines affected: {stats['line_count']}
- Hardest-hit line: {stats['worst_line'] or 'n/a'}
- Heavier period: {stats['heavier_rush'] or 'n/a'}

You MAY use at most ONE of these comparison facts, or none. Do not invent others:
{facts_block}

Scenario: {stats['scenario']}. Past posts in this scenario — match their VOICE
and rhythm, do NOT copy them:
{sample_block}

Your most RECENT posts (any scenario) — make today's clearly DIFFERENT from
these in its opening and overall wording, not just in the numbers:
{recent_block}

Rules:
{rules_block}
- The post MUST contain the exact strings "{stats['hours_str']}" and "{stats['cost_str']}".
- Output ONLY the post text — no preamble, no surrounding quotation marks, no explanation."""


def _call_model(system_text, user_text):
    import anthropic  # lazy

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    message = client.messages.create(
        model=MODEL,
        max_tokens=300,
        system=system_text,
        messages=[{"role": "user", "content": user_text}],
        # Disable thinking via extra_body so the response is a single text
        # block, compatible with the pinned SDK. (Passing a typed `thinking`
        # kwarg could break on older SDK versions; extra_body is a stable
        # passthrough.)
        extra_body={"thinking": {"type": "disabled"}},
    )
    for block in message.content:
        if getattr(block, "type", None) == "text":
            return block.text
    return ""


def _clean(text):
    text = (text or "").strip()
    if len(text) >= 2 and text[0] in "\"'" and text[-1] == text[0]:
        text = text[1:-1].strip()
    return text


def _norm_words(s):
    return re.findall(r"[a-z0-9]+", (s or "").lower())


def _too_similar(text, recent_posts, opening_words=4, jaccard_max=0.6):
    """
    True if `text` reads like a near-duplicate of a recent post: it opens with
    the same first few words as the most recent post, or shares too large a
    fraction of its vocabulary with any of the last three. Conservative on
    purpose — genuinely different framings of similar facts still pass.
    """
    words = _norm_words(text)
    if not words:
        return False
    cand_open, cand_set = words[:opening_words], set(words)
    for i, prev in enumerate(recent_posts[:3]):
        pw = _norm_words(prev)
        if not pw:
            continue
        if i == 0 and len(cand_open) == opening_words and pw[:opening_words] == cand_open:
            return True
        union = cand_set | set(pw)
        if union and len(cand_set & set(pw)) / len(union) > jaccard_max:
            return True
    return False


def validate(text, stats, banned_phrases=(), recent_posts=()):
    """Mechanical checks. A composed post must pass all of these."""
    if not text:
        return False
    if len(text) > MAX_POST_CHARS:
        return False
    low = text.lower()
    if "#" in text or "http" in low:
        return False
    if "person-hour" in low or "person-minute" in low:
        return False
    for phrase in banned_phrases:
        if phrase.lower() in low:
            return False
    for needed in stats["must_include"]:
        if needed not in text:
            return False
    if _too_similar(text, recent_posts):
        return False
    return True


# ── Red-line guard ──────────────────────────────────────────────────────────────

def hits_redline(all_events, keywords):
    """
    Return the first red-line keyword found in any event's cause or raw text,
    or None. On a match the composer is skipped entirely: a witty post on a
    day involving injury, fatality, or police activity is the one outcome
    this whole design must avoid.
    """
    haystack = " ".join(
        f"{ev.get('cause', '')} {ev.get('raw_text', '') or ev.get('text', '')}"
        for ev in all_events
    ).lower()
    for kw in keywords:
        if re.search(r"\b" + re.escape(kw.lower()) + r"\b", haystack):
            return kw
    return None


# ── Public entry point ──────────────────────────────────────────────────────────

def compose_post(yesterday_et, totals, morning_totals, evening_totals, all_events):
    """
    Return a composed post string, or None to signal "use the template."
    Never raises for expected failures; daily.py also wraps this in try/except
    as a final backstop.
    """
    if not USE_COMPOSER:
        print("[COMPOSER] USE_COMPOSER is off — using the fixed template.")
        return None

    try:
        library = load_library()
    except Exception as e:  # noqa: BLE001
        print(f"[COMPOSER] Could not load post library: {e} — using template.")
        return None

    hit = hits_redline(all_events, library.get("redline_keywords", []))
    if hit:
        print(f"[COMPOSER] Red-line keyword '{hit}' present — using template.")
        return None

    history = fetch_history(before_date=yesterday_et.isoformat())
    stats = compute_stats(yesterday_et, totals, morning_totals,
                          evening_totals, all_events, history)
    samples = library["samples"].get(
        stats["scenario"], library["samples"]["typical_day"]
    )
    banned = library.get("banned_phrases", [])

    # Most recent actual posts, newest first — shown to the model so it varies
    # from them, and checked against for near-duplication.
    recent_posts = [
        h["text"] for h in sorted(history, key=lambda r: r["date"], reverse=True)
        if h.get("text")
    ][:RECENT_POSTS_SHOWN]

    prompt = build_task_prompt(stats, samples, library, recent_posts)

    for attempt, extra in enumerate(("", _RETRY_SUFFIX)):
        text = _clean(_call_model(library["style_brief"], prompt + extra))
        if validate(text, stats, banned, recent_posts):
            label = "retry" if attempt else "first try"
            print(f"[COMPOSER] Composed post ({stats['scenario']}, {label}, "
                  f"{len(text)} chars).")
            return text
        print(f"[COMPOSER] Attempt {attempt + 1} failed validation.")

    print("[COMPOSER] Validation failed twice — using template.")
    return None


# ── Prompt preview (no network) ─────────────────────────────────────────────────
if __name__ == "__main__":
    # Eyeball the prompt for a synthetic bad day without calling the API.
    demo_stats = {
        "date": "2026-03-31", "day_name": "Monday",
        "cost_str": "$742,040", "hours_str": "16,864 hours",
        "event_count": 30, "line_count": 5,
        "worst_line": "Northeast Corridor", "heavier_rush": "evening rush",
        "scenario": "bad_day",
        "comparison_facts": ["Yesterday was about 1.8x the recent daily average."],
        "must_include": ["16,864 hours", "$742,040"],
        "sufficient_history": True,
    }
    demo_recent = [
        "Rough one out there Monday. NJ Transit delays cost commuters 11,240 hours...",
        "Credit where due: Tuesday was quiet by NJ Transit standards. 940 hours...",
    ]
    lib = load_library()
    print("SYSTEM:\n" + lib["style_brief"] + "\n")
    print("USER:\n" + build_task_prompt(
        demo_stats, lib["samples"]["bad_day"], lib, demo_recent
    ))
