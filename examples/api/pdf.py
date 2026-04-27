"""PDF generation for screening reports.

Contains generate_map_image() and generate_screening_pdf() — pure rendering
functions that take a report dict and return bytes.  No FastAPI coupling.
"""

from __future__ import annotations

import io
import re
from datetime import date
from xml.sax.saxutils import escape as _xml_escape

from .services import _INACTIVE_VIOLATION_STATUSES

_TITLE_KEEP = {
    "LLC", "LP", "LLP", "INC", "CO", "LTD", "DBA",
    "NE", "NW", "SE", "SW", "US", "USA", "EPA",
    "II", "III", "IV",
    "ST", "DR", "AVE", "BLVD", "RD", "LN", "PL", "HWY",
    # State abbreviations (50 states + DC)
    "AL", "AK", "AZ", "AR", "CA", "CT", "DE", "DC", "FL",
    "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME",
    "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NV", "NH",
    "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
    "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
}
_TITLE_LOWER = {"of", "the", "and", "in", "at", "for", "on", "by", "to", "a", "an"}
_TRAILING_PUNCT = re.compile(r"[,.:;]+$")


def _title_case(s: str) -> str:
    """Title-case ALL-CAPS strings; leave mixed-case untouched."""
    if not s or s != s.upper():
        return s
    words = re.split(r"(\s+|-)", s)
    out = []
    for i, w in enumerate(words):
        if re.match(r"^\s+$", w) or w == "-":
            out.append(w)
            continue
        # Strip trailing punctuation for set lookups (e.g. "CA," → "CA")
        core = _TRAILING_PUNCT.sub("", w)
        suffix = w[len(core):]
        if core.upper() in _TITLE_KEEP:
            out.append(core.upper() + suffix)
        elif i > 0 and core.lower() in _TITLE_LOWER:
            out.append(core.lower() + suffix)
        elif re.match(r"^\d", w):
            out.append(w)
        else:
            out.append(w.capitalize())
    return "".join(out)


# ---------------------------------------------------------------------------
# Source display names
# ---------------------------------------------------------------------------

SOURCE_DISPLAY_NAMES: dict[str, str] = {
    "epa_echo": "EPA ECHO (Multi-Program)",
    "epa_rcra": "EPA RCRAInfo (Hazardous Waste)",
    "epa_caa": "EPA ICIS-Air (Clean Air Act)",
    "epa_sdwa": "EPA SDWIS (Safe Drinking Water)",
    "epa_sems": "EPA SEMS (Superfund/CERCLIS)",
    "tceq": "TX Commission on Environmental Quality",
    "nj_dep": "NJ Dept of Environmental Protection",
    "ca_dtsc": "CA Dept of Toxic Substances Control",
    "ca_waterboard": "CA State Water Resources Control Board",
    "ca_geotracker": "CA GeoTracker (Water Board)",
    "ny_dec": "NY Dept of Environmental Conservation",
    "ny_dec_gis": "NY DEC GIS Facility Data",
    "pa_dep": "PA Dept of Environmental Protection",
    "pa_dep_gis": "PA DEP GIS Facility Data",
    "ct_deep": "CT Dept of Energy & Environmental Protection",
    "fl_dep": "FL Dept of Environmental Protection",
    "fl_dep_stcm": "FL DEP Storage Tank Contamination Monitoring",
    "fl_dep_chaz": "FL DEP Hazardous Waste (CHAZ)",
    "fl_dep_arms": "FL DEP Air Resource Management (ARMS)",
    "fl_dep_bf": "FL DEP Brownfields & PFAS",
    "fl_dep_waste": "FL DEP Solid Waste & Institutional Controls",
    "la_deq": "LA Dept of Environmental Quality",
    "oh_epa": "Ohio EPA",
    "dc_doee": "DC Dept of Energy & Environment",
    "co_cdphe": "CO Dept of Public Health & Environment",
    "tn_tdec": "TN Dept of Environment & Conservation",
    "wa_ecy": "WA Dept of Ecology",
    "ne_dee": "NE Dept of Environment & Energy",
    "mo_dnr": "MO Dept of Natural Resources",
    "id_deq": "ID Dept of Environmental Quality",
    "va_deq": "VA Dept of Environmental Quality",
    "ia_dnr": "IA Dept of Natural Resources",
    "ks_kdhe": "KS Dept of Health & Environment",
    "ok_deq": "OK Dept of Environmental Quality",
    "or_deq": "OR Dept of Environmental Quality",
    "ar_deq": "AR Dept of Environmental Quality",
    "mi_egle": "MI Dept of Environment, Great Lakes & Energy",
    "wy_deq": "WY Dept of Environmental Quality",
    "ky_dep": "KY Dept of Environmental Protection",
    "ut_deq": "UT Dept of Environmental Quality",
    "nh_des": "NH Dept of Environmental Services",
    "de_dnrec": "DE Dept of Natural Resources & Environmental Control",
    "ak_dec": "AK Dept of Environmental Conservation",
    "vt_dec": "VT Dept of Environmental Conservation",
    "me_dep": "ME Dept of Environmental Protection",
    "wi_dnr": "WI Dept of Natural Resources",
    "sd_danr": "SD Dept of Agriculture & Natural Resources",
    "al_adem": "AL Dept of Environmental Management",
    "sc_dhec": "SC Dept of Health & Environmental Control",
    "ma_dep": "MA Dept of Environmental Protection",
    "wv_dep": "WV Dept of Environmental Protection",
    "mt_deq": "MT Dept of Environmental Quality",
    "nv_dep": "NV Dept of Environmental Protection",
    "nc_deq": "NC Dept of Environmental Quality",
    "nd_deq": "ND Dept of Environmental Quality",
    "nm_nmed": "NM Environment Dept",
    "md_mde": "MD Dept of the Environment",
    "ga_epd": "GA Environmental Protection Division",
    "az_deq": "AZ Dept of Environmental Quality",
    "il_epa": "IL Environmental Protection Agency",
    "hi_doh": "HI Dept of Health",
    "ri_dem": "RI Dept of Environmental Management",
    "mn_pca": "MN Pollution Control Agency",
    "in_idem": "IN Dept of Environmental Management",
    "ms_mdeq": "MS Dept of Environmental Quality",
}


def _source_display_name(source_key: str) -> str:
    """Return a human-readable display name for a source key."""
    return SOURCE_DISPLAY_NAMES.get(
        source_key, source_key.upper().replace("_", " ")
    )


# ---------------------------------------------------------------------------
# Title-case helper
# ---------------------------------------------------------------------------

_KEEP_UPPER = {
    "EPA", "LLC", "LP", "LLP", "INC", "CO", "LTD", "USA", "US", "DBA",
    "CVS", "BMW", "IBM", "AT&T", "UPS", "DHL", "UST", "LUST", "RCRA",
    "CWA", "CAA", "SDWA", "NPDES", "WWTP", "PCB", "PFAS", "BP", "II",
    "III", "IV", "SQG", "TSD", "POTW", "WM",
}
_LOWER_WORDS = {"of", "at", "and", "the", "in", "for", "to", "with", "by", "on", "or", "a", "an"}


def _pdf_title_case(s: str) -> str:
    """Convert ALL-CAPS names to title case, preserving acronyms."""
    if not s or s != s.upper():
        return s
    words = s.split()
    result = []
    for i, w in enumerate(words):
        # Strip trailing punctuation for set lookups (e.g. "INC." → "INC")
        core = _TRAILING_PUNCT.sub("", w)
        suffix = w[len(core):]
        if i > 0 and core.lower() in _LOWER_WORDS:
            result.append(core.lower() + suffix)
        elif core.upper() in _KEEP_UPPER:
            result.append(core.upper() + suffix)
        elif w[0:1].isdigit():
            result.append(w)
        else:
            result.append(w.capitalize())
    return " ".join(result)


# ---------------------------------------------------------------------------
# Program display names
# ---------------------------------------------------------------------------

PROGRAM_DISPLAY_NAMES: dict[str, str] = {
    # Federal EPA programs
    "CWA":    "Clean Water Act (EPA)",
    "RCRA":   "Resource Conservation and Recovery Act — Hazardous Waste (EPA)",
    # Generic program codes (used by TCEQ and potentially other state sources)
    "AIR":      "Air Permits",
    "AIROP":    "Air Operating Permit",
    "AQNP":     "Air Quality Network Permit",
    "HW":       "Hazardous Waste",
    "IHW":      "Industrial and Hazardous Waste",
    "IHWCA":    "Industrial and Hazardous Waste Corrective Action",
    "IHWNP":    "Industrial Hazardous Waste Notification Program",
    "WWPERMIT": "Wastewater Permit",
    "PSTREG":   "Petroleum Storage Tank Registration",
    "PSTNR":    "Petroleum Storage Tank Non-Reporter",
    "LPSTRMD":  "Lead Paint Stripping Removal Discharge",
    "STAGEII":  "Stage II Vapor Recovery",
    "AIRNSR":   "Air New Source Review",
    "MSD":      "Municipal Solid Waste Disposal",
    "TIRES":    "Used Tire Management",
    "USEDOIL":  "Used Oil",
    "STORM":    "Stormwater",
    "IOP":      "Industrial Operations Permit",
    "DRYCLEAN": "Dry Cleaner Program",
    "P2PLAN":   "Pollution Prevention Plan",
    "VCP":      "Voluntary Cleanup Program",
    "SDA":      "Stormwater Discharge Authorization",
    "CWCP":     "Clean Water Construction Program",
    "USTOL":    "Underground Storage Tank On-Line",
    "SLUDGE":   "Sludge Land Application",
    # NJ DEP programs
    "NJEMS": "NJ DEP Site Registry",
    "KCSL":  "Known Contaminated Site List (NJ DEP)",
    "UST":   "Underground Storage Tank",
    # NJ DEP KCSL status codes (compound strings in Programs field)
    "ACTIVE - UHOT":     "Active — Underground Heating Oil Tank (NJ DEP KCSL status)",
    "ACTIVE - POST REM": "Active — Post Remediation (NJ DEP KCSL status)",
    "ACTIVE - CLOSED":   "Active — Closed (NJ DEP KCSL status)",
    "ACTIVE - MOA":      "Active — Memorandum of Agreement (NJ DEP KCSL status)",
    "ACTIVE - NFA":      "Active — No Further Action (NJ DEP KCSL status)",
}

# TCEQ-specific display names — appended with "(TCEQ)" only for TX facilities.
# These codes originate from TCEQ but other state sources may reuse the same
# short codes (e.g. NE DEE uses "AIR", OR DEQ uses "HW").  CIV-709.
_TCEQ_DISPLAY_SUFFIXES: frozenset[str] = frozenset({
    "AIR", "AIROP", "AQNP", "HW", "IHW", "IHWCA", "IHWNP", "WWPERMIT",
    "PSTREG", "PSTNR", "LPSTRMD", "STAGEII", "AIRNSR", "MSD", "TIRES",
    "USEDOIL", "STORM", "IOP", "DRYCLEAN", "P2PLAN", "VCP", "SDA", "CWCP",
    "USTOL", "SLUDGE",
})


def _program_display_name(code: str, state: str | None = None) -> str | None:
    """Return human-readable display name for a program code.

    For codes in _TCEQ_DISPLAY_SUFFIXES, appends " (TCEQ)" only when *state*
    is TX.  Returns None if the code has no entry in PROGRAM_DISPLAY_NAMES.
    """
    upper = code.upper()
    base = PROGRAM_DISPLAY_NAMES.get(upper)
    if base is None:
        return None
    if upper in _TCEQ_DISPLAY_SUFFIXES and state and state.upper() == "TX":
        return f"{base} (TCEQ)"
    return base


# PDF table uses abbreviated program names so each entry fits on one line in the
# narrow Programs column (~103pt at 0.22× doc width).  Full names are preserved
# in CSV export and detail views via PROGRAM_DISPLAY_NAMES above.
_PDF_PROGRAM_NAMES: dict[str, str] = {
    # Federal EPA programs
    "CWA":    "Clean Water Act",
    "RCRA":   "RCRA Hazardous Waste",
    # TCEQ (Texas) programs — abbreviated to fit in one table line
    "AIR":      "Air Permits",
    "AIROP":    "Air Operating Permit",
    "AQNP":     "Air Quality Network Permit",
    "HW":       "Hazardous Waste",
    "IHW":      "Ind. Hazardous Waste",
    "IHWCA":    "IHW Corrective Action",
    "IHWNP":    "IHW Notification Program",
    "WWPERMIT": "Wastewater Permit",
    "PSTREG":   "Petroleum Storage Tank",
    "PSTNR":    "PST Non-Reporter",
    "LPSTRMD":  "Lead Paint Removal",
    "STAGEII":  "Stage II Vapor",
    "AIRNSR":   "Air New Source Review",
    "MSD":      "Solid Waste Disposal",
    "TIRES":    "Used Tire Management",
    "USEDOIL":  "Used Oil",
    "STORM":    "Stormwater",
    "IOP":      "Ind. Operations Permit",
    "DRYCLEAN": "Dry Cleaner",
    "P2PLAN":   "Pollution Prev. Plan",
    "VCP":      "Voluntary Cleanup",
    "SDA":      "Stormwater Disc. Auth.",
    "CWCP":     "Clean Water Const.",
    "USTOL":    "UST On-Line",
    "SLUDGE":   "Sludge Land Application",
    # NJ DEP programs
    "NJEMS": "NJ DEP Site Registry",
    "KCSL":  "Known Contaminated Site (NJ)",
    "UST":   "Underground Storage Tank",
    # NJ DEP KCSL status codes
    "ACTIVE - UHOT":     "Active — UHOT (NJ DEP)",
    "ACTIVE - POST REM": "Active — Post Rem (NJ DEP)",
    "ACTIVE - CLOSED":   "Active — Closed (NJ DEP)",
    "ACTIVE - MOA":      "Active — MOA (NJ DEP)",
    "ACTIVE - NFA":      "Active — NFA (NJ DEP)",
}


def _pdf_humanize_programs(programs: str, state: str | None = None) -> str:
    """Return abbreviated program labels for the PDF table Programs column.

    Uses shorter names than _humanize_programs() so each entry fits on a single
    line in the ~103pt Programs column, preventing multi-line wrapping that can
    cause cell content to be clipped at page boundaries (CIV-506).
    """
    if not programs:
        return ""
    parts = [p.strip() for p in programs.split(",")]
    return ", ".join(
        _PDF_PROGRAM_NAMES.get(p.upper(), _program_display_name(p.upper(), state) or p)
        for p in parts
        if p and p.upper() != "NONE SPECIFIED"
    )


# ---------------------------------------------------------------------------
# Source URL helper
# ---------------------------------------------------------------------------

def _source_url(source: str, source_id: str, name: str | None = None) -> str | None:
    """Return the upstream government URL for a facility, if known."""
    if source == "epa_echo":
        return f"https://echo.epa.gov/detailed-facility-report?fid={source_id}"
    if source == "epa_caa":
        return f"https://echo.epa.gov/detailed-facility-report?fid={source_id}&sys=AIR"
    if source == "epa_rcra":
        return f"https://echo.epa.gov/detailed-facility-report?fid={source_id}&sys=RCRA"
    if source == "epa_sdwa":
        # source_id values are ECHO registry IDs, not PWSIDs — omit &sys=SDW so
        # ECHO DFR resolves by registry ID rather than requiring a PWSID lookup.
        return f"https://echo.epa.gov/detailed-facility-report?fid={source_id}"
    if source == "epa_sems":
        return f"https://echo.epa.gov/detailed-facility-report?fid={source_id}&sys=SFM"
    if source.startswith("epa_"):
        return f"https://echo.epa.gov/detailed-facility-report?fid={source_id}"
    if source == "tceq":
        # Use TCEQ CRPUB direct record link when an RN number is available.
        if source_id and source_id.startswith("RN"):
            return (
                f"https://www15.tceq.texas.gov/crpub/index.cfm"
                f"?fuseaction=iwr.rslts&REGNUMBER={source_id}"
            )
        return "https://www15.tceq.texas.gov/crpub/"
    if source == "ny_dec":
        # Route by source_id prefix to the specific NY DEC database.
        if source_id.startswith("rem-"):
            program_num = source_id[4:]
            if program_num:
                from urllib.parse import quote
                return (
                    f"https://www.dec.ny.gov/cfmx/extapps/derexternal/index.cfm"
                    f"?pageid=3&programCode=ERASR&keyId={quote(program_num)}"
                )
            return "https://data.ny.gov/Energy-Environment/Environmental-Remediation-Sites/c6ci-rzpg"
        if source_id.startswith("sw-"):
            return "https://data.ny.gov/Energy-Environment/Solid-Waste-Management-Facilities/2fni-raj8"
        if source_id.startswith("oc-"):
            case_num = source_id[3:]
            if case_num:
                from urllib.parse import quote
                return (
                    f"https://www.dec.ny.gov/cfmx/extapps/derexternal/index.cfm"
                    f"?pageid=4&programid=0&keyword={quote(case_num)}"
                )
            return "https://www.dec.ny.gov/cfmx/extapps/derexternal/index.cfm?pageid=4"
        return "https://data.ny.gov/browse?q=DEC&sortBy=relevance&tags=environment"
    if source == "ny_dec_gis":
        _GIS_BASE = (
            "https://gisservices.dec.ny.gov/arcgis/rest/services"
            "/dil_permits_and_regs/MapServer"
        )
        _PREFIX_LAYER = {
            "pbs-": 21, "cbs-": 23, "mosf-": 22,
            "air-": 3, "airv-": 4, "airs-": 5,
            "landfill-": 12, "hwgen-": 6, "hwtsd-": 2,
            "spdes-": 18, "msgp-": 20, "mine-": 24,
        }
        for prefix, layer in _PREFIX_LAYER.items():
            if source_id.startswith(prefix):
                return f"{_GIS_BASE}/{layer}"
        return _GIS_BASE
    if source == "pa_dep":
        # Route by source_id prefix to the relevant PA DEP database.
        if source_id.startswith("eis-"):
            return "https://www.ahs.dep.pa.gov/eFACTSWeb/"
        if source_id.startswith("sdwa-"):
            return "https://www.padata.state.pa.us/PASAFE/pubView.aspx"
        if source_id.startswith("well-"):
            return "https://www.paoilandgasreporting.state.pa.us/publicreports/"
        return "https://www.dep.pa.gov/"
    if source == "pa_dep_gis":
        # Route by source_id prefix to the specific PA DEP GIS layer record.
        # ArcGIS HTML query URL shows human-readable facility attributes.
        _PASDA_BASE = (
            "https://mapservices.pasda.psu.edu/server/rest/services"
            "/pasda/DEP/MapServer"
        )
        from urllib.parse import quote
        if source_id.startswith("tank-"):
            raw_id = source_id[len("tank-"):]
            if raw_id:
                # Layer 27: Storage Tanks Active; field FACILITY_I (truncated FACILITY_ID)
                return (
                    f"{_PASDA_BASE}/27/query"
                    f"?where=FACILITY_I+%3D+%27{quote(raw_id, safe='')}%27"
                    f"&outFields=*&f=html"
                )
            return "https://www.dep.pa.gov/Business/Land/Tanks/Pages/default.aspx"
        if source_id.startswith("cleanup-"):
            raw_id = source_id[len("cleanup-"):]
            if raw_id:
                # Layer 18: Land Recycling Cleanup Locations; field SITE_ID
                return (
                    f"{_PASDA_BASE}/18/query"
                    f"?where=SITE_ID+%3D+%27{quote(raw_id, safe='')}%27"
                    f"&outFields=*&f=html"
                )
            return "https://www.dep.pa.gov/Business/Land/LandRecycling/Pages/default.aspx"
        if source_id.startswith("hwcap-"):
            raw_id = source_id[len("hwcap-"):]
            if raw_id:
                # Layer 5: Captive Hazardous Waste Operations; field SITE_ID
                return (
                    f"{_PASDA_BASE}/5/query"
                    f"?where=SITE_ID+%3D+%27{quote(raw_id, safe='')}%27"
                    f"&outFields=*&f=html"
                )
            return "https://www.dep.pa.gov/Business/Waste/HazardousWaste/Pages/default.aspx"
        if source_id.startswith("hwcom-"):
            raw_id = source_id[len("hwcom-"):]
            if raw_id:
                # Layer 9: Commercial Hazardous Waste Operations; field SITE_ID
                return (
                    f"{_PASDA_BASE}/9/query"
                    f"?where=SITE_ID+%3D+%27{quote(raw_id, safe='')}%27"
                    f"&outFields=*&f=html"
                )
            return "https://www.dep.pa.gov/Business/Waste/HazardousWaste/Pages/default.aspx"
        if source_id.startswith("mwaste-"):
            raw_id = source_id[len("mwaste-"):]
            if raw_id:
                # Layer 20: Municipal Waste Operations; field SITE_ID
                return (
                    f"{_PASDA_BASE}/20/query"
                    f"?where=SITE_ID+%3D+%27{quote(raw_id, safe='')}%27"
                    f"&outFields=*&f=html"
                )
            return "https://www.dep.pa.gov/Business/Waste/MunicipalWaste/Pages/default.aspx"
        if source_id.startswith("rwaste-"):
            raw_id = source_id[len("rwaste-"):]
            if raw_id:
                # Layer 26: Residual Waste Operations; field SITE_ID
                return (
                    f"{_PASDA_BASE}/26/query"
                    f"?where=SITE_ID+%3D+%27{quote(raw_id, safe='')}%27"
                    f"&outFields=*&f=html"
                )
            return "https://www.dep.pa.gov/Business/Waste/ResidualWaste/Pages/default.aspx"
        if source_id.startswith("aml-"):
            raw_id = source_id[len("aml-"):]
            if raw_id:
                # Layer 0: AML Inventory Points; field PROBLEM_AR (truncated PROBLEM_AREA)
                return (
                    f"{_PASDA_BASE}/0/query"
                    f"?where=PROBLEM_AR+%3D+%27{quote(raw_id, safe='')}%27"
                    f"&outFields=*&f=html"
                )
            return "https://www.dep.pa.gov/Business/Land/BrownfieldsAML/AbandonedMines/Pages/default.aspx"
        # Fallback: generic PASDA DEP MapServer
        return _PASDA_BASE
    if source == "ct_deep":
        # Route by source_id prefix to the specific CT DEEP database.
        if source_id.startswith("ust-"):
            # listsearch.ct.gov is dead — link to CT Open Data UST dataset instead.
            return "https://data.ct.gov/Environment-and-Natural-Resources/Underground-Storage-Tanks-USTs-Facility-and-Tank-D/utni-rddb"
        if source_id.startswith("rem-"):
            return "https://portal.ct.gov/deep/remediation-site-clean-up/"
        return "https://portal.ct.gov/DEEP/"
    if source == "la_deq":
        return "https://www.deq.louisiana.gov/"
    if source == "fl_dep":
        # source_id is prefixed: wafr-<id>, eric-<id>, chaz-<id>, cpv-<id>
        # DepEric is dead — DepNexus supports facility-by-ID deep links.
        if source_id.startswith("eric-"):
            eric_id = source_id[len("eric-"):]
            if eric_id:
                return (
                    f"https://prodenv.dep.state.fl.us/DepNexus/public/facilitysearch"
                    f"?facility.id={eric_id}&facility.searchFor=0&newSearch=Yes"
                )
            return "https://prodenv.dep.state.fl.us/DepNexus/public/searchPortal"
        if source_id.startswith("wafr-"):
            wafr_id = source_id[len("wafr-"):]
            if wafr_id:
                from urllib.parse import quote
                return (
                    "https://ca.dep.state.fl.us/arcgis/rest/services/OpenData"
                    f"/WAFR/MapServer/0/query"
                    f"?where=FACILITY_ID+%3D+%27{quote(wafr_id, safe='')}%27"
                    f"&outFields=*&f=html"
                )
            return "https://ca.dep.state.fl.us/mapdirect/"
        if source_id.startswith("chaz-"):
            handler_id = source_id[len("chaz-"):]
            if handler_id:
                return f"https://echo.epa.gov/detailed-facility-report?fid={handler_id}&sys=RCRA"
        return "https://floridadep.gov/"
    if source == "fl_dep_stcm":
        # source_id is prefixed: stcm-<FACILITY_ID>, pcts-<DISCHARGE_ID>, dryclean-<ERIC_ID>
        # DepNexus supports facility-by-ID deep links for STCM FACILITY_IDs.
        from urllib.parse import quote
        _FL_ARCGIS = "https://ca.dep.state.fl.us/arcgis/rest/services/OpenData"
        if source_id.startswith("stcm-"):
            fac_id = source_id[len("stcm-"):]
            if fac_id:
                return (
                    f"https://prodenv.dep.state.fl.us/DepNexus/public/facilitysearch"
                    f"?facility.id={quote(fac_id, safe='')}&facility.searchFor=0&newSearch=Yes"
                )
            return "https://prodenv.dep.state.fl.us/DepNexus/public/searchPortal"
        if source_id.startswith("pcts-"):
            discharge_id = source_id[len("pcts-"):]
            if discharge_id:
                return (
                    f"{_FL_ARCGIS}/DWM_STCM/MapServer/2/query"
                    f"?where=DISCHARGE_ID+%3D+%27{quote(discharge_id, safe='')}%27"
                    f"&outFields=*&f=html"
                )
            return "https://prodenv.dep.state.fl.us/DepNexus/public/searchPortal"
        if source_id.startswith("dryclean-"):
            eric_id = source_id[len("dryclean-"):]
            if eric_id:
                return (
                    f"{_FL_ARCGIS}/DWM_STCM/MapServer/4/query"
                    f"?where=ERIC_ID+%3D+%27{quote(eric_id, safe='')}%27"
                    f"&outFields=*&f=html"
                )
        return "https://floridadep.gov/"
    if source == "fl_dep_chaz":
        # source_id is prefixed: chaz-<HANDLER_ID>, chaz-enf-<ENF_ID>
        # HANDLER_ID is an EPA RCRA handler ID — link to ECHO RCRA facility report.
        if source_id.startswith("chaz-enf-"):
            handler_id = source_id[len("chaz-enf-"):]
            if handler_id:
                return f"https://echo.epa.gov/detailed-facility-report?fid={handler_id}&sys=RCRA"
        elif source_id.startswith("chaz-"):
            handler_id = source_id[len("chaz-"):]
            if handler_id:
                return f"https://echo.epa.gov/detailed-facility-report?fid={handler_id}&sys=RCRA"
        return "https://floridadep.gov/"
    if source == "fl_dep_arms":
        # source_id is prefixed: arms-<AIRS_ID>
        # Link to the ArcGIS record for the specific facility.
        from urllib.parse import quote
        if source_id.startswith("arms-"):
            airs_id = source_id[len("arms-"):]
            if airs_id:
                return (
                    "https://ca.dep.state.fl.us/arcgis/rest/services/OpenData"
                    f"/ARMS/MapServer/0/query"
                    f"?where=AIRS_ID+%3D+%27{quote(airs_id, safe='')}%27"
                    f"&outFields=*&f=html"
                )
        return "https://floridadep.gov/air/permitting-compliance/content/air-permit-review-section"
    if source == "fl_dep_bf":
        from urllib.parse import quote
        _FL_ARCGIS = "https://ca.dep.state.fl.us/arcgis/rest/services/OpenData"
        if source_id and source_id.startswith("bf-"):
            site_id = source_id[len("bf-"):]
            if site_id:
                return (
                    f"{_FL_ARCGIS}/BROWNFIELD_AREAS/MapServer/1/query"
                    f"?where=BF_SITE_ID+%3D+%27{quote(site_id, safe='')}%27"
                    f"+OR+SITE_ID+%3D+%27{quote(site_id, safe='')}%27"
                    f"+OR+OBJECTID+%3D+%27{quote(site_id, safe='')}%27"
                    f"&outFields=*&f=html"
                )
            return "https://floridadep.gov/waste/waste-cleanup/content/brownfields"
        if source_id and source_id.startswith("pfas-"):
            fac_id = source_id[len("pfas-"):]
            if fac_id:
                return (
                    f"{_FL_ARCGIS}/CLEANUP_SP/MapServer/4/query"
                    f"?where=SOURCE_FACILITY_ID+%3D+%27{quote(fac_id, safe='')}%27"
                    f"+OR+ERIC_ID+%3D+%27{quote(fac_id, safe='')}%27"
                    f"&outFields=*&f=html"
                )
            return "https://floridadep.gov/waste/waste-cleanup/content/pfas"
        return "https://floridadep.gov/"
    if source == "fl_dep_waste":
        from urllib.parse import quote
        _FL_ARCGIS = "https://ca.dep.state.fl.us/arcgis/rest/services/OpenData"
        if source_id and source_id.startswith("swaste-"):
            fac_id = source_id[len("swaste-"):]
            if fac_id:
                return (
                    f"{_FL_ARCGIS}/DWM_WASTE_ICR_BACKG/MapServer/1/query"
                    f"?where=FACILITY_ID+%3D+%27{quote(fac_id, safe='')}%27"
                    f"&outFields=*&f=html"
                )
            return "https://floridadep.gov/waste/permitting-compliance/content/solid-waste"
        if source_id and source_id.startswith("icr-"):
            fac_id = source_id[len("icr-"):]
            if fac_id:
                return (
                    f"{_FL_ARCGIS}/DWM_WASTE_ICR_BACKG/MapServer/12/query"
                    f"?where=PRIMARY_FACILITY_ID+%3D+%27{quote(fac_id, safe='')}%27"
                    f"+OR+PRIMARY_FA+%3D+%27{quote(fac_id, safe='')}%27"
                    f"&outFields=*&f=html"
                )
            return "https://floridadep.gov/waste/waste-cleanup/content/institutional-controls-registry"
        return "https://floridadep.gov/"
    if source == "oh_epa":
        # Route by source_id prefix to the specific Ohio EPA ArcGIS dataset.
        # ArcGIS HTML query URLs show human-readable facility attributes.
        from urllib.parse import quote
        if source_id.startswith("npdes-"):
            epa_no = source_id[len("npdes-"):]
            if epa_no:
                return (
                    "https://geo.epa.ohio.gov/arcgis/rest/services"
                    "/Hosted/NPDES_SELECT_FACS/FeatureServer/0/query"
                    f"?where=ohio_epa_no+%3D+%27{quote(epa_no, safe='')}%27"
                    "&outFields=*&f=html"
                )
        if source_id.startswith("dmwm-"):
            place_id = source_id[len("dmwm-"):]
            if place_id:
                return (
                    "https://geo.epa.ohio.gov/arcgis/rest/services"
                    "/WasteMgmt/DMWM_Regulated_Facilities/MapServer/0/query"
                    f"?where=FP_PLACE_ID+%3D+{quote(place_id, safe='')}"
                    "&outFields=*&f=html"
                )
        if source_id.startswith("derr-"):
            derr_id = source_id[len("derr-"):]
            if derr_id:
                return (
                    "https://geo.epa.ohio.gov/arcgis/rest/services"
                    "/Hosted/DERR_SITES_POINTS/FeatureServer/0/query"
                    f"?where=derr_id+%3D+{quote(derr_id, safe='')}"
                    "&outFields=*&f=html"
                )
        if source_id.startswith("spill-"):
            case_num = source_id[len("spill-"):]
            if case_num:
                return (
                    "https://geo.epa.ohio.gov/arcgis/rest/services"
                    "/EmergResponse/Spills2_OpenData/MapServer/0/query"
                    f"?where=casenumber+%3D+%27{quote(case_num, safe='')}%27"
                    "&outFields=*&f=html"
                )
        return "https://geo.epa.ohio.gov/"
    if source == "nj_dep":
        # dep.nj.gov/njems/site/{id} is dead (404). DataMiner is the correct tool.
        if source_id and source_id.startswith("njems-"):
            site_id = source_id[len("njems-"):]
            if site_id:
                return f"https://njems.nj.gov/DataMiner/Search/SearchBySite?SiteId={site_id}"
        if source_id and source_id.startswith("kcsl-"):
            # KCSL source_ids use NJEMS SITE_ID (numeric) when available,
            # falling back to PI_NUMBER. Numeric IDs get DataMiner deep links;
            # non-numeric PI_NUMBERs fall back to the generic KCSL portal.
            kcsl_id = source_id[len("kcsl-"):]
            if kcsl_id and kcsl_id.isdigit():
                return f"https://njems.nj.gov/DataMiner/Search/SearchBySite?SiteId={kcsl_id}"
            return "https://dep.nj.gov/srp/kcsnj/"
        return "https://gisdata-njdep.opendata.arcgis.com/"
    if source == "ca_dtsc":
        if source_id.startswith("cleanup-"):
            site_code = source_id[len("cleanup-"):]
            if site_code:
                return f"https://www.envirostor.dtsc.ca.gov/public/detail?global_id={site_code}"
        if source_id.startswith("hazwaste-"):
            epa_id = source_id[len("hazwaste-"):]
            if epa_id:
                return f"https://echo.epa.gov/detailed-facility-report?fid={epa_id}&sys=RCRA"
        return "https://www.envirostor.dtsc.ca.gov/public/"
    if source == "ca_geotracker":
        return f"https://geotracker.waterboards.ca.gov/profile_report?global_id={source_id.replace(' ', '')}"
    if source == "ca_waterboard":
        if source_id.startswith("ciwqs-wdid-"):
            wdid = source_id[len("ciwqs-wdid-"):].replace(" ", "")
            if wdid:
                return (
                    f"https://ciwqs.waterboards.ca.gov/ciwqs/readOnly/CiwqsReportServlet"
                    f"?inCommand=reset&reportName=RegdEntityByWDID&wdid={wdid}"
                )
        if source_id.startswith("ciwqs-"):
            ciwqs_id = source_id[len("ciwqs-"):].replace(" ", "")
            if ciwqs_id:
                return f"https://ciwqs.waterboards.ca.gov/ciwqs/readOnly/CiwqsReportServlet?inCommand=drilldownBySite&siteID={ciwqs_id}"
        if source_id.startswith("smarts-"):
            wdid = source_id[len("smarts-"):].replace(" ", "")
            if wdid:
                return f"https://geotracker.waterboards.ca.gov/profile_report?global_id={wdid}"
        return "https://www.waterboards.ca.gov/water_issues/programs/npdes/"
    if source == "wa_ecy":
        return "https://apps.ecology.wa.gov/facilitysite/FacilitySite/FacilitySiteSearch"
    if source == "or_deq":
        return "https://www.oregon.gov/deq/"
    if source == "nc_deq":
        # Route by source_id prefix to the relevant NC DEQ database.
        if source_id.startswith("hw-"):
            # hw- prefix uses EPA RCRA handler IDs (e.g. NCD000123456) — link to ECHO RCRA.
            handler_id = source_id[3:]
            if handler_id:
                return f"https://echo.epa.gov/detailed-facility-report?fid={handler_id}&sys=RCRA"
            return "https://deq.nc.gov/about/divisions/waste-management/hazardous-waste"
        if source_id.startswith("ust-"):
            return "https://deq.nc.gov/about/divisions/waste-management/underground-storage-tanks"
        if source_id.startswith("ast-"):
            return "https://deq.nc.gov/about/divisions/waste-management/aboveground-storage-tanks"
        if source_id.startswith("sso-"):
            return "https://deq.nc.gov/about/divisions/water-resources/permitting/wastewater-compliance-reporting"
        return "https://deq.nc.gov/"
    if source == "mi_egle":
        return "https://www.michigan.gov/egle"
    if source == "mn_pca":
        return "https://www.pca.state.mn.us/"
    if source == "wi_dnr":
        return "https://dnr.wisconsin.gov/"
    if source == "il_epa":
        return "https://epa.illinois.gov/"
    if source == "co_cdphe":
        return "https://cdphe.colorado.gov/"
    if source == "ga_epd":
        return "https://epd.georgia.gov/"
    if source == "sc_dhec":
        # DHEC dissolved 2024 — successor is SC Dept of Environmental Services.
        return "https://des.sc.gov/"
    if source == "va_deq":
        return "https://www.deq.virginia.gov/"
    if source == "md_mde":
        return "https://mde.maryland.gov/"
    if source == "ma_dep":
        return "https://www.mass.gov/dep"
    if source == "tn_tdec":
        return "https://www.tn.gov/environment.html"
    if source == "ky_dep":
        return "https://eec.ky.gov/"
    if source == "in_idem":
        return "https://www.in.gov/idem/"
    if source == "mo_dnr":
        return "https://dnr.mo.gov/"
    if source == "ia_dnr":
        return "https://www.iowadnr.gov/"
    if source == "ne_dee":
        return "https://dee.nebraska.gov/"
    if source == "ks_kdhe":
        return "https://www.kdhe.ks.gov/"
    if source == "ok_deq":
        return "https://www.deq.ok.gov/"
    if source == "ar_deq":
        return "https://www.adeq.state.ar.us/"
    if source == "ms_mdeq":
        return "https://www.mdeq.ms.gov/"
    if source == "al_adem":
        return "https://adem.alabama.gov/"
    if source == "az_deq":
        return "https://azdeq.gov/"
    if source == "nm_nmed":
        return "https://www.env.nm.gov/"
    if source == "id_deq":
        return "https://www.deq.idaho.gov/"
    if source == "ut_deq":
        return "https://deq.utah.gov/"
    if source == "nv_dep":
        return "https://ndep.nv.gov/"
    if source == "hi_doh":
        return "https://health.hawaii.gov/"
    if source == "ak_dec":
        return "https://dec.alaska.gov/"
    if source == "me_dep":
        return "https://www.maine.gov/dep/"
    if source == "nh_des":
        return "https://www.des.nh.gov/"
    if source == "vt_dec":
        return "https://dec.vermont.gov/"
    if source == "ri_dem":
        return "https://dem.ri.gov/"
    if source == "de_dnrec":
        return "https://dnrec.delaware.gov/"
    if source == "dc_doee":
        return "https://doee.dc.gov/"
    if source == "wv_dep":
        return "https://dep.wv.gov/"
    if source == "nd_deq":
        return "https://deq.nd.gov/"
    if source == "sd_danr":
        return "https://danr.sd.gov/"
    if source == "mt_deq":
        return "https://deq.mt.gov/"
    if source == "wy_deq":
        return "https://deq.wyoming.gov/"
    return None


# ---------------------------------------------------------------------------
# Violation recency helper
# ---------------------------------------------------------------------------

def _violation_recency_label(date_str: str | None) -> str:
    """Return a recency label for a violation date string (YYYY-MM-DD).

    Mirrors the dashboard's recency labels so PDF and UI are consistent:
    recent (<180 days), <1yr (<365 days), 1-3yr, 3-5yr, >5yr.
    Returns empty string if date is None, unparseable, or a sentinel value.
    """
    if not date_str:
        return ""
    try:
        vdate = date.fromisoformat(str(date_str)[:10])
    except ValueError:
        return ""
    today = date.today()
    # Reject sentinel dates (more than 5 years in the future)
    if vdate.year > today.year + 5:
        return ""
    delta_days = (today - vdate).days
    if delta_days < 0:
        return ""
    if delta_days < 180:
        return "recent"
    if delta_days < 365:
        return "<1yr"
    if delta_days < 1095:
        return "1-3yr"
    if delta_days < 1825:
        return "3-5yr"
    return ">5yr"


def _violation_status_label(raw_status: str | None) -> str:
    """Return a normalized display label for a violation status field.

    Maps the raw DB status to either a specific resolved/closed status string
    or "Active" — avoiding source-specific labels like "Effective" (NJ DEP)
    that are opaque to consultants, and ensuring violations with no status are
    shown as "Active" consistent with active_violation_count logic (CIV-641).

    - Status in _INACTIVE_VIOLATION_STATUSES → title-cased resolved label
      (e.g. "closed" → "Closed", "resolved" → "Resolved")
    - Anything else (None, "Active", "Effective", unknown codes) → "Active"
    """
    if raw_status:
        normalized = raw_status.strip().lower()
        if normalized in _INACTIVE_VIOLATION_STATUSES:
            return raw_status.strip().title()
    return "Active"


# ---------------------------------------------------------------------------
# OSM basemap tile fetching (lightweight, no contextily/rasterio/GDAL)
# ---------------------------------------------------------------------------

import math
import os
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_TILE_CACHE_DIR = Path(os.environ.get("ENVIOLATIONS_TILE_CACHE_DIR", "data/tile-cache"))
_TILE_MAX_AGE = 7 * 86400  # 7 days
_TILE_URL = "https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png"
_TILE_UA = "enviolations-screening/1.0 (environmental compliance screening)"


def _lon_to_tile(lon: float, zoom: int) -> int:
    return int((lon + 180.0) / 360.0 * (1 << zoom))


def _lat_to_tile(lat: float, zoom: int) -> int:
    lat_rad = math.radians(lat)
    return int((1 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2 * (1 << zoom))


def _tile_to_lon(x: int, zoom: int) -> float:
    return x / (1 << zoom) * 360.0 - 180.0


def _tile_to_lat(y: int, zoom: int) -> float:
    n = math.pi - 2 * math.pi * y / (1 << zoom)
    return math.degrees(math.atan(math.sinh(n)))


def _auto_zoom(lat_span: float, lon_span: float) -> int:
    """Pick a zoom level that gives reasonable detail for the map extent."""
    span = max(lat_span, lon_span) * 2
    for z in range(18, 0, -1):
        if 360.0 / (1 << z) < span:
            return min(z + 1, 15)
    return 12


def _get_cached_tile(zoom: int, x: int, y: int) -> bytes | None:
    path = _TILE_CACHE_DIR / f"{zoom}/{x}/{y}.png"
    try:
        if path.exists() and (_time.time() - path.stat().st_mtime) < _TILE_MAX_AGE:
            return path.read_bytes()
    except OSError:
        pass
    return None


def _cache_tile(zoom: int, x: int, y: int, data: bytes) -> None:
    path = _TILE_CACHE_DIR / f"{zoom}/{x}/{y}.png"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    except OSError:
        pass


def _fetch_single_tile(client, zoom: int, tx: int, ty: int) -> tuple[int, int, bytes] | None:
    cached = _get_cached_tile(zoom, tx, ty)
    if cached:
        return (tx, ty, cached)
    url = _TILE_URL.format(z=zoom, x=tx, y=ty)
    resp = client.get(url)
    if resp.status_code == 200:
        _cache_tile(zoom, tx, ty, resp.content)
        return (tx, ty, resp.content)
    return None


def _add_osm_basemap(ax, center_lat: float, center_lon: float,
                     lat_span: float, lon_span: float) -> None:
    """Fetch map tiles and composite onto a matplotlib axes at zorder=0."""
    import httpx
    from matplotlib.image import imread

    zoom = _auto_zoom(lat_span, lon_span)

    # Tile range covering the map extent.
    x_min = _lon_to_tile(center_lon - lon_span, zoom)
    x_max = _lon_to_tile(center_lon + lon_span, zoom)
    y_min = _lat_to_tile(center_lat + lat_span, zoom)  # note: y is inverted
    y_max = _lat_to_tile(center_lat - lat_span, zoom)

    tile_coords = [(tx, ty) for tx in range(x_min, x_max + 1)
                   for ty in range(y_min, y_max + 1)]

    tiles: list[tuple[int, int, bytes]] = []
    with httpx.Client(timeout=10, headers={"User-Agent": _TILE_UA}) as client:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(_fetch_single_tile, client, zoom, tx, ty): (tx, ty)
                for tx, ty in tile_coords
            }
            for future in as_completed(futures):
                result = future.result()
                if result:
                    tiles.append(result)

    if not tiles:
        return

    for tx, ty, png_bytes in tiles:
        tile_w_lon = _tile_to_lon(tx, zoom)
        tile_e_lon = _tile_to_lon(tx + 1, zoom)
        tile_n_lat = _tile_to_lat(ty, zoom)
        tile_s_lat = _tile_to_lat(ty + 1, zoom)
        img = imread(io.BytesIO(png_bytes))
        ax.imshow(img, extent=[tile_w_lon, tile_e_lon, tile_s_lat, tile_n_lat],
                  zorder=0, aspect="auto", interpolation="bilinear")


# ---------------------------------------------------------------------------
# PDF rendering
# ---------------------------------------------------------------------------

def generate_map_image(report: dict, width_pts: float, height_pts: float) -> bytes | None:
    """Render a map of the search center and facility locations as PNG bytes (CIV-547).

    Uses matplotlib to draw a geographic scatter plot showing:
    - The search center point (star marker)
    - A dashed circle representing the search radius
    - Facility dots color-coded by risk level (critical/high/medium/low)
    - A legend and scale bar

    Returns PNG bytes or None if center coordinates are unavailable or matplotlib fails.
    width_pts / height_pts are the desired image dimensions in PDF points.
    """
    import math

    site = report.get("site", {})
    center_lat = site.get("lat")
    center_lon = site.get("lon")
    if center_lat is None or center_lon is None:
        return None

    radius_miles = site.get("search_radius_miles", 1.0)
    facilities = report.get("facilities", [])

    # Degrees per mile at this latitude (approximate).
    _miles_per_deg_lat = 69.0
    _miles_per_deg_lon = 69.0 * math.cos(math.radians(center_lat))

    # Map extent: radius * 1.5 so the circle has visible padding.
    _pad = radius_miles * 1.5
    lat_span = _pad / _miles_per_deg_lat
    lon_span = _pad / _miles_per_deg_lon

    # Risk color palette (matches PDF table risk highlight colors).
    _RISK_COLORS = {
        "critical": "#dc2626",
        "high": "#ea580c",
        "medium": "#d97706",
        "low": "#16a34a",
    }
    _RISK_Z = {"critical": 5, "high": 4, "medium": 3, "low": 2}
    _DEFAULT_COLOR = "#64748b"

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        from matplotlib.patches import Ellipse
    except ImportError:
        return None

    fig_w = width_pts / 72.0
    fig_h = height_pts / 72.0

    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=150)
    fig.patch.set_facecolor("#f8fafc")
    ax.set_facecolor("#e8f0f7")

    # Draw the search radius circle using an Ellipse so lon/lat degree scaling is correct.
    radius_lat = radius_miles / _miles_per_deg_lat
    radius_lon = radius_miles / _miles_per_deg_lon
    circle = Ellipse(
        (center_lon, center_lat),
        width=2 * radius_lon,
        height=2 * radius_lat,
        facecolor="#dbeafe",
        edgecolor="#3b82f6",
        linewidth=1.2,
        linestyle="--",
        alpha=0.5,
        zorder=1,
    )
    ax.add_patch(circle)

    # Plot facility dots grouped by risk level.
    _seen_levels = {}
    for fac in facilities:
        fac_lat = fac.get("lat")
        fac_lon = fac.get("lon")
        if fac_lat is None or fac_lon is None:
            continue
        rl = (fac.get("risk_level") or "unscored").lower()
        color = _RISK_COLORS.get(rl, _DEFAULT_COLOR)
        z = _RISK_Z.get(rl, 1)
        ax.scatter(fac_lon, fac_lat, c=color, s=20, zorder=z, alpha=0.85,
                   linewidths=0.3, edgecolors="white")
        if rl not in _seen_levels:
            _seen_levels[rl] = [fac_lon, fac_lat, color]

    # Center point on top of all other markers.
    ax.scatter(center_lon, center_lat, marker="*", c="#0c1220", s=120, zorder=10,
               linewidths=0.5, edgecolors="white")

    ax.set_xlim(center_lon - lon_span, center_lon + lon_span)
    ax.set_ylim(center_lat - lat_span, center_lat + lat_span)
    ax.set_xlabel("Longitude", fontsize=6, color="#64748b")
    ax.set_ylabel("Latitude", fontsize=6, color="#64748b")
    ax.tick_params(labelsize=5, colors="#64748b")

    # Fetch OSM basemap tiles and composite behind all other artists (CIV-615).
    # Uses direct tile fetching (no contextily/rasterio/GDAL deps).
    try:
        _add_osm_basemap(ax, center_lat, center_lon, lat_span, lon_span)
    except Exception:
        pass  # Best-effort; falls back to plain background on network/import failure.

    # Scale bar in lower-right area.
    _sb_miles = 0.5 if radius_miles <= 1.0 else 1.0
    _sb_lon_len = _sb_miles / _miles_per_deg_lon
    _sb_x0 = center_lon + lon_span * 0.35
    _sb_x1 = _sb_x0 + _sb_lon_len
    _sb_y = center_lat - lat_span * 0.85
    ax.plot([_sb_x0, _sb_x1], [_sb_y, _sb_y], color="#0c1220", linewidth=2, zorder=8)
    ax.plot([_sb_x0, _sb_x0], [_sb_y - lat_span * 0.02, _sb_y + lat_span * 0.02],
            color="#0c1220", linewidth=1.5, zorder=8)
    ax.plot([_sb_x1, _sb_x1], [_sb_y - lat_span * 0.02, _sb_y + lat_span * 0.02],
            color="#0c1220", linewidth=1.5, zorder=8)
    ax.text((_sb_x0 + _sb_x1) / 2, _sb_y - lat_span * 0.08,
            f"{_sb_miles} mi", ha="center", va="top", fontsize=5, color="#0c1220", zorder=8)

    # Legend for risk levels actually present.
    _LEVEL_LABELS = {
        "critical": "Critical", "high": "High", "medium": "Medium",
        "low": "Low", "unscored": "Unscored",
    }
    _legend_handles = [
        plt.Line2D([0], [0], marker="*", color="w", markerfacecolor="#0c1220",
                   markersize=7, label="Search center"),
    ]
    for rl in ["critical", "high", "medium", "low", "unscored"]:
        if rl in _seen_levels:
            _legend_handles.append(
                mpatches.Patch(facecolor=_RISK_COLORS.get(rl, _DEFAULT_COLOR),
                               label=_LEVEL_LABELS.get(rl, rl.capitalize()),
                               edgecolor="white", linewidth=0.3)
            )
    ax.legend(handles=_legend_handles, loc="upper left",
              fontsize=5, framealpha=0.9, edgecolor="#ddddd5",
              labelspacing=0.3, handlelength=1.2, borderpad=0.6)

    for spine in ax.spines.values():
        spine.set_edgecolor("#ddddd5")
        spine.set_linewidth(0.5)

    ax.set_aspect("auto")
    fig.tight_layout(pad=0.3)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def generate_screening_pdf(report: dict, sort_by: str = "risk") -> bytes:
    """Render screening report dict as a formatted PDF.

    sort_by controls the facility table order:
      "risk"       - highest risk score first (default, recommended for screening)
      "distance"   - nearest first (original behavior)
      "violations" - most violations first
      "name"       - alphabetical
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        HRFlowable,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    buf = io.BytesIO()
    w, h = letter

    def _draw_header(canvas, doc):
        canvas.saveState()
        # Dark banner
        canvas.setFillColor(colors.HexColor("#0c1220"))
        canvas.rect(0, h - 52, w, 52, fill=1, stroke=0)
        # Teal accent line
        canvas.setStrokeColor(colors.HexColor("#0891b2"))
        canvas.setLineWidth(2)
        canvas.line(0, h - 52, w, h - 52)
        # Brand name — "Civ" white + "Data" teal, matching dashboard style
        canvas.setFont("Helvetica-Bold", 16)
        canvas.setFillColor(colors.white)
        civ_w = canvas.stringWidth("Civ", "Helvetica-Bold", 16)
        canvas.drawString(doc.leftMargin, h - 34, "Civ")
        canvas.setFillColor(colors.HexColor("#0891b2"))
        canvas.drawString(doc.leftMargin + civ_w, h - 34, "Data")
        data_w = canvas.stringWidth("Data", "Helvetica-Bold", 16)
        bw = civ_w + data_w
        # Separator
        canvas.setFillColor(colors.HexColor("#0891b2"))
        canvas.setFont("Helvetica", 14)
        canvas.drawString(doc.leftMargin + bw + 8, h - 34, "|")
        # Tagline — uppercase, slate gray, letter-spaced
        tagline_x = doc.leftMargin + bw + 22
        tagline_y = h - 32
        t = canvas.beginText(tagline_x, tagline_y)
        t.setFont("Helvetica", 7.5)
        t.setFillColor(colors.HexColor("#94a3b8"))
        t.setCharSpace(1.2)
        t.textOut("ENVIRONMENTAL COMPLIANCE")
        canvas.drawText(t)
        # Footer line
        canvas.setStrokeColor(colors.HexColor("#ddddd5"))
        canvas.setLineWidth(0.5)
        canvas.line(doc.leftMargin, 45, w - doc.rightMargin, 45)
        canvas.setFillColor(colors.HexColor("#94a3b8"))
        canvas.setFont("Helvetica", 6.5)
        canvas.drawString(doc.leftMargin, 36, "enviolations")
        canvas.drawCentredString(w / 2, 36,
                                 "Not for ASTM E1527-21 Compliance")
        canvas.drawRightString(w - doc.rightMargin, 36,
                               f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    # Compute site address early so it can be used in PDF metadata.
    site = report.get("site", {})
    gen = report.get("generated_at", "")[:10]
    # Prefer the user's original input address for the Site: line; fall back to
    # the geocoded/resolved address, then lat/lon if neither is available.
    _input_addr = site.get("input_address")
    _resolved_addr = site.get("address")
    _site_addr = _title_case(_input_addr or _resolved_addr or "")

    _pdf_title = (
        f"Environmental Screening Report — {_site_addr}"
        if _site_addr
        else "Environmental Screening Report"
    )
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        topMargin=1.1 * inch, bottomMargin=0.75 * inch,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        author="enviolations",
        title=_pdf_title,
    )
    styles = getSampleStyleSheet()
    story: list = []

    # -- Styles --
    title = ParagraphStyle("RTitle", parent=styles["Title"],
                           fontSize=18, spaceAfter=6)
    info = ParagraphStyle("Info", parent=styles["Normal"],
                          fontSize=9, leading=14)
    cell = ParagraphStyle("Cell", parent=styles["Normal"],
                          fontSize=7, leading=9)
    cell_b = ParagraphStyle("CellB", parent=cell, fontName="Helvetica-Bold")
    disc = ParagraphStyle("Disc", parent=styles["Normal"],
                          fontSize=7, textColor=colors.HexColor("#94a3b8"), leading=10)
    hr = lambda: HRFlowable(width="100%", thickness=1, color=colors.HexColor("#ddddd5"))

    # -- Header --
    story.append(Paragraph("Environmental Screening Report", title))
    if not _site_addr:
        _site_lat = site.get("lat", "")
        _site_lon = site.get("lon", "")
        _site_r = site.get("search_radius_miles", "")
        _site_addr = f"{_site_lat}, {_site_lon} ({_site_r}-mile radius)"
    story.append(Paragraph(f"<b>Site:</b> {_site_addr}", info))
    # Show the geocoded match when it differs from the user's input, so the report
    # is transparent about how the address was interpreted.
    if _input_addr and _resolved_addr and _input_addr.strip().lower() != _resolved_addr.strip().lower():
        story.append(Paragraph(f"<b>Matched to:</b> {_title_case(_resolved_addr)}", info))
    story.append(Paragraph(f"<b>Coordinates:</b> {site.get('lat', 'N/A')}, {site.get('lon', 'N/A')}", info))
    story.append(Paragraph(f"<b>Search radius:</b> {site.get('search_radius_miles', 'N/A')} miles", info))
    story.append(Paragraph(f"<b>Generated:</b> {gen}", info))
    story.append(Spacer(1, 8))

    # -- Geocoding quality warning (shown when address could not be precisely located) --
    _geocode_quality = site.get("geocode_quality", "exact")
    if _geocode_quality == "zip_centroid":
        geocode_warn_style = ParagraphStyle(
            "GeocodeWarn", parent=styles["Normal"],
            fontSize=8, leading=11,
            textColor=colors.HexColor("#7f1d1d"),
            backColor=colors.HexColor("#fee2e2"),
            borderColor=colors.HexColor("#dc2626"),
            borderWidth=1.5, borderPadding=8,
        )
        story.append(Paragraph(
            "<b>LOCATION WARNING:</b> The site address could not be found in public geocoding "
            "databases (Census Bureau, OpenStreetMap). Results are centered on the zip code area, "
            "which may be miles from the actual property. The search radius may not cover the "
            "correct location. <b>Do not use this report without independently verifying the "
            "search coordinates above against the actual site location.</b> "
            "Re-run the search using GPS coordinates for an accurate result.",
            geocode_warn_style,
        ))
        story.append(Spacer(1, 8))
    elif _geocode_quality == "street_approx":
        geocode_warn_style = ParagraphStyle(
            "GeocodeWarnStreet", parent=styles["Normal"],
            fontSize=8, leading=11,
            textColor=colors.HexColor("#78350f"),
            backColor=colors.HexColor("#fef3c7"),
            borderColor=colors.HexColor("#d97706"),
            borderWidth=1, borderPadding=8,
        )
        story.append(Paragraph(
            "<b>LOCATION NOTE:</b> Exact address not found — results are centered on the "
            "nearest matching street centerline. Results within ~0.25 miles may be slightly off. "
            "Verify the coordinates above match the actual site.",
            geocode_warn_style,
        ))
        story.append(Spacer(1, 8))
    elif _geocode_quality == "address_mismatch":
        _geocode_warning_text = site.get("geocode_warning", "")
        geocode_warn_style = ParagraphStyle(
            "GeocodeWarnMismatch", parent=styles["Normal"],
            fontSize=8, leading=11,
            textColor=colors.HexColor("#78350f"),
            backColor=colors.HexColor("#fef3c7"),
            borderColor=colors.HexColor("#d97706"),
            borderWidth=1.5, borderPadding=8,
        )
        story.append(Paragraph(
            f"<b>GEOCODING WARNING:</b> {_geocode_warning_text} "
            "Verify the coordinates above match the intended site before using this report.",
            geocode_warn_style,
        ))
        story.append(Spacer(1, 8))

    # -- Prominent disclaimer (page 1, before data) --
    disc_box = ParagraphStyle("DiscBox", parent=styles["Normal"],
                               fontSize=8, leading=11,
                               textColor=colors.HexColor("#374151"),
                               backColor=colors.HexColor("#fef3c7"),
                               borderColor=colors.HexColor("#d97706"),
                               borderWidth=1, borderPadding=8)
    story.append(Paragraph(
        "<b>IMPORTANT:</b> This report is generated from public government records "
        "(EPA ECHO and state environmental agencies). It is <b>not</b> an ASTM E1527-21-compliant "
        "environmental database report and should not be used as a substitute for a Phase I ESA "
        "or other professional environmental assessment. An EDR or equivalent database report "
        "should be obtained for compliant assessments. Data may be incomplete or outdated.",
        disc_box,
    ))
    story.append(Spacer(1, 12))

    # -- Map --
    # Geographic map showing the search center, radius circle, and facility
    # locations color-coded by risk level.  Placed after the disclaimer and
    # before the summary so reviewers get spatial context before the table.
    from reportlab.platypus import Image as RLImage
    _map_width = doc.width
    _map_height = _map_width * 0.55
    _map_png = generate_map_image(report, _map_width, _map_height)
    if _map_png is not None:
        _map_buf = io.BytesIO(_map_png)
        _map_img = RLImage(_map_buf, width=_map_width, height=_map_height)
        story.append(_map_img)
        _map_caption = ParagraphStyle(
            "MapCaption", parent=styles["Normal"],
            fontSize=6.5, textColor=colors.HexColor("#94a3b8"), leading=9,
        )
        _n_mapped = sum(
            1 for f in report.get("facilities", [])
            if f.get("lat") is not None and f.get("lon") is not None
        )
        _n_total = len(report.get("facilities", []))
        _radius_val = site.get("search_radius_miles", "?")
        story.append(Paragraph(
            f"Search radius: {_radius_val} mile(s). "
            f"{_n_mapped} of {_n_total} facilities have GPS coordinates and appear on the map. "
            "Dot color indicates risk level. Star marks the search center.",
            _map_caption,
        ))
        story.append(Spacer(1, 10))

    # -- Summary --
    s = report.get("summary", {})
    # Compute risk counts directly from the facilities list so the summary is always
    # consistent with the table (both use risk_level from the same facility dicts).
    # Using the pre-computed risk_breakdown caused CIV-505: the summary showed a
    # different count than what was highlighted in the table below it.
    _fac_list = report.get("facilities", [])
    _n_critical = sum(1 for f in _fac_list if (f.get("risk_level") or "").lower() == "critical")
    _n_high = sum(1 for f in _fac_list if (f.get("risk_level") or "").lower() == "high")
    _n_medium = sum(1 for f in _fac_list if (f.get("risk_level") or "").lower() == "medium")
    # Count zero-score facilities: score=0 means no violations recorded, not unregulated.
    # These are included in the table so consultants can see all regulated facilities (CIV-637).
    # Use explicit None check — risk_score=0 is falsy so `or` would incorrectly treat it as -1.
    _n_zero_score = sum(
        1 for f in _fac_list
        if f.get("risk_score") == 0
    )
    story.append(hr())
    story.append(Spacer(1, 8))
    _total_violations = s.get("total_violations", 0)
    _active_violations = sum(f.get("active_violation_count", 0) for f in _fac_list)
    _fac_with_viols = sum(1 for f in _fac_list if (f.get("violation_count") or 0) > 0)
    # Use len(_fac_list) directly so the cover count always matches the table row count
    # (CIV-604). s.get('total_facilities') could theoretically diverge from the actual
    # facilities list if the summary was pre-computed before filtering.
    # violation summary: "X facilities w/ violations (Y active, Z total records)"
    # matches dashboard which shows "X w/ Violations (Z total records)" (CIV-647).
    story.append(Paragraph(
        f"<b>{len(_fac_list)}</b> facilities &bull; "
        f"<b>{_n_critical + _n_high}</b> high/critical risk &bull; "
        f"<b>{_n_medium}</b> medium risk &bull; "
        f"<b>{_fac_with_viols}</b> w/ violations"
        f" ({_active_violations} active, {_total_violations} total records)",
        info,
    ))
    # Note zero-score facilities so consultants understand they are included, not omitted.
    # Score 0/100 means no violations recorded — the facility is still regulated (CIV-637).
    if _n_zero_score > 0:
        story.append(Paragraph(
            f"Includes <b>{_n_zero_score}</b> facilit{'y' if _n_zero_score == 1 else 'ies'} "
            f"with 0/100 score (no violations recorded — regulated but currently clean).",
            disc,
        ))
    story.append(Spacer(1, 8))
    story.append(hr())
    story.append(Spacer(1, 12))

    # -- Facilities table --
    facilities = list(report.get("facilities", []))
    # Sort facilities for PDF display.  Default is risk-first so that high-risk
    # sites are never buried below lower-risk nearby sites (CIV-444).
    if sort_by == "distance":
        facilities.sort(key=lambda f: f.get("distance_miles") or 0)
    elif sort_by == "violations":
        facilities.sort(key=lambda f: f.get("violation_count") or 0, reverse=True)
    elif sort_by == "name":
        facilities.sort(key=lambda f: (f.get("name") or "").lower())
    else:  # "risk" (default)
        # Sort by risk level tier (critical > high > medium > low > unscored)
        # then by score descending within each tier.
        _RISK_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "unscored": 4}
        facilities.sort(
            key=lambda f: (
                _RISK_ORDER.get((f.get("risk_level") or "unscored").lower(), 4),
                -(f.get("risk_score") or 0),
            )
        )
    _SORT_LABELS = {
        "risk": "Sorted by risk (highest first)",
        "distance": "Sorted by distance (nearest first)",
        "violations": "Sorted by violation count (most first)",
        "name": "Sorted by name (A-Z)",
    }
    sort_label = _SORT_LABELS.get(sort_by, "Sorted by risk (highest first)")
    if facilities:
        rows = [["Name", "Address", "St", "Dist (mi)", "Programs", "Risk/Level", "Conf.", "Violations\n(act/tot /\nlatest date)"]]
        for f in facilities:
            state_abbr = (f.get("state") or "").strip().upper()[:2]
            addr = ", ".join(filter(None, [_title_case(f.get("address") or ""), _title_case(f.get("city") or "")]))
            sc = f.get("risk_score")
            sc = sc if sc is not None else -1
            display_sc = sc
            raw_name = f.get("name") or "Unnamed Facility"
            display_name = _pdf_title_case(raw_name)
            progs = _pdf_humanize_programs(f.get("programs") or f.get("unified_programs") or "", state=state_abbr)
            risk_level = (f.get("risk_level") or "unscored").capitalize()
            risk_level_combined = (f"{display_sc} {risk_level}" if display_sc >= 0 else risk_level)
            vtotal = f.get("violation_count", 0)
            vactive = f.get("active_violation_count", vtotal)
            # Build violation cell: count + latest date/recency + most recent type
            _viol_html = f"<b>{vactive}/{vtotal}</b>"
            _lvd = f.get("latest_violation_date")
            _recency = _violation_recency_label(_lvd)
            if _lvd and _recency:
                _lvd_short = str(_lvd)[:10]
                _viol_html += (
                    f'<br/><font size="5.5" color="#64748b">'
                    f'{_xml_escape(_lvd_short)} ({_recency})</font>'
                )
            elif _lvd:
                _viol_html += (
                    f'<br/><font size="5.5" color="#64748b">'
                    f'{_xml_escape(str(_lvd)[:10])}</font>'
                )
            # Most recent violation type (from first violation in sorted list)
            _viols = f.get("violations") or []
            if _viols:
                _vtype = _viols[0].get("violation_type") or _viols[0].get("program_area") or ""
                if _vtype and str(_vtype).upper() not in ("NONE SPECIFIED", "\u2014"):
                    _vtype_short = str(_vtype)[:40]
                    _viol_html += (
                        f'<br/><font size="5.5" color="#94a3b8">'
                        f'{_xml_escape(_vtype_short)}</font>'
                    )
            viol_cell = Paragraph(_viol_html, cell)
            # Source verification URL: small gray text beneath the facility name
            # so reviewers can independently look up the original government record.
            _src_url = _source_url(f.get("source", ""), f.get("source_id", "") or "", name=f.get("name"))
            if _src_url:
                name_cell = Paragraph(
                    f"{_xml_escape(display_name)}<br/>"
                    f'<font size="5.5" color="#64748b">'
                    f'<a href="{_xml_escape(_src_url)}" color="#64748b">View source record</a>'
                    f'</font>',
                    cell_b,
                )
            else:
                name_cell = Paragraph(_xml_escape(display_name), cell_b)
            rows.append([
                name_cell,
                Paragraph(_xml_escape(addr), cell),
                state_abbr or "--",
                str(f.get("distance_miles", "--")),
                Paragraph(_xml_escape(progs), cell),
                risk_level_combined,
                {"moderate": "Med", "high": "High", "low": "Low"}.get(
                    (f.get("confidence") or "low").lower(), (f.get("confidence") or "low").capitalize()
                ),
                viol_cell,
            ])

        tw = doc.width
        # Programs column widened from 0.15 -> 0.22 so full TCEQ display names
        # ("Petroleum Storage Tank Registration (TCEQ)" = 43 chars) wrap across
        # fewer lines and don't clip at page boundaries (CIV-482).
        # Name/Address reduced slightly to keep total at 0.95 x doc.width.
        # Violations column widened 0.08 → 0.12 to show latest date + recency
        # (CIV-605). Conf. reduced 0.09 → 0.05 to compensate.
        cw = [tw * 0.16, tw * 0.16, tw * 0.04, tw * 0.07, tw * 0.22, tw * 0.13, tw * 0.05, tw * 0.12]
        # splitByRow=1 lets ReportLab split tall rows across page boundaries
        # instead of clipping them — prevents Programs cell content from being
        # cut off mid-string when a row is taller than the remaining page space.
        tbl = Table(rows, colWidths=cw, repeatRows=1, splitByRow=1)

        cmds = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0c1220")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 8),
            ("FONTSIZE", (0, 1), (-1, -1), 7),
            ("LEADING", (0, 0), (-1, -1), 10),
            ("ALIGN", (2, 0), (-1, -1), "CENTER"),
            ("ALIGN", (0, 0), (1, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#ddddd5")),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]
        risk_bg = {"critical": "#fef2f2", "high": "#fef2f2", "medium": "#fffbeb", "low": "#ecfdf5"}
        risk_fg = {"critical": "#7f1d1d", "high": "#dc2626", "medium": "#d97706", "low": "#059669"}
        # Use explicit per-row BACKGROUND for alternating rows instead of
        # ROWBACKGROUNDS to avoid the ReportLab table-split edge case where
        # ROWBACKGROUNDS with negative row indices silently truncates rows
        # when the table spans pages (same issue fixed for Databases Searched
        # table in CIV-458). Absolute row indices are safe across splits.
        _alt_bg_fac = colors.HexColor("#f8f8f5")
        for i, f in enumerate(facilities, start=1):
            if i % 2 == 0:
                cmds.append(("BACKGROUND", (0, i), (-1, i), _alt_bg_fac))
            rl = (f.get("risk_level") or "").lower()
            if rl in risk_bg:
                cmds.append(("BACKGROUND", (5, i), (5, i), colors.HexColor(risk_bg[rl])))
                cmds.append(("TEXTCOLOR", (5, i), (5, i), colors.HexColor(risk_fg[rl])))
        tbl.setStyle(TableStyle(cmds))
        story.append(Paragraph(sort_label, disc))
        story.append(Spacer(1, 4))
        story.append(tbl)
    else:
        _no_result_addr = site.get("address") or f"{site.get('lat', '')}, {site.get('lon', '')}"
        _no_result_radius = site.get("search_radius_miles", "")
        story.append(Paragraph(
            f"No facilities found within {_no_result_radius} mile(s) of {_no_result_addr}.",
            info,
        ))

    # -- Databases Searched --
    story.append(Spacer(1, 16))
    story.append(hr())
    story.append(Spacer(1, 8))
    section_hdr = ParagraphStyle("SectionHdr", parent=styles["Normal"],
                                 fontSize=10, fontName="Helvetica-Bold")
    story.append(Paragraph("Databases Searched", section_hdr))
    story.append(Spacer(1, 6))
    source_citations = report.get("source_citations", [])
    if source_citations:
        src_rows = [["Database", "Last Retrieved", "Facilities in Radius"]]
        for src in source_citations:
            count = src.get("facility_count_in_radius", 0)
            raw_key = src.get("source", "?")
            src_rows.append([
                Paragraph(_xml_escape(_source_display_name(raw_key)), cell),
                (src.get("last_retrieved") or "N/A")[:10],
                str(count),
            ])
        src_tw = doc.width
        src_cw = [src_tw * 0.55, src_tw * 0.25, src_tw * 0.20]
        # Use explicit per-row BACKGROUND instead of ROWBACKGROUNDS to avoid a
        # ReportLab table-split edge case where ROWBACKGROUNDS with negative row
        # indices can cause rows to silently truncate when the table spans pages
        # (CIV-458). Explicit absolute row indices are always safe across splits.
        src_row_cmds = []
        _alt_bg = colors.HexColor("#f8f8f5")
        for _i in range(1, len(src_rows)):
            if _i % 2 == 0:
                src_row_cmds.append(("BACKGROUND", (0, _i), (-1, _i), _alt_bg))
        src_tbl = Table(src_rows, colWidths=src_cw, repeatRows=1, splitByRow=1)
        src_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0c1220")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("LEADING", (0, 0), (-1, -1), 10),
            ("ALIGN", (1, 0), (-1, -1), "CENTER"),
            ("ALIGN", (0, 0), (0, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#ddddd5")),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ] + src_row_cmds))
        story.append(src_tbl)
    else:
        story.append(Paragraph("No database information available.", info))

    # -- Violation Details Appendix --
    # Collect facilities with violations, sorted by risk score desc, top 10
    _MAX_VIOLATION_FACILITIES = 10
    _MAX_VIOLATIONS_PER_FAC = 100
    facilities_with_violations = sorted(
        [f for f in facilities if f.get("violations")],
        key=lambda f: f.get("risk_score") or 0,
        reverse=True,
    )[:_MAX_VIOLATION_FACILITIES]

    if facilities_with_violations:
        from reportlab.platypus import PageBreak
        story.append(Spacer(1, 16))
        story.append(hr())
        story.append(Spacer(1, 8))
        story.append(Paragraph("Appendix: Violation Records", section_hdr))
        story.append(Spacer(1, 4))
        total_fac_with_viols = len([f for f in facilities if f.get("violations")])
        if total_fac_with_viols > _MAX_VIOLATION_FACILITIES:
            story.append(Paragraph(
                f"Showing top {_MAX_VIOLATION_FACILITIES} highest-risk facilities "
                f"({total_fac_with_viols} facilities have violations).",
                disc,
            ))
        story.append(Spacer(1, 8))

        cell_vio = ParagraphStyle("CellVio", parent=styles["Normal"],
                                  fontSize=6.5, leading=8.5)
        cell_vio_b = ParagraphStyle("CellVioB", parent=cell_vio,
                                    fontName="Helvetica-Bold")

        for fac in facilities_with_violations:
            raw_name = fac.get("name") or "Unnamed Facility"
            fac_name = _pdf_title_case(raw_name)
            fac_addr = ", ".join(filter(None, [fac.get("address"), fac.get("city"), fac.get("state")]))
            sc = fac.get("risk_score")
            sc = sc if sc is not None else -1
            rl = (fac.get("risk_level") or "unscored").capitalize()
            score_label = f"{sc} {rl}" if sc >= 0 else rl
            header_style = ParagraphStyle(
                "VioFacHdr", parent=styles["Normal"],
                fontSize=8, fontName="Helvetica-Bold",
                textColor=colors.HexColor("#0c1220"),
            )
            _vtotal = fac.get("violation_count", 0)
            _vactive = fac.get("active_violation_count", _vtotal)
            _viol_label = (
                f"{_vactive} active / {_vtotal} total"
                if _vactive != _vtotal
                else f"{_vtotal} violation(s)"
            )
            story.append(Paragraph(
                f"{_xml_escape(fac_name)} — {_xml_escape(fac_addr)} "
                f"&nbsp; <font size='7' color='#64748b'>Risk: {score_label} &bull; "
                f"{_viol_label}</font>",
                header_style,
            ))
            story.append(Spacer(1, 2))

            viols = fac["violations"][:_MAX_VIOLATIONS_PER_FAC]
            vio_rows = [["Date", "Type", "Description", "Status"]]
            for v in viols:
                raw_date = v.get("violation_date") or ""
                _vdate_str = str(raw_date)[:10] if raw_date else ""
                # Treat sentinel dates more than 5 years in the future as unknown
                try:
                    _sentinel_year = int(_vdate_str[:4]) if len(_vdate_str) >= 4 else 0
                except ValueError:
                    _sentinel_year = 0
                vdate = "—" if (not _vdate_str or _sentinel_year > date.today().year + 5) else _vdate_str
                vtype = v.get("violation_type") or v.get("program_area") or "—"
                vdesc = v.get("description") or v.get("cfr_summary") or "—"
                vstatus = _violation_status_label(v.get("status"))
                _vdesc_str = str(vdesc)
                _vdesc_display = (
                    _vdesc_str[:497] + "..." if len(_vdesc_str) > 500 else _vdesc_str
                )
                vio_rows.append([
                    vdate,
                    Paragraph(_xml_escape(str(vtype)), cell_vio),
                    Paragraph(_xml_escape(_vdesc_display), cell_vio),
                    Paragraph(_xml_escape(str(vstatus)), cell_vio),
                ])

            vtw = doc.width
            vcw = [vtw * 0.10, vtw * 0.18, vtw * 0.55, vtw * 0.17]
            # Use explicit per-row BACKGROUND instead of ROWBACKGROUNDS to avoid a
            # ReportLab table-split edge case where ROWBACKGROUNDS with negative row
            # indices can cause rows to be silently truncated when the table spans
            # pages (CIV-458, CIV-530). Absolute row indices are safe across splits.
            _vio_alt_bg = colors.HexColor("#f8f8f5")
            _vio_row_cmds = [
                ("BACKGROUND", (0, _ri), (-1, _ri), _vio_alt_bg)
                for _ri in range(2, len(vio_rows), 2)
            ]
            vtbl = Table(vio_rows, colWidths=vcw, repeatRows=1, splitByRow=1)
            vtbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#374151")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 6.5),
                ("FONTSIZE", (0, 1), (-1, -1), 6.5),
                ("LEADING", (0, 0), (-1, -1), 9),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ("ALIGN", (1, 0), (-1, -1), "LEFT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#ddddd5")),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ] + _vio_row_cmds))
            story.append(vtbl)
            if len(fac["violations"]) > _MAX_VIOLATIONS_PER_FAC:
                story.append(Paragraph(
                    f"... {len(fac['violations']) - _MAX_VIOLATIONS_PER_FAC} additional violation(s) not shown."
                    " ",
                    disc,
                ))
            story.append(Spacer(1, 10))

    # -- Program Code Glossary --
    # Collect all program codes that actually appear in this report's facilities
    # so the glossary is relevant rather than exhaustive.
    # Track which states each code appears in to decide TCEQ suffix (CIV-709).
    _code_states: dict[str, set[str]] = {}
    for _f in facilities:
        _fstate = (_f.get("state") or "").strip().upper()
        _prog_str = _f.get("programs") or _f.get("unified_programs") or ""
        for _p in _prog_str.split(","):
            _p = _p.strip().upper()
            if _p and _p != "NONE SPECIFIED":
                _code_states.setdefault(_p, set()).add(_fstate)

    # Build glossary rows for codes that appear in the report and have a definition.
    # Use _program_display_name with state-awareness: append "(TCEQ)" only when
    # all facilities using that code are in TX.
    _glossary_entries = sorted(
        [
            (code, desc)
            for code in _code_states
            if (desc := _program_display_name(
                code,
                "TX" if _code_states[code] == {"TX"} else None,
            )) is not None
        ],
        key=lambda x: x[0],
    )

    if _glossary_entries:
        story.append(Spacer(1, 16))
        story.append(hr())
        story.append(Spacer(1, 8))
        story.append(Paragraph("Program Code Glossary", section_hdr))
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            "Definitions of regulatory program codes appearing in this report.",
            disc,
        ))
        story.append(Spacer(1, 6))

        cell_gl = ParagraphStyle("CellGl", parent=styles["Normal"], fontSize=7, leading=9)
        gl_rows = [["Code", "Full Name / Program Description"]]
        for _code, _desc in _glossary_entries:
            gl_rows.append([
                Paragraph(_xml_escape(_code), cell_gl),
                Paragraph(_xml_escape(_desc), cell_gl),
            ])

        gl_tw = doc.width
        gl_cw = [gl_tw * 0.18, gl_tw * 0.82]
        gl_tbl = Table(gl_rows, colWidths=gl_cw, repeatRows=1, splitByRow=1)
        _alt_bg_gl = colors.HexColor("#f8f8f5")
        gl_cmds = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#374151")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("LEADING", (0, 0), (-1, -1), 9),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#ddddd5")),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]
        for _i in range(1, len(gl_rows)):
            if _i % 2 == 0:
                gl_cmds.append(("BACKGROUND", (0, _i), (-1, _i), _alt_bg_gl))
        gl_tbl.setStyle(TableStyle(gl_cmds))
        story.append(gl_tbl)

    # -- Disclaimer --
    story.append(Spacer(1, 20))
    story.append(hr())
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "Screening data — Not for ASTM E1527-21 Compliance. "
        "This report is generated from public government records (EPA ECHO and state "
        "environmental agencies). It is not an ASTM E1527-21 compliant environmental "
        "database report and does not constitute a Phase I ESA or other professional "
        "environmental assessment. The absence of records does not mean records do not "
        "exist. An EDR or equivalent database report should be obtained for ASTM-compliant "
        "assessments. ",
        disc,
    ))

    doc.build(story, onFirstPage=_draw_header, onLaterPages=_draw_header)
    buf.seek(0)
    return buf.read()
