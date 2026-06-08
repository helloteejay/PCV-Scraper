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
from datetime import datetime
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
    """True/False if we can tell, None if undetectable from this record."""
    # Explicit-ish fields first.
    explicit = _first(record, "ps40", "isPS40", "schoolZone", "school",
                       "schoolDistrict", "elementarySchool")
    if explicit is not None:
        s = str(explicit).lower()
        if any(h.replace(" ", "") in s.replace(" ", "")
               for h in config.PS40_LABEL_HINTS):
            return True
        # A populated, non-matching school field means "not PS40".
        if isinstance(explicit, bool):
            return explicit
        return False
    # Fallback: scan the whole record's JSON for a PS40 marker.
    blob = json.dumps(record, default=str).lower().replace(" ", "")
    for h in config.PS40_LABEL_HINTS:
        if h.replace(" ", "") in blob:
            return True
    return None


def _summarize_arrays(node: Any, path: str = "$", out: list | None = None,
                      max_entries: int = 80) -> list[tuple[str, int, list]]:
    """Find every list-of-objects in a JSON tree.

    Returns (path, length, sample_keys) tuples so the logs reveal exactly where
    listings live and what their fields are called. Diagnostic only.
    """
    if out is None:
        out = []
    if len(out) >= max_entries:
        return out
    if isinstance(node, list):
        dicts = [x for x in node if isinstance(x, dict)]
        if dicts:
            keys = sorted({k for d in dicts[:3] for k in d.keys()})
            out.append((path, len(node), keys[:35]))
        for i, x in enumerate(node[:4]):
            _summarize_arrays(x, f"{path}[{i}]", out, max_entries)
    elif isinstance(node, dict):
        for k, v in node.items():
            _summarize_arrays(v, f"{path}.{k}", out, max_entries)
    return out


def _log_diagnostics(captured, next_data_obj, page, log) -> None:
    """Emit a compact map of the page's data sources to the run logs."""
    log("=== DIAGNOSTICS ===")
    log(f"Captured JSON response URLs ({len(captured)}):")
    for url, _ in captured:
        log(f"  • {url[:160]}")
    for label, payload in (
        [(f"net[{i}]", j) for i, (_, j) in enumerate(captured)]
        + ([("__NEXT_DATA__", next_data_obj)] if next_data_obj is not None else [])
    ):
        for p, n, keys in _summarize_arrays(payload):
            if n >= 1:
                log(f"  [{label}] array {p} len={n} keys={keys}")
    # DOM signal: how many obvious listing-ish anchors/cards exist.
    try:
        counts = page.evaluate(
            """() => ({
                anchors: document.querySelectorAll('a').length,
                aptLinks: [...document.querySelectorAll('a')]
                    .filter(a => /apartment|leasing|unit|floorplan|\\/p\\//i
                        .test(a.getAttribute('href')||'')).length,
                dollar: (document.body.innerText.match(/\\$\\s?\\d[\\d,]{2,}/g)||[]).length,
                bodyLen: document.body.innerText.length
            })"""
        )
        log(f"  DOM: {counts}")
    except Exception as e:
        log(f"  DOM probe failed: {e}")
    log("=== END DIAGNOSTICS ===")


def normalize_unit(record: dict) -> dict | None:
    """Map a raw listing dict to our normalized shape, or None if unusable."""
    bedrooms = _to_int(_first(record, "bedrooms", "beds", "bedroomCount",
                              "numberOfBedrooms", "bed"))
    bathrooms = _to_int(_first(record, "bathrooms", "baths", "bathroomCount",
                               "numberOfBathrooms", "bath", "fullBathrooms"))
    price = _to_price(_first(record, "price", "rent", "netRent", "monthlyRent",
                             "startingPrice", "minPrice", "displayPrice"))
    unit_no = _first(record, "unitNumber", "apartmentNumber", "unit", "aptNo",
                     "number", "name")
    address = _first(record, "address", "buildingAddress", "streetAddress",
                     "building", "addressLine1")
    url = _first(record, "url", "detailUrl", "permalink", "link", "slug",
                 "path", "href")
    if isinstance(url, str) and url.startswith("/"):
        url = "https://www.stuytown.com" + url
    floorplan = _first(record, "floorplan", "floorPlan", "layout", "unitType")
    available = _first(record, "availableDate", "availabilityDate",
                       "dateAvailable", "moveInDate")
    unit_id = _first(record, "id", "unitId", "listingId", "guid", "uid",
                     "apartmentId")

    # A record with no bedroom signal is probably not a real unit.
    if bedrooms is None and unit_no is None and url is None:
        return None

    # Build a stable identity. Prefer an explicit id, then URL, then a composite.
    identity = (
        str(unit_id) if unit_id is not None
        else str(url) if url
        else f"{address}|{unit_no}|{floorplan}|{bedrooms}bd{bathrooms}ba"
    )

    return {
        "id": identity,
        "unit": str(unit_no) if unit_no is not None else None,
        "address": str(address) if address else None,
        "bedrooms": bedrooms,
        "bathrooms": bathrooms,
        "price": price,
        "floorplan": str(floorplan) if floorplan else None,
        "available": str(available) if available else None,
        "ps40": _detect_ps40(record),
        "url": str(url) if url else config.SEARCH_URL,
        "raw_keys": sorted(record.keys()) if isinstance(record, dict) else [],
    }


def matches_criteria(unit: dict) -> bool:
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


def _try_apply_ps40(page, log) -> None:
    """Best-effort click of the PS40 filter checkbox in the page UI."""
    for hint in config.PS40_LABEL_HINTS:
        try:
            # Try a label/text containing the hint, case-insensitive.
            locator = page.get_by_text(re.compile(re.escape(hint), re.I))
            if locator.count() > 0:
                locator.first.click(timeout=3000)
                log(f"Clicked PS40 filter via text match: '{hint}'")
                page.wait_for_timeout(config.SETTLE_MS)
                return
        except Exception:
            continue
    log("PS40 filter control not found in UI (relying on URL param / data filter)")


def _api_get(request_ctx, url: str, params: dict, log) -> Any:
    """GET a StuyTown API endpoint via the browser's request context."""
    from urllib.parse import urlencode

    full = url + "?" + urlencode(params)
    try:
        resp = request_ctx.get(full, timeout=config.NAV_TIMEOUT_MS)
    except Exception as e:
        log(f"  API request error: {e}")
        return None
    log(f"  GET {full} -> {resp.status}")
    body = None
    try:
        body = resp.json()
    except Exception:
        try:
            log(f"  (non-JSON body head: {resp.text()[:200]!r})")
        except Exception:
            pass
    return body


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

        # One-time schema diagnostic.
        if os.environ.get("DIAGNOSTICS", "1") == "1":
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
            ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
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
