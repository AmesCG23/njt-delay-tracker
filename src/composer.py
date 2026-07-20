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

# Some data-commentary claims (records, rankings) need a real denominator
# before they mean anything — "3rd-worst of 40 days" is weak. Gate those
# behind more history than the basic average comparison.
ANALYSIS_MIN_HISTORY = 60

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
            # Column Q (index 16): system-wide Penn Station person-hours. > 0
            # means that day had a Penn/Portal system-wide event. Powers the
            # "Penn frequency" data-commentary detector.
            "penn": (_parse_int(r[16]) or 0) > 0 if len(r) > 16 else False,
        })
    return out


# ── Fact sheet ──────────────────────────────────────────────────────────────────

def _fmt_cost(c):
    """$742,040 below $1M, $1.2M at or above — matches the style brief."""
    return f"${c / 1_000_000:.1f}M" if c >= 1_000_000 else f"${round(c):,}"


def _fmt_hours(h):
    return f"{round(h):,} hours"


def _worst_driver(all_events):
    """
    Human phrase for the biggest single source of lost hours, or None.

    A named rail line comes back as its own name. System-wide events are NOT
    lines — Penn Station is the NYC gateway (a hub-wide slowdown across every
    line), and a Hoboken diversion reroutes Midtown Direct service — so they
    get hub-appropriate phrasing the model can drop into a sentence without
    implying "the Penn Station line ran late."
    """
    tally = {}
    for ev in all_events:
        line = ev.get("line", "Unknown")
        if ev.get("system_wide") and not ev.get("line_suspension"):
            key = "_hoboken" if line == "System-Wide (Hoboken Diversion)" else "_penn"
        else:
            key = line
        hrs = (ev.get("estimated_riders") or 0) * (ev.get("delay_minutes") or 0) / 60
        tally[key] = tally.get(key, 0.0) + hrs
    if not tally:
        return None
    worst = max(tally, key=tally.get)
    if worst == "_penn":
        return "system-wide delays into and out of Penn Station"
    if worst == "_hoboken":
        return "Midtown Direct trains diverted to Hoboken"
    return worst  # a named rail line


def _heavier_rush(morning_totals, evening_totals):
    m = morning_totals.get("total_cost", 0)
    e = evening_totals.get("total_cost", 0)
    if m == 0 and e == 0:
        return None
    if abs(m - e) <= 0.15 * max(m, e):
        return "evenly split between the two rushes"
    return "morning rush" if m > e else "evening rush"


def _ordinal(n):
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _longest_run(vals, pred):
    """Longest run of consecutive items satisfying pred."""
    best = cur = 0
    for v in vals:
        cur = cur + 1 if pred(v) else 0
        best = max(best, cur)
    return best


def _select_analysis(candidates, last_post_text):
    """
    Pick the highest-salience data-commentary phrase, skipping one whose angle
    already appeared in the most recent post (anti-staleness). Falls back to
    the top by salience if every candidate would repeat yesterday.
    Each candidate is (score, phrase, signature).
    """
    if not candidates:
        return None
    ordered = sorted(candidates, key=lambda c: c[0], reverse=True)
    low = (last_post_text or "").lower()
    for _score, phrase, signature in ordered:
        if signature and signature.lower() in low:
            continue
        return phrase
    return ordered[0][1]


def _analysis_candidates(hours, avg_recent, prior, seq, streak_above,
                         streak_below, is_record, milestone_amt, penn_today):
    """
    The palette of data-commentary detectors. Each returns (salience, phrase,
    signature) when it fires. Only the single highest-salience one is surfaced
    (280 chars holds one line). Facts only — the model phrases them.
    """
    cand = []
    n_prior = len(prior)
    deep = n_prior >= ANALYSIS_MIN_HISTORY  # some claims need a real denominator

    # Cumulative milestone crossed today.
    if milestone_amt is not None:
        cand.append((90, f"the running total since launch just passed {_fmt_cost(milestone_amt)}",
                     "just passed"))

    # Penn / Portal recurring system-wide delays.
    penn_hist = [bool(r.get("penn")) for r in prior]
    window = penn_hist[-9:] + [penn_today]
    penn_k, penn_n = sum(window), len(window)
    if penn_today and penn_k >= 3:
        cand.append((85, f"system-wide Penn Station delays have now hit {penn_k} of the last {penn_n} days",
                     "Penn Station"))

    # Record chase — longest run of above-average days ever.
    longest_above = _longest_run(seq, lambda v: avg_recent and v > avg_recent)
    if deep and streak_above >= 4 and streak_above == longest_above:
        cand.append((80, f"the longest stretch of above-average days on record — {streak_above} and counting",
                     "longest stretch"))

    # Ranking among all days (richer than the binary record).
    worse = sum(1 for r in prior if r["hours"] > hours)
    lighter = sum(1 for r in prior if r["hours"] < hours)
    worst_rank, light_rank = worse + 1, lighter + 1
    if deep and not is_record and worst_rank <= 3:
        cand.append((75, f"the {_ordinal(worst_rank)}-worst day on record", "-worst day on record"))
    elif deep and not is_record and worst_rank <= 5:
        cand.append((55, f"the {_ordinal(worst_rank)}-worst day on record", "-worst day on record"))
    if deep and light_rank <= 3:
        cand.append((55, f"one of the lightest days on record — the {_ordinal(light_rank)}-lightest",
                     "lightest"))

    # Relatable equivalence on the biggest days.
    if avg_recent and hours >= 5 * avg_recent:
        n_days = round(hours / avg_recent)
        cand.append((70, f"as much lost time in a single day as {n_days} average days combined",
                     "average days combined"))

    # Calmest stretch — longest run of below-average days ever, or a shorter run.
    longest_below = _longest_run(seq, lambda v: avg_recent and v < avg_recent)
    if deep and streak_below >= 4 and streak_below == longest_below:
        cand.append((65, f"the calmest stretch on record — {streak_below} straight days under the average",
                     "calmest stretch"))
    elif streak_below in (3, 5, 7, 10, 14, 21):
        cand.append((45, f"the {_ordinal(streak_below)} straight day under the average — a calmer run",
                     "straight day under"))

    # Turning point — a notable streak just broke today. prev_* is the run of
    # above/below-average days among the prior days only (not counting today).
    prior_hours = [r["hours"] for r in prior]
    prev_above = 0
    for v in reversed(prior_hours):
        if avg_recent and v > avg_recent:
            prev_above += 1
        else:
            break
    prev_below = 0
    for v in reversed(prior_hours):
        if avg_recent and v < avg_recent:
            prev_below += 1
        else:
            break
    if avg_recent and hours < avg_recent and prev_above >= 3:
        cand.append((60, f"the first day under the average after {prev_above} straight above it",
                     "first day under"))
    elif avg_recent and hours > avg_recent and prev_below >= 3:
        cand.append((60, f"the first day back above average after {prev_below} straight below it",
                     "first day back"))

    # Momentum — the last week versus the monthly norm.
    last7 = prior_hours[-7:]
    if len(last7) >= 5 and avg_recent:
        tr = (sum(last7) / len(last7)) / avg_recent
        if tr >= 1.20:
            cand.append((50, f"the last week is running about {round((tr - 1) * 100)}% above the monthly average",
                         "the last week"))
        elif tr <= 0.80:
            cand.append((50, f"the last week is running about {round((1 - tr) * 100)}% below the monthly average",
                         "the last week"))

    # Above-average streak, surfaced only at escalation points (avoids
    # "3rd... 4th... 5th straight" monotony) and only when nothing richer fired.
    if not is_record and streak_above in (3, 5, 7, 10, 14, 21):
        cand.append((48, f"the {_ordinal(streak_above)} straight day above average", "straight day above"))

    return cand


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

    # Beat 1 (assessment of the day), beat 3 (comparison to the average), and
    # one optional piece of context. All facts — the model only phrases them.
    ratio = hours / avg_recent if (sufficient and avg_recent) else None
    avg_hours_str = _fmt_hours(avg_recent) if ratio is not None else None

    if ratio is None:
        # No baseline yet: assess by absolute size, no average comparison.
        vs_average = None
        analysis = None
        if hours >= 8000:
            assessment, scenario = "a heavy day", "bad_day"
        elif hours <= 2000:
            assessment, scenario = "a light day", "quiet_day"
        else:
            assessment, scenario = "an ordinary day", "typical_day"
    else:
        if ratio >= 1.15:
            vs_average = f"about {ratio:.1f}x the recent average"
        elif ratio <= 0.85:
            vs_average = "well below the recent average"
        else:
            vs_average = "right around the recent average"

        if is_record:
            assessment, scenario = "the worst day on record", "record_day"
        elif ratio >= 2.0:
            assessment, scenario = "one of the worst days lately", "bad_day"
        elif ratio >= 1.5:
            assessment, scenario = "a heavy day, well above average", "bad_day"
        elif ratio <= 0.5:
            assessment, scenario = "a notably light day", "quiet_day"
        elif ratio <= 0.85:
            assessment, scenario = "a lighter-than-average day", "quiet_day"
        else:
            assessment, scenario = "an average day", "typical_day"

        # Beat 5 — the single sharpest piece of data commentary, chosen from a
        # palette of detectors (trend / ranking / composition), skipping any
        # angle that already appeared in the most recent post.
        penn_today = any(
            ev.get("system_wide") and not ev.get("line_suspension")
            and ev.get("line") == "System-Wide (Penn Station)"
            for ev in all_events
        )
        candidates = _analysis_candidates(
            hours, avg_recent, prior, seq, streak_above, streak_below,
            is_record, milestone_amt, penn_today,
        )
        analysis = _select_analysis(candidates, prior[-1].get("text", "") if prior else "")

    return {
        "date": today,
        "day_name": yesterday_et.strftime("%A"),
        "assessment": assessment,
        "cost_str": cost_str,
        "hours_str": hours_str,
        "event_count": totals["event_count"],
        "line_count": len(totals.get("lines_affected", [])),
        "avg_hours_str": avg_hours_str,
        "vs_average": vs_average,
        "worst_driver": _worst_driver(all_events),
        "heavier_rush": _heavier_rush(morning_totals, evening_totals),
        "analysis": analysis,
        "scenario": scenario,
        "must_include": [hours_str, cost_str],
        "sufficient_history": sufficient,
    }


# ── Prompt + model call ─────────────────────────────────────────────────────────

def build_task_prompt(stats, examples, library, recent_posts=()):
    rules = list(library.get("hard_rules", []))
    banned = library.get("banned_phrases", [])
    if banned:
        rules.append(
            "Never use these words or phrases: " + ", ".join(f'"{b}"' for b in banned) + "."
        )
    rules_block = "\n".join(f"- {r}" for r in rules)
    example_block = "\n".join(f"- {s}" for s in examples)
    recent_block = "\n".join(f"- {p}" for p in recent_posts) or "- (none yet)"

    # Beat 3 depends on whether there's a baseline to compare against yet.
    if stats.get("avg_hours_str"):
        beat3 = (f"3. Compare to normal: the recent daily average is "
                 f"{stats['avg_hours_str']} ({stats['vs_average']}).")
    else:
        beat3 = "3. (No average established yet — skip the comparison to normal.)"

    # Beat 5 — one line of data commentary, only when a detector fired.
    if stats.get("analysis"):
        beat5 = (f"5. Add one plain line of data commentary putting the day in a "
                 f"bigger-picture context: {stats['analysis']}. Work it in "
                 f"naturally — it can stand in for the step-3 comparison if they overlap.")
    else:
        beat5 = "5. (No standout pattern today — no data-commentary line needed.)"

    # Secondary detail — used only if it fits and sharpens the point.
    extras = []
    if stats.get("worst_driver"):
        extras.append(f"Hardest hit: {stats['worst_driver']}")
    if stats.get("heavier_rush"):
        extras.append(f"Heavier period: {stats['heavier_rush']}")
    extras_block = "\n".join(f"- {e}" for e in extras) or "- (none)"

    return f"""Write ONE short Bluesky post about {stats['day_name']}'s NJ Transit delays.
Follow this framework, in order — but VARY your word choice day to day and let the
numbers carry the message. State the facts plainly; do not editorialize. Keep the
whole post under 280 characters: if it runs long, drop the least important element
rather than cramming everything in.

1. Open with a plain assessment of the day — it was {stats['assessment']}. Put it in your own words.
2. State the scale: {stats['event_count']} delays and {stats['hours_str']} of riders' time lost.
{beat3}
4. Give the cost: {stats['cost_str']} in lost productive time to commuters and New York City employers.
{beat5}

Secondary detail — weave in AT MOST ONE, and only if it fits and sharpens the point:
{extras_block}

Examples of the right structure and plain tone (do NOT copy them — match the
plainness, and vary from these and from your recent posts):
{example_block}

Your most recent posts — make today's clearly different in wording:
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
    examples = library.get("examples", [])
    banned = library.get("banned_phrases", [])

    # Most recent actual posts, newest first — shown to the model so it varies
    # from them, and checked against for near-duplication.
    recent_posts = [
        h["text"] for h in sorted(history, key=lambda r: r["date"], reverse=True)
        if h.get("text")
    ][:RECENT_POSTS_SHOWN]

    prompt = build_task_prompt(stats, examples, library, recent_posts)

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
        "assessment": "one of the worst days lately",
        "cost_str": "$742,040", "hours_str": "16,864 hours",
        "event_count": 30, "line_count": 5,
        "avg_hours_str": "9,400 hours", "vs_average": "about 1.8x the recent average",
        "worst_driver": "Northeast Corridor", "heavier_rush": "evening rush",
        "analysis": "the 3rd-worst day on record", "scenario": "bad_day",
        "must_include": ["16,864 hours", "$742,040"],
        "sufficient_history": True,
    }
    demo_recent = [
        "Yesterday was an average day on NJ Transit: 23 delays, 4,120 hours lost...",
        "A lighter day than usual: 8 delays, 720 hours lost, well under average...",
    ]
    lib = load_library()
    print("SYSTEM:\n" + lib["style_brief"] + "\n")
    print("USER:\n" + build_task_prompt(
        demo_stats, lib["examples"], lib, demo_recent
    ))
