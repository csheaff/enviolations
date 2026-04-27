"""CFR Part number lookup table and citation enrichment utilities.

Maps 40 CFR Part numbers (and common state-law citations like 30 TAC) to
plain-language summaries. Used to annotate raw violation description strings
with human-readable regulation names.

The raw descriptions stored in the violations table look like:
  "262.C, 265.C, 268.A, 279.C, XXS"
  "Cat B: 262.34(d); 335.474; 335.69(f)"

This module parses the leading part numbers out of those strings and maps
them to their regulatory titles so non-specialists can understand the data.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# 40 CFR Part lookup table
# ---------------------------------------------------------------------------
# Maps part number (as string) to a short plain-language description.
# Priority: cover the parts most commonly seen in RCRA and TCEQ data first.

CFR_PARTS: dict[str, str] = {
    # RCRA — Resource Conservation and Recovery Act
    "260": "RCRA: Hazardous Waste Regulations — General",
    "261": "RCRA: Identification of Hazardous Waste",
    "262": "RCRA: Hazardous Waste Generator Standards",
    "263": "RCRA: Hazardous Waste Transporter Standards",
    "264": "RCRA: Permitted Treatment/Storage/Disposal Facilities",
    "265": "RCRA: Interim Status TSD Facility Standards",
    "266": "RCRA: Hazardous Waste Burned in Boilers/Furnaces",
    "267": "RCRA: Standards for Recognized Treatment Technologies",
    "268": "RCRA: Land Disposal Restrictions",
    "269": "RCRA: Reserved",
    "270": "RCRA: Permit Program",
    "271": "RCRA: Requirements for Authorized State Programs",
    "272": "RCRA: Approved State Hazardous Waste Programs",
    "273": "RCRA: Universal Waste Management Standards",
    "279": "RCRA: Used Oil Standards",
    # CERCLA / Superfund
    "300": "CERCLA: National Oil/Hazardous Substance Contingency Plan",
    "302": "CERCLA: Reportable Quantities (Emergency Notification)",
    "303": "CERCLA: Superfund Response",
    "304": "CERCLA: Emergency Planning Notification",
    # EPCRA — Emergency Planning and Community Right-to-Know Act
    "355": "EPCRA: Emergency Planning (Extremely Hazardous Substances)",
    "370": "EPCRA: Hazardous Chemical Reporting (Tier I/II)",
    "372": "EPCRA: Toxic Release Inventory (TRI)",
    # Clean Air Act
    "50": "CAA: National Ambient Air Quality Standards",
    "60": "CAA: New Source Performance Standards",
    "61": "CAA: National Emission Standards for Hazardous Air Pollutants",
    "63": "CAA: MACT Standards for HAPs",
    "68": "CAA: Chemical Accident Prevention (RMP)",
    "70": "CAA: State Operating Permit Program (Title V)",
    "71": "CAA: Federal Operating Permits",
    "72": "CAA: Permits Regulation (Acid Rain)",
    # Clean Water Act
    "122": "CWA: NPDES Permit Program",
    "123": "CWA: State NPDES Programs",
    "124": "CWA: Procedures for Decision Making",
    "125": "CWA: Technology-Based Discharge Standards",
    "129": "CWA: Toxic Pollutant Effluent Standards",
    "133": "CWA: Secondary Treatment Regulations",
    "136": "CWA: Water Quality Monitoring Methods",
    # Safe Drinking Water Act
    "141": "SDWA: National Primary Drinking Water Regulations",
    "142": "SDWA: National Primary DWR Implementation",
    "143": "SDWA: National Secondary Drinking Water Regulations",
    # Underground Storage Tanks (USTs)
    "280": "UST: Technical Standards for Underground Storage Tanks",
    "281": "UST: Approval of State UST Programs",
    # Spill Prevention / Hazardous Materials
    "110": "SPCC: Oil Discharge Prevention",
    "112": "SPCC: Spill Prevention, Control, and Countermeasure Plan",
    "116": "SPCC: Hazardous Substance Discharge Notification",
    "117": "SPCC: Determination of Reportable Quantities",
    # TSCA — Toxic Substances Control Act
    "700": "TSCA: General",
    "702": "TSCA: Chemical Testing",
    "710": "TSCA: Chemical Substance Inventory",
    "716": "TSCA: Health/Safety Data Reporting",
    "717": "TSCA: Records and Reports — Significant Adverse Reactions",
    "720": "TSCA: Pre-Manufacture Notification",
    "721": "TSCA: Significant New Use Rules",
    "761": "TSCA: Polychlorinated Biphenyls (PCBs)",
    "763": "TSCA: Asbestos",
    "799": "TSCA: Testing Guidelines",
    # FIFRA — Federal Insecticide, Fungicide, and Rodenticide Act
    "152": "FIFRA: Pesticide Registration",
    "156": "FIFRA: Labeling Requirements",
    "170": "FIFRA: Worker Protection Standard",
}

# ---------------------------------------------------------------------------
# Texas state law (30 TAC — Texas Administrative Code)
# ---------------------------------------------------------------------------
# TCEQ violations reference "335.xxx" etc. which are 30 TAC chapters.

TAC_CHAPTERS: dict[str, str] = {
    # Air quality (30 TAC Chapters 101–122)
    "101": "Texas TCEQ: General Air Quality Rules",
    "106": "Texas TCEQ: Permits by Rule (Air)",
    "111": "Texas TCEQ: Control of Air Pollution from Visible Emissions and Particulates",
    "112": "Texas TCEQ: Control of Air Pollution from Sulfur Compounds",
    "113": "Texas TCEQ: Standards of Performance for Hazardous Air Pollutants",
    "114": "Texas TCEQ: Control of Air Pollution from Motor Vehicles",
    "115": "Texas TCEQ: Control of Air Pollution from Volatile Organic Compounds",
    "116": "Texas TCEQ: Control of Air Pollution by Permits for New Construction",
    "117": "Texas TCEQ: Control of Air Pollution from Nitrogen Compounds",
    "118": "Texas TCEQ: Control of Air Pollution Episodes",
    "122": "Texas TCEQ: Federal Operating Permits (Title V / TPDES)",
    # Water quality
    "290": "Texas TCEQ: Public Drinking Water",
    "291": "Texas TCEQ: Utility Regulations",
    "293": "Texas TCEQ: Water Districts",
    "295": "Texas TCEQ: Water Rights",
    "297": "Texas TCEQ: Water Use",
    "305": "Texas TCEQ: Consolidated Permits",
    "307": "Texas TCEQ: Texas Surface Water Quality Standards",
    "309": "Texas TCEQ: Wastewater Treatment",
    "312": "Texas TCEQ: Sludge Use/Disposal",
    "315": "Texas TCEQ: Industrial Solid Waste",
    "317": "Texas TCEQ: Design Standards for Sewage/Industrial Waste",
    "319": "Texas TCEQ: General Nonpoint Source",
    "321": "Texas TCEQ: Landscape Irrigation",
    "325": "Texas TCEQ: In-Situ and Ex-Situ Groundwater",
    "327": "Texas TCEQ: Spill Prevention and Control",
    "328": "Texas TCEQ: Solid Waste Management",
    "330": "Texas TCEQ: Municipal Solid Waste",
    "331": "Texas TCEQ: Underground Injection Control",
    "332": "Texas TCEQ: Composting",
    "334": "Texas TCEQ: Underground Storage Tanks",
    "335": "Texas TCEQ: Industrial Solid Waste and Hazardous Waste",
    "336": "Texas TCEQ: Radioactive Substance Rules",
    "337": "Texas TCEQ: Petroleum Storage Tanks",
    "338": "Texas TCEQ: Public Water Supply",
    "340": "Texas TCEQ: Air Permits",
    "382": "Texas TCEQ: Clean Air Act — State Implementation",
}

# ---------------------------------------------------------------------------
# Regex patterns for extracting part numbers from raw citation strings
# ---------------------------------------------------------------------------
# Matches patterns like: 262.C  262.34(d)  265  302  335.474  335.9(a)(2)
# We want to capture the part number (before the first dot, parens, or space).
_PART_PATTERN = re.compile(
    r"\b(\d{2,3})(?:\.\w[\w()]*(?:\(\w+\))*)*\b"
)


# ---------------------------------------------------------------------------
# TCEQ violation severity tiers
# ---------------------------------------------------------------------------
# Cat A = most significant, Cat B = moderate, Cat C = minor (TCEQ terminology).
# These labels are prepended to cfr_summary for TCEQ violations so that
# consultants can interpret severity without looking up TCEQ rules.

_TCEQ_SEVERITY_LABELS: dict[str, str] = {
    "A": "Cat A (Major violation)",
    "B": "Cat B (Moderate violation)",
    "C": "Cat C (Minor violation)",
}

# Matches "Cat A:", "Cat B:", "Cat C:" at the start of a TCEQ description segment.
_TCEQ_CAT_RE = re.compile(r"\bCat\s+([ABC])\s*:", re.IGNORECASE)


def _is_tceq_description(description: str) -> bool:
    """Return True if the description looks like a TCEQ Cat A/B/C violation."""
    return bool(_TCEQ_CAT_RE.search(description))


def lookup_cfr_part(part_num: str, prefer_tac: bool = False) -> str | None:
    """Return plain-language name for a 40 CFR or 30 TAC part number, or None.

    When *prefer_tac* is True (TCEQ violations), 30 TAC chapters are checked
    first so that Texas-specific chapters like 116 (Air Permits by Rule) are
    not mis-mapped to the federal CFR 116 (SPCC).
    """
    key = part_num.lstrip("0")
    if prefer_tac:
        return TAC_CHAPTERS.get(key) or CFR_PARTS.get(key)
    return CFR_PARTS.get(key) or TAC_CHAPTERS.get(key)


def parse_cfr_parts(text: str) -> list[str]:
    """Extract unique CFR/TAC part numbers from a raw citation string.

    Returns a list of numeric strings in the order they first appear.
    Filters out very short numbers (<2 digits) and very large ones (>3 digits)
    that are not realistic CFR/TAC part numbers.

    Examples:
      "262.C, 265.C, 268.A"        -> ["262", "265", "268"]
      "Cat B: 262.34(d); 335.474"  -> ["262", "335"]
      "XXS"                         -> []
    """
    seen: set[str] = set()
    parts: list[str] = []
    for m in _PART_PATTERN.finditer(text):
        num = m.group(1)
        if num not in seen:
            seen.add(num)
            parts.append(num)
    return parts


def build_cfr_summary(description: str | None) -> str | None:
    """Parse raw CFR/TAC citation codes and return a plain-language summary.

    For TCEQ violations (those with "Cat A/B/C:" prefixes), the most severe
    category is prepended to the summary so consultants can see severity at a
    glance without decoding the raw citation codes.

    Returns None when no recognizable part numbers are found (so the caller
    can decide whether to show the raw description as-is).

    Examples:
      "262.C, 265.C, 268.A, 279.C"
        -> "Hazardous Waste Generator Standards; Interim Status TSD Facility
            Standards; Land Disposal Restrictions; Used Oil Standards"

      "Cat B: 262.34(d); 335.474"
        -> "Cat B (Moderate violation) — Hazardous Waste Generator Standards;
            Industrial Solid Waste and Hazardous Waste"

      "Cat A: 116.115(c); 122.143(4); 382.085(b)"
        -> "Cat A (Major violation) — Control of Air Pollution by Permits for
            New Construction; Federal Operating Permits (Title V / TPDES);
            Clean Air Act — State Implementation"
    """
    if not description:
        return None

    is_tceq = _is_tceq_description(description)

    parts = parse_cfr_parts(description)
    labels: list[str] = []
    seen_labels: set[str] = set()
    for p in parts:
        label = lookup_cfr_part(p, prefer_tac=is_tceq)
        # Strip source prefix ("RCRA: ", "Texas TCEQ: ", etc.) for brevity
        if label:
            short = label.split(": ", 1)[-1] if ": " in label else label
            if short not in seen_labels:
                seen_labels.add(short)
                labels.append(short)

    if not labels:
        return None

    body = "; ".join(labels)

    # Prepend severity label for TCEQ violations (most severe category wins)
    if is_tceq:
        cats = _TCEQ_CAT_RE.findall(description)
        cats_upper = [c.upper() for c in cats]
        for tier in ("A", "B", "C"):
            if tier in cats_upper:
                body = _TCEQ_SEVERITY_LABELS[tier] + " \u2014 " + body
                break

    return body


def enrich_violation(v: dict) -> dict:
    """Add a ``cfr_summary`` key to a violation dict (mutates in-place).

    The value is a plain-language string summarising the CFR/TAC parts cited,
    or None when no recognised parts are found.  The original ``description``
    field is preserved unchanged.
    """
    v["cfr_summary"] = build_cfr_summary(v.get("description"))
    return v
