"""Map raw NJ DEP ArcGIS feature attributes to Pydantic models.

NJ DEP data comes from ArcGIS REST MapServer at mapsdep.nj.gov. This mapper
handles datasets from the Environmental_NJEMS service:
  - Known Contaminated Sites List (Layer 0) → Facility
  - NJEMS Sites (Layer 2) → Facility
  - Underground Storage Tanks (Layer 9) → UST site ID set (used to tag Layer 2 sites)
  - Enforcement Actions (Layer 18) → Violation
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

from ..models import Facility, Violation
from ._utils import clean, parse_float, parse_date

SOURCE = "nj_dep"


# Street suffix pattern used to detect address-like facility names.
# Matches names that start with a number and contain a common street suffix.
# Examples: "20 EAST 5TH AVENUE", "65 BLAIR ROAD", "88 RARITAN AVE"
_STREET_SUFFIX_RE = re.compile(
    r"""
    ^\d+               # starts with a house number
    \b                 # word boundary
    .*?                # any middle words (street name, directionals)
    \b(
        AVE | AVENUE |
        ST  | STREET |
        RD  | ROAD |
        BLVD | BOULEVARD |
        DR  | DRIVE |
        LN  | LANE |
        CT  | COURT |
        PL  | PLACE |
        WAY |
        TER | TERRACE |
        TRL | TRAIL |
        CIR | CIRCLE |
        HWY | HIGHWAY |
        PKWY | PARKWAY |
        PIKE |
        FLR | FLOOR |
        STE | SUITE |
        RM  | ROOM
    )\b                # suffix as whole word
    \s*$               # optional trailing whitespace
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _is_address_name(name: str | None) -> bool:
    """Return True if the facility name looks like a street address.

    NJ DEP NJEMS records frequently use the street address as the site name
    when no business name is available (e.g. "20 EAST 5TH AVENUE",
    "88 RARITAN AVENUE", "65 BLAIR ROAD"). These provide zero additional
    information beyond the address field and clutter search results.
    """
    if not name:
        return False
    return bool(_STREET_SUFFIX_RE.match(name.strip()))


# NJ municipality type suffixes appended by NJEMS to all MUNIC values.
# Stripping these normalizes "NEWARK CITY" → "Newark", "NORTH BERGEN TWP" →
# "North Bergen", "KEARNY TOWN" → "Kearny", etc. For municipalities whose
# name already contains the type word (e.g. "Jersey City"), the MUNIC field
# may append an extra suffix ("JERSEY CITY CITY"), so stripping once correctly
# leaves "Jersey City". "Town" is a distinct NJ municipality class (Kearny,
# Phillipsburg, Dover, etc.) separate from Township.
_NJ_MUNIC_SUFFIX_RE = re.compile(
    r"\s+(city|town|township|borough|village|boro|twp)$",
    re.IGNORECASE,
)

# NJ municipalities whose names legitimately contain "City" or "Town" as part
# of the name (not just a municipality-type suffix). These are protected from
# suffix stripping: when the raw MUNIC value matches one of these (case-
# insensitive), we return the canonical name directly instead of stripping.
#
# Without this guard, "JERSEY CITY" would be stripped to "Jersey" because the
# regex cannot distinguish the type-suffix "CITY" from the name component "City".
# The double-suffix form ("JERSEY CITY CITY") is handled naturally by stripping
# once: "JERSEY CITY CITY" → "Jersey City". Both forms may appear in practice.
_NJ_PROTECTED_CITY_NAMES: dict[str, str] = {
    "jersey city": "Jersey City",
    "atlantic city": "Atlantic City",
    "ocean city": "Ocean City",
    "egg harbor city": "Egg Harbor City",
    "estell manor city": "Estell Manor City",
}

# Bogus city names in NJ DEP upstream data that are neighborhood names, historic
# district names, or other non-municipality values. These are corrected to the
# actual municipality name. "Sparrow Hill" is a historic Heights neighborhood in
# Jersey City that appears in NJEMS source data for 11 facilities.
_NJ_BOGUS_CITY_CORRECTIONS: dict[str, str] = {
    "sparrow hill": "Jersey City",
}

# NJ city values that are clearly invalid (state name used as city, etc.).
# When the stored city matches one of these, we fall back to ZIP-based lookup.
_NJ_INVALID_CITY_VALUES: frozenset[str] = frozenset({"new jersey", "nj"})

# ZIP code prefix ranges for Jersey City, NJ. Used to correct facilities whose
# stored city is invalid ("New Jersey") or geographically wrong ("Union" at a
# Jersey City address). Jersey City ZIPs: 07097 plus 07302–07311, 07395, 07399.
_NJ_ZIP_JERSEY_CITY: frozenset[str] = frozenset(
    ["07097"]
    + [f"073{d:02d}" for d in range(2, 12)]
    + ["07395", "07399"]
)


def _zip_to_nj_city(zip_code: str | None) -> str | None:
    """Return the canonical NJ city for a given ZIP code, or None if not in lookup.

    Only covers ZIPs known to be associated with incorrectly-named facilities.
    Returns None for unrecognized ZIPs so the caller can decide the fallback.
    """
    if not zip_code:
        return None
    z = zip_code.strip()[:5]
    if z in _NJ_ZIP_JERSEY_CITY:
        return "Jersey City"
    return None


def _normalize_nj_city(val: str | None, zip_code: str | None = None) -> str | None:
    """Normalize a NJ municipality name by stripping the type suffix and title-casing.

    NJ DEP MUNIC field appends the municipality type to every name:
      "NEWARK CITY"        → "Newark"
      "JERSEY CITY CITY"   → "Jersey City"
      "JERSEY CITY"        → "Jersey City"  (protected: 'City' is part of the name)
      "NORTH BERGEN TWP"   → "North Bergen"
      "EDGEWATER BORO"     → "Edgewater"
      "HOBOKEN CITY"       → "Hoboken"
      "KEARNY TOWN"        → "Kearny"
      "PHILLIPSBURG TOWN"  → "Phillipsburg"

    Additionally corrects bogus upstream city values:
      "Sparrow Hill"       → "Jersey City"  (neighborhood name, not a municipality)
      "New Jersey"         → ZIP lookup or None  (state name used as city)
      "Union" (ZIP 07306)  → "Jersey City"  (wrong municipality, ZIP overrides)
    """
    raw = clean(val)
    if not raw:
        return raw
    # Check protected names before stripping: these are NJ municipalities
    # whose name contains "City" or "Town" as a real word, not just a suffix.
    key = raw.lower()
    if key in _NJ_PROTECTED_CITY_NAMES:
        return _NJ_PROTECTED_CITY_NAMES[key]
    normalized = _NJ_MUNIC_SUFFIX_RE.sub("", raw)
    city = normalized.title()
    # Check for known bogus neighborhood/non-municipality city values
    city_lower = city.lower()
    if city_lower in _NJ_BOGUS_CITY_CORRECTIONS:
        return _NJ_BOGUS_CITY_CORRECTIONS[city_lower]
    # Check for clearly invalid city values (state name used as city).
    # Fall back to ZIP lookup; return None if ZIP is also unknown.
    if city_lower in _NJ_INVALID_CITY_VALUES:
        return _zip_to_nj_city(zip_code)
    # Apply ZIP-based override when the stored city conflicts with the ZIP code.
    # "Union" at ZIP 07306 is a known data error (997 Summit Ave is in Jersey City).
    zip_city = _zip_to_nj_city(zip_code)
    if zip_city is not None and city_lower != zip_city.lower():
        return zip_city
    return city


def _extract_lat_lon(geometry: dict | None) -> tuple[float | None, float | None]:
    """Extract lat/lon from ArcGIS point geometry (outSR=4326)."""
    if not geometry:
        return None, None
    lon = parse_float(geometry.get("x"))
    lat = parse_float(geometry.get("y"))
    return lat, lon


def map_kcsl_site(attrs: dict, geometry: dict | None = None) -> Facility:
    """Convert a Known Contaminated Sites List feature to a Facility model.

    Key fields: SITE_ID, PI_NAME, ADDRESS, PI_NUMBER, STATUS, COUNTY,
    MUNIC, ZIP_CODE, X_COORDINATE, Y_COORDINATE.

    SITE_ID is preferred over PI_NUMBER because it is the NJEMS site ID
    used to construct facility-specific DataMiner deep links
    (njems.nj.gov/DataMiner/Search/SearchBySite?SiteId={id}). PI_NUMBER
    (e.g. "NJD986643825") is only used as a fallback when SITE_ID is absent.
    ArcGIS returns SITE_ID as a float (e.g. 12345.0); we normalize to int
    so the source_id is "kcsl-12345" (not "kcsl-12345.0").
    """
    raw_site_id = attrs.get("SITE_ID")
    # Normalize ArcGIS float SITE_IDs to int strings (12345.0 → "12345")
    if raw_site_id is not None:
        try:
            raw_site_id = str(int(float(raw_site_id)))
        except (ValueError, TypeError, OverflowError):
            raw_site_id = clean(raw_site_id)
    site_id = raw_site_id or clean(attrs.get("PI_NUMBER")) or ""

    lat, lon = _extract_lat_lon(geometry)

    # STATUS describes regulatory state (Active, Pending, Active - UHOT, etc.),
    # not program enrollment. Exclude it from the programs field so it does not
    # inflate the program count used in scoring (CIV-346).
    programs = "KCSL"

    raw_name = clean(attrs.get("PI_NAME"))
    if _is_address_name(raw_name):
        kcsl_name = "Unknown"
    else:
        kcsl_name = raw_name or "Unknown"

    zip_code = clean(attrs.get("ZIP_CODE"))
    return Facility(
        source=SOURCE,
        source_id=f"kcsl-{site_id}",
        name=kcsl_name,
        address=clean(attrs.get("ADDRESS")),
        city=_normalize_nj_city(attrs.get("MUNIC"), zip_code),
        state="NJ",
        zip_code=zip_code,
        county=clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=programs,
        last_updated=datetime.now(timezone.utc),
    )


def map_njems_site(
    attrs: dict,
    geometry: dict | None = None,
    ust_site_ids: set[int] | None = None,
) -> Facility:
    """Convert an NJEMS Sites feature to a Facility model.

    Key fields: SITE_ID, SITE_NAME, PROGRAM_INTEREST_NAME, ADDRESS_LINE_1,
    CITY, STATE_OR_COUNTRY_CODE, ZIP_CODE, COUNTY, MUNICIPALITY,
    X_COORD_NUM, Y_COORD_NUM.

    The NJEMS Sites layer is NJ DEP's cross-program master site registry.
    Individual sites have no program field; program context is derived from
    related enforcement, permit, and remediation records. We classify all
    NJEMS sites as "NJEMS" to identify their source database, consistent
    with how KCSL sites are classified as "KCSL".

    When ust_site_ids is provided (built from Layer 9 Underground Storage
    Tanks), sites whose SITE_ID appears in that set are tagged with
    "NJEMS, UST" so they appear in the LUST/UST program filter. This is
    the only way to identify UST sites in the NJEMS master registry since
    Layer 2 has no program-type field of its own (CIV-510).

    The CITY field in NJEMS contains municipality type suffixes identical to
    the MUNIC field in KCSL (e.g. "PISCATAWAY TWP", "NEWARK CITY"). We apply
    the same _normalize_nj_city() normalization so that stored city names
    match EPA format ("Piscataway", "Newark") and entity resolution Tier 1
    address matching works correctly across sources.

    Name extraction tries fields in order:
      1. SITE_NAME — if not address-like, use it directly
      2. PROGRAM_INTEREST_NAME — fallback when SITE_NAME is an address
      3. "Unknown" — only when no real name is available
    """
    site_id = clean(attrs.get("SITE_ID")) or ""

    lat, lon = _extract_lat_lon(geometry)

    raw_name = clean(attrs.get("SITE_NAME"))
    if _is_address_name(raw_name):
        # SITE_NAME is just the street address; try the program interest name
        alt_name = clean(attrs.get("PROGRAM_INTEREST_NAME"))
        if alt_name and not _is_address_name(alt_name):
            name = alt_name
        else:
            name = "Unknown"
    else:
        name = raw_name or "Unknown"

    # Tag sites that appear in Layer 9 (Underground Storage Tanks) with "UST"
    # so they match the LUST/UST program filter in the dashboard. The NJEMS
    # master registry does not distinguish UST sites from other program types,
    # so this cross-reference from Layer 9 is the authoritative UST indicator.
    try:
        site_id_int = int(site_id)
    except (ValueError, TypeError):
        site_id_int = None
    if ust_site_ids is not None and site_id_int is not None and site_id_int in ust_site_ids:
        programs = "NJEMS, UST"
    else:
        programs = "NJEMS"

    zip_code = clean(attrs.get("ZIP_CODE"))
    return Facility(
        source=SOURCE,
        source_id=f"njems-{site_id}",
        name=name,
        address=clean(attrs.get("ADDRESS_LINE_1")),
        city=_normalize_nj_city(attrs.get("CITY"), zip_code),
        state="NJ",
        zip_code=zip_code,
        county=clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=programs,
        last_updated=datetime.now(timezone.utc),
    )


def _pref_id_to_source_id(
    pref_id: str,
    pref_id_lookup: dict[str, int] | None = None,
) -> str:
    """Convert a PREF_ID_NUM to a facility source_id.

    All NJ DEP enforcement actions belong to the Air Quality program. Their
    PREF_ID_NUM values are Air Quality permit identifiers that map to NJEMS
    SITE_IDs via the Air Quality Permitted Facilities layer (Layer 15).

    Importantly, numeric PREF_ID_NUMs are NOT the same as NJEMS SITE_IDs.
    For example, PREF_ID_NUM "41955" maps to NJEMS SITE_ID 8616 (Evergreen
    Cemetery & Crematory), not to SITE_ID 41955 (Liquid Carbonic Corp).
    Using numeric PREF_IDs as direct SITE_IDs causes violations to be
    attributed to the wrong facility.

    The pref_id_lookup (built from Layer 15) is the authoritative source for
    resolving any PREF_ID_NUM to the correct NJEMS SITE_ID. The numeric
    direct-map fallback is only used for PREF_IDs not present in the Air
    Quality layer (which may not be Air Quality permit IDs at all).

    Args:
        pref_id: Raw PREF_ID_NUM value from the enforcement action.
        pref_id_lookup: Mapping of PREF_ID_NUM values (both numeric and
            non-numeric) to NJEMS SITE_IDs, built from Layer 15 at ingestion
            time. When provided, any PREF_ID in the lookup is resolved to the
            correct njems-{SITE_ID} facility.

    Resolution order:
        1. In lookup (any format) -> njems-{lookup[pref_id]}
        2. Numeric + not in lookup -> njems-{int(pref_id)} (fallback)
        3. Non-numeric + not in lookup -> kcsl-{pref_id} (orphan stub)
    """
    if pref_id_lookup is not None:
        site_id = pref_id_lookup.get(pref_id)
        if site_id is not None:
            return f"njems-{site_id}"
    try:
        return f"njems-{int(pref_id)}"
    except (ValueError, TypeError):
        return f"kcsl-{pref_id}"


def map_enforcement_facility(
    attrs: dict,
    pref_id_lookup: dict[str, int] | None = None,
) -> Facility:
    """Create a minimal Facility from an enforcement action's PREF_ID_NUM.

    NJ DEP enforcement actions use Air Quality PREF_ID_NUMs which map to
    NJEMS SITE_IDs via the Air Quality layer (Layer 15). When pref_id_lookup
    is provided, any PREF_ID_NUM (numeric or non-numeric) is resolved to the
    correct njems-{SITE_ID} facility. Without the lookup, numeric IDs are
    treated as direct NJEMS SITE_IDs (fallback only) and non-numeric IDs
    fall back to kcsl-{pref_id} orphan stubs.

    Programs: use the enforcement PROGRAM field if available. For IDs that
    resolve to njems-* facilities, fall back to "NJEMS" so enforcement stubs
    always carry a program classification.
    """
    pref_id = clean(attrs.get("PREF_ID_NUM"))
    if not pref_id:
        raise ValueError("Missing PREF_ID_NUM")

    program = clean(attrs.get("PROGRAM"))
    source_id = _pref_id_to_source_id(pref_id, pref_id_lookup)

    # For IDs that resolve to njems-* sites, fall back to "NJEMS"
    # so enforcement stubs always carry a program classification.
    if program is None and source_id.startswith("njems-"):
        program = "NJEMS"

    return Facility(
        source=SOURCE,
        source_id=source_id,
        name="Unknown",
        address=None,
        city=None,
        state="NJ",
        zip_code=None,
        county=None,
        lat=None,
        lon=None,
        naics_codes=None,
        sic_codes=None,
        programs=program,
        last_updated=datetime.now(timezone.utc),
    )


def _parse_date_mdy(val) -> date | None:
    return parse_date(val, formats=("%m/%d/%Y",))


def map_enforcement_action(
    attrs: dict,
    pref_id_lookup: dict[str, int] | None = None,
) -> Violation:
    """Convert an Enforcement Actions feature (Layer 18) to a Violation model.

    Key fields: VIOLATION_ID, ACTIVITY_NUM, PREF_ID_NUM, DOC_TYPE,
    START_DATE, VIOLATED_CITATION, PROGRAM, DOC_STATUS,
    NONCOMPLIANCE_DESC.

    DOC_STATUS contains the resolution status of the enforcement action
    (e.g. "Closed", "Effective", "Superseded", "Voided"). This maps to the
    ``status`` field so consultants can see whether the action is resolved.

    Args:
        attrs: Raw attribute dict from the ArcGIS feature.
        pref_id_lookup: Optional mapping of non-numeric PREF_ID_NUM values
            to NJEMS SITE_IDs (from Layer 15). When provided, Air Quality
            facility identifiers (A/H-prefix) are resolved to njems-{SITE_ID}
            so violations are linked to the correct NJEMS facility record
            rather than to an orphan kcsl- stub.
    """
    violation_id = clean(attrs.get("VIOLATION_ID"))
    activity_num = clean(attrs.get("ACTIVITY_NUM"))
    source_id_key = violation_id or activity_num or ""

    pref_id = clean(attrs.get("PREF_ID_NUM"))
    if not pref_id:
        raise ValueError("Enforcement action missing PREF_ID_NUM")

    return Violation(
        source=SOURCE,
        source_id=f"enf-{source_id_key}",
        facility_source_id=_pref_id_to_source_id(pref_id, pref_id_lookup),
        facility_source=SOURCE,
        violation_type=clean(attrs.get("DOC_TYPE")),
        violation_date=_parse_date_mdy(attrs.get("START_DATE")),
        statute=clean(attrs.get("VIOLATED_CITATION")),
        program_area=clean(attrs.get("PROGRAM")),
        status=clean(attrs.get("DOC_STATUS")),
        description=clean(attrs.get("NONCOMPLIANCE_DESC")),
    )
