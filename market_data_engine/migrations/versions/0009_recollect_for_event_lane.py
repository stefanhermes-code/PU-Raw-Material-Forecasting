"""re-collect the Longzhong/Mysteel page so the event lane produces its first signals (v0.4.59)

The event lane and the producer-signal export shipped after the day's collection had already run, so
`longzhong_tdi` was not due again and the Mysteel headline streams were never read. Clear its marker
so the next start collects it once more. `longzhong_pmdi` / `longzhong_polyol` are cleared for the same
reason: they now read their price from that same block.
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_recollect_for_event_lane"
down_revision = "0008_bootstrap_live_duplicates"
branch_labels = None
depends_on = None

_SOURCES = ("longzhong_tdi", "longzhong_pmdi", "longzhong_polyol")


def upgrade():
    bind = op.get_bind()
    if "source_registry" not in sa.inspect(bind).get_table_names():
        return
    bind.execute(
        sa.text("UPDATE source_registry SET last_successful_collection = NULL WHERE source_id IN :ids")
        .bindparams(sa.bindparam("ids", value=list(_SOURCES), expanding=True))
    )


def downgrade():
    pass
