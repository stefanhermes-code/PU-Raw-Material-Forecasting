"""China TDI ex-works is one series, not two (v0.4.143)

SunSirs prints the same China TDI ex-works assessment under two origin labels - "China" on most days
and "East China" on a few. The origin rule read them as two markets and filed them as two identities,
so the same publisher's own series was split in half: 37 days on one row and 2 on another.

East China is where China's TDI is made. It is not a different market, it is the same assessment with
the region named, and the consolidated series cannot consolidate across two identity rows.

So the East China observations move onto the China identity and the empty row goes. Any evidence that
already exists on both is dropped from the row being retired rather than carried over twice.
"""
from alembic import op
import sqlalchemy as sa

revision = "0022_east_china_is_china"
down_revision = "0021_purchase_identities_are_not_rebuilt"
branch_labels = None
depends_on = None

# Kept in step with NATIONAL_FOR in app/domain/origin.py, which is what stops the collector from
# creating the regional row again tomorrow. This clears the ones already written.
REGIONAL = {
    "east china": "China", "north china": "China", "south china": "China",
    "central china": "China", "northwest china": "China", "northeast china": "China",
    "southwest china": "China",
}

# The publisher names the region in both fields, so both have to fold before two rows can match.
FOLDED = ("origin", "market_geography")
MATCH = ("product_family", "product_grade_or_chemistry", "destination", "commercial_basis",
         "packaging", "currency", "unit", "observation_type")


def _national(value):
    return REGIONAL.get(str(value or "").strip().lower(), value)


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "market_identity" not in tables or "price_observation" not in tables:
        return

    rows = bind.execute(sa.text("""
        SELECT id, product_family, product_grade_or_chemistry, destination, market_geography,
               commercial_basis, packaging, currency, unit, observation_type, origin
          FROM market_identity
         WHERE observation_type = 'market_price'
    """)).mappings().all()

    def folds_to(row):
        return tuple(_national(row[field]) for field in FOLDED) + tuple(row[f] for f in MATCH)

    moved = merged = 0
    for row in rows:
        regional = [field for field in FOLDED
                    if str(row[field] or "").strip().lower() in REGIONAL]
        if not regional:
            continue
        canonical = next((other for other in rows
                          if other["id"] != row["id"] and folds_to(other) == folds_to(row)
                          and str(other["origin"] or "").strip().lower() not in REGIONAL), None)
        if canonical is None:
            # Nothing to merge into: name the market correctly and leave it as its own row.
            bind.execute(sa.text("""
                UPDATE market_identity SET origin = :o, market_geography = :g WHERE id = :id
            """), {"o": _national(row["origin"]), "g": _national(row["market_geography"]),
                   "id": row["id"]})
            moved += 1
            continue

        bind.execute(sa.text("""
            DELETE FROM price_observation
             WHERE identity_id = :orphan
               AND EXISTS (SELECT 1 FROM price_observation keep
                            WHERE keep.identity_id = :canonical
                              AND keep.evidence_id = price_observation.evidence_id)
        """), {"orphan": row["id"], "canonical": canonical["id"]})
        bind.execute(sa.text("UPDATE price_observation SET identity_id = :c WHERE identity_id = :o"),
                     {"c": canonical["id"], "o": row["id"]})
        if "market_reference_point" in tables:
            bind.execute(sa.text("DELETE FROM market_reference_point WHERE identity_id = :id"),
                         {"id": row["id"]})
        bind.execute(sa.text("DELETE FROM market_identity WHERE id = :id"), {"id": row["id"]})
        merged += 1

    if moved or merged:
        print(f"0022_east_china_is_china: {merged} merged, {moved} relabelled")


def downgrade():
    pass
