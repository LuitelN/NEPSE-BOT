"""
NEPSE IPO Alert Bot
-------------------
Scrapes https://nepalipaisa.com/ipo using a headless browser (Playwright),
compares results against a local JSON cache, and sends an email alert
for any newly opened IPOs.

Run via GitHub Actions on a cron schedule (daily at 11 AM Nepal Time = 05:15 UTC).
"""

import json
import os
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout


# Config — all secrets come from GitHub Actions environment variables

EMAIL_SENDER   = os.environ["EMAIL_SENDER"]    # e.g. yourbot@gmail.com
EMAIL_PASSWORD = os.environ["EMAIL_PASSWORD"]  # Gmail App Password
EMAIL_RECEIVER = os.environ["EMAIL_RECEIVER"]  # where alerts go
SMTP_HOST      = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT      = int(os.environ.get("SMTP_PORT", "465"))

IPO_URL        = "https://nepalipaisa.com/ipo"
CACHE_FILE     = Path("seen_ipos.json")        # persisted via GitHub Actions cache



# Cache helpers

def load_cache() -> set:
    """Return a set of IPO identifiers we have already alerted on."""
    if CACHE_FILE.exists():
        try:
            data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            return set(data.get("seen", []))
        except (json.JSONDecodeError, KeyError):
            pass
    return set()


def save_cache(seen: set) -> None:
    CACHE_FILE.write_text(
        json.dumps({"seen": sorted(seen)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )



# Scraper

def scrape_ipos() -> list[dict]:
    """
    Launch a headless Chromium browser, load the IPO page, wait for the
    table to appear, and extract all visible IPO rows.

    Returns a list of dicts, each with keys:
        company, units, price, open_date, close_date, status
    """
    ipos = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id="Asia/Kathmandu",
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        print(f"[scraper] Loading {IPO_URL} ...")
        try:
            page.goto(IPO_URL, wait_until="domcontentloaded", timeout=60_000)
        except PlaywrightTimeout:
            print("[scraper] Page load timed out — site may be slow.")
            browser.close()
            return ipos

        # Wait for any table or list that contains IPO data
        try:
            page.wait_for_selector("table, .ipo-list, .ipo-table, [class*='ipo']", timeout=30_000)
        except PlaywrightTimeout:
            print("[scraper] IPO table selector not found. Dumping page text for debugging.")
            print(page.inner_text("body")[:2000])
            browser.close()
            return ipos

        
        # Parse table rows
        # The site renders a standard HTML table; adapt selectors if the
        # site is restructured.
        
        rows = page.query_selector_all("table tbody tr")
        print(f"[scraper] Found {len(rows)} table rows.")

        for row in rows:
            cells = [td.inner_text().strip() for td in row.query_selector_all("td")]
            if len(cells) < 5:
                continue  # skip header / empty rows

            # Column order observed on nepalipaisa.com/ipo (adjust if site changes):
            # 0: Company Name
            # 1: Units / Shares
            # 2: Price (Rs.)
            # 3: Open Date
            # 4: Close Date
            # 5: Status  (Open / Closed / Upcoming) — may or may not exist
            ipo = {
                "company":    cells[0] if len(cells) > 0 else "N/A",
                "units":      cells[1] if len(cells) > 1 else "N/A",
                "price":      cells[2] if len(cells) > 2 else "N/A",
                "open_date":  cells[3] if len(cells) > 3 else "N/A",
                "close_date": cells[4] if len(cells) > 4 else "N/A",
                "status":     cells[5] if len(cells) > 5 else "N/A",
            }
            ipos.append(ipo)

        browser.close()

    return ipos



# Filtering — only care about IPOs that are currently open

def is_open(ipo: dict) -> bool:
    """
    Return True if the IPO appears to be currently open for application.
    Checks the status column first; falls back to date comparison.
    """
    status = ipo.get("status", "").lower()
    if status in ("open", "opening", "active"):
        return True
    if status in ("closed", "upcoming", "future"):
        return False

    # No status column — try to infer from dates
    open_date  = ipo.get("open_date", "")
    close_date = ipo.get("close_date", "")
    try:
        # Dates on the site are usually in YYYY-MM-DD or DD-MM-YYYY
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
            try:
                od = datetime.strptime(open_date, fmt).date()
                cd = datetime.strptime(close_date, fmt).date()
                today = datetime.now().date()
                return od <= today <= cd
            except ValueError:
                continue
    except Exception:
        pass

    # When in doubt, include it (better a false positive than a miss)
    return True


def parse_ipo_date(value: str):
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def build_no_openings_html(today: str, recent_ipos: list[dict]) -> tuple[str, str]:
    subject = f"📢 No IPO Openings Today — {today}"

    rows_html = ""
    if recent_ipos:
        for ipo in recent_ipos:
            rows_html += f"""
        <tr>
          <td style="padding:10px 14px;border-bottom:1px solid #eee;font-weight:600;color:#1a1a2e;">
            {ipo['company']}
          </td>
          <td style="padding:10px 14px;border-bottom:1px solid #eee;text-align:center;">
            {ipo['open_date']}
          </td>
          <td style="padding:10px 14px;border-bottom:1px solid #eee;text-align:center;">
            {ipo['close_date']}
          </td>
        </tr>"""
    else:
        rows_html = "<tr><td colspan=\"3\" style=\"padding:14px;text-align:center;color:#555;\">No recent IPO openings found.</td></tr>"

    html = f"""
<!DOCTYPE html>
<html>
<head><meta charset=\"utf-8\"></head>
<body style=\"font-family:Arial,sans-serif;background:#f5f5f5;padding:24px;margin:0;\">
  <div style=\"max-width:640px;margin:0 auto;background:#fff;border-radius:10px;
              box-shadow:0 2px 8px rgba(0,0,0,.08);overflow:hidden;\">

    <div style=\"background:#2980b9;padding:24px 28px;\">
      <h1 style=\"margin:0;color:#fff;font-size:20px;\">📢 NEPSE IPO Update</h1>
      <p style=\"margin:6px 0 0;color:#d6eaf8;font-size:14px;\">
        No IPO Openings on {today}. Here are recent openings.
      </p>
    </div>

    <div style=\"padding:20px 28px;\">
      <p style=\"margin:0 0 18px;font-size:14px;color:#333;\">
        There are currently no IPOs open for application. Below are the latest recent IPOs with their open and close dates.
      </p>
      <table style=\"width:100%;border-collapse:collapse;font-size:14px;\">
        <thead>
          <tr style=\"background:#f8f9fa;\">
            <th style=\"padding:10px 14px;text-align:left;color:#555;font-weight:600;border-bottom:2px solid #dee2e6;\">Company</th>
            <th style=\"padding:10px 14px;text-align:center;color:#555;font-weight:600;border-bottom:2px solid #dee2e6;\">Opens</th>
            <th style=\"padding:10px 14px;text-align:center;color:#555;font-weight:600;border-bottom:2px solid #dee2e6;\">Closes</th>
          </tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>
    </div>

    <div style=\"background:#f8f9fa;padding:14px 28px;border-top:1px solid #eee;\">
      <p style=\"margin:0;font-size:12px;color:#888;\">
        Source: <a href=\"{IPO_URL}\" style=\"color:#2980b9;\">{IPO_URL}</a> • Checked at {datetime.now().strftime('%Y-%m-%d %H:%M')} NPT
      </p>
    </div>
  </div>
</body>
</html>"""

    return subject, html


def make_ipo_id(ipo: dict) -> str:
    """Stable unique ID for an IPO so we don't double-alert."""
    return f"{ipo['company']}|{ipo['open_date']}|{ipo['close_date']}"



# Email

def build_email_html(new_ipos: list[dict]) -> tuple[str, str]:
    """Return (subject, html_body) for the alert email."""
    count = len(new_ipos)
    subject = f"🔔 NEPSE IPO Alert — {count} New IPO{'s' if count > 1 else ''} Open Today"

    rows_html = ""
    for ipo in new_ipos:
        rows_html += f"""
        <tr>
          <td style="padding:10px 14px;border-bottom:1px solid #eee;font-weight:600;color:#1a1a2e;">
            {ipo['company']}
          </td>
          <td style="padding:10px 14px;border-bottom:1px solid #eee;text-align:right;">
            {ipo['units']}
          </td>
          <td style="padding:10px 14px;border-bottom:1px solid #eee;text-align:right;">
            Rs. {ipo['price']}
          </td>
          <td style="padding:10px 14px;border-bottom:1px solid #eee;text-align:center;">
            {ipo['open_date']}
          </td>
          <td style="padding:10px 14px;border-bottom:1px solid #eee;text-align:center;">
            {ipo['close_date']}
          </td>
        </tr>"""

    html = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;background:#f5f5f5;padding:24px;margin:0;">
  <div style="max-width:640px;margin:0 auto;background:#fff;border-radius:10px;
              box-shadow:0 2px 8px rgba(0,0,0,.08);overflow:hidden;">

    <!-- Header -->
    <div style="background:#c0392b;padding:24px 28px;">
      <h1 style="margin:0;color:#fff;font-size:20px;">🔔 NEPSE IPO Alert</h1>
      <p style="margin:6px 0 0;color:#f8d7da;font-size:13px;">
        {count} new IPO{'s are' if count > 1 else ' is'} currently open for application
      </p>
    </div>

    <!-- Table -->
    <div style="padding:20px 0;">
      <table style="width:100%;border-collapse:collapse;font-size:14px;">
        <thead>
          <tr style="background:#f8f9fa;">
            <th style="padding:10px 14px;text-align:left;color:#555;font-weight:600;
                       border-bottom:2px solid #dee2e6;">Company</th>
            <th style="padding:10px 14px;text-align:right;color:#555;font-weight:600;
                       border-bottom:2px solid #dee2e6;">Units Issued</th>
            <th style="padding:10px 14px;text-align:right;color:#555;font-weight:600;
                       border-bottom:2px solid #dee2e6;">Price/Unit</th>
            <th style="padding:10px 14px;text-align:center;color:#555;font-weight:600;
                       border-bottom:2px solid #dee2e6;">Opens</th>
            <th style="padding:10px 14px;text-align:center;color:#555;font-weight:600;
                       border-bottom:2px solid #dee2e6;">Closes</th>
          </tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>
    </div>

    <!-- CTA -->
    <div style="padding:16px 28px 28px;">
      <a href="https://meroshare.cdsc.com.np"
         style="display:inline-block;background:#c0392b;color:#fff;text-decoration:none;
                padding:10px 22px;border-radius:6px;font-size:14px;font-weight:600;">
        Apply on Meroshare →
      </a>
    </div>

    <!-- Footer -->
    <div style="background:#f8f9fa;padding:14px 28px;border-top:1px solid #eee;">
      <p style="margin:0;font-size:12px;color:#888;">
        Source: <a href="{IPO_URL}" style="color:#c0392b;">{IPO_URL}</a> •
        Checked at {datetime.now().strftime('%Y-%m-%d %H:%M')} NPT
      </p>
    </div>
  </div>
</body>
</html>"""

    return subject, html


def send_email(subject: str, html_body: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = EMAIL_SENDER
    msg["To"]      = EMAIL_RECEIVER
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    print(f"[email] Connecting to {SMTP_HOST}:{SMTP_PORT} ...")
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
    print(f"[email] Alert sent to {EMAIL_RECEIVER}.")



# Entry point

def main() -> None:
    print(f"[main] IPO check started at {datetime.now().isoformat()}")

    ipos      = scrape_ipos()
    seen      = load_cache()
    open_ipos = [i for i in ipos if is_open(i)]

    print(f"[main] Total scraped: {len(ipos)}, currently open: {len(open_ipos)}")

    # if not open_ipos:
    #     today = datetime.now().strftime("%Y-%m-%d")
    #     recent = sorted(
    #         ipos,
    #         key=lambda ipo: parse_ipo_date(ipo.get("open_date", "")) or datetime.min.date(),
    #         reverse=True,
    #     )[:3]

    #     subject, html = build_no_openings_html(today, recent)
    #     send_email(subject, html)
    #     print("[main] No IPO openings found today — notification sent.")
    #     return

    new_ipos = [i for i in open_ipos if make_ipo_id(i) not in seen]
    print(f"[main] New (not yet alerted): {len(new_ipos)}")

    if new_ipos:
        subject, html = build_email_html(new_ipos)
        send_email(subject, html)
        for i in new_ipos:
            seen.add(make_ipo_id(i))
        save_cache(seen)
    else:
        print("[main] No new IPOs — no email sent.")

    print("[main] Done.")


if __name__ == "__main__":
    main()
