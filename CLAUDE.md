# CLAUDE.md — NJ Transit Delay Cost Tracker

This file gives Claude instant project context. Paste it at the start of any new conversation to resume work without re-explaining decisions.

---

## What This Project Is

An automated pipeline that monitors NJ Transit commuter rail delays by reading public Bluesky alert bot accounts, estimates the economic cost in lost worker time, logs each event to a Google Sheet, and posts a once-daily summary to a Bluesky bot account.

One post goes out daily at ~3pm ET (targeting 5pm ET actual after GitHub delay), summarizing the **previous day's** complete morning and evening rush delays.

**Owner:** Ames (non-technical background — plain English explanations always, code second)

---

## Architecture: Single Daily Pipeline

One workflow fires once per weekday. No staging files, no caching, no collect/summarize split. Bluesky stores all posts permanently so we always reach back and fetch what we need.

```
GitHub Actions fires at 19:00 UTC Mon–Fri (~5pm ET actual after ~2h delay)
       |
       v
daily.py
  |
  ├── get_yesterday_windows()
  |     Calculates yesterday's morning and evening UTC windows.
  |     "Yesterday" in ET time = fully complete by the time we run.
  |     Morning: 09:00–16:00 UTC (5am–noon EDT)
  |     Evening: 19:00–01:00 UTC (3pm–9pm EDT, crosses midnight)
  |
  ├── process_window("morning")  ← runs twice, independently
  ├── process_window("evening")
  |
  |   Each window:
  |     1. watcher.get_window_delays(start, end)
  |          Fetches posts from 5 Bluesky accounts within the time window.
  |          Filters: rail only, no bus, no light rail, ≥10 min delay.
  |          Detects system-wide Penn alerts and line suspensions.
  |          Cross-account dedup: same text from two accounts = one event.
  |     2. interpret_window()
  |          Claude Haiku extracts: line, train_number, delay_minutes,
  |          cause, is_cancellation. System-wide events skip interpreter.
  |     3. deduplicate_by_train()
  |          Normal trains: keep highest delay per (train_number, date).
  |          System-wide events: keep highest cost per (line, window).
  |          *** Dedup is per-window. A Penn suspension in both morning
  |          AND evening counts TWICE — once per window. ***
  |     4. calculate_window()
  |          Costs calculated AFTER dedup (not before).
  |          Normal: riders × (delay_min/60) × $24.00
  |          System-wide Penn: 8,000 riders/hr × (delay_min/60) × $24.00
  |          Line suspension: riders/train × trains/hr × $24.00
  |     5. log_delay() for each event → Google Sheet Event Log tab
  |
  ├── calculate_totals(morning_events + evening_events)
  |
  ├── format_tweet(yesterday_et, totals)
  |     Normal: "On Monday, NJ Transit delayed commuters for a total of..."
  |     No delays: "Good news! Yesterday (Monday), NJ Transit ran on time..."
  |
  ├── post_to_bluesky(tweet_text)
  |
  └── log_tweet() → Google Sheet Tweet_log tab
```

---

## Three Alert Types

### 1. Normal per-train alert
```
NEC train #3876, the 9:28 PM arrival to PSNY, is up to 25 min. late
due to earlier mechanical issues.
```
→ Interpreter extracts train number, delay, cause
→ `calculate_cost()`: riders × (delay/60) × $24
→ Dedup by (train_number, date), keep highest delay

### 2. System-wide Penn Station alert (≥15 min trigger)
```
NJ TRANSIT rail service is subject to up to 20-minute delays
into and out of Penn Station New York.
```
→ Skips interpreter, handled directly
→ `calculate_system_wide_cost()`: 8,000 riders/hr × (delay/60) × $24
→ Dedup by (line="System-Wide (Penn Station)", window), keep highest cost
→ Triggers on: "delays into and out", "subject to up to", "service suspended" + Penn, etc.
→ Also catches: Penn-area service suspensions

### 3. Line-wide suspension
```
Morris & Essex service is suspended in both directions
due to Portal Bridge failure.
```
→ Skips interpreter, handled directly
→ `calculate_line_suspension_cost()`: riders/train × trains/hr × $24
→ Dedup by (line, window), keep highest cost
→ Must name a specific rail line; must NOT mention Penn Station (that's type 2)

---

## Key Decisions — Locked In

### Value of Travel Time
- **$24.00/hour** — USDOT formula (50% of median HHI) applied to NJ median HHI $99,781 (2023 ACS)
- National default $18.80/hr disclosed as lower bound

### Ridership (peak, all events use peak figures)
```python
RIDERS_PER_TRAIN = {
    "Northeast Corridor": 825,  "North Jersey Coast": 500,
    "Morris & Essex":     550,  "Montclair-Boonton":  415,
    "Main/Bergen County": 450,  "Raritan Valley":     450,
    "Pascack Valley":     315,  "Port Jervis":        300,
    "Gladstone Branch":   300,  "Atlantic City":      260,
    "Unknown":            400,
}
TRAINS_PER_HOUR_PEAK = {
    "Northeast Corridor": 8,  "North Jersey Coast": 4,
    "Morris & Essex":     5,  "Montclair-Boonton":  3,
    "Main/Bergen County": 4,  "Raritan Valley":     4,
    "Pascack Valley":     2,  "Port Jervis":        2,
    "Gladstone Branch":   2,  "Atlantic City":      2,
}
PENN_STATION_RIDERS_PER_HOUR = 8000
```

### Thresholds
- **Minimum delay: 10 minutes**
- **Cancellations: 60-minute assumed delay**
- **System-wide Penn alerts: 1-hour assumed duration**
- **Line suspensions: 1-hour assumed duration**
- **System-wide Penn minimum: 15 minutes** (below this, treated as normal alert)

### Deduplication
- Per-window (morning and evening deduplicated independently)
- Normal trains: (train_number, date) → keep highest delay_minutes
- System-wide: (line, window) → keep highest dollar_estimate
- Cross-account text dedup in watcher (same post from two accounts = one)

### Filters
- Rail only — bus alerts filtered out (matches "bus service", "bus route", "nj transit bus", etc.)
- Light rail filtered out (matches "light rail" anywhere)
- "M and E", "M&E", "Morris and Essex" → Morris & Essex
- "MOBO" → Montclair-Boonton (handled in both watcher and interpreter prompt)

### Post format
```
On [Day], NJ Transit delayed commuters for a total of [PERSON-HOURS]
across both rush hours. City employers lost [COST] in productive
working time. ([N] delay events across [N] lines)
```
No delays:
```
Good news! Yesterday ([Day]), NJ Transit commuter rail ran on time
with no significant delays reported. 🚂
```

---

## Data Sources: Bluesky Alert Accounts

| Account | Coverage |
|---|---|
| `njmetroalert.bsky.social` | All lines — primary |
| `njtransit--nec.bsky.social` | NEC — double coverage (note double dash) |
| `njtransit-me.bsky.social` | Morris & Essex |
| `njtransit-mobo.bsky.social` | Montclair-Boonton |
| `njtransit-mbpj.bsky.social` | Main/Bergen County |

---

## Google Sheet Structure

**Single file, three tabs.**

### Tab 1: Event Log (Python writes)
Columns: Date, Time, Line, Train #, Direction, Time Band, Delay Minutes, Estimated Riders, Dollar Estimate, Cause, Is Cancellation, Raw Alert Text, Posted to Bluesky

Note: "Posted to Bluesky" always shows "No" — individual events are never posted, only the daily summary is. Legacy column.

### Tab 2: Totals (formula-driven, Python reads B2 only)
- B2: `=SUM('Event Log'!I:I)`

### Tab 3: Tweet_log (auto-created by Python on first run)
Columns: Timestamp, Tweet Text, Total Cost Estimate, Number of Delay Events, Post URI

---

## Repository Structure

```
/
├── CLAUDE.md
├── README.md
├── requirements.txt
├── CNAME                        ← custom domain for GitHub Pages
├── .github/
│   └── workflows/
│       └── daily.yml            ← one workflow, Mon–Fri, 19:00 UTC
├── src/
│   ├── daily.py                 ← main orchestrator (NEW)
│   ├── watcher.py               ← Bluesky fetcher (window-based)
│   ├── interpreter.py           ← Claude Haiku parser
│   ├── calculator.py            ← cost math (3 calculation types)
│   ├── logger.py                ← Sheets writer (Event Log + Tweet_log)
│   └── aggregator.py            ← dedup, totals, DST window math
└── website/
    ├── index.html               ← static site (in progress)
    └── data.json                ← TBD
```

Note: `main.py`, `staging.py`, `morning.yml`, `evening.yml`, `daily_summary.yml`,
`run_pipeline.yml` are all retired and should be deleted from the repo.

---

## GitHub Actions Schedule

```yaml
# daily.yml
- cron: "0 19 * * 1-5"   # 19:00 UTC Mon–Fri → ~5pm ET actual after ~2h delay
```

~10 minutes/month — well within GitHub's 2,000 free tier.

---

## Environment Variables / GitHub Secrets

| Secret | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key (Haiku for interpretation) |
| `BLUESKY_HANDLE` | Bot handle, e.g. `njtdelaycost.bsky.social` |
| `BLUESKY_PASSWORD` | Bluesky app password |
| `GOOGLE_SHEET_ID` | Sheet ID from URL (between /d/ and /edit) |
| `GOOGLE_CREDENTIALS_JSON` | Full contents of Google service account .json |

| Runtime var | Values | Notes |
|---|---|---|
| `DRY_RUN` | `true` / `false` | Skips Sheets writes and tweet. Default `true` for manual triggers. |

---

## Known Bugs Fixed

### Evening window date rollover
Evening job fires at ~01:00 UTC (next calendar day). Original code used `now_utc.date()` to build window — wrong day. Fixed: use ET date, and for the daily pipeline, derive "yesterday" from ET time before calculating windows.

### System-wide dedup bypass (fixed)
`main.py` was splitting system-wide events out before calling `deduplicate_by_train()`, so they were never deduplicated. Fixed: pass all events through dedup together.

### Period misclassification
`github.event.schedule` returns unreliable values under GitHub backlog conditions. Fixed: split into separate workflow files with `PERIOD` hardcoded, eliminating detection logic entirely. Now further simplified to a single `daily.py` with no period detection needed.

### Hyphenated minute regex
"Up to 20-minute delays" wasn't being parsed. Fixed: `r"up to (\d+)[- ]min"` handles both hyphenated and space-separated formats.

---

## Validation Checkpoint

**Dry run result (Monday, March 31 2026):**
> "On Monday, NJ Transit delayed commuters for a total of 30,918 person-hours across both rush hours. City employers lost $742,040 in productive working time. (30 delay events across 5 lines)"

Live run should match this exactly to confirm windows and dedup are correct.

**Last sheet row validated:** ~row 126

---

## Website (Phase 5 — In Progress)

- Static site (`index.html`) — design complete, newsprint gray + Cornwallis Red
- Hosting: GitHub Pages with custom domain (GoDaddy)
- DNS: 4 A records → GitHub IPs, CNAME `www` → `yourusername.github.io`
- `CNAME` file in repo root with domain name
- `data.json` export from Google Sheet — TBD

---

## Methodology (approved summary)

VTTS rate of $24.00/hr derived from USDOT formula applied to NJ median household income ($99,781, 2023 ACS). Rider counts are estimates from aggregate system data, not real-time measurements. Only delays ≥10 min on commuter rail lines are included. Figures represent opportunity costs, not cash losses. Not affiliated with NJ Transit.

Full white paper: `NJT_Delay_Tracker_Methodology.docx`

---

## Cost Estimate (monthly)
- GitHub Actions: free (~10 min/month)
- Claude API (Haiku): ~$0.50–1.00
- Google Sheets API: free
- Bluesky API: free
- GitHub Pages: free
- **Total: under $1/month**

---

*Last updated: April 1, 2026. Built collaboratively with Claude (Anthropic).*
