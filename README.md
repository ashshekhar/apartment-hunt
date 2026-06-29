# apartment-hunt

Automated check of three Seattle buildings for new 1 bed / 1 bath units, with a
phone alert via [ntfy](https://ntfy.sh) when one matches. Properties, in priority
order: **One Lakefront**, **JUXT**, **True North** (all on SightMap feeds).

## Files

- `hunt.py` — the worker. Fetches each property's live SightMap data, filters for
  qualifying units, prices each on a 12-month-equivalent lease, and pushes one
  grouped ntfy alert for units not already alerted. Standard library only.
- `state.json` — persistence. Under `properties.<key>.seen` is the set of units
  already alerted per building; `disliked.<key>` is each building's skip list.
- `.github/workflows/hunt.yml` — runs `hunt.py` on a schedule (and on demand) and
  commits the updated state back to the repo.

## Match criteria

A unit alerts only if all are true: 1 bed / 1 bath, area greater than 506 sqft,
floor 2 or higher, available on or after 2026-07-15, and not in that building's
skip list. The benchmark effective rent is $2,043/mo (current unit, One Lakefront
W119); a match whose 6-weeks-free effective rent is below that is flagged
"beats $2,043".

## Pricing

Each match is priced on a 12-month lease when the leasing endpoint publishes one.
When it does not, the quoted term's price (13-mo, 14-mo, ...) is used as the
assumed 12-month figure and shown with a leading `~`.

## Alerts

Pushes to ntfy topic `onelakefront-hunt-7tq39fkd2p`. Subscribe to that topic in
the ntfy app to receive them. Override with the `NTFY_TOPIC` environment variable.

## Skip list

Add a unit number to the relevant building's array under `disliked` in
`state.json` (for example `disliked.one-lakefront`) to stop alerts for it.

## Schedule

Every 2 hours from 9AM to 9PM Pacific, plus one overnight run (~3AM). Cron is in
UTC and computed for PDT (UTC-7); during PST (winter) the local times shift one
hour earlier.

## Running

- Scheduled: via GitHub Actions (see above).
- On demand: Actions tab, "one-lakefront-1br-hunt", "Run workflow".
- Locally without sending a push: `DRY_RUN=1 python3 hunt.py`.
