#!/usr/bin/env python3
"""Holland Residential 1BR apartment hunt (Seattle).

Checks One Lakefront (priority), JUXT, and True North for new 1 bed / 1 bath
units via their live SightMap feeds, prices each on a 12-month-equivalent lease,
and pushes a clean phone alert via ntfy for any unit not already alerted.
Per-property "already alerted" state lives in state.json so repeat runs only
notify on genuinely new inventory.

Runs unattended on GitHub Actions (standard library only).
Set DRY_RUN=1 to print the alert instead of pushing to ntfy.
"""

import datetime
import gzip
import json
import math
import os
import re
import urllib.request

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "onelakefront-hunt-7tq39fkd2p")
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
DRY_RUN = os.environ.get("DRY_RUN") == "1"

# Buildings to watch. Each SightMap feed shares the same shape: .data.units[]
# and .data.floor_plans[]. (JUXT and True North removed by request — keep their
# blocks handy below if you want to re-add them later.)
PROPERTIES = [
    {
        "key": "one-lakefront",
        "label": "One Lakefront",
        "emoji": "\U0001F31F",  # star
        "url": "https://sightmap.com/app/api/v1/zlpo6k14pg4/sightmaps/107943",
    },
    # {"key": "juxt", "label": "JUXT", "emoji": "\U0001F3D9",
    #  "url": "https://sightmap.com/app/api/v1/n9w6170mv71/sightmaps/107536"},
    # {"key": "true-north", "label": "True North", "emoji": "\U0001F9ED",
    #  "url": "https://sightmap.com/app/api/v1/zlpo5x08vg4/sightmaps/28201"},
]

BENCH = 2043          # current effective rent (W119); eff below this = a better deal
# Concessions vary by unit. Most units get 2 weeks free; a short list of promo
# units gets 4 weeks free. free_weeks_for() resolves the right one per unit.
DEFAULT_FREE_WEEKS = 2                 # standard concession for all other units
PROMO_FREE_WEEKS = 4                   # boosted concession for the promo units
PROMO_UNITS = {"202", "327"}           # eligible for the 4-weeks-free promo
PROMO_LINKS = {                        # photos + floorplan for each promo unit
    "202": "https://drive.google.com/drive/folders/1eFhXuV6g7JtRFCMWqmrXP2LsRgOLYC5F?usp=drive_link",
    "327": "https://drive.google.com/drive/folders/1__XFwcTFbxsxbeSOZF4r4ZdFUa_y2dRS?usp=drive_link",
}
MIN_SQFT = 506        # must beat the current 506 sqft unit
# Move-in is Aug 23 (current lease ends then) -> a lease starting Aug 23 = $0 overlap.
# SightMap caps the selectable move-in date at the available date + HOLD_DAYS, so a
# unit can only reach an Aug 23 move-in if it becomes available on/after ~Jul 24.
# Otherwise the latest move-in is earlier and you pay overlap (double rent) to Aug 23.
MOVE_IN = "2026-08-23"
HOLD_DAYS = 30


def fetch_json(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": "apartment-hunt/1.0", "Accept-Encoding": "gzip"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
    return json.loads(body)


def plan_name(fp):
    # floor_plan.name is itself a JSON string, e.g. {"name":"1 Bedroom",...}
    raw = fp.get("name", "")
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict) and obj.get("name"):
            return obj["name"]
    except (ValueError, TypeError):
        pass
    return raw or "1BR"


def floor_of(unit_number):
    # 3-digit numbering encodes floor as the first digit (327 -> 3); taller
    # buildings use 4 digits where the floor is all but the last two (1203 -> 12).
    s = str(unit_number)
    if s.startswith("W"):
        s = s[1:]
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return 0
    return int(digits[0]) if len(digits) <= 3 else int(digits[:-2])


def parse_term(display_lease_term):
    m = re.search(r"\d+", str(display_lease_term or ""))
    return int(m.group()) if m else None


def round_half_up(x):
    return int(math.floor(x + 0.5))


def free_weeks_for(unit):
    # Promo units get 4 weeks free; every other unit gets the standard 2 weeks.
    return PROMO_FREE_WEEKS if str(unit) in PROMO_UNITS else DEFAULT_FREE_WEEKS


def money(n):
    return f"${n:,.0f}"


def short_date(iso):
    try:
        d = datetime.date.fromisoformat(iso)
        return d.strftime("%b %-d")
    except (ValueError, TypeError):
        return iso or "?"


def price_for(unit):
    """Return (base_rent, term_months, is_true_12).

    Prefer a true 12-month price from the per-unit leasing endpoint. If none is
    published, assume the quoted term's price (13-mo, 14-mo, ...) as the 12-month
    figure, flagged with is_true_12=False.
    """
    base = unit.get("price") or 0
    term = parse_term(unit.get("display_lease_term"))
    url = unit.get("leasing_price_url")
    if url:
        try:
            options = fetch_json(url)["data"].get("options", [])
            p12 = next((o["price"] for o in options if o.get("lease_term") == 12), None)
            if p12 is not None:
                return p12, 12, True
        except Exception:
            pass
    return base, term, False


def overlap_cost(avail, eff_monthly):
    """Overlap rent (and days) if you lock the unit in for its latest move-in.

    Move-in is capped at the available date + HOLD_DAYS. If that latest move-in is
    on/after Aug 23 you can start the lease Aug 23 for $0 overlap; if earlier, you
    pay rent on the new place for the days between it and Aug 23 (double rent, since
    the old lease runs to Aug 23). Returns (dollars, days); rate = the unit's own
    concession-adjusted effective rent.
    """
    try:
        a = datetime.date.fromisoformat(avail)
        move = datetime.date.fromisoformat(MOVE_IN)
    except (ValueError, TypeError):
        return 0, 0
    latest = a + datetime.timedelta(days=HOLD_DAYS)
    if latest >= move:
        return 0, 0
    days = (move - latest).days
    return round(eff_monthly * days / 30), days


def qualifying_rows(data, disliked):
    floor_plans = {str(fp["id"]): fp for fp in data.get("floor_plans", [])}
    rows = []
    for u in data.get("units", []):
        fp = floor_plans.get(str(u.get("floor_plan_id")))
        if fp is None:
            continue
        if fp.get("bedroom_count") != 1 or fp.get("bathroom_count") != 1:
            continue
        if (u.get("area") or 0) <= MIN_SQFT:
            continue
        if floor_of(u.get("unit_number")) < 1:  # floor 1 and up
            continue
        avail = u.get("available_on") or ""
        if not avail or avail > MOVE_IN:
            continue
        unit = str(u.get("unit_number"))
        if unit in disliked:
            continue

        base, term, true12 = price_for(u)
        area = u.get("area") or 0
        free_weeks = free_weeks_for(unit)
        eff = round_half_up(base * (52 - free_weeks) / 52)
        ov_cost, ov_days = overlap_cost(avail, eff)
        # rank = effective $/sqft with the overlap penalty spread over 12 months
        rank = (eff + ov_cost / 12) / area if area else 9e9
        rows.append(
            {
                "unit": unit,
                "plan": plan_name(fp),
                "floor": floor_of(unit),
                "sqft": area,
                "avail": avail,
                "reservable": ov_days == 0,
                "overlap_cost": ov_cost,
                "overlap_days": ov_days,
                "rank": rank,
                "base": base,
                "term": term,
                "true12": true12,
                "free_weeks": free_weeks,
                "promo": str(unit) in PROMO_UNITS,
                "eff": eff,
                "ppsf": round(eff / area, 2) if area else 0,
            }
        )
    return rows


def unit_block(r):
    term_label = "12-mo" if r["true12"] else (f"{r['term']}-mo" if r["term"] else "term n/a")
    tilde = "" if r["true12"] else "~"
    beats = f" · ✅ beats {money(BENCH)}" if r["eff"] < BENCH else ""
    promo_tag = " ⭐ 4-WK PROMO" if r.get("promo") else ""
    lines = [
        f"\U0001F3E0 {r['unit']} — {r['plan']}{promo_tag}",
        f"\U0001F3E2 Floor {r['floor']} · \U0001F4D0 {r['sqft']} sqft · \U0001F4C5 {short_date(r['avail'])}",
        f"\U0001F4B5 {tilde}{money(r['base'])}/mo ({term_label})",
        f"\U0001F381 {r['free_weeks']} wks free → {money(r['eff'])}/mo",
        f"\U0001F4CA ${r['ppsf']:.2f}/sqft{beats}",
        (
            "\U0001F511 reserve now → move in Aug 23, $0 overlap"
            if r.get("reservable")
            else f"⏳ +${r['overlap_cost']} overlap ({r['overlap_days']}d) if locked in now"
        ),
    ]
    link = PROMO_LINKS.get(str(r["unit"]))
    if link:
        lines.append(f"\U0001F517 photos + floorplan: {link}")
    return "\n".join(lines)


def top_pick(sections):
    """Deterministic 'top pick' across the new matches. No API, no cost.

    Priority order matches the stated preference: One Lakefront first, then
    lowest effective rent per sqft, then largest size. sections is a list of
    (label, rows). Returns a 1-2 line string, or None if there are no matches.
    """
    cands = [(label, r) for label, rows in sections for r in rows]
    if not cands:
        return None
    cands.sort(key=lambda lr: lr[1]["rank"])  # most optimal (lowest all-in $/sqft) first

    def line(lead, label, r):
        beats = " · beats your $2,043" if r["eff"] < BENCH else ""
        promo = " ⭐ 4-wk promo" if r.get("promo") else ""
        return f"{lead}: {label} {r['unit']} — {r['sqft']} sqft at ${r['ppsf']:.2f}/sqft{beats}{promo}"

    out = [line("Top pick", *cands[0])]
    if len(cands) > 1:
        out.append(line("Runner-up", *cands[1]))
    return "\n".join(out)


def notify(title, body, priority=4, tags=("house",)):
    if DRY_RUN:
        print(f"[DRY_RUN p{priority}] would push:\nTITLE: {title}\n{body}")
        return
    if len(body) > 3900:  # ntfy.sh rejects oversized messages (~4 KB limit)
        body = body[:3860] + "\n… (truncated — open the app for the rest)"
    # Publish via JSON so unicode/emoji in the title survive: HTTP headers are
    # latin-1 only, so an emoji in a Title header raises UnicodeEncodeError.
    payload = json.dumps(
        {
            "topic": NTFY_TOPIC,
            "title": title,
            "message": body,
            "priority": priority,
            "tags": list(tags),
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://ntfy.sh/",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=30).read()
    except Exception as e:
        # Never fail the whole run on a push hiccup — state still advances.
        print(f"WARN: ntfy push failed: {e}")


def main():
    with open(STATE_FILE) as f:
        state = json.load(f)
    props_state = state.setdefault("properties", {})
    disliked_map = state.get("disliked", {})

    sections_new = []   # (label, emoji, new rows) for the alert body
    all_current = []    # (label, rows) for the "best right now" pick
    gone = []           # (label, [unit ids that dropped off the list])
    total_new = total_current = 0

    for prop in PROPERTIES:
        key = prop["key"]
        seen = set(props_state.get(key, {}).get("seen", []))
        disliked = set(disliked_map.get(key, []))
        try:
            data = fetch_json(prop["url"])["data"]
        except Exception as e:
            # Keep this property's prior state so a fetch blip isn't read as "all gone".
            print(f"WARN: {prop['label']} fetch failed: {e}")
            continue

        rows = qualifying_rows(data, disliked)
        current_ids = [r["unit"] for r in rows]
        new_rows = sorted(
            (r for r in rows if r["unit"] not in seen),
            key=lambda r: r["rank"],
        )
        gone_ids = [u for u in seen if u not in current_ids]

        total_current += len(rows)
        if rows:
            all_current.append((prop["label"], rows))
        if new_rows:
            sections_new.append((prop["label"], prop["emoji"], new_rows))
            total_new += len(new_rows)
        if gone_ids:
            gone.append((prop["label"], gone_ids))
        props_state[key] = {"seen": current_ids}
        print(
            f"{prop['label']}: qualifying={current_ids or 'none'}, "
            f"new={[r['unit'] for r in new_rows]}, gone={gone_ids}"
        )

    now_pt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=7)
    stamp = now_pt.strftime("%-I:%M %p PT, %b %-d")
    gone_line = ""
    if gone:
        gone_line = "📉 Left the list: " + "; ".join(
            f"{label} {', '.join(ids)}" for label, ids in gone
        )

    # Always send something so it visibly stays alive: a real alert for new units, a
    # notice when a tracked unit drops off, or a low-priority heartbeat otherwise.
    if total_new:
        summary = ", ".join(f"{label} {len(rows)}" for label, _, rows in sections_new)
        title = f"\U0001F3E0 {total_new} new 1BR — {summary}"
        # Flatten + globally rank, then show only the top few so the push stays
        # under ntfy's size limit when a big batch lands at once.
        flat = [(label, r) for label, _emoji, rows in sections_new for r in rows]
        flat.sort(key=lambda lr: lr[1]["rank"])  # most optimal first
        SHOW = 8
        blocks = [f"#{i} {label}:\n{unit_block(r)}" for i, (label, r) in enumerate(flat[:SHOW], 1)]
        body = "\n\n".join(blocks)
        if len(flat) > SHOW:
            body += f"\n\n➕ {len(flat) - SHOW} more match — open the app for the rest."
        if not any(r.get("reservable") for _, r in flat):
            body = (
                "💡 None reach an Aug 23 move-in yet — each needs the overlap shown. "
                "Best to wait for a unit that lists with availability Jul 24+.\n\n"
            ) + body
        pick = top_pick([(label, rows) for label, _emoji, rows in sections_new])
        if pick:
            body = f"\U0001F916 BEST OPTION\n──────────\n{pick}\n\n" + body
        body += "\n\n(Prices are the early/floor rate; a later move-in costs more — revenue-managed, not shown.)"
        if gone_line:
            body += "\n\n" + gone_line
        notify(title, body, priority=4, tags=["house"])
        print(f"NOTIFIED {total_new} new (showing {min(SHOW, len(flat))}).")
    elif gone:
        n = sum(len(ids) for _, ids in gone)
        title = f"📉 {n} unit(s) left the market"
        body = f"{gone_line}\n\n{total_current} still match. Nothing new.\n{stamp}"
        best = top_pick(all_current)
        if best:
            body += f"\n\nBest still available:\n{best}"
        notify(title, body, priority=3, tags=["chart_with_downwards_trend"])
        print(f"NOTIFIED gone: {gone}")
    else:
        title = f"✅ Still hunting — {total_current} match"
        body = (
            f"Checked {stamp}. {total_current} units currently match, "
            "nothing new since last check."
        )
        best = top_pick(all_current)
        if best:
            body += f"\n\nBest right now:\n{best}"
        notify(title, body, priority=2, tags=["eyes"])
        print(f"HEARTBEAT: {total_current} matching, nothing new.")

    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


if __name__ == "__main__":
    main()
