# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Install dependencies:**
```bash
pip install -r requirements.txt
playwright install chromium --with-deps
```

**Run the scraper locally** (requires all env vars set):
```bash
RUN_MODE=daily python scraper/scraper.py
RUN_MODE=weekly python scraper/scraper.py
```

**Required environment variables for local runs:**
```
SUPABASE_URL, SUPABASE_KEY, RESEND_API_KEY, FROM_EMAIL,
EMAIL_1, ALERT_EMAIL, PROXY_URL (optional), WEBSHARE_API_KEY (optional)
```

## Architecture

Single-file Python scraper (`scraper/scraper.py`) triggered by GitHub Actions on a cron schedule. No web server, no local state — everything flows through in one run and exits.

```
GitHub Actions (cron) → scraper.py → Booking.com (Playwright/Chromium)
                                    → Supabase (price_snapshots table)
                                    → Resend (HTML email report)
```

**Run modes** (`RUN_MODE` env var):
- `daily` — scrapes check-in = today, runs 4×/day (8AM, 12PM, 4PM, 8PM Madrid time)
- `weekly` — scrapes check-in = today+7, runs Sundays at 7AM CEST

The workflow auto-detects the mode by day/hour; `workflow_dispatch` lets you pick manually.

## Key scraping details

- Playwright launches headless Chromium with random user-agents, Spain locale/timezone, and webdriver fingerprint masking.
- Each hotel gets up to `RETRIES_PER_HOTEL = 2` attempts with 2–4s delay between retries.
- Room filtering: prefers rooms matching `DOUBLE_KW` keywords and excluding `EXCLUDE_KW` (triple, family, suite, etc.). Falls back to cheapest available room if no doubles found.
- Proxy: if `WEBSHARE_API_KEY` is set, fetches a random proxy from the Webshare list on each run. Falls back to `PROXY_URL` env var, or runs without proxy.
- **CSS selectors for Booking.com break periodically** — this is the most common maintenance task. The selectors are in `scrape_price()`.

## Error handling logic

- `confidence_check()`: if >2 competitor hotels fail with technical errors (not SOLD_OUT/MIN_STAY), the run is aborted and only `ALERT_EMAIL` is notified — no report sent.
- If ≥`ERROR_THRESHOLD` (3) hotels fail but confidence check passes, both the report and an error alert are sent.
- SOLD_OUT and MIN_STAY are treated as valid business statuses, not scraping failures.

## Supabase schema

Table: `price_snapshots` — one row per hotel per run. Columns: `hotel_id`, `name`, `is_mine`, `price`, `scraped_at`, `checkin_date`, `run_mode`.

View: `latest_prices` — deduplicated to most recent scrape per `(hotel_id, checkin_date)`.

Schema lives in `supabase_schema.sql` — run it once in the Supabase SQL editor to initialize.

## Hotels

2 owned hotels (`is_mine: True`): Abadía Hotel, Abadia Suites.  
5 competitors (`is_mine: False`): Dauro 2, Macià Cóndor, Granada by Pierre & Vacances, Granada Centro, Carlos V.

To add/change hotels, edit the `HOTELS` list in `scraper/scraper.py`.
