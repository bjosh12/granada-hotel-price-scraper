"""
Hotel Price Scraper — Granada, Spain
Scrapes Booking.com for cheapest double room prices for 7 hotels,
stores results in Supabase, and sends formatted HTML email reports.
"""

import os
import json
import asyncio
import statistics
from datetime import datetime, timedelta, timezone
from typing import Optional
import random
import time

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from supabase import create_client, Client
import resend

# ─── Configuration ────────────────────────────────────────────────────────────

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
RESEND_API_KEY = os.environ["RESEND_API_KEY"]
FROM_EMAIL = os.environ.get("FROM_EMAIL", "prices@yourdomain.com")

REPORT_RECIPIENTS = [
    os.environ["EMAIL_1"],
]
ALERT_RECIPIENT = os.environ["ALERT_EMAIL"]  # You only, for error alerts

# Number of failures before sending an error alert
ERROR_THRESHOLD = 3

# Run mode: "daily" (4x/day report) or "weekly" (next-week preview)
RUN_MODE = os.environ.get("RUN_MODE", "daily")

# Check-in date: today+1 for daily, today+7 for weekly preview
def get_checkin_date() -> datetime:
    if RUN_MODE == "weekly":
        return datetime.now() + timedelta(days=7)
    return datetime.now() + timedelta(days=1)

# ─── Hotel definitions ────────────────────────────────────────────────────────

HOTELS = [
    {
        "id": "abadia_hotel",
        "name": "Abadía Hotel",
        "booking_url": "https://www.booking.com/hotel/es/abadia.html",
        "is_mine": True,
    },
    {
        "id": "abadia_suites",
        "name": "Abadia Suites",
        "booking_url": "https://www.booking.com/hotel/es/vivienda-turistica-vacacional-abadia.html",
        "is_mine": True,
    },
    # ── Competitors ──────────────────────────────────────────────────────────
    {
        "id": "competitor_1",
        "name": "Hotel Comfort Dauro 2",
        "booking_url": "https://www.booking.com/hotel/es/dauroii.html",
        "is_mine": False,
    },
    {
        "id": "competitor_2",
        "name": "Hotel Macià Cóndor",
        "booking_url": "https://www.booking.com/hotel/es/maciacondor.html",
        "is_mine": False,
    },
    {
        "id": "competitor_3",
        "name": "Hotel Granada by Pierre & Vacances",
        "booking_url": "https://www.booking.com/hotel/es/granadabypierrevacances.html",
        "is_mine": False,
    },
    {
        "id": "competitor_4",
        "name": "Hotel Granada Centro",
        "booking_url": "https://www.booking.com/hotel/es/granada-centro.html",
        "is_mine": False,
    },
    {
        "id": "competitor_5",
        "name": "Hotel Carlos V",
        "booking_url": "https://www.booking.com/hotel/es/carlosvgranada.html",
        "is_mine": False,
    },
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
]

# ─── Scraping ─────────────────────────────────────────────────────────────────

def build_booking_url(base_url: str, checkin: datetime) -> str:
    """Append date + room params to a Booking.com hotel URL."""
    checkout = checkin + timedelta(days=1)
    checkin_str = checkin.strftime("%Y-%m-%d")
    checkout_str = checkout.strftime("%Y-%m-%d")
    sep = "&" if "?" in base_url else "?"
    return (
        f"{base_url}{sep}"
        f"checkin={checkin_str}&checkout={checkout_str}"
        f"&group_adults=2&no_rooms=1&selected_currency=EUR"
    )


async def scrape_price(page, hotel: dict, checkin: datetime) -> dict:
    """Scrape price and return a status dict."""
    url = build_booking_url(hotel["booking_url"], checkin)
    
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        
        # Wait for price OR availability messages
        try:
            await page.wait_for_selector(
                '[data-testid="price-and-discounted-price"], .prco-valign-middle-helper, [data-testid="availability-messages-container"], .hp_no_availability_msg, .availability-advisory', 
                timeout=15000
            )
        except PlaywrightTimeout:
            return {"price": None, "status": "TIMEOUT"}

        # Check for Sold Out or Minimum Stay messages
        avail_msg_selectors = [
            '[data-testid="availability-messages-container"]',
            '.hp_no_availability_msg',
            '.availability-advisory',
            '.bui-alert--error'
        ]
        
        for selector in avail_msg_selectors:
            el = await page.query_selector(selector)
            if el:
                text = (await el.inner_text()).lower()
                if "no availability" in text or "sold out" in text or "not available" in text:
                    return {"price": None, "status": "SOLD_OUT"}
                if "minimum stay" in text or "stay" in text and "nights" in text:
                    return {"price": None, "status": "MIN_STAY"}

        # Try multiple selectors Booking.com uses for prices
        price_selectors = [
            '[data-testid="price-and-discounted-price"]',
            '.prco-valign-middle-helper',
            '[data-testid="recommended-units"] .bui-price-display__value',
            '.hprt-price-policy .bui-price-display__value',
        ]

        prices = []
        for selector in price_selectors:
            elements = await page.query_selector_all(selector)
            for el in elements:
                text = await el.inner_text()
                clean = text.replace("€", "").replace(",", "").replace("\xa0", "").replace(" ", "").strip()
                try:
                    val = float(clean)
                    if 10 < val < 10000:
                        prices.append(val)
                except ValueError:
                    pass

        if prices:
            return {"price": min(prices), "status": "SUCCESS"}
        
        return {"price": None, "status": "NO_PRICE_FOUND"}

    except Exception as e:
        print(f"  [!] Error scraping {hotel['name']}: {e}")
        return {"price": None, "status": "ERROR"}


async def scrape_all_hotels(checkin: datetime) -> list[dict]:
    """Scrape all hotels and return results."""
    results = []
    
    async with async_playwright() as p:
        proxy_url = os.environ.get("PROXY_URL")
        browser = await p.chromium.launch(
            headless=True,
            proxy={"server": proxy_url} if proxy_url else None
        )
        
        for hotel in HOTELS:
            print(f"  Scraping: {hotel['name']}...")
            
            context = await browser.new_context(
                user_agent=random.choice(USER_AGENTS),
                viewport={"width": 1366, "height": 768},
                locale="en-GB",
                timezone_id="Europe/Madrid",
            )
            
            # Mask automation fingerprints
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            """)
            
            page = await context.new_page()
            scraping_result = await scrape_price(page, hotel, checkin)
            await context.close()
            
            results.append({
                "hotel_id": hotel["id"],
                "name": hotel["name"],
                "is_mine": hotel["is_mine"],
                "price": scraping_result["price"],
                "status": scraping_result["status"],
                "scraped_at": datetime.now(timezone.utc).isoformat(),
                "checkin_date": checkin.strftime("%Y-%m-%d"),
                "run_mode": RUN_MODE,
            })
            
            # Polite delay between hotels
            await asyncio.sleep(random.uniform(4, 8))
        
        await browser.close()
    
    return results

# ─── Supabase storage ─────────────────────────────────────────────────────────

def save_results(results: list[dict]) -> None:
    """Save scraped prices to Supabase."""
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    for row in results:
        if row["price"] is not None:
            supabase.table("price_snapshots").insert(row).execute()
    print(f"  Saved {sum(1 for r in results if r['price'] is not None)} prices to Supabase")

# ─── Email building ───────────────────────────────────────────────────────────

def compute_median_badge(my_price: Optional[float], competitor_prices: list[float]) -> str:
    """Return a vs-median badge string like '+12%' or '-8%'."""
    if my_price is None or not competitor_prices:
        return ""
    median = statistics.median(competitor_prices)
    if median == 0:
        return ""
    diff_pct = ((my_price - median) / median) * 100
    sign = "+" if diff_pct >= 0 else ""
    return f"{sign}{diff_pct:.0f}% vs median"


def build_html_email(results: list[dict], checkin: datetime) -> str:
    """Build the full HTML email report."""
    timestamp = datetime.now().strftime("%d %b %Y, %H:%M")
    checkin_str = checkin.strftime("%A %d %B %Y")
    
    my_results = [r for r in results if r["is_mine"]]
    competitor_results = [r for r in results if not r["is_mine"]]
    competitor_prices = [r["price"] for r in competitor_results if r["price"] is not None]
    median_price = statistics.median(competitor_prices) if competitor_prices else None

    # Build rows HTML
    def price_cell(row):
        if row["price"] is not None:
            return f'<td style="font-weight:600">€{row["price"]:.0f}</td>'
        
        status_colors = {
            "SOLD_OUT": "#c0392b",
            "MIN_STAY": "#2980b9",
            "TIMEOUT": "#7f8c8d",
            "NO_PRICE_FOUND": "#7f8c8d"
        }
        color = status_colors.get(row["status"], "#999")
        label = row["status"].replace("_", " ") if row["status"] else "N/A"
        return f'<td style="color:{color};font-size:11px;font-weight:600;text-transform:uppercase">{label}</td>'

    def badge_html(price, is_mine):
        if not is_mine or price is None or not competitor_prices:
            return ""
        median = statistics.median(competitor_prices)
        diff_pct = ((price - median) / median) * 100
        color = "#c0392b" if diff_pct > 5 else "#27ae60" if diff_pct < -5 else "#e67e22"
        label = compute_median_badge(price, competitor_prices)
        return f'<span style="background:{color};color:#fff;font-size:11px;padding:2px 7px;border-radius:10px;margin-left:8px;font-weight:600">{label}</span>'

    rows = ""
    for r in sorted(results, key=lambda x: (not x["is_mine"], x["price"] or 9999)):
        bg = "#fef9e7" if r["is_mine"] else "#ffffff"
        border = "2px solid #f39c12" if r["is_mine"] else "1px solid #eee"
        mine_label = ' <span style="font-size:10px;background:#f39c12;color:#fff;padding:1px 5px;border-radius:4px;vertical-align:middle">MINE</span>' if r["is_mine"] else ""
        rows += f"""
        <tr style="background:{bg};border-left:{border}">
          <td style="padding:10px 14px">{r['name']}{mine_label}{badge_html(r['price'], r['is_mine'])}</td>
          {price_cell(r)}
        </tr>"""

    failures = [r["name"] for r in results if r["price"] is None]
    failure_section = ""
    if failures:
        failure_section = f"""
        <p style="color:#c0392b;font-size:13px;margin-top:16px">
          ⚠️ Could not scrape: {", ".join(failures)}
        </p>"""

    median_row = f"<p style='color:#666;font-size:13px'>Competitor median: <strong>€{median_price:.0f}</strong></p>" if median_price else ""

    mode_label = "Weekly Preview" if RUN_MODE == "weekly" else "Daily Report"

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f4f6f9;margin:0;padding:20px">
  <div style="max-width:560px;margin:0 auto;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08)">
    
    <div style="background:#2c3e50;padding:20px 24px;color:#fff">
      <div style="font-size:11px;letter-spacing:1px;text-transform:uppercase;opacity:0.6">Granada Hotels · {mode_label}</div>
      <div style="font-size:20px;font-weight:600;margin-top:4px">Price Report</div>
      <div style="font-size:13px;opacity:0.75;margin-top:4px">Check-in: {checkin_str}</div>
    </div>

    <div style="padding:20px 24px">
      <table style="width:100%;border-collapse:collapse;font-size:14px">
        <thead>
          <tr style="border-bottom:2px solid #ecf0f1">
            <th style="text-align:left;padding:8px 14px;color:#666;font-weight:500">Hotel</th>
            <th style="text-align:left;padding:8px 14px;color:#666;font-weight:500">Cheapest double</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
      
      {median_row}
      {failure_section}
    </div>

    <div style="background:#f8f9fa;padding:12px 24px;font-size:12px;color:#aaa;border-top:1px solid #eee">
      Generated at {timestamp} UTC · Hotel Price Monitor
    </div>
  </div>
</body>
</html>"""


def build_error_email(results: list[dict], checkin: datetime) -> str:
    """Build a plain error alert email."""
    failures = [r["name"] for r in results if r["price"] is None]
    timestamp = datetime.now().strftime("%d %b %Y, %H:%M")
    items = "".join(f"<li>{f}</li>" for f in failures)
    return f"""<p>⚠️ <strong>{len(failures)} scrapes failed</strong> at {timestamp} UTC for check-in {checkin.strftime('%Y-%m-%d')}.</p>
<ul>{items}</ul>
<p>Check GitHub Actions logs for details.</p>"""

# ─── Email sending ────────────────────────────────────────────────────────────

def send_report(results: list[dict], checkin: datetime) -> None:
    resend.api_key = RESEND_API_KEY
    mode_label = "Weekly Preview" if RUN_MODE == "weekly" else "Daily Report"
    subject = f"Hotel Prices Granada — {checkin.strftime('%d %b')} ({mode_label})"
    html = build_html_email(results, checkin)
    
    resend.Emails.send({
        "from": FROM_EMAIL,
        "to": REPORT_RECIPIENTS,
        "subject": subject,
        "html": html,
    })
    print(f"  Report sent to {len(REPORT_RECIPIENTS)} recipients")


def send_error_alert(results: list[dict], checkin: datetime) -> None:
    resend.api_key = RESEND_API_KEY
    failures = [r for r in results if r["price"] is None]
    resend.Emails.send({
        "from": FROM_EMAIL,
        "to": [ALERT_RECIPIENT],
        "subject": f"⚠️ Scraper alert: {len(failures)} failures",
        "html": build_error_email(results, checkin),
    })
    print(f"  Error alert sent to {ALERT_RECIPIENT}")

# ─── Main ─────────────────────────────────────────────────────────────────────

async def main():
    checkin = get_checkin_date()
    print(f"\n{'='*50}")
    print(f"Hotel Price Scraper — {RUN_MODE.upper()} mode")
    print(f"Check-in date: {checkin.strftime('%Y-%m-%d')}")
    print(f"{'='*50}\n")

    print("Scraping prices...")
    results = await scrape_all_hotels(checkin)

    successes = [r for r in results if r["price"] is not None]
    failures = [r for r in results if r["price"] is None]

    print(f"\nResults: {len(successes)} succeeded, {len(failures)} failed")
    for r in results:
        status_label = f"€{r['price']:.0f}" if r["price"] else r["status"]
        mine = " ★" if r["is_mine"] else ""
        print(f"  {r['name']}{mine}: {status_label}")

    print("\nSaving to Supabase...")
    save_results(results)

    print("Sending email...")
    if len(successes) > 0:
        send_report(results, checkin)
    
    if len(failures) >= ERROR_THRESHOLD:
        send_error_alert(results, checkin)

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
