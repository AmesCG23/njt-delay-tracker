# CLAUDE.md — NJ Transit Delay Cost Tracker

This file gives Claude instant project context. Paste it at the start of any new conversation to resume work without re-explaining decisions.

---

## What This Project Is

An automated pipeline that monitors NJ Transit commuter rail delays by reading public Bluesky alert bot accounts, estimates the economic cost in lost worker time, logs each event to a Google Sheet, and posts a once-daily summary to a Bluesky bot account.

One post goes out daily at ~3pm ET (targeting 5pm ET actual after GitHub delay), summarizing the **previous day's** complete morning and evening rush delays.

**Owner:** Ames, Brennan Center for Justice (non-technical — plain English explanations always, code second)
**Creator persona (anonymous):** TBD — candidates include The Dispatcher, The Flagman, A Daily Rider

---

## Architecture: Single Daily Pipeline

One workflow (`daily.yml`) fires once per weekday (Tue–Sat, covering Mon–Fri delays). No staging files, no caching, no collect/summarize split. Bluesky stores all posts permanently so we always reach back and fetch what we need.

```
GitHub Actions fires at 20:00 UTC Tue–Sat (~4–5pm ET actual after ~2h delay)
       |
       v
daily.py
  |
  ├── get_yesterday_windows()
  |     Calculates yesterday's morning and evening ET windows.
  |     "Yesterday" in ET time = fully complete by the time we run.
  |     Morning: 5:00–10:30 AM ET (09:00–14:30 UTC EDT)
  |     Evening: 3:00–8:30 PM ET  (19:00–00:30 UTC EDT, crosses UTC midnight)
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
  |     5. log_delay_batch() → Google Sheet Event Log tab (batched, one API call)
  |     6. log_alert_batch() → Google Sheet Alert Log tab (pre-dedup, for hand-check)
  |     7. log_run() → Google Sheet Run Log tab
  |
  ├── calculate_totals(morning_events + evening_events)
  |
  ├── format_tweet(yesterday_et, totals)
  |     Normal: "On Monday, NJ Transit delayed commuters for a total of..."
  |     No delays: "Good news! Yesterday (Monday), NJ Transit ran on time..."
  |
  ├── post_to_bluesky(tweet_text)
  |
  ├── log_tweet() → Google Sheet Tweet_log tab
  └── log_run_summary() → updates Run Log with post details
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
→ Assumed duration: 1 hour
→ Dedup by (line="System-Wide (Penn Station)", window), keep highest cost
→ Triggers on: "delays into and out", "subject to up to", "service.{0,30}suspend" + Penn, etc.

### 3. Line-wide suspension
```
Morris & Essex service is suspended in both directions
due to Portal Bridge failure.
```
→ Skips interpreter, handled directly
→ `calculate_line_suspension_cost()`: riders/train × trains/hr × $24
→ Assumed duration: 1 hour
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
- **System-wide Penn minimum: 15 minutes** (below this, not treated as system-wide)

### Deduplication
- Per-window (morning and evening deduplicated independently)
- Normal trains: (train_number, date) → keep highest delay_minutes
- System-wide: (line, window) → keep highest dollar_estimate
- Cross-account text dedup in watcher (same post from two accounts = one)

### Filters
- Rail only — bus alerts filtered on specific phrases: "bus service", "bus route",
  "nj transit bus", "njt bus", "bus detour", starts with "bus "
- Light rail filtered on "light rail" anywhere in text
- Minimum 10-minute delay threshold

### Line name matching (wildcards)
```
Northeast Corridor: "northeast corridor", "nec "
North Jersey Coast: "north jersey coast", "njcl", "nj coast", "coast line", "jersey coast line"
Morris & Essex:     "morris & essex", "morris and essex", "m and e", "m&e"
Montclair-Boonton:  "montclair-boonton", "montclair boonton", "mobo"
Main/Bergen County: "main/bergen", "main bergen", "mbpj", "main line",
                    "bergen county line", "bergen line", "main-bergen"
Raritan Valley:     "raritan valley", "rvl"
Pascack Valley:     "pascack valley", "pvl"
Port Jervis:        "port jervis"
Atlantic City:      "atlantic city rail"
Gladstone:          "gladstone"
```
Note: "main line" is a generic phrase — watch for false positives in Alert Log.

Interpreter prompt also includes abbreviation rules so Claude Haiku maps
MOBO → Montclair-Boonton, M&E → Morris & Essex, MBPJ → Main/Bergen County, etc.

### Post format
```
On [Day], NJ Transit delayed commuters for a total of [PERSON-HOURS]
across the morning and afternoon rush hours. City employers lost out on
working time conservatively valued at [COST]. (Estimate based on [N] delay events)
```
Person-hours vs person-minutes: hours used if total ≥ 60,000 person-minutes, otherwise minutes.
Cost formatted as $X.XM if ≥ $1M, otherwise $X,XXX.

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
| `njtransit-njcl.bsky.social` | North Jersey Coast — double coverage |
| `njtransit-me.bsky.social` | Morris & Essex |
| `njtransit-mobo.bsky.social` | Montclair-Boonton |
| `njtransit-mbpj.bsky.social` | Main/Bergen County |
| `njtransit-pvl.bsky.social` | Pascack Valley — double coverage |

---

## Google Sheet Structure

**Single file, six tabs.**

### Tab 1: Event Log (Python writes — batched)
One row per deduplicated delay event. Written once per window via `log_delay_batch()`.
Columns: Date, Time, Line, Train #, Direction, Time Band, Delay Minutes, Estimated Riders, Dollar Estimate, Cause, Is Cancellation, Raw Alert Text, Posted to Bluesky

Note: "Posted to Bluesky" always "No" — individual events never posted. Legacy column.

### Tab 2: Totals (formula-driven)
- B2: `=SUM('Event Log'!I:I)` — running cumulative total

### Tab 3: Tweet_log (auto-created)
One row per daily tweet sent. 18 columns A–R.
- A: Timestamp
- B: Tweet Text
- C: Total Cost Estimate
- D: Number of Delay Events
- E: Post URI
- F: Report Date (YYYY-MM-DD — the date of delays, i.e. yesterday)
- G: Person-Hours (total; drives the line graph on graphs.html)
- H: (reserved)
- I: Morning Cost (post-dedup dollar total)
- J: Evening Cost (post-dedup dollar total)
- K–Q: Per-line person-hours (NEC, M&E, NJCL, Main/Bergen, Raritan, MoBo, System-wide Penn)
- R: Pascack Valley person-hours

### Tab 4: Alert Log (wiped and rewritten each run)
Pre-dedup audit trail for hand-checking. One row per interpreted alert before
deduplication. Use this to verify the script saw what Bluesky shows.
Columns: Date Seen, Alert Date, Alert Time, Line, Train #, Delay Minutes,
Estimated Cost (pre-dedup), Raw Alert Text

### Tab 5: Run Log (wiped and rewritten each run)
One row per window (morning + evening). Quick sanity check on overall numbers.
Columns: Run Date, Period, Raw Posts Fetched, After Dedup, Total Cost,
Date of Post, Time of Post, Post URI

### Tab 6: for_web (publish to web)
Feeds the public website via Google Visualization API CSV endpoint.
- A1: yesterday's total cost — written automatically by `logger.py` each run
- A2: cumulative total since launch — enter manually or use a formula
- A3: yesterday's person-hours — written automatically by `logger.py` each run
Must be published: File → Share → Publish to web

---

## Repository Structure

```
/
├── CLAUDE.md
├── README.md
├── requirements.txt
├── next_steps.md                ← checklist for getting the website live
├── data/                        ← local data files (not committed to git)
├── .github/
│   └── workflows/
│       ├── daily.yml            ← main pipeline, Tue–Sat, 20:00 UTC
│       └── benchmark.yml        ← manual dry-run replay against a past date
├── src/
│   ├── daily.py                 ← main orchestrator
│   ├── watcher.py               ← Bluesky fetcher + alert type detection
│   ├── interpreter.py           ← Claude Haiku parser
│   ├── calculator.py            ← cost math (3 calculation types)
│   ├── logger.py                ← Sheets writer (all tabs)
│   └── aggregator.py            ← dedup, totals, post formatter
└── docs/                        ← GitHub Pages root (served at custom domain)
    ├── index.html               ← main public page
    ├── graphs.html              ← data visualization page
    ├── methodology.html         ← methodology explainer
    ├── CNAME                    ← custom domain for GitHub Pages
    └── njt-delay-tracker-logo.svg
```

Retired files (delete from repo if present):
`main.py`, `staging.py`, `morning.yml`, `evening.yml`,
`daily_summary.yml`, `run_pipeline.yml`

---

## GitHub Actions Schedule

```yaml
# daily.yml — Tue–Sat so Tuesday covers Monday, Saturday covers Friday
- cron: "0 20 * * 2-6"   # 20:00 UTC = 3pm EST / 4pm EDT → ~4–5pm ET actual after ~2h delay
```

Two workflows total:
- `daily.yml` — automated daily run (no `DRY_RUN` on schedule; `DRY_RUN=true` default for manual triggers)
- `benchmark.yml` — manual only; always `DRY_RUN=true`; accepts `OVERRIDE_DATE` to replay any past date

~10 minutes/month — well within GitHub's 2,000 free tier.

---

## Google Sheets API Rate Limiting

All Sheets writes are batched to avoid 429 quota errors on bad NJT days:
- `log_delay_batch()` — one `append_rows()` call per window (not per event)
- `log_alert_batch()` — one `append_rows()` call per window
- Total API calls per run: ~10 regardless of delay count

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
| `DRY_RUN` | `true` / `false` | Skips all Sheets writes and tweet. Default `true` for manual triggers, `false` for scheduled runs. |
| `OVERRIDE_DATE` | `YYYY-MM-DD` | If set, treats this date as "yesterday" instead of computing from current time. Used by `benchmark.yml`. |

---

## Known Bugs Fixed

### Evening window date rollover
Evening window crosses UTC midnight. Original code used `now_utc.date()` — fetched
wrong day. Fixed: derive "yesterday" from ET time, compute window from that.

### System-wide dedup bypass
`daily.py` was splitting system-wide events out before `deduplicate_by_train()`,
bypassing dedup entirely. Fixed: all events flow through dedup together.

### Period misclassification
`github.event.schedule` returns unreliable values under backlog. Fixed: retired
multi-workflow approach, `PERIOD` no longer needed — `daily.py` always processes
both windows.

### Hyphenated minute regex
"Up to 20-minute delays" wasn't parsed. Fixed: `r"up to (\d+)[- ]min"` handles
both "20-minute" and "20 min" formats.

### process_window return type
`process_window()` returned a plain list but caller expected `(events, raw_count)`
tuple. Fixed: all return paths return a 2-tuple including early-exit cases.

### raw_count NameError
`raw_count` variable was referenced in the return statement but never assigned
in the happy path. Fixed: `raw_count = len(raw)` added after fetch.

### Interpreter null + resolution language false resurrection
When the Haiku interpreter returned null, the code fell back to watcher-extracted
data — which could resurrect a "service restored" alert as a new delay event.
Fixed: check raw text for resolution phrases ("on or close to schedule", "service
restored", "normal service", etc.) before applying the watcher fallback. Null +
resolution language → drop the event. Null + no resolution language + known line
and delay → use watcher fallback.

---

## Hand-Check Protocol

To validate a daily total against Bluesky manually:
1. Open the Alert Log tab — shows every alert the script saw pre-dedup
2. Open the five Bluesky accounts, scroll to the time window
3. Count qualifying rail alerts on Bluesky; compare to Raw Posts in Run Log
4. For each Bluesky alert, verify it appears in Alert Log with correct line/delay
5. For any train # appearing twice in Alert Log, verify Event Log has it once at highest delay
6. Spot-check one cost: riders × (delay_min/60) × $24 should match Dollar Estimate

Common sources of legitimate slippage:
- Alert uses unrecognized line name → filtered, not in Alert Log (add wildcard)
- Same alert from two accounts → cross-account deduped in watcher, one copy in Alert Log
- Alert posted after window closes → never fetched, expected
- Escalating alert (15→25 min) → two rows in Alert Log, one in Event Log at 25 min

---

## Website

Three pages, all in `docs/`:
- `index.html` — main public page (newsprint gray #f0eeeb + EB Garamond)
- `graphs.html` — data visualizations (daily hours chart, by-line bar, morning/evening doughnut)
- `methodology.html` — methodology explainer

Data sources:
- `for_web` tab → Google Visualization API CSV endpoint → `index.html` summary stats
- `Tweet_log` tab → same endpoint → `graphs.html` charts (columns F, G, I–R)

Hosting: GitHub Pages, `docs/` folder on `main` branch, custom domain via GoDaddy.
`docs/CNAME` contains the domain name.

**GitHub Pages DNS records:**
```
185.199.108.153
185.199.109.153
185.199.110.153
185.199.111.153
```

**graphs.html pending feature:** Methodology change date markers (red vertical lines
on the hours chart). Implementation is commented out inside `graphs.html` with
step-by-step instructions. Activate when Ames confirms a methodology change date.

---

## Project Identity (In Progress)

**Regional framing:** The project's core argument is that NJ Transit delays are
a NY/NJ regional economic problem — NY employers have a direct stake. Name should
invoke the shared commuter experience across the Hudson.

**Name candidates under consideration:**
- The 7:14 (or similar specific train time) — insider shorthand feel
- Residual Delays — NJT's own euphemism turned against them
- Making All Stops — the announcement everyone recognizes
- Departure Vision — NJT's own product name, cheeky
- Portal — named after Portal Bridge, the actual bottleneck

**Creator persona (anonymous):** TBD
- The Dispatcher — institutional knowledge, knows where all the trains are
- The Flagman — raising a flag on a problem; railroad vernacular
- A Daily Rider — humble, relatable, impossible to attack

---

## Methodology (approved summary)

VTTS rate of $24.00/hr derived from USDOT formula applied to NJ median household
income ($99,781, 2023 ACS). Rider counts are estimates from aggregate system data,
not real-time measurements. Only delays ≥10 min on commuter rail lines are included.
Figures represent opportunity costs, not cash losses. Not affiliated with NJ Transit.

Full white paper: `NJT_Delay_Tracker_Methodology.docx`

---

## Validation Checkpoint

**Dry run result (Monday, March 31 2026):**
> "On Monday, NJ Transit delayed commuters for a total of 30,918 person-hours
> across both rush hours. City employers lost $742,040 in productive working time.
> (30 delay events across 5 lines)"

---

## Cost Estimate (monthly)
- GitHub Actions: free (~10 min/month)
- Claude API (Haiku): ~$0.50–1.00
- Google Sheets API: free
- Bluesky API: free
- GitHub Pages: free
- **Total: under $1/month**

---

*Last updated: May 4, 2026. Built collaboratively with Claude (Anthropic).*
