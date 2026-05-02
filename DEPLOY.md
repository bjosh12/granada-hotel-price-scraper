# Deploy in 3 steps

## Step 1 — Push this code to GitHub

```bash
cd granada-hotel-price-scraper
git init
git remote add origin https://github.com/bjosh12/granada-hotel-price-scraper.git
git checkout -b main
git add .
git commit -m "Initial setup: hotel price scraper"
git push -u origin main
```

## Step 2 — Run Supabase schema (one time)

1. Go to https://supabase.com/dashboard/project/oaohsencjgbzrdmlxrkg/sql/new
2. Paste and run the contents of `supabase_schema.sql`

## Step 3 — Add GitHub secrets

**Option A (easiest): GitHub web UI**

Go to: https://github.com/bjosh12/granada-hotel-price-scraper/settings/secrets/actions

Add these 6 secrets one by one:

| Name | Value |
|---|---|
| `SUPABASE_URL` | `https://oaohsencjgbzrdmlxrkg.supabase.co` |
| `SUPABASE_KEY` | `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...` (the full anon key) |
| `RESEND_API_KEY` | `re_a4hkL4vH_236vUwXzEDuKcvdsT4Bjtczd` |
| `FROM_EMAIL` | `joshua@lingologic.es` |
| `EMAIL_1` | `joshua.aguirre.dst@gmail.com` |
| `ALERT_EMAIL` | `joshua.aguirre.dst@gmail.com` |

**Option B: GitHub CLI (if you have `gh` installed)**

```bash
bash setup_github_secrets.sh
```

## Step 4 — Test it

Go to: https://github.com/bjosh12/granada-hotel-price-scraper/actions
Click "Hotel Price Scraper" → "Run workflow" → "Run workflow"

You should receive an email at joshua.aguirre.dst@gmail.com within ~3 minutes.
