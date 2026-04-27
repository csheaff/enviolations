# Environmental Compliance Risk Score: Methodology

**Version**: 2.0
**Date**: March 2026
**Status**: Published

---

## Abstract

No industry standard exists for facility-level environmental compliance risk scoring. EPA uses categorical designations (SNC, HPV, Violation/No Violation), ESG rating agencies operate at the company level, and only two states (Texas, California) have published numerical scoring systems. This document describes the Compliance Risk Score (CRS), a 0-100 numerical score that quantifies environmental compliance risk for individual regulated facilities by synthesizing violation history, severity classifications, resolution status, industry risk profiles, and regulatory program complexity from federal and state data sources.

---

## 1. Problem Statement

Environmental due diligence today requires manually searching multiple government databases, interpreting inconsistent compliance designations across programs, and making subjective risk judgments. A single facility may be regulated under the Clean Water Act, Clean Air Act, RCRA, and Safe Drinking Water Act simultaneously, each with its own compliance vocabulary and reporting cadence.

**The gap**: No product aggregates multi-source government compliance data at the facility level, normalizes it across programs, and assigns a quantitative risk score. The CRS fills this gap.

**Who uses it**: Environmental consultants conducting Phase I assessments, lenders evaluating collateral risk, real estate investors screening acquisitions, insurance underwriters pricing environmental liability, and AI agents performing automated due diligence.

---

## 2. Design Principles

The scoring methodology follows four principles:

1. **Deduction-based, then inverted**: Internally start at 100, deduct for risk factors, then invert so 0=clean and 100=worst. The output scale matches industry convention (higher=higher risk): EPA HRS, RealPage, HUD NSPIRE.

2. **Government data only**: Scores are derived exclusively from authoritative government records (EPA ECHO, state environmental agencies). No self-reported data, no questionnaires, no proprietary signals.

3. **Transparent and reproducible**: Given the same input data, the score is deterministic. Every deduction traces to a specific data element.

4. **Conservative by default**: When data is ambiguous, the methodology favors caution. Records that cannot be confirmed as violations are filtered out rather than penalized. Violations without dates receive full weight — unknown age is not assumed to be old.

---

## 3. Data Sources

The CRS draws from three categories of government data:

### 3.1 Violation Records

Each violation record includes:
- **Violation type**: Program-specific classification (e.g., SNC, HPV, effluent exceedance, enforcement order type)
- **Violation date**: When the violation occurred or was detected
- **Severity**: Source-specific severity designation
- **Description**: Free-text or coded description
- **Resolution status**: Whether the violation is active, closed, resolved, or withdrawn

Sources include EPA ECHO (covering CWA, CAA, RCRA, SDWA programs), state environmental agencies (e.g., TCEQ for Texas, NJ DEP, CA State Water Board), and local regulatory databases.

### 3.2 Facility Metadata

- **NAICS codes**: North American Industry Classification System codes indicating industry sector
- **SIC codes**: Standard Industrial Classification codes (still assigned by EPA and many state sources, often more specific than NAICS for Phase I REC facility types)
- **Regulatory programs**: Which environmental programs regulate the facility (CWA, CAA, RCRA, SDWA, etc.)
- **Facility name**: Used for industry inference when classification codes are absent

### 3.3 Data Scope

As of March 2026, the database covers 7.1 million facilities across all 50 states and the District of Columbia, drawn from 65 source-program combinations. After cross-source entity resolution, these resolve to 3.9 million unique facilities. Coverage varies significantly by state and program; see Section 7 (Data Confidence) for how this affects score interpretation.

---

## 4. Scoring Algorithm

The CRS is computed in five layers, applied sequentially, followed by post-inversion score floors and caps that enforce minimum risk levels for contamination-designated sites.

### 4.1 Layer 1: Non-Violation Filtering

Government databases frequently contain records that look like violations but represent compliance confirmations. Before scoring, records are filtered if any of the violation type, severity, or description fields match known non-violation values:

> no, no violation, no violation identified, no high priority violation, na, none, not applicable, in compliance, compliant, resolved

This filtering prevents false penalties from compliance-confirmation records. The count before and after filtering is preserved as `raw_violation_count` and `violation_count` respectively.

### 4.2 Layer 2: Per-Violation Severity Deductions (with Temporal Decay and Resolution Discounting)

Each confirmed violation deducts points based on its severity classification, discounted by two factors: how old the violation is and whether it has been resolved.

#### 4.2.1 Severity Classification

| Severity Signal | Base Deduction | Rationale |
|----------------|----------------|-----------|
| Significant Noncompliance (SNC), Significant, or HPV-equivalent | -15 points | Most serious classification across all EPA programs |
| Significant enforcement actions (administrative orders with penalties, consent orders) | -15 points | State-level enforcement actions equivalent to SNC in severity |
| Standard violation, notice of violation, or affirmative compliance flag | -5 points | Confirmed violation without "significant" designation |
| Active compliance tracking indicator ("on", "yes") | -5 points | Facility is under active compliance monitoring |

**Severity signal detection**: The algorithm checks for the presence of keywords ("significant", "snc") in both the severity and violation_type fields. This cross-field approach handles the inconsistent field usage across different source programs. Additionally, specific state enforcement action types (e.g., NJ DEP administrative orders with civil administrative penalties) are classified at the appropriate severity level based on their legal significance.

**Standard violation cap**: Cumulative severity deductions from standard (non-significant) violations are capped at 40 points. Significant violations (SNC, HPV, enforcement orders) are not capped. This prevents a facility with many minor violations from scoring identically to one with serious enforcement actions.

#### 4.2.2 Temporal Decay

Each violation's deduction is multiplied by a decay factor based on its age, aligning with the ASTM E1527-21 distinction between current RECs and Historical RECs (HRECs):

| Violation Age | Decay Multiplier | Rationale |
|--------------|-----------------|-----------|
| 0-3 years | 1.00 (full weight) | Active compliance history |
| 3-5 years | 0.75 | Diminishing relevance; still within TCEQ 5-year window |
| 5-10 years | 0.50 | Historical; still a signal but significantly reduced |
| 10+ years | 0.25 | Heavily discounted; aligns with ASTM HREC treatment |
| No date | 1.00 (full weight) | Conservative — cannot discount what cannot be dated |

A facility with 195 violations from 2014 receives one-quarter the deduction of a facility with 195 violations from last month. Deductions are applied per violation, meaning a facility with multiple significant violations accumulates multiple deductions (each decay-adjusted).

#### 4.2.3 Resolution Status Discounting

Violations with a closed, resolved, or withdrawn status are discounted to 25% of their normal weight. This is applied as a multiplier alongside temporal decay, so a resolved violation from 8 years ago receives `0.50 (temporal) × 0.25 (resolution) = 0.125` of its base deduction.

Resolution statuses recognized include: closed, resolved, withdrawn, superseded, voided, completed, dismissed, rescinded, and addressed (at local, state, or federal level).

**Design rationale**: A facility that had environmental issues in the past but resolved them should score lower than one with identical violations still open. This aligns with the ASTM E1527-21 treatment of Historical RECs (HRECs) — resolved enforcement actions are noted in a Phase I but do not drive current risk assessment the way active violations do. The 0.25 multiplier was chosen to ensure resolved violations still contribute some signal (a facility with 50 resolved violations has a different risk profile than one with zero history) while heavily discounting their impact.

#### 4.2.4 Stormwater and Water Quality Violations

Routine stormwater permit and NPDES water discharge violations (monitoring and reporting violations, effluent limit exceedances, stormwater permit noncompliance) are scored separately from other violation types and capped at a maximum total contribution of 25 points.

Without this cap, a parking garage with 38 stormwater monitoring violations would score identically to a refinery with RCRA hazardous waste violations. Stormwater violations are a compliance signal, but they represent a fundamentally different risk category than hazardous waste, air quality, or contamination violations.

Non-stormwater violations (RCRA, CAA, cleanup orders, enforcement actions) are unaffected by this cap and use the full scoring rules. If a facility has both stormwater and non-stormwater violations, the two categories are computed separately; the stormwater contribution is capped before being added to the total.

### 4.3 Layer 3: Violation Volume Penalty (Temporally Weighted)

Independent of severity, the total count of confirmed violations incurs an additional deduction based on a temporally-weighted count:

```
weighted_count = sum(decay_weight × resolution_weight for each violation)
volume_penalty = min(weighted_count × 3, 40)
```

The decay and resolution weights are the same per-violation factors applied in Layer 2. This means a hundred decade-old resolved violations contribute far less volume penalty than a hundred recent active violations.

The cap prevents extreme outliers (facilities with hundreds of minor violations) from completely overwhelming other scoring factors. Stormwater and non-stormwater violations have separate volume penalties, each capped at 40, with the stormwater total subject to the 25-point stormwater cap.

**Design rationale**: The cap at ~13 effective violations reflects a judgment that beyond a certain volume, the severity and recency factors are more informative than the raw count. The temporal and resolution weighting ensures that historical violation accumulation at legacy industrial sites does not permanently mark those sites as critical risk when they may have been clean for a decade.

### 4.4 Layer 4: Recency Penalty

The most recent violation date triggers a time-decay penalty:

| Recency Window | Deduction | Rationale |
|---------------|-----------|-----------|
| Within 1 year | -15 points | Active or very recent compliance issues |
| 1-3 years ago | -8 points | Recent history, may indicate ongoing risk |
| 3-5 years ago | -3 points | Older issues, diminishing relevance |
| Over 5 years ago | 0 points | Outside the standard compliance review window |

This aligns with TCEQ's 5-year rolling window and reflects the environmental consulting convention that violations older than 5 years carry minimal weight in risk assessment. The penalty applies once (based on the most recent violation), not per violation.

### 4.5 Layer 5: Contextual Risk Adjustments

Three facility-level factors adjust the score independent of violation history.

#### 4.5.1 Industry Risk Classification

Facilities are classified into risk tiers using a multi-source approach that addresses the well-known data quality issues in government industry code assignments.

**Primary classification: NAICS codes**

| Tier | NAICS Sectors | Deduction | Examples |
|------|--------------|-----------|----------|
| 1 (High) | 21, 32, 56 | -15 points | Mining, Chemical/Petroleum manufacturing, Waste management |
| 2 (Moderate) | 22, 31, 33, 48, 49 | -10 points | Utilities, Food/Metal manufacturing, Transportation |
| 3 (Low) | 11, 23, 42, 44, 45 | -5 points | Agriculture, Construction, Wholesale/Retail trade |
| Unclassified | All others | 0 points | Service industries, Healthcare, Education |

When a facility has multiple NAICS codes, the highest-risk tier applies.

**Secondary classification: SIC codes**

EPA and state source data frequently assign SIC codes alongside or instead of NAICS codes. SIC codes are often more specific for Phase I REC facility types — for example, SIC 7216 (Dry Cleaning Plants) and SIC 5541 (Gasoline Stations) directly identify high-risk operations that may be obscured by broad NAICS categories.

The scoring engine evaluates both NAICS and SIC codes and applies whichever produces the higher risk deduction. SIC classification uses 4-digit exact match for specific facility types (gasoline stations, dry cleaning plants, scrap yards, auto repair shops) and 2-digit prefix match for broad industry families (mining, chemicals, petroleum refining, primary metals).

**Misclassification corrections**

EPA ECHO NAICS assignments are sometimes incorrect — a dental practice may be coded as Medical Instrument Manufacturing (NAICS 339114), or a state park visitor center may carry a restaurant code (NAICS 72210). The scoring engine uses facility name patterns to detect and correct obvious misclassifications:

- **Suppression**: Healthcare, dental, veterinary, and government facility names suppress Manufacturing or Food Service penalties that would be incorrect for those facility types.
- **Upgrade**: Facilities with heavy industrial names (cement plants, refineries, smelters) that are coded as Warehousing or Machinery Manufacturing are upgraded to the correct higher-risk tier.
- **Program-based upgrade**: Facilities enrolled in programs that indicate chemical handling (e.g., used oil processing, RCRA) but classified as low-risk retail are upgraded to reflect the actual operational risk.

**Fallback: Name-based industry inference**

When a facility has no NAICS or SIC codes at all (common in state-only source data), the scoring engine infers industry risk from the facility name. Facilities whose names indicate environmentally significant operations — dry cleaners, gas stations, auto repair shops, scrap yards, printing operations — receive the appropriate industry risk deduction. This prevents facilities with known environmental risk profiles from appearing as zero-risk simply because they lack classification codes.

**Design rationale**: Industry risk tiers are derived from EPA enforcement priority patterns and the relative frequency of environmental violations by sector. The multi-source approach (NAICS → SIC → program signals → name inference) ensures that industry risk is captured regardless of which classification system the source data uses. Phase I consultants intuitively expect a dry cleaner to score higher than a dentist office, even when both lack formal industry codes.

#### 4.5.2 Multi-Program Complexity

Facilities regulated under multiple environmental programs face compounding compliance obligations:

| Program Count | Deduction | Rationale |
|--------------|-----------|-----------|
| 4+ programs | -12 points | Highest complexity; simultaneous CWA + CAA + RCRA + SDWA obligations |
| 3 programs | -8 points | Significant multi-program compliance burden |
| 2 programs | -5 points | Moderate overlap |
| 0-1 programs | 0 points | Single-program or unclassified |

This factor is supported by the UChicago/EPA machine learning study (Greenstone et al.), which demonstrated that cross-program violation data is predictive: facilities regulated under multiple programs have statistically higher violation rates, and violations in one program predict violations in others.

#### 4.5.3 Contamination Program Designation

Facilities listed in government contamination tracking programs carry inherent risk regardless of their current violation records. A site on the National Priorities List has confirmed contamination even if no recent violations have been filed.

The deduction is based on the most severe program designation (does not stack):

| Program Designation | Deduction | Source |
|-------------------|-----------|--------|
| Federal Superfund / NPL (currently listed) | -30 points | EPA SEMS: `INTEREST_TYPE = "SUPERFUND NPL"`, `ACTIVE_STATUS = "CURRENTLY ON THE FINAL NPL"` |
| VCP / BCP / Mandatory Corrective Action | -30 points | State voluntary cleanup programs, brownfield cleanup programs (NY BCP), mandatory corrective action (TCEQ IHWCA). Enrollment requires documented contamination. |
| Superfund (delisted or generic) | -25 points | EPA SEMS: NPL-listed sites that have been deleted from the Final NPL (cleanup completed) |
| SEMS / CERCLIS (non-NPL) | -20 points | EPA SEMS: sites screened but not priority-listed (`INTEREST_TYPE = "SUPERFUND (NON-NPL)"`) |
| State Response / Known Contamination | -20 points | State agency programs (e.g., CA DTSC State Response, NJ KCSL Known Contaminated Sites) |
| LUST / RCRA Corrective Action / Remediation | -15 points | Leaking underground storage tanks, RCRA corrective action, active remediation programs, PFAS contamination |
| Brownfield / Investigation / UST | -10 points | Brownfield redevelopment sites, active contamination investigations, underground storage tank registration |

**Data source for Superfund**: EPA's Superfund Enterprise Management System (SEMS), the successor to CERCLIS. SEMS data is sourced from the EPA FRS INTERESTS ArcGIS service, which provides site inventory, NPL status, and active status for approximately 14,800 sites nationally. SEMS sites share EPA FRS Registry IDs with ECHO/RCRA/CAA/SDWA records, enabling automatic cross-source entity resolution.

**ASTM E1527-21 relevance**: Phase I Environmental Site Assessments require searching federal and state environmental databases for sites within specified search distances. The NPL and CERCLIS/SEMS databases are among the mandatory federal databases listed in the standard. The library's SEMS connector directly supports this requirement.

### 4.6 Score Bounds and Risk Classification

The final score is clamped to [0, 100] and then inverted:

```
score = max(0, min(100, round(score)))
score = 100 - score  # Invert: 0=clean, 100=worst
```

Risk levels are assigned based on score thresholds:

| Score Range | Risk Level | Interpretation |
|------------|------------|----------------|
| 0-25 | Low | Clean or near-clean compliance history |
| 26-49 | Medium | Some compliance issues; warrants review |
| 50-74 | High | Significant compliance concerns; detailed assessment recommended |
| 75-100 | Critical | Severe compliance failures or confirmed contamination |

### 4.7 Post-Inversion Score Floors and Caps

After the score is inverted and risk levels are assigned, two types of adjustments enforce domain-specific constraints that override the purely additive scoring:

#### 4.7.1 Contamination Program Score Floors

Certain program designations establish minimum scores regardless of violation history. A Superfund site must always appear as high-risk even if it has zero violation records in ECHO — CERCLA enforcement does not produce "violations" in the same databases that track CWA/CAA/RCRA compliance.

| Program | Minimum Score | Risk Level | Rationale |
|---------|--------------|------------|-----------|
| NPL (National Priorities List) | 70 | High | Active NPL listing is near-automatic maximum risk. CERCLA enforcement produces cleanup orders, not ECHO violations. |
| SEMS / CERCLIS / KCSL / State Response | 60 | High | Confirmed or actively investigated contamination at federal or state level. |
| RCRA TSDF (Treatment, Storage, Disposal) | 60 | High | Part B permit holders under 40 CFR Parts 264/265 — the most stringent RCRA tier. A TSDF within search radius constitutes a REC under ASTM E1527-21. |
| Mandatory Corrective Action (IHWCA) | 55 | High | State-ordered cleanup of documented hazardous waste releases. Stricter than voluntary cleanup. |
| VCP / BCP (Voluntary Cleanup Programs) | 50 | High | Enrollment requires documented contamination. Qualifies as REC or Controlled REC under ASTM E1527-21. |

These floors are applied in priority order. If a site is both NPL-listed and enrolled in a VCP, the NPL floor (70) applies.

**Design rationale**: Environmental consultants expect Superfund sites, RCRA TSDFs, and known contaminated sites to always display as High or Critical risk. A Superfund site scoring "Low" because it has no ECHO violations would be a credibility-destroying result. The floor values are calibrated so that NPL > SEMS/TSDF > Corrective Action > VCP, reflecting the severity hierarchy that Phase I practitioners use.

#### 4.7.2 Residential Score Cap

Residential buildings (apartment complexes, condominiums, housing authorities) that are registered with state and federal agencies for boiler or generator air permits frequently receive administrative enforcement actions for permit paperwork violations. Without a cap, a 200-unit apartment building with 12 permit administrative violations could score Critical — identical to a refinery with RCRA violations.

Facilities identified as residential-use (by NAICS code or facility name) are capped at a maximum score of 49 (Medium risk). This cap is overridden if the facility is flagged by any contamination program (NPL, SEMS, KCSL, VCP, TSDF, corrective action) — contamination matters regardless of building type.

---

## 5. Worked Examples

### 5.1 Low-Risk Facility

A retail store (NAICS 44) regulated under one program, with no violation history.

| Factor | Deduction |
|--------|-----------|
| Base score | 100 |
| Violations (0) | 0 |
| Volume penalty (0 × 3) | 0 |
| Recency (no violations) | 0 |
| Industry (Tier 3, retail) | -5 |
| Program complexity (1 program) | 0 |
| Pre-inversion score | 95 |
| **Final score (100 - 95)** | **5 (Low)** |

### 5.2 Medium-Risk Facility

A food manufacturer (NAICS 31) regulated under CWA and RCRA, with 3 standard violations in the last 2 years.

| Factor | Deduction |
|--------|-----------|
| Base score | 100 |
| 3 standard violations (3 × -5) | -15 |
| Volume penalty (3 × 3) | -9 |
| Recency (within 1 year) | -15 |
| Industry (Tier 2, manufacturing) | -10 |
| Program complexity (2 programs) | -5 |
| Pre-inversion score | 46 |
| **Final score (100 - 46)** | **54 (High)** |

Note: Even a "medium-looking" violation profile can produce a High risk score when industry and program factors compound.

### 5.3 High-Risk Facility

A chemical plant (NAICS 32) regulated under CWA, CAA, RCRA, and SDWA, with 2 SNC violations and 5 standard violations in the last year.

| Factor | Deduction |
|--------|-----------|
| Base score | 100 |
| 2 SNC violations (2 × -15) | -30 |
| 5 standard violations (5 × -5) | -25 |
| Volume penalty (min(7 × 3, 40)) | -21 |
| Recency (within 1 year) | -15 |
| Industry (Tier 1, chemical) | -15 |
| Program complexity (4 programs) | -12 |
| Pre-inversion score | 0 (clamped from -18) |
| **Final score (100 - 0)** | **100 (Critical)** |

### 5.4 Resolved Violations — Score Improvement

A metal fabrication shop (NAICS 33) that had 4 significant violations from 2020, all now resolved/closed.

| Factor | Deduction |
|--------|-----------|
| Base score | 100 |
| 4 SNC violations: base -15 each | — |
| × temporal decay (5-10 years): 0.50 | — |
| × resolution discount (closed): 0.25 | — |
| Effective per-violation: -15 × 0.50 × 0.25 = **-1.875 each** | -7.5 |
| Volume penalty: 4 × (0.50 × 0.25) × 3 = 1.5 | -1.5 |
| Recency (over 5 years ago) | 0 |
| Industry (Tier 2, manufacturing) | -10 |
| Program complexity (2 programs) | -5 |
| Pre-inversion score | 76 |
| **Final score (100 - 76)** | **24 (Low)** |

Without resolution discounting, this facility would score 54 (High). The resolution discount reflects that the violations were addressed — a facility that cleaned up its act should not carry the same risk as one with identical violations still open.

### 5.5 Contamination Score Floor — Superfund Site

A former industrial site (NAICS 56) on the National Priorities List, with 0 violation records in ECHO.

| Factor | Deduction |
|--------|-----------|
| Base score | 100 |
| Violations (0) | 0 |
| Volume penalty (0) | 0 |
| Recency (no violations) | 0 |
| Industry (Tier 1, waste management) | -15 |
| Program complexity (1 program) | 0 |
| Contamination designation (NPL, -30) | -30 |
| Pre-inversion score | 55 |
| Inverted: 100 - 55 | 45 |
| **NPL floor applied: max(45, 70)** | **70 (High)** |

Without the NPL score floor, this site would score 45 (Medium) — a misleading result for an active Superfund site. The floor ensures that NPL sites always appear as High or Critical risk, matching the expectation of any Phase I practitioner.

---

## 6. Comparison to Existing Approaches

| Element | CRS | TCEQ (Texas) | CA DTSC VSP | Restaurant Grades | HUD NSPIRE |
|---------|-------------|-------------|-------------|-------------------|------------|
| Scale | 0-100 | 0 to unbounded | 0 to unbounded | 0-100 | 0-100 |
| Direction | 0 = best (100 = worst) | 0 = best | 0 = best | 100 = best | 100 = best |
| Method | Deduction from 100 | Point accumulation | Point accumulation | Deduction from 100 | Deduction from 100 |
| Scope | All programs, all states | TCEQ-regulated sites in TX | ~78 hazardous waste facilities in CA | Individual restaurants | HUD-assisted housing |
| Industry adjustment | Yes (NAICS + SIC + name inference) | Yes (complexity points) | No | No | No |
| Cross-program | Yes | No (TCEQ only) | No (RCRA only) | N/A | N/A |
| Resolution discounting | Yes (0.25× for resolved) | No | No | N/A | N/A |
| Recency decay | Per-violation temporal decay + recency penalty | 5-year rolling window | 10-year window | Per inspection | Per inspection |
| Contamination floors | Yes (NPL, SEMS, VCP, TSDF) | No | No | N/A | N/A |
| Public methodology | This document | 30 TAC Chapter 60 | VSP Guidance Document | Varies by jurisdiction | Federal Register |

---

## 7. Data Confidence

A score is only as reliable as the data behind it. Because violation data coverage varies significantly across sources and states, the CRS reports alongside each score:

- **`violation_count`**: Number of confirmed violations used in scoring
- **`raw_violation_count`**: Total records before non-violation filtering (indicates data noise)
- **`latest_violation_date`**: When the most recent violation occurred (indicates data freshness)
- **`program_count`**: Number of regulatory programs tracking this facility

### 7.1 Known Coverage Gaps

As of March 2026:
- **Many state sources provide facility registration data but not violation histories.** A score of 0 from a source with no violation data reflects absence of evidence, not evidence of absence. Sources with violation data include EPA ECHO programs, CA State Water Board (439K violations), NJ DEP (88K), VA DEQ (84K), TCEQ (55K), and 28 other state sources.
- **States vary widely.** Texas, Virginia, California, and New Jersey have the richest violation data. Coverage depth depends on both federal EPA data (available for all states) and state-level data (available where state sources are integrated).
- **Inspection history is not yet incorporated.** A facility that was inspected last month and found clean is meaningfully different from one that hasn't been inspected in 5 years. Adding inspection dates is a planned enhancement.

### 7.2 Interpreting Scores Given Coverage

| Scenario | What the score means |
|----------|---------------------|
| Score > 25 (Medium+), violation_count > 0 | High confidence: concrete violation data supports the risk assessment |
| Score 0-25 (Low), covered by EPA source, violation_count = 0 | Moderate confidence: source tracks violations for other facilities — this one was checked and appears clean |
| Score 0-25 (Low), only in state source with 0 total violations | Low confidence: never effectively checked — score reflects absence of data |

Users should treat low scores with low violation counts as "no data available" rather than "confirmed clean."

### 7.3 Confidence Level

Every score includes a source-aware confidence level, returned in API responses as the `confidence` field. The key insight: a facility with 0 violations in EPA ECHO (which has violation data for tens of thousands of other facilities) is meaningfully different from a facility with 0 violations in a state source that has 0 violations across all its facilities. The former was likely checked; the latter was never effectively checked.

A source must have at least 100 violation records to qualify as "violation-tracking." This prevents a handful of stray records from inflating confidence across an entire source's facility population.

| Level | Criteria | Meaning |
|-------|----------|---------|
| **high** | `violation_count > 0` | Concrete violation data supports the risk assessment |
| **moderate** | No violations, but covered by a source that HAS violation data for other facilities; OR records exist but all were filtered as non-violations | Checked and appears clean, or compliance-confirmation records exist |
| **low** | Only covered by sources that have zero violation data across all their facilities | Never effectively checked — score reflects absence of data, not absence of violations |

The confidence level is available via the REST API (`/api/v1/unified/facilities/{source_id}`, `/api/v1/reports/screening`) and MCP server (all tools that return facility data). Use `/api/v1/coverage` to see data availability at the state/source level.

---

## 8. Limitations and Future Work

### 8.1 Current Limitations

1. **No positive factors.** The CRS only deducts; it cannot reward voluntary audits, pollution prevention programs, or environmental management system certifications. TCEQ's system credits these; ours does not yet.

2. **Linear volume penalty with hard cap.** The 40-point cap means a facility with 14 violations and one with 200 violations receive the same volume penalty. More nuanced volume scaling is under evaluation.

3. **No trend analysis.** The CRS is a point-in-time snapshot. A facility whose violation rate is declining may appear riskier than one whose rate is increasing, if the declining facility had more violations historically.

4. **No penalty interaction.** All deductions are additive. The CERCLA Hazard Ranking System uses multiplicative factors (likelihood × severity × targets) combined via root-mean-square. Whether multiplicative interaction would improve the CRS is an open question.

### 8.2 Planned Enhancements

- **Inspection history integration**: Track last-inspected dates to distinguish "inspected and clean" from "never inspected."
- **Trend indicators**: Is the facility getting better or worse? Compute score deltas across scoring runs.
- **Peer comparison**: Score relative to industry and regional peers, following the MSCI industry-relative model.
- **Positive factors**: Credit for voluntary disclosures, environmental management systems, and sustained clean inspection history.

---

## 9. Score Distribution (March 2026)

Across 3,927,534 scored unified facilities:

| Risk Level | Count | Percentage | Average Score |
|-----------|-------|------------|---------------|
| Low (0-25) | 3,734,067 | 95.1% | 6.0 |
| Medium (26-49) | 143,804 | 3.7% | 33.2 |
| High (50-74) | 46,299 | 1.2% | 58.7 |
| Critical (75-100) | 3,364 | 0.1% | 91.4 |

**Confidence distribution** (source-aware, see Section 7.3):

| Confidence | Count | Percentage |
|-----------|-------|------------|
| High | 355,033 | 9.0% |
| Moderate | 3,142,140 | 80.0% |
| Low | 430,361 | 11.0% |

The score distribution reflects calibration improvements introduced since February 2026, including temporal decay, resolution status discounting, and stormwater violation caps. These changes moved a significant number of facilities from Medium to Low risk — not because violations were removed, but because the scoring engine now properly differentiates between active risk and historical compliance records that have been resolved. The 9.0% of facilities with "high" confidence have direct violation records; the 80% at "moderate" have been checked by a violation-tracking source and appear clean.

States with the richest violation data show a wider score distribution. Texas, with the most comprehensive state-level data (TCEQ + EPA), and California, with extensive water board violation data, provide the most granular risk differentiation.

---

## 10. API Access

Compliance Risk Scores are available through the the bundled reference REST API.

**Facility detail** returns the score alongside facility metadata:
```
GET /api/v1/facilities/{source}/{source_id}
```

**Facility listing** supports filtering by risk level:
```
GET /api/v1/facilities?state=TX&risk_level=high
```

**Score fields returned**:
```json
{
  "score": 58,
  "risk_level": "high",
  "confidence": "high",
  "violation_count": 8,
  "raw_violation_count": 12,
  "latest_violation_date": "2025-09-14",
  "naics_tier": 1,
  "program_count": 3,
  "scored_at": "2026-02-16T04:30:00"
}
```

**Transparency endpoints**:
```
GET /api/v1/methodology        # This document (machine-readable)
GET /api/v1/coverage           # Per-state, per-source data coverage matrix
GET /api/v1/coverage?state=TX  # Coverage for a single state
```

---

## Appendix A: Precedent Scoring Systems

This methodology was informed by analysis of existing compliance and risk scoring systems:

- **EPA ECHO**: Categorical designations (SNC, HPV) — no numerical score
- **TCEQ Compliance History (Texas)**: Numerical rating based on 5-year violation history with complexity weighting (30 TAC Chapter 60)
- **CA DTSC Violations Scoring Procedure**: Deduction-based scoring for hazardous waste facilities using harm/deviation matrix
- **HUD NSPIRE**: 0-100 deduction-based housing inspection scores (Federal Register 2023-14362)
- **Restaurant Food Safety Grades**: Start at 100, deduct for violations; measurably reduced foodborne illness (NYC 2010 mandate study)
- **CERCLA Hazard Ranking System**: 0-100 score using multiplicative pathway scoring (40 CFR Appendix A to Part 300)
- **FICO Credit Score**: Industry standard adoption path — prove predictive value, publish methodology, gain practitioner adoption, then regulatory reference
- **UChicago/EPA ML Study** (Greenstone et al.): Demonstrated 79% improvement in violation detection using cross-program data signals

Full citations available in [COMPLIANCE_SCORING_RESEARCH.md](research/COMPLIANCE_SCORING_RESEARCH.md).

---

## Appendix B: NJ DEP Program Codes

New Jersey Department of Environmental Protection data comes from two source layers: the **Known Contaminated Sites List (KCSL)** and the **NJ Environmental Management System (NJEMS)** site registry. Both use state-specific codes that are not self-explanatory.

### B.1 NJ DEP Program Identifiers

| Code | Full Name | Significance |
|------|-----------|-------------|
| KCSL | Known Contaminated Site List (NJ DEP) | State's official list of known contaminated sites; a confirmed REC for Phase I assessments |
| NJEMS | NJ Environmental Management System Site Registry | NJ DEP's cross-program master site registry; includes any site regulated by NJ DEP |

### B.2 NJ DEP KCSL Status Codes

KCSL sites carry a STATUS field indicating the current remediation status. These appear in the Programs field alongside the KCSL code.

| Status | Meaning |
|--------|---------|
| Active | Site is currently under active remediation oversight |
| Active - UHOT | Active — Underground Heating Oil Tank (site involves a heating oil tank) |
| Active - Post Rem | Active — Post Remediation (cleanup complete, monitoring in progress) |
| Active - MOA | Active — Memorandum of Agreement (formal cleanup agreement in place) |
| Active - NFA | Active — No Further Action (remediation concluded; NFA determination issued) |
| Active - Closed | Active — Closed (case administratively closed) |
| Pending | Pending review or initial investigation; not yet in active remediation |

**Phase I relevance**: Any site on the KCSL is a Recognized Environmental Condition (REC) under ASTM E1527-21. The STATUS code provides additional context on the remediation stage, but all KCSL sites warrant disclosure and further inquiry regardless of status.

---

## Appendix C: Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-02 | Initial methodology publication |
| 1.1 | 2026-02 | Added NJ DEP program code glossary (Appendix B) |
| 1.2 | 2026-03 | Added temporal decay to Layers 2 and 3. Per-violation deductions and volume penalty weighted by violation age: 0-3yr=1.0, 3-5yr=0.75, 5-10yr=0.5, 10+yr=0.25. Aligns with ASTM E1527-21 HREC/REC distinction. |
| 2.0 | 2026-03 | Major revision. Added: resolution status discounting (0.25× for closed/resolved violations), stormwater violation cap (25 points max), SIC code risk classification, name-based industry inference, NAICS misclassification corrections, contamination program score floors (NPL 70, SEMS/TSDF 60, corrective action 55, VCP 50), residential score cap, significant enforcement action types. Updated data scope (7.1M facilities, 65 sources) and score distribution. Added worked examples for resolved violations and score floors. |
