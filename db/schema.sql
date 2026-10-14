-- Sri Lanka Vehicle Price Tracker schema (Supabase Postgres).
-- Idempotent: safe to re-run.

create table if not exists listings (
  source        text not null,            -- 'riyasewana' | 'ikman'
  ad_id         text not null,
  url           text not null,
  title         text,
  category      text,                     -- site category slug the ad was found under
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

create table if not exists price_changes (
  id         bigserial primary key,
  source     text not null,
  ad_id      text not null,
  old_price  bigint,
  new_price  bigint,
  changed_at timestamptz not null default now(),
  foreign key (source, ad_id) references listings(source, ad_id)
);

create table if not exists daily_stats (
  day            date not null,
  vehicle_type   text not null,
  make           text not null,
  model          text not null,
  year           int  not null,
  active_count   int,
  new_count      int,
  delisted_count int,
  median_price   bigint,
  min_price      bigint,
  max_price      bigint,
  primary key (day, vehicle_type, make, model, year)
);

-- One row per completed full sweep of a (source, category); used for delisting.
create table if not exists sweeps (
  id          bigserial primary key,
  source      text not null,
  category    text not null,
  started_at  timestamptz not null,
  finished_at timestamptz not null,
  pages       int not null,
  ads_seen    int not null
);

-- Rolling backfill cursor: each run walks a chunk of pages and saves where it
-- stopped. A cycle ends when every category of a source has reached its last page.
create table if not exists backfill_state (
  source           text not null,
  category         text not null,
  next_page        int  not null default 1,
  cycle_started_at timestamptz,             -- when this category's current pass began at page 1
  pages_done       int  not null default 0, -- pages fetched in the current pass
  failed_pages     int  not null default 0,
  ads_seen         int  not null default 0,
  completed_at     timestamptz,             -- set when the current pass reached the last page
  updated_at       timestamptz not null default now(),
  primary key (source, category)
);

create index if not exists listings_make_model_year_idx on listings (make, model, year);
create index if not exists listings_status_last_seen_idx on listings (status, last_seen);
create index if not exists listings_source_category_idx on listings (source, category, status);
create index if not exists price_changes_changed_at_idx on price_changes (changed_at);
create index if not exists price_changes_ad_idx on price_changes (source, ad_id);

-- Supabase exposes public tables through its REST API; RLS with no policies
-- blocks that. The scraper connects as the table owner, which bypasses RLS.
alter table listings      enable row level security;
alter table price_changes enable row level security;
alter table daily_stats   enable row level security;
alter table sweeps        enable row level security;
alter table backfill_state enable row level security;

-- Read-only role for the Streamlit dashboard. Run once by hand, with a real password:
--   create role dashboard_ro login password 'CHANGE_ME';
--   grant usage on schema public to dashboard_ro;
--   grant select on listings, price_changes, daily_stats to dashboard_ro;
--   create policy dashboard_ro_read on listings      for select to dashboard_ro using (true);
--   create policy dashboard_ro_read on price_changes for select to dashboard_ro using (true);
--   create policy dashboard_ro_read on daily_stats   for select to dashboard_ro using (true);
