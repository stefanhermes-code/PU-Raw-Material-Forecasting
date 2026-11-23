"""retire the hand-seeded KGI purchase history superseded by the governed import (v0.4.78)

v0.4.77 added `app/history/transactions.py`, which loads HTC's own purchase records from
`data/research/historical_rm_native.csv` and registers them under `kgi_thailand` and
`foampartner_china` as `transaction_price` observations.

Three of KGI's series were already in the store: 61 observations hand-seeded into
`bootstrap_observations.json` against `htc_internal_tdi_wanhua`, `htc_internal_tdi_basf` and
`htc_internal_polyol`, typed `actual_purchase`. They are the same trades read from the same
workbooks - BASF TDI at 93.49 THB/kg in March 2022 appears in both - so leaving them in place would
have counted every KGI month twice under two source ids and two observation types, and any
correlation fitted on that history would have been fitted on doubled evidence.

The governed import wins: it carries the supplier in the identity, it is idempotent, and it is
reproducible from the workbook. This removes the seeded copies and the three superseded source
definitions (also dropped from `app/sources/registry.py`, so they are not re-created on startup).
"""
from alembic import op
import sqlalchemy as sa

revision = "0013_retire_bootstrap_kgi_history"
down_revision = "0012_merge_split_identities"
branch_labels = None
depends_on = None

RETIRED = ("htc_internal_tdi_wanhua", "htc_internal_tdi_basf", "htc_internal_polyol")


def upgrade() -> None:
    bind = op.get_bind()
    placeholders = ", ".join(f":s{i}" for i in range(len(RETIRED)))
    params = {f"s{i}": value for i, value in enumerate(RETIRED)}

    # `price_observation.source_id`, `raw_evidence.source_id` and `ingestion_run.source_id` all carry
    # the natural key ("kgi_thailand"), not `source_registry.id`. Joining on the uuid finds nothing
    # and silently deletes nothing, which is how this migration first passed while changing no rows.
    identity_ids = [
        row[0] for row in bind.execute(
            sa.text(f"SELECT DISTINCT identity_id FROM price_observation WHERE source_id IN ({placeholders})"),
            params,
        )
    ]
    bind.execute(
        sa.text(f"DELETE FROM price_observation WHERE source_id IN ({placeholders})"), params
    )

    # Only drop an identity that nothing else points at: these three series were seeded alone, but a
    # later import could legitimately have attached to the same identity.
    for identity_id in identity_ids:
        remaining = bind.execute(
            sa.text("SELECT COUNT(*) FROM price_observation WHERE identity_id = :identity_id"),
            {"identity_id": identity_id},
        ).scalar_one()
        if remaining:
            continue
        bind.execute(
            sa.text("DELETE FROM market_reference_point WHERE identity_id = :identity_id"),
            {"identity_id": identity_id},
        )
        bind.execute(
            sa.text("DELETE FROM market_identity WHERE id = :identity_id"), {"identity_id": identity_id}
        )

    # Retire the registry rows, but only where nothing else points at them. A row that still carries
    # collection history is deactivated rather than deleted, so no reference is left dangling.
    for key in RETIRED:
        referenced = bind.execute(
            sa.text(
                "SELECT (SELECT COUNT(*) FROM raw_evidence WHERE source_id = :key)"
                " + (SELECT COUNT(*) FROM ingestion_run WHERE source_id = :key)"
            ),
            {"key": key},
        ).scalar_one()
        if referenced:
            bind.execute(
                sa.text("UPDATE source_registry SET active = 0, notes = :note WHERE source_id = :key"),
                {"key": key, "note": "Retired in v0.4.78: superseded by the governed kgi_thailand import."},
            )
        else:
            bind.execute(
                sa.text("DELETE FROM source_registry WHERE source_id = :key"), {"key": key}
            )


def downgrade() -> None:
    # The seeded observations are gone from bootstrap_observations.json, so there is nothing to
    # restore them from. Downgrading leaves the governed import as the only KGI history.
    pass
