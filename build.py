"""Turn scraped round JSON files into the analysis the DI sheet renders.

Usage:
  python3 -I build.py <data_dir> <out.json>
Reads every <round>.json in data_dir (q1..q4, e1..e4) and writes one JSON bundle.
Handles quarter-mile classes (60/330/660/1000/1320) and 1,000-foot nitro (60/330/660/1000).
"""
import json
import os
import re
import statistics
import sys
from datetime import datetime, timezone

ROUND_ORDER = ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "E1", "E2", "E3", "E4", "E5"]
CLEAN_WINDOW = 0.30   # a run within this much of the round's low ET is a full pull
OFF_FACTOR = 1.06     # a segment this much slower than the clean median = where it went away
WEATHER_RE = re.compile(r"Weather conditions:\s*(.*?degrees\.)", re.S)
DIST = {"ft60": 60, "ft330": 330, "ft660": 660, "ft1000": 1000}


def r4(x):
    return None if x is None else round(x, 4)


def ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def fmt_gap(g):
    return f"+{g:.3f}".replace("+0.", "+.")


def parse_weather(note):
    if not note:
        return None, note
    m = WEATHER_RE.search(note)
    if not m:
        return None, note
    text = re.sub(r"\s+", " ", m.group(1))
    w = {}
    for key, pat in [("air", r"air temperature (\d+)"), ("humidity", r"relative humidity (\d+)"),
                     ("baro", r"barometer ([\d.]+)"), ("altitude", r"adjusted\s+altitude ([\d,]+)"),
                     ("track", r"track temperature (\d+)")]:
        mm = re.search(pat, text)
        if mm:
            w[key] = mm.group(1).replace(",", "")
    return w, None


def layout(raws):
    """Timing points and segments this class actually reports."""
    has1000 = any(r.get("ft1000") is not None for raw in raws for p in raw["pairs"] for r in p["runs"])
    points = ["ft60", "ft330", "ft660"] + (["ft1000"] if has1000 else []) + ["et"]
    finish = 1320 if has1000 else 1000
    feet = [DIST.get(p, finish) for p in points]
    labels = {p: (f"{DIST[p]}'" if p in DIST else "ET") for p in points}
    segs, seg_label, start = [], {}, 0
    for i, p in enumerate(points):
        key = f"s{i + 1}"
        segs.append(key)
        seg_label[key] = f"{start}-{feet[i]}"
        start = feet[i]
    return points, segs, labels, seg_label, finish


def build_round(raw, points, segs, seg_label, inc_label):
    runs, weather = [], None
    for i, pair in enumerate(raw["pairs"]):
        w, _ = parse_weather(pair.get("note"))
        if w:
            weather = w
        live = [r for r in pair["runs"] if r["driver"] and r["driver"] != "--"]
        for r in live:
            opp = [x for x in live if x is not r]
            row = {"driver": r["driver"], "car": r.get("car"), "lane": (r.get("lane") or "?")[:1].upper(),
                   "pair": i + 1, "win": r.get("win", False), "opp": opp[0]["driver"] if opp else None}
            for k in ["rt", "mph660", "mph"] + points:
                row[k] = r.get(k)
            runs.append(row)

    for r in runs:
        r["red"] = r["rt"] is not None and r["rt"] < 0
        prev = 0.0
        for key, p in zip(segs, points):
            v = r[p]
            r[key] = r4(v - prev) if (v is not None and prev is not None) else None
            prev = v
        r["back"] = r4(r["et"] - r["ft660"]) if r["et"] and r["ft660"] else None

    ets = [r["et"] for r in runs if r["et"]]
    low = min(ets) if ets else None
    full = [r for r in runs if r["et"] and low and r["et"] <= low + CLEAN_WINDOW]
    med = {s: statistics.median([r[s] for r in full if r[s] is not None]) for s in segs} if full else {}

    for r in runs:
        r["off_at"] = None
        if r["et"] is None or not med:
            r["off_at"] = segs[0] if r["et"] is None else None
        else:
            for s in segs:
                if r[s] is None or r[s] > med[s] * OFF_FACTOR:
                    r["off_at"] = s
                    break
        r["clean"] = r["off_at"] is None
        if r["clean"]:
            ok = {"rt", "mph660", "mph", "back"} | set(points) | set(segs)
        else:
            idx = segs.index(r["off_at"])
            ok = {"rt"} | set(segs[:idx]) | set(points[:idx])
            if "ft660" in points[:idx]:
                ok.add("mph660")
        r["valid"] = sorted(ok)
        r["rank"], r["gap"] = {}, {}

    best, order = {}, {}
    for key in ["rt", "mph660", "mph", "back"] + points + segs:
        always = key in ("et", "mph")
        pool = [r for r in runs if (always or key in r["valid"]) and r[key] is not None
                and not (key == "rt" and r["red"])]
        if not pool:
            continue
        rev = key in ("mph660", "mph")
        pool.sort(key=lambda r: r[key], reverse=rev)
        best[key] = pool[0][key]
        order[key] = [{"driver": p["driver"], "v": p[key], "gap": r4(abs(p[key] - pool[0][key]))} for p in pool]
        for n, p in enumerate(pool):
            p["rank"][key] = n + 1
            p["gap"][key] = r4(abs(p[key] - pool[0][key]))

    # the read: one automated line per car
    for r in runs:
        if r["et"] is None:
            r["read"] = "No time recorded."
            continue
        if not r["clean"]:
            idx = segs.index(r["off_at"])
            last = points[idx - 1] if idx > 0 else None
            r["read"] = f"Went away {seg_label[r['off_at']]}." + (
                f" Was {ordinal(r['rank'][last])} to the {inc_label[last]} before that."
                if last and r["rank"].get(last) else "")
            continue
        g = [(s, r["gap"].get(s)) for s in segs if r["gap"].get(s) is not None]
        worst = max(g, key=lambda x: x[1]) if g else None
        strong = [s for s, v in g if r["rank"].get(s) == 1]
        bits = []
        if r["gap"].get("et") == 0:
            bits.append("Low ET of the round.")
        if strong:
            bits.append("Quickest in the field " + ", ".join(seg_label[s] for s in strong) + ".")
        if worst and worst[1] > 0.0005:
            bits.append(f"Gave up the most {seg_label[worst[0]]}, {fmt_gap(worst[1])} off the quickest.")
        r["read"] = " ".join(bits) or "Even all the way down."

    runs.sort(key=lambda r: (r["et"] is None, r["et"] or 99))
    return {"round": raw["round"], "source_url": raw["source_url"], "weather": weather, "low_et": low,
            "best": best, "order": order, "clean_count": sum(r["clean"] for r in runs), "runs": runs}


def qualifying_after(rounds):
    """Best ET to date after each Q round (ET, then MPH)."""
    best, out = {}, {}
    for rd in rounds:
        if not rd["round"].startswith("Q"):
            continue
        for r in rd["runs"]:
            if r["et"] is None:
                continue
            cur = best.get(r["driver"])
            if cur is None or (r["et"], -(r["mph"] or 0)) < (cur["et"], -(cur["mph"] or 0)):
                best[r["driver"]] = {"et": r["et"], "mph": r["mph"], "round": rd["round"]}
        ordered = sorted(best.items(), key=lambda kv: (kv[1]["et"], -(kv[1]["mph"] or 0)))
        out[rd["round"]] = [{"driver": d, **v} for d, v in ordered]
    return out


def build(src):
    raws = [json.load(open(os.path.join(src, f))) for f in os.listdir(src) if f.endswith(".json")]
    raws = [r for r in raws if r["pairs"]]
    raws.sort(key=lambda r: ROUND_ORDER.index(r["round"]))
    if not raws:
        return None
    points, segs, inc_label, seg_label, finish = layout(raws)
    rounds = [build_round(r, points, segs, seg_label, inc_label) for r in raws]
    qual = qualifying_after(rounds)
    for rd in rounds:
        if rd["round"] in qual:
            pos = {q["driver"]: i + 1 for i, q in enumerate(qual[rd["round"]])}
            for r in rd["runs"]:
                r["qpos"] = pos.get(r["driver"])
    history = {}
    for rd in rounds:
        for r in rd["runs"]:
            history.setdefault(r["driver"], []).append(
                {"round": rd["round"], **{k: r[k] for k in points + ["mph", "clean", "win", "opp", "lane"]}})
    first = raws[0]
    return {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event_name": first["event_name"], "event_id": first["event_id"], "year": first["year"],
        "class": first["class"], "finish": finish, "points": points, "segs": segs,
        "inc_label": inc_label, "seg_label": seg_label,
        "rounds": rounds, "qualifying": qual, "history": history,
    }


if __name__ == "__main__":
    b = build(sys.argv[1])
    os.makedirs(os.path.dirname(sys.argv[2]) or ".", exist_ok=True)
    json.dump(b, open(sys.argv[2], "w"), separators=(",", ":"))
    print(f"{len(b['rounds']) if b else 0} rounds -> {sys.argv[2]}", file=sys.stderr)
