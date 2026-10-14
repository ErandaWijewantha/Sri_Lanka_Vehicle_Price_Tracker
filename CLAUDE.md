# CLAUDE.md — Sri Lanka Vehicle Price Tracker

## Project goal
Track asking prices of all vehicle types listed on **riyasewana.com** and **ikman.lk** over time, store them in Postgres, and show trends on a live dashboard (price trends per make/model/year, biggest price cuts, supply heating up / cooling down, new and delisted ads).

Everything must run on **free tiers only**:
- **GitHub Actions** (public repo → unlimited free minutes on standard runners) — scheduler + scraper
- **Supabase Postgres** (free: 500 MB DB, pauses after 7 days idle — our 3-hourly writes keep it active)
- **Streamlit Community Cloud** — dashboard

## Architecture
```
riyasewana.com ─┐
                ├─> GitHub Actions (Python)
ikman.lk ───────┘     ├─ sync (every 3h): fresh scan, then a backfill chunk
                      └─ stats (daily, ~02:00 Asia/Colombo)
                             │
                   parse → normalize → upsert
                             │
                     Supabase Postgres
            listings | price_changes | daily_stats
                             │
                     Streamlit dashboard
```

## Repository layout
```
.
├── CLAUDE.md
├── README.md
├── requirements.txt
├── pyproject.toml              # pytest config
├── .env.example                # DATABASE_URL=postgresql://...
├── scraper/
│   ├── __init__.py
│   ├── config.py               # categories, delays, user agent, page sizes
│   ├── http.py                 # session, polite delay, retries w/ backoff
│   ├── models.py               # Listing dataclass (normalized shape)
│   ├── normalize.py            # make/model/price/mileage cleanup
│   ├── db.py                   # upserts, price change detection, delisting
│   ├── stats.py                # rebuild daily_stats
│   ├── run.py                  # CLI: python -m scraper.run --mode fresh|full [--source ...]
│   └── sources/
│       ├── __init__.py
│       ├── riyasewana.py       # parse_listing_page(html) -> list[Listing]
│       └── ikman.py
├── db/
│   └── schema.sql
├── dashboard/
│   └── app.py                  # Streamlit
├── tests/
│   ├── fixtures/               # saved HTML samples from each site
│   ├── test_riyasewana.py
│   ├── test_ikman.py
│   ├── test_db.py              # needs TEST_DATABASE_URL
│   └── test_normalize.py
└── .github/workflows/
    ├── sync.yml                # cron: every 3h (fresh + backfill chunk)
    ├── stats.yml               # cron: daily
    ├── full.yml                # manual one-shot sweep
    └── tests.yml               # pytest on push
```

## Sources

### riyasewana.com
- Listing URL: `https://riyasewana.com/search/<type>?page=N` — 40 ads/page
- Types: `cars, suvs, vans, motorcycles, lorries, three-wheels, pickups, heavy-duties`
  (all types: `https://riyasewana.com/search?page=N`, ~87k ads / ~2,170 pages)
- Ad ID: numeric suffix of ad URL, e.g. `/buy/toyota-axio-sale-negombo-12500688` → `12500688`
- Listing card shows: title (make + model + year + type), price (`Rs. 8,450,000` or `Negotiable`), year, city, mileage (`101,000 km`), posted time, `TOP AD` badge
- robots.txt allows `/search`. Never request: `/vehicle_more.php`, `/get-phone.php`, `/add-favorite.php`, `/get-price-range.php`, `/sug.php`, `/login.php?`

### ikman.lk
- Listing URL: `https://ikman.lk/en/ads/sri-lanka/<category>?page=N` — 25 ads/page
- Categories: `cars, motorbikes-scooters, three-wheelers, vans, lorries, heavy-duty, tractors, buses` (~45k ads / ~1,800 pages)
- Ad ID: the ad's `id` from the embedded JSON (e.g. `6ac9e339bed598567a7b8bda`), not the slug — slugs can change when a seller edits the title. URL is `/en/ad/<slug>`.
- Card shows: title, mileage, body type, condition (Used / Reconditioned / Brand New / Import), city, price (`Rs 7,690,000`), MEMBER badge, FEATURED / top-ad markers
- The page embeds its results as JSON in `window.initialData` (`serp.ads.data.ads`, `paginationData.total`); the parser reads that, not the HTML. `topAds` duplicates entries in `ads` and is ignored. Promoted = `isTopAd` or `isFeaturedAd`.
- The same ad can appear on two pages in one run with **different prices** (one copy stale). Only the first sighting per run counts — otherwise every run logs a fake price cut + rise.
- **robots.txt: only `?page=` is allowed.** Never add `sort=`, `filters=`, `query=`, `type=`, `c=`, `tree.brand=`, `nearby=`, `login-modal` or other disallowed params.

### Before going public
Read and respect both sites' terms: `https://riyasewana.com/terms.php`, `https://ikman.lk/en/terms`.

## Scraping rules (non-negotiable)
- One request at a time per site, with a per-site delay (`config.DELAY_S`): riyasewana 3–5 s, ikman 1.5–3 s. riyasewana returned HTTP 429 after ~480 pages at 1–2 s (Oct 2026), so don't speed it up.
- On a persistent 429, stop scraping that site for the rest of the run (no next-page pecking). Abort a category after 3 failed pages in a row.
- Real, identifiable User-Agent (e.g. `vehicle-price-tracker/0.1 (+github repo url)`).
- Retries with exponential backoff on 429/5xx; give up on a page after 3 tries and log it.
- Listing pages only — never fetch phone numbers or seller contact info. Don't store seller names/phones.
- If a run parses **0 ads** from a page that should have ads, fail the job loudly (layout probably changed).

## Run modes

Scheduled: `--mode sync` every 3h = **fresh first, then a backfill chunk** for that site (same client, same politeness). `--mode fresh`, `--mode backfill` and `--mode full` exist for manual runs.

### fresh
For each source and category, newest-first, page 1 → N:
1. Skip promoted ads (`TOP AD`, `FEATURED`) when deciding whether to stop.
2. New ad → insert into `listings` (status `active`, `first_seen = last_seen = now`).
3. Known ad → update `last_seen`; if price differs, insert into `price_changes` and update `listings.price`.
4. **Stop** when an entire page (excluding promoted ads) contains only known ads (or ads already seen earlier in this run) — bumped old ads reappear at the top, so never stop at the first known ad.
5. Hard cap pages per category (e.g. 60) as a safety net.

Expected load: ~30–60 pages per run, a few minutes.

### backfill (rolling, replaces a nightly full sweep)
Walking ~2,000 pages of one site in one go got riyasewana to rate-limit us, so every page is covered in chunks instead:
- Each run fetches up to `BACKFILL_PAGES_PER_RUN` (250) pages per site, resuming from a cursor in `backfill_state` (per source + category), re-reading `BACKFILL_OVERLAP_PAGES` (2) pages before it. The cursor is saved even when the run is cut short (429, crash).
- Categories are finished in config order; when every category of a site has reached its last page, the cycle resets to page 1. 8 runs/day × 250 ≈ 2,000 pages/day ≈ one full pass per site per day.
- Same upsert + price-change logic as fresh.
- When a category's pass reaches the end: ads not seen since that pass started, in **2 consecutive passes** → `status = 'delisted'`, set `delisted_at`. (Delisted usually means sold, sometimes expired.)
- Misses are counted per `(source, category)` and **only for a complete pass** (≥ 90% of expected pages, ≤ 1% failed pages); each completed pass is logged in the `sweeps` table.
- A delisted ad that reappears → back to `active`.

### full (manual)
Same as one whole backfill pass, in a single run (riyasewana ≈ 2.5 h, ikman ≈ 1.5 h). Use sparingly.

### stats (daily)
Rebuild `daily_stats` for the day that just ended.

## Data model (db/schema.sql)
Keep storage small: **no full snapshot per run**. Store changes only. `db/schema.sql` is the source of truth (idempotent; also enables RLS so Supabase's REST API can't read the tables).

`daily_stats` keeps only groups with ≥ 3 active ads (`STATS_MIN_GROUP_SIZE`) — without that cap it would outgrow the 500 MB free tier within a year. Days are Sri Lanka days (fixed UTC+05:30). The nightly sweep writes stats for the day that just ended.

```sql
create table listings (
  source        text not null,            -- 'riyasewana' | 'ikman'
  ad_id         text not null,
  url           text not null,
  title         text,
  category      text,                     -- site category slug (delisting is per category)
  vehicle_type  text,                     -- car, suv, van, motorcycle, three_wheel, lorry, pickup, heavy_duty, bus, tractor
  make          text,
  model         text,                     -- normalized
  model_raw     text,
  year          int,
  condition     text,                     -- used, reconditioned, brand_new, unregistered...
  price         bigint,                   -- LKR; null when 'Negotiable'
  mileage_km    int,
  city          text,
  is_promoted   boolean default false,
  status        text not null default 'active',  -- active | delisted
  first_seen    timestamptz not null,
  last_seen     timestamptz not null,
  missed_sweeps int not null default 0,
  delisted_at   timestamptz,
  primary key (source, ad_id)
);

create table price_changes (
  id         bigserial primary key,
  source     text not null,
  ad_id      text not null,
  old_price  bigint,
  new_price  bigint,
  changed_at timestamptz not null default now(),
  foreign key (source, ad_id) references listings(source, ad_id)
);

create table daily_stats (
  day           date not null,
  vehicle_type  text not null,
  make          text not null,
  model         text not null,
  year          int  not null,
  active_count  int,
  new_count     int,
  delisted_count int,
  median_price  bigint,
  min_price     bigint,
  max_price     bigint,
  primary key (day, vehicle_type, make, model, year)
);

create table sweeps (source, category, started_at, finished_at, pages, ads_seen);  -- completed passes
create table backfill_state (source, category, next_page, cycle_started_at, pages_done, failed_pages, ads_seen, completed_at, ...);  -- rolling cursor

create index on listings (make, model, year);
create index on listings (status, last_seen);
create index on price_changes (changed_at);
```

## Normalization
- Price: strip `Rs.`, `Rs`, commas → int; `Negotiable` → null. Ignore obvious junk (e.g. < Rs 10,000 for cars).
- Mileage: strip commas/`km` → int; flag absurd values (e.g. > 1,000,000) as null.
- Make: map to canonical names (`Hero-Honda`, `Mercedes Benz` → `Mercedes-Benz`, etc.).
- Model: lowercase, trim trims/grades into a base model where possible (`Wagon R FZ`, `Wagon R FX Safety` → `wagon r`), keep original in `model_raw`. Keep the mapping table in `normalize.py` and grow it over time.
- Year: int; reject future years > current year + 1.
- Same car on both sites is **not** merged in v1 — stats are per listing. (Possible later: fuzzy match on make/model/year/mileage/city/price.)

## Dashboard (dashboard/app.py)
Read-only from Supabase (use a read-only DB role). Pages:
1. **Market** — active ads by type, new/delisted this week, biggest price cuts (24h / 7d), heating up / cooling down (change in active_count + new_count).
2. **Model trend** — pick type → make → model → year: median price over time, supply over time, list of current ads with links.
3. **Browse** — filterable table of active listings.
Cache queries with `st.cache_data(ttl=600)`.

Label clearly: **prices are asking prices, not sale prices.**

## GitHub Actions
- `sync.yml`: `cron: '0 */3 * * *'` + `workflow_dispatch` (input: backfill pages); matrix over source; `--mode sync`.
- `stats.yml`: `cron: '30 20 * * *'` (20:30 UTC = 02:00 Asia/Colombo) + `workflow_dispatch`; `--mode stats`.
- `full.yml`: `workflow_dispatch` only (choose source).
- `tests.yml`: pytest on push, with a Postgres service container for `tests/test_db.py`.
- Both scrape workflows share `concurrency: scrape-<source>` and set `SCRAPER_USER_AGENT` to include the repo URL.
- Secret: `DATABASE_URL` (Supabase pooled connection string). Never commit secrets; keep `.env` in `.gitignore`.
- Use `concurrency` groups so two runs never scrape the same site at once.

## Commands
```bash
pip install -r requirements.txt
cp .env.example .env                      # add DATABASE_URL
python -m scraper.run --mode init-db     # or: psql "$DATABASE_URL" -f db/schema.sql
python -m scraper.run --mode sync                    # what the schedule runs
python -m scraper.run --mode fresh
python -m scraper.run --mode backfill --backfill-pages 100
python -m scraper.run --mode full --source riyasewana
python -m scraper.run --mode fresh --dry-run --max-pages 1   # no DB
python -m scraper.run --mode stats
pytest                                    # tests/test_db.py needs TEST_DATABASE_URL (throwaway DB)
streamlit run dashboard/app.py
```

## Stack
Python 3.11+, `httpx`, `selectolax` ≥ 1.0 (lexbor backend: `selectolax.lexbor`), `psycopg[binary]`, `pandas`, `streamlit`, `plotly`, `pytest`, `python-dotenv`.

## Coding conventions
- One parser module per site; parsers are pure functions `html -> list[Listing]` with no network calls, tested against saved fixtures in `tests/fixtures/`.
- When a parser breaks, save the new HTML as a fixture and fix the test first.
- Batch DB writes (`executemany` / `COPY`), one transaction per page.
- Structured logging: per run print pages fetched, ads parsed, new, updated, price changes, failures.
- Keep functions small; type hints everywhere.

## Build order
1. riyasewana parser + tests against a saved HTML fixture
2. Supabase schema + `db.py` upserts + price-change detection
3. `run.py --mode fresh` working locally for riyasewana
4. ikman parser + tests
5. full sweep + delisting + `daily_stats`
6. GitHub Actions workflows
7. Streamlit dashboard
