"""A forecast method is governed (v0.5.50)

Adaptive Forecast Method Selection CR, Workpackage 1. Six tables about METHODS - which exist, what
each may do, how each did in a Model Fit Review, which is in force per series and horizon, and who
overrode or promoted what - and three nullable provenance columns on `forecast_vintage` that stay
null until Workpackage 3 writes them.

Additive only. No existing row changes; no live forecast changes. Idempotent: a table or column
already there is left alone, so a store that was created from the models (SQLite, create_all)
upgrades cleanly. Reversible: the downgrade drops exactly what this added.
"""
from alembic import op
import sqlalchemy as sa

revision = "0053_a_forecast_method_is_governed"
down_revision = "0052_a_comext_month_needs_a_market_quantity"
branch_labels = None
depends_on = None

TABLES = ("forecast_method", "model_fit_review", "model_fit_result", "forecast_method_selection",
          "forecast_method_override", "challenger_approval")
VINTAGE_COLUMNS = ("selected_method_id", "selection_source", "review_id")


def _existing_tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _existing_columns(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    have = _existing_tables(bind)
    stamp = sa.DateTime(timezone=True)

    if "forecast_method" not in have:
        op.create_table(
            "forecast_method",
            sa.Column("id", sa.String, primary_key=True),
            sa.Column("method_key", sa.String, nullable=False),
            sa.Column("name", sa.String, nullable=False, server_default=""),
            sa.Column("kind", sa.String, nullable=False, server_default="UNIVERSAL"),
            sa.Column("status", sa.String, nullable=False, server_default="CHALLENGER"),
            sa.Column("version", sa.String, nullable=False, server_default="1"),
            sa.Column("is_flat_control", sa.Boolean, nullable=False, server_default=sa.false()),
            sa.Column("commercial_use", sa.String, nullable=False, server_default="UNKNOWN"),
            sa.Column("config", sa.JSON, nullable=False),
            sa.Column("notes", sa.Text, nullable=False, server_default=""),
            sa.Column("status_history", sa.JSON, nullable=False),
            sa.Column("created_at", stamp, nullable=False),
            sa.Column("updated_at", stamp, nullable=False),
        )
        op.create_index("ix_forecast_method_method_key", "forecast_method", ["method_key"], unique=True)
        op.create_index("ix_forecast_method_kind", "forecast_method", ["kind"])
        op.create_index("ix_forecast_method_status", "forecast_method", ["status"])

    if "model_fit_review" not in have:
        op.create_table(
            "model_fit_review",
            sa.Column("id", sa.String, primary_key=True),
            sa.Column("cutoff", stamp, nullable=False),
            sa.Column("started_at", stamp, nullable=False),
            sa.Column("finished_at", stamp, nullable=True),
            sa.Column("status", sa.String, nullable=False, server_default="RUNNING"),
            sa.Column("actor", sa.String, nullable=False, server_default="SYSTEM"),
            sa.Column("notes", sa.Text, nullable=False, server_default=""),
        )
        op.create_index("ix_model_fit_review_cutoff", "model_fit_review", ["cutoff"])
        op.create_index("ix_model_fit_review_status", "model_fit_review", ["status"])

    if "model_fit_result" not in have:
        op.create_table(
            "model_fit_result",
            sa.Column("id", sa.String, primary_key=True),
            sa.Column("review_id", sa.String, sa.ForeignKey("model_fit_review.id"), nullable=False),
            sa.Column("identity_id", sa.String, sa.ForeignKey("market_identity.id"), nullable=False),
            sa.Column("horizon_weeks", sa.Integer, nullable=False),
            sa.Column("method_id", sa.String, sa.ForeignKey("forecast_method.id"), nullable=False),
            sa.Column("method_version", sa.String, nullable=False, server_default=""),
            sa.Column("execution_status", sa.String, nullable=False, server_default="SUCCEEDED"),
            sa.Column("matched_mae", sa.Float, nullable=True),
            sa.Column("directional_accuracy", sa.Float, nullable=True),
            sa.Column("matched_dates", sa.Integer, nullable=False, server_default="0"),
            sa.Column("rank", sa.Integer, nullable=True),
            sa.Column("details", sa.JSON, nullable=False),
        )
        for column in ("review_id", "identity_id", "horizon_weeks", "method_id"):
            op.create_index(f"ix_model_fit_result_{column}", "model_fit_result", [column])

    if "forecast_method_selection" not in have:
        op.create_table(
            "forecast_method_selection",
            sa.Column("id", sa.String, primary_key=True),
            sa.Column("identity_id", sa.String, sa.ForeignKey("market_identity.id"), nullable=False),
            sa.Column("horizon_weeks", sa.Integer, nullable=False),
            sa.Column("method_id", sa.String, sa.ForeignKey("forecast_method.id"), nullable=False),
            sa.Column("method_version", sa.String, nullable=False, server_default=""),
            sa.Column("source", sa.String, nullable=False),
            sa.Column("review_id", sa.String, sa.ForeignKey("model_fit_review.id"), nullable=True),
            sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
            sa.Column("selected_at", stamp, nullable=False),
            sa.Column("superseded_at", stamp, nullable=True),
            sa.Column("actor", sa.String, nullable=False, server_default="SYSTEM"),
            sa.Column("reason", sa.Text, nullable=False, server_default=""),
        )
        for column in ("identity_id", "horizon_weeks", "method_id", "source", "review_id", "active"):
            op.create_index(f"ix_forecast_method_selection_{column}", "forecast_method_selection", [column])

    if "forecast_method_override" not in have:
        op.create_table(
            "forecast_method_override",
            sa.Column("id", sa.String, primary_key=True),
            sa.Column("identity_id", sa.String, sa.ForeignKey("market_identity.id"), nullable=False),
            sa.Column("horizon_weeks", sa.Integer, nullable=False),
            sa.Column("method_id", sa.String, sa.ForeignKey("forecast_method.id"), nullable=False),
            sa.Column("actor", sa.String, nullable=False),
            sa.Column("reason", sa.Text, nullable=False),
            sa.Column("created_at", stamp, nullable=False),
            sa.Column("removed_at", stamp, nullable=True),
            sa.Column("removed_by", sa.String, nullable=False, server_default=""),
            sa.Column("removal_reason", sa.Text, nullable=False, server_default=""),
        )
        for column in ("identity_id", "horizon_weeks", "method_id", "removed_at"):
            op.create_index(f"ix_forecast_method_override_{column}", "forecast_method_override", [column])

    if "challenger_approval" not in have:
        op.create_table(
            "challenger_approval",
            sa.Column("id", sa.String, primary_key=True),
            sa.Column("method_id", sa.String, sa.ForeignKey("forecast_method.id"), nullable=False),
            sa.Column("actor", sa.String, nullable=False),
            sa.Column("reason", sa.Text, nullable=False),
            sa.Column("approved_at", stamp, nullable=False),
        )
        op.create_index("ix_challenger_approval_method_id", "challenger_approval", ["method_id"])

    if "forecast_vintage" in have:
        columns = _existing_columns(bind, "forecast_vintage")
        with op.batch_alter_table("forecast_vintage") as batch:
            if "selected_method_id" not in columns:
                batch.add_column(sa.Column("selected_method_id", sa.String, nullable=True))
            if "selection_source" not in columns:
                batch.add_column(sa.Column("selection_source", sa.String, nullable=True))
            if "review_id" not in columns:
                batch.add_column(sa.Column("review_id", sa.String, nullable=True))
        if bind.dialect.name == "postgresql":
            if "selected_method_id" not in columns:
                op.create_foreign_key("fk_forecast_vintage_selected_method_id", "forecast_vintage",
                                      "forecast_method", ["selected_method_id"], ["id"])
                op.create_index("ix_forecast_vintage_selected_method_id", "forecast_vintage", ["selected_method_id"])
            if "review_id" not in columns:
                op.create_foreign_key("fk_forecast_vintage_review_id", "forecast_vintage",
                                      "model_fit_review", ["review_id"], ["id"])
                op.create_index("ix_forecast_vintage_review_id", "forecast_vintage", ["review_id"])


def downgrade() -> None:
    bind = op.get_bind()
    have = _existing_tables(bind)
    if "forecast_vintage" in have:
        columns = _existing_columns(bind, "forecast_vintage")
        if bind.dialect.name == "postgresql":
            for name in ("fk_forecast_vintage_selected_method_id", "fk_forecast_vintage_review_id"):
                try:
                    op.drop_constraint(name, "forecast_vintage", type_="foreignkey")
                except Exception:
                    pass
            for name in ("ix_forecast_vintage_selected_method_id", "ix_forecast_vintage_review_id"):
                try:
                    op.drop_index(name, table_name="forecast_vintage")
                except Exception:
                    pass
        with op.batch_alter_table("forecast_vintage") as batch:
            for column in VINTAGE_COLUMNS:
                if column in columns:
                    batch.drop_column(column)
    # Children before parents.
    for table in ("challenger_approval", "forecast_method_override", "forecast_method_selection",
                  "model_fit_result", "model_fit_review", "forecast_method"):
        if table in have:
            op.drop_table(table)
