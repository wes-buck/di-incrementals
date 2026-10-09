# DI Incrementals: notes for Claude

Live site: https://wes-buck.github.io/di-incrementals/ (GitHub Pages, gh-pages branch).
Owner: Wes Buck, Drag Illustrated. Never use em dashes in anything user-facing.

Where to make a change:
- Looks, wording, layout, colors, sections: `template.html` (one self-contained page; DI black/white, DI red = quickest only)
- Rankings, splits, "went away" rule, per-car read, ladder: `build.py`
- Reading NHRA's tables: `scrape.py` (header-driven; quarter-mile and 1,000-ft nitro)
- Which races and classes: `config.json` (`schedule` picks this week's race by start date)
- Schedule / publishing: `.github/workflows/incrementals.yml`

Workflow for a tweak:
1. `pip install -r requirements.txt`, then `python run.py 2026 midwest Q1` to build a past race into `site/`.
2. Make the change, rebuild, screenshot site/pro-mod/index.html at desktop and phone width.
3. Commit and push to main. Every push triggers the workflow, which republishes the live site
   (a change to template.html, build.py or config.json counts as a change even with no new results).
