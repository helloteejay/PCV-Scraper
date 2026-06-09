"""Scrape available StuyTown / PCV units using a headless browser.

Strategy (most-robust first):
  1. Drive a real headless Chromium to the filtered search URL. This executes
     the site's JavaScript and looks like a genuine browser, which defeats most
     basic bot protection.
  2. Capture every JSON network response the page makes. The listing data is
     almost certainly delivered by an internal API; parsing that JSON is far
     more stable than scraping rendered HTML.
  3. Fall back to the page's embedded `__NEXT_DATA__` / inline JSON if no API
     response yields listings.
  4. Always dump debug artifacts (HTML, screenshot, captured JSON) so selectors
     and field names can be confirmed against the live site after a run.

Because we don't control the site, field names are discovered heuristically.
`normalize_unit` tries many plausible key spellings; if the live data differs,
the debug artifacts tell us exactly what to add here.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Iterable

import config


# Keys that, when present in a dict, strongly suggest "this is an apartment".
_LISTING_SIGNALS = ("bedroom", "bathroom", "beds", "baths", "floorplan",
                    "unitnumber", "apartmentnumber", "rent", "availabledate")


def _lower_keys(d: dict) -> dict:
    return {str(k).lower(): v for k, v in d.items()}


def _looks_like_listing(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    keys = " ".join(str(k).lower() for k in obj.keys())
    hits = sum(1 for sig in _LISTING_SIGNALS if sig in keys)
    return hits >= 2


def _iter_listing_dicts(node: Any) -> Iterable[dict]:
    """Walk an arbitrary JSON structure yielding dicts that look like units."""
    if isinstance(node, dict):
        if _looks_like_listing(node):
            yield node
        for v in node.values():
            yield from _iter_listing_dicts(v)
    elif isinstance(node, list):
        for v in node:
            yield from _iter_listing_dicts(v)


def _first(d: dict, *names: str) -> Any:
    """Return the first present value among case-insensitive key names."""
    low = _lower_keys(d)
    for n in names:
        if n.lower() in low and low[n.lower()] not in (None, ""):
            return low[n.lower()]
    return None


def _to_int(val: Any) -> int | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return int(val)
    m = re.search(r"\d+", str(val))
    return int(m.group()) if m else None


def _to_price(val: Any) -> int | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return int(val)
    digits = re.sub(r"[^\d]", "", str(val).split(".")[0])
    return int(digits) if digits else None


def _detect_ps40(record: dict) -> bool | None:
    """True/False if we can tell, None if undetectable from this record.

    StuyTown encodes the P.S. 40 school zone as a unit amenity, e.g.
        {"searchCode": "PS40Code", "code": "PS40",
         "friendlyDescription": "PS40 School District"}
    """
    amenities = record.get("amenities")
    if isinstance(amenities, list):
        for a in amenities:
            if not isinstance(a, dict):
                continue
            blob = (str(a.get("code", "")) + str(a.get("searchCode", ""))
                    + str(a.get("friendlyDescription", ""))).upper().replace(" ", "")
            if "PS40" in blob:
                return True
        # Amenities present but no PS40 entry -> definitively not PS40.
        return False
    # Fallback for unexpected shapes: scan the record's JSON.
    if "PS40" in json.dumps(record, default=str).upper().replace(" ", ""):
        return True
    return None


def normalize_unit(record: dict) -> dict | None:
    """Map a raw StuyTown unit dict to our normalized shape, or None.

    Shape confirmed from the live API; generic fallbacks are kept so the code
    still works if StuyTown renames a field.
    """
    if not isinstance(record, dict):
        return None

    bedrooms = _to_int(_first(record, "bedrooms", "beds", "bedroomCount"))
    bathrooms = _to_int(_first(record, "bathrooms", "baths", "bathroomCount"))
    price = _to_price(_first(record, "price", "rent", "netRent", "monthlyRent"))
    sqft = _to_int(_first(record, "sqft", "squareFeet", "size"))
    unit_no = _first(record, "unitNumber", "apartmentNumber", "unit", "aptNo")
    available = _first(record, "availableDate", "availabilityDate",
                       "dateAvailable", "moveInDate")
    unit_id = _first(record, "unitSpk", "id", "unitId", "listingId", "guid")

    is_available = record.get("isAvailable")
    if not isinstance(is_available, bool):
        is_available = True  # assume listed == available unless told otherwise

    # Nested building object holds the address.
    building = record.get("building") if isinstance(record.get("building"), dict) else {}
    address = (building.get("buildingName") or building.get("address")
               or _first(record, "address", "buildingAddress", "streetAddress"))

    # Finish (e.g. "Classic", "Platinum") doubles as a human-friendly layout tag.
    finish_obj = record.get("finish") if isinstance(record.get("finish"), dict) else {}
    finish = finish_obj.get("name") or _first(record, "finish", "layout", "unitType")

    url = _first(record, "url", "detailUrl", "permalink", "link", "href")
    if isinstance(url, str) and url.startswith("/"):
        url = "https://www.stuytown.com" + url

    # Not a real unit if it lacks the basics.
    if bedrooms is None and unit_no is None and unit_id is None:
        return None

    identity = (
        str(unit_id) if unit_id is not None
        else str(url) if url
        else f"{address}|{unit_no}|{bedrooms}bd{bathrooms}ba"
    )

    return {
        "id": identity,
        "unit": str(unit_no) if unit_no is not None else None,
        "address": str(address) if address else None,
        "bedrooms": bedrooms,
        "bathrooms": bathrooms,
        "price": price,
        "sqft": sqft,
        "floorplan": str(finish) if finish else None,
        "available": str(available) if available else None,
        "is_available": is_available,
        "ps40": _detect_ps40(record),
        "url": str(url) if url else config.SEARCH_URL,
    }


def matches_criteria(unit: dict) -> bool:
    if not unit.get("is_available", True):
        return False
    if unit["bedrooms"] is not None and unit["bedrooms"] != config.WANT_BEDROOMS:
        return False
    if unit["bathrooms"] is not None and unit["bathrooms"] != config.WANT_BATHROOMS:
        return False
    if config.WANT_PS40:
        if unit["ps40"] is False:
            return False
        if unit["ps40"] is None and not config.PS40_TRUST_SITE_FILTER:
            return False
    return True


def _save_debug(name: str, content: str | bytes) -> None:
    os.makedirs(config.DEBUG_DIR, exist_ok=True)
    mode = "wb" if isinstance(content, bytes) else "w"
    with open(os.path.join(config.DEBUG_DIR, name), mode) as f:
        f.write(content)


def _api_get(request_ctx, url: str, params: dict, log) -> Any:
    """GET a StuyTown API endpoint via the browser's request context.

    Retries with exponential backoff on rate-limiting (429) or 5xx, so an
    occasional throttle doesn't drop a run.
    """
    import time
    from urllib.parse import urlencode

    full = url + "?" + urlencode(params)
    for attempt in range(config.API_MAX_RETRIES + 1):
        try:
            resp = request_ctx.get(full, timeout=config.NAV_TIMEOUT_MS)
        except Exception as e:
            log(f"  API request error: {e}")
            return None
        status = resp.status
        if status == 429 or status >= 500:
            if attempt < config.API_MAX_RETRIES:
                wait = 2 ** attempt
                log(f"  GET {full} -> {status}; backing off {wait}s "
                    f"(attempt {attempt + 1}/{config.API_MAX_RETRIES})")
                time.sleep(wait)
                continue
        log(f"  GET {full} -> {status}")
        try:
            return resp.json()
        except Exception:
            try:
                log(f"  (non-JSON body head: {resp.text()[:200]!r})")
            except Exception:
                pass
            return None
    return None


def _log_unit_schema(request_ctx, log) -> None:
    """One-time diagnostic: dump a real unit's fields to learn the schema."""
    log("=== SCHEMA PROBE ===")
    total = _api_get(request_ctx, config.API_COUNT,
                     {"PropertyName": config.PROPERTY_NAME}, log)
    log(f"  Total inventory count payload: {json.dumps(total)[:300]}")
    sample = _api_get(request_ctx, config.API_UNITS,
                      {"PropertyName": config.PROPERTY_NAME, "itemsOnPage": 3,
                       "page": 0}, log)
    if sample is not None:
        if isinstance(sample, dict):
            log(f"  units payload top-level keys: {sorted(sample.keys())}")
        units = list(_iter_listing_dicts(sample))
        log(f"  sample unit dicts found: {len(units)}")
        if units:
            log(f"  FIRST UNIT KEYS: {sorted(units[0].keys())}")
            log(f"  FIRST UNIT JSON: {json.dumps(units[0], default=str)[:1800]}")
            # Surface any field that might encode school zone / PS40.
            for u in units[:1]:
                hits = {k: v for k, v in u.items()
                        if "school" in k.lower() or "ps40" in str(v).lower()
                        or "ps 40" in str(v).lower() or "zone" in k.lower()}
                log(f"  candidate school/PS40 fields: {hits}")
    log("=== END SCHEMA PROBE ===")


def scrape(log=print) -> list[dict]:
    """Return a list of normalized units currently matching our criteria.

    Queries StuyTown's internal JSON API directly. A short browser visit first
    establishes cookies / anti-bot context, then we call the API with the
    browser's request session.
    """
    from playwright.sync_api import sync_playwright

    raw_units: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            user_agent=config.USER_AGENT,
            viewport={"width": 1366, "height": 900},
            locale="en-US",
        )
        page = ctx.new_page()
        page.set_default_timeout(config.NAV_TIMEOUT_MS)

        # Warm up: load the search page so the API sees normal browser cookies.
        log(f"Warming session at {config.SEARCH_URL}")
        try:
            page.goto(config.SEARCH_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(config.SETTLE_MS)
        except Exception as e:
            log(f"Warm-up navigation issue (continuing): {e}")

        # Schema diagnostic (off by default; set DIAGNOSTICS=1 to inspect).
        if os.environ.get("DIAGNOSTICS", "0") == "1":
            _log_unit_schema(ctx.request, log)

        # Real query for the units we want.
        params = dict(config.API_FILTERS)
        params["PropertyName"] = config.PROPERTY_NAME
        if config.PS40_API_PARAM:
            k, v = config.PS40_API_PARAM
            params[k] = v

        count = _api_get(ctx.request, config.API_COUNT, params, log)
        log(f"Count for our filters: {json.dumps(count)[:200]}")

        data = _api_get(ctx.request, config.API_UNITS, params, log)
        if data is not None:
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            try:
                _save_debug(f"units-{ts}.json",
                            json.dumps(data, default=str)[:2_000_000])
            except Exception:
                pass
            for rec in _iter_listing_dicts(data):
                raw_units.append(rec)
        log(f"{len(raw_units)} unit records returned by API")

        browser.close()

    # Normalize, filter, de-dup.
    seen_ids: set[str] = set()
    units: list[dict] = []
    for rec in raw_units:
        u = normalize_unit(rec)
        if not u or u["id"] in seen_ids:
            continue
        if matches_criteria(u):
            seen_ids.add(u["id"])
            units.append(u)

    log(f"{len(units)} units match criteria "
        f"({config.WANT_BEDROOMS}BR/{config.WANT_BATHROOMS}BA"
        f"{', PS40' if config.WANT_PS40 else ''})")
    return units
