"""Shared geocoding and geospatial utilities."""

from __future__ import annotations

import csv
import io
import logging
import math
import os
import re

import httpx

logger = logging.getLogger(__name__)

_geocode_client: httpx.Client | None = None
_batch_client: httpx.Client | None = None


def _get_geocode_client() -> httpx.Client:
    global _geocode_client
    if _geocode_client is None:
        _geocode_client = httpx.Client(
            timeout=10.0,
            headers={"User-Agent": os.environ.get("GEOCODE_USER_AGENT", "compliance-pipeline/1.0")},
        )
    return _geocode_client


def _get_batch_client() -> httpx.Client:
    global _batch_client
    if _batch_client is None:
        _batch_client = httpx.Client(timeout=120.0)
    return _batch_client


def _http_get(url, **kwargs):
    """HTTP GET using pooled geocode client."""
    return _get_geocode_client().get(url, **kwargs)


def _http_post(url, **kwargs):
    """HTTP POST using pooled batch client."""
    return _get_batch_client().post(url, **kwargs)


# US state abbreviations for input validation
_STATE_ABBREVS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN",
    "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH",
    "OK", "OR", "PA", "PR", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA",
    "WA", "WV", "WI", "WY",
}


def _normalize_state_in_address(address: str) -> str:
    """Uppercase a 2-letter US state abbreviation in a geocoded address string.

    Census geocoder occasionally returns mixed-case state abbreviations such as
    "4800 SAN JACINTO ST, HOUSTON, Tx, 77004".  This function finds any
    comma-delimited segment that is a known state abbreviation (case-insensitive)
    and replaces it with the canonical uppercase form.
    """
    def _upper_if_state(m: re.Match) -> str:
        token = m.group(0)
        if token.upper() in _STATE_ABBREVS:
            return token.upper()
        return token

    # Match a comma, optional whitespace, exactly 2 letters, then either a
    # comma or end-of-string (with optional whitespace/zip after the state).
    return re.sub(r"(?<=,\s)([A-Za-z]{2})(?=\s*(?:,|\s+\d{5}|$))", _upper_if_state, address)


def _extract_state(address: str) -> str | None:
    """Try to extract a US state abbreviation from an address string."""
    # Match ", ST" or ", ST 12345" patterns near end of address
    m = re.search(r",\s*([A-Z]{2})\s*(?:\d{5})?$", address.upper().strip())
    if m and m.group(1) in _STATE_ABBREVS:
        return m.group(1)
    # Also try matching state abbreviations anywhere with common patterns
    for part in reversed(address.upper().split(",")):
        part = part.strip()
        # "IL 60639" or just "IL"
        m2 = re.match(r"^([A-Z]{2})(?:\s+\d{5})?$", part)
        if m2 and m2.group(1) in _STATE_ABBREVS:
            return m2.group(1)
    return None


_STATE_NAMES = {
    "AL": "ALABAMA", "AK": "ALASKA", "AZ": "ARIZONA", "AR": "ARKANSAS",
    "CA": "CALIFORNIA", "CO": "COLORADO", "CT": "CONNECTICUT", "DE": "DELAWARE",
    "DC": "DISTRICT OF COLUMBIA", "FL": "FLORIDA", "GA": "GEORGIA", "HI": "HAWAII",
    "ID": "IDAHO", "IL": "ILLINOIS", "IN": "INDIANA", "IA": "IOWA", "KS": "KANSAS",
    "KY": "KENTUCKY", "LA": "LOUISIANA", "ME": "MAINE", "MD": "MARYLAND",
    "MA": "MASSACHUSETTS", "MI": "MICHIGAN", "MN": "MINNESOTA", "MS": "MISSISSIPPI",
    "MO": "MISSOURI", "MT": "MONTANA", "NE": "NEBRASKA", "NV": "NEVADA",
    "NH": "NEW HAMPSHIRE", "NJ": "NEW JERSEY", "NM": "NEW MEXICO", "NY": "NEW YORK",
    "NC": "NORTH CAROLINA", "ND": "NORTH DAKOTA", "OH": "OHIO", "OK": "OKLAHOMA",
    "OR": "OREGON", "PA": "PENNSYLVANIA", "PR": "PUERTO RICO", "RI": "RHODE ISLAND",
    "SC": "SOUTH CAROLINA", "SD": "SOUTH DAKOTA", "TN": "TENNESSEE", "TX": "TEXAS",
    "UT": "UTAH", "VT": "VERMONT", "VA": "VIRGINIA", "WA": "WASHINGTON",
    "WV": "WEST VIRGINIA", "WI": "WISCONSIN", "WY": "WYOMING",
}

# Nominatim OSM type/class values that indicate a street-level match rather
# than an address-point match.  Accepting these silently places the search
# center on a road segment, which can be miles from the actual building.
_STREET_LEVEL_TYPES = {
    "highway", "road", "residential", "primary", "secondary", "tertiary",
    "unclassified", "pedestrian", "footway", "path", "cycleway", "service",
    "living_street", "track",
}

# Subset of _STREET_LEVEL_TYPES that represent minor/residential roads.
# When a full address geocode fails, these results can be used as approximate
# street-centerline fallbacks — they are substantially more precise than a zip
# centroid (typically < 0.2 mi vs > 0.5 mi from the actual property).
# Only used when the matched result has the correct zip code or state so we
# don't silently snap to the wrong road.
_RESIDENTIAL_STREET_TYPES = {
    "residential", "unclassified", "living_street", "service", "tertiary",
}


def _result_contains_state(matched_addr: str, expected_state: str) -> bool:
    """Check if a geocoder result address string contains the expected state.

    Matches both 2-letter abbreviation (", NJ") and full name (", New Jersey").
    """
    upper = matched_addr.upper()
    if f", {expected_state}" in upper or f" {expected_state} " in upper:
        return True
    full_name = _STATE_NAMES.get(expected_state)
    if full_name and full_name in upper:
        return True
    return False


# Regex to extract a directional prefix from the street portion of an address.
# Matches patterns like "123 N Main St", "456 SW 3rd Ave", "789 NE Broadway".
# Group 1 is the directional (N, S, E, W, NE, NW, SE, SW).
_DIRECTIONAL_RE = re.compile(
    r"^\s*\d+\s+(N|S|E|W|NE|NW|SE|SW)\s+\S",
    re.IGNORECASE,
)


def _extract_directional(address: str) -> str | None:
    """Extract the street directional prefix from an address string.

    Looks for patterns like "123 N Main St", "456 SW 3rd Ave".
    Returns the directional in upper-case (e.g. "N", "SW") or None if not found.
    Only considers the first comma-delimited segment (the street portion).
    """
    street = address.split(",")[0].strip()
    m = _DIRECTIONAL_RE.match(street)
    if m:
        return m.group(1).upper()
    return None


# Mapping from road-type abbreviation (upper-case) to a canonical group.
# Any two types that share a group are considered interchangeable (e.g. AVE and
# AVENUE).  Types in *different* groups are substitutions worth warning about
# (e.g. FWY vs BLVD).
_ROAD_TYPE_GROUPS: dict[str, str] = {
    # Freeway / highway variants
    "FWY": "freeway", "FRWY": "freeway", "FREEWAY": "freeway",
    "HWY": "highway", "HIWAY": "highway", "HIGHWAY": "highway",
    "EXPY": "expressway", "EXPWY": "expressway", "EXPRESSWAY": "expressway",
    "PKWY": "parkway", "PARKWAY": "parkway", "PKWAY": "parkway",
    "PIKE": "pike",
    # Street variants
    "ST": "street", "STR": "street", "STREET": "street",
    # Avenue variants
    "AVE": "avenue", "AV": "avenue", "AVENUE": "avenue",
    # Boulevard variants
    "BLVD": "boulevard", "BOUL": "boulevard", "BOULEVARD": "boulevard",
    # Drive variants
    "DR": "drive", "DRV": "drive", "DRIVE": "drive",
    # Road variants
    "RD": "road", "ROAD": "road",
    # Court variants
    "CT": "court", "COURT": "court",
    # Circle variants
    "CIR": "circle", "CIRCLE": "circle",
    # Lane variants
    "LN": "lane", "LANE": "lane",
    # Place variants
    "PL": "place", "PLACE": "place",
    # Trail variants
    "TRL": "trail", "TRAIL": "trail",
    # Way variants
    "WAY": "way",
    # Terrace variants
    "TER": "terrace", "TERR": "terrace", "TERRACE": "terrace",
}

# Regex to extract the last street-type token from the street portion of an
# address.  Matches the final WORD token before a comma or end-of-string,
# within the first comma-delimited segment.
# Pattern: optional house number, then any name words, then a type token.
_STREET_TYPE_RE = re.compile(
    r"^\s*\d+\s+(?:\S+\s+)*(\S+)\s*$",
    re.IGNORECASE,
)


def _extract_road_type(address: str) -> str | None:
    """Extract and normalize the road type from the street portion of an address.

    Looks at the first comma-delimited segment (the street portion), strips the
    house number, and returns the normalized canonical group for the last token
    if it is a recognized road-type abbreviation, or None otherwise.

    Examples:
        "2300 Pasadena Fwy, Pasadena, TX"  -> "freeway"
        "2300 PASADENA BLVD, PASADENA, TX" -> "boulevard"
        "123 Main St, Austin, TX"          -> "street"
        "100 Meadowlands Pkwy, ..."        -> "parkway"
    """
    street = address.split(",")[0].strip()
    m = _STREET_TYPE_RE.match(street)
    if m:
        token = m.group(1).upper().rstrip(".")
        return _ROAD_TYPE_GROUPS.get(token)
    return None


def detect_road_type_substitution(
    input_address: str, matched_address: str, lat: float | None = None, lon: float | None = None
) -> str | None:
    """Return a warning string if the geocoder substituted a different road type.

    Compares the road type (e.g. Fwy, Blvd, Ave) in the input address against
    the road type in the matched address returned by the geocoder.  If they
    differ (and both are recognised types), returns a plain-English warning
    with actionable guidance.  Returns None when no substitution is detected
    or when either address is missing a recognised road type.

    When ``lat`` and ``lon`` are provided (the geocoded coordinates), the
    warning includes them so the user can copy-paste for a precise re-search.

    Example:
        input:   "2300 Pasadena Fwy, Pasadena, TX 77506"
        matched: "2300 PASADENA BLVD, PASADENA, TX, 77502"
        ->       "Matched to '2300 PASADENA BLVD, PASADENA, TX, 77502' instead
                  of the freeway in your query. Freeways are often misidentified
                  by geocoders. For a precise location use GPS coordinates
                  (29.69, -95.21), or try a highway number like 'TX-225'."
    """
    input_type = _extract_road_type(input_address)
    matched_type = _extract_road_type(matched_address)

    if not (input_type and matched_type and input_type != matched_type):
        return None

    # Build road-type-specific guidance
    _HIGHWAY_TYPES = {"freeway", "highway", "expressway"}
    if input_type in _HIGHWAY_TYPES:
        type_label = input_type  # "freeway", "highway", or "expressway"
        extra = (
            f" {type_label.capitalize()}s are often misidentified by geocoders."
            " Try a highway number (e.g. 'TX-225' or 'US-90') or GPS coordinates for accuracy."
        )
    else:
        type_label = input_type
        extra = " Try using GPS coordinates for a precise location."

    coords_hint = ""
    if lat is not None and lon is not None:
        coords_hint = f" GPS: {lat:.4f}, {lon:.4f}."

    return (
        f"Matched to \u2018{matched_address}\u2019 instead of the {type_label} in your query."
        f"{extra}{coords_hint}"
    )


def _nominatim_matched_address(best: dict, display_name: str) -> str:
    """Build a human-readable matched address from a Nominatim result.

    Nominatim sometimes returns display_name formatted as "100, Meadowlands
    Parkway, ..." where the house number and street are separate comma-delimited
    fields.  Splitting on comma and taking only the first element in that case
    yields just the house number (e.g. "100"), which is confusing.

    When structured ``address`` components are present (requires addressdetails=1
    in the query), we reconstruct the address from its parts.  Otherwise we fall
    back to combining the first two comma-delimited segments when the first looks
    like a bare house number (all-digit), or just the first segment otherwise.
    """
    addr_parts = best.get("address")
    if addr_parts:
        house = addr_parts.get("house_number", "").strip()
        road = addr_parts.get("road", "").strip()
        city = (
            addr_parts.get("city")
            or addr_parts.get("town")
            or addr_parts.get("village")
            or addr_parts.get("hamlet")
            or ""
        ).strip()
        state = addr_parts.get("state", "").strip()
        postcode = addr_parts.get("postcode", "").strip()

        parts = []
        if house and road:
            parts.append(f"{house} {road}")
        elif road:
            parts.append(road)
        if city:
            parts.append(city)
        if state:
            parts.append(state)
        if postcode:
            parts.append(postcode)
        if parts:
            return ", ".join(parts)

    # Fallback: use display_name segments.  When Nominatim returns
    # "100, Meadowlands Parkway, ..." the first segment is just the house
    # number — combine with the next segment to get a usable street address.
    segments = [s.strip() for s in display_name.split(",")]
    if segments and re.match(r"^\d+$", segments[0]) and len(segments) > 1:
        return f"{segments[0]} {segments[1].strip()}"
    return segments[0] if segments else display_name


def _try_nominatim(
    params: dict,
    address: str,
    expected_state: str | None,
    allow_highway: bool = False,
) -> tuple[float, float, str] | None:
    """Make a single Nominatim request and validate/filter the result.

    Returns (lat, lon, matched_address) or None if rejected or not found.
    Rejects results where OSM class or type indicates a street-level match
    (highway, road, etc.) rather than an address-point match.

    When allow_highway is True, results with class=highway are accepted even
    though they are technically a route-level match.  This is appropriate when
    the input address is a state/US highway route (e.g. "4200 State Highway 225")
    where Census TIGER has no addressable range and the highway centre-line is
    the best available match.
    """
    resp = _http_get(
        "https://nominatim.openstreetmap.org/search",
        params=params,
    )
    resp.raise_for_status()
    results = resp.json()
    if not results:
        return None

    best = results[0]
    display_name = best.get("display_name", address)

    # Validate state if we know what to expect
    if expected_state and not _result_contains_state(display_name, expected_state):
        logger.warning(
            "Nominatim returned %r for input %r — state mismatch (expected %s)",
            display_name, address, expected_state,
        )
        return None

    # Reject street-level matches (type=highway/road/residential/etc.)
    # When allow_highway=True, accept class=highway results (state/US highway
    # route lines) — these are the best available match for highway addresses.
    osm_type = best.get("type", "")
    osm_class = best.get("class", "")
    if osm_type in _STREET_LEVEL_TYPES or osm_class in _STREET_LEVEL_TYPES:
        if allow_highway and osm_class == "highway":
            logger.debug(
                "Nominatim returned highway-class match (class=%r, type=%r) for %r — accepting (allow_highway)",
                osm_class, osm_type, address,
            )
        else:
            logger.warning(
                "Nominatim returned street-level match (class=%r, type=%r) for %r — rejecting",
                osm_class, osm_type, address,
            )
            return None

    matched_addr = _nominatim_matched_address(best, display_name)
    return (float(best["lat"]), float(best["lon"]), matched_addr)


def _try_nominatim_street_approx(
    params: dict,
    address: str,
    expected_state: str | None,
    input_zip: str | None,
) -> tuple[float, float, str] | None:
    """Try Nominatim and accept residential street-centerline results as approximate.

    When a full address geocode fails (no address-point data in Census or OSM),
    this function accepts Nominatim results with OSM type in
    _RESIDENTIAL_STREET_TYPES (residential, unclassified, living_street,
    service, tertiary) as approximate street-centerline matches.

    Applies additional validation to avoid snapping to the wrong road:
    - State must match (same as _try_nominatim)
    - If input_zip is provided, the result must contain that zip code

    Returns (lat, lon, matched_address) with "(approximate — matched street,
    not exact address)" appended to matched_address, or None if no acceptable
    result found.
    """
    resp = _http_get(
        "https://nominatim.openstreetmap.org/search",
        params=params,
    )
    resp.raise_for_status()
    results = resp.json()
    if not results:
        return None

    for candidate in results:
        display_name = candidate.get("display_name", address)

        # Validate state
        if expected_state and not _result_contains_state(display_name, expected_state):
            continue

        osm_type = candidate.get("type", "")
        osm_class = candidate.get("class", "")

        # Only accept residential/minor road types as approximate
        if osm_type not in _RESIDENTIAL_STREET_TYPES:
            continue

        # If the input has a zip, the result must contain the same zip
        if input_zip and input_zip not in display_name:
            continue

        matched_addr = _nominatim_matched_address(candidate, display_name)
        logger.info(
            "Nominatim street-approx match (class=%r, type=%r) for %r → %r",
            osm_class, osm_type, address, matched_addr,
        )
        return (
            float(candidate["lat"]),
            float(candidate["lon"]),
            f"{matched_addr} (approximate — matched street, not exact address)",
        )

    return None


def _strip_zip(address: str) -> str | None:
    """Return address with trailing zip code removed, or None if no zip found.

    Handles formats like "123 Main St, City, ST 12345" → "123 Main St, City, ST"
    and "123 Main St, City, ST 12345-6789" → "123 Main St, City, ST".
    Returns None if no zip code is found (to avoid unnecessary retries).
    """
    stripped = re.sub(r"\s+\d{5}(?:-\d{4})?$", "", address.strip())
    if stripped != address.strip():
        return stripped.rstrip(", ").strip()
    return None


def _extract_zip(address: str) -> str | None:
    """Extract the first 5-digit zip code from an address string, or None."""
    m = re.search(r"\b(\d{5})\b", address)
    return m.group(1) if m else None


def _normalize_commas(address: str) -> str | None:
    """Return address with commas replaced by spaces, or None if no commas present.

    The Census Bureau geocoder sometimes fails on standard comma-formatted
    addresses like "1200 Navigation Blvd, Houston, TX 77003" but succeeds for
    the comma-free form "1200 Navigation Blvd Houston TX 77003".  This
    normalization lets us retry with the flattened form before giving up.

    Multiple spaces are collapsed to a single space.
    """
    if "," not in address:
        return None
    normalized = re.sub(r"\s+", " ", address.replace(",", " ")).strip()
    if normalized != address.strip():
        return normalized
    return None


# Mapping from English ordinal word to numeric ordinal for street names.
# Many cities (Detroit, Chicago, New York, etc.) label streets with numeric
# ordinals ("2nd Ave") while users often write the word form ("Second Ave").
# Nominatim's OSM data stores the numeric form, so word-form queries return
# no results even when the address exists.
_ORDINAL_WORDS: dict[str, str] = {
    "first": "1st", "second": "2nd", "third": "3rd", "fourth": "4th",
    "fifth": "5th", "sixth": "6th", "seventh": "7th", "eighth": "8th",
    "ninth": "9th", "tenth": "10th", "eleventh": "11th", "twelfth": "12th",
    "thirteenth": "13th", "fourteenth": "14th", "fifteenth": "15th",
    "sixteenth": "16th", "seventeenth": "17th", "eighteenth": "18th",
    "nineteenth": "19th", "twentieth": "20th",
    "twenty-first": "21st", "twenty-second": "22nd", "twenty-third": "23rd",
    "twenty-fourth": "24th", "twenty-fifth": "25th", "twenty-sixth": "26th",
    "twenty-seventh": "27th", "twenty-eighth": "28th", "twenty-ninth": "29th",
    "thirtieth": "30th",
}

# Regex to find a word-form ordinal as a whole word in the street portion.
# We only look in the first comma-delimited segment (the street part).
_ORDINAL_WORD_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _ORDINAL_WORDS) + r")\b",
    re.IGNORECASE,
)


def _normalize_ordinal_street(address: str) -> str | None:
    """Replace word-form ordinals in the street portion with numeric ordinals.

    Many cities label streets with numeric ordinals ("2nd Ave", "3rd St")
    while users often write the word form ("Second Ave", "Third St").
    Nominatim OSM data uses the numeric form, so "Second Ave" queries return
    no results even when "2nd Ave" would resolve exactly.

    Only substitutes within the first comma-delimited segment (the street
    portion) to avoid mangling city/state names.

    Returns the normalised address if any substitution was made, else None.

    Example:
        "5600 Second Ave, Detroit, MI 48202" → "5600 2nd Ave, Detroit, MI 48202"
        "100 Third Street, Chicago, IL 60601" → "100 3rd Street, Chicago, IL 60601"
    """
    parts = address.split(",")
    street = parts[0]
    normalized_street = _ORDINAL_WORD_RE.sub(
        lambda m: _ORDINAL_WORDS[m.group(1).lower()], street
    )
    if normalized_street == street:
        return None
    return normalized_street + ("," + ",".join(parts[1:]) if len(parts) > 1 else "")


def _census_geocode(address: str, expected_state: str | None) -> tuple[float, float, str] | None:
    """Try Census Bureau geocoder (both benchmarks) for a given address string.

    Returns (lat, lon, matched_address) or None if no valid match found.
    """
    input_directional = _extract_directional(address)

    for benchmark in ("Public_AR_Current", "Public_AR_ACS2023"):
        try:
            resp = _http_get(
                "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
                params={"address": address, "benchmark": benchmark, "format": "json"},
            )
            resp.raise_for_status()
            matches = resp.json().get("result", {}).get("addressMatches", [])
            if matches:
                best = matches[0]
                coords = best["coordinates"]
                matched_addr = best.get("matchedAddress", address)
                # If Census returned a truncated/degenerate matched address
                # (e.g. just "100" for "100 Meadowlands Pkwy"), fall back to
                # the input address so the UI shows something useful.
                if len(matched_addr) < 10 and len(address) > len(matched_addr):
                    logger.debug(
                        "Census geocoder (%s) returned truncated matchedAddress %r for %r; using input",
                        benchmark, matched_addr, address,
                    )
                    matched_addr = address
                if expected_state and not _result_contains_state(matched_addr, expected_state):
                    logger.warning(
                        "Census geocoder (%s) returned %r for input %r — state mismatch (expected %s)",
                        benchmark, matched_addr, address, expected_state,
                    )
                    continue
                # Reject results where the directional prefix (N/S/E/W) was swapped.
                # e.g. input "2120 S Wilmington Ave" → Census returns "2120 N WILMINGTON AVE"
                # — the entire search center would be miles away from the actual address.
                if input_directional is not None:
                    matched_directional = _extract_directional(matched_addr)
                    if matched_directional is not None and matched_directional != input_directional:
                        logger.warning(
                            "Census geocoder (%s) returned %r for input %r — directional mismatch "
                            "(input=%s, matched=%s); skipping",
                            benchmark, matched_addr, address, input_directional, matched_directional,
                        )
                        continue
                matched_addr = _normalize_state_in_address(matched_addr)
                return (coords["y"], coords["x"], matched_addr)
        except Exception:
            logger.debug(
                "Census geocoder (%s) failed for %r", benchmark, address, exc_info=True
            )
    return None


# Mapping of Texas highway prefixes to Census/TIGER-compatible equivalents.
# Census Bureau TIGER data uses "State Highway NNN" for TX state routes.
# Farm-to-Market roads are stored as "Farm to Market Road NNN" in TIGER.
# "Hwy NNN" is a generic abbreviation that Census usually handles, but we
# normalise it to "Highway NNN" for consistency.
_TX_HIGHWAY_PATTERNS: list[tuple[re.Pattern, str]] = [
    # "Texas NNN" or "Texas Hwy NNN" → "State Highway NNN"
    (re.compile(r"\bTexas\s+(?:Hwy\s+)?(\d+)\b", re.IGNORECASE), r"State Highway \1"),
    # "TX-NNN" or "TX NNN" (state abbreviation prefix) → "State Highway NNN"
    # Limit to 1-4 digit highway numbers to avoid matching TX zip codes (5 digits).
    (re.compile(r"\bTX[-\s]+(\d{1,4})\b", re.IGNORECASE), r"State Highway \1"),
    # "SH-NNN", "SH NNN" (bare 2-letter abbreviation) → "State Highway NNN"
    (re.compile(r"\bSH[-\s]+(\d+)\b", re.IGNORECASE), r"State Highway \1"),
    # "State Hwy NNN" → "State Highway NNN"
    (re.compile(r"\bState\s+Hwy[-\s]+(\d+)\b", re.IGNORECASE), r"State Highway \1"),
    # "IH-NNN", "IH NNN" → "Interstate Highway NNN" (Texas uses IH prefix)
    (re.compile(r"\bIH[-\s]+(\d+)\b", re.IGNORECASE), r"Interstate Highway \1"),
    # "FM-NNN", "FM NNN" → "Farm to Market Road NNN"
    (re.compile(r"\bFM[-\s]+(\d+)\b", re.IGNORECASE), r"Farm to Market Road \1"),
    # "Farm to Market NNN" (without "Road") → "Farm to Market Road NNN"
    (re.compile(r"\bFarm\s+to\s+Market\s+(\d+)\b", re.IGNORECASE), r"Farm to Market Road \1"),
    # "Hwy NNN" → "Highway NNN" (generic — keep last so FM/SH patterns run first)
    (re.compile(r"\bHwy\s+(\d+)\b", re.IGNORECASE), r"Highway \1"),
]


def _normalize_tx_highway(address: str) -> str | None:
    """Normalise Texas highway address formats for Census Bureau geocoding.

    Census Bureau TIGER data uses canonical road names like "State Highway 225"
    and "Farm to Market Road 1960" rather than the informal abbreviations
    commonly used in addresses ("Texas 225", "FM 1960", "SH 288").

    Returns the normalised address if any substitution was made, else None.
    Only applied when the address appears to be in Texas (contains ", TX" or
    ", TEXAS") to avoid false positives on other states.
    """
    upper = address.upper()
    if ", TX" not in upper and ", TEXAS" not in upper:
        return None

    result = address
    for pattern, replacement in _TX_HIGHWAY_PATTERNS:
        result = pattern.sub(replacement, result)

    return result if result != address else None


def normalize_tx_highway_street(street: str) -> str:
    """Normalise a TX highway street component for Census Bureau batch geocoding.

    Unlike _normalize_tx_highway (which requires ", TX" in the full address),
    this function operates unconditionally on the street component alone.  It
    is intended for use when the caller already knows the address is in Texas
    (e.g. when building batch geocode payloads for the TCEQ source).

    Applies the same substitutions as _normalize_tx_highway:
      "FM 1960"  → "Farm to Market Road 1960"
      "SH 288"   → "State Highway 288"
      "IH 10"    → "IH 10"  (IH not in patterns — Census handles "IH" ok)

    Returns the normalised street, or the original if no changes were made.
    """
    result = street
    for pattern, replacement in _TX_HIGHWAY_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def _geocode_city_state_fallback(address: str, expected_state: str | None) -> tuple[float, float, str] | None:
    """Last-resort geocode: try zip code, then city/state via Nominatim.

    Used when the full street address fails all other geocoding strategies.
    Returns (lat, lon, matched_address) where matched_address contains
    "(approximate)" to signal that the result is not a precise address match.
    Returns None if even city/state/zip cannot be geocoded.
    """
    # Try zip code first — most precise city-level fallback
    zip_code = _extract_zip(address)
    if zip_code:
        params: dict = {
            "q": f"{zip_code}, USA",
            "format": "json",
            "limit": 3,
            "countrycodes": "us",
            "addressdetails": 1,
        }
        try:
            result = _try_nominatim(params, address, expected_state)
            if result is not None:
                lat, lon, matched = result
                return lat, lon, f"{matched} (approximate — address not in geocoding databases)"
        except Exception:
            logger.debug("_geocode_city_state_fallback: Nominatim zip lookup failed for %r", zip_code, exc_info=True)

    # Try city + state extracted from address parts
    parts = [p.strip() for p in address.split(",")]
    if len(parts) >= 3:
        city = parts[1].strip()
        state_zip = parts[2].strip()
        state_only = state_zip.split()[0] if state_zip else ""
        if city and state_only:
            q = f"{city}, {state_only}, USA"
            params2: dict = {
                "q": q,
                "format": "json",
                "limit": 3,
                "countrycodes": "us",
                "addressdetails": 1,
            }
            try:
                result2 = _try_nominatim(params2, address, expected_state)
                if result2 is not None:
                    lat, lon, matched = result2
                    return lat, lon, f"{matched} (approximate — address not in geocoding databases)"
            except Exception:
                logger.debug(
                    "_geocode_city_state_fallback: Nominatim city/state lookup failed for %r", q, exc_info=True
                )

    return None


_COORD_RE = re.compile(
    r"^\s*(-?\d{1,3}(?:\.\d+)?)\s*[,\s]\s*(-?\d{1,3}(?:\.\d+)?)\s*$"
)


def parse_coordinate_string(address: str) -> tuple[float, float] | None:
    """Return (lat, lon) if address looks like a raw coordinate pair, else None.

    Accepts formats like "29.6835, -95.0610" or "29.6835 -95.0610".
    Validates that lat is in [-90, 90] and lon is in [-180, 180].
    """
    m = _COORD_RE.match(address)
    if not m:
        return None
    try:
        lat = float(m.group(1))
        lon = float(m.group(2))
    except ValueError:
        return None
    if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
        return lat, lon
    return None


def geocode_address(address: str) -> tuple[float, float, str] | None:
    """Geocode a US street address.

    Tries multiple strategies in order:
    0. Direct coordinate parse — if address looks like "lat, lon" or "lat lon",
       return immediately without any geocoding API calls.
    1. Census Bureau geocoder with Public_AR_Current benchmark
    2. Census Bureau geocoder with Public_AR_ACS2023 benchmark (catches addresses
       the current benchmark misses)
    3. Census Bureau geocoder with zip code stripped (some zip+directional combos
       confuse the Census parser; e.g. "2500 N Main St, Los Angeles, CA 90031"
       fails but "2500 N Main St, Los Angeles, CA" succeeds)
    4. Census Bureau geocoder with commas replaced by spaces (standard
       comma-formatted addresses like "1200 Navigation Blvd, Houston, TX 77003"
       sometimes fail Census but the comma-free form succeeds).  Also retries
       with commas removed AND zip stripped.
    5. TX highway normalisation — if the address looks like a Texas highway
       address ("Texas 225", "FM 1960", "SH 288"), convert to Census-compatible
       road names ("State Highway 225", "Farm to Market Road 1960") and retry
       all Census strategies.
    6. Nominatim unstructured query with "USA" appended
    7. Nominatim structured query (street/city/state/postalcode separated)
    7a. Nominatim structured query WITHOUT city — for unincorporated communities
       (e.g. Murray Hill, NJ is part of Berkeley Heights Township). The community
       name is not a formal city and confuses geocoders. Dropping the city and
       relying on street + state + zip lets Nominatim resolve via postal index.
    7b. Nominatim residential street-centerline fallback — when strategies 6 & 7
       return only a road segment (class=highway, type=residential/unclassified/
       etc.) for a numbered street address, accept the street centerline as an
       approximate result if the zip code matches.  A street centerline is
       typically < 0.2 mi from the actual property, far better than Strategy 8's
       zip centroid (> 0.5 mi).  Only applied when the input starts with a house
       number.
    7c. Ordinal word → numeric retry — when the street name uses a word-form
       ordinal ("Second Ave", "Third St") that Nominatim stores as a numeric
       ordinal ("2nd Ave", "3rd St"), re-run all Nominatim strategies with the
       normalised form.  Example: "5600 Second Ave, Detroit, MI 48202" fails
       strategies 6–7b but resolves exactly as "5600 2nd Ave, Detroit, MI 48202".
    8. City/zip centroid fallback — when the full street address fails all
       geocoders (common for rural/industrial roads not in public databases),
       geocodes just the zip code or city/state via Nominatim.  Returns an
       approximate result (matched_address contains "(approximate)") so the
       search can proceed centered on the right area rather than failing entirely.

    Validates that the result is in the expected state (if parseable from
    input) and rejects Nominatim street-level matches (highway, road,
    residential, etc.) to prevent silent wrong-location results (except for
    Strategy 7b which explicitly accepts residential road types as approximate).

    Returns (lat, lon, matched_address) or None if no match found.
    When Strategy 7b is used, matched_address contains "(approximate — matched
    street, not exact address)" to signal that the result is a street centerline.
    When Strategy 8 is used, matched_address contains "(approximate — address
    not in geocoding databases)" to signal that the result is approximate.
    """
    # --- Strategy 0: Direct coordinate input ---
    coords = parse_coordinate_string(address)
    if coords is not None:
        lat, lon = coords
        logger.debug("geocode_address: detected coordinate input %r → (%.6f, %.6f)", address, lat, lon)
        return lat, lon, address.strip()

    expected_state = _extract_state(address)

    # --- Strategy 1 & 2: Census Bureau (two benchmarks, full address) ---
    result = _census_geocode(address, expected_state)
    if result is not None:
        return result

    # --- Strategy 3: Census Bureau with zip code stripped ---
    # Some addresses with directional prefixes (N/S/E/W) fail Census when a zip
    # code is appended, but succeed without the zip.  Strip and retry before
    # falling back to Nominatim so we keep the directional precision.
    no_zip_address = _strip_zip(address)
    if no_zip_address:
        logger.debug("Census geocoder failed for %r; retrying without zip: %r", address, no_zip_address)
        result = _census_geocode(no_zip_address, expected_state)
        if result is not None:
            return result

    # --- Strategy 4: Census Bureau with commas normalized ---
    # Standard comma-formatted addresses like "1200 Navigation Blvd, Houston,
    # TX 77003" sometimes fail Census even though "1200 Navigation Blvd Houston
    # TX 77003" succeeds.  Replace commas with spaces and retry.
    comma_free = _normalize_commas(address)
    if comma_free:
        logger.debug(
            "Census geocoder failed for %r; retrying with commas removed: %r",
            address, comma_free,
        )
        result = _census_geocode(comma_free, expected_state)
        if result is not None:
            return result
        # Also try comma-free + zip stripped
        no_zip_comma_free = _strip_zip(comma_free)
        if no_zip_comma_free:
            logger.debug(
                "Census geocoder failed for comma-free %r; retrying without zip: %r",
                comma_free, no_zip_comma_free,
            )
            result = _census_geocode(no_zip_comma_free, expected_state)
            if result is not None:
                return result

    # --- Strategy 5: TX highway address normalisation ---
    # Texas highway addresses ("4200 Texas 225", "1960 FM 1960") use informal
    # road names that Census TIGER doesn't recognise.  Normalise to canonical
    # forms and retry all Census strategies before falling back to Nominatim.
    tx_norm = _normalize_tx_highway(address)
    if tx_norm:
        logger.debug(
            "TX highway normalisation: %r → %r; retrying Census", address, tx_norm
        )
        result = _census_geocode(tx_norm, expected_state)
        if result is not None:
            return result
        # Also try no-zip and comma-free variants of the normalised address
        no_zip_tx = _strip_zip(tx_norm)
        if no_zip_tx:
            result = _census_geocode(no_zip_tx, expected_state)
            if result is not None:
                return result
        comma_free_tx = _normalize_commas(tx_norm)
        if comma_free_tx:
            result = _census_geocode(comma_free_tx, expected_state)
            if result is not None:
                return result
            no_zip_comma_free_tx = _strip_zip(comma_free_tx)
            if no_zip_comma_free_tx:
                result = _census_geocode(no_zip_comma_free_tx, expected_state)
                if result is not None:
                    return result
        # Census TIGER often lacks addressable ranges for TX state highways.
        # Try Nominatim with the normalised form ("State Highway NNN") — OSM
        # knows these routes.  Accept highway-class results (class=highway)
        # because the input IS a highway address; a route-line match is far
        # better than falling back to the zip-code centroid.
        tx_norm_usa = tx_norm if tx_norm.upper().endswith("USA") else tx_norm + ", USA"
        try:
            result = _try_nominatim(
                {"q": tx_norm_usa, "format": "json", "limit": 5, "countrycodes": "us", "addressdetails": 1},
                tx_norm,
                expected_state,
                allow_highway=True,
            )
            if result is not None:
                logger.debug(
                    "TX highway Nominatim fallback matched %r → %r", address, result[2]
                )
                return result
        except Exception:
            logger.debug(
                "TX highway Nominatim fallback failed for %r", tx_norm, exc_info=True
            )

    # --- Strategy 6 & 7: Nominatim fallback ---
    # Build Nominatim query variants to try in order.
    nominatim_queries: list[dict] = []

    # Strategy 6: unstructured query with "USA" appended (improves precision)
    # addressdetails=1 returns structured address components so _try_nominatim
    # can reconstruct the full matched address (e.g. "100 Meadowlands Pkwy, ..."
    # instead of just "100" when Nominatim uses comma-separated house number).
    q_with_usa = address if address.upper().endswith("USA") else address + ", USA"
    nominatim_queries.append({"q": q_with_usa, "format": "json", "limit": 5, "countrycodes": "us", "addressdetails": 1})

    # Strategy 7: structured query — parse address into components
    parts = [p.strip() for p in address.split(",")]
    if len(parts) >= 3:
        # "123 Main St, City, ST 12345" → street=parts[0], city=parts[1], state+zip=parts[2]
        state_zip = parts[2].strip()
        state_part = state_zip.split()[0] if state_zip else ""
        zip_part = state_zip.split()[1] if len(state_zip.split()) > 1 else ""
        structured: dict = {
            "street": parts[0],
            "city": parts[1],
            "state": state_part,
            "format": "json",
            "limit": 5,
            "countrycodes": "us",
            "addressdetails": 1,
        }
        if zip_part:
            structured["postalcode"] = zip_part
        nominatim_queries.append(structured)

        # Strategy 7a: structured query WITHOUT city — for unincorporated
        # communities (e.g. Murray Hill, NJ is part of Berkeley Heights Township).
        # The community name is not a formal city and confuses geocoders.
        # Dropping the city and relying on street + state + zip lets Nominatim
        # resolve the address via its postal code / state index.
        no_city_structured: dict = {
            "street": parts[0],
            "state": state_part,
            "format": "json",
            "limit": 5,
            "countrycodes": "us",
            "addressdetails": 1,
        }
        if zip_part:
            no_city_structured["postalcode"] = zip_part
        nominatim_queries.append(no_city_structured)
    elif len(parts) == 2:
        # "123 Main St, City ST 12345"
        nominatim_queries.append({
            "street": parts[0],
            "city": parts[1],
            "format": "json",
            "limit": 5,
            "countrycodes": "us",
            "addressdetails": 1,
        })

    for params in nominatim_queries:
        try:
            result = _try_nominatim(params, address, expected_state)
            if result is not None:
                return result
        except Exception:
            logger.debug("Nominatim query failed for %r (params=%r)", address, params, exc_info=True)

    # --- Strategy 7b: Nominatim residential street-centerline fallback ---
    # Many residential streets exist in OSM but lack individual address-point
    # data, so Nominatim returns the road segment rather than the building.
    # When the input has a house number and the result matches the expected zip
    # (or state), accept the street centerline as an approximate result.
    # This is substantially more accurate than the zip-code centroid (Strategy 8)
    # — typically < 0.2 mi vs > 0.5 mi from the actual property.
    # Only applied when the input looks like a numbered street address.
    input_has_housenumber = bool(re.match(r"^\s*\d+\s+\S", address))
    if input_has_housenumber and nominatim_queries:
        input_zip = _extract_zip(address)
        for params in nominatim_queries:
            try:
                result = _try_nominatim_street_approx(
                    params, address, expected_state, input_zip
                )
                if result is not None:
                    logger.info(
                        "geocode_address: using Nominatim street-centerline approx for %r → %r",
                        address, result[2],
                    )
                    return result
            except Exception:
                logger.debug(
                    "Nominatim street-approx query failed for %r (params=%r)",
                    address, params, exc_info=True,
                )

    # --- Strategy 7c: Ordinal word → numeric retry ---
    # Many US cities label streets with numeric ordinals ("2nd Ave", "3rd St")
    # but users often type the word form ("Second Ave", "Third St").  OSM data
    # (Nominatim) stores the numeric form, so word-form queries return nothing
    # even when the address exists and resolves perfectly with the numeric form.
    # Example: "5600 Second Ave, Detroit, MI 48202" → "5600 2nd Ave, Detroit, MI 48202"
    # Re-run all Nominatim strategies (unstructured + structured) with the
    # normalised form before falling back to the zip centroid.
    ordinal_address = _normalize_ordinal_street(address)
    if ordinal_address:
        logger.debug(
            "Ordinal street normalisation: %r → %r; retrying Nominatim",
            address, ordinal_address,
        )
        ordinal_queries: list[dict] = []
        q_ordinal_usa = ordinal_address if ordinal_address.upper().endswith("USA") else ordinal_address + ", USA"
        ordinal_queries.append({"q": q_ordinal_usa, "format": "json", "limit": 5, "countrycodes": "us", "addressdetails": 1})

        ordinal_parts = [p.strip() for p in ordinal_address.split(",")]
        if len(ordinal_parts) >= 3:
            state_zip_o = ordinal_parts[2].strip()
            state_part_o = state_zip_o.split()[0] if state_zip_o else ""
            zip_part_o = state_zip_o.split()[1] if len(state_zip_o.split()) > 1 else ""
            structured_o: dict = {
                "street": ordinal_parts[0],
                "city": ordinal_parts[1],
                "state": state_part_o,
                "format": "json",
                "limit": 5,
                "countrycodes": "us",
                "addressdetails": 1,
            }
            if zip_part_o:
                structured_o["postalcode"] = zip_part_o
            ordinal_queries.append(structured_o)
            # Also try without city (unincorporated communities)
            no_city_o: dict = {
                "street": ordinal_parts[0],
                "state": state_part_o,
                "format": "json",
                "limit": 5,
                "countrycodes": "us",
                "addressdetails": 1,
            }
            if zip_part_o:
                no_city_o["postalcode"] = zip_part_o
            ordinal_queries.append(no_city_o)

        for params in ordinal_queries:
            try:
                result = _try_nominatim(params, ordinal_address, expected_state)
                if result is not None:
                    logger.info(
                        "geocode_address: ordinal normalisation resolved %r → %r",
                        address, result[2],
                    )
                    return result
            except Exception:
                logger.debug(
                    "Nominatim ordinal query failed for %r (params=%r)",
                    ordinal_address, params, exc_info=True,
                )

        # Also try residential street-centerline approx with ordinal form
        if input_has_housenumber and ordinal_queries:
            input_zip_o = _extract_zip(ordinal_address)
            for params in ordinal_queries:
                try:
                    result = _try_nominatim_street_approx(
                        params, ordinal_address, expected_state, input_zip_o
                    )
                    if result is not None:
                        logger.info(
                            "geocode_address: ordinal street-centerline approx for %r → %r",
                            address, result[2],
                        )
                        return result
                except Exception:
                    logger.debug(
                        "Nominatim ordinal street-approx failed for %r (params=%r)",
                        ordinal_address, params, exc_info=True,
                    )

    # --- Strategy 8: City/zip centroid fallback ---
    # When the full street address fails all geocoders (common for rural/industrial
    # roads not in public databases), try to resolve just the zip code or city/state.
    # Returns an approximate result so the map can center on the right area.
    result = _geocode_city_state_fallback(address, expected_state)
    if result is not None:
        logger.info(
            "geocode_address: using city/zip centroid fallback for %r → %r", address, result[2]
        )
        return result

    logger.warning("All geocoding strategies failed for %r", address)
    return None


_CENSUS_BATCH_URL = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
_CENSUS_BATCH_SIZE = 9999  # Census limit is 10,000 records per request; use 9999 to be safe
_CENSUS_BATCH_BENCHMARK = "Public_AR_Current"


def batch_geocode_addresses(
    records: list[tuple[str, str, str, str, str]],
) -> dict[str, tuple[float, float]]:
    """Batch geocode up to _CENSUS_BATCH_SIZE addresses using Census Bureau API.

    Args:
        records: list of (id, street, city, state, zip) tuples.
            id must be unique; street/city/state/zip are the address components.
            zip may be empty string.

    Returns:
        dict mapping id -> (lat, lon) for successful matches.
        Unmatched or errored records are omitted.
    """
    if not records:
        return {}

    # Build CSV payload: ID,Street,City,State,Zip
    buf = io.StringIO()
    for rec_id, street, city, state, zip_code in records:
        # Escape any commas in fields
        parts = [
            str(rec_id).replace(",", " "),
            (street or "").replace(",", " "),
            (city or "").replace(",", " "),
            (state or "").replace(",", " "),
            (zip_code or "").replace(",", " "),
        ]
        buf.write(",".join(parts) + "\n")
    csv_bytes = buf.getvalue().encode("utf-8")

    try:
        resp = _http_post(
            _CENSUS_BATCH_URL,
            files={"addressFile": ("addresses.csv", csv_bytes, "text/csv")},
            data={"benchmark": _CENSUS_BATCH_BENCHMARK},
        )
        resp.raise_for_status()
    except Exception as e:
        logger.warning("Census batch geocoder request failed: %s", e)
        return {}

    # Parse CSV response — Census returns quoted fields, some containing commas
    result: dict[str, tuple[float, float]] = {}
    reader = csv.reader(resp.text.splitlines())
    for row in reader:
        if len(row) < 6:
            continue
        rec_id = row[0].strip()
        match_status = row[2].strip()
        coords_str = row[5].strip()
        if match_status.lower() != "match" or not coords_str:
            continue
        try:
            # Census returns "lon,lat" (note: reversed from lat,lon convention)
            lon_str, lat_str = coords_str.split(",")
            lat = float(lat_str.strip())
            lon = float(lon_str.strip())
            if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
                result[rec_id] = (lat, lon)
        except (ValueError, TypeError):
            continue

    return result


# In-memory cache of zip code -> (centroid_lat, centroid_lon).
# Populated lazily by zip_centroid(); persists for the lifetime of the process.
_ZIP_CENTROID_CACHE: dict[str, tuple[float, float] | None] = {}


def warm_zip_centroid_cache(conn) -> int:
    """Pre-warm the zip centroid cache from DB facility coordinates.

    Computes average lat/lon per 5-digit zip from facilities table.  Called at
    API startup so the first radius search doesn't hit the Census geocoder for
    every unique zip code.  Returns count of zips cached.
    """
    rows = conn.execute(
        "SELECT SUBSTR(zip_code, 1, 5) AS zc, AVG(lat) AS avg_lat, AVG(lon) AS avg_lon "
        "FROM facilities "
        "WHERE zip_code IS NOT NULL AND LENGTH(zip_code) >= 5 "
        "AND lat IS NOT NULL AND lon IS NOT NULL "
        "GROUP BY zc"
    ).fetchall()
    count = 0
    for row in rows:
        zc = row[0]
        if zc and len(zc) == 5 and zc.isdigit():
            _ZIP_CENTROID_CACHE[zc] = (float(row[1]), float(row[2]))
            count += 1
    logger.info("Pre-warmed zip centroid cache with %d zips from DB", count)
    return count


def zip_centroid(
    zip_code: str, *, api_fallback: bool = True
) -> tuple[float, float] | None:
    """Return the approximate centroid (lat, lon) for a US zip code.

    Uses the Census geocoder to look up the zip code as a location.  Result is
    cached in `_ZIP_CENTROID_CACHE` so repeated lookups for the same zip are
    free.  Returns None if the zip cannot be geocoded or on API failure.

    When ``api_fallback=False`` the function only consults the in-process cache
    (pre-warmed by ``warm_zip_centroid_cache`` at startup) and returns None for
    any zip code not already in the cache.  Use this in hot paths such as
    ``search_radius`` to avoid blocking Census geocoder calls during requests.
    """
    zc = (zip_code or "").strip()
    if not zc or len(zc) < 5:
        return None

    if zc in _ZIP_CENTROID_CACHE:
        return _ZIP_CENTROID_CACHE[zc]

    if not api_fallback:
        return None

    result: tuple[float, float] | None = None
    try:
        resp = _http_get(
            "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
            params={
                "address": zc,
                "benchmark": "Public_AR_Current",
                "format": "json",
            },
            timeout=5.0,
        )
        resp.raise_for_status()
        matches = resp.json().get("result", {}).get("addressMatches", [])
        if matches:
            coords = matches[0]["coordinates"]
            result = (float(coords["y"]), float(coords["x"]))
    except Exception:
        logger.debug("zip_centroid: Census lookup failed for zip=%r", zc, exc_info=True)

    _ZIP_CENTROID_CACHE[zc] = result
    return result


def reverse_geocode_state(lat: float, lon: float) -> str | None:
    """Return the US state abbreviation for a lat/lon point, or None on failure.

    Uses the Census Bureau reverse geocoder (geography endpoint).  Timeout is
    short (5s) so a slow Census API doesn't block search results — the caller
    falls back to no state filtering on failure.
    """
    try:
        resp = _http_get(
            "https://geocoding.geo.census.gov/geocoder/geographies/coordinates",
            params={
                "x": lon,
                "y": lat,
                "benchmark": "Public_AR_Current",
                "vintage": "Current_Current",
                "format": "json",
            },
            timeout=5.0,
        )
        resp.raise_for_status()
        data = resp.json()
        states = (
            data.get("result", {})
            .get("geographies", {})
            .get("States", [])
        )
        if states:
            abbr = states[0].get("STUSAB", "").upper()
            if abbr in _STATE_ABBREVS:
                return abbr
    except Exception:
        logger.debug("reverse_geocode_state failed for lat=%s lon=%s", lat, lon, exc_info=True)
    return None


def _normalize_county_for_comparison(county: str) -> str:
    """Normalize a county name for mismatch comparison.

    Strips common suffixes ("COUNTY", "PARISH", "BOROUGH", "CENSUS AREA"),
    removes punctuation, and uppercases.  This lets us compare "HARRIS" ==
    "HARRIS COUNTY" == "Harris" without false positives.
    """
    s = county.upper().strip()
    # Remove common administrative suffixes
    for suffix in (" COUNTY", " PARISH", " BOROUGH", " CENSUS AREA", " MUNICIPALITY"):
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
    # Remove non-alphanumeric characters (e.g. "ST. CLAIR" → "ST CLAIR")
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    return " ".join(s.split())


def _normalize_city_for_comparison(city: str) -> str:
    """Normalize a city name for mismatch comparison.

    Uppercases and strips common suffixes used in CDP/incorporated place names
    (e.g. "HOUSTON CITY" → "HOUSTON") so comparison is more robust.
    """
    s = city.upper().strip()
    # Remove non-alphanumeric (keep spaces)
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    return " ".join(s.split())


def reverse_geocode_city_county(
    lat: float, lon: float
) -> tuple[str | None, str | None]:
    """Return (city, county) for a lat/lon point using Census reverse geocoder.

    Queries the Census geography endpoint with Counties and Incorporated Places
    layers.  Returns (None, None) on failure or when coordinates are invalid.

    County is returned as the BASENAME (e.g. "Harris"), not "Harris County".
    City is the NAME from the best matching place (Incorporated Places preferred
    over Census Designated Places).
    """
    if lat is None or lon is None:
        return None, None
    try:
        resp = _http_get(
            "https://geocoding.geo.census.gov/geocoder/geographies/coordinates",
            params={
                "x": lon,
                "y": lat,
                "benchmark": "Public_AR_Current",
                "vintage": "Current_Current",
                "layers": "Counties,Incorporated Places,Census Designated Places",
                "format": "json",
            },
            timeout=8.0,
        )
        resp.raise_for_status()
        data = resp.json()
        geos = data.get("result", {}).get("geographies", {})

        # Extract county
        county: str | None = None
        counties = geos.get("Counties", [])
        if counties:
            county = counties[0].get("BASENAME") or counties[0].get("NAME")
            if county:
                # Strip " County" suffix if present (some vintages include it)
                county = re.sub(r"\s+County$", "", county, flags=re.IGNORECASE).strip()
                county = county.upper() if county else None

        # Extract city — prefer incorporated places over CDPs
        city: str | None = None
        for layer in ("Incorporated Places", "Census Designated Places"):
            places = geos.get(layer, [])
            if places:
                city = places[0].get("BASENAME") or places[0].get("NAME")
                if city:
                    city = city.upper()
                    break

        return city, county
    except Exception:
        logger.debug(
            "reverse_geocode_city_county failed for lat=%s lon=%s", lat, lon, exc_info=True
        )
        return None, None


def geocode_county_by_address(
    address: str | None,
    city: str | None,
    state: str | None,
    zip_code: str | None,
) -> str | None:
    """Look up county for a facility using its street address via Census geocoder.

    Used as a fallback when lat/lon are unavailable.  Calls the Census Bureau
    address geocoder with geographies (Counties layer) to get the authoritative
    county for the given address.

    Returns the county name (uppercased, without "County" suffix) or None if
    the address cannot be geocoded or the response contains no county data.
    """
    if not (address or zip_code) or not state:
        return None
    # Need at least a street address to geocode; fall back to city-only if no street
    street = (address or "").strip()
    if not street:
        return None
    try:
        params: dict = {
            "benchmark": "Public_AR_Current",
            "vintage": "Current_Current",
            "layers": "Counties",
            "format": "json",
        }
        if street:
            params["street"] = street
        if city:
            params["city"] = city
        if state:
            params["state"] = state
        if zip_code:
            params["zip"] = zip_code[:5]  # Census expects 5-digit ZIP

        resp = _http_get(
            "https://geocoding.geo.census.gov/geocoder/geographies/address",
            params=params,
            timeout=8.0,
        )
        resp.raise_for_status()
        data = resp.json()
        matches = data.get("result", {}).get("addressMatches", [])
        if not matches:
            return None
        counties = matches[0].get("geographies", {}).get("Counties", [])
        if not counties:
            return None
        county = counties[0].get("BASENAME") or counties[0].get("NAME")
        if county:
            county = re.sub(r"\s+County$", "", county, flags=re.IGNORECASE).strip()
            return county.upper() if county else None
        return None
    except Exception:
        logger.debug(
            "geocode_county_by_address failed for %r %r %s %s",
            address, city, state, zip_code,
            exc_info=True,
        )
        return None


def correct_city_county(
    facility_id: str,
    city: str | None,
    county: str | None,
    lat: float | None,
    lon: float | None,
    address: str | None = None,
    zip_code: str | None = None,
    state: str | None = None,
) -> tuple[str | None, str | None]:
    """Validate and correct city/county against reverse-geocoded coordinates.

    When lat/lon are available, fetches the authoritative city and county from
    the Census reverse geocoder.  If the stated city or county disagrees with
    the geocoded value (after normalization), the geocoded value wins.

    When lon is None but lat is available, falls back to address-based geocoding
    (Census address geocoder with geography layers) to correct the county field.
    City correction is skipped in this fallback path.

    Logs corrections at INFO level.  Returns (city, county) — either corrected
    or unchanged.  Returns the original values unchanged when no geocoding path
    succeeds or when the geocoder returns nothing.
    """
    geo_city: str | None = None
    geo_county: str | None = None

    if lat is not None and lon is not None:
        # Primary path: reverse geocode lat/lon
        geo_city, geo_county = reverse_geocode_city_county(lat, lon)
    elif lat is not None and lon is None and address and state:
        # Fallback path: facility has a latitude but is missing longitude.
        # Geocode the street address to get county from Census geography endpoint.
        # (Only corrects county — city correction requires a coordinate reverse lookup.)
        # Restricted to facilities with at least lat, to avoid geocoding fully
        # unlocated records (lat=None lon=None) which are too uncertain.
        geo_county = geocode_county_by_address(address, city, state, zip_code)

    if geo_city is None and geo_county is None:
        return city, county

    new_city = city
    new_county = county

    # Validate county
    if geo_county and county:
        stated_norm = _normalize_county_for_comparison(county)
        geo_norm = _normalize_county_for_comparison(geo_county)
        if stated_norm != geo_norm:
            new_county = geo_county
    elif geo_county and not county:
        # Fill in missing county from geocoder
        new_county = geo_county

    # Validate city
    if geo_city and city:
        stated_norm = _normalize_city_for_comparison(city)
        geo_norm = _normalize_city_for_comparison(geo_city)
        if stated_norm != geo_norm:
            new_city = geo_city
    elif geo_city and not city:
        # Fill in missing city from geocoder
        new_city = geo_city

    # Log corrections
    city_changed = new_city != city
    county_changed = new_county != county
    if city_changed or county_changed:
        logger.info(
            "City/county corrected for %s: city %r->%r, county %r->%r",
            facility_id,
            city,
            new_city,
            county,
            new_county,
        )

    return new_city, new_county


def bbox_deltas(center_lat: float, radius_miles: float) -> tuple[float, float]:
    """Compute lat/lon deltas for a bounding-box pre-filter around a point."""
    delta_lat = radius_miles / 69.0
    delta_lon = radius_miles / (69.0 * max(math.cos(math.radians(center_lat)), 0.01))
    return delta_lat, delta_lon


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in miles between two lat/lon points."""
    R = 3959.0  # Earth radius in miles
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters between two lat/lon points."""
    R = 6371000.0  # Earth radius in meters
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))
