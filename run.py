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
            date = scrape.event_date(html)
        except Exception as e:
            print(f"warn: {cls} {rnd} fetch failed ({e}); keeping saved copy", file=sys.stderr)
            if os.path.exists(saved):
                pulled.append(rnd.upper())
            continue
        if not pairs:
            continue
        json.dump({"source_url": url, "year": int(year), "event_id": event, "class": cls,
                   "round": rnd.upper(), "event_name": name, "event_date": date, "pairs": pairs},
                  open(os.path.join(data_dir, f"{rnd}.json"), "w"), indent=1)
        pulled.append(rnd.upper())
    return data_dir, pulled


LABEL_FIX = {"NATIONALS": None}   # plain "Nationals" is ambiguous; fall back to the full name below


def event_label(name, event_id, cfg):
    """Short race name for menus and folders: 'Midwest Nationals', 'U.S. Nationals', 'Gatornationals'."""
    over = cfg.get("event_labels", {}).get(str(event_id))
    if over:
        return over
    n = re.split(r"\s+(?:PRESENTED|POWERED)\s+BY\b", (name or "").upper())[0].strip()
    tail = n.split("NHRA ")[-1].strip() if "NHRA " in n else n
    if tail in LABEL_FIX:
        tail = n.replace(" NHRA", "").replace("NHRA ", "")
    return " ".join(w.capitalize() if not re.match(r"^[A-Z]\.", w) else w for w in tail.lower().split()).replace("4-wide", "4-Wide").replace("U.s.", "U.S.")


def slug_for(year, label):
    return f"{year}-" + re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")


def load_template():
    t = open(os.path.join(HERE, "template.html")).read()
    t = t.replace("__LOGO__", open(os.path.join(HERE, "assets", "di-logo.svg")).read())
    ga = json.load(open(os.path.join(HERE, "config.json"))).get("analytics", {}).get("ga4")
    tag = ""
    if ga and re.fullmatch(r"G-[A-Z0-9]+", ga):
        tag = (f'<script async src="https://www.googletagmanager.com/gtag/js?id={ga}"></script>'
               '<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}'
               f'gtag("js",new Date());gtag("config","{ga}",{{content_group:"DI Incrementals"}});</script>')
    return t.replace("__GA__", tag)


def build_bundles(year, event, cfg, open_round="LATEST"):
    bundles = {}
    for cls in cfg["classes"]:
        data_dir, pulled = pull(year, event, cls)
        if not pulled:
            continue
        b = build.build(data_dir)
        b["default_round"] = open_round if open_round in pulled else pulled[-1]
        bundles[cls] = b
    return bundles


def load_registry(site):
    path = os.path.join(site, "events", "events.json")
    if not os.path.exists(path):
        return {"live": None, "events": []}
    reg = json.load(open(path))
    return reg if isinstance(reg, dict) else {"live": None, "events": reg}


def save_registry(site, reg):
    path = os.path.join(site, "events", "events.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    reg["events"].sort(key=lambda e: e.get("date") or "", reverse=True)
    json.dump(reg, open(path, "w"), indent=1)


def register(reg, year, event, bundles, cfg):
    b = next(iter(bundles.values()))
    label = event_label(b["event_name"], event, cfg)
    slug = slug_for(year, label)
    entry = {"slug": slug, "id": str(event), "name": b["event_name"], "label": label, "year": int(year),
             "date": b.get("event_date"), "classes": [c for c in cfg["classes"] if c in bundles]}
    reg["events"] = [e for e in reg["events"] if e["slug"] != slug and e.get("id") != str(event)] + [entry]
    return slug, label


def render_event(dest, root, bundles, cfg, slug, label, template, preview=False, waiting_query=None, year=None):
    """One complete, self-contained copy of a race: home page, class pages, spreadsheets, logo."""
    shutil.copytree(os.path.join(HERE, "assets"), os.path.join(dest, "assets"), dirs_exist_ok=True)
    nav = [{"slug": c, "name": CLASS_NAMES.get(c, c), "live": c in bundles} for c in cfg["classes"]]
    common = {"nav": nav, "sponsor": cfg.get("sponsor", {}), "slug": slug, "label": label}
    if not bundles:
        write_page(os.path.join(dest, "index.html"), template.replace("__DATA__", json.dumps(dict(
            common, waiting=True, year=year, event_query=waiting_query, base="", root=root))))
        return
    for cls, b in bundles.items():
        csv_name = f"DI-Incrementals-{slug}-{cls}.csv"
        write_csv(os.path.join(dest, cls, csv_name), b, CLASS_NAMES.get(cls, cls))
        page = dict(b, **common, class_name=CLASS_NAMES.get(cls, cls), base="../", root="../" + root,
                    csv=csv_name, save=f"DI-Incrementals-{slug}-{cls}.html", self_path="index.html")
        write_page(os.path.join(dest, cls, "index.html"),
                   template.replace("__DATA__", json.dumps(page, separators=(",", ":")).replace("</", "<\\/")))
    first = next(c for c in cfg["classes"] if c in bundles)
    page = dict(bundles[first], **common, class_name=CLASS_NAMES.get(first, first), base="", root=root,
                csv=f"{first}/DI-Incrementals-{slug}-{first}.csv", save=f"DI-Incrementals-{slug}-{first}.html",
                self_path=f"{first}/index.html")
    write_page(os.path.join(dest, "index.html"),
               template.replace("__DATA__", json.dumps(page, separators=(",", ":")).replace("</", "<\\/")),
               os.path.join(HERE, "preview", "index.html") if preview else None)


def season_events(year):
    """Every event id and name NHRA lists for the season."""
    base = SERIES_URL.format(year=year)
    ids = re.findall(r"/results/%s/nhra-mission-foods-drag-racing-series/(\d+)" % year, get(base))
    html = get(f"{base}/{ids[0]}/detailed-results")
    return re.findall(r'value="/results/%s/nhra-mission-foods-drag-racing-series/(\d+)">\s*([^<]+)' % year, html)


def backfill(year, only=None, workers=6):
    """Archive every finished race of a season (or just the ids given) under site/events/.
    Races are pulled in parallel; a race already in the archive is skipped unless named in `only`."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    cfg = json.load(open(os.path.join(HERE, "config.json")))
    site = os.path.join(HERE, cfg.get("out_dir", "site"))
    template = load_template()
    reg = load_registry(site)
    done = {e.get("id") for e in reg["events"]}
    todo = [(e, n) for e, n in season_events(year) if (only and e in only) or (not only and e not in done)]

    def one(event, name):
        return event, name, build_bundles(year, event, cfg)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for fut in as_completed([pool.submit(one, e, n) for e, n in todo]):
            try:
                event, name, bundles = fut.result()
            except Exception as ex:
                print(f"failed: {ex}", file=sys.stderr)
                continue
            if not bundles:
                print(f"skip {event} {name.strip()}: no results", file=sys.stderr)
                continue
            slug, label = register(reg, year, event, bundles, cfg)
            for cls, b in bundles.items():
                b["leaders"] = leaders_for(cls, b, label, reg, year, b.get("event_date"))
            render_event(os.path.join(site, "events", slug), "../../", bundles, cfg, slug, label, template)
            save_registry(site, reg)
            print(f"archived {slug}: {', '.join(bundles)}", file=sys.stderr)


LB_KEYS = ["rt", "ft60", "ft330", "ft660", "ft1000", "back", "gain", "et", "mph"]
HIGHER = {"mph", "gain"}
_built = {}


def built(path):
    if path not in _built:
        _built[path] = build.build(path) if os.path.isdir(path) and any(f.endswith(".json") for f in os.listdir(path)) else None
    return _built[path]


def top_by_key(entries, n=5):
    """Each driver's best at every block, top n, from (bundle, race label) pairs. Clean runs only."""
    out = {}
    for k in LB_KEYS:
        best = {}
        for b, race in entries:
            for rd in b["rounds"]:
                for r in rd["runs"]:
                    v = r.get(k)
                    if v is None or (k == "rt" and (r.get("red") or v < 0)):
                        continue
                    if k not in ("et", "mph") and k not in r["valid"]:
                        continue
                    if k in ("et", "mph", "rt") and not r["clean"] and k != "rt":
                        continue
                    cur = best.get(r["driver"])
                    better = cur is None or (v > cur["v"] if k in HIGHER else v < cur["v"])
                    if better:
                        best[r["driver"]] = {"driver": r["driver"], "v": v, "race": race, "round": rd["round"]}
        rows = sorted(best.values(), key=lambda x: -x["v"] if k in HIGHER else x["v"])[:n]
        if rows:
            out[k] = rows
    return out


def leaders_for(cls, bundle, label, reg, year, upto):
    """'This race' and 'season to date' leaderboards for one class."""
    season = []
    for e in reg["events"]:
        if e.get("year") != year or cls not in e.get("classes", []) or (e.get("date") or "") > (upto or "9999"):
            continue
        b = built(os.path.join(HERE, DATA_ROOT, f"{e['year']}-{e['id']}", cls))
        if b:
            season.append((b, e["label"]))
    return {"race": top_by_key([(bundle, label)]), "season": top_by_key(season), "season_races": len(season)}


def rerender_all(site, cfg, template, reg):
    """Rebuild every archived race from its saved data (no NHRA calls), so a design or wording
    change reaches past races too."""
    for e in reg["events"]:
        if e["slug"] == reg.get("live"):
            continue
        root = os.path.join(HERE, DATA_ROOT, f"{e['year']}-{e['id']}")
        bundles = {}
        for cls in cfg["classes"]:
            d = os.path.join(root, cls)
            if os.path.isdir(d) and any(f.endswith(".json") for f in os.listdir(d)):
                b = built(d)
                if b:
                    b = dict(b, default_round=b["rounds"][-1]["round"])
                    b["leaders"] = leaders_for(cls, b, e["label"], reg, e["year"], e.get("date"))
                    bundles[cls] = b
        if bundles:
            render_event(os.path.join(site, "events", e["slug"]), "../../", bundles, cfg, e["slug"], e["label"], template)


def main():
    cfg = json.load(open(os.path.join(HERE, "config.json")))
    if len(sys.argv) > 1 and sys.argv[1] == "backfill":
        backfill(int(sys.argv[2]), set(sys.argv[3:]) or None)
        return
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
    template = load_template()

    bundles = build_bundles(year, event, cfg, cfg.get("open_round", "LATEST"))
    reg = load_registry(site)
    slug = label = None
    if bundles:
        slug, label = register(reg, year, event, bundles, cfg)
        reg["live"] = slug
        for cls, b in bundles.items():
            b["leaders"] = leaders_for(cls, b, label, reg, year, b.get("event_date"))
    save_registry(site, reg)
    render_event(site, "", bundles, cfg, slug, label, template, preview=True, waiting_query=cfg["event"], year=year)
    if bundles:
        render_event(os.path.join(site, "events", slug), "../../", bundles, cfg, slug, label, template)
    event_name = next((b["event_name"] for b in bundles.values()), None)

    # when the page, analysis or settings change, rebuild the archived races too
    code_hash = hashlib.sha256("".join(open(os.path.join(HERE, f)).read()
                                       for f in ("template.html", "build.py", "config.json", "run.py")).encode()).hexdigest()
    ch_file = os.path.join(site, ".codehash")
    if (open(ch_file).read() if os.path.exists(ch_file) else "") != code_hash:
        rerender_all(site, cfg, template, reg)
        open(ch_file, "w").write(code_hash)

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
