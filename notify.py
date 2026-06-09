"""Telegram notifications.

Set two secrets/environment variables:
  TELEGRAM_BOT_TOKEN  - from @BotFather
  TELEGRAM_CHAT_ID    - your chat id (see README for how to get it)

If either is missing we log and no-op, so the scraper can be validated before
Telegram is wired up.
"""

from __future__ import annotations

import html
import os

import requests

API = "https://api.telegram.org/bot{token}/sendMessage"


def _credentials() -> tuple[str | None, str | None]:
    return os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")


def is_configured() -> bool:
    token, chat = _credentials()
    return bool(token and chat)


def _format_unit(u: dict) -> str:
    price = f"${u['price']:,}/mo" if u.get("price") else "price n/a"
    bits = [b for b in (
        f"{u['bedrooms']}BR" if u.get("bedrooms") else None,
        f"{u['bathrooms']}BA" if u.get("bathrooms") else None,
        f"{u['sqft']} sqft" if u.get("sqft") else None,
        u.get("floorplan"),
    ) if b]
    line1 = " · ".join(bits) if bits else "Unit"
    addr = u.get("address") or "StuyTown / PCV"
    unit_no = f" #{u['unit']}" if u.get("unit") else ""
    avail = f"\nAvailable: {u['available']}" if u.get("available") else ""
    url = u.get("url")
    title = html.escape(f"{addr}{unit_no}")
    return (
        f"🏠 <b>{title}</b>\n"
        f"{html.escape(line1)} — {html.escape(price)}{html.escape(avail)}\n"
        f'<a href="{html.escape(url)}">View / apply</a>'
    )


def _post(text: str, log=print) -> bool:
    """Send one HTML message; returns True on success."""
    token, chat = _credentials()
    if not (token and chat):
        log("Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID "
            "missing) — skipping notification.")
        return False
    try:
        r = requests.post(
            API.format(token=token),
            json={"chat_id": chat, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=30,
        )
        if r.status_code != 200:
            log(f"Telegram error {r.status_code}: {r.text}")
            return False
        return True
    except Exception as e:
        log(f"Telegram request failed: {e}")
        return False


def send_test(log=print) -> bool:
    """Send a one-off connectivity check so you know alerts will arrive."""
    ok = _post(
        "✅ <b>StuyTown/PCV watcher connected.</b>\n"
        "You'll get a message here the moment a 2BR/2BA PS40-zone unit opens up.",
        log=log,
    )
    log("Test message sent." if ok else "Test message NOT sent.")
    return ok


def send_new_units(units: list[dict], log=print) -> bool:
    """Send one Telegram message summarizing newly-found units."""
    if not units:
        return True
    if not is_configured():
        log("Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID "
            "missing) — skipping notification.")
        return False

    header = (f"🚨 <b>{len(units)} new StuyTown/PCV unit"
              f"{'s' if len(units) != 1 else ''}</b> matching "
              f"2BR/2BA, PS40 zone:")
    body = "\n\n".join(_format_unit(u) for u in units)
    text = f"{header}\n\n{body}"

    # Telegram caps messages at 4096 chars; chunk if needed.
    ok = all(_post(chunk, log=log) for chunk in _chunk(text, 4000))
    if ok:
        log(f"Sent Telegram alert for {len(units)} unit(s).")
    return ok


def _chunk(text: str, size: int):
    if len(text) <= size:
        yield text
        return
    # Split on blank lines so we don't cut a listing in half.
    buf = ""
    for block in text.split("\n\n"):
        if len(buf) + len(block) + 2 > size and buf:
            yield buf
            buf = ""
        buf += (("\n\n" if buf else "") + block)
    if buf:
        yield buf
