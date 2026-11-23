"""China TDI ex-works: one series, net of VAT, back to 2014 (v0.4.144)

Two decisions by Stefan on 14 September 2026, applied together because each one breaks without the
other.

**The consolidated series is stored net of VAT.** Chinese domestic assessments are quoted 含税 -
inclusive of the 13% on chemicals. That is now taken out where the consolidated value is built, so
the stored series is the one that gets used and there is no second, net number living somewhere else
that has to be kept in step. The raw prints are untouched: 16,700 is what SunSirs printed on 5
January 2024 and it still says so.

**FoamPartner's Changzhou T-80 joins that series.** They are ex-works prices - his decision; nothing
in the workbooks states a basis either way - and the published assessments do not begin until January
2024. Eighty-one months separate the two, so there is nothing to overlap and nothing to average: they
are different years of one line. The rows lose the "TDI 80/20" grade to land on the published series,
which carries no grade because a China TDI spot assessment is T-80 without saying so.

Why the two together: FoamPartner's figures are net already. Had they joined a series still carrying
tax, everything before 2024 would have read 13% below the market.

This clears the FoamPartner rows and the identities and reference points they leave behind, so the
reload at startup rebuilds them under the new rules. `app/history/transactions.py` holds those rules
now - a migration that knows something the loader does not is a migration the next start undoes.
"""
from alembic import op
import sqlalchemy as sa

revision = "0023_china_exw_is_one_series_net_of_vat"
down_revision = "0022_east_china_is_china"
branch_labels = None
depends_on = None

SOURCE = "foampartner_china"


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "price_observation" not in tables or "market_identity" not in tables:
        return

    touched = [row[0] for row in bind.execute(sa.text("""
        SELECT DISTINCT identity_id FROM price_observation WHERE source_id = :source
    """), {"source": SOURCE}).all()]

    cleared = bind.execute(sa.text("DELETE FROM price_observation WHERE source_id = :source"),
                           {"source": SOURCE}).rowcount

    orphans = [row[0] for row in bind.execute(sa.text("""
        SELECT i.id FROM market_identity i
         WHERE NOT EXISTS (SELECT 1 FROM price_observation o WHERE o.identity_id = i.id)
    """)).all()]

    for identity_id in set(touched) | set(orphans):
        if "market_reference_point" in tables:
            bind.execute(sa.text("DELETE FROM market_reference_point WHERE identity_id = :id"),
                         {"id": identity_id})
    for identity_id in orphans:
        bind.execute(sa.text("DELETE FROM market_identity WHERE id = :id"), {"id": identity_id})

    # Every consolidated point in the store was computed with the tax still in it. They are rebuilt
    # at the next start, net, from the raw prints - which is cheaper and safer than dividing stored
    # values by 1.13 and hoping none of them had already been corrected.
    if "market_reference_point" in tables:
        stale = bind.execute(sa.text("DELETE FROM market_reference_point")).rowcount
    else:
        stale = 0

    print(f"0023_china_exw_is_one_series_net_of_vat: {cleared} FoamPartner observations cleared, "
          f"{len(orphans)} empty identities removed, {stale} consolidated points dropped for rebuild")


def downgrade():
    pass
