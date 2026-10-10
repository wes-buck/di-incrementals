# DI Incrementals: notes for Claude

Live site: https://wes-buck.github.io/di-incrementals/ (GitHub Pages, gh-pages branch).
Owner: Wes Buck, Drag Illustrated. Never use em dashes in anything user-facing.

Where to make a change:
- Looks, wording, layout, colors, sections: `template.html` (phone first: car cards under 900px, table above. DI black/white/red is brand chrome; data heat scale is green = quickest, then yellow, orange, red. W/L only shown in eliminations. "How Do I Compare?" picks two cars by name, this round or each car's best clean run of the weekend). Eliminations Ladder: bracket from the final qualifying order (NHRA 16-car pairings), projected from current qualifying until E1 posts; holeshot and margin flags; tapping a pairing loads it into How Do I Compare. The qualifying list is titled "Qualifying Order"
- Rankings, splits, "went away" rule, per-car read, ladder: `build.py`
- Reading NHRA's tables: `scrape.py` (header-driven; quarter-mile and 1,000-ft nitro)
- Which races and classes: `config.json` (`schedule` picks this week's race by start date)
- Sponsor spots: `config.json` > `sponsor` (logo files go in `assets/`); DI logo is `assets/di-logo.svg`
- Print, downloads, archive: print sheet is `#print` + `@media print` in template.html; run.py writes a CSV per class and a permanent copy of each race under site/events/<year-event>/ (listed in events/events.json, shown as Past Races)
- Race picker / archive: site/events/events.json ({live, events[]}) drives the header Race menu and Past Races. Short race names: config.json > event_labels. Backfill a season into a checkout of gh-pages at site/: `DI_DATA_DIR=site/data python run.py backfill 2026` (skips races already archived), then commit and push gh-pages. Any change to template.html, build.py, config.json or run.py automatically re-renders every archived race from saved data on the next run
- Usage stats: Google Analytics (DI property, config.json > analytics.ga4). Custom events: view_round, open_slip, rank_by, compare, ladder_compare, switch_race, print_round, download_spreadsheet, save_page; params race, class_name, round. Nothing is shown on the page
- Schedule / publishing: `.github/workflows/incrementals.yml`

Workflow for a tweak:
1. `pip install -r requirements.txt`, then `python run.py 2026 midwest Q1` to build a past race into `site/`.
2. Make the change, rebuild, screenshot site/pro-mod/index.html at desktop and phone width.
3. Commit and push to main. Every push triggers the workflow, which republishes the live site
   (a change to template.html, build.py or config.json counts as a change even with no new results).
