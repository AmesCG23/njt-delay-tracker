# NJT Delay Tracker

Every weekday, this pipeline reads NJ Transit's own rail service alerts, estimates what the previous day's delays cost commuters and New York City employers in lost productive time, and publishes the results.

**Website:** [bettertrains.org](https://bettertrains.org)
**Bluesky bot:** [@njtdelaytracker.bsky.social](https://bsky.app/profile/njtdelaytracker.bsky.social)

---

## How It Works

Once a day (Tuesday–Saturday, covering the previous day's morning and evening rush hours), GitHub Actions runs a five-stage pipeline:

1. **Watcher** — fetches NJ Transit service alerts posted to Bluesky by automated alert accounts
2. **Interpreter** — Claude (Haiku) parses each alert into line, train number, and delay minutes
3. **Aggregator** — deduplicates escalating alerts, keeping one event per train per rush window
4. **Calculator** — estimates cost: riders per train × delay time × value of travel time
5. **Logger / Poster** — writes a full audit trail to Google Sheets and posts one daily summary to Bluesky

Only delays of **10 minutes or more** on **commuter rail lines** are counted. Buses and light rail are excluded.

## Methodology

Delay time is valued at **$44.00/hour**: the USDOT value-of-travel-time method (delayed time is worth about one-third of working time) applied to North Jersey commuter earnings of roughly $138k/year. Riders per train are modeled per line from NJ Transit ridership records obtained via OPRA and GTFS schedules.

Figures are estimates of opportunity cost — not cash losses, and not an official accounting. [Full methodology →](https://bettertrains.org/methodology.html)

The whole system runs on GitHub Actions for under $1/month.

---

## Disclaimer

*This project is not endorsed by, affiliated with, or sponsored by NJ TRANSIT.*

---

*An independent public accountability project.*
