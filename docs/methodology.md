*Work in Progress*

---

## Scraper

The core of the Delay Tracker is a scraper that pulls matching NJT delay alerts from a series of automated Bluesky accounts, chiefly [NJMetroAlert](https://bsky.app/profile/njmetroalert.bsky.social). Claude (Haiku) parses the alerts for line, train number, and delay information, then deduplicates by preserving the longest delay per train number.

One problem for this method is that we don't capture systemwide delays — the dreaded notification regarding "all traffic to and from Penn Station." We treat Penn delays as their own "line," taking the longest reported delay and basing the number of riders affected on an assumption of …

---

## Ridership

We model riders per train during the AM and PM rush based on average weekday ridership estimates obtained through (someone else's) OPRA. Specifically, we use data posted to Reddit by another person interested in transit patterns. You can find it [here](https://www.reddit.com/r/nycrail/comments/1jyki9k/nj_transit_boarding_data_by_line_station/). We host the data [here](opra-njt-files/NJT%20OPRA%20Ridership%20Data.zip) to guard against future deletion.

Unfortunately, NJ Transit does not break down the percentages of their ridership traveling on- and off-peak, so we have to estimate how many daily riders are commuters. We do this through a three-step process.

First, we break down lines by whether they are predominantly commuter lines or serve other functions. That categorization is based on the falloff between weekday and weekend ridership.

Second, we ballpark peak ridership ratios using figures from a similar regional system. We know the Long Island Railroad now serves approximately 50 percent of its customers during peak hours. But unlike the LIRR, where off-peak usage has surpassed pre-pandemic levels, public testimony indicates peak ridership in New Jersey recovered faster post-Covid 19.

With that in mind, last, we treat the LIRR peak-hour percentage as a lower bound of peak-period riders and apply it to those lines less frequented by commuters. We then apply a slightly higher percentage to commuter-heavy lines, like Montclair-Boonton. We inflate ratios slightly because our tracker scrapes data from an hour or so before and after peak periods.

To arrive at our per-rush-hour train ridership figures, we divide our new by-line estimates of daily peak commuters by the sum of morning and evening rush trains. Those were calculated from GTFS data using Claude Code and then checked by hand. We then round up to the nearest 50.

Results are presented below. We're always interested in improving this process. Please get in touch with any ideas. No need to recommend an OPRA request for peak ridership data; we're already on it.

| Line | Average Weekday Passengers | Average Riders Per Train (Peak) |
| --- | ---: | ---: |
| Northeast Corridor | 58,075 | 800 |
| Coast Line | 8,200 | 200 |
| Raritan Valley Line | 5,125 | 450 |
| Morris & Essex | 15,950 | 400 |
| Montclair-Boonton | 2,125 | 350 |
| Main Line Bergen County | 6,650 | 300 |
| Pascack Valley | 1,325 | 100 |

---

## Changelog

This section notes all changes to the methodology over time. Given that our estimates are rough, I try to avoid "hard breaks" in the trend — entailing tossing all previous data and starting from scratch. Instead, methodology changes are noted on the line graph so people can draw their own conclusions.

- **3/31/26:** Launch for tracking and testing.
- **5/4/26:** Soft launch. Lowered per train rider counts based on new GTFS data, raised hourly cost in two ways — increased base salary per regional report, lowered productivity during delays. Noted change on line graph. No hard break in trend as revisions expected to cancel each other out.
