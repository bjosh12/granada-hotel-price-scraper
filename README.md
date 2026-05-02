# Hotel Price Scraper — Granada

Scrapes Booking.com for cheapest double room prices across 7 hotels (2 yours + 5 competitors),
runs on a schedule via GitHub Actions, and emails formatted HTML reports.

## Architecture

```
GitHub Actions (cron) → Python + Playwright → Booking.com
                                             ↓
                                       Supabase (storage)
                                             ↓
                                    Resend (email reports)
```

**Cost: $0/month** on free tiers of all services.

---

## Setup (30–45 minutes)

### 1. Fork / clone this repo

```bash
git clone <your-repo-url>
cd hotel-scraper
```

### 2. Set up Supabase

1. Create a free account at [supabase.com](https://supabase.com)
2. Create a new project (choose any region)
3. Go to **SQL Editor** and run the contents of `supabase_schema.sql`
4. Go to **Settings → API** and copy:
   - **Project URL** → `SUPABASE_URL`
   - **anon / public key** → `SUPABASE_KEY`

### 3. Set up Resend

1. Create a free account at [resend.com](https://resend.com)
2. Add and verify your domain (or use the sandbox `@resend.dev` address for testing)
3. Go to **API Keys** → create a new key → copy it as `RESEND_API_KEY`
4. Set `FROM_EMAIL` to an address from your verified domain (e.g. `prices@yourdomain.com`)

### 4. Configure your hotels

Edit `scraper/scraper.py`:

```python
HOTELS = [
    {
        "id": "my_hotel_1",
        "name": "My Hotel Granada",
        "booking_url": "https://www.booking.com/hotel/es/YOUR-HOTEL-SLUG.html",
        "is_mine": True,
    },
    # ... etc
]
```

**Finding your Booking.com URL slug:**
Go to your hotel's page on Booking.com. The URL will look like:
`https://www.booking.com/hotel/es/hotel-name-slug.html`
Copy everything up to and including `.html` (without any query params).

### 5. Add GitHub Secrets

In your GitHub repo: **Settings → Secrets and variables → Actions → New repository secret**

Add all of these:

| Secret name     | Value                                      |
|-----------------|--------------------------------------------|
| `SUPABASE_URL`  | Your Supabase project URL                  |
| `SUPABASE_KEY`  | Your Supabase anon key                     |
| `RESEND_API_KEY`| Your Resend API key                        |
| `FROM_EMAIL`    | Sender address (e.g. prices@yourdomain.com)|
| `EMAIL_1`       | First report recipient email               |
| `EMAIL_2`       | Second report recipient email              |
| `EMAIL_3`       | Third report recipient email               |
| `ALERT_EMAIL`   | Your email (for error-only alerts)         |

### 6. Test manually

1. Push your code to GitHub
2. Go to **Actions → Hotel Price Scraper → Run workflow**
3. Select `daily` and click "Run workflow"
4. Watch the logs — you should see prices scraped and an email arrive

### 7. Adjust the cron schedule (timezone)

The cron times in `.github/workflows/scrape.yml` are in **UTC**. Granada is:
- **CET (winter, Nov–Mar):** UTC+1 → subtract 1 hour from your local time
- **CEST (summer, Apr–Oct):** UTC+2 → subtract 2 hours

The defaults in the workflow are set for CEST (summer). If running in winter, add 1 hour to each cron UTC value.

---

## How scraping works

Playwright launches a headless Chromium browser with:
- Random user-agent rotation (5 different UA strings)
- Human-like delays between hotels (4–8 seconds)
- Timezone and locale set to Spain
- Webdriver fingerprint masked

The scraper tries multiple CSS selectors since Booking.com's structure changes. If none match, it logs a failure and continues.

## Error handling

- If **≥3 hotels fail** to scrape, an error alert is sent to `ALERT_EMAIL` only
- If any hotels succeed, the report is always sent (with "N/A" for failed ones)
- GitHub Actions logs are retained for 7 days on failure

## Viewing stored data

Log into [supabase.com](https://supabase.com) → your project → **Table Editor → price_snapshots**

Or use the SQL editor with the example queries in `supabase_schema.sql`.

---

## Notes on Booking.com scraping

Booking.com actively fights scrapers. Expect occasional failures — a 15–20% failure rate is normal. To improve reliability:
- The scraper already uses random UAs and delays
- If failures increase dramatically, the CSS selectors in `scraper.py` may need updating
- Do NOT increase scraping frequency — it increases ban risk
- Consider using a residential proxy service (Bright Data, Oxylabs) if you need higher reliability — adds ~$10–20/month
