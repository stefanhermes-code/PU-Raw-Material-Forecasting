"""A computed number gets its own table, so it can never be read as a price (v0.4.192)

`price_observation` is what a publisher printed. `market_reference_point` is the governed value
consolidated from those prints. A cost floor computed from other chemicals' prices through the route
graph is neither, and putting it in either table would be the last step before somebody charts it,
sells it, or feeds it to a model as an observation.

The idea came from a specification circulated on 19 September 2026, which kept `feedstock_spots` and
`calculated_floors` as separate objects. That separation is right. What came with it - a module that
invented a synthetic price when a feed went quiet - is not, and is not here.

Every row records the route it came down, the coefficients used and the ids of the readings it was
computed from, so a floor can be opened up and disagreed with like every other row in this store.
"""
from alembic import op
import sqlalchemy as sa

revision = "0037_derived_values_have_their_own_table"
down_revision = "0036_bootstrap_stops_recreating_polyol"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "derived_value",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("identity_id", sa.String(), sa.ForeignKey("market_identity.id"), nullable=False),
        sa.Column("value_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False, server_default="COST_FLOOR"),
        sa.Column("route_id", sa.String(), nullable=True),
        sa.Column("inputs", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("currency", sa.String(), nullable=False, server_default="CNY"),
        sa.Column("unit", sa.String(), nullable=False, server_default="t"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_derived_value_identity", "derived_value", ["identity_id"])
    op.create_index("ix_derived_value_date", "derived_value", ["value_date"])
    op.create_index("ix_derived_value_kind", "derived_value", ["kind"])
    op.create_index("ix_derived_value_route", "derived_value", ["route_id"])


def downgrade() -> None:
    op.drop_index("ix_derived_value_route", table_name="derived_value")
    op.drop_index("ix_derived_value_kind", table_name="derived_value")
    op.drop_index("ix_derived_value_date", table_name="derived_value")
    op.drop_index("ix_derived_value_identity", table_name="derived_value")
    op.drop_table("derived_value")
