"""KGI TDI: one series, DAP, both suppliers (v0.4.145)

The store carried BASF on CIF and Nagase on DDP. Neither came off a document.

What the documents say: BASF (Thai) Limited and Nagase (Thailand) Co., Ltd are both Thai entities,
invoicing in baht, with 7% Thai VAT on the face of every one of the sixteen orders. That is a
domestic sale - duty and import VAT settled before the invoice was raised, material delivered. On a
CIF import those sit with Customs, not on the supplier's invoice. None of the five BASF orders prints
an Incoterm at all; the only term printed anywhere in the sixteen is DAP, on three Nagase forms.

The two series also said the opposite of what their terms implied. On the two months both quoted,
BASF was 8.2% and 3.9% ABOVE Nagase - and DDP is meant to be the dearer of the two, carrying duty and
the distributor's margin the whole way to the gate. The gap is negotiation, not Incoterm.

So both are DAP, and they are one series: same buyer, same Chinese T-80, same terms, same delivery
point. Stefan, 14 September 2026. The supplier stays in every observation's evidence id, so nothing
about who sold what is lost - it just no longer splits the line in two.

This clears the KGI rows so the reload rebuilds them on one identity. The rules live in
`app/history/transactions.py`, which both loaders read; a migration that knows something the loaders
do not is one the next start undoes.
"""
from alembic import op
import sqlalchemy as sa

revision = "0024_kgi_tdi_is_one_dap_series"
down_revision = "0023_china_exw_is_one_series_net_of_vat"
branch_labels = None
depends_on = None

SOURCE = "kgi_thailand"


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "price_observation" not in tables or "market_identity" not in tables:
        return

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

    print(f"0024_kgi_tdi_is_one_dap_series: {cleared} KGI observations cleared for reload, "
          f"{len(orphans)} empty identities removed")


def downgrade():
    pass
