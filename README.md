# Chess Calendar (India) — static / GitHub-hosted build

A **free**, serverless deployment of the chess-calendar app. There is no live
backend: a scheduled GitHub Actions job runs the scrapers and commits the
result as a static JSON file, and GitHub Pages serves the frontend.

This repo was derived from the FastAPI app; the Python `backend/` is copied
verbatim and reused by `scripts/export_json.py` (which calls the same
`refresh_all()` / `query_tournaments()`), so scraping/geocoding/dedup behaviour
is identical — only the delivery changed.

## How it works

```
GitHub Actions (cron, 2×/day)          GitHub Pages
  refresh.yml                            pages.yml
    └─ python scripts/export_json.py       └─ serves site/
         ├─ refresh_all()   → backend/data/chess.sqlite   (committed)
         └─ query_tournaments() → site/tournaments.json   (committed)
```

- **`site/`** — the static frontend (`index.html`, `app.js`, `styles.css`) plus
  the generated `tournaments.json`. The frontend `fetch`es `./tournaments.json`
  instead of a live API; the old "Refresh sources" button is removed.
- **`backend/data/chess.sqlite` is committed on purpose.** It holds the
  per-tournament detail cache. Persisting it between runs keeps each scheduled
  refresh to a handful of requests instead of a ~1500-request cold crawl —
  essential for staying under chess-results.com's **2000 requests/IP/day** cap
  across two runs a day.

## One-time setup on GitHub

1. Create a repo and push this directory.
2. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
3. `refresh.yml` needs write access to commit data: **Settings → Actions →
   General → Workflow permissions → Read and write permissions.**
4. (Optional) trigger the first run manually: **Actions → "Refresh tournament
   data" → Run workflow.**

## Gotchas

- **Freshness** is bounded by the cron (2×/day). No on-demand refresh from the page.
- **Shared runner IPs:** Actions runners egress from shared, rotating IPs. The
  small per-run request count (thanks to the committed cache) keeps this well
  within chess-results' cap, but a cold crawl on a fresh DB is ~1500 requests —
  don't run several cold refreshes in one day.
- **Cron auto-disable:** GitHub disables scheduled workflows after **60 days**
  of repository inactivity. The 2×/day data commits count as activity, so this
  won't trigger in normal operation.

## Local preview

```
python scripts/export_json.py          # refresh + regenerate site/tournaments.json
python -m http.server -d site 8000     # then open http://localhost:8000
```
