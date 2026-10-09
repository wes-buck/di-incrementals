# DI Incrementals

Round-by-round incrementals for NHRA pro classes, built from NHRA's posted detailed results.
Every car at every timing light, ranked, with splits, car slips, the top five at each light,
lane averages and the qualifying ladder.

## Race weekend

1. Nothing to do. `config.json` holds the rest of the 2026 schedule, and each week the page switches
   to the race whose start date has arrived. Add next season's races there when NHRA posts them.
2. From Thursday through Sunday the workflow checks NHRA every few minutes and
   publishes to GitHub Pages whenever a round posts. Before Q1 posts, the page says it's waiting.
3. To force an update, or to rebuild a past race: Actions tab > DI Incrementals > Run workflow
   (type an event such as `midwest` to rebuild that race).

## How it works

- `scrape.py` reads one round of NHRA detailed results (reads the table headers, so quarter-mile
  and 1,000-foot nitro tables both work)
- `build.py` rankings, splits, where a car went away, per-car read, ladder after each qualifying round
- `template.html` the page
- `run.py` finds the event, pulls every posted round for every class in `config.json`, renders
  `site/index.html` plus `site/<class>/index.html`, and prints CHANGED when there's something new
- `.github/workflows/incrementals.yml` the schedule

Pulled rounds are saved under `data/` on the gh-pages branch. If NHRA hiccups mid-weekend,
the last good copy of each round stays up.

## Custom domain

Add a file named `CNAME` containing `incrementals.dragillustrated.com`, then point a DNS CNAME
record for `incrementals` at `wes-buck.github.io`. Turn on HTTPS in Settings > Pages.

## Run it locally

    pip install -r requirements.txt
    python run.py                      # whatever config.json says
    python run.py 2026 midwest Q1      # one-off for a past event
