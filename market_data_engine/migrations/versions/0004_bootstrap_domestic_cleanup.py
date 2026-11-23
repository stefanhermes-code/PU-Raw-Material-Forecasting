"""re-fold DOMESTIC rows re-created by the pre-fix bootstrap file (v0.4.44)

The startup bootstrap (data/bootstrap_observations.json) still carried 33 SunSirs/Longzhong
rows labelled DOMESTIC and re-created the identity right after migration 0003 ran. The
file now says EXW; this migration folds the re-created rows back, dropping the ones that
already exist under the EXW identity.
"""
from alembic import op
import sqlalchemy as sa

from app.db.domestic_to_exw import fold_domestic_into_exw

revision = "0004_bootstrap_domestic_cleanup"
down_revision = "0003_china_domestic_is_exw"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    fold_domestic_into_exw(bind)
    # SCI99 rows collected today landed under a 'TDI 80/20' grade the page never states;
    # fold them into the grade-less China EXW identity shared by SunSirs/Longzhong/ECHEMI.
    graded = bind.execute(sa.text(
        "SELECT id FROM market_identity WHERE product_family = 'TDI' AND product_grade_or_chemistry = 'TDI 80/20' "
        "AND market_geography = 'China' AND commercial_basis = 'EXW' AND upper(currency) = 'CNY' AND observation_type = 'market_price'"
    )).scalar()
    plain = bind.execute(sa.text(
        "SELECT id FROM market_identity WHERE product_family = 'TDI' AND product_grade_or_chemistry IS NULL "
        "AND market_geography = 'China' AND commercial_basis = 'EXW' AND upper(currency) = 'CNY' AND observation_type = 'market_price'"
    )).scalar()
    if graded and plain:
        bind.execute(sa.text("UPDATE price_observation SET identity_id = :p WHERE identity_id = :g AND source_id = 'sci99_tdi'"), {"p": plain, "g": graded})
        bind.execute(sa.text("DELETE FROM market_reference_point WHERE identity_id = :g"), {"g": graded})
        bind.execute(sa.text("DELETE FROM market_identity WHERE id = :g AND id NOT IN (SELECT identity_id FROM price_observation)"), {"g": graded})


def downgrade():
    pass
