"""FoamPartner and KGI: one identity per series, and the loader now agrees (v0.4.142)

Every FoamPartner and KGI observation was in the store twice. 352 rows for 176 pieces of evidence on
the FoamPartner side, 105 for 69 on KGI.

How. 0017 and 0018 moved the purchase rows onto China-origin identities, which is where they belong.
`app/history/transactions.py` went on building its identities as UNKNOWN origin on a DELIVERED basis,
so at the next start it looked for its rows on the identity it still built, found none - the loader
is idempotent by identity and evidence id together - and inserted a fresh copy. Then another on the
start after that. A migration cannot hold a fact the code that writes the rows does not share.

The fact now lives in the loader, in `ATTRIBUTION`. This clears the copies so the reload rebuilds
each series once: FoamPartner's rows go the way 0019 already sent KGI's, and the identities left
holding nothing are removed. An identity that still has observations from some other source is left
exactly where it is - emptiness is the test, not the name on the row.
"""
from alembic import op
import sqlalchemy as sa

revision = "0021_purchase_identities_are_not_rebuilt"
down_revision = "0020_fpb_window_runs_to_april_2017"
branch_labels = None
depends_on = None

SOURCES = ("foampartner_china", "kgi_thailand")


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "price_observation" not in tables or "market_identity" not in tables:
        return

    cleared = bind.execute(
        sa.text("DELETE FROM price_observation WHERE source_id = 'foampartner_china'")).rowcount

    orphans = [row[0] for row in bind.execute(sa.text("""
        SELECT i.id FROM market_identity i
         WHERE i.observation_type = 'transaction_price'
           AND NOT EXISTS (SELECT 1 FROM price_observation o WHERE o.identity_id = i.id)
    """)).all()]

    for identity_id in orphans:
        if "market_reference_point" in tables:
            bind.execute(sa.text("DELETE FROM market_reference_point WHERE identity_id = :id"),
                         {"id": identity_id})
        bind.execute(sa.text("DELETE FROM market_identity WHERE id = :id"), {"id": identity_id})

    if cleared or orphans:
        print(f"0021_purchase_identities_are_not_rebuilt: {cleared} observations cleared for "
              f"reload, {len(orphans)} empty identities removed")


def downgrade():
    pass
