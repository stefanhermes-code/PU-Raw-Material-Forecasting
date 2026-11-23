"""FoamPartner Changzhou: state the origin and the Incoterm (v0.4.128)

The FoamPartner records came in with no origin and a generic "DELIVERED" basis, because the extract
had no column for either, and `app/domain/origin.py` then correctly marked the origin UNKNOWN - a
destination-side term says where a tank landed and nothing about who made it.

They are the Changzhou plant's own purchases: Chinese material, delivered in China on DAP. Stated by
Stefan on 14 September 2026. Before this, 147 months of Chinese TDI and polyol history sat in the
store labelled as material of unknown origin.

DELIBERATELY NOT INCLUDED
-------------------------
The KGI Thailand history. The purchase orders from 2024 state Chinese origin, BASF on CIF and Nagase
on DDP, and those are loaded that way - but nobody has said the 2021-2023 postings carry the same
terms, and asserting about 2022 what is known about 2024 is how a series acquires facts nobody
checked. The store therefore holds two series per supplier and the purchase-order loader names the
split on every run. That is a question waiting for an answer rather than a defect.
"""
from alembic import op
import sqlalchemy as sa

revision = "0017_internal_purchase_attribution"
down_revision = "0016_forecast_vintage"
branch_labels = None
depends_on = None

SOURCE = "foampartner_china"
ORIGIN = "China"
DESTINATION = "China"
BASIS = "DAP"


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "market_identity" not in tables or "price_observation" not in tables:
        return

    rows = bind.execute(sa.text("""
        SELECT DISTINCT i.* FROM market_identity i
          JOIN price_observation o ON o.identity_id = i.id
         WHERE o.source_id = :source
    """), {"source": SOURCE}).mappings().all()

    updated = 0
    for row in rows:
        if row["origin"] == ORIGIN and row["commercial_basis"] == BASIS:
            continue
        bind.execute(sa.text("""
            UPDATE market_identity
               SET origin = :o, destination = :d, commercial_basis = :b, comparability_group = :g
             WHERE id = :id
        """), {
            "o": ORIGIN, "d": DESTINATION, "b": BASIS, "id": row["id"],
            "g": "|".join([row["product_family"], row["product_grade_or_chemistry"] or "UNKNOWN",
                           ORIGIN, DESTINATION, BASIS, row["currency"], row["unit"]]),
        })
        updated += 1

    if updated:
        print(f"0017_internal_purchase_attribution: {updated} FoamPartner identities re-attributed")


def downgrade():
    pass
