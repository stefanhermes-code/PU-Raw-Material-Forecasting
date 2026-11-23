"""A review commits only when automatic selection is switched on (v0.5.64)

Adaptive Forecast Method Selection CR, Workpackage 4 (acting-architect decision D7). One table,
`forecast_governance_setting`: named settings an administrator changes with actor and reason, with
their history. `automatic_selection_enabled` starts false - a Model Fit Review records results and
proposals but commits no selection until WP5's application-native validation has passed and an
administrator switches it on. `review_cadence_months` defaults to 3.

Additive, idempotent, reversible; row-level security on Postgres like the rest of the store.
"""
from alembic import op
import sqlalchemy as sa

revision = "0055_a_review_commits_only_when_switched_on"
down_revision = "0054_a_governed_forecast_is_run_and_recorded"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "forecast_governance_setting" not in set(sa.inspect(bind).get_table_names()):
        op.create_table(
            "forecast_governance_setting",
            sa.Column("key", sa.String, primary_key=True),
            sa.Column("value", sa.JSON, nullable=False),
            sa.Column("actor", sa.String, nullable=False, server_default="SYSTEM"),
            sa.Column("reason", sa.Text, nullable=False, server_default=""),
            sa.Column("history", sa.JSON, nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    if bind.dialect.name == "postgresql":
        op.execute('ALTER TABLE public."forecast_governance_setting" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    bind = op.get_bind()
    if "forecast_governance_setting" in set(sa.inspect(bind).get_table_names()):
        op.drop_table("forecast_governance_setting")
