"""govern every China domestic CNY market price as EXW (v0.4.44)

Decision (Stefan, 2026-09-08): "DOMESTIC" is not an Incoterm. All China domestic
tax-inclusive market prices in CNY/t are governed as EXW. See app/db/domestic_to_exw.py.
"""
from alembic import op

from app.db.domestic_to_exw import fold_domestic_into_exw

revision = "0003_china_domestic_is_exw"
down_revision = "0002_source_acquisition_repair"
branch_labels = None
depends_on = None


def upgrade():
    fold_domestic_into_exw(op.get_bind())


def downgrade():
    pass
