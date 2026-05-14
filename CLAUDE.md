# CLAUDE.md — NJ Transit Delay Cost Tracker

A complete reference for picking this project back up after time away. Written for both Ames (project owner) and Claude (AI collaborator). Plain English first, code second.

---

## What This Project Does

Every weekday, NJ Transit posts service alerts to Bluesky when trains are delayed, cancelled, or suspended. This project:

1. **Collects** those alerts once a day, for the previous day's morning and evening rush hours
2. **Interprets** each alert using Claude AI to extract the line, train number, and delay length
3. **Calculates** the economic cost of each delay (delayed passengers × time × dollar value of time)
4. **Logs** everything to a Google Sheet for auditing
5. **Posts** a single daily summary to Bluesky with the total cost
6. **Feeds** a public website that shows running totals and charts

The whole thing runs automatically on GitHub Actions and costs under $1/month to operate.

**Owner:** Ames, Brennan Center for Justice  
**Contact:** bettertrains@proton.me  
**Website:** Lives at `docs/CNAME` — the custom domain set up via GitHub Pages + GoDaddy  
**Bluesky bot:** `njtdelaytracker.bsky.social`

---

## Project Identity (In Progress)

**Regional framing:** NJ Transit delays are a NY/NJ regional economic problem. NY employers have a stake. The name and persona should invoke the shared commuter experience.

**Name candidates:**
- The 7:14 (or similar train time) — insider shorthand feel
- Residual Delays — NJT's own euphemism turned against them
- Making All Stops — the announcement everyone hears
- Departure Vision — NJT's own product name, cheeky
- Portal — named after Portal Bridge, the actual bottleneck

**Creator persona (anonymous):**
- The Dispatcher — institutional knowledge, knows where all the trains are
- The Flagman — raising a flag on a problem; railroad vernacular
- A Daily Rider — humble, relatable, impossible to attack

---

## Repository Layout

```
/
├── CLAUDE.md                    ← this file
├── README.md                    ← public-facing project description
├── requirements.txt             ← Python package list
├── next_steps.md                ← checklist for getting the website live (mostly done now)
├── data/                        ← local data files, not committed to git
├── docs/                        ← GitHub Pages root; served as the public website
│   ├── index.html               ← main page (running totals, summary stats)
│   ├── graphs.html              ← charts page (delay trends, by-line, morning/evening)
│   ├── methodology.html         ← how the estimates are calculated
│   ├── CNAME                    ← custom domain for GitHub Pages
│   └── njt-delay-tracker-logo.svg
├── src/                         ← all Python pipeline code
│   ├── daily.py                 ← main orchestrator — runs the full pipeline
│   ├── watcher.py               ← fetches posts from Bluesky, classifies alert types
│   ├── interpreter.py           ← calls Claude Haiku to extract structured data
│   ├── calculator.py            ← does the cost math (three calculation types)
│   ├── aggregator.py            ← deduplicates events, sums totals
│   └── logger.py                ← writes to Google Sheets (all tabs)
└── .github/workflows/
    ├── daily.yml                ← automated daily run, Tue–Sat at 20:00 UTC
    └── benchmark.yml            ← manual dry-run against any historical date
```

**Retired files** (delete if present): `main.py`, `staging.py`, `morning.yml`, `evening.yml`, `daily_summary.yml`, `run_pipeline.yml`

---

## How the Pipeline Runs

### Schedule

`daily.yml` fires on GitHub Actions at **20:00 UTC, Tuesday through Saturday**. That translates to roughly 3–4pm ET (the cron can't track DST), and with typical GitHub queue delays, the script usually actually runs around 4–5pm ET.

The workflow runs Tuesday–Saturday so that Tuesday covers Monday's delays, and Saturday covers Friday's delays — the "yesterday" logic means you need to run the day after.

### What "yesterday" means

When the script runs on Tuesday at 4pm ET, it fetches Monday's alerts. It calculates what time Monday's rush windows were in UTC (accounting for EST vs. EDT automatically using Python's `ZoneInfo`), then queries Bluesky's API for posts from those accounts within those time windows.

- **Morning rush:** 5:00 AM – 10:30 AM Eastern Time (yesterday)
- **Evening rush:** 3:00 PM – 8:30 PM Eastern Time (yesterday)

### The five pipeline stages (per window)

Each rush hour window goes through these stages in sequence:

```
Bluesky posts
    ↓ watcher.py
Classified alert dicts (rail only, ≥10 min, with type flags)
    ↓ interpreter.py (via Claude Haiku)
Structured events (line, train #, delay, cause, direction)
    ↓ aggregator.deduplicate_by_train()
One event per train per window (highest delay wins)
    ↓ calculator.py
Events with dollar_estimate and estimated_riders filled in
    ↓ logger.py
Written to Google Sheets (Event Log tab)
```

After both windows finish, `daily.py` combines the results, formats a tweet, posts it to Bluesky, and writes to the Tweet_log tab.

---

## The Code Files, One by One

### `src/daily.py` — The Orchestrator

This is the entry point. When GitHub Actions runs `python src/daily.py`, everything happens from here.

**Key functions:**

`get_yesterday_windows()` — Computes yesterday's date in Eastern Time (or uses `OVERRIDE_DATE` if set), then converts the rush hour start/end times to UTC. Returns five values: `yesterday_et`, `morn_start`, `morn_end`, `eve_start`, `eve_end`.

`process_window(label, start_utc, end_utc)` — Runs the full five-stage pipeline for one window. Calls watcher → interpreter → dedup → calculator in sequence. Returns `(list_of_events, raw_count)`. Important: always returns a 2-tuple even in error/empty cases.

`interpret_window(raw_delays)` — Loops over raw Bluesky posts and calls the interpreter on each normal alert. System-wide and line-suspension events skip the interpreter — they're already structured from watcher.py. Handles the fallback logic for when the interpreter returns `None` (see Known Bugs section).

`calculate_window(deduped_delays)` — Routes each event to the right calculator based on its `system_wide` and `line_suspension` flags.

`compute_line_hours(events)` — Before logging the tweet, tallies person-hours by line. This feeds the per-line columns (K–R) in Tweet_log, which powers the "by line" bar chart on the website.

`format_tweet(yesterday_et, totals)` — Builds the text of the daily Bluesky post. Two formats: normal delays (total person-hours + cost + event count), or "good news" (no qualifying delays found). Has a 295-character safety cap.

`post_to_bluesky(text)` — Logs in to Bluesky using `BLUESKY_HANDLE` and `BLUESKY_PASSWORD` secrets and posts the tweet. Returns the post URI, or `None` on failure.

`run()` — The main function. Orchestrates everything: clears logs, runs morning window, runs evening window, combines totals, posts tweet, logs everything.

**DRY_RUN mode:** When `DRY_RUN=true`, the script fetches from Bluesky and interprets alerts normally, but skips all Google Sheets writes and does not post to Bluesky. Used for testing. Manual triggers default to `DRY_RUN=true`; scheduled runs always use `DRY_RUN=false`.

---

### `src/watcher.py` — The Bluesky Fetcher

Connects to Bluesky's public API (no login needed for reading) and fetches posts from seven alert accounts. Filters them, classifies them, and returns a clean list of dicts.

**The seven accounts monitored:**

| Account | Coverage |
|---|---|
| `njmetroalert.bsky.social` | All lines — primary, highest volume |
| `njtransit--nec.bsky.social` | NEC only (note: double dash) |
| `njtransit-njcl.bsky.social` | North Jersey Coast only |
| `njtransit-me.bsky.social` | Morris & Essex only |
| `njtransit-mobo.bsky.social` | Montclair-Boonton only |
| `njtransit-mbpj.bsky.social` | Main/Bergen County only |
| `njtransit-pvl.bsky.social` | Pascack Valley only |

The `njmetroalert` account posts everything — bus, light rail, and all rail lines. On a bad day it can post 200+ alerts, which can push morning alerts beyond the first page of results by the time the script runs at ~5pm. The fetcher paginates (up to 500 posts) to make sure it gets everything.

**Three alert types recognized:**

1. **System-wide Penn Station alert** (`system_wide=True, line_suspension=False`): "NJ TRANSIT rail service is subject to up to 20-minute delays into and out of Penn Station New York." Detected by `is_system_wide_alert()`. Must mention Penn Station + a system-wide pattern + ≥15 min delay.

2. **Line-wide suspension** (`system_wide=True, line_suspension=True`): "Morris & Essex service is suspended in both directions." Detected by `is_line_suspension_alert()`. Must name a specific line + use suspension language + NOT mention Penn Station (which would make it type 1).

3. **Normal per-train alert** (`system_wide=False`): "NEC train #3876, the 9:28 PM arrival to PSNY, is up to 25 min. late." Everything else that passes the rail/delay filters.

**Cross-account deduplication:** The same post sometimes appears from two accounts (e.g., an NEC delay shows up on both `njmetroalert` and `njtransit--nec`). Watcher deduplicates by normalized text before returning — same post from two accounts counts as one event.

**Filters applied:**
- Must contain a delay keyword ("late", "delayed", "cancelled", "suspended", etc.)
- Must mention a rail line signal or "penn station" / "train #"
- Bus alerts filtered on specific phrases (starting with "bus ", "bus service", "bus route", "nj transit bus", "njt bus", "bus detour") — using phrases rather than the bare word "bus" avoids dropping rail alerts that mention bus connections in passing
- Light rail filtered out anywhere "light rail" appears
- Normal alerts must be ≥10 minutes delay

**Line name mapping in watcher.py** (used for `line_hint` when an account covers a single line, and for line-suspension detection):

| Alert text contains | Maps to |
|---|---|
| "northeast corridor", "nec " | Northeast Corridor |
| "north jersey coast", "njcl", "nj coast", "coast line", "jersey coast line" | North Jersey Coast |
| "morris & essex", "morris and essex", "m and e", "m&e" | Morris & Essex |
| "montclair-boonton", "montclair boonton", "mobo" | Montclair-Boonton |
| "main/bergen", "main bergen", "mbpj", "main line", "bergen county line", "bergen line", "main-bergen" | Main/Bergen County |
| "raritan valley", "rvl" | Raritan Valley |
| "pascack valley", "pvl" | Pascack Valley |
| "port jervis" | Port Jervis |
| "atlantic city rail" | Atlantic City |
| "gladstone" | Gladstone Branch |

Note: "main line" is a common English phrase — watch the Alert Log for false positives.

---

### `src/interpreter.py` — The Claude Haiku Parser

For each normal (per-train) alert, this module sends the raw text to Claude Haiku and asks it to extract structured data as JSON.

**What it extracts:**
- `line` — full rail line name
- `delay_minutes` — integer minutes (null if not mentioned)
- `direction` — "inbound", "outbound", or "unknown"
- `cause` — 3–6 word cause description
- `train_number` — as a string (e.g. "3876"), or null
- `is_cancellation` — true if the train is cancelled

**The prompt includes special rules for:**
- RVL (Raritan Valley Line) — terminates at Newark Penn, not PSNY; don't drop outbound RVL trains for unknown destination
- Hoboken Terminal — valid for M&E, MOBO, Main/Bergen, Pascack, Port Jervis lines
- Line name abbreviations — NEC, NJCL, M&E, MOBO, MBPJ, RVL, PVL
- Only return null for the full response if the alert says "on or close to schedule" or "normal service" — not just because the destination is unfamiliar

**What happens when Haiku returns null (no delay found):**
The code in `daily.py → interpret_window()` checks whether the raw alert text contains resolution language first ("service restored", "back on schedule", etc.). If it does → trust the null and drop the event. If it doesn't, and the watcher already extracted a line name and delay, use the watcher's data as a fallback. This prevents Raritan Valley and Hoboken-terminal alerts from being silently dropped.

**Model used:** `claude-haiku-4-5-20251001` — the cheapest Claude model, since this is called for every alert. Costs roughly $0.50–1.00/month total.

---

### `src/calculator.py` — The Cost Math

Three calculation functions, one for each alert type.

**VTTS rate: $44.00/hour**
Derived from NJ metro area median household income (~$138k/yr) → ~$66/hr gross → 2/3 × $66 = **$44.00/hr** (USDOT formula). The national default ($18.80/hr) is retained in the code as `VTTS_NATIONAL_DEFAULT` and disclosed in methodology as a lower bound.

**`calculate_cost(event)` — normal per-train delay:**
```
cost = riders_per_train × (delay_minutes / 60) × $44.00
```
Cancellations use 60-minute assumed delay (typical wait for next train during peak).

**`calculate_system_wide_cost(event)` — Penn Station system-wide:**
```
cost = 9,600 riders/hour × (delay_minutes / 60) × $44.00
```
The 9,600 figure comes from RPA data: ~65,000 daily NJ→Penn departures, 60% during peak hours ÷ 2 for in/out = ~9,600/hr. Duration assumed = 1 hour.

**`calculate_line_suspension_cost(event)` — full line suspension:**
```
cost = riders_per_train × trains_per_hour × $44.00
```
The 1-hour duration is baked in: riders/train × trains/hr = riders/hr, which equals riders affected in 1 hour. A suspended M&E: 400 riders × 5 trains/hr × $44 = $88,000.

**Ridership figures (peak, all windows use peak):**

| Line | Riders/Train | Trains/Hr (peak) |
|---|---|---|
| Northeast Corridor | 800 | 8 |
| North Jersey Coast | 200 | 4 |
| Morris & Essex | 400 | 5 |
| Montclair-Boonton | 350 | 3 |
| Main/Bergen County | 300 | 4 |
| Raritan Valley | 450 | 4 |
| Pascack Valley | 100 | 2 |
| Port Jervis | 300 | 2 |
| Gladstone Branch | 300 | 2 |
| Atlantic City | 260 | 2 |
| Unknown (fallback) | 400 | 3 |

These are built from OPRA ridership data and NJT timetables, checked by hand. All windows use peak figures since monitoring only occurs during rush hours.

---

### `src/aggregator.py` — Deduplication and Totals

**`deduplicate_by_train(delays)` — three buckets:**

1. **Normal trains with a train number:** keyed on `(train_number, date)`. If the same train appears twice (escalating alert: first "15 min late", then "25 min late"), keep the higher delay. The Alert Log will show both; Event Log shows only the 25-min one.

2. **System-wide events (Penn Station and line suspensions):** keyed on `(line, date)`. Keep highest cost. This prevents the same infrastructure problem from being double-counted within a window.

3. **Events with no train number and not system-wide:** kept as-is (rare).

**Important: dedup is per-window.** Morning and evening run independently. If Penn Station is suspended in both rush hours, that's two separate events — one per window — and both count toward the daily total. That's correct: it was disrupted twice.

**`calculate_totals(events)` → returns a dict with:**
- `event_count`
- `total_person_minutes`
- `total_person_hours` (rounded)
- `total_cost` (dollars, rounded to 2 decimal places)
- `lines_affected` (sorted list, excludes "Unknown" and "System-Wide (Penn Station)")

---

### `src/logger.py` — Google Sheets Writer

All Google Sheets writes happen here. Uses `gspread` + a service account credential JSON. The service account email must be added as an editor to the Google Sheet.

**Authentication:** Reads `GOOGLE_CREDENTIALS_JSON` (full contents of service account `.json` file) and `GOOGLE_SHEET_ID` (the ID from the sheet URL, between `/d/` and `/edit`).

**Tabs written to (see Google Sheet Structure below for column details):**

| Function | Tab | When called |
|---|---|---|
| `log_delay_batch()` | Event Log | Once per window, after dedup+calculate |
| `log_alert_batch()` | Alert Log | Once per window, after interpret, before dedup |
| `log_run()` | Run Log | Once per window |
| `log_run_summary()` | Run Log | After tweet posts (fills in post URI) |
| `log_tweet()` | Tweet_log | After tweet posts |
| `log_tweet()` (side effect) | for_web | After tweet posts (updates A1, A3, A4) |
| `clear_run_log()` | Run Log | Start of each run (wipes previous day) |
| `clear_alert_log()` | Alert Log | Start of each run (wipes previous day) |

**Rate limiting:** All writes are batched. `log_delay_batch()` makes one `append_rows()` call per window (all morning events at once, all evening events at once). Total Sheets API calls per run: ~10, regardless of how many delays occurred.

---

## The Google Sheet: Six Tabs

One tab accumulates forever (Tweet_log), two are fresh every day (Run Log, Alert Log), one grows daily and keeps history (Event Log), one is formula-driven (Totals), and one feeds the website (for_web).

### Tab 1: Event Log (accumulates forever — never wiped)

One row per delay event after deduplication. Written once per window by `log_delay_batch()`.

| Column | Content |
|---|---|
| A: Date | YYYY-MM-DD (in ET) of the alert |
| B: Time | HH:MM (in ET) of the alert |
| C: Line | Rail line name |
| D: Train # | Train number, or blank for system-wide events |
| E: Direction | "inbound", "outbound", "both", or "unknown" |
| F: Time Band | "peak" (always — all events are during rush) |
| G: Delay Minutes | Integer minutes (60 for cancellations; also 60 for suspensions) |
| H: Estimated Riders | Riders used in cost calculation |
| I: Dollar Estimate | Cost in dollars |
| J: Cause | Short phrase from interpreter (or "full line suspension") |
| K: Is Cancellation | "Yes" or "No" |
| L: Raw Alert Text | Original Bluesky post text |
| M: Posted to Bluesky | Always "No" — individual events are never posted; legacy column |

### Tab 2: Totals (formula-driven — never written by code)

- **B2:** `=SUM('Event Log'!I:I)` — running cumulative dollar total across all events ever logged

This tab exists so you can see the cumulative total at a glance. The formula updates automatically as Event Log grows.

### Tab 3: Tweet_log (accumulates forever — never wiped)

One row per daily summary post. 18 columns, A–R. Written by `log_tweet()` after each post.

| Column | Content |
|---|---|
| A | Timestamp (when the script ran, in ET) |
| B | Tweet text |
| C | Total cost estimate formatted as "$X,XXX.XX" |
| D | Number of delay events |
| E | Bluesky post URI |
| F | Report date (YYYY-MM-DD — the date of delays, i.e. yesterday) |
| G | Total person-hours lost (integer) — **drives the time-series line chart** |
| H | Reserved (blank) |
| I | Morning window cost (dollars, rounded) — **drives the doughnut chart** |
| J | Evening window cost (dollars, rounded) — **drives the doughnut chart** |
| K | NEC person-hours |
| L | Morris & Essex person-hours |
| M | North Jersey Coast person-hours |
| N | Main/Bergen County person-hours |
| O | Raritan Valley person-hours |
| P | Montclair-Boonton person-hours |
| Q | System-Wide (Penn Station) person-hours |
| R | Pascack Valley person-hours |

Columns K–R power the "by line" bar chart on `graphs.html`. Column G powers the time-series chart.

### Tab 4: Alert Log (wiped and rewritten at start of each daily run)

Pre-deduplication audit trail. Every interpreted alert, before any dedup logic is applied. Used for hand-checking.

| Column | Content |
|---|---|
| A: Date Seen | When the script ran |
| B: Alert Date | Date of the original alert (ET) |
| C: Alert Time | Time of the original alert (ET) |
| D: Line | Line identified |
| E: Train # | Train number |
| F: Delay Minutes | Minutes as extracted |
| G: Estimated Cost (pre-dedup) | Quick cost for reference — NOT what ends up in Event Log |
| H: Raw Alert Text | Original post text |

### Tab 5: Run Log (wiped and rewritten at start of each daily run)

Two rows per run (one for morning window, one for evening). Quick sanity check.

| Column | Content |
|---|---|
| A: Run Date | When the script ran |
| B: Period | "Morning" or "Evening" |
| C: Raw Posts Fetched | Count from watcher before any filtering |
| D: After Dedup | Count of events after deduplication |
| E: Total Cost | Window dollar total |
| F: Date of Post | Filled in after tweet fires |
| G: Time of Post | Filled in after tweet fires |
| H: Post URI | Filled in after tweet fires |

### Tab 6: for_web (feeds the public website — must be published to web)

This tab is what the website reads. It's a simple column of values the website fetches as CSV. **Must be published via File → Share → Publish to web (CSV format) for the website to work.**

| Cell | Content | Written by |
|---|---|---|
| A1 | Yesterday's total delay cost (dollars, rounded) | `log_tweet()` — automatic |
| A2 | Cumulative total since launch (dollars) | **Manual** — use `=Totals!B2` or enter directly |
| A3 | Yesterday's total person-hours | `log_tweet()` — automatic |
| A4 | Report date (YYYY-MM-DD — the date of delays = yesterday) | `log_tweet()` — automatic |
| A5 | (reserved) | — |
| A6 | Cumulative morning cost since launch | **Manual** — use `=SUM(Tweet_log!I:I)` |
| A7 | Cumulative evening cost since launch | **Manual** — use `=SUM(Tweet_log!J:J)` |

A2, A6, and A7 are not written by code — use formulas or update manually.

---

## The Website: Three Pages

All files are in `docs/`. Hosted on GitHub Pages from the `main` branch `docs/` folder. Custom domain configured via `docs/CNAME` and GoDaddy DNS.

**GitHub Pages DNS records:**
```
185.199.108.153
185.199.109.153
185.199.110.153
185.199.111.153
```

### `docs/index.html` — Main Page

Displays three numbers fetched live from the `for_web` tab:
- Most recent delay cost (A1)
- Cumulative total (A2)
- Person-hours for most recent day (A3)
- Report date label (A4, used to say "On Monday, April 21...")

**How the data fetch works:** The script at the bottom of `index.html` uses two constants:

```javascript
const PUBLISHED_ID = '2PACX-1v...';   // "published ID" from the Google Sheets publish URL
const SHEET_GID    = '1868128114';    // GID of the for_web tab (visible in URL when tab selected)
```

It fetches a CSV from Google's publish-to-web endpoint, parses each row as one cell from column A, and populates the page. No server required — pure static HTML.

**If the numbers show `$—`:** Either the for_web tab isn't published to web, `PUBLISHED_ID` or `SHEET_GID` is wrong, or A1/A2 is empty.

### `docs/graphs.html` — Data Visualizations

Uses Chart.js (loaded from CDN). Three charts:

1. **Line chart:** Daily person-hours of delay over time. Reads Tweet_log column G (person-hours) and column F (report date). Includes a 3-day moving average toggle, CSV export, and PNG export. Uses `TWEET_LOG_GID = '943972512'` — this GID must be published separately from for_web.

2. **Bar chart (horizontal):** Cumulative person-hours by rail line, from Tweet_log columns K–R.

3. **Doughnut:** Morning vs. evening share of total cost, from `for_web` A6 and A7.

**`METHODOLOGY_CHANGES` array** near the top of `graphs.html`: Draws red vertical lines on the time-series chart at methodology change dates. Currently one entry is configured. Activate by adding entries with `{ after: 'YYYY-MM-DD', label: '...' }`.

### `docs/methodology.html` — Methodology Explainer

Static HTML. No dynamic data. Update the prose here when the methodology changes (VTTS rate, ridership figures, etc.).

---

## GitHub Actions Workflows

### `daily.yml` — The Main Pipeline

**Schedule:** `cron: "0 20 * * 2-6"` — 20:00 UTC, Tuesday through Saturday  
**Timeout:** 15 minutes  
**Manual trigger:** `workflow_dispatch` with a `dry_run` input (default `true`)

Scheduled runs: `DRY_RUN=false`. Manual runs: `DRY_RUN=true` by default. You can safely click "Run workflow" in the Actions tab to test without affecting production.

### `benchmark.yml` — Historical Replay

**Manual trigger only.** Enter a past date (YYYY-MM-DD). The pipeline fetches that date's alerts from Bluesky, interprets them, calculates costs, and prints everything to the Actions log. Always `DRY_RUN=true`.

Use this any time you change the calculation logic, to confirm the output against a known date.

**How to run:** Actions tab → "NJT Benchmark" → Run workflow → enter date.

---

## Secrets Required

Set in GitHub → Settings → Secrets and variables → Actions:

| Secret name | What it is |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key (for Haiku interpretation calls) |
| `BLUESKY_HANDLE` | Bot handle, e.g. `njtdelaytracker.bsky.social` |
| `BLUESKY_PASSWORD` | Bluesky app password (generate in Bluesky settings — not your login password) |
| `GOOGLE_SHEET_ID` | Sheet ID from URL: `/d/THIS_PART/edit` |
| `GOOGLE_CREDENTIALS_JSON` | Full contents of the Google service account JSON file |

**Runtime env vars (not secrets):**

| Variable | Values | Default |
|---|---|---|
| `DRY_RUN` | `true` / `false` | `false` on schedule; `true` for manual triggers |
| `OVERRIDE_DATE` | `YYYY-MM-DD` | If set, used as "yesterday" instead of computing automatically. Used by benchmark.yml. |

---

## Key Policy Decisions

These are locked in. Don't change them without updating `methodology.html` and CLAUDE.md.

### Value of Travel Time: $44.00/hour
NJ metro area median household income ~$138k/yr → ~$66/hr gross → 2/3 × $66 = **$44.00/hr** (USDOT formula). The national default ($18.80/hr) is retained in `calculator.py` as `VTTS_NATIONAL_DEFAULT` and disclosed in methodology as a lower bound.

### Thresholds
- Minimum delay to count: **10 minutes**
- Cancellations: **60-minute assumed delay** (wait for next train during peak)
- System-wide Penn alerts: must be **≥15 minutes** to trigger system-wide treatment
- System-wide Penn assumed duration: **1 hour**
- Line suspension assumed duration: **1 hour**

### Deduplication scope
- **Per-window.** Morning and evening are deduplicated independently. If Penn is suspended in both rush hours, that's two events.
- Within a window: normal trains deduplicated by `(train_number, date)`, keep highest delay. System-wide by `(line, date)`, keep highest cost.

### Posting cadence
One post per day. No per-event posts. The post covers the previous full day (both rush hours).

---

## How to Verify a Daily Result (Hand-Check Protocol)

Use this when you want to confirm the script saw the right things.

1. **Alert Log tab** — shows every alert the script saw, before dedup. Wiped each run, so check it on the day of the run.
2. **Run Log tab** — shows raw count vs. dedup count per window. If Raw Posts = 0 and you know there were delays, something went wrong with the Bluesky fetch.
3. **Event Log tab** — final post-dedup events with costs. Spot-check: find a row and calculate `riders × (delay_min / 60) × $44.00` — should match Dollar Estimate.
4. **Bluesky manually** — open the accounts, scroll to the right time window, count qualifying rail alerts. Compare to Alert Log.

**Common reasons for legitimate slippage (expected gaps):**
- Alert uses an unrecognized line name → filtered in watcher, never appears in Alert Log. Add the phrase to `watcher.py`'s `line_map` if needed.
- Same alert from two accounts → cross-account deduped in watcher, one copy in Alert Log (correct).
- Alert posted after window closes → never fetched (expected).
- Escalating alert (15 min → 25 min) → two rows in Alert Log, one row in Event Log at 25 min (correct).
- Bus or light rail alert → intentionally filtered out.

---

## Known Bugs Fixed

### Evening window date rollover (fixed)
The evening window (3–8:30 PM ET) stays on the same ET calendar day. The old code used `now_utc.date()` and could fetch the wrong day. Fixed by computing "yesterday" from ET time and using `ZoneInfo("America/New_York")` for both windows.

### System-wide dedup bypass (fixed)
`daily.py` was once splitting system-wide events out before `deduplicate_by_train()`. This bypassed dedup entirely — escalating Penn alerts could be counted multiple times. Fixed: all events flow through `deduplicate_by_train()` together.

### Period misclassification (fixed)
Old multi-workflow design used `github.event.schedule` to determine morning vs. evening. That value is unreliable under backlog. Retired. Single `daily.py` now always processes both windows.

### Hyphenated minute regex (fixed)
"Up to 20-minute delays" wasn't captured by the original regex. Fixed: `r"up to (\d+)[- ]min"` handles both "20-minute" and "20 min".

### `process_window` return type (fixed)
Returned a plain list in some paths, but the caller expected a `(events, raw_count)` tuple. Fixed: all return paths are 2-tuples.

### `raw_count` NameError (fixed)
`raw_count` was referenced in a return statement but not assigned in the happy path. Fixed by adding `raw_count = len(raw)` after the fetch.

### Interpreter null + resolution language false resurrection (fixed)
When Haiku returned null for an alert, the fallback code would check the watcher's extracted data — but the watcher might have found a delay figure in what's actually a "service restored" message. Fixed: before applying the fallback, check raw text for resolution phrases. If found, drop the event. Only use the fallback if there's no resolution language and the watcher found a real line name and valid delay.

---

## Approximate Monthly Costs

- GitHub Actions: free (~10 min/month)
- Claude API (Haiku): ~$0.50–1.00
- Google Sheets API: free
- Bluesky API: free
- GitHub Pages: free
- **Total: under $1/month**

---

## The NJ Transit API — What Exists and What We Could Do With It

NJ Transit has a developer API portal at **developer.njtransit.com** that requires free registration. They also publish standard GTFS data and real-time feeds. Here's what's available and what it could unlock.

### What's available

**GTFS Static (schedule data)**
Standard GTFS zip file: stops, routes, trips, scheduled stop times. Freely downloadable. Not real-time — this tells you every scheduled train, when it should arrive, and at which stops. Useful for knowing whether a given train number is a peak service, what line it belongs to, and what its full route is.

**GTFS-RT (real-time feeds)**
Three feeds available through the NJT developer portal:
- **Trip Updates** — actual vs. scheduled arrival/departure times for all active trains, updated frequently
- **Vehicle Positions** — GPS coordinates of trains in service
- **Service Alerts** — structured delay/disruption notices (similar to Bluesky alerts, but machine-readable and more complete)

**DepartureVision**
NJT's real-time departure board for every station, accessible programmatically. Various third-party apps use it.

**Developer portal APIs**
Registration at developer.njtransit.com gives access to structured rail schedule data, station data, and real-time departures via documented endpoints.

### What this could unlock for the tracker

**1. Ground-truth delay data instead of Bluesky alert text**
The GTFS-RT Trip Updates feed directly measures "Train 3876 was scheduled at 9:28 AM but arrived at 9:54 AM — 26 minutes late" for every train, not just ones NJT chose to post an alert about. This would be more accurate and comprehensive than alert parsing. Trade-off: more complex to implement (need to join GTFS-RT trip IDs against static GTFS to get train numbers, handle cancelled trips, etc.).

**2. Catching delays NJT never alerted on**
NJT only posts Bluesky alerts for notable delays. A 12-minute delay may never get a post. GTFS-RT would catch everything ≥10 minutes regardless of whether an alert was posted.

**3. More precise ridership from actual schedule frequency**
Right now ridership is a static estimate per line. With GTFS schedule data, we could count actual trains per hour on a given day (schedule changes seasonally) and multiply against ridership-per-train for a more precise estimate.

**4. On-time performance benchmarking**
GTFS-RT lets you calculate an on-time rate for any day, line, or station. New website feature: "In the past month, the NEC was on time X% of mornings."

**5. Station-level delay data**
Rather than line-level averages, we could report how many minutes the average commuter at New Brunswick or Metropark was delayed — giving the cost a geographic dimension useful for advocacy.

**6. Replacing Bluesky as the primary data source**
The GTFS-RT Service Alerts feed is the structured equivalent of what we read from Bluesky. We could use it as a backup source, or switch to it entirely as the primary source to reduce dependence on alert bot accounts that could change their format or disappear.

### Recommended next step

Register at developer.njtransit.com and get API credentials. Then run a test: fetch the GTFS-RT Trip Updates feed for a single day and compare it to the Bluesky alerts logged in the Alert Log for the same day. The key question: does GTFS-RT cover all lines including Pascack Valley, Port Jervis, and RVL — or is it patchy for smaller lines? Some NJT data sources historically favor the main NEC corridor. The answer determines whether GTFS-RT can replace Bluesky or just supplement it.

---

## Validation Reference

**Benchmark result (Monday, March 31, 2026):**
> "On Monday, NJ Transit delayed commuters for a total of 30,918 person-hours across both rush hours. City employers lost $742,040 in productive working time. (30 delay events across 5 lines)"

This is the canonical test case. If you change the cost calculation logic, run the benchmark against this date and confirm the output changed (or didn't) as intended.

---

## Methodology (Approved Summary)

VTTS rate of $44.00/hr derived from USDOT formula applied to NJ metro area median household income (~$138k, NJ metro area). Rider counts are estimates from aggregate system data, not real-time measurements. Only delays ≥10 min on commuter rail lines are included. Figures represent opportunity costs, not cash losses. Not affiliated with NJ Transit.

Full white paper: `NJT_Delay_Tracker_Methodology.docx`

---

*Last updated: May 14, 2026. Built collaboratively with Claude (Anthropic).*
