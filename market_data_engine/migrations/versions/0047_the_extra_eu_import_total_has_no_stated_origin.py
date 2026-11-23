"""The extra-EU import total has no stated origin, one spelling for every material (v0.5.10)

v0.5.9 (13 October 2026) wrote the EU polyether polyol extra-EU import series with origin
'Extra-EU27'. The five Comext feedstocks collected since 1 October write the same kind of series with
origin NULL - "not stated", the one spelling migration 0046 settled. The trade layer of v0.5.10
writes every extra-EU import total the NULL way, so a store that ran v0.5.9 would otherwise hold the
polyol total under two identities.

This migration moves the 'Extra-EU27' total onto the NULL identity: renamed in place where no NULL
identity exists, folded into it where one does. Prices are not changed. Nothing else is touched:
'Extra-EU27 excluding China' is the derived residual and keeps its own identity.
"""
from alembic import op
import sqlalchemy as sa

revision = "0047_the_extra_eu_import_total_has_no_stated_origin"
down_revision = "0046_one_identity_per_market_and_one_reading_per_day"
branch_labels = None
depends_on = None

MATCH = ("product_family", "product_grade_or_chemistry", "destination", "market_geography",
         "commercial_basis", "packaging", "currency", "unit", "observation_type")


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if not {"market_identity", "price_observation"} <= tables:
        return
    rows = list(bind.execute(sa.text(
        "SELECT id, %s FROM market_identity WHERE origin = 'Extra-EU27' AND observation_type = 'trade_unit_value'"
        % ", ".join(MATCH))).mappings())
    renamed = folded = 0
    for row in rows:
        clauses = " AND ".join("%s IS :%s" % (c, c) for c in MATCH)
        twin = bind.execute(sa.text(
            "SELECT id FROM market_identity WHERE origin IS NULL AND " + clauses),
            {c: row[c] for c in MATCH}).scalar()
        if twin is None:
            bind.execute(sa.text("UPDATE market_identity SET origin = NULL WHERE id = :i"), {"i": row["id"]})
            renamed += 1
            continue
        # Same market, same source and month on both sides is one reading: keep the twin's.
        bind.execute(sa.text(
            "DELETE FROM price_observation WHERE identity_id = :l AND EXISTS ("
            "SELECT 1 FROM price_observation p WHERE p.identity_id = :k "
            "AND p.source_id = price_observation.source_id "
            "AND p.observation_date = price_observation.observation_date)"),
            {"k": twin, "l": row["id"]})
        bind.execute(sa.text("UPDATE price_observation SET identity_id = :k WHERE identity_id = :l"),
                     {"k": twin, "l": row["id"]})
        if "market_reference_point" in tables:
            bind.execute(sa.text("DELETE FROM market_reference_point WHERE identity_id IN (:k, :l)"),
                         {"k": twin, "l": row["id"]})
        bind.execute(sa.text("DELETE FROM market_identity WHERE id = :l"), {"l": row["id"]})
        folded += 1
    print("[0047] extra-EU import totals renamed to origin NULL: %d" % renamed)
    print("[0047] extra-EU import totals folded into an existing NULL identity: %d" % folded)


def downgrade():
    pass
