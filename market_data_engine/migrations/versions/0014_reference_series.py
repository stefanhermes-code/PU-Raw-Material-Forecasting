"""reference series: non-price history held for correlation (v0.4.81)

The correlation engine needs upstream history, and most of what exists for the upstream chemicals is
not a price. The first pack of TDI feedstock data is five US PPI indices and one physical benchmark:
an index has no currency and no tonne, so it cannot be converted, cannot be averaged with a price,
and must never reach a market reference or a customer view.

Putting them in `price_observation` would have made that a rule someone has to remember. Two separate
tables make it structural: the price path cannot read this one by accident.

They still measure co-movement perfectly well - the log change of a true price index is the log
change of the price it tracks - which is all the correlation work asks of them.
"""
from alembic import op
import sqlalchemy as sa

revision = "0014_reference_series"
down_revision = "0013_retire_bootstrap_kgi_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reference_series",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("series_id", sa.String(), nullable=False, unique=True),
        sa.Column("chemical", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("geography", sa.String(), nullable=False),
        sa.Column("unit", sa.String(), nullable=False),
        sa.Column("is_index", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("quality", sa.String(), nullable=False, server_default="USABLE_PROXY"),
        sa.Column("source_type", sa.String(), nullable=False, server_default=""),
        sa.Column("publisher", sa.String(), nullable=False, server_default=""),
        sa.Column("source_url", sa.String(), nullable=False, server_default=""),
        sa.Column("rights_class", sa.String(), nullable=False, server_default="PUBLIC_ATTRIBUTION"),
        sa.Column("note", sa.String(), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_reference_series_chemical", "reference_series", ["chemical"])
    op.create_index("ix_reference_series_geography", "reference_series", ["geography"])
    op.create_index("ix_reference_series_quality", "reference_series", ["quality"])

    op.create_table(
        "reference_observation",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("series_id", sa.String(), sa.ForeignKey("reference_series.series_id"), nullable=False),
        sa.Column("observation_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("evidence_id", sa.String(), nullable=False, unique=True),
    )
    op.create_index("ix_reference_observation_series", "reference_observation", ["series_id"])
    op.create_index("ix_reference_observation_date", "reference_observation", ["observation_date"])


def downgrade() -> None:
    op.drop_table("reference_observation")
    op.drop_table("reference_series")
