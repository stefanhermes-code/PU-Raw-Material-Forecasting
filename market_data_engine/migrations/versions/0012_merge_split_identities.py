"""merge the identities that 0011 split in two (v0.4.71)

Migration 0011 backfilled `market_identity.origin` for the live collection path. But `app/bootstrap.py`
carries its OWN identity upsert, and it still searched for `origin = None`. On the first v0.4.70 start
the bootstrap therefore matched nothing, minted a duplicate identity for every governed series, and
inserted its 33 observations against the duplicates - splitting each series' history away from its new
rows. The visible symptom was the Current Market reference for TDI China EXW falling to a single
source and reporting "limited corroboration" despite four independent publishers.

This repairs the data. The cause is fixed in `app/bootstrap.py`, which now derives origin before the
lookup exactly as the live path does.

For every identity whose origin is NULL:
  * derive what it should have been;
  * if a twin already carries that derived origin, move this one's observations and reference points
    onto the twin - skipping any observation the twin already holds - then delete it;
  * otherwise simply set the origin, since it is the only row for that series.
"""
from alembic import op
import sqlalchemy as sa

revision = "0012_merge_split_identities"
down_revision = "0011_origin_attribution"
branch_labels = None
depends_on = None

_MATCH = (
    "product_family", "product_grade_or_chemistry", "market_geography",
    "commercial_basis", "packaging", "currency", "unit", "observation_type",
)


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "market_identity" not in tables:
        return

    from app.domain.origin import derive

    rows = bind.execute(sa.text("SELECT * FROM market_identity")).mappings().all()
    by_signature: dict[tuple, list[dict]] = {}
    for row in rows:
        by_signature.setdefault(tuple(row[key] for key in _MATCH), []).append(dict(row))

    merged = 0
    for signature, group in by_signature.items():
        orphans = [r for r in group if not r["origin"]]
        if not orphans:
            continue
        derived_origin, derived_destination = derive(signature[2], signature[3])
        canonical = next((r for r in group if r["origin"] == derived_origin), None)

        for orphan in orphans:
            if canonical is None or canonical["id"] == orphan["id"]:
                bind.execute(
                    sa.text("UPDATE market_identity SET origin = :o, destination = :d WHERE id = :id"),
                    {"o": derived_origin, "d": derived_destination, "id": orphan["id"]},
                )
                canonical = {**orphan, "origin": derived_origin}
                continue

            if "price_observation" in tables:
                # Move only what the canonical does not already hold; the rest are the duplicates
                # this bug created and are dropped.
                bind.execute(sa.text("""
                    DELETE FROM price_observation
                     WHERE identity_id = :orphan
                       AND EXISTS (SELECT 1 FROM price_observation keep
                                    WHERE keep.identity_id = :canonical
                                      AND keep.source_id = price_observation.source_id
                                      AND keep.observation_date = price_observation.observation_date
                                      AND keep.price = price_observation.price)
                """), {"orphan": orphan["id"], "canonical": canonical["id"]})
                bind.execute(
                    sa.text("UPDATE price_observation SET identity_id = :canonical WHERE identity_id = :orphan"),
                    {"canonical": canonical["id"], "orphan": orphan["id"]},
                )
            if "market_reference_point" in tables:
                # The canonical's own reference points are the ones computed over the full history.
                bind.execute(sa.text("DELETE FROM market_reference_point WHERE identity_id = :orphan"),
                             {"orphan": orphan["id"]})
            bind.execute(sa.text("DELETE FROM market_identity WHERE id = :id"), {"id": orphan["id"]})
            merged += 1

    if merged:
        print(f"0012_merge_split_identities: merged {merged} duplicate identities")


def downgrade():
    pass
