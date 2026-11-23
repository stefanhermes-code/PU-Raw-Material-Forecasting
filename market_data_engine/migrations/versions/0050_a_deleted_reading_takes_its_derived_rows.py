"""A deleted reading takes its derived rows with it (v0.5.39)

0049 removed 6,247 Comtrade unit values that were computed over less than 1,000 t. It deleted the
price rows and nothing else. SQLite does not enforce the foreign keys the models declare, so the
currency conversions of those readings (price_in_currency) and the reference points computed from
them (market_reference_point) stayed behind, pointing at rows that no longer exist: 24,945 and 144
of them on 17 October 2026.

The Supabase store enforces the keys, and the load of 17 October 2026 left those rows out. This
migration brings a SQLite store to the same state, so that the two never differ, and is a no-op
where there is nothing dangling. It uses plain SQL that both databases accept.
"""
from alembic import op
import sqlalchemy as sa

revision = "0050_a_deleted_reading_takes_its_derived_rows"
down_revision = "0049_a_unit_value_needs_a_market_quantity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(
        "DELETE FROM price_in_currency WHERE observation_id NOT IN (SELECT id FROM price_observation)"))
    bind.execute(sa.text(
        "DELETE FROM market_reference_point WHERE identity_id NOT IN (SELECT id FROM market_identity)"))


def downgrade() -> None:
    """Deliberately nothing: the rows pointed at readings that are gone."""
