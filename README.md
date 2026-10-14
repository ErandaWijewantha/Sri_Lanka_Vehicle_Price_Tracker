# Sri Lanka Vehicle Price Tracker

Tracks asking prices of vehicles listed on **riyasewana.com** and **ikman.lk** over time and shows trends on a dashboard: median price per make/model/year, biggest price cuts, which models are heating up or cooling down, and new and delisted ads.

Prices are **asking prices**, not sale prices.

Runs entirely on free tiers: GitHub Actions does the scraping, Supabase Postgres stores the data and Streamlit Community Cloud hosts the dashboard.

## How it works

| Job | When | What it does |
|---|---|---|
| `sync.yml` | every 3 hours, one job per site | **1. Fresh scan:** walks each category newest-first and stops once a whole page (ignoring promoted ads) has only ads it already knows. **2. Backfill chunk:** continues walking older pages from where the last run stopped, up to 250 pages. Together the chunks cover every page about once a day. When a category's pass finishes, ads missing from 2 complete passes in a row are marked delisted. |
| `stats.yml` | daily, 02:00 Colombo time | Rebuilds `daily_stats` for the day that just ended. |
| `full.yml` | manual only | Walks every page of one site in a single run. Normally not needed. |
| `tests.yml` | on push | Runs `pytest` against a Postgres service container. |

Storage stays small because only changes are kept:
- `listings` has one row per ad.
- `price_changes` has one row per price change.
- `daily_stats` has one row per (type, make, model, year) group per day, for groups with at least 3 active ads.

Scraping is deliberately polite:
- Listing pages only, one request at a time per site, 3–5 s apart on riyasewana and 1.5–3 s on ikman.
- An identifiable User-Agent.
- On ikman, only the `?page=` parameter, as its robots.txt requires.
- No phone numbers or seller details.

## Setup

1. **Supabase**: create a free project. Then:
   1. Copy the *pooled* connection string (port 6543) into `.env` as `DATABASE_URL` (see `.env.example`).
   2. Create the tables:
      ```bash
      python -m scraper.run --mode init-db      # or: psql "$DATABASE_URL" -f db/schema.sql
      ```
   3. Create the read-only dashboard role. The commands are commented at the bottom of `db/schema.sql`; run them with a real password.
2. **First load**: nothing special is needed. The scheduled `sync` runs fill the database within about a day. To speed this up, run `python -m scraper.run --mode sync` locally, or trigger `sync` from the Actions tab with more backfill pages. Don't go much faster than the defaults: riyasewana rate-limits fast scrapers.
3. **GitHub**:
   1. Push the repo. It must be public for unlimited free Actions minutes.
   2. Add the repository secret `DATABASE_URL`. The schedules then start on their own.
4. **Dashboard**: on Streamlit Community Cloud, deploy `dashboard/app.py`. Under app secrets, add `DATABASE_URL` using the `dashboard_ro` connection string.

## Local commands

```bash
python -m venv .venv && .venv/Scripts/activate        # Windows; use bin/activate elsewhere
pip install -r requirements.txt
python -m scraper.run --mode sync                     # fresh, then a 250-page backfill chunk per site
python -m scraper.run --mode fresh                    # new ads only
python -m scraper.run --mode backfill --source ikman --backfill-pages 100
python -m scraper.run --mode full --source ikman      # one whole site in one go (slow)
python -m scraper.run --mode fresh --dry-run --max-pages 1   # fetch + parse only, no DB
python -m scraper.run --mode stats [--day 2026-10-14] # rebuild daily_stats
pytest                                                # DB tests run only if TEST_DATABASE_URL is set
streamlit run dashboard/app.py
```

## Maintenance

- **A job fails with "parsed 0 ads"**: the site layout probably changed. To fix it:
  1. Save the new page into `tests/fixtures/`.
  2. Update the tests to the new layout and watch them fail.
  3. Fix the parser in `scraper/sources/` until they pass.
- **One model is split across several names on the dashboard**: add an alias to `_MODEL_ALIASES` (or `_MAKE_ALIASES`) in `scraper/normalize.py`.
- **Before making the dashboard public**: read both sites' terms, at https://riyasewana.com/terms.php and https://ikman.lk/en/terms.
