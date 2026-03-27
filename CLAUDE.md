# CLAUDE.md — NJ Transit Delay Cost Tracker

This file gives Claude instant project context. Paste it at the start of any new conversation to resume work without re-explaining decisions.

---

## What This Project Is

An automated pipeline that monitors NJ Transit commuter rail delays by reading public Bluesky alert bot accounts, estimates the economic cost in lost worker time, logs each event to a Google Sheet, and posts twice-daily summary posts to a Bluesky bot account.

Posts go out at **10:30am** (morning rush summary) and **9:00pm** (evening rush summary) on weekdays. Each run is a single self-contained process: fetch, interpret, calculate, aggregate, log, post.

**Owner:** Ames (non-technical background — plain English explanations always, code second)

---

## Architecture: Single-Pass Pipeline

The pipeline runs **twice a day** via GitHub Actions. There is no continuous polling, no staging file, no collect/summarize split. Each run fetches everything it needs from Bluesky in one shot.

```
GitHub Actions fires at 15:00 UTC (morning) or 02:00 UTC (evening)
       |
       v
main.py
  |
  ├── aggregator.get_utc_window(period)
  |     Calculates the correct UTC time window for the rush hour,
  |     accounting for EDT vs EST automatically via DST calendar rules.
  |     IMPORTANT: Uses ET date (not UTC date) to avoid midnight rollover bug.
  |     Evening job fires at 02:00 UTC = 10pm ET, which is already the
  |     next calendar day in UTC — fixed by offsetting before taking .date()
  |
  ├── watcher.get_window_delays(start_utc, end_utc)
  |     Fetches up to 100 posts per account from 5 Bluesky accounts.
  |     Filters to posts within the time window.
  |     Filters to rail delays only (keyword + line signal matching).
  |     Cross-account dedup: same text from two accounts = one event.
  |     No state file. Each run is self-contained.
  |
  ├── interpreter.interpret_alert(text)  [× N events]
  |     Claude Haiku extracts: line, delay_minutes, direction, cause,
  |     train_number, is_cancellation.
  |
  ├── calculator.calculate_cost(event)   [× N events]
  |     Looks up peak riders by line.
  |     Formula: riders × (delay_minutes / 60) × $24.00
  |     Cancellations = 60-minute assumed delay.
  |     All events treated as peak (we only run during rush hours).
  |
  ├── aggregator.deduplicate_by_train(events)
  |     Groups by (train_number, date). Keeps HIGHEST delay per train.
  |     e.g. train #3876 at 15 min then 25 min → counts once at 25 min.
  |
  ├── aggregator.calculate_totals(deduplicated)
  |     total_person_minutes = sum of (delay_minutes × riders)
  |     total_cost = sum of dollar estimates
  |
  ├── logger.log_delay(event)            [× N deduplicated events]
  |     Appends row to Google Sheet Tab 1: Event Log.
  |
  └── post_to_bluesky(post_text)
        Posts the summary to the bot account.
```

---

## Post Format

```
Good [morning/evening], fellow commuters! Today, NJ Transit delayed us
for a total of [TOTAL PERSON-MINUTES] during the [morning/afternoon] rush.
City employers lost [TOTAL COST] in productive working time.
([N] delay events across [N] lines)
```

---

## Tech Stack

| Component | Tool | Notes |
|---|---|---|
| Language | Python 3.11 | All code in /src/ |
| Scheduler | GitHub Actions | 2 cron jobs, ~60 min/month |
| Data source | Bluesky public API | No auth needed for reading |
| AI parsing | Claude API — Haiku | ~$1-3/month |
| Spreadsheet | Google Sheets API (gspread) | |
| Social posting | Bluesky AT Protocol (atproto) | |
| Hosting | GitHub Actions | Free tier |

---

## Data Sources: Bluesky Alert Accounts

| Account | Coverage |
|---|---|
| `njmetroalert.bsky.social` | All lines — primary source |
| `njtransit--nec.bsky.social` | NEC only — double coverage (note double dash) |
| `njtransit-me.bsky.social` | Morris & Essex |
| `njtransit-mobo.bsky.social` | Montclair-Boonton |
| `njtransit-mbpj.bsky.social` | Main/Bergen County |

NJCL, RVL, PVL, ACL covered by njmetroalert only.

---

## Key Decisions — Locked In

### Value of Travel Time (VTTS)
- **Rate: $24.00/hour**
- USDOT formula (50% of median HHI) applied to NJ median HHI $99,781 (2023 Census ACS)
- National USDOT default $18.80/hour disclosed as lower bound on website

### Ridership
- **All events use peak figures** — pipeline only runs during rush hours
- No time-band logic (was removed as unnecessary complexity)

### Minimum Delay Threshold
- **10 minutes.** Delays under 10 min are ignored.

### Cancellations
- **60-minute assumed delay**, regardless of where on the line
- Reflects realistic wait for next train during peak hours
- Changed from 45 min after review — 60 min is more accurate

### Deduplication Rule
- Same train appearing multiple times → count **once at the highest observed delay**
- Keyed on (train_number, date) in ET timezone
- Trains without a number: each alert treated as unique

### Direction
- Not coded. Alert text too inconsistent for reliable parsing. Skipped by design.

### Posting Cadence
- **Two posts per day** (weekdays only): ~10:30am and ~9pm ET
- No per-delay posts — aggregate summaries only

---

## Ridership Table (peak, all events)

```python
RIDERS_PER_TRAIN = {
    "Northeast Corridor": 825,
    "North Jersey Coast":  500,
    "Morris & Essex":      550,
    "Montclair-Boonton":   415,
    "Main/Bergen County":  450,
    "Raritan Valley":      450,
    "Pascack Valley":      315,
    "Port Jervis":         300,
    "Gladstone Branch":    300,
    "Atlantic City":       260,
    "Unknown":             400,   # fallback
}
```

---

## Google Sheet Structure

**Single file, two tabs.**

### Tab 1: Event Log (Python writes here)
Columns: Date, Time, Line, Train #, Direction, Time Band, Delay Minutes, Estimated Riders, Dollar Estimate, Cause, Is Cancellation, Raw Alert Text, Posted to Bluesky

Note: "Posted to Bluesky" column always shows "No" for individual rows — this is correct. Individual events are never posted; only the summary is. Column is a legacy artifact.

**Last validated checkpoint: row 126 (March 27, 2026)**
Hand-check in progress: comparing sheet rows against observed Bluesky alerts for the same window.

### Tab 2: Totals (formula-driven, Python reads B2 only)
- A2: `Annual Total`
- B2: `=SUM('Event Log'!I:I)`

Renaming the Google Sheet file is safe (code uses Sheet ID). Renaming the tabs would break things.

---

## GitHub Actions Schedule

```yaml
# Morning summary: 15:00 UTC (11am EDT / 10am EST)
- cron: "0 15 * * 1-5"

# Evening summary: 02:00 UTC (10pm EDT / 9pm EST)
- cron: "0 2 * * 2-6"
```

~60 minutes/month — well within GitHub's 2,000 free tier.

---

## Environment Variables / GitHub Secrets

| Secret | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key |
| `BLUESKY_HANDLE` | Bot handle, e.g. `njtdelaycost.bsky.social` |
| `BLUESKY_PASSWORD` | Bluesky app password (Settings → Privacy & Security → App Passwords) |
| `GOOGLE_SHEET_ID` | ID from sheet URL (between /d/ and /edit) |
| `GOOGLE_CREDENTIALS_JSON` | Full contents of Google service account .json file |

### Runtime env vars (set by workflow)
| Variable | Values | Description |
|---|---|---|
| `PERIOD` | `morning` / `evening` | Which rush window to summarize |
| `DRY_RUN` | `true` / `false` | Skip posting and logging. Defaults to true for manual triggers. |

---

## Repository Structure

```
/
├── CLAUDE.md
├── README.md
├── requirements.txt
├── .github/
│   └── workflows/
│       └── run_pipeline.yml
└── src/
    ├── main.py          ← single-pass orchestrator
    ├── watcher.py       ← Bluesky fetcher (window-based, stateless)
    ├── interpreter.py   ← Claude Haiku parser
    ├── calculator.py    ← cost math + ridership table
    ├── logger.py        ← Google Sheets writer
    └── aggregator.py    ← DST-aware window calc, dedup, totals, post formatter
```

Note: `staging.py` and `data/` directory were removed in the architectural
simplification. No state files are needed — each run fetches from Bluesky directly.

---

## Known Bugs Fixed

### Evening window date rollover (fixed March 27, 2026)
The evening job fires at 02:00 UTC, which is already the next calendar day
in UTC. The original code used `now_utc.date()` to build the window, so it
looked for posts on the wrong (future) day and found nothing.

Fix: use ET date instead of UTC date when building the window:
```python
today = (now_utc - timedelta(hours=offset)).date()  # correct
# not: today = now_utc.date()                        # wrong
```

### Seed mode / first-run spam (resolved)
On first deploy, the watcher found all historical posts as "new" and posted ~50
old alerts. Since we no longer use a seen_alerts state file, this isn't possible
in the new architecture — each run only looks at posts within the current rush
window, so there's no concept of "old" vs "new" posts.

---

## Methodology Statement (website copy, approved)

> This tracker estimates the economic cost of NJ Transit commuter rail delays
> using the U.S. Department of Transportation's standard Value of Travel Time
> methodology, adjusted to reflect New Jersey's median household income
> ($99,781, 2023 U.S. Census). We apply a rate of $24.00 per hour. The national
> USDOT default of $18.80/hour is disclosed as a lower-bound alternative.
>
> Rider counts per train are estimates based on system-wide annual ridership
> distributed proportionally across lines. They are not real-time measurements.
> Only delays of 10 minutes or more are included. This tracker covers commuter
> rail only.
>
> These figures represent estimated opportunity costs, not cash losses. Data is
> sourced from unofficial Bluesky bots mirroring NJ Transit's public MyTransit
> alert system. This project is not affiliated with NJ Transit.

---

## Project Status (as of March 27, 2026)

| Phase | Status | Notes |
|---|---|---|
| 1 — Brainstorming | ✅ Complete | |
| 2 — Research | ✅ Complete | NJT API abandoned; Bluesky bots used |
| 3 — Design | ✅ Complete | All key decisions locked |
| 4 — Coding: Mechanism | ✅ Complete | Pipeline running live, bugs resolved |
| 5 — Coding: Website | 🟡 Mostly complete | index.html done; data.json export TBD |
| 6 — Polish | 🟡 In progress | |
| 7 — Testing | 🟡 In progress | Hand-check of sheet data pending |
| 8 — Launch | ⬜ Not started | |

### What's working
- Bluesky polling (window-based, stateless) ✅
- DST-aware time window calculation ✅
- Claude interpretation ✅
- Cost calculation (peak-only, 60-min cancellations) ✅
- Deduplication by train number ✅
- Google Sheets logging ✅
- Summary post formatting ✅
- Bluesky posting ✅
- Two-job GitHub Actions schedule ✅

### Immediate next steps
1. Hand-check sheet rows from row 126 onward against observed Bluesky alerts
2. Validate dedup is working correctly (compare raw alert count vs sheet row count)
3. Connect website data.json export (Phase 5 remaining work)
4. Update methodology white paper to reflect 60-min cancellation assumption

---

## Cost Estimate (monthly)
- GitHub Actions: free (~60 min/month)
- Claude API (Haiku): ~$1–3
- Google Sheets API: free
- Bluesky API: free
- **Total: ~$1–3/month**

---

*Last updated: March 27, 2026. Generated collaboratively with Claude (Anthropic).*
