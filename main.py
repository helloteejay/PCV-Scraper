"""Entry point: scrape -> diff against state -> notify on new units -> save state.

Run locally:   python main.py
On a schedule:  see .github/workflows/check-availability.yml
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime

import config
import notify
import scraper


def log(msg: str) -> None:
    print(f"[{datetime.utcnow().isoformat(timespec='seconds')}Z] {msg}",
          flush=True)


def within_active_window() -> bool:
    if os.environ.get("ENFORCE_ACTIVE_WINDOW", "1") == "0":
        return True
    now = datetime.now(config.TIMEZONE)
    if now.weekday() not in config.ACTIVE_WEEKDAYS:
        log(f"Outside active days (it's {now:%A} ET). Skipping.")
        return False
    if not (config.ACTIVE_HOUR_START <= now.hour < config.ACTIVE_HOUR_END):
        log(f"Outside active hours (it's {now:%H:%M} ET). Skipping.")
        return False
    return True


def load_state() -> dict:
    try:
        with open(config.STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"seen": {}}


def save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(config.STATE_FILE), exist_ok=True)
    with open(config.STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def main() -> int:
    # Test mode: send a Telegram connectivity check and exit. Triggered by the
    # workflow's "send_test_message" input. Runs regardless of the time window.
    if os.environ.get("TELEGRAM_TEST") == "1":
        log("Test mode: sending Telegram connectivity check.")
        notify.send_test(log=log)
        return 0

    if not within_active_window():
        return 0

    state = load_state()
    seen: dict = state.get("seen", {})

    try:
        units = scraper.scrape(log=log)
    except Exception as e:
        log(f"Scrape failed: {e}")
        # Don't crash the whole workflow on a transient failure.
        return 0

    new_units = [u for u in units if u["id"] not in seen]

    if new_units:
        log(f"Found {len(new_units)} NEW unit(s):")
        for u in new_units:
            log(f"  - {u['address']} #{u['unit']} | "
                f"{u['bedrooms']}BR/{u['bathrooms']}BA | "
                f"${u['price']} | ps40={u['ps40']} | {u['url']}")
        notify.send_new_units(new_units, log=log)
    else:
        log("No new units this run.")

    # Record everything currently matching so we don't re-alert next time.
    now_iso = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    for u in units:
        if u["id"] not in seen:
            seen[u["id"]] = {"first_seen": now_iso, **{k: u[k] for k in
                             ("unit", "address", "bedrooms", "bathrooms",
                              "price", "url")}}
        else:
            seen[u["id"]]["last_seen"] = now_iso
            seen[u["id"]]["price"] = u["price"]

    state["seen"] = seen
    state["last_run"] = now_iso
    save_state(state)
    log(f"State saved ({len(seen)} units tracked).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
