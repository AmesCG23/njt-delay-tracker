# CLAUDE.md — NJ Transit Delay Cost Tracker

This file gives Claude instant project context. Paste it at the start of any new conversation to resume work without re-explaining decisions.

---

## What This Project Is

An automated pipeline that monitors NJ Transit commuter rail delays by reading public Bluesky alert bot accounts, estimates the economic cost in lost worker time, logs each event to a Google Sheet, and posts twice-daily summary posts to a Bluesky bot account.

Posts go out at **10:30am** (morning rush summary) and **9:00pm** (evening rush summary) on weekdays. The system accumulates delays silently throughout each rush window, then posts one punchy aggregate summary per window rather than one post per delay event.

**Owner:** Ames (non-technical background — plain English explanations always, code second)

---

## Architecture: Two-Mode Pipeline

### MODE=collect (runs every 15 min during rush hours)

```
Bluesky alert accounts
       |
       v
Station 1 — WATCHER (watcher.py)
  Polls 5 Bluesky accounts for new rail delay posts.
  Deduplicates by post URI (seen_alerts.json).
  Filters: rail lines only, delay keywords only.
  Cross-account dedup: same text from two accounts = one event.
       |
       v
Station 2 — INTERPRETER (interpreter.py)
  Passes raw post text to Claude Haiku.
  Extracts: line, delay_minutes, direction, cause, train_number, is_cancellation.
  Returns structured JSON.
       |
       v
Station 3 — CALCULATOR (calculator.py)
  Looks up riders from RIDERS_PER_TRAIN table (line + time band).
  Formula: riders × (delay_minutes / 60) × $24.00 = dollar_estimate
       |
       v
Station 4 — LOGGER (logger.py)
  Appends a row to Google Sheet Tab 1: Event Log.
  Reads running total from Tab 2: Totals (formula-driven).
       |
       v
Station 5 — STAGING (staging.py)
  Appends delay to data/delay_log.json.
  This file accumulates throughout the rush window
  for later aggregation by the summarize pipeline.
```

### MODE=summarize (runs at 10:30am and 9:00pm ET)

```
data/delay_log.json
       |
       v
AGGREGATOR (aggregator.py)
  1. Filters entries by time window (morning: 5am-11am ET, evening: 3pm-9:30pm ET)
  2. Deduplicates by (train_number, date) — keeps HIGHEST delay per train
     e.g. train #3876 seen at 15 min then 25 min → counts once at 25 min
  3. Calculates totals:
     - total_person_minutes = sum of (delay_minutes × riders) per train
     - total_cost = sum of recalculated dollar estimates
  4. Formats summary post
  5. Clears the window from delay_log.json
       |
       v
Posts to Bluesky bot account
```

---

## Post Format

```
Good [morning/evening], fellow commuters! Today, NJ Transit delayed us
for a total of [TOTAL PERSON-MINUTES] during the [morning/afternoon] rush.
City employers lost [TOTAL COST] in productive working time.
([N] delay events across [N] lines)
```

Example:
```
Good morning, fellow commuters! Today, NJ Transit delayed us for a
total of 14,300 person-minutes during the morning rush. City employers
lost $9,867 in productive working time. (12 delay events across 4 lines)
```

---

## Tech Stack

| Component | Tool | Notes |
|---|---|---|
| Language | Python 3.11 | All code in /src/ |
| Scheduler | GitHub Actions | Rush-hours-only cron |
| Data source | Bluesky public API | No auth needed for reading |
| AI parsing | Claude API — Haiku | ~$1-3/month |
| Spreadsheet | Google Sheets API (gspread) | |
| Social posting | Bluesky AT Protocol (atproto) | |
| Hosting | GitHub Actions | Free tier: ~540 min/month |

---

## Data Sources: Bluesky Alert Accounts

| Account | Coverage |
|---|---|
| `njmetroalert.bsky.social` | All lines — primary source |
| `njtransit--nec.bsky.social` | NEC only — double coverage (note double dash) |
| `njtransit-me.bsky.social` | Morris & Essex |
| `njtransit-mobo.bsky.social` | Montclair-Boonton |
| `njtransit-mbpj.bsky.social` | Main/Bergen County |

These are unofficial bots mirroring the NJT MyTransit alert system. Not affiliated with NJ Transit.

Lines not yet on Bluesky (NJCL, RVL, PVL, ACL) are covered by `njmetroalert`.

---

## Key Decisions — Locked In

### Value of Travel Time (VTTS)
- **Primary rate: $24.00/hour**
- USDOT standard formula (50% of median hourly household income) applied to NJ median HHI of $99,781 (2023 Census ACS)
- National USDOT default $18.80/hour disclosed as lower bound on website
- Source: U.S. Census Bureau 2023 ACS; USDOT Revised Departmental Guidance on Valuation of Travel Time

### Minimum Delay Threshold
- **10 minutes.** Delays under 10 min are not logged or posted.

### Scope
- **Rail only.** No bus, no light rail.

### Posting Cadence
- **Two posts per day** (weekdays): 10:30am and 9:00pm ET
- No per-delay posts — aggregate summaries only

### Deduplication Rule
- Same train appearing multiple times in a window (delay growing from 15→25 min) → count **once at the highest observed delay**
- Keyed on (train_number, date)
- Trains without a number: each alert treated as unique

### Running Total Storage
- Lives in Google Sheet Tab 2 (Totals), cell B2
- Formula: `=SUM('Event Log'!I:I)` — auto-updates as rows are added
- Python reads but never writes to this tab

---

## Ridership Proxy Table

NJT does not publish per-train ridership. Built from 62M annual riders (2025), per-line train frequency data, and RPA anchor data (63,014 daily Penn Station boardings).

```python
RIDERS_PER_TRAIN = {
    "Northeast Corridor": {"peak": 825, "off_peak": 250, "weekend": 180},
    "North Jersey Coast":  {"peak": 500, "off_peak": 150, "weekend": 120},
    "Morris & Essex":      {"peak": 550, "off_peak": 175, "weekend": 130},
    "Montclair-Boonton":   {"peak": 415, "off_peak": 110, "weekend":  90},
    "Main/Bergen County":  {"peak": 450, "off_peak": 120, "weekend":  95},
    "Raritan Valley":      {"peak": 450, "off_peak": 125, "weekend": 100},
    "Pascack Valley":      {"peak": 315, "off_peak":  85, "weekend":  65},
    "Port Jervis":         {"peak": 300, "off_peak":  75, "weekend":  60},
    "Gladstone Branch":    {"peak": 300, "off_peak":  80, "weekend":  60},
    "Atlantic City":       {"peak": 260, "off_peak": 100, "weekend":  90},
}
```

Peak = 6–9am inbound / 4–7pm outbound on weekdays.

---

## Google Sheet Structure

**Single file, two tabs.**

### Tab 1: Event Log (Python writes here)
Columns: Date, Time, Line, Train #, Direction, Time Band, Delay Minutes, Estimated Riders, Dollar Estimate, Cause, Is Cancellation, Raw Alert Text, Posted to Bluesky

### Tab 2: Totals (formula-driven, Python reads B2 only)
- A2: `Annual Total`
- B2: `=SUM('Event Log'!I:I)`

---

## GitHub Actions Schedule

```yaml
# Collect: every 15 min, morning rush (5am-11am EDT = 9am-3pm UTC)
- cron: "*/15 9-14 * * 1-5"

# Collect: every 15 min, evening rush (3pm-9:30pm EDT)
- cron: "*/15 19-23 * * 1-5"
- cron: "*/15 0-1 * * 2-6"

# Morning summary: 10:30am EDT (14:30 UTC)
- cron: "30 14 * * 1-5"

# Evening summary: 9:00pm EDT (01:00 UTC next day)
- cron: "0 1 * * 2-6"
```

~540 minutes/month — well within GitHub's 2,000 free tier.

---

## Environment Variables / GitHub Secrets

| Secret | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key |
| `BLUESKY_HANDLE` | Bot handle, e.g. `njtdelaycost.bsky.social` |
| `BLUESKY_PASSWORD` | Bluesky app password (Settings → Privacy & Security → App Passwords) |
| `GOOGLE_SHEET_ID` | ID from sheet URL (between /d/ and /edit) |
| `GOOGLE_CREDENTIALS_JSON` | Full contents of Google service account .json file |

### Runtime env vars (set by workflow, not secrets)
| Variable | Values | Description |
|---|---|---|
| `MODE` | `collect` / `summarize` | Which pipeline to run |
| `PERIOD` | `morning` / `evening` | Which rush window to summarize |
| `DRY_RUN` | `true` / `false` | Skip posting and logging |
| `SEED_ONLY` | `true` / `false` | Mark all current posts as seen, process nothing |

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
├── src/
│   ├── main.py          ← orchestrator (collect + summarize modes)
│   ├── watcher.py       ← Station 1: Bluesky poller
│   ├── interpreter.py   ← Station 2: Claude Haiku parser
│   ├── calculator.py    ← Station 3: cost math + ridership table
│   ├── logger.py        ← Station 4: Google Sheets writer
│   ├── staging.py       ← Station 5: delay_log.json manager
│   └── aggregator.py    ← rush hour dedup, totals, post formatter
├── data/
│   ├── seen_alerts.json ← dedup state (cached between GitHub Actions runs)
│   └── delay_log.json   ← staging log (cleared after each summary post)
└── website/
    ├── index.html       ← static site (newsprint gray + Cornwallis Red)
    └── data.json        ← weekly export from Google Sheet (TBD in Phase 5)
```

---

## Methodology Statement (website copy, approved)

> This tracker estimates the economic cost of NJ Transit commuter rail delays using the U.S. Department of Transportation's standard Value of Travel Time methodology, adjusted to reflect New Jersey's median household income ($99,781, 2023 U.S. Census). We apply a rate of $24.00 per hour. The national USDOT default of $18.80/hour is disclosed as a lower-bound alternative.
>
> Rider counts per train are estimates based on system-wide annual ridership distributed proportionally across lines and adjusted for time of day. They are not real-time measurements. Only delays of 10 minutes or more are included. This tracker covers commuter rail only.
>
> These figures represent estimated opportunity costs, not cash losses. Data is sourced from unofficial Bluesky bots mirroring NJ Transit's public MyTransit alert system. This project is not affiliated with NJ Transit.

---

## Project Status (as of session ending March 25, 2026)

| Phase | Status | Notes |
|---|---|---|
| 1 — Brainstorming | ✅ Complete | |
| 2 — Research | ✅ Complete | NJT API abandoned; Bluesky bots used instead |
| 3 — Design | ✅ Complete | All key decisions locked |
| 4 — Coding: Mechanism | ✅ Substantially complete | All 7 files written and deployed |
| 5 — Coding: Website | 🟡 Mostly complete | index.html done; data.json export TBD |
| 6 — Polish | ⬜ Not started | |
| 7 — Testing | 🟡 In progress | Pipeline runs end-to-end; summarize not yet tested live |
| 8 — Launch | ⬜ Not started | |

### What's working
- Bluesky polling (watcher) ✅
- Claude interpretation ✅
- Cost calculation ✅
- Google Sheets logging ✅
- Staging log ✅
- Aggregation and dedup logic ✅
- Summary post formatting ✅
- Rush-hours-only GitHub Actions schedule ✅
- Website (static, sample data) ✅

### Immediate next steps
1. Upload 4 new/updated files to GitHub repo: `staging.py`, `aggregator.py`, `main.py`, `run_pipeline.yml`
2. Trigger manual dry run of `MODE=summarize, PERIOD=morning` to preview post format
3. Let it run live through one full weekday and review the first real summary posts
4. Connect website `data.json` export (Phase 5 remaining work)

---

## Known Issues / Watch List
- GitHub Actions cron shifts 1 hour in winter (EST vs EDT) — acceptable
- Lines without Bluesky accounts (NJCL, RVL, PVL, ACL) depend on `njmetroalert` coverage — monitor for gaps
- `seen_alerts.json` and `delay_log.json` persist via GitHub Actions cache — if cache is evicted, next run re-seeds cleanly (seen_alerts) or starts a fresh window (delay_log)
- On first deploy to a new repo: run with `SEED_ONLY=true` first to avoid spam posting old alerts

---

## Cost Estimate (monthly)
- GitHub Actions: free (~540 min/month)
- Claude API (Haiku): ~$1–3
- Google Sheets API: free
- Bluesky API: free
- **Total: ~$1–3/month**

---

*Last updated: March 25, 2026. Generated collaboratively with Claude (Anthropic).*
