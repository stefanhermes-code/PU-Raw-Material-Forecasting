"""KGI Thailand: one continuous series per supplier (v0.4.132)

The KGI purchase history arrived in two pieces. The ERP workbook extract runs July 2021 to November
2023 and states neither origin nor Incoterm; the signed purchase orders pick up from January 2024 and
state both. Loaded as they stood, each supplier ended up as two identities - BASF on UNKNOWN/DELIVERED
to 2023 and on China/CIF from 2024, Nagase the same with DDP - and a chart of "the BASF contract"
would have drawn two lines with a fourteen-month hole between them.

They are one series. Same buyer, same suppliers, same contracts, same market; the only thing that
changed in January 2024 is which document the price was read from. Stated by Stefan on 14 September
2026, which also settles the question 0017 deliberately left open: the earlier postings carry the
same origin and terms as the orders that continue them.

So the ERP identities take the terms the orders state, and the two rows per supplier collapse into
one. The Shell polyol is untouched - a different supplier, and nobody has said where Caradol is made.

The purchase-order loader's split warning stops firing once this has run, which is the point: it was
reporting a question, and the question now has an answer.
"""
from alembic import op
import sqlalchemy as sa

revision = "0018_kgi_one_series_per_supplier"
down_revision = "0017_internal_purchase_attribution"
branch_labels = None
depends_on = None

SOURCE = "kgi_thailand"
ORIGIN = "China"
DESTINATION = "Thailand"

# Grade fragment -> the Incoterm that supplier sells on.
TERMS = (("BASF", "CIF"), ("Wanhua", "DDP"))


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "market_identity" not in tables or "price_observation" not in tables:
        return

    rows = bind.execute(sa.text("""
        SELECT DISTINCT i.* FROM market_identity i
          JOIN price_observation o ON o.identity_id = i.id
         WHERE o.source_id = :source AND i.product_family = 'TDI'
    """), {"source": SOURCE}).mappings().all()

    moved = merged = 0
    for fragment, basis in TERMS:
        group = [dict(r) for r in rows if fragment in (r["product_grade_or_chemistry"] or "")]
        if not group:
            continue
        # Keep the row the orders already landed on; it carries the stated terms.
        canonical = next((r for r in group
                          if r["origin"] == ORIGIN and r["commercial_basis"] == basis), None)
        if canonical is None:
            canonical = group[0]
        group_note = "|".join([canonical["product_family"],
                               canonical["product_grade_or_chemistry"] or "UNKNOWN",
                               ORIGIN, DESTINATION, basis, canonical["currency"],
                               canonical["unit"]])
        bind.execute(sa.text("""
            UPDATE market_identity
               SET origin = :o, destination = :d, commercial_basis = :b, comparability_group = :g
             WHERE id = :id
        """), {"o": ORIGIN, "d": DESTINATION, "b": basis, "g": group_note, "id": canonical["id"]})
        moved += 1

        for row in group:
            if row["id"] == canonical["id"]:
                continue
            bind.execute(sa.text("""
                DELETE FROM price_observation
                 WHERE identity_id = :orphan
                   AND EXISTS (SELECT 1 FROM price_observation keep
                                WHERE keep.identity_id = :canonical
                                  AND keep.evidence_id = price_observation.evidence_id)
            """), {"orphan": row["id"], "canonical": canonical["id"]})
            bind.execute(
                sa.text("UPDATE price_observation SET identity_id = :c WHERE identity_id = :o"),
                {"c": canonical["id"], "o": row["id"]})
            if "market_reference_point" in tables:
                bind.execute(sa.text("DELETE FROM market_reference_point WHERE identity_id = :id"),
                             {"id": row["id"]})
            bind.execute(sa.text("DELETE FROM market_identity WHERE id = :id"), {"id": row["id"]})
            merged += 1

    # Both internal purchase sources are CLOSED, and the store should say so.
    #
    # Stefan left KGI in September 2024, so that series ends there and no later month is coming.
    # FoamPartner ended in February 2017. A source that has finished is a different thing from a
    # source that has stopped working, and without this the freshness checks read a permanent silence
    # as a collector that needs fixing - and someone spends an afternoon on it.
    if "source_registry" in tables:
        for source_id, window in (
            (SOURCE, "Purchase history runs July 2021 to September 2024 and is CLOSED - "
                     "HTC's involvement with KGI ended in September 2024. ERP workbook postings to "
                     "November 2023, signed purchase orders from January 2024."),
            ("foampartner_china", "Purchase history runs January 2014 to February 2017 and is "
                                  "CLOSED. Changzhou plant, Chinese origin, delivered China DAP."),
        ):
            bind.execute(sa.text("""
                UPDATE source_registry
                   SET notes = :note, active = 0, refresh_frequency = 'NONE'
                 WHERE source_id = :id
            """), {"note": window + " Transaction prices, not market assessments: stored as "
                                   "transaction_price so they never reach a customer-facing "
                                   "reference.", "id": source_id})

    if moved or merged:
        print(f"0018_kgi_one_series_per_supplier: {moved} re-attributed, {merged} merged")


def downgrade():
    pass
