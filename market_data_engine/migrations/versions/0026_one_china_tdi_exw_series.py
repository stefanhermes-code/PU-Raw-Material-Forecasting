"""One TDI China ex-works series, and nothing else to call anything (v0.4.148)

The last of the consolidation decisions, Stefan, 14 September 2026. KGI's purchases join the series
that already carries the four publishers and FoamPartner's Changzhou prices. After this there is one
TDI China ex-works line.

    FoamPartner    2014-01 to 2017-04
    KGI            2021-07 to 2024-07
    publishers     2024-01 onward

The two months where KGI and the publishers both have a value are averaged - one value per source,
then the mean, which is the same rule the four publishers already share with each other.

What is inside this that was not before, stated rather than buried: KGI bought DAP into Thailand, so
freight out of China, Thai duty and the distributor's margin sit inside those prices and they are
filed here as ex-works China. Across the two overlapping months they land within 1.3% of the
published assessment, so the join shows no step; the terms are still not the same terms.

This clears both internal sources so the reload rebuilds them on the one identity, and drops every
consolidated point so the series is recomputed from what is actually there.
"""
from alembic import op
import sqlalchemy as sa

revision = "0026_one_china_tdi_exw_series"
down_revision = "0025_kgi_series_is_stored_in_yuan"
branch_labels = None
depends_on = None

SOURCES = ("kgi_thailand", "foampartner_china")


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "price_observation" not in tables or "market_identity" not in tables:
        return

    cleared = 0
    for source in SOURCES:
        cleared += bind.execute(sa.text("DELETE FROM price_observation WHERE source_id = :s"),
                                {"s": source}).rowcount

    orphans = [row[0] for row in bind.execute(sa.text("""
        SELECT i.id FROM market_identity i
         WHERE NOT EXISTS (SELECT 1 FROM price_observation o WHERE o.identity_id = i.id)
    """)).all()]
    for identity_id in orphans:
        if "market_reference_point" in tables:
            bind.execute(sa.text("DELETE FROM market_reference_point WHERE identity_id = :id"),
                         {"id": identity_id})
        bind.execute(sa.text("DELETE FROM market_identity WHERE id = :id"), {"id": identity_id})

    stale = 0
    if "market_reference_point" in tables:
        stale = bind.execute(sa.text("DELETE FROM market_reference_point")).rowcount

    print(f"0026_one_china_tdi_exw_series: {cleared} observations cleared for reload, "
          f"{len(orphans)} empty identities removed, {stale} consolidated points dropped")


def downgrade():
    pass
