# PCV / StuyTown Availability Watcher

Watches the [StuyTown apartment search](https://www.stuytown.com/nyc-apartments-for-rent)
for **new units matching 2 bedrooms · 2 bathrooms · PS40 school zone**, and
texts you on **Telegram** the moment one appears.

It runs **entirely in the cloud on GitHub Actions** — no server, no machine of
your own needs to be on. It checks **every 30 minutes, Tuesday–Saturday,
7am–9pm ET** (the days/hours StuyTown posts new units).

---

## How it works

```
GitHub Actions cron ─▶ main.py ─▶ scraper.py (headless Chromium)
                                      │
                                      ├─ loads the filtered search page
                                      ├─ captures the site's JSON listing data
                                      └─ keeps only 2BR / 2BA / PS40 units
                                      │
                          diff against state/seen.json (what we've already seen)
                                      │
                          notify.py ─▶ Telegram message for each NEW unit
                                      │
                          commit updated state/seen.json back to the repo
```

- **No simple HTTP scraping** — the site is a JavaScript app, so we drive a real
  headless Chrome (Playwright). That also looks like a genuine browser.
- **State persists** by committing `state/seen.json` back to the repo each run,
  so you never get alerted twice for the same unit.
- **Debug artifacts** (rendered HTML + screenshot + captured JSON) are attached
  to every Actions run for 7 days, so the scraper can be tuned against the live
  site.

---

## One-time setup

### 1. Create your Telegram bot (2 minutes)

1. In Telegram, message **[@BotFather](https://t.me/BotFather)**.
2. Send `/newbot`, pick a name and username. BotFather replies with a
   **bot token** like `123456789:AAH...`. Copy it.
3. Send your new bot any message (say "hi") so it's allowed to message you.
4. Get your **chat id**: open
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser
   (paste your token in). Look for `"chat":{"id":...}` — that number is your
   `TELEGRAM_CHAT_ID`. (For a group, add the bot to the group first; the id
   will be negative.)

### 2. Add the secrets to this repo

GitHub → this repo → **Settings → Secrets and variables → Actions → New
repository secret**. Add both:

| Secret name           | Value                          |
| --------------------- | ------------------------------ |
| `TELEGRAM_BOT_TOKEN`  | the token from BotFather       |
| `TELEGRAM_CHAT_ID`    | your chat id                   |

> Until these are set, the scraper still runs and logs matches — it just skips
> the Telegram step.

### 3. Activate the schedule

GitHub only runs scheduled (`cron`) workflows from the **default branch**.
This code currently lives on the `claude/apartment-scraper-telegram-z3XAw`
branch. To turn on automatic 30-minute checks, **merge it to `main`**.

Before merging you can test it immediately:
**Actions tab → "Check StuyTown Availability" → Run workflow.**

---

## Testing / running manually

- **In the cloud:** Actions tab → *Check StuyTown Availability* → *Run workflow*.
  Manual runs ignore the day/hour window by default. Check the logs, and
  download the **debug artifact** to see exactly what the site returned.
- **Locally** (needs Python 3.10+):
  ```bash
  pip install -r requirements.txt
  python -m playwright install chromium
  export TELEGRAM_BOT_TOKEN=...    # optional
  export TELEGRAM_CHAT_ID=...      # optional
  ENFORCE_ACTIVE_WINDOW=0 python main.py
  ```

---

## Tuning

Everything adjustable lives in **`config.py`**:

- `WANT_BEDROOMS`, `WANT_BATHROOMS`, `WANT_PS40` — the criteria.
- `SEARCH_URL` — the search page + URL filters.
- `ACTIVE_WEEKDAYS`, `ACTIVE_HOUR_START/END`, `TIMEZONE` — when it runs.
- `PS40_LABEL_HINTS`, `PS40_TRUST_SITE_FILTER` — PS40 detection.

> **First-run note:** because StuyTown's internal data field names aren't
> publicly documented, the parser (`scraper.py`) discovers fields like
> `bedrooms`/`price`/`ps40` heuristically. After the first real run, open the
> debug artifact's `captured-*.json` to confirm the field names and, if needed,
> add the exact spellings to `normalize_unit` / `_detect_ps40`. Changing the
> simple settings above (cadence/criteria) never requires this.

## Files

| File | Purpose |
| --- | --- |
| `main.py` | Orchestrates scrape → diff → notify → save state |
| `scraper.py` | Headless-browser scraper + listing parser |
| `notify.py` | Telegram sender |
| `config.py` | All tunable settings |
| `state/seen.json` | Units already alerted on (auto-updated) |
| `.github/workflows/check-availability.yml` | The cloud schedule |
