"""KGI's polymeric MDI purchases merge into the China MDI series (v0.4.165)

Stefan, 16 September 2026.

Four orders across three years, and the only MDI evidence that exists before October 2025:

    Nov 2021   Wanhua W8223 through Nagase      two orders the same day, averaged
    Nov 2023   Wanhua W8223 through Nagase
    Dec 2023   BASF Lupranate MX118/1
    Feb 2024   Wanhua W8223 through Nagase

They were on two identities, split by supplier and by the Incoterm printed on the paperwork. Both
suppliers are Thai entities invoicing in baht with Thai VAT on the face of the order - one way of
selling, not two - which is the same evidence that put KGI's TDI on one series in 0026.

The cost, stated rather than hidden: these were delivered into Thailand, so freight out of China,
Thai duty and the distributor's margin sit inside them, and they are filed here as ex-works China.
On TDI there were two overlapping months to measure that step and it came to 1.3%. Here there is
nothing to measure it against: the published series does not reach back to 2024. The merge is a
decision, not a calculation, and the page will draw four purchase points in 2021-2024 against
published prices that begin in October 2025.

They are HTC's own records, so no tax is stripped from them - the buyer's ledger is already net.

`MARKET_LANE` and `COMBINED_SUPPLIER_SERIES` carry the same rule now, so a reload files them here
rather than rebuilding the identities this drops. That is the fault 0021 exists to prevent.
"""
from alembic import op
import sqlalchemy as sa

revision = "0033_kgi_mdi_purchases_merge"
down_revision = "0032_pmdi_has_no_grade"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if not {"price_observation", "market_identity"} <= tables:
        return

    target = bind.execute(sa.text(
        "SELECT id FROM market_identity WHERE product_family = 'PMDI' "
        "AND market_geography = 'China' AND commercial_basis = 'EXW' AND currency = 'CNY' LIMIT 1"
    )).scalar()
    if target is None:
        print("[0033] no China PMDI ex-works series to merge into; nothing done")
        return

    rows = bind.execute(sa.text(
        "SELECT id, product_grade_or_chemistry, market_geography, commercial_basis, "
        "  (SELECT COUNT(*) FROM price_observation WHERE identity_id = market_identity.id) AS held "
        "FROM market_identity WHERE product_family IN ('PMDI', 'MDI') AND id <> :target"
    ), {"target": target}).mappings().all()
    purchases = [row for row in rows if row["held"]]
    if not purchases:
        print("[0033] no purchase identities left to merge")
        return
    for row in purchases:
        print(f"[0033] merging: {row['product_grade_or_chemistry']} / {row['market_geography']} / "
              f"{row['commercial_basis']} ({row['held']} readings)")

    moved = 0
    for row in purchases:
        result = bind.execute(sa.text(
            "UPDATE price_observation SET identity_id = :target WHERE identity_id = :old"
        ), {"target": target, "old": row["id"]})
        moved += int(result.rowcount or 0)
    print(f"[0033] readings merged onto the China MDI ex-works series: {moved}")

    doubled = bind.execute(sa.text(
        "DELETE FROM price_observation WHERE id IN ("
        "  SELECT id FROM price_observation p WHERE identity_id = :id AND EXISTS ("
        "    SELECT 1 FROM price_observation q WHERE q.identity_id = p.identity_id"
        "      AND q.source_id = p.source_id AND q.price = p.price"
        "      AND q.observation_date = p.observation_date AND q.id < p.id))"
    ), {"id": target})
    print(f"[0033] duplicate rows collapsed: {int(doubled.rowcount or 0)}")

    emptied = bind.execute(sa.text(
        "DELETE FROM market_identity WHERE product_family IN ('PMDI', 'MDI') AND id <> :id "
        "AND NOT EXISTS (SELECT 1 FROM price_observation WHERE identity_id = market_identity.id)"
    ), {"id": target})
    print(f"[0033] emptied identities removed: {int(emptied.rowcount or 0)}")

    if "market_reference_point" in tables:
        cleared = bind.execute(sa.text(
            "DELETE FROM market_reference_point WHERE identity_id = :id"), {"id": target})
        print(f"[0033] consolidated points cleared for rebuild: {int(cleared.rowcount or 0)}")

    total, sources, first, last = bind.execute(sa.text(
        "SELECT COUNT(*), COUNT(DISTINCT source_id), MIN(observation_date), MAX(observation_date) "
        "FROM price_observation WHERE identity_id = :id"), {"id": target}).fetchone()
    print(f"[0033] the China polymeric MDI ex-works series holds {total} readings from {sources} "
          f"sources, {str(first)[:10]} to {str(last)[:10]}")


def downgrade():
    # A buyer's own record filed onto the market series is not taken back off it.
    pass
