"""Map raw Arizona DEQ ArcGIS feature data to Pydantic models.

Arizona DEQ data comes from ArcGIS Online FeatureServer as JSON features.
Three facility datasets:
  - Active Underground Storage Tanks (UST) → Facility
  - Municipal Landfills → Facility
  - Superfund Centroids → Facility

Arizona DEQ does not publish a structured violation/enforcement dataset.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Facility
from ._utils import clean, parse_float

SOURCE = "az_deq"


def map_ust_facility(attrs: dict) -> Facility:
    """Convert UST feature attributes to a Facility model.

    Key fields: Place_ID, Site, Latitude, Longitude, Last_Activ,
    Date_of_La, Media_Affe, REMEDY_INI.
    """
    place_id = attrs.get("Place_ID")
    place_id_str = str(int(place_id)) if place_id is not None else ""

    programs = []
    last_activ = clean(attrs.get("Last_Activ"))
    media = clean(attrs.get("Media_Affe"))
    if last_activ:
        programs.append(last_activ)
    if media:
        programs.append(media)

    return Facility(
        source=SOURCE,
        source_id=f"ust-{place_id_str}",
        name=clean(attrs.get("Site")) or "Unknown",
        address=None,
        city=None,
        state="AZ",
        zip_code=None,
        county=None,
        lat=parse_float(attrs.get("Latitude")),
        lon=parse_float(attrs.get("Longitude")),
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs) if programs else "UST",
        last_updated=datetime.now(timezone.utc),
    )


def map_landfill(attrs: dict) -> Facility:
    """Convert Municipal Landfill attributes to a Facility model.

    Key fields: IDNO, NAME, ADDRESS, CITY, ZIP, COUNTY,
    LATTITUDE (sic), LONGITUDE, STATUS, PLACE_TYPE.
    """
    idno = attrs.get("IDNO")
    idno_str = str(int(idno)) if idno is not None else ""

    programs = []
    place_type = clean(attrs.get("PLACE_TYPE"))
    status = clean(attrs.get("STATUS"))
    if place_type:
        programs.append(place_type)
    if status:
        programs.append(status)

    return Facility(
        source=SOURCE,
        source_id=f"landfill-{idno_str}",
        name=clean(attrs.get("NAME")) or "Unknown",
        address=clean(attrs.get("ADDRESS")),
        city=clean(attrs.get("CITY")),
        state="AZ",
        zip_code=clean(attrs.get("ZIP")),
        county=clean(attrs.get("COUNTY")),
        lat=parse_float(attrs.get("LATTITUDE")),  # Note: misspelled in source data
        lon=-abs(v) if (v := parse_float(attrs.get("LONGITUDE"))) is not None else None,
        naics_codes=None,
        sic_codes=None,
        programs=", ".join(programs) if programs else "Landfill",
        last_updated=datetime.now(timezone.utc),
    )


def map_superfund_site(feature: dict) -> Facility:
    """Convert Superfund Centroids feature to a Facility model.

    Key attribute fields: NAME, CITY, COUNTY, TYPE, PLACE_ID,
    LASTUPDATE, PUBNUMB, DATELISTED.
    Geometry is polygon; we extract centroid rings for approximate location.
    """
    attrs = feature.get("attributes", {})

    place_id = attrs.get("PLACE_ID")
    place_id_str = str(int(place_id)) if place_id is not None else ""

    # Extract centroid from polygon rings if available
    lat, lon = None, None
    geom = feature.get("geometry")
    if geom:
        rings = geom.get("rings")
        if rings and rings[0]:
            # Average all ring points for centroid approximation
            points = rings[0]
            if points:
                avg_x = sum(p[0] for p in points) / len(points)
                avg_y = sum(p[1] for p in points) / len(points)
                lon = avg_x
                lat = avg_y

    sf_type = clean(attrs.get("TYPE"))

    return Facility(
        source=SOURCE,
        source_id=f"superfund-{place_id_str}",
        name=clean(attrs.get("NAME")) or "Unknown",
        address=None,
        city=clean(attrs.get("CITY")),
        state="AZ",
        zip_code=None,
        county=clean(attrs.get("COUNTY")),
        lat=lat,
        lon=lon,
        naics_codes=None,
        sic_codes=None,
        programs=sf_type or "Superfund",
        last_updated=datetime.now(timezone.utc),
    )
