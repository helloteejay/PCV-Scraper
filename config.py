"""Configuration for the StuyTown / Peter Cooper Village availability checker.

Everything you might want to tweak lives here. The scraper itself reads these
values, so you should rarely need to touch the other files.
"""

from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# What we're searching for
# ---------------------------------------------------------------------------

# The StuyTown search page. We pin the filters we know via URL params and also
# re-apply them in the browser UI as a belt-and-suspenders measure. The default
# page loads a filter set we DON'T want, so we always pass our own params.
SEARCH_URL = (
    "https://www.stuytown.com/nyc-apartments-for-rent"
    "?Bedrooms=2&Bathrooms=2&Order=low-price"
)

# The site's internal JSON API (discovered from the page's own network calls).
# We query this directly — far more robust than scraping rendered HTML.
API_UNITS = "https://units.stuytown.com/api/units"
API_COUNT = "https://units.stuytown.com/api/units/units-filter/count"
PROPERTY_NAME = "Stuyvesant Town_Peter Cooper Village"

# Query params for the units we want. PS40 handled separately (see below).
API_FILTERS = {
    "Bedrooms": 2,
    "Bathrooms": 2,
    "Order": "low-price",
    "itemsOnPage": 100,
    "page": 0,
}

# How the PS40 school-zone filter is applied via the API. Once confirmed from
# the live data, set this to a (param_name, value) tuple, e.g. ("Ps40", "true").
# While None, we fall back to per-unit detection in _detect_ps40.
PS40_API_PARAM = None

# The unit criteria we actually care about. Filtering is done in code against
# the data we scrape, so even if the site ignores a URL param we still only
# alert on genuine matches.
WANT_BEDROOMS = 2
WANT_BATHROOMS = 2

# PS40 = the P.S. 40 (Augustus Saint-Gaudens) elementary school zone. StuyTown
# exposes this as a per-unit amenity (code "PS40" / "PS40 School District"),
# which we match directly in the unit data — see scraper._detect_ps40.
WANT_PS40 = True

# Fallback only: if a unit's PS40 status can't be determined from its data,
# whether to let it through (True) or exclude it (False). Detection is reliable
# now that PS40 is a known amenity, so this rarely matters.
PS40_TRUST_SITE_FILTER = False

# ---------------------------------------------------------------------------
# When to run (active window enforced in code so DST + day boundaries are safe)
# ---------------------------------------------------------------------------

TIMEZONE = ZoneInfo("America/New_York")

# New units post Tuesday–Saturday. Monday(0)..Sunday(6); we run Tue(1)–Sat(5).
ACTIVE_WEEKDAYS = {1, 2, 3, 4, 5}

# Active hours in local (ET) time, inclusive start, exclusive end. 7am–9pm.
ACTIVE_HOUR_START = 7
ACTIVE_HOUR_END = 21

# Set ENFORCE_ACTIVE_WINDOW=0 in the environment to bypass (useful for testing
# and for manual workflow_dispatch runs at any time).

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

STATE_FILE = "state/seen.json"
DEBUG_DIR = "debug"

# ---------------------------------------------------------------------------
# Browser
# ---------------------------------------------------------------------------

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
NAV_TIMEOUT_MS = 60_000
# Extra settle time (ms) after network goes idle, for late-rendering listings.
SETTLE_MS = 4_000
