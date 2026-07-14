# handoff.md — Marketing Strategy Briefing for the NJT Delay Tracker

**How to use this file:** Give it to Claude (or any strategist) as the starting brief for developing real-world marketing, press, and growth strategies for this project. It explains what the project is, how the machinery works, what the vision is, what assets and constraints exist, and where the open questions are. Technical reference lives in `CLAUDE.md`; this file is the strategy-facing summary.

*Note: this file lives in a public repository. It is written accordingly.*

---

## The project in one paragraph

Every weekday, NJ Transit trains run late, and the cost of that lateness — thousands of commuter-hours, hundreds of thousands of dollars in lost productive time — evaporates without ever being counted. This project counts it. An automated pipeline reads NJ Transit's own service alerts every day, estimates the economic cost of the previous day's rush-hour delays using a documented, defensible methodology, and publishes the running tally: a website with the headline numbers ([bettertrains.org](https://bettertrains.org)), a daily Bluesky post ([@njtdelaytracker.bsky.social](https://bsky.app/profile/njtdelaytracker.bsky.social)), and a public data trail. Since tracking began on March 31, 2026, the meter has been running — and the number only goes up.

## The thesis

From the homepage, and the closest thing to a mission statement:

> "Fixing New Jersey Transit will be expensive, but letting it slowly get worse, day by day, will cost more. Nothing will change until policymakers and business leaders realize that."

The core positioning insight: **NJ Transit delays are not a New Jersey problem — they are a New York/New Jersey regional economic problem.** Roughly 65,000 people a day ride NJ Transit into Penn Station. When trains stall in the Hudson tunnels, it's New York City employers who lose the morning's work, alongside the commuters who lose their time. The tracker deliberately prices delays in terms both audiences understand: dollars of lost productive time, valued at $44/hour using US DOT methodology applied to North Jersey commuter earnings (~$138k/year median). NY employers have a stake; the framing should keep inviting them in.

The numbers are estimates and are always presented as estimates — that honesty is a feature. The methodology page shows all the work, cites public sources (OPRA ridership records, RPA reports, USDOT guidance, GTFS schedules), and maintains a changelog. Defensibility is the project's core asset: it must survive contact with a skeptical reporter, a transit agency spokesperson, or a hostile reply guy.

## What exists today (the surfaces marketing can build on)

| Surface | What it is |
|---|---|
| **bettertrains.org** | Homepage with yesterday's cost, the cumulative total since April 2026, and the thesis. Newsprint-style design, EB Garamond, deliberately restrained. |
| **/graphs.html** | Charts: person-hours lost per day (with 3-day average, CSV + PNG export), cumulative hours by rail line, morning vs. evening rush split. |
| **/methodology.html** | The full methodology, written in a plain, direct voice. The credibility anchor. |
| **Bluesky bot** | One post per day, ~8:30–9am ET, covering the previous day. AI-drafted in a controlled voice (see below), with a link card back to the site. Bluesky provides an RSS feed of it. |
| **Daily social card** | `og-card.png` is redrawn every day with the live cumulative total, so every link share of the site displays the current number. |
| **Public data** | `bettertrains.org/data/latest.json` (daily snapshot), a published CSV of the full daily history, and CSV export on the charts page. Journalists and researchers can self-serve. |
| **Ko-fi** | ko-fi.com/bettertrains — low-key donation link in the site footer. |
| **Contact** | bettertrains@proton.me |

## How the machine works (what's possible and what isn't)

Plain-English version — full detail in `CLAUDE.md`:

1. **Once a day** (Tuesday–Saturday, covering Monday–Friday's service), a GitHub Actions job fetches NJ Transit service alerts posted to Bluesky by automated alert accounts, for yesterday's morning (5:00–10:30am ET) and evening (3:00–8:30pm ET) rush windows.
2. **Claude (Haiku)** parses each alert into structured data: line, train number, delay minutes, cause.
3. Escalating alerts are deduplicated (one event per train per window, longest delay wins). Only commuter-rail delays ≥10 minutes count. Penn Station system-wide disruptions and line suspensions get their own models.
4. **The calculator** prices each event: riders per train (modeled per line from OPRA ridership data) × delay time × $44/hour.
5. Everything is logged to a Google Sheet (full audit trail, one row per event), the website's numbers update, and **Claude (Sonnet) drafts the daily post** from a fact sheet of the day's numbers — with a fixed template as fallback.
6. The whole system costs **under $1/month** to operate and runs unattended.

**Implications for marketing:**
- The cadence is **daily, next-morning** — not real-time. Strategies that need live "the NEC is melting down right now" posting don't fit the current architecture (though the underlying alert stream exists, and a real-time mode is conceivable future work).
- The data trail is rich: per-line breakdowns, morning/evening splits, records, streaks, 30-day averages, and cumulative milestones are already computed for the daily post. Milestone moments ("$10M since April") are cheap to productize.
- Anything automated must fit the project's fail-safe pattern: features degrade gracefully and can be turned off with one flag. Marketing automations should propose the same discipline.
- A roadmap exists (documented in `CLAUDE.md`) to move from alert-parsing to NJ Transit's GTFS-RT real-time feeds — which would unlock on-time-rate stats ("the NEC was on time X% of mornings this month"), station-level figures, and delays NJT never alerted on. Each of those is also a content engine.

## Voice

The daily posts are written in the voice of **a Daily Rider**: someone on these trains every day, keeping score. Weary but precise. The numbers carry the weight, so the writing never shouts. Occasionally first-person plural. Never official, never wonky, a dry wit ("We are nothing if not consistent"). The 404 page reads: "This page is operating up to 60 minutes behind schedule — or it may have been cancelled altogether."

Hard rules already encoded in the post composer (`src/post_library.json`) that any marketing voice must respect:

- **The system is held to account — never crews, conductors, engineers, or any individual worker.**
- No speculation about causes of specific delays.
- Red-line topics where the bot goes silent and the fixed template takes over: injuries, fatalities, medical emergencies, police activity, anything involving death or terror. **Never joke near tragedy.** A delay caused by someone being struck by a train is not content.
- Plain text, no hashtags, understatement over hype. Say "hours," not "person-hours."
- Figures are always presented as estimates; never overclaim precision.

## Identity (in progress — decisions not yet made)

The current name ("NJT Delay Tracker") and domain (bettertrains.org) are functional placeholders. Naming exploration to date, for a name that invokes the shared commuter experience:

- **The 7:14** (or similar train time) — insider shorthand feel
- **Residual Delays** — NJT's own euphemism turned against them
- **Making All Stops** — the announcement everyone hears
- **Departure Vision** — NJT's own product name, used cheekily
- **Portal** — after Portal Bridge, the actual bottleneck

Creator persona candidates (anonymous by design):

- **The Dispatcher** — institutional knowledge, knows where all the trains are
- **The Flagman** — raising a flag on a problem; railroad vernacular
- **A Daily Rider** — humble, relatable, impossible to attack *(currently the de facto voice of the daily posts)*

A marketing engagement should either pressure-test these or recommend committing to one; the site footer currently describes the project as "a project of BetterTrains.org, a nonprofit that doesn't exist yet."

## Constraints (non-negotiable unless the owner says otherwise)

1. **Anonymity.** The tracker is the work of one person, writing anonymously, plus Claude. The footer literally says "No doxxing, please." Strategies must not require the creator's name, face, employer, or in-person appearances. (Note: the persona is anonymous; the plumbing — a public GitHub repo — is only lightly pseudonymous. If anonymity ever becomes high-stakes, that gap needs its own review.)
2. **Independence.** Not affiliated with, endorsed by, or sponsored by NJ Transit — stated on every page, and legally prudent to keep prominent given "NJT" appears in the project name. Be careful with NJ Transit's marks and logo.
3. **Honest numbers.** Estimates presented as estimates; methodology changes are disclosed in a changelog and flagged on the charts. Nothing that trades credibility for reach.
4. **Budget ethos.** The pipeline runs on under $1/month and one person's limited spare time. Marketing plans should be similarly leveraged: automation, earned media, and compounding assets over anything requiring sustained manual effort or spend.
5. **Punch up.** The target is institutional failure and underinvestment — never workers, and not riders.

## Audiences (in rough priority order)

1. **NJ Transit commuters** — the constituency. They live the delays; the tracker validates their experience with numbers. Growth here = social following + word of mouth ("have you seen the delay cost site?").
2. **Journalists** — transit beat (NJ.com, NJ Spotlight, Gothamist, amNY), business press, and data journalists. The tracker is a citable, always-current statistic factory: "According to bettertrains.org, delays this month cost commuters $X." The site is now structured (Dataset markup, CSV/JSON endpoints, methodology page) to make citation frictionless.
3. **NY/NJ employers and business organizations** — chambers of commerce, Partnership for NYC-type groups, real estate interests. The framing "your payroll is stuck in the Hudson tunnels" gives them a self-interested reason to amplify and to lobby.
4. **Policymakers and advocates** — NJ legislators, the Gateway Program constituency, transit advocacy groups (Tri-State Transportation Campaign, RPA). The tracker is ammunition: a running, undeniable cost-of-inaction counter.
5. **AI assistants** — increasingly how people ask "how bad are NJ Transit delays?" The site is now optimized to be read and cited by AI crawlers (see below).

## Distribution state of play (July 2026)

- **Bluesky**: the only active channel. Daily cadence established; follower count is small and organic. No presence on X/Twitter, Threads, Instagram, TikTok, or Reddit — all open questions for strategy (Reddit's r/NJTransit is where the OPRA ridership data itself came from; that community is primed for this).
- **SEO / AI crawlability**: a July 2026 engineering pass made the site fully legible to search engines and AI crawlers — the live figures are now baked into the static HTML daily (previously invisible to non-JS crawlers), the methodology is readable without JavaScript, and the site carries schema.org Dataset markup, `llms.txt`, a machine-readable JSON snapshot, sitemap freshness signals, and an explicit all-crawlers-welcome robots policy. Remaining *human* to-dos: register the site in Google Search Console and Bing Webmaster Tools and submit the sitemap; consider a data license statement (e.g. CC BY 4.0) so reuse terms are unambiguous.
- **Earned media**: none attempted yet. No press kit exists.
- **Seasonal stunts**: precedent exists — a "World Cup Mode" easter egg (floating soccer balls and vuvuzelas, Geocities-style toggle) runs on the homepage June 13–July 31, 2026. Playfulness is on-brand when it doesn't touch the numbers.
- **Support**: Ko-fi link exists; no membership, newsletter, or merch.

## Assets a strategist should know exist

- **The cumulative counter** — a single, ever-growing dollar figure with a date anchor ("since April 2026"). It is the brand. Milestone crossings ($5M, $10M, $25M) are natural news pegs, and the composer already knows about them.
- **Records and streaks** — worst day on record, days-since-on-time, 30-day averages: all computed daily for the post.
- **Per-line league table** — cumulative hours by line makes line-specific content possible ("the M&E has now eaten 40,000 hours of its riders' lives").
- **The daily social card** — every share of the site shows the live total; any new page or campaign inherits this.
- **Exportable data** — CSV/JSON that journalists, academics, and advocacy groups can use without asking permission (a license statement would formalize this).
- **A benchmark day for credibility demos** — Monday, March 31, 2026: 30,918 person-hours, $742,040, 30 delay events across 5 lines, in a single day.

## What the marketing engagement should produce

Real-world strategies, prioritized by leverage-per-hour-of-owner-time, ideally including:

1. **A naming/identity recommendation** (commit, rename, or hybrid) with the domain and handle implications worked out.
2. **A press strategy**: which outlets/reporters, what the pitch is (the running counter? a milestone? a worst-week story?), and a lightweight press kit (one-pager, methodology summary, quotable figures, contact protocol that preserves anonymity).
3. **A social/channel strategy**: whether and how to expand beyond Bluesky (Reddit, Instagram-friendly chart cards, a weekly email digest), and what can be automated within the pipeline's fail-safe pattern.
4. **Audience-specific plays**: e.g., a quarterly "cost to NYC employers" number packaged for business press; a legislative-session-timed figure for Trenton; commuter-facing "your line this month" content.
5. **Moment planning**: milestone crossings, the World Cup mode sunset (July 31), major service meltdowns (the tracker's traffic spikes when NJT fails — how do we capture that attention next-morning?), and Gateway/Portal Bridge news cycles.
6. **Measurement**: what counts as success (citations, followers, sheet/CSV hits, inbound press) given there is currently no analytics on the site at all — recommending a privacy-respecting analytics setup is in scope.

Anything proposed should respect the constraints section above — especially anonymity, honesty about estimates, and the punch-up rule.

## Key facts cheat sheet

| Fact | Value |
|---|---|
| Domain | bettertrains.org |
| Bluesky | @njtdelaytracker.bsky.social (RSS: profile URL + /rss) |
| Contact | bettertrains@proton.me |
| Ko-fi | ko-fi.com/bettertrains |
| Tracking since | March 31, 2026 (soft launch May 4, 2026) |
| Coverage | NJ Transit commuter rail, weekday rush hours (5:00–10:30am, 3:00–8:30pm ET), delays ≥10 min |
| Cost rate | $44/hour (USDOT value-of-travel-time method × North Jersey commuter earnings) |
| Posting | One Bluesky post daily ~8:30–9am ET, Tue–Sat, covering the previous day |
| Ops cost | Under $1/month |
| Reference day | 3/31/26: 30,918 person-hours, $742,040, 30 events, 5 lines |

## Where to read more

- `CLAUDE.md` — complete technical and operational reference (pipeline, Google Sheet schema, feature flags, roadmap including the NJ Transit GTFS-RT API expansion)
- `docs/methodology.md` — the methodology as published
- `src/post_library.json` — the voice: style brief, hard rules, red lines, and ~a dozen sample posts across scenarios (typical day, bad day, record day, good news)
- The live site and bot — read a week of posts to absorb the tone before proposing anything

*Prepared July 14, 2026, alongside the SEO/AI-crawlability engineering pass (see that branch's changes for what was implemented).*
