"""KGI: the FX effect comes out in the store (v0.4.146)

KGI bought Chinese T-80 and paid in baht, so the baht number already contains whatever rate the
seller applied on the day. Left in baht the series moves when the baht moves and not only when the
market does: Nagase held 64,200 THB from July 2021 to July 2022, and the same unchanged contract read
in yuan falls 6.8% across that year.

So the series is stored in yuan, converted at the ECB cross of the day each price was struck - the
same moment the seller did their own conversion, rather than today's rate. Stefan, 14 September 2026,
and stored rather than computed over the top for the same reason the VAT strip is stored: one number,
no second one to keep in step.

What is not lost: `price_observation` gains `native_price` and `native_currency`, so every converted
row still carries the baht figure it came from. A conversion whose original is gone cannot be
rechecked when the rate, or the rule, turns out to be wrong.

This clears the KGI rows so the reload rebuilds them converted, and drops the identities they leave
behind - the currency is part of an identity, so the THB rows and the CNY rows cannot share one.
"""
from alembic import op
import sqlalchemy as sa

revision = "0025_kgi_series_is_stored_in_yuan"
down_revision = "0024_kgi_tdi_is_one_dap_series"
branch_labels = None
depends_on = None

SOURCE = "kgi_thailand"


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "price_observation" not in tables or "market_identity" not in tables:
        return

    columns = {column["name"] for column in inspector.get_columns("price_observation")}
    if "native_price" not in columns:
        op.add_column("price_observation", sa.Column("native_price", sa.Float(), nullable=True))
    if "native_currency" not in columns:
        op.add_column("price_observation", sa.Column("native_currency", sa.String(), nullable=True))

    cleared = bind.execute(sa.text("DELETE FROM price_observation WHERE source_id = :source"),
                           {"source": SOURCE}).rowcount

    orphans = [row[0] for row in bind.execute(sa.text("""
        SELECT i.id FROM market_identity i
         WHERE NOT EXISTS (SELECT 1 FROM price_observation o WHERE o.identity_id = i.id)
    """)).all()]
    for identity_id in orphans:
        if "market_reference_point" in tables:
            bind.execute(sa.text("DELETE FROM market_reference_point WHERE identity_id = :id"),
                         {"id": identity_id})
        bind.execute(sa.text("DELETE FROM market_identity WHERE id = :id"), {"id": identity_id})

    print(f"0025_kgi_series_is_stored_in_yuan: {cleared} KGI observations cleared for reload in CNY, "
          f"{len(orphans)} empty identities removed")


def downgrade():
    pass
