"""remove duplicate price observations and collect the Mysteel page (v0.4.54)

Until now a price observation was deduplicated on the evidence hash, so a publisher's
rolling table (Longzhong's 7-day window) re-inserted identical rows on every collection
run. Keep the earliest row per (source, identity, observation_date, price).
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_dedupe_price_observations"
down_revision = "0006_add_businessanalytiq"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "price_observation" not in tables:
        return
    bind.execute(sa.text("""
        DELETE FROM price_observation
        WHERE id NOT IN (
            SELECT MIN(id) FROM price_observation
            GROUP BY source_id, identity_id, observation_date, price
        )
    """))
    if "source_registry" in tables:
        bind.execute(sa.text(
            "UPDATE source_registry SET last_successful_collection = NULL WHERE source_id = 'longzhong_tdi'"
        ))


def downgrade():
    pass
