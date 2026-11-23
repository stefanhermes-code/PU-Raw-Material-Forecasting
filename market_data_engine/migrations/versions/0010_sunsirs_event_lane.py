"""collect SunSirs' news lane so signals can finally be corroborated (v0.4.62)

SunSirs becomes the engine's second *independent* event publisher in this release. Until now
Longzhong was the only one, so no signal could ever be corroborated and every report without a named
producer was discarded unverified - which is what forced the manual promotion queue this release
removes.

`sunsirs_tdi` will not be due again today, so its news index would go unread until tomorrow and the
Market Intelligence page would still show only single-source signals. Clear its marker so the next
start collects it once, news included.
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_sunsirs_event_lane"
down_revision = "0009_recollect_for_event_lane"
branch_labels = None
depends_on = None

_SOURCES = ("sunsirs_tdi",)


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
