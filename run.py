"""One command, start to finish: find the event, pull every posted round for every class,
crunch the numbers, render the pages.

Usage:
  python3 -I run.py                      # uses config.json (the event schedule picks this week's race)
  python3 -I run.py 2026 midwest Q1      # one-off: year, event name or id, round to open on

Writes site/index.html (event home) and site/<class>/index.html, each self-contained.
Prints CHANGED or UNCHANGED so the scheduler knows whether to publish.
"""
import hashlib
import shutil
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build  # noqa: E402
import scrape  # noqa: E402

DATA_ROOT = os.environ.get("DI_DATA_DIR", "data")
SERIES_URL = "https://www.nhra.com/results/{year}/nhra-mission-foods-drag-racing-series"
CLASS_NAMES = {"pro-mod": "Pro Mod", "top-fuel": "Top Fuel", "funny-car": "Funny Car",
               "pro-stock": "Pro Stock", "pro-stock-motorcycle": "Pro Stock Motorcycle"}


def get(url, tries=4):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (DI Incrementals)"})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(2 * (attempt + 1))


def find_event(year, query):
    """Event id from a name fragment, e.g. 'fallnationals' or 'midwest'."""
    if str(query).isdigit():
        return str(query)
    base = SERIES_URL.format(year=year)
    ids = re.findall(r"/results/%s/nhra-mission-foods-drag-racing-series/(\d+)" % year, get(base))
    if not ids:
        sys.exit("No events found on the NHRA results index.")
    html = get(f"{base}/{ids[0]}/detailed-results")
    opts = re.findall(r'value="/results/%s/nhra-mission-foods-drag-racing-series/(\d+)">\s*([^<]+)' % year, html)
    q = query.lower().replace("-", " ")
    hits = [(i, n.strip()) for i, n in opts if q in n.lower()]
    if len(hits) != 1:
        sys.exit(f"Event '{query}' matched {len(hits)}: {hits}")
    return hits[0][0]


def rounds_for(year, event, cls):
    html = get(f"{SERIES_URL.format(year=year)}/{event}/detailed-results/{cls}")
    found = re.findall(r"/%s/detailed-results/%s/([qe]\d)\"" % (event, cls), html)
    return sorted(set(found), key=lambda r: (r[0] != "q", int(r[1:])))


SKELETON = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
            '<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}'
            'body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>'
            '</head><body>{body}</body></html>')


def write_page(path, body, preview_path=None):
    """Full document for the live site; the bare body for an Artifact preview of the home page."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write(SKELETON.replace("{body}", body))
    if preview_path:
        os.makedirs(os.path.dirname(preview_path), exist_ok=True)
        open(preview_path, "w").write(body)


CSV_COLS = [("Round", "round"), ("Pair", "pair"), ("Lane", "lane"), ("Driver", "driver"), ("Car", "car"),
            ("Result", "result"), ("RT", "rt"), ("60'", "ft60"), ("330'", "ft330"), ("660'", "ft660"),
            ("660 MPH", "mph660"), ("1000'", "ft1000"), ("ET", "et"), ("MPH", "mph")]


def write_csv(path, b, class_name):
    """Every run of the weekend for one class, with DI's splits, ready for Excel or Numbers."""
    import csv
    pts, segs, sl = b["points"], b["segs"], b["seg_label"]
    head = ["Event", "Class"] + [h for h, _ in CSV_COLS] + [f"Split {sl[s]}" for s in segs] + \
        (["Back half 660-1320"] if b["finish"] == 1320 else []) + ["Back-half MPH gain", "Clean run",
                                                                   "Went away", "ET rank", "60' rank", "330' rank", "660' rank"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(head)
        for rd in b["rounds"]:
            for r in sorted(rd["runs"], key=lambda r: (r["pair"], r["lane"])):
                row = dict(r, round=rd["round"], lane="Left" if r["lane"] == "L" else "Right",
                           result=("W" if r["win"] else "L") if rd["round"].startswith("E") else "")
                vals = [b["event_name"], class_name] + [row.get(k, "") if row.get(k) is not None else "" for _, k in CSV_COLS]
                vals += [r.get(s) if r.get(s) is not None else "" for s in segs]
                if b["finish"] == 1320:
                    vals.append(r.get("back") if r.get("back") is not None else "")
                vals += [r.get("gain") if r.get("gain") is not None else "", "yes" if r["clean"] else "no",
                         sl.get(r["off_at"], "") if r.get("off_at") else "",
                         r["rank"].get("et", ""), r["rank"].get("ft60", ""), r["rank"].get("ft330", ""), r["rank"].get("ft660", "")]
                w.writerow(vals)


def pull(year, event, cls):
    data_dir = os.path.join(HERE, DATA_ROOT, f"{year}-{event}", cls)
    os.makedirs(data_dir, exist_ok=True)
    pulled = []
    try:
        rnds = rounds_for(year, event, cls)
    except Exception as e:  # NHRA hiccup: fall back to what we already have on disk
        print(f"warn: round list for {cls} failed ({e}); using saved rounds", file=sys.stderr)
        rnds = sorted(f[:-5] for f in os.listdir(data_dir) if f.endswith(".json"))
    for rnd in rnds:
        saved = os.path.join(data_dir, f"{rnd}.json")
        try:
            url, html = scrape.fetch(year, event, cls, rnd)
            name, pairs = scrape.parse(html)
        except Exception as e:
            print(f"warn: {cls} {rnd} fetch failed ({e}); keeping saved copy", file=sys.stderr)
            if os.path.exists(saved):
                pulled.append(rnd.upper())
            continue
        if not pairs:
            continue
        json.dump({"source_url": url, "year": int(year), "event_id": event, "class": cls,
                   "round": rnd.upper(), "event_name": name, "pairs": pairs},
                  open(os.path.join(data_dir, f"{rnd}.json"), "w"), indent=1)
        pulled.append(rnd.upper())
    return data_dir, pulled


def main():
    cfg = json.load(open(os.path.join(HERE, "config.json")))
    if len(sys.argv) > 2:
        cfg["year"], cfg["event"] = int(sys.argv[1]), sys.argv[2]
        cfg["open_round"] = sys.argv[3].upper() if len(sys.argv) > 3 else "LATEST"
    if "event" not in cfg:
        today = datetime.now(ZoneInfo("America/Chicago")).date().isoformat()
        current = [e for e in cfg["schedule"] if e["starts"] <= today]
        cfg["event"] = (current[-1] if current else cfg["schedule"][0])["event"]
    year, event = cfg["year"], find_event(cfg["year"], cfg["event"])
    site = os.path.join(HERE, cfg.get("out_dir", "site"))
    os.makedirs(site, exist_ok=True)
    template = open(os.path.join(HERE, "template.html")).read()
    template = template.replace("__LOGO__", open(os.path.join(HERE, "assets", "di-logo.svg")).read())
    sponsor = cfg.get("sponsor", {})

    bundles = {}
    for cls in cfg["classes"]:
        data_dir, pulled = pull(year, event, cls)
        if not pulled:
            continue
        b = build.build(data_dir)
        want = cfg.get("open_round", "LATEST")
        b["default_round"] = want if want in pulled else pulled[-1]
        bundles[cls] = b

    nav = [{"slug": c, "name": CLASS_NAMES.get(c, c), "live": c in bundles} for c in cfg["classes"]]
    event_name = next((b["event_name"] for b in bundles.values()), None)
    slug = f"{year}-" + re.sub(r"[^a-z0-9]+", "-", str(cfg["event"]).lower()).strip("-")

    # past-races registry lives on the site so every race stays available
    reg_path = os.path.join(site, "events", "events.json")
    os.makedirs(os.path.dirname(reg_path), exist_ok=True)
    registry = json.load(open(reg_path)) if os.path.exists(reg_path) else []
    if bundles:
        registry = [e for e in registry if e["slug"] != slug] + [{"slug": slug, "name": event_name, "year": year}]
        json.dump(registry, open(reg_path, "w"), indent=1)

    def write_event(dest, root):
        """One complete, self-contained copy of this race: home page, class pages, spreadsheets, logo."""
        shutil.copytree(os.path.join(HERE, "assets"), os.path.join(dest, "assets"), dirs_exist_ok=True)
        common = {"nav": nav, "sponsor": sponsor, "slug": slug,
                  "past": [e for e in registry if e["slug"] != slug][::-1]}
        if not bundles:
            write_page(os.path.join(dest, "index.html"), template.replace("__DATA__", json.dumps(dict(
                common, waiting=True, year=year, event_query=cfg["event"], base="", root=root))))
            return
        for cls, b in bundles.items():
            csv_name = f"DI-Incrementals-{slug}-{cls}.csv"
            write_csv(os.path.join(dest, cls, csv_name), b, CLASS_NAMES.get(cls, cls))
            page = dict(b, **common, class_name=CLASS_NAMES.get(cls, cls), base="../", root="../" + root,
                        csv=csv_name, save=f"DI-Incrementals-{slug}-{cls}.html", self_path="index.html")
            write_page(os.path.join(dest, cls, "index.html"),
                       template.replace("__DATA__", json.dumps(page, separators=(",", ":")).replace("</", "<\\/")))
        first = next(c for c in cfg["classes"] if c in bundles)
        b = bundles[first]
        page = dict(b, **common, class_name=CLASS_NAMES.get(first, first), base="", root=root,
                    csv=f"{first}/DI-Incrementals-{slug}-{first}.csv", save=f"DI-Incrementals-{slug}-{first}.html",
                    self_path=f"{first}/index.html")
        write_page(os.path.join(dest, "index.html"),
                   template.replace("__DATA__", json.dumps(page, separators=(",", ":")).replace("</", "<\\/")),
                   os.path.join(HERE, "preview", "index.html") if dest == site else None)

    write_event(site, "")
    if bundles:
        write_event(os.path.join(site, "events", slug), "../../")

    # new results OR a change to the page/analysis code both trigger a publish
    code = "".join(open(os.path.join(HERE, f)).read() for f in ("template.html", "build.py", "config.json"))
    code += "".join(sorted(os.listdir(os.path.join(HERE, "assets"))))
    fingerprint = hashlib.sha256((code + json.dumps(
        {c: b["rounds"] for c, b in bundles.items()}, sort_keys=True)).encode()).hexdigest()
    fp_file = os.path.join(site, ".fingerprint")
    prev = open(fp_file).read() if os.path.exists(fp_file) else ""
    open(fp_file, "w").write(fingerprint)
    summary = "; ".join(f"{CLASS_NAMES.get(c, c)} {','.join(r['round'] for r in b['rounds'])}" for c, b in bundles.items())
    print(f"{event_name or 'waiting on results'} | {summary or 'no rounds posted yet'}")
    print("CHANGED" if fingerprint != prev else "UNCHANGED")


if __name__ == "__main__":
    main()
