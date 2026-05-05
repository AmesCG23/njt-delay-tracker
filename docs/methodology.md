This tracker's methodology remains a work in progress. We hope to refine it by obtaining better data through OPRA requests. Until then, here is how the system works.


## Scraper

The core of the Delay Tracker is a scraper that pulls matching NJT delay alerts from a series of automated Bluesky accounts, chiefly [NJMetroAlert](https://bsky.app/profile/njmetroalert.bsky.social). Claude (Haiku) parses the alerts for line, train number, and delay information, then deduplicates by preserving the longest delay per train number.

The scraper looks for delays between 5:00 AM and 10:30 AM for the morning rush, and 3:00 PM and 8:30 PM for the evening rush. These are larger windows than traditional "peak" times but the effects of this wider "net" should cancel out without biasing our estimates. On the one hand, we observe more delays. On the other hand, we estimate lower ridership *per* train (see next section).

One feature of New Jersey commuting is the dreaded notification regarding "all traffic to and from Penn Station." We treat Penn delays as their own "line," taking the longest reported delay and matching with estimated *hourly* trips through Penn Station, calculated below.


## Ridership

We model riders per train during the AM and PM rush based on average weekday ridership estimates obtained through (someone else's) OPRA. Specifically, we use data posted to Reddit by another person interested in transit patterns. You can find it [here](https://www.reddit.com/r/NJTransit/comments/1jykihh/nj_transit_boarding_data_by_line_station/). We have a backup of the data internally to guard against potential deletion.

Unfortunately, NJ Transit does not break down the percentages of their ridership traveling on- and off-peak, so we have to estimate how many daily riders are commuters. We do this through a three-step process.

First, we break down lines by whether they are predominantly commuter lines or serve other functions. That categorization is based on the falloff between weekday and weekend ridership.

Second, we ballpark peak ridership ratios using figures from a similar regional system. We know the Long Island Railroad [now serves](https://www.mta.info/document/170826) approximately 50 percent of its customers during peak hours. But unlike the LIRR, where off-peak usage has surpassed pre-pandemic levels, [reporting we've seen](https://www.njspotlightnews.org/2023/11/nj-commuters-boost-regional-economy-rpa-report/) indicates peak ridership in New Jersey recovered faster post-Covid 19.

With that in mind, last, we treat the LIRR peak-hour percentage as a lower bound of peak-period riders and apply it to those lines less frequented by commuters. We then apply a slightly higher percentage to commuter-heavy lines, like Montclair-Boonton. We inflate ratios slightly because our tracker scrapes data from an hour or so before and after peak periods.

To arrive at our per-rush-hour train ridership figures, we divide our new by-line estimates of daily peak commuters by the sum of morning and evening rush trains. Those were calculated from GTFS data using Claude Code and then checked by hand. We then round up to the nearest 50.

Last, we use an hourly estimate of Penn Station ridership to calculate the impact of systemwide delays — like the dreaded Portal Bridge failure. We start with the Regional Plan Association's [figure](https://rpa.org/work/reports/the-value-of-nj-transit_) of roughly 65,000 people departing from Penn Station for New Jersey every day. Assuming an equal number coming *into* Penn from New Jersey, and 60 percent of those total trips during peak hours, gives a figure of 9,600 passengers per hour.

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
| NY Penn Station (systemwide) | — | 9,600, modeled hourly |


## Costs

Delays are then matched to "cost" based on the expected hourly earnings of commuters and a "discount" representing how much or how little of their job they can do during a train delay. We start with a relatively high estimate for annual earnings — around $138,000 — that works out to around $66 per hour. That figure is based on [the reported earnings of North Jersey commuters](https://www.njspotlightnews.org/2023/11/nj-commuters-boost-regional-economy-rpa-report/) and defensible as a systemwide figure given NJT's heavy slant toward serving the north of the state.

The core of our project is the idea that NJT rail delays create hidden costs for *everyone* with a stake in the region's success — including New York City employers. We could theoretically treat all delayed time as a full "loss" to the regional economy. But that would be an aggressive assumption that's not fully reflective of reality. Commuters *can* and *do* use their time while delayed — to the best of their ability — both for work and for pleasure. But it's not enjoyable or particularly efficient. Many of the delays we observe represent hours people are spending jammed shoulder to shoulder in dark, overheated, immobile trains. What work can they realistically do? How do we account for it?

Thankfully, there's a robust literature in transit policy on how to "value" time spent in transit. We draw inspiration from one of the [most recent government summaries](https://www.transportation.gov/office-policy/transportation-policy/revised-departmental-guidance-valuation-travel-time-economic) of how to value transit time *savings* to assume that time spent while delayed in transit is about one-third as "useful" as time on the job. That works out to a "cost" of $44 per commuter per hour of delay.

We think this is a fair estimate — even a generous one. If you doubt us, try to send a particularly important email while holding your backpack on your lap *and* jammed into the middle seat of a 75-year-old train car. Then try it again standing for an hour.


## Changelog

This section notes all changes to the methodology over time. Given that our estimates are rough, I try to avoid "hard breaks" in the trend — entailing tossing all previous data and starting from scratch. Instead, methodology changes are noted on the line graph so people can draw their own conclusions.

- **3/31/26:** Launch for tracking and testing.
- **5/4/26:** Soft launch. Lowered per train rider counts based on new GTFS data, raised hourly cost in two ways — increased base salary per regional report, lowered productivity during delays. Noted change on line graph. No hard break in trend as revisions expected to cancel each other out.
