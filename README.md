# apartment-hunt

Automated check of **One Lakefront** (Seattle) for new 1 bed / 1 bath units, with a
phone alert via [ntfy](https://ntfy.sh) when one matches. (JUXT and True North were
removed by request; their feed entries are kept commented in `hunt.py` for easy
re-adding.)

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
floor 1 or higher, available on or before the move-in date (`MOVE_IN`, currently
2026-08-23), and not in that building's skip list. The benchmark effective rent is
$2,043/mo (current unit, One Lakefront W119); a match whose concession-adjusted
effective rent is below that is flagged "beats $2,043".

### Concessions (free rent)

Concessions vary by unit. The two **promo units get 4 weeks free** and are flagged
⭐ in the alert, with a photos + floorplan link:

- Unit **202** — Urban 1BR/1BA (~$2,286)
- Unit **327** — 1BR/1BA + Den (~$3,024)

**Every other unit gets 2 weeks free.** The lists live in `PROMO_UNITS` /
`PROMO_LINKS` (4-week promo) and `DEFAULT_FREE_WEEKS` (2 weeks) in `hunt.py`; the
effective rent, `$/sqft`, ranking, and overlap cost are all computed from each
unit's own concession.

### Move-in timing and overlap cost

Move-in is Aug 23 (current lease ends then), so a lease starting Aug 23 means $0
overlap. SightMap caps the selectable move-in at the available date + `HOLD_DAYS`
(30), so a unit only reaches an Aug 23 move-in if it's available on/after ~Jul 24
(tagged **reserve now → $0 overlap**). Otherwise its latest move-in is earlier and
you pay double rent until Aug 23; each such unit shows its **overlap cost** (the
unit's own concession-adjusted effective rate × the gap days). Matches are ordered most-optimal first by an
all-in `rank` (effective $/sqft with the overlap penalty spread over 12 months).

Note: One Lakefront uses revenue-management pricing, so the price shown is the early/
floor rate; a later move-in costs more, and that premium is not exposed by the public
API, so it is not included.

## Pricing

Each match is priced on a 12-month lease when the leasing endpoint publishes one.
When it does not, the quoted term's price (13-mo, 14-mo, ...) is used as the
assumed 12-month figure and shown with a leading `~`.

## Top pick

Each alert is prefixed with a "top pick" computed from the matches (no API, no
cost): it favors One Lakefront, then lowest effective rent per sqft, then largest
size, with a runner-up.

## Alerts

Pushes to ntfy topic `onelakefront-hunt-7tq39fkd2p`. Subscribe to that topic in
the ntfy app to receive them. Override with the `NTFY_TOPIC` environment variable.

Every run sends something so it's visibly alive: a high-priority alert when new units
appear, a medium-priority notice when a tracked unit drops off the list, or a
low-priority heartbeat ("Still hunting — N match, nothing new") otherwise.

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
