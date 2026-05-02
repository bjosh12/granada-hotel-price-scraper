-- Run this once in the Supabase SQL editor to set up the schema

create table if not exists price_snapshots (
  id          bigserial primary key,
  hotel_id    text not null,
  name        text not null,
  is_mine     boolean not null default false,
  price       numeric(8, 2),
  scraped_at  timestamptz not null default now(),
  checkin_date date not null,
  run_mode    text not null default 'daily'
);

-- Index for common query patterns
create index if not exists idx_price_snapshots_hotel_checkin
  on price_snapshots (hotel_id, checkin_date, scraped_at desc);

create index if not exists idx_price_snapshots_scraped_at
  on price_snapshots (scraped_at desc);

-- Optional: view for the latest price per hotel per date
create or replace view latest_prices as
select distinct on (hotel_id, checkin_date)
  hotel_id,
  name,
  is_mine,
  price,
  checkin_date,
  scraped_at
from price_snapshots
order by hotel_id, checkin_date, scraped_at desc;


-- Example queries you can run after data starts accumulating:

-- Latest prices for a specific check-in date
-- select * from latest_prices where checkin_date = current_date + 1;

-- Price history for your hotels over the past 7 days
-- select name, checkin_date, price, scraped_at
-- from price_snapshots
-- where is_mine = true
--   and scraped_at > now() - interval '7 days'
-- order by scraped_at desc;

-- Average price by hotel for the past 30 days
-- select name, avg(price)::numeric(8,2) as avg_price, count(*) as samples
-- from price_snapshots
-- where scraped_at > now() - interval '30 days'
-- group by name
-- order by avg_price;
