"""A producer's price carries a delivery term, and it is an Incoterm.

JLC's five price types were stored as five invented basis codes - CHINA_PRODUCER_LIST,
CHINA_PRODUCER_EXW_CASH, CHINA_PRODUCER_DELIVERED_CASH, CHINA_PRODUCER_EXW_ACCEPTANCE and
CHINA_PRODUCER_DELIVERED_ACCEPTANCE - each folding the way a price is paid into the field that holds
a delivery term, and none of them an Incoterm.

JLC states the delivery term in four of the five. 出厂 is ex-factory and 自提 is the buyer collecting,
so both are EXW; 送到 is delivered to the buyer, which is DAP. 挂牌价 is a list price and states
nothing, and a producer quoting its own list price is quoting at its own gate. Stefan, 21 September
2026: if a producer shows no Incoterm, assume EXW - there is nothing else it can be.

现金 cash and 承兑 acceptance-bill are dropped rather than kept in a field of their own. Stefan's call
the same day.

Revision ID: 0039_a_producer_price_carries_a_delivery_term
Revises: 0038_a_revision_replaces_what_it_revises
"""
from alembic import op
import sqlalchemy as sa

revision = "0039_a_producer_price_carries_a_delivery_term"
down_revision = "0038_a_revision_replaces_what_it_revises"
branch_labels = None
depends_on = None

REMAP = {
    "CHINA_PRODUCER_LIST": "EXW",
    "CHINA_PRODUCER_EXW_CASH": "EXW",
    "CHINA_PRODUCER_EXW_ACCEPTANCE": "EXW",
    "CHINA_PRODUCER_DELIVERED_CASH": "DAP",
    "CHINA_PRODUCER_DELIVERED_ACCEPTANCE": "DAP",
}


def upgrade() -> None:
    bind = op.get_bind()
    for old, basis in REMAP.items():
        bind.execute(
            sa.text("""
                UPDATE market_identity
                   SET commercial_basis = :basis,
                       comparability_group = replace(comparability_group, :old, :basis)
                 WHERE commercial_basis = :old
            """),
            {"old": old, "basis": basis},
        )
    # Two of the old codes can land on one identity - cash and acceptance ex-works are both EXW now.
    # Nothing in the store does today, and the revision rule from 0038 settles it if one ever does:
    # one publisher, one market, one day, one price, and the latest reading is the one that stands.


def downgrade() -> None:
    # The old codes said nothing the delivery term does not, apart from a payment term that is no
    # longer recorded, so there is nothing to rebuild them from.
    pass
