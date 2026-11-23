"""source acquisition repair (v0.4.44)

* ECHEMI now reads through a real off-screen Edge (app.sources.edge_cdp); SCI99 reads the
  page's own public JSON table; ChemAnalyst is parsed per named country as quarterly
  averages; PUdaily / Baiinfo / Intratec are monitor-only.
* Data repair: the pre-v0.4.44 ChemAnalyst parser stored another country's quarterly
  figure as a China market price. Those rows are removed.
* Scheduling repair: the reworked sources are marked never-collected so the next startup
  collection picks them up today instead of tomorrow (DAILY_CHECK cadence).
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_source_acquisition_repair"
down_revision = "0001_core_entities"
branch_labels = None
depends_on = None

_RECOLLECT = ("echemi_tdi", "sci99_tdi", "chemanalyst_tdi", "pudaily_tdi", "baiinfo_tdi", "intratec_tdi")


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "price_observation" in tables:
        bind.execute(sa.text("DELETE FROM price_observation WHERE source_id = 'chemanalyst_tdi'"))
    if "source_registry" in tables:
        bind.execute(
            sa.text("UPDATE source_registry SET last_successful_collection = NULL WHERE source_id IN :ids").bindparams(
                sa.bindparam("ids", value=list(_RECOLLECT), expanding=True)
            )
        )


def downgrade():
    # Data repair is not reversible; nothing to undo structurally.
    pass
