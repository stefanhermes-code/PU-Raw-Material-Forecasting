"""ECB daily FX reference rates for display-currency conversion (v0.4.45)"""
from alembic import op
import sqlalchemy as sa

revision = "0005_fx_rates"
down_revision = "0004_bootstrap_domestic_cleanup"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "fx_rate" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "fx_rate",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("rate_date", sa.DateTime(timezone=True), index=True),
        sa.Column("base", sa.String(), nullable=False),
        sa.Column("currency", sa.String(), index=True),
        sa.Column("rate", sa.Float(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True)),
    )


def downgrade():
    op.drop_table("fx_rate")
