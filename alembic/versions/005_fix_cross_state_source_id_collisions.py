"""Fix cross-state source_id collisions in facility_matches

Revision ID: 005_fix_cross_state_collisions
Revises: 004_normalize_county_names
Create Date: 2026-03-05

Root cause (CIV-459): Multiple state DEQ sources use the same source_id
naming conventions (brownfield-NNN, ust-NNN, sw-NNN, air-NNN, etc.) with
overlapping numeric IDs. Because upsert_facility() used bare source_id as
the initial canonical_id (not source/source_id), different state sources
with the same source_id were silently merged into a single canonical entity.

Examples of bad merges:
  - id_deq/brownfield-108, ky_dep/brownfield-108, nm_nmed/brownfield-108
    merged to canonical brownfield-108 (Idaho, Kentucky, and NM brownfields)
  - ri_dem/sw-1, ak_dec/sw-1, ks_kdhe/sw-1, ma_dep/sw-1, nh_des/sw-1
    merged to canonical sw-1 (solid waste sites in 5 different states)

Fix: For any non-EPA facility_matches row whose canonical_id is shared by
facilities from multiple different state sources, update canonical_id to
source || '/' || source_id to make it globally unique.

Also clears affected rows from unified_facilities and facility_scores so
they are cleanly rebuilt by the next rebuild-unified + score-all run.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "005_fix_cross_state_collisions"
down_revision: Union[str, None] = "004_normalize_county_names"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_EPA_SOURCES = (
    "epa_echo",
    "epa_rcra",
    "epa_caa",
    "epa_sdwa",
    "epa_sems",
)


def upgrade() -> None:
    # Step 1: Find canonical_ids shared by multiple non-EPA state sources.
    # These represent bad cross-state merges.
    #
    # A canonical_id is "bad" when the same source_id value was used by
    # facilities from two or more different state sources (e.g. id_deq and
    # ky_dep both have a facility with source_id='brownfield-108').

    epa_placeholders = ", ".join(f"'{s}'" for s in _EPA_SOURCES)

    # Step 2: Update facility_matches to use source-qualified canonical_ids.
    # Only touch rows where:
    #   a) The source is a non-EPA state source
    #   b) The current canonical_id (= bare source_id) is shared by 2+ different
    #      non-EPA sources in facility_matches
    #
    # Use "source || '/' || source_id" as the new canonical_id so that e.g.
    # (id_deq, brownfield-108) gets canonical_id "id_deq/brownfield-108" and
    # (ky_dep, brownfield-108) gets "ky_dep/brownfield-108".
    op.execute(
        f"""
        UPDATE facility_matches
        SET canonical_id = source || '/' || source_id
        WHERE source NOT IN ({epa_placeholders})
        AND canonical_id IN (
            SELECT canonical_id
            FROM facility_matches
            WHERE source NOT IN ({epa_placeholders})
            GROUP BY canonical_id
            HAVING COUNT(DISTINCT source) > 1
        )
        """
    )

    # Step 3: Remove unified_facilities rows whose source_id was one of the
    # bad canonical_ids.  These will be rebuilt by rebuild-unified.
    # After step 2, the old bad canonical_ids no longer exist in
    # facility_matches, so unified_facilities rows referencing them are stale.
    # The new canonical_ids (source/source_id format) will be picked up by
    # the next rebuild-unified run.
    #
    # Note: we can't easily enumerate the old bad canonical_ids in SQL after
    # the update, so we use a different approach: delete unified_facilities
    # rows that have multiple state-source entries in their sources column
    # where those sources are from different states.  The simplest safe
    # approach is to delete any unified_facilities row whose source_id no
    # longer exists in facility_matches as a canonical_id.
    op.execute(
        """
        DELETE FROM unified_facilities
        WHERE source_id NOT IN (
            SELECT DISTINCT canonical_id FROM facility_matches
        )
        """
    )

    # Step 4: Remove facility_scores rows whose source_id was one of the
    # bad canonical_ids.  Same logic as Step 3.
    op.execute(
        """
        DELETE FROM facility_scores
        WHERE source_id NOT IN (
            SELECT DISTINCT canonical_id FROM facility_matches
        )
        """
    )


def downgrade() -> None:
    # Cannot safely reverse: we'd need to know which rows were changed.
    # The original bad state (cross-state merges) should not be restored.
    pass
