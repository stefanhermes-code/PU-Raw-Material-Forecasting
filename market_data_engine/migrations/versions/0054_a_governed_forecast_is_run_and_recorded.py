"""A governed forecast is run and recorded (v0.5.62)

Adaptive Forecast Method Selection CR, Workpackage 3 (acting-architect decision D5). One table,
`governed_forecast_run`: every time the production forecast job runs the method in force for one
series and horizon, what it ran, on whose choice, with what result - SUCCEEDED with the vintage it
wrote, or FAILED with the reason and the automatic recommendation shown beside it (Review Focus 3).
A forecast vintage cannot hold a failure, and a failure that is not written down is a failure the page
cannot show.

Also switches on row-level security for the six governance tables 0053 created and this one, as every
other table in the Supabase store already has it (the engine connects as the owner and is unaffected;
the public REST API no longer exposes them).

Additive, idempotent, reversible.
"""
from alembic import op
import sqlalchemy as sa

revision = "0054_a_governed_forecast_is_run_and_recorded"
down_revision = "0053_a_forecast_method_is_governed"
branch_labels = None
depends_on = None

GOVERNANCE_TABLES = ("forecast_method", "model_fit_review", "model_fit_result", "forecast_method_selection",
                     "forecast_method_override", "challenger_approval", "governed_forecast_run")


def upgrade() -> None:
    bind = op.get_bind()
    have = set(sa.inspect(bind).get_table_names())
    stamp = sa.DateTime(timezone=True)
    if "governed_forecast_run" not in have:
        op.create_table(
            "governed_forecast_run",
            sa.Column("id", sa.String, primary_key=True),
            sa.Column("run_key", sa.String, nullable=False),
            sa.Column("identity_id", sa.String, sa.ForeignKey("market_identity.id"), nullable=False),
            sa.Column("identity_key", sa.String, nullable=False),
            sa.Column("horizon_weeks", sa.Integer, nullable=False),
            sa.Column("as_of", stamp, nullable=False),
            sa.Column("method_id", sa.String, sa.ForeignKey("forecast_method.id"), nullable=False),
            sa.Column("method_key", sa.String, nullable=False),
            sa.Column("method_version", sa.String, nullable=False, server_default=""),
            sa.Column("selection_source", sa.String, nullable=False),
            sa.Column("review_id", sa.String, sa.ForeignKey("model_fit_review.id"), nullable=True),
            sa.Column("status", sa.String, nullable=False),
            sa.Column("reason", sa.Text, nullable=False, server_default=""),
            sa.Column("vintage_id", sa.String, sa.ForeignKey("forecast_vintage.id"), nullable=True),
            sa.Column("recommended_method_key", sa.String, nullable=True),
            sa.Column("inputs", sa.JSON, nullable=False),
            sa.Column("created_at", stamp, nullable=False),
        )
        op.create_index("ix_governed_forecast_run_run_key", "governed_forecast_run", ["run_key"], unique=True)
        for column in ("identity_id", "identity_key", "horizon_weeks", "as_of", "method_id", "status"):
            op.create_index(f"ix_governed_forecast_run_{column}", "governed_forecast_run", [column])
    if bind.dialect.name == "postgresql":
        present = set(sa.inspect(bind).get_table_names())
        for table in GOVERNANCE_TABLES:
            if table in present:
                op.execute(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    bind = op.get_bind()
    if "governed_forecast_run" in set(sa.inspect(bind).get_table_names()):
        op.drop_table("governed_forecast_run")
    # Fully reversible (WP3 gate): row-level security back off on the six 0053 tables this switched on.
    if bind.dialect.name == "postgresql":
        present = set(sa.inspect(bind).get_table_names())
        for table in GOVERNANCE_TABLES:
            if table in present:
                op.execute(f'ALTER TABLE public."{table}" DISABLE ROW LEVEL SECURITY')
