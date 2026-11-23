"""One active selection per series and horizon (v0.5.65)

WP4 gate: two Model Fit Reviews committing at once could each deactivate the old selection and insert
their own, leaving two active rows. A unique partial index makes the store refuse the second; that
review then fails as a whole and the first one's selections stand (Review Focus 4). Additive,
idempotent, reversible.
"""
from alembic import op
import sqlalchemy as sa

revision = "0056_one_active_selection_per_pair"
down_revision = "0055_a_review_commits_only_when_switched_on"
branch_labels = None
depends_on = None

NAME = "ux_forecast_method_selection_active"


def upgrade() -> None:
    bind = op.get_bind()
    have = {i["name"] for i in sa.inspect(bind).get_indexes("forecast_method_selection")}
    if NAME not in have:
        # Should two active rows for one pair already exist, the newest stays active and the older
        # become history (superseded now) - the index could not be built otherwise (fourth review).
        rows = bind.execute(sa.text(
            "SELECT id, identity_id, horizon_weeks FROM forecast_method_selection "
            "WHERE active ORDER BY identity_id, horizon_weeks, selected_at DESC")).fetchall()
        seen = set()
        for row_id, identity_id, horizon in rows:
            if (identity_id, horizon) in seen:
                bind.execute(sa.text("UPDATE forecast_method_selection SET active = :f, superseded_at = CURRENT_TIMESTAMP "
                                     "WHERE id = :i"), {"f": False, "i": row_id})
            seen.add((identity_id, horizon))
        op.create_index(NAME, "forecast_method_selection", ["identity_id", "horizon_weeks"], unique=True,
                        sqlite_where=sa.text("active = 1"), postgresql_where=sa.text("active"))


def downgrade() -> None:
    bind = op.get_bind()
    have = {i["name"] for i in sa.inspect(bind).get_indexes("forecast_method_selection")}
    if NAME in have:
        op.drop_index(NAME, table_name="forecast_method_selection")
