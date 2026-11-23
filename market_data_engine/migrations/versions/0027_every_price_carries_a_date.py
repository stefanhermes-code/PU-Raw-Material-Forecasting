"""Every stored price carries an observation date (v0.4.155)

One SUNSIRS reading for 1 September 2026 was stored with no observation date at all, beside the
properly dated reading of the same price from the same publisher. Two faults let it in and one more
let it do damage:

  * the loader took `observation_date` straight from the connector and wrote whatever came back,
    including nothing;
  * its duplicate check compares `observation_date = :observation_date`, and in SQL NULL is never
    equal to NULL, so an undated row can never match an existing one and re-inserts on every run;
  * the consolidator falls back to the publication date when a row has no observation date, so the
    undated twin still voted - while the repair pass, which reads `observation_date` directly,
    treated the resulting point as blended and rewrote it.

The two writers then rewrote each other without end. September's market price alternated between
16,597.11 and 14,687.71 - the same three readings, once with China's VAT inside and once without -
through four versions in three seconds, and whichever landed last was what the banner showed.

This gives the undated row the date its publisher printed it on, drops it where that makes it a
duplicate of a row already there, and clears the contested points so they are rebuilt once from a
settled set of readings.
"""
from alembic import op
import sqlalchemy as sa

revision = "0027_every_price_carries_a_date"
down_revision = "0026_one_china_tdi_exw_series"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "price_observation" not in tables:
        return

    undated = bind.execute(sa.text(
        "SELECT id, identity_id, source_id, price, publication_date, available_from "
        "FROM price_observation WHERE observation_date IS NULL"
    )).mappings().all()
    print(f"[0027] undated price observations: {len(undated)}")

    dated = dropped = orphaned = 0
    touched: set[str] = set()
    for row in undated:
        stamp = row["publication_date"] or row["available_from"]
        if stamp is None:
            # A price whose date nobody recorded is not a point on a time series. It is dropped
            # rather than parked on a guessed day.
            bind.execute(sa.text("DELETE FROM price_observation WHERE id = :id"), {"id": row["id"]})
            orphaned += 1
            touched.add(row["identity_id"])
            continue
        day = str(stamp)[:10]
        twin = bind.execute(sa.text(
            "SELECT id FROM price_observation "
            "WHERE identity_id = :identity AND source_id = :source AND price = :price "
            "AND observation_date IS NOT NULL AND substr(observation_date, 1, 10) = :day "
            "AND id <> :id LIMIT 1"
        ), {"identity": row["identity_id"], "source": row["source_id"], "price": row["price"],
            "day": day, "id": row["id"]}).first()
        if twin is not None:
            bind.execute(sa.text("DELETE FROM price_observation WHERE id = :id"), {"id": row["id"]})
            dropped += 1
        else:
            bind.execute(sa.text(
                "UPDATE price_observation SET observation_date = :stamp WHERE id = :id"
            ), {"stamp": str(stamp), "id": row["id"]})
            dated += 1
        touched.add(row["identity_id"])

    print(f"[0027] dated from the publisher's own date: {dated}")
    print(f"[0027] dropped as duplicates of a dated row: {dropped}")
    print(f"[0027] dropped with no date of any kind: {orphaned}")

    if touched and "market_reference_point" in tables:
        cleared = 0
        for identity_id in touched:
            result = bind.execute(sa.text(
                "DELETE FROM market_reference_point WHERE identity_id = :identity"
            ), {"identity": identity_id})
            cleared += int(result.rowcount or 0)
        print(f"[0027] consolidated points cleared for rebuild: {cleared}")


def downgrade():
    # A date that was recovered is not put back to nothing, and a row that was a duplicate is not
    # recreated. There is no state here worth restoring.
    pass
