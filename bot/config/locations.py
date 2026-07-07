"""
locations.py - centrally maintained, approved country/city configuration
for the City Exposure Overview feature.

Why a static config module instead of a database table:
  The spec for this feature explicitly says not to introduce a new
  "locations" lookup table unless ARGUS already has a similar pattern
  (it doesn't - VALID_TYPES in database/assets.py is the closest existing
  precedent, and that's also a static in-code set, not a DB table). This
  module follows that same established convention.

  It is intentionally structured so an administrator-managed `locations`
  table could replace it later without touching any of the call sites -
  every function below only deals with plain Python data structures
  (dicts/sets/tuples), so a future DB-backed version only needs to keep
  the same function signatures and return shapes.

Coordinates are city-centroid level ONLY - this is a hard requirement of
the feature (no per-asset GPS, no building/room precision). Do not add
finer-grained coordinates here.

Immutability: this module is imported once per process and then shared,
unchanged, across every request for the life of the WSGI worker — it is
the app's security allowlist for city/country filtering. CITY_COORDINATES
and RISK_LEVEL_COLORS are exposed as read-only views (MappingProxyType),
so an accidental in-place mutation elsewhere in the codebase raises
immediately instead of silently corrupting shared state until restart.

SUPPORTED_LOCATIONS stays a plain (outer) dict rather than a
MappingProxyType: several templates serialize it directly via Jinja's
`| tojson` filter for client-side JS (assets.html, findings.html,
add_asset.html, edit_asset.html), and Python's json module cannot
serialize MappingProxyType. Its per-country "cities" lists are still
tuples (immutable), and a derived, never-exposed frozenset backs the
actual validation check in is_valid_city() — so the allowlist itself
still cannot be corrupted by an accidental in-place mutation, without
breaking template JSON serialization.
"""

from types import MappingProxyType
from typing import Optional

# ── Approved country -> city list ────────────────────────────────────────────
# This is the single source of truth for every country/city dropdown and
# every server-side validation check in the City Exposure feature. Add new
# entries here (and a matching entry in CITY_COORDINATES below, if you want
# the city to appear on the map) to expand supported locations.
#
# Read-only in effect: not a MappingProxyType (see module docstring — this
# object is serialized directly via Jinja's `| tojson` in several
# templates, which cannot handle MappingProxyType), but each "cities"
# list is a tuple, and the actual validation logic below never reads this
# structure directly — it uses _VALID_COUNTRY_CITY_PAIRS, a frozenset
# derived from it once at import time, so the allowlist enforced by
# is_valid_city() cannot be corrupted even if this dict were mutated.
SUPPORTED_LOCATIONS = {
    "IN": {
        "name": "India",
        "cities": ("New Delhi", "Mumbai", "Bengaluru", "Chennai", "Hyderabad"),
    },
    "ID": {
        "name": "Indonesia",
        "cities": ("Jakarta", "Surabaya", "Bandung", "Medan"),
    },
}

# O(1) membership set derived once at import time from SUPPORTED_LOCATIONS,
# instead of is_valid_city() doing a linear scan of a per-country list on
# every call. is_valid_city() is on the request path for /assets, /findings,
# /add_asset, and /edit_asset, so this turns a repeated O(n) scan (small n,
# but non-zero on every request) into a single O(1) hash lookup.
_VALID_COUNTRY_CITY_PAIRS = frozenset(
    (country_code, city)
    for country_code, entry in SUPPORTED_LOCATIONS.items()
    for city in entry["cities"]
)

# ── City-centroid coordinates (NOT precise asset locations) ─────────────────
# Keyed on (country_code, city) so identically-named cities in different
# countries never collide (e.g. there could be a "Singapore" city entry
# elsewhere with a different country_code in a future expansion).
#
# A city can exist in SUPPORTED_LOCATIONS without an entry here - that is
# the expected/handled "Unmapped" case (still shown in the exposure table,
# never on the map, never silently dropped). Do not assume every approved
# city has a coordinate.
#
# Read-only: MappingProxyType, same rationale as SUPPORTED_LOCATIONS above.
CITY_COORDINATES = MappingProxyType({
    ("IN", "New Delhi"):  MappingProxyType({"lat": 28.6139, "lng": 77.2090}),
    ("IN", "Mumbai"):     MappingProxyType({"lat": 19.0760, "lng": 72.8777}),
    ("IN", "Bengaluru"):  MappingProxyType({"lat": 12.9716, "lng": 77.5946}),
    ("IN", "Chennai"):    MappingProxyType({"lat": 13.0827, "lng": 80.2707}),
    ("IN", "Hyderabad"):  MappingProxyType({"lat": 17.3850, "lng": 78.4867}),
    ("ID", "Jakarta"):    MappingProxyType({"lat": -6.2088, "lng": 106.8456}),
    ("ID", "Surabaya"):   MappingProxyType({"lat": -7.2575, "lng": 112.7521}),
    ("ID", "Bandung"):    MappingProxyType({"lat": -6.9175, "lng": 107.6191}),
    # "Medan", "Manchester", "Birmingham" are deliberately left without a
    # centroid here to exercise/demonstrate the "Unmapped" path end-to-end -
    # add coordinates for them whenever real centroids are available.
})


def is_valid_city(country_code: Optional[str], city: Optional[str]) -> bool:
    """
    True only if country_code is an approved country AND city is one of
    that country's approved cities. Used for server-side validation -
    never trust the dropdown alone (the spec's own explicit requirement).

    Fails closed (returns False) on non-string input rather than raising,
    since this function gates access-control-relevant filtering/query
    construction and a malformed caller value must never turn into an
    unhandled exception / stack trace on the request path.
    """
    if not isinstance(country_code, str) or not isinstance(city, str):
        return False
    if not country_code or not city:
        return False
    return (country_code.upper(), city) in _VALID_COUNTRY_CITY_PAIRS


def get_country_name(country_code: Optional[str]) -> Optional[str]:
    """Return the human-readable country name, or None if not approved."""
    if not isinstance(country_code, str) or not country_code:
        return None
    entry = SUPPORTED_LOCATIONS.get(country_code.upper())
    return entry["name"] if entry else None


def get_coordinates(country_code: Optional[str], city: Optional[str]) -> Optional[dict]:
    """
    Return {"lat": float, "lng": float} for a city-centroid, or None if
    the city has no configured coordinate (the "Unmapped" case).

    Returns a fresh plain dict (a copy), never the stored MappingProxyType
    itself, so callers can never end up holding a reference to — or
    accidentally believing they can mutate — this module's internal
    shared state.
    """
    if not isinstance(country_code, str) or not isinstance(city, str):
        return None
    if not country_code or not city:
        return None
    coords = CITY_COORDINATES.get((country_code.upper(), city))
    return dict(coords) if coords is not None else None


def classify_risk_level(max_risk_score: int, kev_count: int) -> str:
    """
    Map a max risk score + KEV count to a human-readable risk level.

    Reuses the SAME thresholds already established in app.py's findings()
    route (RISK_RANGES: Low 0-75, Medium 76-125, High 126-175, Critical
    176+), per the feature spec's own instruction to use the project's
    existing risk convention rather than inventing a second, incompatible
    one. KEV presence always forces Critical regardless of score, matching
    how KEV is treated as an automatic escalation everywhere else in ARGUS
    (see risk/scoring.py's KEV bonus and the findings page's KEV badge).
    """
    score = max_risk_score or 0
    if kev_count and kev_count > 0:
        return "Critical"
    if score == 0:
        return "None"
    if score >= 176:
        return "Critical"
    if score >= 126:
        return "High"
    if score >= 76:
        return "Medium"
    return "Low"


# Display colors for each risk level, used by both the map markers and the
# exposure table badges so the two views are always visually consistent
# with each other and with the rest of ARGUS's existing badge styling.
#
# Read-only for the same reason as SUPPORTED_LOCATIONS/CITY_COORDINATES.
RISK_LEVEL_COLORS = MappingProxyType({
    "Critical": "#f75f5f",
    "High":     "#f7a14f",
    "Medium":   "#ffc107",
    "Low":      "#4fcf8e",
    "None":     "#7a869a",
})
