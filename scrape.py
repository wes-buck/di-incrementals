"""Pull one round of NHRA detailed results and save it as clean JSON.

Usage:
  python3 -I scrape.py <year> <event_id> <class_slug> <round> [out.json]
  e.g. python3 -I scrape.py 2026 73887 pro-mod q1 data/q1.json
"""
import json
import re
import sys
import time
import urllib.request

from bs4 import BeautifulSoup

SERIES = "nhra-mission-foods-drag-racing-series"
URL = "https://www.nhra.com/results/{year}/{series}/{event}/detailed-results/{cls}/{rnd}"

HEADER_KEYS = {"lane": "lane", "driver": "driver", "car no.": "car", "class": "class", "dial in": "dial",
               "q. pos.": "qpos_nhra", "rt": "rt", "60 ft": "ft60", "330 ft": "ft330", "660 ft": "ft660",
               "660 mph": "mph660", "1000 ft": "ft1000", "et": "et", "mph": "mph", "ov/un": "ovun",
               "mov": "mov", "first": "first"}
NUMERIC = {"rt", "ft60", "ft330", "ft660", "mph660", "ft1000", "et", "mph", "mov", "first"}


def num(v):
    v = v.strip()
    if not v or v.startswith("--"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def fetch(year, event, cls, rnd, tries=4):
    url = URL.format(year=year, series=SERIES, event=event, cls=cls, rnd=rnd)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (DI Incrementals)"})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return url, r.read().decode("utf-8", "replace")
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(2 * (attempt + 1))


MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
DATE_RE = re.compile(r"\b(%s)[a-z]*\.? (\d{1,2})\s*-\s*(?:(?:%s)[a-z]*\.? )?\d{1,2},\s*(\d{4})" % ("|".join(MONTHS), "|".join(MONTHS)))


def event_date(html):
    """Start date of the event as YYYY-MM-DD, from the date line on NHRA's results page."""
    m = DATE_RE.search(BeautifulSoup(html, "html.parser").get_text(" "))
    if not m:
        return None
    return f"{m.group(3)}-{MONTHS.index(m.group(1)) + 1:02d}-{int(m.group(2)):02d}"


def parse(html):
    soup = BeautifulSoup(html, "html.parser")
    event = soup.select_one("h2")
    event_name = None
    for h in soup.find_all("h2"):
        t = h.get_text(" ", strip=True)
        if "NATIONALS" in t.upper() or "FINALS" in t.upper():
            event_name = t
    table = soup.select_one("table.table--results--desktop")
    pairs, current, cols = [], [], []
    if table is None:
        return event_name, pairs
    for tr in table.find_all("tr"):
        if tr.find("th"):
            cols = [HEADER_KEYS.get(th.get_text(" ", strip=True).lower(), "x") for th in tr.find_all("th")]
            if current:
                pairs.append({"runs": current, "note": None})
            current = []
            continue
        note = tr.select_one("td.table-notes")
        if note is not None:
            text = note.get_text(" ", strip=True) or None
            if current:
                pairs.append({"runs": current, "note": text})
                current = []
            elif pairs and text:
                pairs[-1]["note"] = text
            continue
        tds = tr.find_all("td")
        if not cols or len(tds) < len(cols):
            continue
        row = {k: None for k in ("rt", "ft60", "ft330", "ft660", "mph660", "ft1000", "et", "mph", "first")}
        for key, td in zip(cols, tds):
            if key == "driver":
                bubble = td.select_one(".table__bubble")
                row["win"] = bool(bubble and "WIN" in bubble.get_text())
                if bubble:
                    bubble.extract()
                row["driver"] = re.sub(r"\s+", " ", td.get_text(" ", strip=True))
            elif key in NUMERIC:
                row[key] = num(td.get_text())
            else:
                row[key] = re.sub(r"\s+", " ", td.get_text()).strip()
        current.append(row)
    if current:
        pairs.append({"runs": current, "note": None})
    return event_name, pairs


def main():
    year, event, cls, rnd = sys.argv[1:5]
    out = sys.argv[5] if len(sys.argv) > 5 else None
    url, html = fetch(year, event, cls, rnd)
    event_name, pairs = parse(html)
    data = {"source_url": url, "year": int(year), "event_id": event, "class": cls,
            "round": rnd.upper(), "event_name": event_name, "pairs": pairs}
    text = json.dumps(data, indent=1)
    if out:
        open(out, "w").write(text)
    else:
        print(text)
    print(f"{len(pairs)} pairs, {sum(len(p['runs']) for p in pairs)} runs", file=sys.stderr)


if __name__ == "__main__":
    main()
