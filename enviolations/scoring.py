"""Per-facility compliance risk scoring.

SCORE CONVENTION: 0 = clean/low risk, 100 = worst/high risk.
Deductions are computed by subtracting from 100, then inverted via `100 - score`.
DO NOT flip this back — it matches industry standards (EPA HRS, HUD NSPIRE, etc.).
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import date, datetime, timezone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Layer 1: Non-violation record filtering
# ---------------------------------------------------------------------------

NON_VIOLATION_VALUES = frozenset({
    "no",
    "no violation",
    "no violation identified",
    "no high priority violation",
    "na",
    "none",
    "not applicable",
    "in compliance",
    "compliant",
    "resolved",
})


# NJ DEP and other state enforcement types that represent significant actions
# (administrative orders, consent orders, penalty assessments).
_SIGNIFICANT_ENFORCEMENT_TYPES = frozenset({
    "aonocapa",           # NJ DEP: Administrative Order with Civil Administrative Penalty
    "aco",                # NJ DEP: Administrative Consent Order
    "administrative order",
})

# NJ DEP and other state enforcement types that represent standard violations
# (notices of violation, settlement agreements).
_STANDARD_ENFORCEMENT_TYPES = frozenset({
    "nov",                # NJ DEP: Notice of Violation
    "settlement agreement",
    "noe",                # NJ DEP: Notice of Entry (TCEQ NOE already has status field)
    "directive",
    "warning",
})


def is_real_violation(
    violation_type: str | None,
    severity: str | None,
    description: str | None,
) -> bool:
    """Return True if the record represents an actual violation."""
    for field in (violation_type, severity, description):
        if field and field.strip().lower() in NON_VIOLATION_VALUES:
            return False
    return True


# ---------------------------------------------------------------------------
# Layer 2: NAICS industry risk tiers
# ---------------------------------------------------------------------------

# 4-digit NAICS prefix overrides — checked BEFORE the 2-digit fallback.
# These correct cases where a high-risk sub-industry falls inside a low-risk
# 2-digit parent (e.g., auto salvage yards inside NAICS 42 "Wholesale Trade").
NAICS_4DIGIT_RISK_TIERS: dict[str, tuple[int, int]] = {
    # Tier 1: High-risk sub-industries (-15 pts)
    "4231": (1, 15),  # Motor Vehicle Parts & Supplies (incl. used/salvage)
    "4239": (1, 15),  # Miscellaneous Durable Goods Merchant Wholesalers (incl. scrap)
    "4471": (1, 15),  # Gasoline Stations — UST contamination risk (CIV-572)
    # NAICS 44711 = Gasoline Stations with Convenience Stores
    # NAICS 44719 = Other Gasoline Stations
    # Both carry underground storage tank (UST) contamination risk.
    # The 2-digit parent "44" is Retail Trade (Tier 3), but gasoline stations
    # are high-risk Phase I REC sites regardless of retail classification.
    # Without this override, stations with NAICS codes score 10 pts lower than
    # stations without codes (Tier 3 -5 vs Tier 1 -15 via PSTREG fallback).
    "8123": (1, 15),  # Drycleaning and Laundry Services — PERC/PCE/TCE contamination (CIV-727)
    # NAICS 812320 = Drycleaning and Laundry Services (except Coin-Operated)
    # NAICS 812310 = Coin-Operated Laundries and Drycleaners
    # Dry cleaners are among the most common sources of PCE contamination and
    # are explicit ASTM E1527-21 RECs. The 2-digit parent "81" (Other Services)
    # has no risk tier, so without this override dry cleaners with NAICS codes
    # score 0 — worse than the name-based fallback which correctly catches them.
    # Parallels SIC 7211/7216 already in SIC_RISK_TIERS as Tier 1.
}

# 2-digit NAICS prefix → (tier_number, point_deduction)
NAICS_RISK_TIERS: dict[str, tuple[int, int]] = {
    # Tier 1: High-risk industries (-15 pts)
    "21": (1, 15),  # Mining, Quarrying, Oil/Gas
    "32": (1, 15),  # Chemical, Petroleum, Plastics manufacturing
    "56": (1, 15),  # Waste management and remediation
    # Tier 2: Moderate-risk (-10 pts)
    "22": (2, 10),  # Utilities
    "31": (2, 10),  # Food, Textile, Apparel manufacturing
    "33": (2, 10),  # Primary/Fabricated metals, Machinery manufacturing
    "48": (2, 10),  # Transportation
    "49": (2, 10),  # Warehousing
    # Tier 3: Low-risk (-5 pts)
    "23": (3, 5),   # Construction
    "42": (3, 5),   # Wholesale trade
    "44": (3, 5),   # Retail trade
    "45": (3, 5),   # Retail trade (continued)
    "11": (3, 5),   # Agriculture
}

# ---------------------------------------------------------------------------
# NAICS name-pattern suppression
# ---------------------------------------------------------------------------
#
# EPA ECHO NAICS assignments are sometimes wrong — a dental practice may be
# coded as Medical Instrument Manufacturing (339114 → NAICS 33 → "Moderate-risk
# Manufacturing +10") when the correct code is Healthcare (621210 → no penalty).
#
# These patterns suppress a NAICS industry penalty when the facility name clearly
# indicates a different sector. The match is applied in _naics_risk_adjustment()
# after the normal tier lookup, and only suppresses penalties — it never adds risk.
#
# Pattern groups → set of 2-digit NAICS prefixes whose penalty is suppressed.
# Only penalties for those prefix groups are zeroed out; SIC-based adjustments
# are unaffected.
NAICS_NAME_SUPPRESSION: list[tuple[re.Pattern, set[str]]] = [
    # Healthcare / dental / medical / veterinary names should not be penalised
    # for Manufacturing codes (3x) — EPA sometimes assigns instrument/supply
    # manufacturing codes to end-user healthcare facilities.
    #
    # Complete-word terms use \b...\b; prefix-style stems (orthodont*, periodon*,
    # veterinar*, etc.) use \b at the start only so that "Orthodontics", "Periodontal",
    # and "Veterinary" are all matched.
    (
        re.compile(
            r'\b('
            # Complete-word or suffix-independent terms
            r'dental|dentist|dentistry|'
            r'oral\s+surg|dental\s+care|dental\s+clinic|'
            r'medical|clinic|hospital|healthcare|health\s+care|'
            r'physician|pediatric|family\s+practice|urgent\s+care|'
            r'chiropractic|pharmacy|pharmacist|'
            r'vet\s+clinic|animal\s+hospital|'
            r'nursing\s+home|assisted\s+living|dialysis|rehab|'
            r'physical\s+therapy|occupational\s+therapy'
            r')\b'
            r'|'
            # Prefix-style stems — \b at start only (match any word starting with stem)
            r'\b(?:orthodont|periodon|endodon|optom|ophthalm|veterinar)',
            re.IGNORECASE,
        ),
        {"31", "32", "33"},  # Suppress Manufacturing tier penalties
    ),
    # Government / parks / recreation facilities should not be penalised for
    # Food & Accommodation codes (72xx) — EPA ECHO sometimes assigns restaurant
    # NAICS codes (e.g. 72210 Full-Service Restaurants) to state park gift shops,
    # visitor centers, and similar government facilities.  NAICS 72 has no current
    # risk tier, but this suppression prevents future tier additions from
    # incorrectly penalising parks/government/recreation sites.
    (
        re.compile(
            r'\b('
            r'state\s+park|national\s+park|city\s+park|county\s+park|'
            r'state\s+parks|national\s+parks|'
            r'park\s+service|parks\s+(?:and\s+)?recreation|'
            r'visitor\s+center|nature\s+center|'
            r'dept\s+of\s+(?:parks|conservation|natural\s+resources)|'
            r'department\s+of\s+(?:parks|conservation|natural\s+resources)|'
            r'public\s+works|public\s+utilities|'
            r'city\s+hall|county\s+(?:building|office|government)|'
            r'school\s+district|military\s+base|air\s+force|army\s+base|'
            r'fire\s+(?:station|dept|department)|'
            r'police\s+(?:station|dept|department)|'
            r'water\s+(?:authority|district|utility|treatment)|'
            r'wastewater|sewage\s+treatment|water\s+reclamation'
            r')\b',
            re.IGNORECASE,
        ),
        {"72"},  # Suppress Food & Accommodation tier penalties for government/parks
    ),
]

# ---------------------------------------------------------------------------
# NAICS name-pattern upgrade
# ---------------------------------------------------------------------------
#
# Some facilities are misclassified as Warehousing (NAICS 49xx) in EPA ECHO
# or state source data when they are in fact heavy industrial operations
# (cement plants, chemical plants, refineries, etc.). NAICS 49 is Tier 2
# in our risk table (-10 pts), but these facilities should be Tier 1 (-15 pts).
#
# When a facility name matches an industrial keyword AND its NAICS falls in one
# of the listed 2-digit prefixes, the risk is upgraded to (tier, deduction).
# Upgrade only applies when the name-matched code would otherwise produce a
# lower deduction — it never reduces an existing higher deduction.
#
# Pattern groups → (upgraded_tier, upgraded_deduction, set of 2-digit NAICS prefixes)
NAICS_NAME_UPGRADE: list[tuple[re.Pattern, int, int, set[str]]] = [
    # Heavy industrial names assigned to Warehousing (49xx) or Machinery
    # Manufacturing (33xx) should be Tier 1. Cement, chemical, refinery,
    # smelter, foundry, and similar facilities are never mere warehouses or
    # machinery manufacturers — misassignment understates environmental risk.
    #
    # 33xx (Industrial Machinery Manufacturing) is included because EPA ECHO
    # sometimes assigns industrial machinery NAICS codes (333xxx, 334xxx) to
    # petroleum chemicals processors and refinery-related operations that have
    # no correct match in the NAICS hierarchy — the machinery codes describe
    # equipment made, not the chemical/petroleum processes operated on-site.
    (
        re.compile(
            r'\b('
            r'cement|concrete|'
            r'chemical|chem\s+plant|'
            r'refin(?:ery|ing)|petroleum\s+plant|'
            r'smelter|smelting|foundry|'
            r'steel\s+mill|steel\s+plant|'
            r'manufacturing\s+plant|industrial\s+plant|'
            r'power\s+plant|generating\s+station|'
            r'incinerator|waste\s+treatment'
            r')\b',
            re.IGNORECASE,
        ),
        1,   # Upgrade to Tier 1
        15,  # Deduction: 15 pts
        {"49", "33"},  # Upgrade Warehousing and Machinery Manufacturing misclassifications
    ),
]

# ---------------------------------------------------------------------------
# Program-signal NAICS upgrade
# ---------------------------------------------------------------------------
#
# Some programs indicate on-site chemical handling that overrides a low-risk
# NAICS label. Used Oil and RCRA enrollment means a facility handles regulated
# waste regardless of its NAICS sector. If the NAICS-only deduction is Tier 3
# (low-risk: wholesale/retail, -5pts), upgrade to Tier 2 (moderate, -10pts).
#
# "used oil" appears as a TCEQ program; RCRA is standard EPA.
_PROGRAM_NAICS_UPGRADE_PATTERNS: list[re.Pattern] = [
    re.compile(r'\bused\s+oil\b', re.IGNORECASE),
    re.compile(r'\brcra\b', re.IGNORECASE),
]

_PROGRAM_UPGRADE_NAICS_PREFIXES: set[str] = {
    "42", "44", "45",  # Wholesale and Retail Trade (Tier 3)
}

# SIC code → (tier_number, point_deduction).
# SIC codes use 4-digit exact match. These supplement NAICS when a facility's
# SIC classification captures environmental risk more precisely.
SIC_RISK_TIERS: dict[str, tuple[int, int]] = {
    # Tier 1: High-risk SIC codes (-15 pts)
    "5015": (1, 15),  # Motor Vehicle Parts, Used (auto salvage yards)
    "5093": (1, 15),  # Scrap and Waste Materials
    "5541": (1, 15),  # Gasoline Stations (w/ service & convenience stores) — UST risk (CIV-572)
    "5171": (1, 15),  # Petroleum Bulk Stations & Terminals — UST/bulk fuel risk (CIV-572)
    "5172": (1, 15),  # Petroleum & Petroleum Products Wholesalers — bulk fuel risk (CIV-572)
    "7211": (1, 15),  # Power Laundries / Dry Cleaning
    "7216": (1, 15),  # Dry Cleaning Plants, Except Rug Cleaning
    # Tier 2: Moderate-risk SIC codes (-10 pts)
    "7532": (2, 10),  # Top, Body, and Upholstery Repair Shops
    "7533": (2, 10),  # Auto Exhaust System Repair Shops
    "7534": (2, 10),  # Tire Retreading and Repair Shops
    "7535": (2, 10),  # Automotive Glass Replacement Shops
    "7536": (2, 10),  # Automotive Transmission Repair Shops
    "7537": (2, 10),  # Automotive Repair Shops, NEC
    "7538": (2, 10),  # General Automotive Repair Shops
    "7539": (2, 10),  # Automotive Repair Shops, NEC
    "7542": (2, 10),  # Carwashes
    "7549": (2, 10),  # Automotive Services, NEC (includes lube, alignment)
}

# 2-digit SIC prefix → (tier_number, point_deduction).
# Checked as a fallback when the 4-digit exact match in SIC_RISK_TIERS does not fire.
# Mirrors the NAICS 2-digit prefix approach: broad industry families carry inherent
# environmental risk regardless of the specific 4-digit sub-code assigned.
#
# SIC structure (selected high-risk families):
#   28xx = Chemicals and Allied Products (petroleum chemicals, industrial chemicals, plastics)
#   29xx = Petroleum Refining and Related Industries
#   10xx = Metal Mining
#   12xx = Coal Mining
#   13xx = Oil and Gas Extraction
#   14xx = Mining & Quarrying of Nonmetallic Minerals
#
# These parallel NAICS families already at Tier 1 (21xx = Mining/Oil/Gas, 32xx = Chemical).
# EPA ECHO sometimes assigns SIC codes from these families when NAICS codes are wrong
# (e.g., a petroleum chemicals refiner carrying NAICS 333xxx but SIC 2819).
SIC_2DIGIT_RISK_TIERS: dict[str, tuple[int, int]] = {
    # Tier 1: High-risk SIC 2-digit families (-15 pts)
    "10": (1, 15),  # Metal Mining
    "12": (1, 15),  # Coal Mining
    "13": (1, 15),  # Oil and Gas Extraction
    "14": (1, 15),  # Mining & Quarrying of Nonmetallic Minerals
    "26": (1, 15),  # Paper and Allied Products (pulp mills, bleaching)
    "28": (1, 15),  # Chemicals and Allied Products (industrial/inorganic/organic chemicals)
    "29": (1, 15),  # Petroleum Refining and Related Industries
    "33": (1, 15),  # Primary Metal Industries (smelting, steel, aluminum)
}


# ---------------------------------------------------------------------------
# Name-based industry inference (fallback when NAICS/SIC are absent)
# ---------------------------------------------------------------------------

# Each entry: (compiled_pattern, industry_label, tier_number, point_deduction)
# Patterns are checked in order; first match wins.
# Tier 1 = high-risk (-15), Tier 2 = moderate-risk (-10).
_NAME_INDUSTRY_PATTERNS: list[tuple[re.Pattern, str, int, int]] = [
    # Dry cleaners / laundry — PERC/PCE contamination, explicit ASTM E1527-21 REC
    (re.compile(r"\bdry\s*clean\w*\b", re.IGNORECASE),
     "Laundry/Dry Cleaning", 1, 15),
    (re.compile(r"\b(laundromat|coin\s*laundry|launder)\b", re.IGNORECASE),
     "Laundry/Dry Cleaning", 1, 15),
    (re.compile(r"\bcleaner(s)?\b", re.IGNORECASE),
     "Laundry/Dry Cleaning", 1, 15),
    # Gas stations / petroleum retail — UST contamination risk
    (re.compile(r"\b(gas\s+station|filling\s+station|service\s+station|fuel\s+station)\b", re.IGNORECASE),
     "Petroleum Retail", 1, 15),
    (re.compile(r"\b(exxon|mobil|chevron|bp|shell|valero|marathon|sunoco|citgo|76|arco|texaco)\b", re.IGNORECASE),
     "Petroleum Retail", 1, 15),
    # Auto repair / body shops — solvent + oil contamination
    (re.compile(r"\b(auto\s+repair|body\s+shop|auto\s+body|collision\s+center|muffler|transmission\s+shop|brake\s+shop)\b", re.IGNORECASE),
     "Auto Repair", 2, 10),
    (re.compile(r"\b(jiffy\s+lube|midas|pep\s+boys|meineke|maaco|grease\s+monkey|oil\s+change)\b", re.IGNORECASE),
     "Auto Repair", 2, 10),
    # Scrap / salvage yards — metals contamination
    (re.compile(r"\b(scrap|salvage\s+yard|junk\s+yard|junkyard|auto\s+salvage)\b", re.IGNORECASE),
     "Scrap/Salvage", 1, 15),
    # Photo processing / printing — solvent contamination
    (re.compile(r"\b(photo\s+process|photo\s+lab|print\s+shop|printing)\b", re.IGNORECASE),
     "Printing/Photo", 2, 10),
]


def infer_industry_from_name(name: str | None) -> tuple[str | None, int | None, int]:
    """Return (industry_label, tier_number, point_deduction) inferred from facility name.

    Returns (None, None, 0) if no pattern matches or name is absent.
    Used as a fallback when a facility has no NAICS or SIC codes.
    """
    if not name:
        return None, None, 0
    for pattern, label, tier, deduction in _NAME_INDUSTRY_PATTERNS:
        if pattern.search(name):
            return label, tier, deduction
    return None, None, 0


def _naics_risk_adjustment(
    naics_codes: str | None,
    facility_name: str | None = None,
    programs: str | None = None,
) -> tuple[int, int | None]:
    """Return (point_deduction, tier_number) based on NAICS codes.

    Checks 4-digit NAICS prefix overrides before falling back to the
    2-digit prefix. Uses the highest-risk tier found among all codes.

    Name suppression: if the facility name matches a healthcare/dental pattern,
    Manufacturing NAICS penalties (3x) are zeroed out — EPA frequently
    mis-assigns instrument/supply manufacturing codes to end-user healthcare
    facilities (e.g., NAICS 339114 on a dental practice).

    Name upgrade: if the facility name matches an industrial keyword (cement,
    chemical, refinery, etc.) but is classified as Warehousing (49xx), the
    tier is upgraded to Tier 1 — EPA/state sources sometimes assign 49311
    to heavy industrial facilities that are clearly not warehouses.

    Program upgrade: if the facility has Used Oil or RCRA programs and the
    NAICS-only deduction is Tier 3 (wholesale/retail), upgrade to Tier 2
    (moderate) — on-site chemical handling overrides the low-risk label.
    """
    if not naics_codes:
        return 0, None

    # Build set of NAICS 2-digit prefixes whose penalties should be suppressed
    # based on facility name (e.g. dental office should not get Manufacturing hit).
    suppressed_prefixes: set[str] = set()
    if facility_name:
        for pattern, prefixes in NAICS_NAME_SUPPRESSION:
            if pattern.search(facility_name):
                suppressed_prefixes.update(prefixes)

    best_tier = None
    best_deduction = 0
    best_prefix = None  # track the 2-digit prefix that produced best_deduction

    for code in naics_codes.split(","):
        code = code.strip()
        # 4-digit prefix check first (more specific overrides)
        if len(code) >= 4:
            prefix4 = code[:4]
            if prefix4 in NAICS_4DIGIT_RISK_TIERS:
                tier, deduction = NAICS_4DIGIT_RISK_TIERS[prefix4]
                if deduction > best_deduction:
                    best_deduction = deduction
                    best_tier = tier
                    best_prefix = code[:2]
                continue  # 4-digit match found; skip 2-digit fallback for this code
        # 2-digit prefix fallback
        if len(code) >= 2:
            prefix = code[:2]
            if prefix in suppressed_prefixes:
                continue  # name-pattern suppression: skip this code's penalty
            if prefix in NAICS_RISK_TIERS:
                tier, deduction = NAICS_RISK_TIERS[prefix]
                if deduction > best_deduction:
                    best_deduction = deduction
                    best_tier = tier
                    best_prefix = prefix

    # Name-pattern upgrade: industrial facility names misclassified as Warehousing
    # (or other low/moderate-risk NAICS) should be upgraded to their correct tier.
    if facility_name and best_prefix is not None:
        for pattern, up_tier, up_deduction, upgrade_prefixes in NAICS_NAME_UPGRADE:
            if best_prefix in upgrade_prefixes and pattern.search(facility_name):
                if up_deduction > best_deduction:
                    best_deduction = up_deduction
                    best_tier = up_tier
                break  # only apply first matching upgrade rule

    # Program-signal upgrade: Used Oil / RCRA enrollment signals on-site
    # chemical handling; override Tier 3 (wholesale/retail) with Tier 2.
    if best_tier == 3 and programs:
        naics_prefix_used = None
        for code in naics_codes.split(","):
            code = code.strip()
            if len(code) >= 2 and code[:2] in _PROGRAM_UPGRADE_NAICS_PREFIXES:
                naics_prefix_used = code[:2]
                break
        if naics_prefix_used:
            for prog_pattern in _PROGRAM_NAICS_UPGRADE_PATTERNS:
                if prog_pattern.search(programs):
                    best_tier = 2
                    best_deduction = 10
                    break

    return best_deduction, best_tier


def _sic_risk_adjustment(sic_codes: str | None) -> tuple[int, int | None]:
    """Return (point_deduction, tier_number) based on SIC codes.

    Checks the 4-digit SIC code against SIC_RISK_TIERS first (exact match),
    then falls back to the 2-digit prefix in SIC_2DIGIT_RISK_TIERS.
    Uses the highest-risk tier found among all SIC codes for the facility.
    """
    if not sic_codes:
        return 0, None

    best_tier = None
    best_deduction = 0

    for code in sic_codes.split(","):
        code = code.strip()
        if len(code) >= 4:
            # 4-digit exact match takes precedence (more specific)
            prefix4 = code[:4]
            if prefix4 in SIC_RISK_TIERS:
                tier, deduction = SIC_RISK_TIERS[prefix4]
                if deduction > best_deduction:
                    best_deduction = deduction
                    best_tier = tier
                continue  # 4-digit match found; skip 2-digit fallback for this code
        # 2-digit prefix fallback (broad industry family)
        if len(code) >= 2:
            prefix2 = code[:2]
            if prefix2 in SIC_2DIGIT_RISK_TIERS:
                tier, deduction = SIC_2DIGIT_RISK_TIERS[prefix2]
                if deduction > best_deduction:
                    best_deduction = deduction
                    best_tier = tier

    return best_deduction, best_tier


# ---------------------------------------------------------------------------
# Layer 3: Multi-program complexity
# ---------------------------------------------------------------------------


def _program_complexity_adjustment(programs: str | None) -> tuple[int, int]:
    """Return (point_deduction, program_count) based on regulatory programs."""
    if not programs:
        return 0, 0

    # Deduplicate: unified_facilities GROUP_CONCAT may produce duplicates
    program_list = list(dict.fromkeys(
        p.strip() for p in programs.split(",") if p.strip()
    ))
    count = len(program_list)

    if count >= 4:
        return 12, count
    elif count == 3:
        return 8, count
    elif count == 2:
        return 5, count
    else:
        return 0, count


# ---------------------------------------------------------------------------
# Layer 3b: Program-type risk
# ---------------------------------------------------------------------------

# Programs indicating acknowledged contamination or heightened regulatory
# attention. These warrant inherent risk deductions regardless of violation
# history, because being listed in these programs means contamination has
# been identified or is being investigated.
_HIGH_RISK_PROGRAMS = {
    "federal superfund": 30,
    "npl": 30,
    "national priorities list": 30,
    "cerclis": 20,
    "sems": 20,
    "superfund": 25,
    "state response": 20,
    "known contamination": 20,
    "known contaminated sites": 20,
    "kcsl": 20,           # NJ DEP: Known Contaminated Sites List
    "rr": 20,            # OH EPA: Remediation Required (mandated cleanup)
    # Voluntary Cleanup Programs (VCP): enrollment requires documented releases —
    # treated as a REC under ASTM E1527-21. Weight matches "state response" (20)
    # and the VCP_SCORE_FLOOR (50) ensures High risk designation.
    "vcp": 30,
    "voluntary cleanup": 30,
    "vap": 30,            # OH EPA: Voluntary Action Program (= VCP)
    # Mandatory corrective action programs: documented hazardous waste releases
    # requiring state-ordered cleanup. More serious than VCP (mandatory, not
    # voluntary). CORRECTIVE_ACTION_SCORE_FLOOR (55) ensures High risk.
    "ihwca": 30,          # TCEQ: Industrial and Hazardous Waste Corrective Action
    "bcp": 30,            # NY DEC: Brownfield Cleanup Program (= state VCP, CIV-712)
    "brownfield cleanup": 30,  # NY DEC: Brownfield Cleanup Program full name
}
_MODERATE_RISK_PROGRAMS = {
    "brownfield": 10,
    "brownfields": 10,
    "erp": 15,            # NY DEC: Environmental Restoration Program (active remediation)
    "lust": 15,
    "leaking underground storage": 15,
    "corrective action": 15,
    "corracts": 15,
    "rcra ca": 15,            # EPA RCRA Corrective Action (mandated hazardous waste cleanup)
    "rcra lqg": 10,           # RCRA Large Quantity Generator (>=1,000 kg/mo hazwaste -- REC in Phase I ESA)
    "rcra sqg": 5,            # RCRA Small Quantity Generator (100-1,000 kg/mo hazwaste -- moderate signal)
    "remediation": 15,
    "cleanup": 15,
    "post rem": 15,
    "confirmed leaking": 15,
    "contamination": 15,
    "pfas": 15,
    "petroleum restoration": 15,  # FL DEP: state LUST cleanup program
    "petroleum": 10,              # Generic petroleum contamination programs
    "formerly used defense": 15,  # FUDS: DoD contamination sites
    "fuds": 15,                   # FUDS abbreviation
    "ust incident": 10,           # NC DEQ: UST incident records
    "ust": 10,                    # NJ DEP/generic: Underground Storage Tank registration
    "leaking underground": 15,    # Generic leaking UST
    "stageii": 10,                # TCEQ: Stage II Vapor Recovery — gasoline dispensing = UST risk
    "pstreg": 10,                 # TCEQ: Petroleum Storage Tank Registration — UST risk
    "pstnr": 10,                  # TCEQ: PST Non-Reporter — enrolled in PST program, not reporting
    "ustol": 10,                  # TCEQ: Underground Storage Tank On-Line — active UST registrant
    "smbrp": 10,
    "investigation": 10,
    "tiered permit": 10,
    "spill": 10,
    "sa": 15,            # OH EPA: Site Assessment (contamination investigation)
    "er": 10,            # OH EPA: Emergency Response (spill/release)
    "cof": 5,            # OH EPA: Closure (formerly contaminated, lower risk)
}


_PROGRAM_RISK_PATTERNS: list[tuple[re.Pattern, int]] = sorted(
    [(re.compile(r'\b' + re.escape(kw) + r'\b'), ded)
     for kw, ded in {**_HIGH_RISK_PROGRAMS, **_MODERATE_RISK_PROGRAMS}.items()],
    key=lambda x: -x[1],
)

# NPL sites represent the most severe contamination in the federal system.
# Active NPL listing is near-automatic maximum risk — CERCLA enforcement does
# not produce "violations" in ECHO, so the violation-based scoring underestimates
# these sites. Floor score at 70 so they always display as high risk.
NPL_SCORE_CAP = 70

# Contamination-designated sites (SEMS, KCSL, CERCLIS, etc.) represent facilities
# with confirmed or investigated contamination. Any site on a contamination
# tracking list must score at least 60 (High Risk) regardless of violation history.
# SEMS is a federal contamination tracking system — listing means contamination
# has been confirmed or is under active CERCLA investigation, which is more severe
# than a state voluntary cleanup (VCP, floor 50). Raising from 50→60 ensures
# consultants can differentiate SEMS/federal contamination sites from VCP
# sites in sort-by-risk views (CIV-561).
CONTAMINATION_SCORE_CAP = 60

# RCRA TSDF (Treatment, Storage, Disposal Facilities) operate under a Part B
# permit (40 CFR Parts 264/265) — the most stringent RCRA tier. In EDR and
# ASTM E1527-21 Phase I practice, a TSDF within the search radius constitutes
# a REC by definition. Floor score at 60 (HIGH risk) before violation history.
TSDF_SCORE_FLOOR = 60

# VCP (Voluntary Cleanup Program) sites have documented releases — enrollment
# requires evidence of contamination. Under ASTM E1527-21 these qualify as RECs
# or Controlled RECs requiring evaluation. Floor score at 50 (just into HIGH)
# so VCP sites always display as High Risk, matching EDR/professional practice.
VCP_SCORE_FLOOR = 50

# Mandatory corrective action programs (IHWCA, etc.) have documented hazardous
# waste releases requiring state-ordered cleanup. Stricter than VCP (mandatory,
# not voluntary) but below TSDF (active handling vs. past-release remediation).
CORRECTIVE_ACTION_SCORE_FLOOR = 55

_NPL_PATTERNS: list[re.Pattern] = [
    re.compile(r'\bnpl\b', re.IGNORECASE),
    re.compile(r'\bfederal superfund\b', re.IGNORECASE),
    re.compile(r'\bnational priorities list\b', re.IGNORECASE),
]

# Programs that indicate Voluntary Cleanup Program (VCP) enrollment.
# VCP sites have documented releases — enrollment requires evidence of
# contamination. Includes state equivalents (VAP for Ohio, BCP for NY, etc.).
# NY BCP (Brownfield Cleanup Program) requires documented contamination for
# enrollment and qualifies as a REC under ASTM E1527-21 (CIV-712).
_VCP_PATTERNS: list[re.Pattern] = [
    re.compile(r'\bvcp\b', re.IGNORECASE),
    re.compile(r'\bvoluntary cleanup\b', re.IGNORECASE),
    re.compile(r'\bvap\b', re.IGNORECASE),   # OH EPA: Voluntary Action Program
    re.compile(r'\bbcp\b', re.IGNORECASE),   # NY DEC: Brownfield Cleanup Program
    re.compile(r'\bbrownfield cleanup\b', re.IGNORECASE),  # NY DEC: full name
]

# Programs that indicate RCRA Treatment, Storage, and Disposal Facility (TSDF)
# status -- Part B permit holders regulated under 40 CFR Parts 264/265.
_TSDF_PATTERNS: list[re.Pattern] = [
    re.compile(r'\brcra\s+tsdf\b', re.IGNORECASE),
    re.compile(r'\btsdf\b', re.IGNORECASE),
    re.compile(r'\btsd\b', re.IGNORECASE),
]

# Programs indicating mandatory (state-ordered) corrective action for
# documented hazardous waste releases. Currently TCEQ IHWCA; extensible
# to other state equivalents (e.g., PADEP Act 2 mandatory sites).
_CORRECTIVE_ACTION_PATTERNS: list[re.Pattern] = [
    re.compile(r'\bihwca\b', re.IGNORECASE),
    re.compile(r'\bindustrial\s+and\s+hazardous\s+waste\s+corrective\s+action\b', re.IGNORECASE),
]

# Programs that indicate confirmed or actively-investigated contamination.
# Facilities in any of these programs must score >= CONTAMINATION_SCORE_CAP.
_CONTAMINATION_PROGRAM_PATTERNS: list[re.Pattern] = [
    re.compile(r'\bsems\b', re.IGNORECASE),
    re.compile(r'\bcerclis\b', re.IGNORECASE),
    re.compile(r'\bkcsl\b', re.IGNORECASE),
    re.compile(r'\bknown contaminated sites\b', re.IGNORECASE),
    re.compile(r'\bknown contamination\b', re.IGNORECASE),
    re.compile(r'\bstate response\b', re.IGNORECASE),
]

# Residential buildings (apartments, condominiums, housing) registered with NJ
# DEP and EPA for boiler/generator air permits receive AONOCAPA enforcement
# actions for permit paperwork violations. These are not environmental
# contamination events. Without a cap, 10+ AONOCAPA violations push the
# facility score to 100 (Critical) — identical to a refinery. This is a
# product credibility issue (CIV-487).
#
# RESIDENTIAL_SCORE_CAP: residential-use buildings (lessors of residential
# property, property managers) cannot score above Medium Risk (49) from
# permit administrative violations alone. Contamination-flagged sites
# (KCSL, SEMS, NPL, VCP, TSDF) override this cap — those represent real
# environmental hazards regardless of building type.
RESIDENTIAL_SCORE_CAP = 49

# NAICS 6-digit prefixes that indicate residential or residential property
# management use only. These are checked against the first 6 digits of each
# NAICS code. We use narrow codes (531110, 531311) not the broad 531 parent
# to avoid capping industrial tenants whose landlord NAICS code appears as
# a secondary code (e.g. NAICS 531390 "Other Real Estate Activities" is
# assigned to industrial park owners).
_RESIDENTIAL_NAICS_PREFIXES: frozenset[str] = frozenset({
    "531110",  # Lessors of Residential Buildings and Dwellings
    "531311",  # Residential Property Managers
})

# Name patterns that identify residential facilities when NAICS codes are
# absent. Matched against the full facility name (case-insensitive).
_RESIDENTIAL_NAME_PATTERN: re.Pattern = re.compile(
    r'\b('
    r'condominium|condominium\s+assoc|condo\s+assoc|'
    r'condo\b|'
    r'apartments\b|'
    r'housing\s+(?:authority|assoc|inc|corp|llc|ltd)|'
    r'senior\s+(?:hous|cit|living|resident)|'
    r'residential\s+(?:tower|building|community|complex)'
    r')\b',
    re.IGNORECASE,
)


def _is_npl_site(programs: str | None) -> bool:
    """Return True if the facility is on the National Priorities List (NPL)."""
    if not programs:
        return False
    for pattern in _NPL_PATTERNS:
        if pattern.search(programs):
            return True
    return False


def _is_tsdf_site(programs: str | None) -> bool:
    """Return True if the facility is a RCRA Treatment, Storage, and Disposal Facility.

    RCRA TSDFs operate under a Part B permit (40 CFR Parts 264/265) and
    represent the most stringent RCRA regulatory tier. In Phase I ESA practice
    (ASTM E1527-21), a TSDF within the standard search radius constitutes a REC.
    """
    if not programs:
        return False
    for pattern in _TSDF_PATTERNS:
        if pattern.search(programs):
            return True
    return False


def _is_vcp_site(programs: str | None) -> bool:
    """Return True if the facility is enrolled in a Voluntary Cleanup Program.

    VCP enrollment requires documented contamination — sites cannot self-select
    into these programs. Under ASTM E1527-21 a VCP site qualifies as a REC or
    Controlled REC. Floor score at VCP_SCORE_FLOOR (50, High Risk).
    """
    if not programs:
        return False
    for pattern in _VCP_PATTERNS:
        if pattern.search(programs):
            return True
    return False


def _is_corrective_action_site(programs: str | None) -> bool:
    """Return True if the facility is in a mandatory corrective action program.

    Mandatory corrective action (e.g., TCEQ IHWCA) requires state-ordered
    cleanup for documented hazardous waste releases. More serious than VCP
    (mandatory, not voluntary) — floor at CORRECTIVE_ACTION_SCORE_FLOOR (55).
    """
    if not programs:
        return False
    for pattern in _CORRECTIVE_ACTION_PATTERNS:
        if pattern.search(programs):
            return True
    return False


def _is_contamination_program_site(programs: str | None) -> bool:
    """Return True if the facility is in a contamination-designated program.

    These programs indicate confirmed or actively-investigated contamination:
    SEMS, CERCLIS, KCSL (NJ DEP Known Contaminated Sites List), etc.
    Sites in these programs must score at least CONTAMINATION_SCORE_CAP (50)
    so they always display as High Risk.
    """
    if not programs:
        return False
    for pattern in _CONTAMINATION_PROGRAM_PATTERNS:
        if pattern.search(programs):
            return True
    return False


def _is_residential_only_facility(
    naics_codes: str | None,
    facility_name: str | None,
) -> bool:
    """Return True if the facility is a residential-use building.

    Residential buildings (apartment complexes, condominiums, housing
    associations) registered with environmental agencies for boiler/generator
    air permits should not score Critical solely because of permit
    administrative violations (AONOCAPA, NOV). Their max score is capped at
    RESIDENTIAL_SCORE_CAP (49) unless contamination programs (KCSL, NPL, VCP,
    TSDF) are also present — those override the cap.

    Detection priority:
    1. NAICS 531110/531311 present → residential (regardless of name).
    2. NAICS codes present but none are residential → NOT residential (NAICS
       takes precedence; prevents name-only false positives on industrial
       facilities with long addresses or unusual names).
    3. No NAICS codes → fall back to name patterns (condo, apartment, etc.).
    """
    if naics_codes:
        # Check each NAICS code for a residential prefix match
        any_numeric_code = False
        for code in naics_codes.split(","):
            code = code.strip()
            if len(code) >= 6 and code[:6].isdigit():
                any_numeric_code = True
                if code[:6] in _RESIDENTIAL_NAICS_PREFIXES:
                    return True
        # If we found numeric NAICS codes but none were residential, trust NAICS
        if any_numeric_code:
            return False
    # No numeric NAICS codes available — fall back to name matching
    if facility_name and _RESIDENTIAL_NAME_PATTERN.search(facility_name):
        return True
    return False


def _program_type_risk_adjustment(programs: str | None) -> int:
    """Return point deduction for high-risk program designations.

    A facility on the NPL, in a VCP, or designated as a Brownfield carries
    inherent contamination risk even with zero violation records. Returns
    the maximum single deduction found (does not stack multiple categories).

    Uses word-boundary matching to prevent false positives on short codes
    (e.g., "SA" should not match inside "waSte").
    """
    if not programs:
        return 0

    programs_lower = programs.lower()
    max_deduction = 0

    for pattern, deduction in _PROGRAM_RISK_PATTERNS:
        if deduction <= max_deduction:
            break  # sorted desc — nothing left can beat current max
        if pattern.search(programs_lower):
            max_deduction = deduction

    return max_deduction


# ---------------------------------------------------------------------------
# Layer 4: Data confidence
# ---------------------------------------------------------------------------


MIN_VIOLATIONS_FOR_TRACKING = 100


def _sources_with_violations(conn: sqlite3.Connection) -> frozenset[str]:
    """Return sources with enough violation records to be considered violation-tracking.

    A source needs at least MIN_VIOLATIONS_FOR_TRACKING violation records to
    qualify. This prevents a handful of stray records from flipping an entire
    source's facilities from low → moderate confidence.
    """
    rows = conn.execute(
        "SELECT facility_source, COUNT(*) AS cnt FROM violations "
        "GROUP BY facility_source HAVING cnt >= ?",
        (MIN_VIOLATIONS_FOR_TRACKING,),
    ).fetchall()
    return frozenset(r[0] for r in rows)


def _data_confidence(
    violation_count: int,
    raw_violation_count: int,
    covered_by_violation_source: bool,
) -> str:
    """Return a confidence level for the score based on data availability.

    Source-aware: distinguishes between "checked and clean" vs "never checked."

    - high: concrete violation data supports the risk assessment
    - moderate: no violations, but covered by a source that HAS violation data
      for other facilities (checked and appears clean)
    - low: only covered by sources with zero violation data across all their
      facilities (never effectively checked)
    (The old 'minimal' level has been removed — facilities with no source data
    now fall into 'low' since they were never effectively checked.)
    """
    if violation_count > 0:
        return "high"
    if raw_violation_count > 0:
        # Records exist but all were filtered out as non-violations.
        # The source clearly tracks violations — this is meaningful.
        return "moderate"
    if covered_by_violation_source:
        # Facility is in a source that has violations for OTHER facilities.
        # This facility having 0 means it was likely checked and is clean.
        return "moderate"
    # Only covered by sources that have no violation data at all.
    # We have no way to know if this facility is clean or just unchecked.
    return "low"


# ---------------------------------------------------------------------------
# Layer 5: Stormwater / water quality violation downweighting
# ---------------------------------------------------------------------------
#
# CA State Water Board (ca_waterboard) violations are routine NPDES water
# discharge and stormwater permit compliance issues. They are NOT the same
# risk signal as RCRA hazardous waste violations or Superfund listings.
#
# Without downweighting, a parking garage with 38 stormwater monitoring
# violations scores 100 — identical to a refinery with RCRA violations.
# This destroys consultant trust (CIV-358).
#
# Fix: stormwater/water quality violations are capped at a lower maximum
# contribution to the score. Non-stormwater violations (RCRA, CAA, cleanup,
# enforcement orders) are unaffected.
#
# The cap applies to the TOTAL deduction accumulated from stormwater
# violations only. If a facility has both stormwater and non-stormwater
# violations, the two buckets are computed separately; non-stormwater
# violations use the full scoring rules and the stormwater contribution
# is capped before adding.

# Patterns that identify stormwater / routine water discharge violations.
# Matches violation_type values produced by the ca_waterboard mapper.
_STORMWATER_VIO_PATTERNS: list[re.Pattern] = [
    re.compile(r'\bstormwater\b', re.IGNORECASE),
    re.compile(r'\bstorm\s+water\b', re.IGNORECASE),
    re.compile(r'\bsmarts\b', re.IGNORECASE),          # SMARTS permit system
    re.compile(r'\bmonitoring\s+and\s+reporting\b', re.IGNORECASE),  # M&R violations
    re.compile(r'\bmonitoring\s+&\s+reporting\b', re.IGNORECASE),
    re.compile(r'\bmonitoring\/reporting\b', re.IGNORECASE),
    re.compile(r'\beffluent\s+limit', re.IGNORECASE),  # Effluent Limitation Violation
    re.compile(r'\bnpdes\b', re.IGNORECASE),           # Generic NPDES permit violation
    re.compile(r'\bwastewater\s+violation\b', re.IGNORECASE),
]

# Maximum total score deduction from stormwater/water quality violations.
# A facility with ONLY stormwater violations can contribute at most this
# many points to the score (before NAICS/program adjustments).
# Set to 25: "medium" risk territory, well below the 50+ "high" threshold.
STORMWATER_MAX_DEDUCTION = 25


def _is_stormwater_violation(vtype: str, severity: str) -> bool:
    """Return True if this violation is a routine stormwater / water quality type.

    Used to identify ca_waterboard SMARTS and CIWQS M&R violations that
    should have a capped contribution to facility scores.
    """
    text = f"{vtype} {severity}"
    return any(pat.search(text) for pat in _STORMWATER_VIO_PATTERNS)




# ---------------------------------------------------------------------------
# Temporal decay
# ---------------------------------------------------------------------------
#
# Violations age out of relevance over time. ASTM E1527-21 distinguishes
# historical RECs (HRECs) from current RECs. EDR and other industry tools
# use time-weighted violation counts. A facility with 195 violations from
# 2014 that has been clean since 2018 should not score identically to a
# facility with 195 violations last month.
#
# Decay tiers (applied per-violation to both severity deductions and bulk count):
#   0-3 years:   1.00 (full weight -- active compliance history)
#   3-5 years:   0.75 (diminishing relevance, still within TCEQ 5-year window)
#   5-10 years:  0.50 (historical, reduced weight; still a signal)
#   10+ years:   0.25 (heavily discounted; aligns with ASTM HREC treatment)
#
# Violations with no date default to 1.0 -- we can't discount what we can't date.

TEMPORAL_DECAY_TIERS: list[tuple[int, float]] = [
    (1095, 1.00),   # < 3 years (days threshold, weight)
    (1825, 0.75),   # 3-5 years
    (3650, 0.50),   # 5-10 years
]
TEMPORAL_DECAY_OLDEST: float = 0.25  # 10+ years


def _temporal_decay_weight(vdate: date | None, today: date) -> float:
    """Return a decay multiplier (0.0-1.0) based on violation age.

    Used to discount the impact of old violations on the compliance score.
    Violations without a date receive full weight (1.0) -- unknown age is not
    assumed to be old.
    """
    if vdate is None:
        return 1.0
    days_ago = (today - vdate).days
    for threshold, weight in TEMPORAL_DECAY_TIERS:
        if days_ago < threshold:
            return weight
    return TEMPORAL_DECAY_OLDEST


# ---------------------------------------------------------------------------
# Resolution status discount
# ---------------------------------------------------------------------------
#
# Violations with a closed/resolved/withdrawn status are discounted relative
# to active violations. A facility that had environmental issues in the past
# but resolved them should score lower than one with the same violations still
# unresolved.
#
# Resolution weight (applied as a multiplier on top of temporal decay):
#   Active / unknown status:   1.00  (full weight)
#   Closed / resolved / etc.:  0.25  (heavily discounted)
#
# This matches the ASTM E1527-21 treatment of Historical RECs (HRECs) —
# resolved enforcement actions are noted but do not drive current risk.
#
# Sources of resolved statuses (case-insensitive match):
#   "Closed"      -- NJ DEP (DOC_STATUS), many state sources
#   "Resolved"    -- TCEQ, VA DEQ, normalized sources
#   "Withdrawn"   -- enforcement actions withdrawn before penalty
#   "Superseded"  -- NJ DEP: action replaced by a newer order
#   "Voided"      -- NJ DEP: action nullified
#   "Completed"   -- some state sources use this for fully resolved actions
#   "Dismissed"   -- enforcement actions dismissed without penalty
#   "Rescinded"   -- action rescinded (e.g. TCEQ, OH EPA)
#   "Closed (NFA)"   -- Closed with No Further Action required
#   "Effective-NFA"  -- Effective but No Further Action required
#   "Addressed-Local", "Addressed (State)", "Addressed (EPA)" -- addressed
#
# Note: "Active", "Active Violation", null/None, "In Review",
# "Pending Resolution", "Hearing Requested", "SNC: *" are all treated as
# active (full weight 1.0).

_RESOLVED_VIOLATION_STATUSES: frozenset[str] = frozenset({
    "resolved",
    "closed",
    "withdrawn",
    "superseded",
    "voided",
    "completed",
    "dismissed",
    "rescinded",
    "closed (nfa)",
    "effective-nfa",
    "addressed-local",
    "addressed (state)",
    "addressed (epa)",
})

RESOLUTION_DISCOUNT_WEIGHT: float = 0.25


def _resolution_weight(status: str | None) -> float:
    """Return a weight multiplier based on violation resolution status.

    Returns 0.25 for closed/resolved/withdrawn violations; 1.0 otherwise.
    Applied as a multiplier alongside temporal decay in _compute_score().
    """
    if status is None:
        return 1.0
    return RESOLUTION_DISCOUNT_WEIGHT if status.strip().lower() in _RESOLVED_VIOLATION_STATUSES else 1.0


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _compute_score(
    violations: list,
    naics_codes,
    programs,
    covered_by_violation_source: bool,
    sic_codes=None,
    facility_name: str | None = None,
) -> dict:
    raw_violation_count = len(violations)

    real_violations = [
        r for r in violations if is_real_violation(r[0], r[2], r[3])
    ]

    score = 100
    violation_count = len(real_violations)
    latest_date = None
    nonsig_sev_deduction = 0.0
    stormwater_deduction = 0.0  # accumulated deduction from stormwater violations only

    today = date.today()

    # Track weighted counts for bulk deductions (temporal-decay-adjusted).
    non_stormwater_weight_sum = 0.0
    stormwater_weight_sum = 0.0

    for row in real_violations:
        vtype = row[0] or ""
        vdate_str = row[1]
        severity = row[2] or ""
        desc = row[3] or ""
        status = row[4] if len(row) > 4 else None

        vdate = None
        if vdate_str:
            try:
                parsed = date.fromisoformat(str(vdate_str))
                # Reject sentinel/placeholder dates more than 5 years in the future
                # (e.g. 3000-12-31 from EPA CAA) to avoid spurious recency penalties
                # and misleading "Last Violation" display values.
                if parsed.year <= today.year + 5:
                    vdate = parsed
            except ValueError:
                pass

        if vdate and (latest_date is None or vdate > latest_date):
            latest_date = vdate

        decay = _temporal_decay_weight(vdate, today) * _resolution_weight(status)

        sev_lower = severity.lower() if severity else ""
        vtype_lower = vtype.lower() if vtype else ""

        is_stormwater = _is_stormwater_violation(vtype, severity)
        vio_deduction = 0.0  # per-violation deduction this iteration (float, decay-adjusted)

        if "significant" in sev_lower or "snc" in sev_lower or "significant" in vtype_lower:
            vio_deduction = 15 * decay
        elif vtype_lower in _SIGNIFICANT_ENFORCEMENT_TYPES:
            vio_deduction = 15 * decay
        elif vtype_lower in _STANDARD_ENFORCEMENT_TYPES or (
            sev_lower != "low" and ("violation" in vtype_lower or "yes" in vtype_lower)
        ):
            # nonsig_sev_deduction caps non-stormwater per-violation deductions.
            # Stormwater violations are capped separately via STORMWATER_MAX_DEDUCTION,
            # so they do not consume capacity on the non-stormwater cap.
            if not is_stormwater and nonsig_sev_deduction < 40:
                vio_deduction = 5 * decay
                nonsig_sev_deduction += vio_deduction
            elif is_stormwater:
                vio_deduction = 5 * decay

        desc_lower = desc.lower() if desc else ""
        if desc_lower in ("on", "yes"):
            vio_deduction += 5 * decay

        if is_stormwater:
            stormwater_deduction += vio_deduction
            stormwater_weight_sum += decay
        else:
            score -= vio_deduction
            non_stormwater_weight_sum += decay

    # Violation count bulk deduction: use temporally-weighted counts so that
    # a hundred decade-old violations don't equal a hundred recent ones.
    score -= min(non_stormwater_weight_sum * 3, 40)
    stormwater_deduction += min(stormwater_weight_sum * 3, 40)

    # Apply stormwater deduction with cap: routine water compliance violations
    # cannot push a facility's score above STORMWATER_MAX_DEDUCTION points.
    # Hazmat/cleanup violations (non-stormwater) are unaffected.
    score -= min(stormwater_deduction, STORMWATER_MAX_DEDUCTION)

    if latest_date:
        days_ago = (today - latest_date).days
        if days_ago < 365:
            score -= 15
        elif days_ago < 1095:
            score -= 8
        elif days_ago < 1825:
            score -= 3

    naics_deduction, naics_tier = _naics_risk_adjustment(naics_codes, facility_name, programs)
    sic_deduction, sic_tier = _sic_risk_adjustment(sic_codes)

    # Use whichever industry risk adjustment is highest (NAICS or SIC).
    # SIC codes are often more specific than NAICS for Phase I REC facility types.
    if sic_deduction > naics_deduction:
        industry_deduction = sic_deduction
        naics_tier = sic_tier
    else:
        industry_deduction = naics_deduction

    # Program-based industry inference: when programs explicitly identify a
    # petroleum storage or dispensing facility but no NAICS/SIC codes are present,
    # classify as Petroleum Retail (Tier 1, -15 pts).
    # STAGEII = TCEQ Stage II Vapor Recovery (gas stations dispensing gasoline).
    # PSTREG  = TCEQ Petroleum Storage Tank Registration (UST operators).
    # PSTNR   = TCEQ PST Non-Reporter (enrolled in PST program but not reporting).
    # USTOL   = TCEQ Underground Storage Tank On-Line (active UST registrant).
    # All signal petroleum storage operations with UST contamination risk.
    if not naics_codes and not sic_codes and programs:
        progs_upper = programs.upper()
        if (
            re.search(r'\bSTAGEII\b', progs_upper)
            or re.search(r'\bPSTREG\b', progs_upper)
            or re.search(r'\bPSTNR\b', progs_upper)
            or re.search(r'\bUSTOL\b', progs_upper)
        ):
            industry_deduction = 15
            naics_tier = 1

    # Name-based fallback: when NO NAICS or SIC codes are present at all, infer
    # industry risk from the facility name (e.g. "dry cleaner" → Tier 1, -15 pts).
    # Only triggered when codes are completely absent — not when codes exist but
    # happen to map to a low/no-risk sector (e.g. Health Care with NAICS 62).
    # Also skipped when program-based inference already assigned a deduction above.
    if not naics_codes and not sic_codes and industry_deduction == 0:
        _inferred_label, name_tier, name_ded = infer_industry_from_name(facility_name)
        if name_ded > 0:
            industry_deduction = name_ded
            naics_tier = name_tier

    score -= industry_deduction

    program_deduction, program_count = _program_complexity_adjustment(programs)
    score -= program_deduction

    program_type_deduction = _program_type_risk_adjustment(programs)
    score -= program_type_deduction

    score = max(0, min(100, round(score)))
    score = 100 - score  # Invert: 0=clean, 100=worst

    # Supplemental risk: the portion of the score driven by violations,
    # industry (NAICS/SIC), and program complexity — everything EXCEPT the
    # program-type deduction. When a score floor is applied, half of this
    # supplemental risk is added above the floor so that KCSL/SEMS/VCP/etc.
    # sites can differentiate based on their actual risk profile (CIV-720).
    supplemental_risk = max(0, score - program_type_deduction)

    def _apply_floor(current: int, floor: int) -> int:
        if current >= floor:
            return current
        return min(100, floor + round(supplemental_risk * 0.5))

    if _is_npl_site(programs):
        score = _apply_floor(score, NPL_SCORE_CAP)
    elif _is_contamination_program_site(programs):
        score = _apply_floor(score, CONTAMINATION_SCORE_CAP)

    if _is_vcp_site(programs):
        score = _apply_floor(score, VCP_SCORE_FLOOR)

    if _is_corrective_action_site(programs):
        score = _apply_floor(score, CORRECTIVE_ACTION_SCORE_FLOOR)

    if _is_tsdf_site(programs):
        score = _apply_floor(score, TSDF_SCORE_FLOOR)

    # Residential cap: apartment buildings and condominiums registered for
    # boiler/generator air permits should not score Critical solely from
    # permit administrative violations. Cap at Medium risk (49) unless
    # contamination programs (NPL, KCSL, SEMS, TSDF, VCP/BCP) are flagged —
    # those override this cap because contamination matters regardless of use type.
    if (
        not _is_npl_site(programs)
        and not _is_contamination_program_site(programs)
        and not _is_corrective_action_site(programs)
        and not _is_tsdf_site(programs)
        and not _is_vcp_site(programs)
        and _is_residential_only_facility(naics_codes, facility_name)
    ):
        score = min(score, RESIDENTIAL_SCORE_CAP)

    if score <= 25:
        risk_level = "low"
    elif score < 50:
        risk_level = "medium"
    elif score < 75:
        risk_level = "high"
    else:
        risk_level = "critical"

    confidence = _data_confidence(
        violation_count, raw_violation_count, covered_by_violation_source,
    )

    return {
        "score": score,
        "risk_level": risk_level,
        "confidence": confidence,
        "violation_count": violation_count,
        "raw_violation_count": raw_violation_count,
        "latest_violation_date": latest_date.isoformat() if latest_date else None,
        "naics_tier": naics_tier,
        "program_count": program_count,
    }


def score_facility(source_id: str, conn: sqlite3.Connection) -> dict:
    """Score a single facility based on violations, industry, and programs.

    Returns dict with: source_id, score, risk_level, violation_count,
    raw_violation_count, latest_violation_date, naics_tier, program_count,
    scored_at
    """
    # Get facility metadata for enrichment layers
    fac_row = conn.execute(
        "SELECT naics_codes, programs, sources, sic_codes, name FROM unified_facilities WHERE source_id = ?",
        (source_id,),
    ).fetchone()
    if not fac_row:
        # Fallback: unified table may not be rebuilt yet; query raw facilities
        fac_row = conn.execute(
            "SELECT naics_codes, programs, source AS sources, sic_codes, name FROM facilities WHERE source_id = ?",
            (source_id,),
        ).fetchone()

    naics_codes = fac_row[0] if fac_row else None
    programs = fac_row[1] if fac_row else None
    facility_sources_str = fac_row[2] if fac_row else None
    sic_codes = fac_row[3] if fac_row else None
    facility_name = fac_row[4] if fac_row else None

    # Determine if this facility is covered by any source that has violations
    vio_sources = _sources_with_violations(conn)
    facility_sources = set(
        s.strip() for s in (facility_sources_str or "").split(",") if s.strip()
    )
    covered_by_violation_source = bool(facility_sources & vio_sources)

    # Get violations — aggregate across canonical group via facility_matches
    rows = conn.execute(
        "SELECT v.violation_type, v.violation_date, v.severity, v.description, v.status "
        "FROM violations v "
        "JOIN facility_matches fm ON v.facility_source = fm.source "
        "  AND v.facility_source_id = fm.source_id "
        "WHERE fm.canonical_id = ?",
        (source_id,),
    ).fetchall()

    result = _compute_score(rows, naics_codes, programs, covered_by_violation_source, sic_codes, facility_name)
    return {
        "source_id": source_id,
        **result,
        "scored_at": datetime.now(timezone.utc).isoformat(),
    }


def score_state(state: str, conn: sqlite3.Connection, *, vio_sources: frozenset[str] | None = None) -> int:
    """Score all unified facilities in a state. Returns count scored.

    Bulk-loads facilities and violations in two queries instead of
    two per facility, then scores in-memory.
    """
    from collections import defaultdict

    state = state.upper()

    # Precompute which sources have violation data (use cached if provided)
    if vio_sources is None:
        vio_sources = _sources_with_violations(conn)

    # Bulk-load facility metadata for this state
    fac_rows = conn.execute(
        "SELECT source_id, naics_codes, programs, sources, sic_codes, name FROM unified_facilities WHERE state = ?",
        (state,),
    ).fetchall()
    if not fac_rows:
        # Fallback: unified table may not be rebuilt yet; query raw facilities
        fac_rows = conn.execute(
            "SELECT source_id, naics_codes, programs, source AS sources, sic_codes, name FROM facilities WHERE state = ?",
            (state,),
        ).fetchall()
    if not fac_rows:
        return 0

    source_ids = [r["source_id"] for r in fac_rows]
    logger.info("Loading violations for %d facilities...", len(source_ids))

    # Build lookup dict for facility metadata
    fac_meta = {
        r["source_id"]: {
            "naics_codes": r["naics_codes"],
            "programs": r["programs"],
            "sources": r["sources"] or "",
            "sic_codes": r["sic_codes"],
            "name": r["name"],
        }
        for r in fac_rows
    }

    # Bulk-load all violations for these facilities in one query.
    # Join through facility_matches to aggregate across canonical groups.
    # SQLite has a variable limit, so chunk the IN clause for large states.
    chunk_size = 900
    vio_grouped = defaultdict(list)
    total_vio = 0
    for i in range(0, len(source_ids), chunk_size):
        chunk = source_ids[i : i + chunk_size]
        placeholders = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT fm.canonical_id AS facility_source_id, "
            f"  v.violation_type, v.violation_date, v.severity, v.description, v.status "
            f"FROM violations v "
            f"JOIN facility_matches fm ON v.facility_source = fm.source "
            f"  AND v.facility_source_id = fm.source_id "
            f"WHERE fm.canonical_id IN ({placeholders})",
            chunk,
        ).fetchall()
        for r in rows:
            vio_grouped[r["facility_source_id"]].append(
                (r["violation_type"], r["violation_date"], r["severity"], r["description"], r["status"])
            )
            total_vio += 1

    logger.info("Loaded %d violation records. Scoring...", total_vio)

    # Score each facility using pre-loaded data
    scored_at = datetime.now(timezone.utc).isoformat()
    results = []

    for idx, sid in enumerate(source_ids):
        meta = fac_meta.get(sid, {})
        naics_codes = meta.get("naics_codes")
        programs = meta.get("programs")
        sic_codes = meta.get("sic_codes")
        facility_name = meta.get("name")
        sources_str = meta.get("sources", "")
        covered_by_violation_source = any(
            s.strip() in vio_sources for s in sources_str.split(",") if s.strip()
        )
        rows = vio_grouped.get(sid, [])

        r = _compute_score(rows, naics_codes, programs, covered_by_violation_source, sic_codes, facility_name)

        results.append((
            sid, r["score"], r["risk_level"], r["confidence"], r["violation_count"],
            r["raw_violation_count"], r["latest_violation_date"],
            r["naics_tier"], r["program_count"], scored_at,
        ))

        if (idx + 1) % 10000 == 0:
            logger.info("Scored %d/%d...", idx + 1, len(source_ids))

    # Bulk write results
    conn.executemany(
        """INSERT OR REPLACE INTO facility_scores
           (source_id, score, risk_level, confidence, violation_count,
            raw_violation_count, latest_violation_date,
            naics_tier, program_count, scored_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        results,
    )
    conn.commit()
    logger.info("Done: %d facilities scored.", len(results))

    return len(results)


def score_all(conn: sqlite3.Connection) -> int:
    """Score all unified facilities. Returns total count scored.

    Hybrid approach: computes vio_sources once, then delegates to
    score_state() per state. Keeps per-state memory footprint while
    avoiding redundant vio_sources queries across states.
    """
    vio_sources = _sources_with_violations(conn)

    states = [r[0] for r in conn.execute(
        "SELECT DISTINCT state FROM unified_facilities "
        "WHERE state IS NOT NULL ORDER BY state"
    ).fetchall()]

    if not states:
        logger.info("No facilities to score.")
        return 0

    logger.info("Scoring %d states (vio_sources cached)...", len(states))
    total = 0
    for i, state in enumerate(states, 1):
        logger.info("[%d/%d] %s", i, len(states), state)
        count = score_state(state, conn, vio_sources=vio_sources)
        total += count

    return total
