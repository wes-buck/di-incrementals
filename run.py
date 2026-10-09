"""One command, start to finish: find the event, pull every posted round for every class,
crunch the numbers, render the pages.

Usage:
  python3 -I run.py                      # uses config.json (the event schedule picks this week's race)
  python3 -I run.py 2026 midwest Q1      # one-off: year, event name or id, round to open on

Writes site/index.html (event home) and site/<class>/index.html, each self-contained.
Prints CHANGED or UNCHANGED so the scheduler knows whether to publish.
"""
import hashlib
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
    for cls, b in bundles.items():
        b["class_name"] = CLASS_NAMES.get(cls, cls)
        b["nav"] = nav
        b["base"] = "../"
        payload = json.dumps(b, separators=(",", ":")).replace("</", "<\\/")
        write_page(os.path.join(site, cls, "index.html"), template.replace("__DATA__", payload))

    # event home: the first class with data, rendered at the root, so the QR code goes to one place
    if bundles:
        first = next(c for c in cfg["classes"] if c in bundles)
        b = dict(bundles[first], base="")
        payload = json.dumps(b, separators=(",", ":")).replace("</", "<\\/")
        write_page(os.path.join(site, "index.html"), template.replace("__DATA__", payload),
                   os.path.join(HERE, "preview", "index.html"))
    else:
        write_page(os.path.join(site, "index.html"),
                   template.replace("__DATA__", json.dumps({"waiting": True, "year": year, "event_query": cfg["event"],
                                                            "nav": nav, "base": ""})))

    # new results OR a change to the page/analysis code both trigger a publish
    code = "".join(open(os.path.join(HERE, f)).read() for f in ("template.html", "build.py", "config.json"))
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
