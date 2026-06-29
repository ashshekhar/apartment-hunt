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

# Properties in priority order (One Lakefront first). Each SightMap feed shares
# the same shape: .data.units[] and .data.floor_plans[].
PROPERTIES = [
    {
        "key": "one-lakefront",
        "label": "One Lakefront",
        "emoji": "\U0001F31F",  # star
        "url": "https://sightmap.com/app/api/v1/zlpo6k14pg4/sightmaps/107943",
    },
    {
        "key": "juxt",
        "label": "JUXT",
        "emoji": "\U0001F3D9",  # cityscape
        "url": "https://sightmap.com/app/api/v1/n9w6170mv71/sightmaps/107536",
    },
    {
        "key": "true-north",
        "label": "True North",
        "emoji": "\U0001F9ED",  # compass
        "url": "https://sightmap.com/app/api/v1/zlpo5x08vg4/sightmaps/28201",
    },
]

BENCH = 2043          # current effective rent (W119); eff6 below this = a better deal
MIN_SQFT = 506        # must beat the current 506 sqft unit
MIN_DATE = "2026-07-15"


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
        if floor_of(u.get("unit_number")) < 2:
            continue
        if (u.get("available_on") or "") < MIN_DATE:
            continue
        unit = str(u.get("unit_number"))
        if unit in disliked:
            continue

        base, term, true12 = price_for(u)
        area = u.get("area") or 0
        eff6 = round_half_up(base * (52 - 6) / 52)
        eff8 = round_half_up(base * (52 - 8) / 52)
        rows.append(
            {
                "unit": unit,
                "plan": plan_name(fp),
                "floor": floor_of(unit),
                "sqft": area,
                "avail": u.get("available_on"),
                "base": base,
                "term": term,
                "true12": true12,
                "eff6": eff6,
                "eff8": eff8,
                "ppsf": round(eff8 / area, 2) if area else 0,
            }
        )
    return rows


def unit_block(r):
    term_label = "12-mo" if r["true12"] else (f"{r['term']}-mo" if r["term"] else "term n/a")
    tilde = "" if r["true12"] else "~"
    beats = f" · ✅ beats {money(BENCH)}" if r["eff6"] < BENCH else ""
    return "\n".join(
        [
            f"\U0001F3E0 {r['unit']} — {r['plan']}",
            f"\U0001F3E2 Floor {r['floor']} · \U0001F4D0 {r['sqft']} sqft · \U0001F4C5 {short_date(r['avail'])}",
            f"\U0001F4B5 {tilde}{money(r['base'])}/mo ({term_label})",
            f"\U0001F381 6 wks free → {money(r['eff6'])}/mo",
            f"\U0001F381 8 wks free → {money(r['eff8'])}/mo",
            f"\U0001F4CA ${r['ppsf']:.2f}/sqft{beats}",
        ]
    )


def top_pick(sections):
    """Deterministic 'top pick' across the new matches. No API, no cost.

    Priority order matches the stated preference: One Lakefront first, then
    lowest effective rent per sqft, then largest size. sections is a list of
    (label, rows). Returns a 1-2 line string, or None if there are no matches.
    """
    cands = [(label, r) for label, rows in sections for r in rows]
    if not cands:
        return None
    cands.sort(key=lambda lr: (lr[0] != "One Lakefront", lr[1]["ppsf"], -lr[1]["sqft"]))

    def line(lead, label, r):
        beats = " · beats your $2,043" if r["eff6"] < BENCH else ""
        return f"{lead}: {label} {r['unit']} — {r['sqft']} sqft at ${r['ppsf']:.2f}/sqft{beats}"

    out = [line("Top pick", *cands[0])]
    if len(cands) > 1:
        out.append(line("Runner-up", *cands[1]))
    return "\n".join(out)


def notify(title, body):
    if DRY_RUN:
        print(f"[DRY_RUN] would push:\nTITLE: {title}\n{body}")
        return
    # Publish via JSON so unicode/emoji in the title survive: HTTP headers are
    # latin-1 only, so an emoji in a Title header raises UnicodeEncodeError.
    payload = json.dumps(
        {
            "topic": NTFY_TOPIC,
            "title": title,
            "message": body,
            "priority": 4,
            "tags": ["house"],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://ntfy.sh/",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=30).read()


def main():
    with open(STATE_FILE) as f:
        state = json.load(f)
    props_state = state.setdefault("properties", {})
    disliked_map = state.get("disliked", {})

    sections = []   # (label, emoji, sorted new rows) in priority order
    total_new = 0

    for prop in PROPERTIES:
        key = prop["key"]
        seen = set(props_state.get(key, {}).get("seen", []))
        disliked = set(disliked_map.get(key, []))
        try:
            data = fetch_json(prop["url"])["data"]
        except Exception as e:
            print(f"WARN: {prop['label']} fetch failed: {e}")
            continue

        rows = qualifying_rows(data, disliked)
        current_ids = [r["unit"] for r in rows]
        new_rows = sorted(
            (r for r in rows if r["unit"] not in seen),
            key=lambda r: (r["eff8"], -r["sqft"]),
        )
        if new_rows:
            sections.append((prop["label"], prop["emoji"], new_rows))
            total_new += len(new_rows)
        props_state[key] = {"seen": current_ids}
        print(f"{prop['label']}: qualifying={current_ids or 'none'}, new={[r['unit'] for r in new_rows]}")

    if total_new:
        summary = ", ".join(f"{label} {len(rows)}" for label, _, rows in sections)
        title = f"\U0001F3E0 {total_new} new 1BR — {summary}"
        blocks = []
        for label, emoji, rows in sections:
            header = f"{emoji} {label.upper()} · {len(rows)} new\n──────────"
            blocks.append(header + "\n" + "\n\n".join(unit_block(r) for r in rows))
        body = "\n\n".join(blocks)
        pick = top_pick([(label, rows) for label, _emoji, rows in sections])
        if pick:
            body = f"\U0001F916 PICK\n──────────\n{pick}\n\n" + body
        notify(title, body)
        print(f"NOTIFIED {total_new} new unit(s).")
    else:
        print("No new matches across any property.")

    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


if __name__ == "__main__":
    main()
