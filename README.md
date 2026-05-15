# NEPSE IPO Alert Bot 🔔

Automatically checks [nepalipaisa.com/ipo](https://nepalipaisa.com/ipo) every day at **11:00 AM Nepal Time** and sends you an email if any new IPOs are open for application.

Runs entirely on **GitHub Actions** — no server or hosting costs.

---

## How It Works

1. A GitHub Actions cron job fires at 05:15 UTC (= 11:00 AM NPT) every day
2. A headless Chromium browser loads the IPO page (bypasses Cloudflare)
3. Open IPOs are compared against `seen_ipos.json` (cached between runs)
4. If any are new → a formatted HTML email is sent to your inbox
5. The cache is updated so you never get duplicate alerts

---

## Setup (5 minutes)

### 1. Fork / create this repo on GitHub

Push all files to a new GitHub repository (public or private).

### 2. Add GitHub Secrets

Go to your repo → **Settings → Secrets and variables → Actions → New repository secret**

| Secret name      | Value                                      |
|------------------|--------------------------------------------|
| `EMAIL_SENDER`   | The Gmail address the bot sends **from**   |
| `EMAIL_PASSWORD` | A Gmail **App Password** (see below)       |
| `EMAIL_RECEIVER` | Your email address that receives alerts    |

#### Getting a Gmail App Password
1. Make sure **2-Step Verification** is ON for your Google account
2. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
3. Create a new app password → name it "NEPSE Bot"
4. Copy the 16-character password → paste as `EMAIL_PASSWORD`

> ⚠️ Do **not** use your regular Gmail password. App Passwords are separate.

### 3. Enable GitHub Actions

Go to your repo → **Actions** tab → enable workflows if prompted.

### 4. Test it manually

Go to **Actions → NEPSE IPO Alert → Run workflow** to trigger it immediately without waiting for 11 AM.

---

## Project Structure

```
nepse-ipo-alert/
├── check_ipo.py                    # Main scraper + email logic
├── requirements.txt                # Python deps (just playwright)
├── seen_ipos.json                  # Auto-generated cache (do not edit)
└── .github/
    └── workflows/
        └── ipo_alert.yml           # GitHub Actions cron config
```

---

## Customisation

| What to change              | Where                          |
|-----------------------------|--------------------------------|
| Check time (default 11 AM)  | `cron:` line in `ipo_alert.yml` — use [crontab.guru](https://crontab.guru) |
| Email styling               | `build_email_html()` in `check_ipo.py` |
| Source website              | `IPO_URL` constant in `check_ipo.py` |
| Add WhatsApp alerts         | Add Twilio call inside `main()` after `send_email()` |

### Cron time reference
Nepal is UTC+5:45. To run at a different Nepal time:

| Nepal Time | UTC cron          |
|------------|-------------------|
| 8:00 AM    | `15 2 * * *`      |
| 11:00 AM   | `15 5 * * *`      |
| 6:00 PM    | `15 12 * * *`     |

---

## Troubleshooting

- **No email received** → Check the Actions run log for errors. Verify your secrets are set correctly.
- **Scraper finds 0 rows** → The site may have changed its HTML structure. Check the debug output in the Actions log and update the selectors in `scrape_ipos()`.
- **403 / bot detection** → Playwright with a real browser should bypass this, but if not, try adding a short `page.wait_for_timeout(2000)` after `page.goto()`.

---

## Cost

**Free.** GitHub Actions gives 2,000 free minutes/month on public repos and 500 on private. This job takes ~2 minutes per run × 30 days = ~60 minutes/month.
