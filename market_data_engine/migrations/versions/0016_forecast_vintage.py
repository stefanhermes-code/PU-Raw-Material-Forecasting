"""forecast vintages: what a model said, on the day it said it (v0.4.118)

Every forecast the application has shown has been computed on request and discarded. Nothing on disk
records what any horizon promised, which means no claim about forecast accuracy can be checked after
the fact - and one was made and repeated for weeks ("the price lands inside this range about eight
times in ten") with nothing able to contradict it.

An append-only table fixes that at the level where it can't be undone by good intentions. One row per
identity, model, horizon and as-of, written once. The outcome is appended into reserved columns when
the period closes, so recording what happened cannot rewrite what was claimed.
"""
from alembic import op
import sqlalchemy as sa

revision = "0016_forecast_vintage"
down_revision = "0015_repair_unquotable_identities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "forecast_vintage",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("vintage_key", sa.String(), nullable=False, unique=True),
        sa.Column("identity_key", sa.String(), nullable=False),
        sa.Column("model_key", sa.String(), nullable=False),
        sa.Column("model_version", sa.String(), nullable=False, server_default=""),
        sa.Column("horizon_weeks", sa.Integer(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("chemical", sa.String(), nullable=False, server_default=""),
        sa.Column("currency", sa.String(), nullable=False, server_default=""),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("quantiles", sa.JSON(), nullable=True),
        sa.Column("period", sa.DateTime(timezone=True), nullable=False),
        sa.Column("steps", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("resolution", sa.String(), nullable=False, server_default="M"),
        sa.Column("exact", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("calibration", sa.JSON(), nullable=True),
        sa.Column("observations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("anchor_value", sa.Float(), nullable=True),
        sa.Column("state", sa.String(), nullable=False, server_default="CANDIDATE"),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome_value", sa.Float(), nullable=True),
        sa.Column("outcome_recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Float(), nullable=True),
        sa.Column("inside_band", sa.Boolean(), nullable=True),
    )
    op.create_index("ix_forecast_vintage_identity", "forecast_vintage", ["identity_key"])
    op.create_index("ix_forecast_vintage_model", "forecast_vintage", ["model_key"])
    op.create_index("ix_forecast_vintage_as_of", "forecast_vintage", ["as_of"])
    op.create_index("ix_forecast_vintage_period", "forecast_vintage", ["period"])
    op.create_index("ix_forecast_vintage_horizon", "forecast_vintage", ["horizon_weeks"])
    op.create_index("ix_forecast_vintage_state", "forecast_vintage", ["state"])
    # The query the performance screen runs: which claims for this identity have now come due.
    op.create_index("ix_forecast_vintage_due", "forecast_vintage",
                    ["identity_key", "period", "outcome_value"])


def downgrade() -> None:
    op.drop_table("forecast_vintage")
