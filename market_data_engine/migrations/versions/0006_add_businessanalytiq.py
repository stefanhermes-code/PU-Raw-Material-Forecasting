"""collect the new Business Analytiq TDI source on the next start (v0.4.52)"""
from alembic import op
import sqlalchemy as sa

revision = "0006_add_businessanalytiq"
down_revision = "0005_fx_rates"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "source_registry" in sa.inspect(bind).get_table_names():
        bind.execute(sa.text(
            "UPDATE source_registry SET last_successful_collection = NULL WHERE source_id = 'businessanalytiq_tdi'"
        ))


def downgrade():
    pass
