# apartment-hunt

Automated hourly check of One Lakefront (Seattle) for new 1 bed / 1 bath units,
with a phone alert via [ntfy](https://ntfy.sh) when a unit matches.

## Files

- `hunt.py` — the worker. Fetches live SightMap data, filters for qualifying
  units, prices each on a 12-month lease, and pushes an ntfy alert for units
  not already alerted. Standard library only.
- `state.json` — persistence. `seen` is the set of units already alerted;
  `disliked` is the skip list.
- `.github/workflows/hunt.yml` — runs `hunt.py` hourly (and on demand) and
  commits the updated state back to the repo.

## Match criteria

A unit alerts only if all are true: 1 bed / 1 bath, area greater than 506 sqft,
floor 2 or higher, available on or after 2026-07-15, and not in the skip list.
The benchmark effective rent is $2,043/mo (current unit W119); a match whose
6-weeks-free effective rent is below that is flagged "BEATS CURRENT".

## Alerts

Pushes to ntfy topic `onelakefront-hunt-7tq39fkd2p`. Subscribe to that topic in
the ntfy app to receive them. Override with the `NTFY_TOPIC` environment variable.

## Skip list

Add a unit number to the `disliked` array in `state.json` to stop alerts for it.

## Running

- Scheduled: hourly via GitHub Actions.
- On demand: Actions tab, "one-lakefront-1br-hunt", "Run workflow".
- Locally without sending a push: `DRY_RUN=1 python3 hunt.py`.
