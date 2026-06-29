#!/usr/bin/env python3
"""One Lakefront 1BR apartment hunt.

Fetches live SightMap floor-plan data, filters for qualifying 1 bed / 1 bath
units, prices each on a 12-month lease, and pushes a phone alert via ntfy for
any unit not already alerted. The set of already-alerted units is persisted in
state.json so repeat runs only notify on genuinely new inventory.

Runs unattended on GitHub Actions (stdlib only, no third-party packages).
Set DRY_RUN=1 to print what would be sent instead of pushing to ntfy.
"""

import gzip
import json
import math
import os
import urllib.request

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "onelakefront-hunt-7tq39fkd2p")
DATA_URL = "https://sightmap.com/app/api/v1/zlpo6k14pg4/sightmaps/107943"
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")

BENCH = 2043          # current effective rent (W119); eff6 below this = a better deal
MIN_SQFT = 506        # must be bigger than the current 506 sqft unit
MIN_DATE = "2026-07-15"
DRY_RUN = os.environ.get("DRY_RUN") == "1"


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
    # Floor = first digit of the numeric part, after dropping a leading "W".
    s = str(unit_number)
    if s.startswith("W"):
        s = s[1:]
    for ch in s:
        if ch.isdigit():
            return int(ch)
    return 0


def round_half_up(x):
    return int(math.floor(x + 0.5))


def notify(title, body):
    if DRY_RUN:
        print(f"[DRY_RUN] would push: {title}\n{body}")
        return
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=body.encode("utf-8"),
        headers={"Title": title, "Priority": "high", "Tags": "house"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=30).read()


def main():
    with open(STATE_FILE) as f:
        state = json.load(f)
    seen = set(state.get("seen", []))
    disliked = set(state.get("disliked", []))

    data = fetch_json(DATA_URL)["data"]
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

        # True 12-month base rent from the per-unit leasing endpoint.
        base = u.get("price") or 0
        term = u.get("display_lease_term") or ""
        note = ""
        url = u.get("leasing_price_url")
        if url:
            try:
                options = fetch_json(url)["data"].get("options", [])
                p12 = next(
                    (o["price"] for o in options if o.get("lease_term") == 12), None
                )
                if p12 is not None:
                    base = p12
                else:
                    note = f" ({term} price; 12-mo not yet published)"
            except Exception:
                note = f" ({term} price; leasing endpoint unreachable)"

        area = u.get("area") or 0
        eff6 = round_half_up(base * (52 - 6) / 52)
        eff8 = round_half_up(base * (52 - 8) / 52)
        ppsf = round(eff8 / area, 2) if area else 0
        beats = " | BEATS CURRENT" if eff6 < BENCH else ""
        line = (
            f"{unit} | {plan_name(fp)} | fl{floor_of(unit)} | {area}sqft | "
            f"{u.get('available_on')} | ${base} 12-mo{note} | "
            f"eff@6wk ${eff6} | eff@8wk ${eff8} | ${ppsf}/sqft{beats}"
        )
        rows.append({"unit": unit, "eff8": eff8, "sqft": area, "line": line})

    current_ids = [r["unit"] for r in rows]
    new_rows = sorted(
        (r for r in rows if r["unit"] not in seen),
        key=lambda r: (r["eff8"], -r["sqft"]),
    )

    if new_rows:
        title = f"One Lakefront: {len(new_rows)} new 1BR match(es)"
        body = "\n".join(r["line"] for r in new_rows)
        notify(title, body)
        print(f"NOTIFIED {len(new_rows)} new unit(s):\n{body}")
    else:
        print(f"No new matches. Currently qualifying: {current_ids or 'none'}")

    state["seen"] = current_ids
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


if __name__ == "__main__":
    main()
