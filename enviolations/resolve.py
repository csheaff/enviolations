"""Cross-source facility entity resolution.

Matches state-source facilities to their EPA counterparts by address,
proximity, and name. Matched facilities share a canonical_id, enabling
the unified view to merge data across sources.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from datetime import datetime, timezone
from functools import lru_cache

from rapidfuzz import fuzz

from .geo import haversine_meters as _haversine_meters

logger = logging.getLogger(__name__)

_scourgify_warned = False

# Sources with more facilities than this threshold are skipped in the
# same-source geo+address dedup pass.  The O(n²) pair-checking becomes
# prohibitively expensive (hours + GBs of memory) for large permit
# databases like nm_nmed (~2.1 M records).  These sources have unique
# permit IDs per record, so same-source dedup adds no value anyway.
_SAME_SOURCE_SKIP_THRESHOLD = 100_000

# Sources that ALWAYS run same-source dedup even when they exceed the skip
# threshold.  Add a source here only when it has multiple overlapping ID
# namespaces that generate duplicate physical-facility records at the same
# location (e.g. nj_dep uses both 'kcsl-*' and 'njems-*' IDs for the
# same physical sites).
_SAME_SOURCE_FORCE_DEDUP: frozenset[str] = frozenset({"nj_dep"})

# When running the exact-coordinate dedup pass for large sources (those that
# exceed _SAME_SOURCE_SKIP_THRESHOLD), only process coord groups with this
# many or fewer facilities.  Groups larger than this cap are almost certainly
# different entities sharing a management-office or law-firm address (e.g. 52
# Texas MUDs all registered at their shared counsel's address), not genuine
# duplicates.  Small groups (2–5) are the typical case for data-entry typos.
_EXACT_COORD_MAX_GROUP = 5

# EPA sources that have RegistryIDs and violation data
EPA_SOURCES = ("epa_echo", "epa_rcra", "epa_caa", "epa_sdwa", "epa_sems")

_EPA_IN_CLAUSE = ",".join(f"'{s}'" for s in EPA_SOURCES)

# State-level sources mapped to their home state.
# Used to prevent cross-state false matches in entity resolution.
STATE_SOURCE_MAP: dict[str, str] = {
    "tceq": "TX",
    "pa_dep": "PA",
    "pa_dep_gis": "PA",
    "oh_epa": "OH",
    "ny_dec": "NY",
    "ny_dec_gis": "NY",
    "fl_dep": "FL",
    "fl_dep_stcm": "FL",
    "fl_dep_chaz": "FL",
    "fl_dep_arms": "FL",
    "fl_dep_bf": "FL",
    "fl_dep_waste": "FL",
    "il_epa": "IL",
    "ca_dtsc": "CA",
    "ca_waterboard": "CA",
    "ca_geotracker": "CA",
    "nj_dep": "NJ",
    "mi_egle": "MI",
    "nc_deq": "NC",
    "ga_epd": "GA",
    "wa_ecy": "WA",
    "ma_dep": "MA",
    "va_deq": "VA",
    "co_cdphe": "CO",
    "az_deq": "AZ",
    "mn_pca": "MN",
    "la_deq": "LA",
    "mo_dnr": "MO",
    "md_mde": "MD",
    "in_idem": "IN",
    "sc_des": "SC",
    "tn_tdec": "TN",
    "ks_kdhe": "KS",
    "or_deq": "OR",
    "wi_dnr": "WI",
    "ct_deep": "CT",
    "al_adem": "AL",
    "ok_deq": "OK",
    "ia_dnr": "IA",
    "ky_dep": "KY",
    "ut_deq": "UT",
    "nv_dep": "NV",
    "ar_deq": "AR",
    "de_dnrec": "DE",
    "ms_mdeq": "MS",
    "wv_dep": "WV",
    "nm_nmed": "NM",
    "ne_dee": "NE",
    "me_dep": "ME",
    "nh_des": "NH",
    "nd_deq": "ND",
    "sd_danr": "SD",
    "mt_deq": "MT",
    "id_deq": "ID",
    "vt_dec": "VT",
    "wy_deq": "WY",
    "ak_dec": "AK",
    "ri_dem": "RI",
    "hi_doh": "HI",
    "dc_doee": "DC",
    "mi_pfas": "MI",
    "nj_pfas": "NJ",
    "wi_pfas": "WI",
    "oh_pfas": "OH",
    "il_pfas": "IL",
}

# Reverse mapping: state -> set of sources that belong to that state
_STATE_ALLOWED_SOURCES: dict[str, set[str]] = {}
for _src, _st in STATE_SOURCE_MAP.items():
    _STATE_ALLOWED_SOURCES.setdefault(_st, set()).add(_src)


class UnionFind:
    """Disjoint-set (Union-Find) with path compression and priority-based union.

    Used in entity resolution to efficiently merge facility clusters.
    priority_fn(a, b) returns whichever element should be the root.
    """

    def __init__(self, priority_fn=None):
        self.parent: dict[str, str] = {}
        self.priority_fn = priority_fn or (lambda a, b: a)

    def find(self, x: str) -> str:
        if x not in self.parent:
            self.parent[x] = x
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        # priority_fn returns whichever should be root
        if self.priority_fn(ra, rb) == rb:
            ra, rb = rb, ra
        self.parent[rb] = ra

    def get_groups(self) -> dict[str, list[str]]:
        """Return all elements grouped by their root."""
        groups: dict[str, list[str]] = {}
        for x in self.parent:
            root = self.find(x)
            groups.setdefault(root, []).append(x)
        return groups


def _usps_abbreviate(addr: str) -> str:
    """Apply USPS Pub 28 abbreviations and normalization to a lowercased address."""
    addr = re.sub(r"\s+", " ", addr)
    addr = re.sub(r"\bstreet\b", "st", addr)
    addr = re.sub(r"\bavenue\b", "ave", addr)
    addr = re.sub(r"\bboulevard\b", "blvd", addr)
    addr = re.sub(r"\bdrive\b", "dr", addr)
    addr = re.sub(r"\broad\b", "rd", addr)
    addr = re.sub(r"\blane\b", "ln", addr)
    addr = re.sub(r"\bcourt\b", "ct", addr)
    addr = re.sub(r"\bcircle\b", "cir", addr)
    addr = re.sub(r"\bplace\b", "pl", addr)
    addr = re.sub(r"\bterrace\b", "ter", addr)
    addr = re.sub(r"\bparkway\b", "pkwy", addr)
    addr = re.sub(r"\bhighway\b", "hwy", addr)
    addr = re.sub(r"\bnorth\b", "n", addr)
    addr = re.sub(r"\bsouth\b", "s", addr)
    addr = re.sub(r"\beast\b", "e", addr)
    addr = re.sub(r"\bwest\b", "w", addr)
    addr = re.sub(r"\bnortheast\b", "ne", addr)
    addr = re.sub(r"\bnorthwest\b", "nw", addr)
    addr = re.sub(r"\bsoutheast\b", "se", addr)
    addr = re.sub(r"\bsouthwest\b", "sw", addr)
    addr = re.sub(r"\bsuite\b", "ste", addr)
    addr = re.sub(r"[.,#]", "", addr)
    # Strip ordinal suffixes from number tokens: "20th" → "20", "1st" → "1"
    addr = re.sub(r"\b(\d+)(?:st|nd|rd|th)\b", r"\1", addr)
    # Non-standard directional abbreviations: WST → W (West variant in EPA/state data)
    addr = re.sub(r"\bwst\b", "w", addr)
    return addr.strip()


# Trailing directionals that state sources sometimes omit when EPA includes them.
# E.g. "2100 LOUISIANA BLVD NE" (EPA) vs "2100 Louisiana Blvd." (nm_nmed).
# Used in Tier 1 fallback matching.
_TRAILING_DIRECTIONAL_RE = re.compile(
    r"\s+(?:ne|nw|se|sw|n|s|e|w)\s*$", re.IGNORECASE
)


def _strip_trailing_directional(norm_addr: str) -> str:
    """Strip a trailing directional suffix from a normalized address.

    Returns the stripped address, or the original if no trailing directional found.
    Only strips when the directional is the last token (after a space), so
    "500 w main st" is unchanged but "2100 louisiana blvd ne" → "2100 louisiana blvd".
    """
    stripped = _TRAILING_DIRECTIONAL_RE.sub("", norm_addr).strip()
    return stripped if stripped != norm_addr else norm_addr


# Trailing street-type suffixes that one source may include while another omits.
# E.g. "1411 E POMONA ST" (EPA) vs "1411 E. Pomona" (ca_dtsc).
# Applied after _usps_abbreviate, so only abbreviated forms need matching.
#
# Tradeoff: ~0.2% of EPA addresses in the same city share the same stripped form
# with different suffixes (e.g., "905 S Hunt Rd" vs "905 S Hunt St" in Terre Haute).
# 4,472 collisions out of ~2M EPA facilities. A false-positive merge requires a state
# source to also lack a suffix at one of these collision addresses — very unlikely.
# Many collisions are actually the same facility with variant addresses. Net gain
# (catching legitimate suffix mismatches like ca_dtsc/epa_echo) far outweighs the
# edge-case risk. Tier 2/3 provide additional cross-checks. See CIV-552.
_TRAILING_STREET_SUFFIX_RE = re.compile(
    r"\s+(?:st|ave|blvd|dr|rd|ln|ct|cir|pl|ter|pkwy|hwy|way)\s*$"
)


def _strip_trailing_street_suffix(norm_addr: str) -> str:
    """Strip a trailing street-type suffix from a normalized address.

    Returns the stripped address, or the original if no suffix found.
    E.g. "1411 e pomona st" → "1411 e pomona", but "500 w main" unchanged.
    """
    stripped = _TRAILING_STREET_SUFFIX_RE.sub("", norm_addr).strip()
    return stripped if stripped != norm_addr else norm_addr


# Leading directional that one source includes while the other omits.
# E.g. "4335 EAST VALLEY BOULEVARD" (ca_waterboard) vs "4335 VALLEY BOULEVARD" (EPA).
# After normalization: "4335 e valley blvd" vs "4335 valley blvd".
# Used in Tier 1 fallback matching. The directional must immediately follow
# the house number (e.g., "4335 e valley blvd" → strip "e " → "4335 valley blvd").
# SLC-style addresses ("165 s 800 w") produce non-address stripped forms ("165 800 w")
# that won't match any real EPA address, so false-positive risk is minimal.
_LEADING_DIRECTIONAL_RE = re.compile(
    r"^(\d+\s+)(?:ne|nw|se|sw|n|s|e|w)\s+", re.IGNORECASE
)


def _strip_leading_directional(norm_addr: str) -> str:
    """Strip a leading directional after the house number.

    Returns the stripped address, or the original if no leading directional found.
    Only strips when a single directional token (N/S/E/W/NE/NW/SE/SW) immediately
    follows the house number, so "4335 e valley blvd" → "4335 valley blvd" but
    "500 w main st" also → "500 main st".

    Used as a Tier 1 fallback when the state source includes a directional prefix
    that the EPA data omits (or vice versa). CIV-619.
    """
    m = _LEADING_DIRECTIONAL_RE.match(norm_addr)
    if not m:
        return norm_addr
    stripped = _LEADING_DIRECTIONAL_RE.sub(m.group(1), norm_addr, count=1)
    return stripped if stripped != norm_addr else norm_addr


def _normalize_intersection_addr(addr: str) -> str:
    """Canonicalize an intersection address by sorting the street parts.

    Sorts all ` & `-delimited segments alphabetically so that reversed
    orderings like "Main St & Broadway" and "Broadway & Main St" produce
    the same normalized form ("broadway & main st").

    Assumes addr has already been lowercased and run through _usps_abbreviate.
    """
    parts = [p.strip() for p in addr.split(" & ")]
    parts.sort()
    return " & ".join(parts)


@lru_cache(maxsize=500_000)
def normalize_address(addr: str | None) -> str:
    """Normalize a street address for comparison.

    Uses usaddress-scourgify for USPS Pub 28 standardization when available,
    then always applies regex-based USPS abbreviation normalization as a
    consistent post-processing step. This ensures LANE→LN, ROAD→RD, etc.
    are applied regardless of whether scourgify is available.

    Intersection addresses (containing ` & `) bypass scourgify because
    scourgify strips the second street name entirely (e.g. "Main St &
    Broadway" → "main st &").  Instead they are normalized with the regex
    fallback and then canonicalized by sorting the parts so that reversed
    orderings ("Main St & Broadway" vs "Broadway & Main St") resolve to the
    same form.  CIV-659.
    """
    if not addr:
        return ""
    # Intersection addresses: bypass scourgify (it mangles them) and
    # canonicalize by sorting parts so reversed orderings match.
    if " & " in addr:
        return _normalize_intersection_addr(_usps_abbreviate(addr.lower().strip()))
    try:
        from scourgify import normalize_address_record

        result = normalize_address_record(addr)
        parts = [result.get("address_line_1", ""), result.get("address_line_2", "")]
        normalized = " ".join(p for p in parts if p).lower().strip()
        return _usps_abbreviate(normalized)
    except Exception:
        global _scourgify_warned
        if not _scourgify_warned:
            logger.debug("scourgify unavailable, using regex fallback for address normalization")
            _scourgify_warned = True
        return _usps_abbreviate(addr.lower().strip())


@lru_cache(maxsize=500_000)
def normalize_name(name: str | None) -> str | None:
    """Normalize a facility name for comparison.

    Strips parenthetical qualifiers, normalizes corporate-suffix variants,
    removes apostrophes/possessives, strips commas, replaces hyphens/dashes
    with spaces, and extracts DBA trade names so that names differing only
    in these ways compare as equal.

    Commas are stripped so that "MOTCO, INC." tokenizes as {"motco", "inc"}
    rather than {"motco,", "inc"}, enabling token-based matching against
    names like "MOTCO TRUST GROUP".

    Hyphens and dashes (ASCII hyphen, en dash, em dash) are replaced with
    spaces so that "HI-Quality Cleaners" tokenizes identically to
    "HI Quality Cleaners", giving Jaccard = 1.0 instead of 0.25.

    Slashes are replaced with spaces so that geographic qualifiers appended
    via slash (e.g. "NORTHEAST WPCP/PHILA") tokenize identically to
    space-separated forms ("NORTHEAST WPCP PHILA"), enabling prefix matching
    and name similarity checks to correctly recognize that "NORTHEAST WPCP"
    is a prefix of "NORTHEAST WPCP/PHILA". CIV-707.

    Examples:
      "Signal Hill Terminal (Groundwater remediation)" → "Signal Hill Terminal"
      "Signal Hill Terminal Corporation"               → "Signal Hill Terminal Corp"
      "Signal Hill Terminal Corp."                     → "Signal Hill Terminal Corp"
      "Acme Inc."                                      → "Acme Inc"
      "Acme Incorporated"                              → "Acme Inc"
      "Danny's Automotive"                             → "Dannys Automotive"
      "DANNYS AUTOMOTIVE"                              → "DANNYS AUTOMOTIVE"
      "Ryan Bloom Inc DBA OC Recycling"               → "OC Recycling"
      "MOTCO, INC."                                    → "MOTCO Inc"
      "HI-Quality Cleaners"                            → "HI Quality Cleaners"
      "NORTHEAST WPCP/PHILA"                           → "NORTHEAST WPCP PHILA"
    """
    if not name:
        return name
    # Extract DBA trade name: "RYAN BLOOM INC DBA OC RECYCLING" → "OC RECYCLING"
    # The trade name (after DBA) is the public-facing identity used for search.
    # Require at least 2 words before "DBA" to avoid treating "NO DBA HERE"
    # as a DBA construct (where "NO" is a 1-word company name).
    dba_parts = re.split(r"\bdba\b", name, maxsplit=1, flags=re.IGNORECASE)
    if len(dba_parts) == 2 and len(dba_parts[0].split()) >= 2 and dba_parts[1].strip():
        name = dba_parts[1].strip()
    # Strip parenthetical qualifiers: "Foo (bar baz)" → "Foo"
    name = re.sub(r"\s*\([^)]*\)\s*", " ", name).strip()
    # Remove apostrophes/possessives: "Danny's" → "Dannys", "O'Brien" → "OBrien"
    name = name.replace("'", "").replace("\u2019", "")
    # Strip commas: "MOTCO, INC." → "MOTCO INC." so token matching works correctly.
    # Without this, "MOTCO," is a distinct token from "MOTCO" and Jaccard/prefix
    # matching fails even when the names clearly refer to the same company.
    name = name.replace(",", "")
    # Normalize spaces around ampersand: "L & N" → "L&N" so that spacing variants
    # tokenize identically (CIV-492). Without this, "L&N" is one token {"l&n"} but
    # "L & N" is three tokens {"l", "&", "n"} — Jaccard = 0.0.
    name = re.sub(r"\s*&\s*", "&", name)
    # Expand WWTP abbreviation so abbreviated and full-form names compare as similar (CIV-416).
    name = re.sub(r"\bWWTP\b", "WASTEWATER TREATMENT PLANT", name, flags=re.IGNORECASE)
    # Replace hyphens and dashes with spaces: "HI-Quality" → "HI Quality"
    # ASCII hyphen, en dash (U+2013), em dash (U+2014) all act as word separators
    # in facility names. Without this, "HI-Quality" is a single token that fails
    # Jaccard matching against the two-token "HI Quality".
    name = re.sub(r"[-\u2013\u2014]", " ", name)
    # Replace slashes with spaces: "NORTHEAST WPCP/PHILA" → "NORTHEAST WPCP PHILA"
    # Slashes in facility names typically delimit geographic qualifiers (e.g.
    # /PHILA, /ALISO) or site-division markers. Treating them as word separators
    # allows "NORTHEAST WPCP" to prefix-match "NORTHEAST WPCP/PHILA" and
    # increases token Jaccard from 0.33 → 0.67. CIV-707.
    name = name.replace("/", " ")
    # Normalize corporate suffixes (order matters: longer first)
    name = re.sub(r"\bcorporation\b\.?", "Corp", name, flags=re.IGNORECASE)
    name = re.sub(r"\bcorp\.", "Corp", name, flags=re.IGNORECASE)
    name = re.sub(r"\bincorporated\b\.?", "Inc", name, flags=re.IGNORECASE)
    name = re.sub(r"\binc\.", "Inc", name, flags=re.IGNORECASE)
    name = re.sub(r"\blimited\b\.?", "Ltd", name, flags=re.IGNORECASE)
    name = re.sub(r"\bltd\.", "Ltd", name, flags=re.IGNORECASE)
    name = re.sub(r"\bllc\.", "LLC", name, flags=re.IGNORECASE)
    # Collapse any extra whitespace from removals
    name = re.sub(r"\s+", " ", name).strip()
    return name


def token_jaccard(a: str | None, b: str | None) -> float:
    """Compute Jaccard similarity on word tokens.

    Applies normalize_name() before tokenizing so that corporate-suffix
    variants (Corp./Corporation, Inc./Incorporated) and parenthetical
    qualifiers do not prevent a match.
    """
    a = normalize_name(a)
    b = normalize_name(b)
    if not a or not b:
        return 0.0
    tokens_a = set(a.lower().split())
    tokens_b = set(b.lower().split())
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


def name_similarity(
    a: str | None, b: str | None, *, score_cutoff: float = 0.0
) -> float:
    """Character-level similarity using rapidfuzz.

    Applies normalize_name() before comparison so that parenthetical
    qualifiers (e.g. "(Groundwater remediation)") and corporate-suffix
    variants (Corp./Corporation) do not prevent a match.

    Better than token Jaccard for misspellings (e.g. Paramount vs Paramont).

    Args:
        score_cutoff: If set >0, returns 0.0 immediately when the score
            cannot reach this threshold (rapidfuzz early-exit optimisation).
    """
    a = normalize_name(a)
    b = normalize_name(b)
    if not a or not b:
        return 0.0
    # rapidfuzz.fuzz.ratio returns 0-100; divide by 100 to match old 0-1 scale.
    return fuzz.ratio(a.lower(), b.lower(), score_cutoff=score_cutoff * 100) / 100


def name_is_prefix_match(a: str | None, b: str | None, min_chars: int = 5) -> bool:
    """Return True if one name is a word-boundary prefix of the other.

    Used as a merge signal in same-address dedup for shorthand/substring
    name variants (e.g. "WILLIAMS" as a shorthand for "WILLIAMS OIL GATHERING,
    LLC" at the same address). Applies normalize_name() before comparison.

    Requires the shorter name to be at least `min_chars` characters to
    avoid false positives from very short abbreviations.

    Examples:
      ("WILLIAMS", "WILLIAMS OIL GATHERING, LLC") → True
      ("OC Recycling", "OC Recycling Supply Co")  → True
      ("A", "ABC Corp")                            → False  (too short)
    """
    a = normalize_name(a)
    b = normalize_name(b)
    if not a or not b:
        return False
    al = a.lower()
    bl = b.lower()
    short = al if len(al) <= len(bl) else bl
    long_ = bl if len(al) <= len(bl) else al
    if len(short) < min_chars:
        return False
    # Word-boundary prefix: long starts with short followed by space or end-of-string
    return long_.startswith(short + " ") or long_ == short


def _normalize_city(city: str | None) -> str:
    """Normalize city name for blocking.

    Strips common municipal suffixes (BORO, BOROUGH, TWP, TOWNSHIP, VILLAGE,
    CITY, TOWN), parenthetical qualifiers, and fixes known EPA RCRA
    truncations (20-char limit) so entity resolution groups facilities
    from the same city.

    Also applies NJ neighborhood-to-municipality aliases so that EPA sources
    using neighborhood names (e.g. "Port Reading", "Avenel") match NJEMS
    records using the official municipality name (e.g. "Woodbridge").
    """
    if not city:
        return ""
    city = re.sub(r"\s+", " ", city.lower().strip())
    # Strip parenthetical qualifiers: "rancho dominguez (subdivision)" → "rancho dominguez"
    city = re.sub(r"\s*\([^)]*\)\s*", " ", city).strip()
    city = re.sub(r"\s+(boro|borough|twp|township|village|city|town)$", "", city)
    # Fix known EPA RCRA 20-char truncations in launch states
    city = _EPA_CITY_CORRECTIONS.get(city, city)
    # Map NJ neighborhood/section names to their parent municipality
    city = _NJ_NEIGHBORHOOD_TO_MUNICIPALITY.get(city, city)
    return city


# Known EPA RCRA city name truncations and misspellings (20-char limit).
# These are sourced from actual data quality issues found in launch states.
_EPA_CITY_CORRECTIONS: dict[str, str] = {
    # CA truncations
    "east rancho domingue": "rancho dominguez",
    "rncho domingz": "rancho dominguez",
    "rancho domingz": "rancho dominguez",
    "south san francisc": "south san francisco",
    "north hills (los an": "north hills",
    "city of industry": "industry",
    # NJ truncations
    "north bergen (town)": "north bergen",
    # TX truncations
    "north richland hill": "north richland hills",
}

# NJ neighborhood/section-to-municipality aliases.
# Many NJ towns have named sections that appear as "cities" in EPA data
# but are legally parts of a parent township. NJEMS uses the official
# municipality name (e.g. "WOODBRIDGE TWP" → "Woodbridge") while EPA
# sources use the neighborhood name (e.g. "Port Reading", "Avenel").
# This table maps neighborhood names to their parent municipality so
# that entity resolution Tier 1 address matching works across sources.
_NJ_NEIGHBORHOOD_TO_MUNICIPALITY: dict[str, str] = {
    # Woodbridge Township sections
    "port reading": "woodbridge",
    "avenel": "woodbridge",
    "colonia": "woodbridge",
    "fords": "woodbridge",
    "hopelawn": "woodbridge",
    "iselin": "woodbridge",
    "keasbey": "woodbridge",
    "menlo park": "woodbridge",
    "oak tree": "woodbridge",
    "sewaren": "woodbridge",
    # Piscataway Township sections
    "new market": "piscataway",
    "possumtown": "piscataway",
    "stelton": "piscataway",
    # Edison Township sections
    "clara barton": "edison",
    "metuchen": "metuchen",  # Metuchen is its own borough, not Edison
    # Hamilton Township (Mercer County) sections
    "yardville": "hamilton",
    "mercerville": "hamilton",
    "groveville": "hamilton",
    # Evesham Township sections
    "marlton": "evesham",
    "voorhees": "voorhees",  # Voorhees is its own township
    # Brick Township sections
    "laurelton": "brick",
    "cedar bridge": "brick",
    # Old Bridge Township sections
    "sayreville": "sayreville",  # Sayreville is its own borough
    "parlin": "old bridge",
    "cliffwood": "old bridge",
    "madison park": "old bridge",
    "laurence harbor": "old bridge",
    "morgan": "old bridge",
    "matawan": "matawan",  # Matawan is its own borough
}


# ---------------------------------------------------------------------------
# Known EPA FRS RegistryID merge pairs (CIV-418 and similar).
#
# Some facilities have DIFFERENT RegistryIDs in SEMS vs ECHO/RCRA because
# the EPA FRS assigned multiple IDs to the same physical site. Neither
# proximity (site coordinates differ by >500m) nor fuzzy name matching
# (names are too dissimilar) can catch these programmatically without
# generating false positives.
#
# Format: (sems_source_id, canonical_source_id) where canonical_source_id
# is the epa_echo or epa_rcra RegistryID to use as the canonical entity.
# The SEMS record will be merged into the canonical record's cluster.
#
# Only add entries here after confirming in the EPA FRS or Superfund site
# records that both IDs refer to the same physical facility.
# ---------------------------------------------------------------------------
_KNOWN_EPA_REGISTRY_MERGES: list[tuple[str, str]] = [
    # MOTCO, INC. (SEMS 110009320804) → MOTCO TRUST GROUP (ECHO/RCRA 110022435587)
    # La Marque, TX. SEMS coordinates reference "JCT HIGHWAYS 3 6 & 75" (2.2km away).
    # CIV-418: without this link, NPL floor is not applied to the ECHO entity.
    ("110009320804", "110022435587"),
]


def resolve_epa_overrides(conn: sqlite3.Connection) -> dict:
    """Apply known EPA FRS RegistryID merge pairs from _KNOWN_EPA_REGISTRY_MERGES.

    Some NPL Superfund sites have two different RegistryIDs in SEMS vs ECHO/RCRA
    because the EPA FRS assigned multiple IDs to the same physical site. The
    automated geo+name passes can't catch these without generating false positives
    (distance >500m, name similarity too low). This function applies manually
    curated pairs as a pre-pass before the fuzzy matcher runs.

    The SEMS record is updated to point at the canonical (ECHO-priority) ID.
    Tier 0 / score 1.0 marks it as an exact known match.
    """
    now = datetime.now(timezone.utc).isoformat()
    applied = 0
    for sems_id, canonical_id in _KNOWN_EPA_REGISTRY_MERGES:
        # Verify both IDs exist in facility_matches before merging
        sems_row = conn.execute(
            "SELECT canonical_id FROM facility_matches WHERE source = 'epa_sems' AND source_id = ?",
            (sems_id,),
        ).fetchone()
        canonical_row = conn.execute(
            "SELECT canonical_id FROM facility_matches WHERE source_id = ? "
            "AND source IN ('epa_echo', 'epa_rcra')",
            (canonical_id,),
        ).fetchone()
        if not sems_row or not canonical_row:
            logger.debug(
                "resolve_epa_overrides: skipping %s → %s (one or both not in facility_matches)",
                sems_id,
                canonical_id,
            )
            continue
        if sems_row["canonical_id"] == canonical_id:
            continue  # already merged
        conn.execute(
            "UPDATE facility_matches SET canonical_id = ?, match_tier = 0, "
            "match_score = 1.0, matched_at = ? "
            "WHERE source = 'epa_sems' AND source_id = ?",
            (canonical_id, now, sems_id),
        )
        applied += 1
        logger.info(
            "resolve_epa_overrides: merged SEMS %s → canonical %s", sems_id, canonical_id
        )
    if applied:
        conn.commit()
    return {"epa_override_matches": applied}


# ---------------------------------------------------------------------------
# Known same-source merge pairs for large state sources (CIV-653 and similar).
#
# Some state agencies (e.g. TCEQ) issue separate permit IDs to structures on
# the same physical property (hotel + parking garage, main plant + outbuilding).
# These cannot be caught by resolve_same_source() because that pass is skipped
# for sources exceeding _SAME_SOURCE_SKIP_THRESHOLD (100K records).
#
# Format: (source, source_id_a, source_id_b)
# The canonical_id is derived as source + '/' + min(source_id_a, source_id_b)
# (same convention as resolve_same_source write phase for non-EPA sources).
#
# Only add entries after confirming both IDs refer to the same physical site
# (e.g. identical address + coordinates in the TCEQ permit database).
# ---------------------------------------------------------------------------
_KNOWN_SAME_SOURCE_MERGES: list[tuple[str, str, str]] = [
    # WARWICK HOTEL (RN102650355, UICIHW, score 0) and WARWICK PARKING GARAGE
    # (RN104700158, VCP, score 50) at 5701 Main St, Houston TX 77005.
    # Identical coordinates (29.724880804095, -95.390244723888). Same property --
    # the Warwick Hotel (now Hotel ZaZa) and its attached parking structure.
    # TCEQ issues separate RN numbers per program, but this is one physical site.
    # CIV-653: automated dedup skipped because TCEQ has 736K TX records (>100K threshold).
    ("tceq", "RN102650355", "RN104700158"),
]


def resolve_same_source_overrides(conn: sqlite3.Connection) -> dict:
    """Apply known same-source merge pairs from _KNOWN_SAME_SOURCE_MERGES.

    Handles same-source facilities on large state databases (e.g. TCEQ) where
    resolve_same_source() is skipped due to the 100K record threshold. The
    automated geo+name passes cannot run for these sources, so pairs confirmed
    as the same physical property are listed here for manual-override merging.

    Uses the same canonical_id convention as resolve_same_source: for non-EPA
    sources, canonical_id = source + '/' + min(source_id_a, source_id_b).
    Tier 5 / score 0.8 matches the same-source dedup tier assignment.
    """
    now = datetime.now(timezone.utc).isoformat()
    applied = 0
    for source, sid_a, sid_b in _KNOWN_SAME_SOURCE_MERGES:
        canonical_id = f"{source}/{min(sid_a, sid_b)}"
        secondary_id = max(sid_a, sid_b)
        row_a = conn.execute(
            "SELECT canonical_id FROM facility_matches WHERE source = ? AND source_id = ?",
            (source, sid_a),
        ).fetchone()
        row_b = conn.execute(
            "SELECT canonical_id FROM facility_matches WHERE source = ? AND source_id = ?",
            (source, sid_b),
        ).fetchone()
        if not row_a or not row_b:
            logger.debug(
                "resolve_same_source_overrides: skipping %s/%s + %s/%s "
                "(one or both not in facility_matches)",
                source, sid_a, source, sid_b,
            )
            continue
        if row_a["canonical_id"] == row_b["canonical_id"]:
            continue
        conn.execute(
            "UPDATE facility_matches SET canonical_id = ?, match_tier = 5, "
            "match_score = 0.8, matched_at = ? "
            "WHERE source = ? AND source_id = ?",
            (canonical_id, now, source, secondary_id),
        )
        conn.execute(
            "UPDATE facility_matches SET canonical_id = ?, match_tier = 5, "
            "match_score = 0.8, matched_at = ? "
            "WHERE source = ? AND source_id = ? AND canonical_id != ?",
            (canonical_id, now, source, min(sid_a, sid_b), canonical_id),
        )
        applied += 1
        logger.info(
            "resolve_same_source_overrides: merged %s/%s + %s/%s -> %s",
            source, sid_a, source, sid_b, canonical_id,
        )
    if applied:
        conn.commit()
    return {"same_source_override_matches": applied}


def _geo_cell(lat: float, lon: float) -> tuple[int, int]:
    """Map coordinates to ~100m grid cell for spatial indexing."""
    return (round(lat * 1000), round(lon * 1000))


def _geo_neighbors(lat: float, lon: float) -> list[tuple[int, int]]:
    """Return the 3x3 grid cells surrounding a coordinate."""
    cy, cx = _geo_cell(lat, lon)
    return [(cy + dy, cx + dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1)]


def _geo_neighbors_wide(lat: float, lon: float) -> list[tuple[int, int]]:
    """Return the 9x9 grid cells surrounding a coordinate (~500m radius)."""
    cy, cx = _geo_cell(lat, lon)
    return [(cy + dy, cx + dx) for dy in range(-4, 5) for dx in range(-4, 5)]


def _build_epa_index(
    conn: sqlite3.Connection, state: str
) -> tuple[
    dict[str, list[dict]],
    dict[tuple[str, str], list[dict]],
    dict[tuple[int, int], list[dict]],
]:
    """Load EPA facilities for a state with multiple indexes.

    Returns (city_index, addr_index, geo_index):
      - city_index: normalized_city -> list of facs
      - addr_index: (normalized_city, normalized_addr) -> list of facs
      - geo_index: (grid_lat, grid_lon) -> list of facs for spatial queries
    """
    rows = conn.execute(
        "SELECT source, source_id, name, address, city, zip_code, lat, lon "
        f"FROM facilities WHERE state = ? AND source IN ({_EPA_IN_CLAUSE})",
        (state.upper(),),
    ).fetchall()

    city_index: dict[str, list[dict]] = {}
    addr_index: dict[tuple[str, str], list[dict]] = {}
    geo_index: dict[tuple[int, int], list[dict]] = {}
    for row in rows:
        fac = dict(row)
        fac["_norm_addr"] = normalize_address(fac["address"])
        fac["_norm_city"] = _normalize_city(fac["city"])
        city_key = fac["_norm_city"]
        city_index.setdefault(city_key, []).append(fac)
        if fac["_norm_addr"]:
            addr_index.setdefault((city_key, fac["_norm_addr"]), []).append(fac)
            # Also index with trailing directional stripped so state sources that
            # omit the directional (e.g. "2100 Louisiana Blvd." for EPA's
            # "2100 LOUISIANA BLVD NE") still get a Tier 1 match.
            stripped = _strip_trailing_directional(fac["_norm_addr"])
            if stripped != fac["_norm_addr"]:
                addr_index.setdefault((city_key, stripped), []).append(fac)
            # Also index with trailing street suffix stripped so state sources
            # that omit the suffix (e.g. "1411 E. Pomona" for EPA's
            # "1411 E POMONA ST") still get a Tier 1 match.  CIV-552.
            suffix_stripped = _strip_trailing_street_suffix(fac["_norm_addr"])
            if suffix_stripped != fac["_norm_addr"]:
                addr_index.setdefault((city_key, suffix_stripped), []).append(fac)
            # Also index with leading directional stripped so state sources that
            # include a directional prefix EPA omits (e.g. EPA "4335 VALLEY BLVD"
            # vs state "4335 EAST VALLEY BLVD") still get a Tier 1 match.  CIV-619.
            lead_stripped = _strip_leading_directional(fac["_norm_addr"])
            if lead_stripped != fac["_norm_addr"]:
                addr_index.setdefault((city_key, lead_stripped), []).append(fac)
        if fac.get("lat") and fac.get("lon"):
            cell = _geo_cell(fac["lat"], fac["lon"])
            geo_index.setdefault(cell, []).append(fac)

    return city_index, addr_index, geo_index


def resolve_epa(conn: sqlite3.Connection, state: str) -> dict:
    """Link EPA facilities sharing the same RegistryID across different sources.

    When epa_echo, epa_rcra, epa_caa etc. all have a facility with the same
    source_id (RegistryID), they should share a canonical_id so violations
    from all programs aggregate correctly.

    Priority: epa_echo > epa_rcra > epa_caa > epa_sdwa > epa_sems
    """
    state = state.upper()
    priority = {s: i for i, s in enumerate(EPA_SOURCES)}

    # Find source_ids shared across multiple EPA sources in this state
    rows = conn.execute(
        "SELECT source_id, GROUP_CONCAT(source) AS sources "
        f"FROM facilities WHERE state = ? AND source IN ({_EPA_IN_CLAUSE}) "
        "GROUP BY source_id HAVING COUNT(DISTINCT source) > 1",
        (state,),
    ).fetchall()

    updated = 0
    now = datetime.now(timezone.utc).isoformat()

    for row in rows:
        sid = row["source_id"]
        sources = row["sources"].split(",")
        # Pick canonical source by priority
        canonical_source = min(sources, key=lambda s: priority.get(s, 99))

        for src in sources:
            if src == canonical_source:
                continue
            # Point non-canonical entries to the canonical's source_id
            conn.execute(
                "UPDATE facility_matches SET canonical_id = ?, match_tier = 0, "
                "match_score = 1.0, matched_at = ? "
                "WHERE source = ? AND source_id = ?",
                (sid, now, src, sid),
            )
            updated += 1

    if updated:
        conn.commit()

    return {"state": state, "cross_epa_matches": updated, "groups": len(rows)}


def resolve_epa_fuzzy(
    conn: sqlite3.Connection,
    state: str,
    *,
    _epa_geo_index: dict | None = None,
    _epa_addr_index: dict | None = None,
) -> dict:
    """Match EPA facilities with different RegistryIDs by proximity + fuzzy name.

    Catches cases like Paramount Laundry (epa_sems, ID 110071102104) vs
    Paramont Laundry (epa_rcra, ID 110009558031) — same facility, different
    RegistryIDs, misspelled name. Run AFTER resolve_epa().

    Uses geo grid index for O(1) proximity lookups instead of O(n^2) pairwise.
    Pass _epa_geo_index to reuse a pre-built index (avoids rebuilding).

    Also does an address-based pass for facilities without lat/lon: EPA entries
    sharing the same normalized address + city with name_similarity >= 0.5 are
    merged. This catches cases like Phillips 66 Bayway Refinery where one entry
    lacks coordinates but both share "1400 PARK AVE, LINDEN".
    Pass _epa_addr_index to reuse a pre-built index (avoids rebuilding).
    """
    state = state.upper()
    if _epa_geo_index is None or _epa_addr_index is None:
        _city_idx, _addr_idx, _geo_idx = _build_epa_index(conn, state)
        if _epa_geo_index is None:
            _epa_geo_index = _geo_idx
        if _epa_addr_index is None:
            _epa_addr_index = _addr_idx
    priority = {s: i for i, s in enumerate(EPA_SOURCES)}

    def _epa_priority(a: str, b: str) -> str:
        """Return whichever canonical_id belongs to the higher-priority EPA source."""
        # canonical_ids are source_ids; look up their source via the fac_by_sid map
        src_a = fac_by_sid.get(a, ("",))[0] if isinstance(fac_by_sid.get(a), tuple) else ""
        src_b = fac_by_sid.get(b, ("",))[0] if isinstance(fac_by_sid.get(b), tuple) else ""
        if priority.get(src_a, 99) <= priority.get(src_b, 99):
            return a
        return b

    # Batch-load canonical_ids from facility_matches for all EPA facs in state
    rows = conn.execute(
        "SELECT fm.source, fm.source_id, fm.canonical_id "
        "FROM facility_matches fm "
        "JOIN facilities f ON fm.source = f.source AND fm.source_id = f.source_id "
        f"WHERE f.state = ? AND fm.source IN ({_EPA_IN_CLAUSE})",
        (state,),
    ).fetchall()

    # Build source lookup for priority decisions and seed Union-Find
    fac_by_sid: dict[str, tuple[str, str]] = {}  # source_id -> (source, source_id)
    uf = UnionFind(priority_fn=_epa_priority)
    for r in rows:
        fac_by_sid[r[1]] = (r[0], r[1])
        # Seed UF with existing canonical mappings
        uf.union(r[1], r[2])

    # Deduplicate geo index entries (a fac could be in multiple cells)
    seen_keys: set[tuple[str, str]] = set()
    unique_facs: list[dict] = []
    for facs in _epa_geo_index.values():
        for fac in facs:
            key = (fac["source"], fac["source_id"])
            if key not in seen_keys:
                seen_keys.add(key)
                unique_facs.append(fac)
                fac_by_sid[fac["source_id"]] = (fac["source"], fac["source_id"])

    now = datetime.now(timezone.utc).isoformat()
    checked_pairs: set[tuple[str, str]] = set()
    merge_count = 0

    # Pre-compute coordinate density to detect sentinel/centroid coordinates.
    # Coordinates shared by many facilities (e.g. county centroids used as
    # defaults for facilities without real GPS) must NOT be auto-merged at
    # 50m without a name check — they are unrelated facilities that happen to
    # share a placeholder coordinate.  CIV-677.
    _coord_counts: dict[tuple[float, float], int] = {}
    for fac in unique_facs:
        coord = (fac["lat"], fac["lon"])
        _coord_counts[coord] = _coord_counts.get(coord, 0) + 1
    _SENTINEL_COORD_THRESHOLD = 5  # >5 facilities at exact same point = sentinel

    for fac_a in unique_facs:
        key_a = (fac_a["source"], fac_a["source_id"])
        # Check only nearby EPA facilities via geo grid.
        # Use a wider search radius for SEMS (Superfund) entries since
        # those sites can span large areas with coordinates spread >200m.
        neighbor_fn = (
            _geo_neighbors_wide
            if fac_a["source"] == "epa_sems"
            else _geo_neighbors
        )
        for cell in neighbor_fn(fac_a["lat"], fac_a["lon"]):
            for fac_b in _epa_geo_index.get(cell, []):
                key_b = (fac_b["source"], fac_b["source_id"])
                if key_a == key_b:
                    continue
                # Avoid checking same pair twice
                pair = (min(key_a, key_b), max(key_a, key_b))
                if pair in checked_pairs:
                    continue
                checked_pairs.add(pair)
                if uf.find(fac_a["source_id"]) == uf.find(fac_b["source_id"]):
                    continue
                dist = _haversine_meters(
                    fac_a["lat"], fac_a["lon"], fac_b["lat"], fac_b["lon"]
                )
                # Superfund sites can span large areas; use a wider threshold
                # when either facility is from epa_sems.
                either_sems = (
                    fac_a["source"] == "epa_sems" or fac_b["source"] == "epa_sems"
                )
                dist_threshold = 500 if either_sems else 200
                if dist > dist_threshold:
                    continue
                # Geo-confirmed same location (<=50m): merge if coordinates are
                # organic (few facilities share them). Sentinel/centroid coords
                # (shared by many facilities) require a name check to avoid
                # merging hundreds of unrelated facilities. CIV-542, CIV-677.
                if dist <= 50:
                    coord_a = (fac_a["lat"], fac_a["lon"])
                    coord_b = (fac_b["lat"], fac_b["lon"])
                    is_sentinel = (
                        _coord_counts.get(coord_a, 0) > _SENTINEL_COORD_THRESHOLD
                        or _coord_counts.get(coord_b, 0) > _SENTINEL_COORD_THRESHOLD
                    )
                    if not is_sentinel:
                        uf.union(fac_a["source_id"], fac_b["source_id"])
                        merge_count += 1
                        continue
                    # Sentinel coordinate — proximity is meaningless (county/
                    # state centroid shared by hundreds of unrelated facilities).
                    # Require near-identical names since the coordinate provides
                    # zero evidence of co-location.
                    sim = name_similarity(
                        fac_a.get("name"), fac_b.get("name"), score_cutoff=0.5,
                    )
                    if sim >= 0.9:
                        uf.union(fac_a["source_id"], fac_b["source_id"])
                        merge_count += 1
                    continue
                sim = name_similarity(fac_a.get("name"), fac_b.get("name"), score_cutoff=0.5)
                if sim < 0.5:
                    continue
                uf.union(fac_a["source_id"], fac_b["source_id"])
                merge_count += 1

    # Address-based pass: catch EPA facilities that share the same normalized
    # address+city but lack lat/lon (so they're absent from the geo index).
    # Reproduces: Phillips 66 Bayway Refinery — two EPA entries at identical
    # "1400 PARK AVE, LINDEN" where one entry has no coordinates.
    for facs in _epa_addr_index.values():
        if len(facs) < 2:
            continue
        for i, fac_a in enumerate(facs):
            key_a = (fac_a["source"], fac_a["source_id"])
            fac_by_sid.setdefault(fac_a["source_id"], (fac_a["source"], fac_a["source_id"]))
            for fac_b in facs[i + 1 :]:
                key_b = (fac_b["source"], fac_b["source_id"])
                pair = (min(key_a, key_b), max(key_a, key_b))
                if pair in checked_pairs:
                    continue
                checked_pairs.add(pair)
                fac_by_sid.setdefault(fac_b["source_id"], (fac_b["source"], fac_b["source_id"]))
                if uf.find(fac_a["source_id"]) == uf.find(fac_b["source_id"]):
                    continue
                sim = name_similarity(fac_a.get("name"), fac_b.get("name"), score_cutoff=0.5)
                if sim < 0.5:
                    continue
                uf.union(fac_a["source_id"], fac_b["source_id"])
                merge_count += 1

    # Write merged canonical_ids to facility_matches
    matched = 0
    for r in rows:
        new_cid = uf.find(r[2])
        if new_cid != r[2]:
            cur = conn.execute(
                "UPDATE facility_matches SET canonical_id = ?, match_tier = 4, "
                "match_score = 0.5, matched_at = ? "
                "WHERE source = ? AND source_id = ?",
                (new_cid, now, r[0], r[1]),
            )
            matched += cur.rowcount
    if matched:
        conn.commit()

    return {"state": state, "fuzzy_epa_matches": matched}


def resolve_same_source(conn: sqlite3.Connection, state: str) -> dict:
    """Deduplicate facilities within the same source, and across state sources, for a state.

    Finds pairs from the same source (e.g. two ca_waterboard records) or from
    different state sources (e.g. ca_dtsc vs ca_geotracker) that represent the
    same physical facility, and merges their canonical_ids.

    Catches cases like:
    - BMW & MERCEDES WORLD INC (ca_waterboard, 337 W Avenue 26) vs
      Bmw Mercedes World Inc (ca_waterboard, 337 Avenue 26) -- geo proximity
    - Blossom Plaza (ca_waterboard, 900 North Broadway) vs
      Blossom Plaza Mixed Use Community (ca_waterboard, same coords)
    - SO CAL GAS/ALISO E 490 Bauchet vs SO CAL GAS/ALISO E MGP 496 Bauchet
      (ca_dtsc, adjacent addresses, same site)
    - SPEND A BUCK CLEANERS (ca_dtsc, 910 DAISEY AVE) vs
      Spend a Buck Cleaners (ca_geotracker, 910 Daisy Avenue) -- cross-source geo

    Uses three passes:
    1. Per-source geo pass: same-source pairs within 100m with name_similarity >= 0.7
    2. Per-source address pass: same-source pairs sharing normalized address+city
       with name_similarity >= 0.5
    3. Cross-source geo pass: different-state-source pairs within 100m with
       name_similarity >= 0.5 (geo-confirmed match across sources)

    Canonical_id is the lexicographically smaller source_id (deterministic).
    """
    state = state.upper()

    # Load all non-EPA facilities for this state, grouped by source
    allowed = _STATE_ALLOWED_SOURCES.get(state)
    if allowed:
        placeholders = ",".join("?" for _ in allowed)
        rows = conn.execute(
            "SELECT source, source_id, name, address, city, zip_code, lat, lon "
            f"FROM facilities WHERE state = ? AND source IN ({placeholders})",
            (state, *allowed),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT source, source_id, name, address, city, zip_code, lat, lon "
            f"FROM facilities WHERE state = ? AND source NOT IN ({_EPA_IN_CLAUSE})",
            (state,),
        ).fetchall()

    if not rows:
        return {"state": state, "same_source_matches": 0}

    # Count facilities per source before the expensive normalize_address pass
    # so we can skip large sources entirely without wasting time on normalization.
    source_counts: dict[str, int] = {}
    for row in rows:
        source_counts[row["source"]] = source_counts.get(row["source"], 0) + 1

    for src, cnt in source_counts.items():
        logger.info(
            "[resolve_same_source] %s/%s: %d facilities", state, src, cnt
        )

    # Group by source, skipping large sources before normalize_address.
    # Sources in _SAME_SOURCE_FORCE_DEDUP bypass the threshold — they have
    # multiple overlapping ID namespaces that produce duplicate records.
    by_source: dict[str, list[dict]] = {}
    for row in rows:
        src = row["source"]
        if (
            source_counts.get(src, 0) > _SAME_SOURCE_SKIP_THRESHOLD
            and src not in _SAME_SOURCE_FORCE_DEDUP
        ):
            continue  # skip normalize_address + geo/addr scan for large source
        fac = dict(row)
        fac["_norm_addr"] = normalize_address(fac["address"])
        fac["_norm_city"] = _normalize_city(fac["city"])
        by_source.setdefault(src, []).append(fac)

    # Batch-load canonical_ids and seed Union-Find
    uf = UnionFind(priority_fn=lambda a, b: min(a, b))
    sid_to_source: dict[str, str] = {}  # source_id -> source (for DB updates)
    for row in conn.execute(
        "SELECT fm.source, fm.source_id, fm.canonical_id "
        "FROM facility_matches fm "
        "JOIN facilities f ON fm.source = f.source AND fm.source_id = f.source_id "
        "WHERE f.state = ?",
        (state,),
    ).fetchall():
        sid_to_source[row[1]] = row[0]
        uf.union(row[1], row[2])

    now = datetime.now(timezone.utc).isoformat()
    # geo_checked tracks pairs already evaluated in the geo pass (to avoid
    # re-checking from overlapping geo-cell neighbors). It does NOT gate the
    # address pass — pairs that fail the geo name check may still be merged
    # by the address pass if they share the same normalized street address.
    geo_checked: set[tuple] = set()
    merge_count = 0

    for source, facs in by_source.items():
        # Large sources were already excluded during loading (see source_counts
        # check above), so every source here is within the threshold.

        # --- Geo pass: find close pairs within same source ---
        # Build a geo index for this source
        src_geo_index: dict[tuple[int, int], list[dict]] = {}
        for fac in facs:
            if fac.get("lat") and fac.get("lon"):
                cell = _geo_cell(fac["lat"], fac["lon"])
                src_geo_index.setdefault(cell, []).append(fac)

        for fac_a in facs:
            if not (fac_a.get("lat") and fac_a.get("lon")):
                continue
            key_a = (fac_a["source"], fac_a["source_id"])

            for cell in _geo_neighbors(fac_a["lat"], fac_a["lon"]):
                for fac_b in src_geo_index.get(cell, []):
                    key_b = (fac_b["source"], fac_b["source_id"])
                    if key_a == key_b:
                        continue
                    pair = (min(key_a, key_b), max(key_a, key_b))
                    if pair in geo_checked:
                        continue
                    geo_checked.add(pair)

                    if uf.find(fac_a["source_id"]) == uf.find(fac_b["source_id"]):
                        continue

                    dist = _haversine_meters(
                        fac_a["lat"], fac_a["lon"], fac_b["lat"], fac_b["lon"]
                    )
                    if dist >= 100:
                        continue

                    sim = name_similarity(fac_a.get("name"), fac_b.get("name"), score_cutoff=0.5)
                    # Prefix match: one name is a prefix of the other (e.g.
                    # "WILLIAMS" vs "WILLIAMS OIL GATHERING, LLC") at same
                    # geo location — treat as 0.7 threshold equivalent.
                    #
                    # CIV-563: the geo pass requires name similarity at ALL
                    # distances (including 0m / identical coordinates) to avoid
                    # merging genuinely different businesses that share a building
                    # address.  A geocoder that resolves all tenants of "26
                    # Journal Square, Jersey City" to the same building centroid
                    # produces identical coordinates for a law firm, a bank, and a
                    # dental office — these must NOT be merged.
                    # Same-address corporate renames (e.g. CEMEX / Transit Mixed
                    # Concrete at "625 Lamar St") are caught by the address pass
                    # below, which merges same-source records at the same
                    # normalized address regardless of name similarity.
                    if dist <= 50:
                        if sim < 0.5 and not name_is_prefix_match(
                            fac_a.get("name"), fac_b.get("name")
                        ):
                            continue
                    else:
                        if sim < 0.7 and not name_is_prefix_match(
                            fac_a.get("name"), fac_b.get("name")
                        ):
                            continue

                    uf.union(fac_a["source_id"], fac_b["source_id"])
                    merge_count += 1

        # --- Address pass: same source, same normalized address+city ---
        # Pairs are iterated via enumerate(fac_list)[i+1:] so each pair is
        # visited exactly once within this pass — no separate dedup set needed.
        # We intentionally do NOT gate on geo_checked here: pairs that failed
        # the geo-pass name check (e.g. corporate renames with dissimilar names
        # that happen to share GPS coordinates) must still be re-evaluated by
        # this pass and merged if they share the same normalized street address.
        src_addr_index: dict[tuple[str, str], list[dict]] = {}
        for fac in facs:
            if fac["_norm_addr"] and fac["_norm_city"]:
                ak = (fac["_norm_city"], fac["_norm_addr"])
                src_addr_index.setdefault(ak, []).append(fac)

        for fac_list in src_addr_index.values():
            if len(fac_list) < 2:
                continue
            for i, fac_a in enumerate(fac_list):
                for fac_b in fac_list[i + 1 :]:
                    if uf.find(fac_a["source_id"]) == uf.find(fac_b["source_id"]):
                        continue

                    sim = name_similarity(fac_a.get("name"), fac_b.get("name"), score_cutoff=0.5)
                    # Same normalized address + city: merge if name is similar enough
                    # or one is a prefix of the other (same behavior as before CIV-563).
                    # sim >= 0.5 guards against blindly merging different businesses
                    # that happen to share the same street address (e.g. different
                    # tenants with no suite number in their permit record).
                    if sim < 0.5 and not name_is_prefix_match(
                        fac_a.get("name"), fac_b.get("name")
                    ):
                        continue

                    uf.union(fac_a["source_id"], fac_b["source_id"])
                    merge_count += 1

    # --- Cross-source geo pass: different state sources at the same location ---
    # Matches facilities from different state sources (e.g. ca_dtsc vs ca_geotracker)
    # when they are within 100m and have similar names.  Catches cases where the same
    # physical site appears in multiple state data programs with slightly different
    # addresses (e.g. "910 DAISEY AVE" vs "910 Daisy Avenue" — a typo that survives
    # address normalization but the GPS coordinates are essentially identical).
    #
    # Only runs when 2+ distinct state sources have geocoded facilities in this state.
    # Same name-similarity thresholds as the per-source geo pass (sim >= 0.5 at <=50m,
    # sim >= 0.7 at 50-100m) to guard against merging different businesses at a
    # shared address.
    #
    # geo_checked already contains pairs evaluated in the per-source geo pass (which
    # covers same-source pairs). Cross-source pairs are new, so we add them here.
    all_source_names = list(by_source.keys())
    if len(all_source_names) >= 2:
        # Build a combined geo index across all sources in by_source
        combined_geo_index: dict[tuple[int, int], list[dict]] = {}
        for facs in by_source.values():
            for fac in facs:
                if fac.get("lat") and fac.get("lon"):
                    cell = _geo_cell(fac["lat"], fac["lon"])
                    combined_geo_index.setdefault(cell, []).append(fac)

        for facs in by_source.values():
            for fac_a in facs:
                if not (fac_a.get("lat") and fac_a.get("lon")):
                    continue
                key_a = (fac_a["source"], fac_a["source_id"])

                for cell in _geo_neighbors(fac_a["lat"], fac_a["lon"]):
                    for fac_b in combined_geo_index.get(cell, []):
                        # Only cross-source pairs
                        if fac_a["source"] == fac_b["source"]:
                            continue
                        key_b = (fac_b["source"], fac_b["source_id"])
                        pair = (min(key_a, key_b), max(key_a, key_b))
                        if pair in geo_checked:
                            continue
                        geo_checked.add(pair)

                        if uf.find(fac_a["source_id"]) == uf.find(fac_b["source_id"]):
                            continue

                        dist = _haversine_meters(
                            fac_a["lat"], fac_a["lon"], fac_b["lat"], fac_b["lon"]
                        )
                        if dist >= 100:
                            continue

                        sim = name_similarity(fac_a.get("name"), fac_b.get("name"), score_cutoff=0.5)
                        if dist <= 50:
                            if sim < 0.5 and not name_is_prefix_match(
                                fac_a.get("name"), fac_b.get("name")
                            ):
                                continue
                        else:
                            if sim < 0.7 and not name_is_prefix_match(
                                fac_a.get("name"), fac_b.get("name")
                            ):
                                continue

                        uf.union(fac_a["source_id"], fac_b["source_id"])
                        merge_count += 1

    # --- Exact-coordinate pass for large sources ---
    # Sources exceeding _SAME_SOURCE_SKIP_THRESHOLD are not included in
    # by_source (the O(n²) geo/address passes would be too expensive).
    # However, they may still have data-entry-typo duplicate registrations
    # at the exact same coordinates (e.g. TCEQ "FROPF-M76K" and "KROPF-M76K"
    # at 515 W Main St, Houston — same address, same lat/lon, edit distance 1).
    #
    # This pass is O(n) in loading + O(k²) within each small coordinate group,
    # where k is typically 2-3.  Groups larger than _EXACT_COORD_MAX_GROUP are
    # skipped to avoid merging genuinely distinct entities that share a
    # management-office or law-firm address (e.g. 52 Texas MUDs at one address).
    #
    # Merge criterion: name_similarity >= 0.7 on names.
    # Catches identical registrations, abbreviation variants (TELE vs TELEPHONE),
    # and typos.  The 0.7 threshold at exact coordinates is safe because the
    # group-size cap prevents merging different tenants at shared addresses.
    large_source_facs: dict[str, list[dict]] = {}
    for row in rows:
        src = row["source"]
        if (
            source_counts.get(src, 0) <= _SAME_SOURCE_SKIP_THRESHOLD
            or src in _SAME_SOURCE_FORCE_DEDUP
        ):
            continue  # already processed in by_source loop above
        if row["lat"] and row["lon"]:
            large_source_facs.setdefault(src, []).append(dict(row))

    for src, facs in large_source_facs.items():
        # Group by exact (lat, lon)
        by_coord: dict[tuple, list[dict]] = {}
        for fac in facs:
            key = (fac["lat"], fac["lon"])
            by_coord.setdefault(key, []).append(fac)

        for coord_group in by_coord.values():
            if len(coord_group) < 2 or len(coord_group) > _EXACT_COORD_MAX_GROUP:
                continue
            for i, fac_a in enumerate(coord_group):
                for fac_b in coord_group[i + 1:]:
                    if uf.find(fac_a["source_id"]) == uf.find(fac_b["source_id"]):
                        continue
                    sim = name_similarity(
                        fac_a.get("name"), fac_b.get("name"), score_cutoff=0.5
                    )
                    if sim < 0.7:
                        continue
                    uf.union(fac_a["source_id"], fac_b["source_id"])
                    merge_count += 1
                    # Register the new source_id → source mapping so
                    # the write-back loop can qualify the canonical_id.
                    sid_to_source.setdefault(fac_a["source_id"], src)
                    sid_to_source.setdefault(fac_b["source_id"], src)

    # Write merged canonical_ids to facility_matches.
    # uf.find(sid) returns a bare source_id (the UnionFind root). For non-EPA
    # sources we must qualify it as source || '/' || source_id to prevent
    # cross-state canonical_id collisions (CIV-460).
    #
    # IMPORTANT: skip EPA sources entirely. This function only merges non-EPA
    # same-source duplicates (the geo/address passes above operate only on
    # non-EPA facilities). However, the UF is seeded from ALL facility_matches
    # (including EPA), so EPA source_ids are present in sid_to_source. Writing
    # back EPA records here would incorrectly overwrite canonical_ids and
    # match_tiers set by resolve_epa() / resolve_epa_fuzzy() with the
    # lexicographic-min root of the UF (which may be a different facility).
    # CIV-549: this caused epa_rcra|110040929369 (Troy Chemical) to be merged
    # into epa_rcra|110001134601 (Jarchem), corrupting the unified facility.
    matched = 0
    for sid, src in sid_to_source.items():
        if src in EPA_SOURCES:
            continue  # EPA canonical_ids are managed by resolve_epa/resolve_epa_fuzzy
        raw_cid = uf.find(sid)
        # Qualify: if the raw_cid is a known non-EPA source_id, prefix its source.
        cid_source = sid_to_source.get(raw_cid)
        if cid_source and cid_source not in EPA_SOURCES:
            new_cid = f"{cid_source}/{raw_cid}"
        else:
            new_cid = raw_cid
        # Only update rows whose canonical_id changed
        cur = conn.execute(
            "UPDATE facility_matches SET canonical_id = ?, match_tier = 5, "
            "match_score = 0.8, matched_at = ? "
            "WHERE source = ? AND source_id = ? AND canonical_id != ?",
            (new_cid, now, src, sid, new_cid),
        )
        matched += cur.rowcount
    if matched:
        conn.commit()

    return {"state": state, "same_source_matches": matched}


def resolve_full(
    conn: sqlite3.Connection,
    state: str,
    _phase_totals: dict | None = None,
) -> dict:
    """Run cross-EPA + fuzzy EPA + same-source dedup + state-to-EPA in a single pass.

    Builds the EPA index once and shares it across all steps.
    Returns combined stats.
    """
    state = state.upper()
    pt = _phase_totals  # alias for brevity; None means no accumulation
    t_start = time.monotonic()
    logger.info("[resolve] %s: building EPA index...", state)
    epa_indexes = _build_epa_index(conn, state)
    _, addr_idx, geo_idx = epa_indexes
    dt = time.monotonic() - t_start
    if pt is not None:
        pt["epa_index"] += dt
    logger.info(
        "[resolve] %s: EPA index built (addr=%d, geo=%d cells) in %.1fs",
        state, len(addr_idx), len(geo_idx), dt,
    )

    t = time.monotonic()
    logger.info("[resolve] %s: resolve_epa (cross-EPA same-RegistryID)...", state)
    epa_stats = resolve_epa(conn, state)
    dt = time.monotonic() - t
    if pt is not None:
        pt["resolve_epa"] += dt
    logger.info(
        "[resolve] %s: resolve_epa done (%d matches) in %.1fs",
        state, epa_stats["cross_epa_matches"], dt,
    )

    # Apply known RegistryID merge pairs (e.g. MOTCO SEMS → MOTCO ECHO).
    # Runs after resolve_epa (same-ID pass) and before fuzzy matching so the
    # Union-Find in resolve_epa_fuzzy can seed from the corrected canonical_ids.
    # Idempotent — safe to call once per state even though overrides are global.
    t = time.monotonic()
    override_stats = resolve_epa_overrides(conn)
    if override_stats["epa_override_matches"]:
        logger.info(
            "[resolve] %s: overrides applied (%d) in %.1fs",
            state, override_stats["epa_override_matches"], time.monotonic() - t,
        )

    t = time.monotonic()
    logger.info("[resolve] %s: resolve_epa_fuzzy (geo+name across EPA sources)...", state)
    fuzzy_stats = resolve_epa_fuzzy(
        conn, state, _epa_geo_index=geo_idx, _epa_addr_index=addr_idx
    )
    dt = time.monotonic() - t
    if pt is not None:
        pt["epa_fuzzy"] += dt
    logger.info(
        "[resolve] %s: resolve_epa_fuzzy done (%d matches) in %.1fs",
        state, fuzzy_stats["fuzzy_epa_matches"], dt,
    )

    t = time.monotonic()
    logger.info("[resolve] %s: resolve_same_source (intra-source dedup)...", state)
    same_source_stats = resolve_same_source(conn, state)
    dt = time.monotonic() - t
    if pt is not None:
        pt["same_source"] += dt
    logger.info(
        "[resolve] %s: resolve_same_source done (%d matches) in %.1fs",
        state, same_source_stats["same_source_matches"], dt,
    )

    # Apply manually curated same-source merges for large sources (e.g. TCEQ)
    # that are skipped by the automated resolve_same_source pass. Idempotent.
    override2_stats = resolve_same_source_overrides(conn)
    if override2_stats["same_source_override_matches"]:
        logger.info(
            "[resolve] %s: same_source_overrides applied (%d)",
            state, override2_stats["same_source_override_matches"],
        )

    t = time.monotonic()
    logger.info("[resolve] %s: resolve_state (state-to-EPA matching)...", state)
    state_stats = resolve_state(conn, state, _epa_indexes=epa_indexes)
    dt = time.monotonic() - t
    if pt is not None:
        pt["resolve_state"] += dt
    logger.info(
        "[resolve] %s: resolve_state done (%d/%d matched) in %.1fs",
        state, state_stats["matched"], state_stats["total"], dt,
    )

    elapsed = time.monotonic() - t_start
    logger.info("[resolve] %s: resolve_full complete in %.1fs", state, elapsed)

    return {
        "state": state,
        "cross_epa_matches": epa_stats["cross_epa_matches"],
        "epa_override_matches": override_stats["epa_override_matches"],
        "fuzzy_epa_matches": fuzzy_stats["fuzzy_epa_matches"],
        "same_source_matches": same_source_stats["same_source_matches"],
        "same_source_override_matches": override2_stats["same_source_override_matches"],
        **{k: state_stats[k] for k in ("total", "matched", "tier_1", "tier_2", "tier_3", "tier_4")},
    }


def resolve_full_all(conn: sqlite3.Connection) -> dict:
    """Run full resolution across all states with per-state reset.

    Resets and resolves one state at a time so that if the process is
    interrupted, already-processed states keep their new matches and
    unprocessed states retain their old matches (resumable).
    """
    states = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT state FROM facilities WHERE state IS NOT NULL ORDER BY state"
        ).fetchall()
    ]

    totals = {
        "states": len(states),
        "cross_epa_matches": 0,
        "epa_override_matches": 0,
        "fuzzy_epa_matches": 0,
        "same_source_matches": 0,
        "same_source_override_matches": 0,
        "total": 0,
        "matched": 0,
        "tier_1": 0,
        "tier_2": 0,
        "tier_3": 0,
        "tier_4": 0,
    }
    phase_totals = {
        "reset": 0.0, "epa_index": 0.0, "resolve_epa": 0.0,
        "epa_fuzzy": 0.0, "same_source": 0.0, "resolve_state": 0.0,
    }
    wall_start = time.monotonic()

    for i, st in enumerate(states, 1):
        t = time.monotonic()
        reset_matches(conn, state=st)
        phase_totals["reset"] += time.monotonic() - t

        stats = resolve_full(conn, st, _phase_totals=phase_totals)
        for key in totals:
            if key != "states":
                totals[key] += stats.get(key, 0)

        elapsed = time.monotonic() - wall_start
        logger.info(
            "[resolve_full_all] %d/%d %s done (wall=%.0fs). "
            "Phase cumulative: reset=%.0fs epa_idx=%.0fs epa=%.0fs "
            "fuzzy=%.0fs same_src=%.0fs state=%.0fs",
            i, len(states), st, elapsed,
            phase_totals["reset"], phase_totals["epa_index"],
            phase_totals["resolve_epa"], phase_totals["epa_fuzzy"],
            phase_totals["same_source"], phase_totals["resolve_state"],
        )

    logger.info(
        "[resolve_full_all] PHASE TOTALS (seconds): %s  wall=%.0fs",
        {k: round(v, 1) for k, v in phase_totals.items()},
        time.monotonic() - wall_start,
    )
    return totals


def resolve_state(
    conn: sqlite3.Connection,
    state: str,
    *,
    _epa_indexes: tuple | None = None,
) -> dict:
    """Run tiered matching for one state.

    Returns summary: {state, total, matched, tier_1, tier_2, tier_3, tier_4}
    Pass _epa_indexes=(city_idx, addr_idx, geo_idx) to reuse a pre-built index.
    """
    state = state.upper()

    if _epa_indexes is not None:
        epa_city_index, epa_addr_index, epa_geo_index = _epa_indexes
    else:
        epa_city_index, epa_addr_index, epa_geo_index = _build_epa_index(conn, state)
    if not epa_city_index:
        return {
            "state": state,
            "total": 0,
            "matched": 0,
            "tier_1": 0,
            "tier_2": 0,
            "tier_3": 0,
            "tier_4": 0,
        }

    # Build zip index for Tier 3+4 with pre-computed token sets and normalized names.
    # Avoids redundant normalize_name/split calls in the hot inner loop.
    epa_zip_index: dict[str, list[tuple[dict, frozenset, str]]] = {}
    for facs in epa_city_index.values():
        for fac in facs:
            if fac.get("zip_code"):
                nn = normalize_name(fac.get("name"))
                if nn:
                    tokens = frozenset(nn.lower().split())
                    norm_lower = nn.lower()
                else:
                    tokens = frozenset()
                    norm_lower = ""
                epa_zip_index.setdefault(fac["zip_code"], []).append(
                    (fac, tokens, norm_lower)
                )

    # Load state-source facilities (non-EPA), filtered to only sources
    # that belong to this state.  Prevents cross-state false matches
    # (e.g. ut_deq facilities with state='DC' matching DC EPA records).
    allowed = _STATE_ALLOWED_SOURCES.get(state)
    if allowed:
        placeholders = ",".join("?" for _ in allowed)
        state_facs = conn.execute(
            "SELECT source, source_id, name, address, city, zip_code, lat, lon "
            f"FROM facilities WHERE state = ? AND source IN ({placeholders})",
            (state, *allowed),
        ).fetchall()
    else:
        # Unknown state — fall back to all non-EPA sources
        state_facs = conn.execute(
            "SELECT source, source_id, name, address, city, zip_code, lat, lon "
            f"FROM facilities WHERE state = ? AND source NOT IN ({_EPA_IN_CLAUSE})",
            (state,),
        ).fetchall()

    matches = []  # (source, source_id, canonical_id, tier, score)
    stats = {
        "state": state,
        "total": len(state_facs),
        "matched": 0,
        "tier_1": 0,
        "tier_2": 0,
        "tier_3": 0,
        "tier_4": 0,
    }

    total = len(state_facs)
    log_interval = max(100_000, total // 10)  # log every 10% (or every 100K, whichever is larger)
    t_loop = time.monotonic()
    logger.info("[resolve_state] %s: matching %d state facilities...", state, total)

    for idx, fac_row in enumerate(state_facs):
        if idx > 0 and idx % log_interval == 0:
            elapsed = time.monotonic() - t_loop
            rate = idx / elapsed if elapsed > 0 else 0
            remaining = (total - idx) / rate if rate > 0 else 0
            logger.info(
                "[resolve_state] %s: %d/%d (%.0f/s, ~%.0fs remaining, matched=%d)",
                state, idx, total, rate, remaining, stats["matched"],
            )

        fac = dict(fac_row)
        norm_addr = normalize_address(fac["address"])
        norm_city = _normalize_city(fac["city"])

        matched = False

        # Tier 1: O(1) hash lookup on (city, normalized_address)
        # Falls back to suffix-stripped address when exact match fails,
        # so "1411 E Pomona" matches EPA's "1411 E Pomona St" (CIV-552).
        # Also falls back to leading-directional-stripped address so
        # "4335 East Valley Blvd" matches EPA's "4335 Valley Blvd" (CIV-619).
        if norm_addr and norm_city:
            addr_matches = epa_addr_index.get((norm_city, norm_addr))
            if not addr_matches:
                # Fallback: try with trailing street suffix stripped
                sfx_stripped = _strip_trailing_street_suffix(norm_addr)
                if sfx_stripped != norm_addr:
                    addr_matches = epa_addr_index.get((norm_city, sfx_stripped))
            if not addr_matches:
                # Fallback: try with leading directional stripped (CIV-619)
                lead_stripped = _strip_leading_directional(norm_addr)
                if lead_stripped != norm_addr:
                    addr_matches = epa_addr_index.get((norm_city, lead_stripped))
            if not addr_matches:
                # Fallback: try with trailing directional stripped (CIV-726)
                # Catches cases where directional is trailing in state source
                # but leading in EPA (e.g. "22751 golden springs dr e" vs
                # EPA's "22751 e golden springs dr" — both strip to
                # "22751 golden springs dr").
                trail_stripped = _strip_trailing_directional(norm_addr)
                if trail_stripped != norm_addr:
                    addr_matches = epa_addr_index.get((norm_city, trail_stripped))
            if addr_matches:
                epa_fac = addr_matches[0]
                matches.append(
                    (fac["source"], fac["source_id"], epa_fac["source_id"], 1, 1.0)
                )
                stats["tier_1"] += 1
                stats["matched"] += 1
                matched = True

        if matched:
            continue

        # Tier 2: Geo proximity (<200m) + loose name overlap (Jaccard > 0.3)
        # Geo-confirmed sub-tier: when distance <=50m, skip name check entirely —
        # facilities at essentially the same GPS point are the same physical site
        # even if the company name changed (corporate rename / acquisition). CIV-542.
        # Between 50m and 200m, require Jaccard > 0.3 to prevent false merges of
        # adjacent-but-different businesses. The 200m threshold (up from 100m) catches
        # sources like fl_dep WAFR permits whose coordinates can be 100-200m from the
        # EPA-registered site centroid. CIV-722.
        # Uses spatial grid index for O(1) cell lookup instead of scanning all candidates
        if fac.get("lat") and fac.get("lon"):
            nearby: list[dict] = []
            for cell in _geo_neighbors(fac["lat"], fac["lon"]):
                nearby.extend(epa_geo_index.get(cell, []))
            for epa_fac in nearby:
                dist = _haversine_meters(
                    fac["lat"], fac["lon"], epa_fac["lat"], epa_fac["lon"]
                )
                if dist < 200:
                    if dist <= 50 or token_jaccard(fac.get("name"), epa_fac.get("name")) > 0.3:
                        score = 1.0 - (dist / 200.0)
                        matches.append(
                            (
                                fac["source"],
                                fac["source_id"],
                                epa_fac["source_id"],
                                2,
                                round(score, 3),
                            )
                        )
                        stats["tier_2"] += 1
                        stats["matched"] += 1
                        matched = True
                        break

        if matched:
            continue

        # Tier 3+4: Same zip + name similarity.
        # Tier 3: token Jaccard > 0.85 (word overlap).
        # Tier 4: character-level similarity >= 0.75 (catches shared
        #   prefixes like "Diamond Head Oil Refinery Div." vs "...Superfund
        #   Site" where token Jaccard fails due to unique trailing tokens).
        #   Threshold raised from 0.70 → 0.75 (CIV-532) to prevent false
        #   positives where two facilities share a municipality prefix but
        #   are different businesses (e.g. "Cliffside Park Dental Group" vs
        #   "Cliffside Park Boro" — char sim 0.74 would fire at 0.70).
        #
        # Uses pre-computed EPA token sets and normalized names from the zip
        # index to avoid redundant normalize_name/split calls. State fac
        # tokens are computed once per facility.
        #
        # Dense zip codes (>200 EPA facilities) are skipped entirely — these
        # are oilfield zips where 99.85% of facilities never match EPA records
        # and the comparison volume causes timeouts (e.g. NM: 467M comparisons
        # across 12 Permian Basin zip codes). Tier 1 (address) and Tier 2
        # (geo+name) still run for all facilities regardless.
        _MAX_ZIP_CANDIDATES = 200
        if fac.get("zip_code"):
            zip_candidates = epa_zip_index.get(fac["zip_code"], [])
            if zip_candidates and len(zip_candidates) <= _MAX_ZIP_CANDIDATES:
                s_nn = normalize_name(fac.get("name"))
                if s_nn:
                    s_tokens = frozenset(s_nn.lower().split())
                    s_norm = s_nn.lower()
                    best_match = None
                    best_tier = 0
                    best_score = 0.0
                    for epa_fac, e_tokens, e_norm in zip_candidates:
                        if not e_tokens:
                            continue
                        # Inline token Jaccard using pre-computed sets
                        isect = s_tokens & e_tokens
                        if not isect:
                            continue
                        jac = len(isect) / (len(s_tokens) + len(e_tokens) - len(isect))
                        if jac > 0.85:
                            best_match = epa_fac
                            best_tier = 3
                            best_score = jac
                            break  # Tier 3 match, stop immediately
                        if jac > 0.3:
                            sim = fuzz.ratio(s_norm, e_norm, score_cutoff=75) / 100
                            if sim >= 0.75 and sim > best_score:
                                best_match = epa_fac
                                best_tier = 4
                                best_score = sim
                    if best_match:
                        matches.append(
                            (
                                fac["source"],
                                fac["source_id"],
                                best_match["source_id"],
                                best_tier,
                                round(best_score, 3),
                            )
                        )
                        stats[f"tier_{best_tier}"] += 1
                        stats["matched"] += 1
                        matched = True

    # Write matches to facility_matches
    now = datetime.now(timezone.utc).isoformat()

    # Resolve transitive canonical chains (CIV-686): if an EPA facility A was
    # merged into B by resolve_epa_fuzzy(), state-source records matched to A
    # must point to B, not A. Without this, unified_facilities ends up with two
    # separate groups (A with the state records, B with the EPA records) because
    # _UNIFIED_QUERY groups by canonical_id and never follows the A→B chain.
    # resolve_state() runs AFTER resolve_epa_fuzzy(), so the facility_matches
    # table already contains the final canonical for each EPA source_id — we
    # just need to look it up before writing.
    if matches:
        target_sids = list({cid for _, _, cid, _, _ in matches})
        epa_canonical: dict[str, str] = {}
        BATCH = 900
        for i in range(0, len(target_sids), BATCH):
            batch = target_sids[i : i + BATCH]
            placeholders = ",".join("?" for _ in batch)
            for row in conn.execute(
                f"SELECT source_id, canonical_id FROM facility_matches "
                f"WHERE source_id IN ({placeholders})",
                batch,
            ).fetchall():
                if row[1] != row[0]:  # only record when canonical differs (merged)
                    epa_canonical[row[0]] = row[1]
        if epa_canonical:
            matches = [
                (s, sid, epa_canonical.get(cid, cid), tier, score)
                for s, sid, cid, tier, score in matches
            ]

    conn.executemany(
        "INSERT OR REPLACE INTO facility_matches "
        "(source, source_id, canonical_id, match_tier, match_score, matched_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(s, sid, cid, tier, score, now) for s, sid, cid, tier, score in matches],
    )
    conn.commit()

    return stats


def resolve_all(conn: sqlite3.Connection) -> dict:
    """Run entity resolution across all states. Returns aggregate stats."""
    states = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT state FROM facilities WHERE state IS NOT NULL ORDER BY state"
        ).fetchall()
    ]

    totals = {
        "states": len(states),
        "total": 0,
        "matched": 0,
        "tier_1": 0,
        "tier_2": 0,
        "tier_3": 0,
        "tier_4": 0,
    }

    for state in states:
        stats = resolve_state(conn, state)
        totals["total"] += stats["total"]
        totals["matched"] += stats["matched"]
        totals["tier_1"] += stats["tier_1"]
        totals["tier_2"] += stats["tier_2"]
        totals["tier_3"] += stats["tier_3"]
        totals["tier_4"] += stats.get("tier_4", 0)

    return totals


def reset_matches(conn: sqlite3.Connection, state: str | None = None) -> int:
    """Populate facility_matches with self-matches.

    For EPA sources, canonical_id = source_id (globally unique EPA registry IDs).
    For non-EPA state sources, canonical_id = source || '/' || source_id to avoid
    collisions across states that share overlapping source_id namespaces
    (e.g. 'brownfield-108' appears in ID, KY, NJ, NM DEP/DEQ sources).

    This is the baseline — resolve_state() then upgrades matched facilities
    by setting canonical_id to the matched EPA facility's source_id.

    If state is given, only resets matches for facilities in that state.
    Returns the number of rows inserted/replaced.
    """
    # SQL CASE expression for canonical_id assignment
    canonical_expr = (
        f"CASE WHEN source IN ({_EPA_IN_CLAUSE}) "
        "THEN source_id ELSE source || '/' || source_id END"
    )
    now = datetime.now(timezone.utc).isoformat()
    if state:
        state = state.upper()
        conn.execute(
            "DELETE FROM facility_matches WHERE rowid IN "
            "(SELECT fm.rowid FROM facility_matches fm "
            "JOIN facilities f ON fm.source = f.source AND fm.source_id = f.source_id "
            "WHERE f.state = ?)",
            (state,),
        )
        cur = conn.execute(
            "INSERT INTO facility_matches "
            "(source, source_id, canonical_id, match_tier, match_score, matched_at) "
            f"SELECT source, source_id, {canonical_expr}, 0, 1.0, ? "
            "FROM facilities WHERE state = ?",
            (now, state),
        )
    else:
        conn.execute("DELETE FROM facility_matches")
        cur = conn.execute(
            "INSERT INTO facility_matches "
            "(source, source_id, canonical_id, match_tier, match_score, matched_at) "
            f"SELECT source, source_id, {canonical_expr}, 0, 1.0, ? FROM facilities",
            (now,),
        )
    count = cur.rowcount
    conn.commit()
    return count


def cleanup_orphaned_scores(conn: sqlite3.Connection) -> int:
    """Delete facility_scores rows that no longer correspond to a unified facility.

    After entity resolution merges facilities, the old source_ids disappear
    from unified_facilities but their scores persist. This cleans them up.
    """
    cur = conn.execute(
        "DELETE FROM facility_scores "
        "WHERE source_id NOT IN (SELECT source_id FROM unified_facilities)"
    )
    count = cur.rowcount
    conn.commit()
    return count
