# NJT Delay Cost Tracker

An automated pipeline that monitors NJ Transit commuter rail delays, estimates the economic cost in lost worker time, and posts results to Bluesky.

**Live bot:** [@njtdelaycost.bsky.social](https://bsky.app/profile/njtdelaycost.bsky.social)
**Website:** [njtdelaycost.com](https://njtdelaycost.com) *(coming soon)*

---

## How It Works

Every 5 minutes, GitHub Actions runs a 5-station pipeline:

1. **Watcher** — Scrapes NJT's public travel alerts page
2. **Interpreter** — Claude Haiku parses each alert into structured data
3. **Calculator** — Estimates dollar cost using USDOT value-of-time methodology
4. **Logger** — Writes to a Google Sheet
5. **Poster** — Posts to Bluesky

Only delays of **10 minutes or more** on **commuter rail lines** are counted.

## Methodology

Cost is estimated using the U.S. Department of Transportation's Value of Travel Time methodology, adjusted to New Jersey's median household income ($99,781, 2023 Census ACS): **$24.00/hour**.

Rider counts are schedule-based averages — not real-time measurements. [Full methodology →](https://njtdelaycost.com#methodology)

---

## Disclaimer

*Data provided in part by NJ TRANSIT. This project is not endorsed by, affiliated with, or sponsored by NJ TRANSIT.*

---

*An independent public accountability project.*
