"""
Hotel Price Scraper — Granada, Spain
Scrapes Booking.com for cheapest double room prices for 7 hotels,
stores results in Supabase, and sends formatted HTML email reports.
"""

import os
import re
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
    return datetime.now()

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

# --- Room Filtering (from old scraper) ---
DOUBLE_KW = ['double', 'twin', 'queen', 'king', 'standard', 'superior', 'classic', 'deluxe']
EXCLUDE_KW = ['triple', 'family', 'suite', 'single', 'quadruple']

# Max competitor failures before aborting and alerting
MAX_COMP_FAILURES = 2 
RETRIES_PER_HOTEL = 2

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
]

# ─── Scraping ─────────────────────────────────────────────────────────────────

# --- Webshare API Support ---
def get_webshare_proxy():
    """Fetch a random proxy from the Webshare proxy list."""
    api_key = os.environ.get("WEBSHARE_API_KEY")
    if not api_key:
        return os.environ.get("PROXY_URL")
    
    api_key = api_key.strip()
    try:
        import urllib.request
        import json
        import random
        # Fetch up to 50 proxies from your list
        req = urllib.request.Request(
            "https://proxy.webshare.io/api/v2/proxy/list/?mode=direct&page=1&page_size=50",
            headers={"Authorization": f"Token {api_key}"}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())
            if data.get("results"):
                # Pick a random proxy so retries use different IPs
                proxy = random.choice(data["results"])
                user = proxy["username"]
                pw = proxy["password"]
                addr = proxy["proxy_address"]
                port = proxy["port"]
                print(f"  [Proxy Check] Successfully fetched direct proxy: {addr}:{port}")
                return f"http://{user}:{pw}@{addr}:{port}"
            return os.environ.get("PROXY_URL")
    except Exception as e:
        import urllib.error
        if isinstance(e, urllib.error.HTTPError):
            print(f"  [!] Failed to fetch Webshare proxy list: {e.code} - {e.read().decode()}")
        else:
            print(f"  [!] Failed to fetch Webshare proxy list: {e}")
        return os.environ.get("PROXY_URL")


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
    """Scrape price and return a status dict with room name."""
    url = build_booking_url(hotel["booking_url"], checkin)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        # Accept cookie consent if present
        try:
            accept = await page.query_selector("button#onetrust-accept-btn-handler")
            if accept:
                await accept.click()
                await page.wait_for_timeout(1000)
        except Exception:
            pass

        # Click the prices/availability tab if present
        tabs = await page.query_selector_all("a")
        for tab in tabs:
            try:
                text = (await tab.inner_text()).strip().lower()
                if text in ["prices", "info & prices", "availability"]:
                    await tab.click()
                    await page.wait_for_timeout(2000)
                    break
            except Exception:
                pass

        # Scroll to trigger lazy-loaded room table
        await page.evaluate("window.scrollBy(0, 1500)")
        await page.wait_for_timeout(1000)

        # Check for sold out / no availability
        for selector in ['[data-testid="availability-messages-container"]', '.hp_no_availability_msg', '.availability-advisory', '.bui-alert--error']:
            el = await page.query_selector(selector)
            if el:
                text = (await el.inner_text()).lower()
                if "no availability" in text or "sold out" in text or "not available" in text:
                    return {"price": None, "status": "SOLD_OUT", "room": "Sold out"}
                if "minimum stay" in text or ("stay" in text and "nights" in text):
                    return {"price": None, "status": "MIN_STAY", "room": "Min stay required"}

        # Extract room/price pairs — try proven selectors first, then modern layout
        all_rooms = []

        rows = await page.query_selector_all("tr.js-rt-block-row, .hprt-table tr")
        for row in rows:
            room_el = await row.query_selector(".hprt-roomtype-icon-link")
            price_el = await row.query_selector(".bui-price-display__value, .prco-valign-middle-helper")
            if room_el and price_el:
                room_text = (await room_el.inner_text()).strip()
                price_text = (await price_el.inner_text()).strip()
                nums = re.findall(r'\d+', price_text.replace('.', '').replace(',', ''))
                if nums:
                    val = float(nums[-1])
                    if 10 < val < 10000:
                        all_rooms.append({"room": room_text, "price": val})

        # Fall back to modern data-testid layout if classic table empty
        if not all_rooms:
            blocks = await page.query_selector_all('[data-testid="recommended-units"]')
            for block in blocks:
                room_el = await block.query_selector('[data-testid="room-name"]')
                price_el = await block.query_selector('[data-testid="price-and-discounted-price"]')
                if room_el and price_el:
                    room_text = (await room_el.inner_text()).strip()
                    price_text = (await price_el.inner_text()).strip()
                    clean = price_text.replace("€", "").replace(",", "").replace("\xa0", "").replace(" ", "")
                    nums = re.findall(r'\d+', clean)
                    if nums:
                        val = float(nums[-1])
                        if 10 < val < 10000:
                            all_rooms.append({"room": room_text, "price": val})

        if not all_rooms:
            return {"price": None, "status": "NO_PRICE_FOUND", "room": None}

        doubles = [r for r in all_rooms
                   if any(k in r['room'].lower() for k in DOUBLE_KW)
                   and not any(k in r['room'].lower() for k in EXCLUDE_KW)]

        cheapest = min(doubles if doubles else all_rooms, key=lambda x: x['price'])
        return {"price": cheapest["price"], "status": "SUCCESS", "room": cheapest["room"]}

    except Exception as e:
        print(f"  [!] Error scraping {hotel['name']}: {e}")
        return {"price": None, "status": "ERROR", "room": None}


async def scrape_all_hotels(checkin: datetime) -> list[dict]:
    """Scrape all hotels and return results."""
    results = []
    
    async with async_playwright() as p:
        proxy_url = get_webshare_proxy()
        if proxy_url:
            print(f"  [Proxy] Using proxy configuration (source: {'WEBSHARE_API' if os.environ.get('WEBSHARE_API_KEY') else 'PROXY_URL'})")
        
        browser = await p.chromium.launch(
            headless=True,
            proxy={"server": proxy_url} if proxy_url else None
        )

        # Single shared context so cookies/session accumulate across hotels,
        # making the traffic pattern look like one user browsing hotel to hotel.
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            timezone_id="Europe/Madrid",
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )
        page = await context.new_page()

        for hotel in HOTELS:
            print(f"  Scraping: {hotel['name']}...")

            scraping_result = {"price": None, "status": "FAILED", "room": None}

            for attempt in range(RETRIES_PER_HOTEL):
                if attempt > 0:
                    print(f"    [retry] Attempt {attempt + 1} for {hotel['name']}...")
                    await asyncio.sleep(random.uniform(3, 5))

                scraping_result = await scrape_price(page, hotel, checkin)

                if scraping_result["status"] in ["SUCCESS", "SOLD_OUT", "MIN_STAY"]:
                    break

            results.append({
                "hotel_id": hotel["id"],
                "name": hotel["name"],
                "is_mine": hotel["is_mine"],
                "price": scraping_result["price"],
                "status": scraping_result["status"],
                "room_name": scraping_result["room"],
                "scraped_at": datetime.now(timezone.utc).isoformat(),
                "checkin_date": checkin.strftime("%Y-%m-%d"),
                "run_mode": RUN_MODE,
            })

            await asyncio.sleep(random.uniform(2, 4))

        await context.close()
        await browser.close()
    
    return results

# ─── Supabase storage ─────────────────────────────────────────────────────────

def save_results(results: list[dict]) -> None:
    """Save scraped prices to Supabase."""
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    schema_keys = {"hotel_id", "name", "is_mine", "price", "scraped_at", "checkin_date", "run_mode"}
    for row in results:
        if row["price"] is not None:
            supabase.table("price_snapshots").insert({k: v for k, v in row.items() if k in schema_keys}).execute()
    print(f"  Saved {sum(1 for r in results if r['price'] is not None)} prices to Supabase")

# ─── Reliability (from old scraper) ───────────────────────────────────────────

def confidence_check(results: list[dict]) -> tuple[bool, str]:
    """Check if the scrape results are reliable."""
    competitors = [r for r in results if not r["is_mine"]]
    # Only count technical errors as failures, not "Sold Out" or "Min Stay"
    failures = [r for r in competitors if r["status"] in ["ERROR", "TIMEOUT", "FAILED"]]
    
    if len(failures) > MAX_COMP_FAILURES:
        return False, f"{len(failures)} competitor hotels had scraping errors"
    return True, "ok"


# ─── Email building ───────────────────────────────────────────────────────────

def compute_median_badge(my_price: Optional[float], competitor_prices: list[float]) -> str:
    """Return a vs-median badge string like '+€12 vs median'."""
    if my_price is None or not competitor_prices:
        return ""
    median = statistics.median(competitor_prices)
    if median == 0:
        return ""
    diff = my_price - median
    sign = "+" if diff >= 0 else "-"
    return f"{sign}€{abs(diff):.0f} vs median"


def build_html_email(results: list[dict], checkin: datetime) -> str:
    """Build the full HTML email report."""
    MADRID_TZ = timezone(timedelta(hours=2))
    timestamp = datetime.now(MADRID_TZ).strftime("%d %b %Y, %H:%M")
    checkin_str = checkin.strftime("%A %d %B %Y")

    my_results = [r for r in results if r["is_mine"]]
    competitor_results = [r for r in results if not r["is_mine"]]
    competitor_prices = [r["price"] for r in competitor_results if r["price"] is not None]
    median_price = statistics.median(competitor_prices) if competitor_prices else None
    min_price = min(competitor_prices) if competitor_prices else None
    max_price = max(competitor_prices) if competitor_prices else None

    def price_cell(row):
        if row["price"] is not None:
            return f'<td style="padding:10px 14px;font-weight:600;font-size:15px">€{row["price"]:.0f}</td>'
        status_colors = {
            "SOLD_OUT": "#c0392b",
            "MIN_STAY": "#2980b9",
            "TIMEOUT": "#7f8c8d",
            "NO_PRICE_FOUND": "#7f8c8d",
        }
        color = status_colors.get(row["status"], "#999")
        label = row["status"].replace("_", " ") if row["status"] else "N/A"
        return f'<td style="padding:10px 14px;color:{color};font-size:11px;font-weight:600;text-transform:uppercase">{label}</td>'

    def badge_html(price):
        if price is None or not competitor_prices:
            return ""
        median = statistics.median(competitor_prices)
        diff = price - median
        color = "#c0392b" if diff > 5 else "#27ae60" if diff < -5 else "#e67e22"
        label = compute_median_badge(price, competitor_prices)
        return f'<span style="background:{color};color:#fff;font-size:11px;padding:2px 8px;border-radius:10px;margin-left:8px;font-weight:600">{label}</span>'

    def build_section(hotel_list):
        html = ""
        for r in sorted(hotel_list, key=lambda x: x["price"] or 9999):
            room_label = f'<div style="font-size:11px;color:#888;margin-top:2px">{r["room_name"]}</div>' if r.get("room_name") else ""
            vs = badge_html(r["price"]) if r["is_mine"] else ""
            html += f"""
        <tr style="border-bottom:1px solid #f0f0f0">
          <td style="padding:10px 14px">
            <div style="font-weight:500">{r['name']}{vs}</div>
            {room_label}
          </td>
          {price_cell(r)}
        </tr>"""
        return html

    my_rows = build_section(my_results)
    comp_rows = build_section(competitor_results)

    scrape_errors = [r["name"] for r in results if r["status"] in ("ERROR", "TIMEOUT", "FAILED", "NO_PRICE_FOUND")]
    failure_section = ""
    if scrape_errors:
        failure_section = f"""
      <p style="color:#c0392b;font-size:12px;margin:12px 24px 0">
        ⚠️ Scrape errors (check logs): {", ".join(scrape_errors)}
      </p>"""

    if median_price is not None:
        range_str = f"€{min_price:.0f} – €{max_price:.0f}" if min_price != max_price else f"€{median_price:.0f}"
        market_row = f"""
      <div style="padding:12px 24px;background:#f8f9fa;border-top:1px solid #eee;font-size:12px;color:#666">
        Competitor range: <strong>{range_str}</strong> &nbsp;·&nbsp; Median: <strong>€{median_price:.0f}</strong>
      </div>"""
    else:
        market_row = ""

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

    <div style="padding:8px 24px 4px;font-size:10px;color:#aaa;text-transform:uppercase;letter-spacing:0.8px">Your Properties</div>
    <table style="width:100%;border-collapse:collapse;font-size:14px;background:#fef9e7">
      <tbody>{my_rows}</tbody>
    </table>

    <div style="padding:12px 24px 4px;font-size:10px;color:#aaa;text-transform:uppercase;letter-spacing:0.8px;border-top:2px solid #ecf0f1;margin-top:4px">Competitors</div>
    <table style="width:100%;border-collapse:collapse;font-size:14px">
      <tbody>{comp_rows}</tbody>
    </table>

    {market_row}
    {failure_section}

    <div style="padding:12px 24px;font-size:11px;color:#bbb;border-top:1px solid #eee;margin-top:4px">
      Generated at {timestamp} (Madrid) · Hotel Price Monitor
    </div>
  </div>
</body>
</html>"""


def build_error_email(results: list[dict], checkin: datetime) -> str:
    """Build a plain error alert email."""
    failures = [r["name"] for r in results if r["price"] is None]
    MADRID_TZ = timezone(timedelta(hours=2))
    timestamp = datetime.now(MADRID_TZ).strftime("%d %b %Y, %H:%M")
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

    # Confidence check (from old scraper)
    ok, reason = confidence_check(results)
    if not ok:
        print(f"\n❌ CONFIDENCE CHECK FAILED: {reason}")
        print("   Skipping report and sending alert to Josh.")
        send_error_alert(results, checkin)
        return

    print("Sending email...")
    if len(successes) > 0:
        send_report(results, checkin)
    
    if len(failures) >= ERROR_THRESHOLD:
        send_error_alert(results, checkin)

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
