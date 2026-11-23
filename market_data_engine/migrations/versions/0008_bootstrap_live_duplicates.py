"""remove rows the bootstrap file duplicated against live collection

`bootstrap_file` deduped on its own `bootstrap:<hash>` evidence id, so once live collection covered a
day the bootstrap file also carries, the same observation was stored twice. Keep the live-collected
row (a real evidence hash) in preference to the bootstrap copy.
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_bootstrap_live_duplicates"
down_revision = "0007_dedupe_price_observations"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "price_observation" not in sa.inspect(bind).get_table_names():
        return
    # Drop bootstrap copies that have a live-collected twin.
    bind.execute(sa.text("""
        DELETE FROM price_observation
        WHERE evidence_id LIKE 'bootstrap:%'
          AND EXISTS (
              SELECT 1 FROM price_observation live
              WHERE live.source_id = price_observation.source_id
                AND live.identity_id = price_observation.identity_id
                AND live.observation_date = price_observation.observation_date
                AND live.price = price_observation.price
                AND live.evidence_id NOT LIKE 'bootstrap:%'
          )
    """))
    # Belt and braces: any remaining exact duplicate keeps its earliest row.
    bind.execute(sa.text("""
        DELETE FROM price_observation
        WHERE id NOT IN (
            SELECT MIN(id) FROM price_observation
            GROUP BY source_id, identity_id, observation_date, price
        )
    """))


def downgrade():
    pass
