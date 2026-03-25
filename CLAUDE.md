# CLAUDE.md — NJ Transit Delay Cost Tracker

This file gives Claude instant project context. Paste it at the start of any new conversation to resume work without re-explaining decisions.

---

## What This Project Is

An automated pipeline that monitors NJ Transit commuter rail delays in real time, estimates the economic cost of lost worker time, logs each event to a Google Sheet, and posts the result to a Bluesky bot account. A companion static website displays running totals updated weekly.

The goal is to make the economic cost of transit failures visible to the public and to policymakers — one delay at a time.

**Owner:** Ames (Senior Counsel, Brennan Center for Justice — background in criminal justice policy, not software engineering. Treat all technical explanations accordingly: plain English first, code second.)

---

## Architecture: The 5-Station Pipeline

```
[NJT GTFS-RT Feed]
       |
       v
Station 1 — WATCHER
  Polls NJ Transit GTFS-Realtime feed every 5 minutes via GitHub Actions cron job.
  Filters for new service alerts and trip updates only.
  Deduplicates against previously-seen alert IDs (stored in a small JSON state file).
       |
       v
Station 2 — INTERPRETER
  Passes raw alert text to Claude API (Haiku model) for structured extraction.
  Output: { line, delay_minutes, direction, time_of_day, alert_type }
  Filters out delays under 10 minutes — these do not get logged or posted.
       |
       v
Station 3 — CALCULATOR
  Looks up riders_per_train from ridership proxy table (see below).
  Uses time-of-day band: peak / off_peak / weekend.
  Formula: riders × (delay_minutes / 60) × VTTS_RATE = dollar_estimate
  Also maintains running annual total (read from Google Sheet Tab 2 before writing).
       |
       v
Station 4 — LOGGER
  Appends one row to Google Sheet Tab 1 (Event Log).
  Reads updated totals from Tab 2 (auto-calculated via sheet formulas).
       |
       v
Station 5 — POSTER
  Formats post text using Bluesky post template (see below).
  Posts to bot account via atproto Python library.
```

---

## Tech Stack

| Component | Tool | Notes |
|---|---|---|
| Language | Python 3.11+ | All code in /src/ |
| Scheduler | GitHub Actions | Cron: every 5 min. Free tier sufficient. |
| Data feed | NJT GTFS-Realtime | Via developer.njtransit.com (API key pending approval) |
| AI parsing | Claude API — Haiku | Low cost ~$1-3/month at this volume |
| Spreadsheet | Google Sheets API | Via gspread Python library |
| Social | Bluesky AT Protocol | Via atproto Python library |
| Website | Static HTML/CSS/JS | Hosted on Netlify free tier |
| Secrets | GitHub Actions Secrets | API keys, Bluesky credentials stored here — never in code |

---

## Key Decisions — Locked In

### Value of Travel Time (VTTS)
- **Primary rate: $24.00/hour**
- Methodology: USDOT standard formula (50% of median hourly household income), applied to NJ median HHI of $99,781 (2023 Census ACS, second-highest in nation).
- National baseline for disclosure: $18.80/hour (USDOT default using national median).
- Rationale: NJT rail riders are disproportionately white-collar NYC commuters. NJ state median is the appropriate and defensible geographic adjustment. Rail-only scope (not bus) further justifies the higher figure.
- Source to cite: U.S. Census Bureau 2023 ACS; USDOT Revised Departmental Guidance on Valuation of Travel Time.

### Minimum Delay Threshold
- **10 minutes.** Delays under 10 minutes are not logged, not calculated, not posted.
- Rationale: Sub-10-minute delays are often recovered in transit and may not materially affect riders' plans. This makes estimates more conservative and more defensible.

### Scope
- **Rail only.** No bus delays.
- Rationale: Rail riders skew higher-income white-collar commuters; VTTS adjustment is appropriate. Bus rider demographics are more economically diverse and would require a different methodology.

### Ridership Proxy Table
NJT does not publish per-line or per-train ridership. This table is derived from: total system annual ridership (62M, 2025), Wikipedia-sourced train frequency counts per line, and RPA anchor data (63,014 daily Penn Station boardings). Peak = 6–9am inbound / 4–7pm outbound on weekdays.

```python
RIDERS_PER_TRAIN = {
    "Northeast Corridor": {
        "peak": 825, "off_peak": 250, "weekend": 180
    },
    "Morris & Essex": {
        "peak": 550, "off_peak": 175, "weekend": 130
    },
    "North Jersey Coast": {
        "peak": 500, "off_peak": 150, "weekend": 120
    },
    "Raritan Valley": {
        "peak": 450, "off_peak": 125, "weekend": 100
    },
    "Montclair-Boonton": {
        "peak": 415, "off_peak": 110, "weekend": 90
    },
    "Main Bergen County": {
        "peak": 450, "off_peak": 120, "weekend": 95
    },
    "Pascack Valley": {
        "peak": 315, "off_peak": 85, "weekend": 65
    },
    "Port Jervis": {
        "peak": 300, "off_peak": 75, "weekend": 60
    },
    "Gladstone Branch": {
        "peak": 300, "off_peak": 80, "weekend": 60
    },
    "Atlantic City": {
        "peak": 260, "off_peak": 100, "weekend": 90
    }
}
```

**Caveats to disclose on website:**
1. Rider counts are schedule-based averages, not real-time actuals.
2. Individual trains vary in consist length; averages are used.
3. VTTS represents the opportunity cost of time, not cash-in-hand loss.
4. Post-pandemic ridership (2025 figures) is approximately 75–85% of 2019 levels.

---

## Google Sheet Structure

**Single file, two tabs.**

### Tab 1: Event Log (Python writes here)
| Column | Content |
|---|---|
| Date | YYYY-MM-DD |
| Time | HH:MM (24hr) |
| Line | e.g. "Northeast Corridor" |
| Train # | From alert if available, else blank |
| Direction | Inbound / Outbound / Unknown |
| Delay_Minutes | Integer |
| Time_Band | peak / off_peak / weekend |
| Estimated_Riders | From lookup table |
| Dollar_Estimate | Float, 2 decimal places |
| Raw_Alert_Text | Full original alert string |
| Posted_to_Bluesky | TRUE / FALSE |
| Bluesky_Post_URI | URI string if posted |

### Tab 2: Totals (Formula-driven, Python reads but does not write)
- Annual running total ($)
- Total delay events logged
- Total delay minutes
- Worst single delay (minutes)
- Highest single-event cost ($)
- Last updated timestamp

---

## Bluesky Configuration

- **Account type:** Dedicated bot account (not personal account)
- **Credentials:** Stored in GitHub Actions Secrets as `BLUESKY_HANDLE` and `BLUESKY_PASSWORD`
- **Library:** atproto (Python) — `pip install atproto`
- **Post character limit:** 300

### Post Template
```
NJ Transit [LINE NAME]: [X]-min delay, [direction].
~[RIDERS] commuters affected.
Estimated lost worker time: $[AMOUNT]

2025 running total: $[RUNNING_TOTAL]
[link to website]
```

Example:
```
NJ Transit Northeast Corridor: 23-min delay, inbound.
~825 commuters affected.
Estimated lost worker time: $7,590

2025 running total: $1,243,800
njtdelaycost.com
```

---

## Data Source

- **NJT GTFS-Realtime feed:** Available via developer.njtransit.com (free, requires registration)
- **API key status:** Application submitted. Awaiting NJT approval (may take several days).
- **Feed types we use:** Trip Updates (delay minutes) + Service Alerts (disruption text)
- **Polling interval:** Every 5 minutes via GitHub Actions cron
- **Python library for parsing:** `gtfs-realtime-bindings` or `protobuf`

---

## Methodology Statement (for website)

> This tracker estimates the economic cost of NJ Transit rail delays using the U.S. Department of Transportation's standard Value of Travel Time methodology, adjusted to reflect New Jersey's median household income ($99,781, 2023 U.S. Census). We apply a rate of $24.00 per hour — consistent with USDOT guidance that personal travel time is valued at 50% of median hourly household income. The national USDOT default of $18.80/hour is disclosed as a lower-bound alternative.
>
> Rider counts per train are estimates based on system-wide annual ridership (62 million trips, 2025) distributed proportionally across lines and adjusted for time of day. They are not real-time measurements. Only delays of 10 minutes or more are included. This tracker covers commuter rail only; bus delays are not included.
>
> These figures represent estimated opportunity costs — the value of time that could have been spent otherwise — not cash losses. They are intended to illustrate the aggregate scale of service disruptions, not to provide precise accounting.

---

## Project Status (as of session ending March 23, 2026)

| Phase | Status | Notes |
|---|---|---|
| 1 — Brainstorming | ✅ Complete | All decisions documented |
| 2 — Research | 🟡 Mostly complete | API key pending NJT approval |
| 3 — Design | ⬜ Not started | Ready to begin; API key not required to start |
| 4 — Coding: Mechanism | ⬜ Not started | |
| 5 — Coding: Website | ⬜ Not started | |
| 6 — Polish | ⬜ Not started | |
| 7 — Testing | ⬜ Not started | |
| 8 — Launch | ⬜ Not started | |

---

## Immediate Next Steps

1. **You:** Check email for NJT API key approval. When received, store key somewhere safe.
2. **Claude:** Run Phase 3 (Design) — Google Sheet schema detail, Bluesky post templates, edge case rules, website wireframe. Can start this before API key arrives.
3. **Open questions still to decide:**
   - Does the website have an RSS feed or email list?
   - What is the public URL / domain name?

---

## Repository Structure (planned)

```
/
├── CLAUDE.md                  ← this file
├── README.md                  ← public-facing project description
├── requirements.txt           ← Python dependencies
├── .github/
│   └── workflows/
│       └── run_pipeline.yml   ← GitHub Actions cron config
├── src/
│   ├── main.py                ← orchestrator (calls all 5 stations)
│   ├── watcher.py             ← Station 1: GTFS feed poller
│   ├── interpreter.py         ← Station 2: Claude API parser
│   ├── calculator.py          ← Station 3: cost math + ridership table
│   ├── logger.py              ← Station 4: Google Sheets writer
│   ├── poster.py              ← Station 5: Bluesky poster
│   └── config.py              ← constants (VTTS rate, thresholds, etc.)
├── data/
│   └── seen_alerts.json       ← deduplication state file
└── website/
    ├── index.html
    ├── style.css
    └── data.json              ← exported weekly from Google Sheet
```

---

*Last updated: March 23, 2026. Generated collaboratively with Claude (Anthropic).*
