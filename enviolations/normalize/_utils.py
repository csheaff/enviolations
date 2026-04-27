"""Shared mapper utility functions.

Extracted from per-mapper helpers to reduce duplication across 58 mappers.
"""

from __future__ import annotations

import re as _re_zip
from datetime import date, datetime, timezone

# Matches a 5-digit zip code (optionally followed by -XXXX) anywhere in a string.
_ZIP_FROM_ADDR_RE = _re_zip.compile(r"\b(\d{5})(?:-\d{4})?\b")


def extract_zip_from_address(address: str | None) -> str | None:
    """Extract a 5-digit zip code from an address string using regex.

    Useful when a source provides full address strings (e.g. '123 Main St,
    Chicago, IL 60601') but no parsed zip field.  Returns the first match or
    None.
    """
    if not address:
        return None
    m = _ZIP_FROM_ADDR_RE.search(address)
    return m.group(1) if m else None


def clean(val, *, sentinel: bool = False, normalize_ws: bool = False) -> str | None:
    """Strip whitespace, return None for empty.

    sentinel=True also filters common placeholder strings
    (NAN, NA, NONE, NULL, N/A).
    normalize_ws=True collapses internal whitespace runs to single spaces.
    """
    if val is None:
        return None
    if normalize_ws:
        val = " ".join(str(val).split())
    else:
        val = str(val).strip()
    if not val:
        return None
    if sentinel and val.upper() in ("NAN", "NA", "NONE", "NULL", "N/A"):
        return None
    return val


def parse_float(val, *, zero_as_none: bool = False) -> float | None:
    """Safe float parse. Returns None for NaN values.

    zero_as_none=True treats zero-like values as None (coordinate convention
    where 0,0 means "no data" for US facilities). Catches both exact 0.0 and
    near-zero floating point artifacts (e.g. 5.68e-14) that ArcGIS returns
    when a feature has no geometry in the requested projection (CIV-796).
    """
    if val is None:
        return None
    try:
        f = float(val)
        if f != f:  # NaN
            return None
        if zero_as_none and abs(f) < 1e-9:
            return None
        return f
    except (ValueError, TypeError):
        return None


_MAX_VALID_YEAR = date.today().year + 5


def parse_date(val, formats: tuple[str, ...] = ("%Y-%m-%d",)) -> date | None:
    """Try each format in order, return first match or None.

    Dates more than 5 years in the future are treated as sentinel/placeholder
    values (e.g. 3000-12-31 from EPA CAA) and returned as None.
    """
    if not val:
        return None
    val = str(val).strip()
    for fmt in formats:
        try:
            d = datetime.strptime(val, fmt).date()
            if d.year > _MAX_VALID_YEAR:
                return None
            return d
        except (ValueError, TypeError):
            continue
    # Fallback: fromisoformat (handles 2024-01-15T00:00:00 etc.)
    try:
        d = datetime.fromisoformat(val.replace("Z", "+00:00")).date()
        if d.year > _MAX_VALID_YEAR:
            return None
        return d
    except (ValueError, TypeError):
        return None


def epoch_ms_to_date(val) -> date | None:
    """Convert ArcGIS epoch-milliseconds to a date.

    Returns None for epoch 0, which ArcGIS uses to mean "no data".
    """
    if val is None:
        return None
    try:
        ms = int(val)
        if ms == 0:
            return None
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date()
    except (ValueError, TypeError, OSError):
        return None


def extract_arcgis_coords(feature: dict) -> tuple[float | None, float | None]:
    """Pull (lat, lon) from ArcGIS feature geometry dict (outSR=4326)."""
    geom = feature.get("geometry")
    if not geom:
        return None, None
    lon = parse_float(geom.get("x"), zero_as_none=True)
    lat = parse_float(geom.get("y"), zero_as_none=True)
    return lat, lon


import re as _re

_NAICS_SPACE_SEP_RE = _re.compile(r"^\d{4,7}(?:\s+\d{4,7})+$")
_NAICS_BARE_TOKEN_RE = _re.compile(r"^\d{4,7}$")

# Known city name misspellings in CA state source data (case-insensitive lookup).
# Keys are lowercase misspellings; values are the correct spellings.
# "Wilminton" (missing 'g') appears in ca_geotracker and ca_dtsc records for
# the Wilmington neighborhood of Los Angeles (CIV-652).
_CA_CITY_CORRECTIONS: dict[str, str] = {
    "wilminton": "Wilmington",
}


def normalize_ca_city(city: str | None) -> str | None:
    """Correct known CA city name misspellings found in state source data.

    Applies a small lookup table of confirmed typos. Only corrects exact
    matches (case-insensitive); does not attempt fuzzy correction.
    """
    if not city:
        return city
    corrected = _CA_CITY_CORRECTIONS.get(city.strip().lower())
    return corrected if corrected is not None else city


def normalize_naics_codes(val: str | None) -> str | None:
    """Pad 5-digit NAICS codes to 6 digits by appending a trailing zero.

    Valid NAICS codes are 6 digits in the US. Some sources (EPA ECHO, TCEQ,
    RCRA) occasionally omit the trailing zero, storing e.g. '49311' instead
    of '493110'. This function pads any exactly-5-digit numeric token to 6
    digits. Codes of other lengths are left unchanged.

    Handles both space-separated tokens (EPA ECHO: '49311 56221') and
    comma-separated entries (TCEQ: '21111,211111 - Natural Gas').
    """
    if not val:
        return val

    # Detect delimiter: prefer comma if present, else space
    if "," in val:
        sep = ","
        parts = val.split(",")
    else:
        sep = " "
        parts = val.split()

    padded = []
    for part in parts:
        stripped = part.strip()
        # Extract leading numeric code (may be followed by " - Description")
        m = _re.match(r"^(\d{5})(\s*-.*)?$", stripped)
        if m:
            code = m.group(1) + "0"
            suffix = m.group(2) or ""
            padded.append(code + suffix)
        else:
            padded.append(stripped)

    if sep == ",":
        # Rejoin with comma, preserving original spacing
        return ",".join(padded)
    return " ".join(padded)
