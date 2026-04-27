from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pydantic import BaseModel, Field, field_validator, model_validator

# Strips formal county-type suffixes from county names at ingest time so all
# sources store a consistent canonical form (e.g. "HARRIS" not "Harris County").
# Matches CIV-278: EPA ECHO stores "SAN FRANCISCO", CA Waterboard stores
# "San Francisco", merged records pick up "SAN FRANCISCO COUNTY".
# Applied case-insensitively and the result is uppercased for uniformity.
_COUNTY_SUFFIX_RE = re.compile(
    r"\s+(?:county|parish|borough|census\s+area|municipality|city\s+and\s+borough)$",
    re.IGNORECASE,
)


_ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")

# Normalize non-standard directional abbreviations to USPS standard.
# Some sources (e.g. EPA ECHO) store NO/SO/EA/WE instead of N/S/E/W.
# Only matches the token immediately after the street number, which is the
# canonical USPS pre-directional position (e.g. "3011 NO BROADWAY" → "3011 N BROADWAY").
# Avoids false positives like "NO ADDRESS ON FILE" (no leading number).
_ALT_DIRECTIONAL_RE = re.compile(
    r"^(\d+\s+)(NO|SO|EA|WE)(\s+)",
    re.IGNORECASE,
)
_ALT_DIRECTIONAL_MAP = {"NO": "N", "SO": "S", "EA": "E", "WE": "W"}


def _normalize_directional(v: str) -> str:
    """Replace alternate directional abbreviations with USPS standard form.

    Converts NO→N, SO→S, EA→E, WE→W when the token appears immediately
    after the street number (the pre-directional position in USPS addressing).
    """
    m = _ALT_DIRECTIONAL_RE.match(v)
    if m:
        canon = _ALT_DIRECTIONAL_MAP.get(m.group(2).upper(), m.group(2))
        v = m.group(1) + canon + m.group(3) + v[m.end():]
    return v


def _deduplicate_address(v: str) -> str:
    """Return the clean base address if ``v`` is a repeated/concatenated substring.

    Handles cases like upstream EPA data corruption where the same address
    string is concatenated multiple times (with or without trailing truncation):
    e.g. "920 HAMILTON ST920 HAMILTON ST920 HAMILTON ST920 H" → "920 HAMILTON ST"

    Only applies when the candidate base string is at least 6 characters and
    repeats at least twice.  Normal addresses are returned unchanged.
    """
    n = len(v)
    # Try each candidate prefix length (shortest first)
    for plen in range(6, n // 2 + 1):
        prefix = v[:plen]
        # Confirm every full copy of prefix is identical
        full_reps = n // plen
        remainder = n % plen
        matches = all(v[i * plen:(i + 1) * plen] == prefix for i in range(full_reps))
        if not matches:
            continue
        # The tail (if any) must be a prefix of the candidate
        if remainder > 0 and v[full_reps * plen:] != prefix[:remainder]:
            continue
        # Found a repeating pattern — return just the base string
        return prefix
    return v


# Strips formal NJ municipality type suffixes from city names at ingest time.
# NJ DEP uses legal municipality names (e.g. "EDGEWATER BORO", "NORTH BERGEN TWP",
# "EDGEWATER BOROUGH") that differ from the common name used by other sources
# (e.g. EPA ECHO uses "EDGEWATER", "NORTH BERGEN"). Normalizing these at ingest
# time is required for entity resolution (Tier 1 address matching) to work across
# sources. Applied case-insensitively; original case preserved.
#
# Note: CITY and TOWN are intentionally excluded here because they are common
# parts of real city names (e.g. "Salt Lake City", "Ocean City"). The
# _normalize_city() function in resolve.py strips CITY/TOWN too, but that is
# only used for in-memory comparison during entity resolution, not for storage.
_CITY_SUFFIX_RE = re.compile(
    r"\s+(boro|borough|township|twp)$", re.IGNORECASE
)


class Facility(BaseModel):
    source: str
    source_id: str
    name: str
    address: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    county: str | None = None
    lat: float | None = None
    lon: float | None = None
    naics_codes: str | None = None
    sic_codes: str | None = None
    programs: str | None = None
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("address", mode="before")
    @classmethod
    def _normalize_address(cls, v):
        if v is None:
            return v
        v = " ".join(str(v).split())
        v = _deduplicate_address(v)
        v = _normalize_directional(v)
        return v or None

    @field_validator("city", mode="before")
    @classmethod
    def _normalize_city(cls, v):
        if v is None:
            return v
        v = str(v).strip()
        return _CITY_SUFFIX_RE.sub("", v).strip() or None

    @field_validator("county", mode="before")
    @classmethod
    def _normalize_county(cls, v):
        if v is None:
            return v
        v = _COUNTY_SUFFIX_RE.sub("", str(v).strip()).strip()
        return v.upper() if v else None

    @field_validator("state", mode="before")
    @classmethod
    def _normalize_state(cls, v):
        if v is None:
            return v
        v = str(v).strip().upper()
        return v if len(v) == 2 and v.isalpha() else None

    @field_validator("zip_code", mode="before")
    @classmethod
    def _validate_zip(cls, v):
        if v is None:
            return v
        v = str(v).strip()
        # Normalize ZIP+4 with space separator (e.g. OH EPA NPDES: "45638 8687" → "45638-8687")
        if re.match(r"^\d{5} \d{4}$", v):
            v = v[:5] + "-" + v[6:]
        # Strip trailing dash with no +4 digits (e.g. OH EPA DERR: "45804-" → "45804")
        if re.match(r"^\d{5}-$", v):
            v = v[:5]
        if not _ZIP_RE.match(v):
            return None
        # Strip placeholder ZIP+4 suffix (-0000) used by some data sources
        # when no real +4 code is available (e.g. EPA ECHO, RCRA, CA Water Board).
        if v.endswith("-0000"):
            v = v[:5]
        return v

    @field_validator("lat", mode="before")
    @classmethod
    def _validate_lat(cls, v):
        if v is None:
            return v
        try:
            f = float(v)
            return f if -90.0 <= f <= 90.0 else None
        except (ValueError, TypeError):
            return None

    @field_validator("lon", mode="before")
    @classmethod
    def _validate_lon(cls, v):
        if v is None:
            return v
        try:
            f = float(v)
            return f if -180.0 <= f <= 180.0 else None
        except (ValueError, TypeError):
            return None

    @model_validator(mode="after")
    def _reject_null_island(self) -> "Facility":
        """Null out coordinates that are exactly (0.0, 0.0) — null island.

        Coordinates at the origin (0°N, 0°E) are in the Gulf of Guinea and
        never represent a valid US facility location. They arise from geocoders
        that return 0,0 as a sentinel for "not found" rather than NULL.
        """
        if self.lat == 0.0 and self.lon == 0.0:
            self.lat = None
            self.lon = None
        return self


class Violation(BaseModel):
    source: str
    source_id: str
    facility_source_id: str
    facility_source: str
    violation_type: str | None = None
    violation_date: date | None = None
    statute: str | None = None
    program_area: str | None = None
    severity: str | None = None
    status: str | None = None
    description: str | None = None
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class IngestionRecord(BaseModel):
    source: str
    started_at: datetime
    completed_at: datetime | None = None
    status: str = "running"
    records_processed: int = 0
    error_message: str | None = None


class SourceSystem(BaseModel):
    name: str
    description: str
    base_url: str
    coverage: str


class StatsResponse(BaseModel):
    total_facilities: int
    total_violations: int
    sources: list[str]
    states: list[str]
    last_ingestion: datetime | None = None


class PaginatedResponse(BaseModel):
    items: list
    total: int
    limit: int
    offset: int
