"""A price assessment carries its range, not just its middle (v0.4.251)

Stefan, 25 September 2026. Every ICIS assessment is published as a low, a high and a mid, and the
store had one column for the value. Importing only the mid would have thrown away two thirds of
41,483 numbers - and the two thirds that say how firm the assessment was.

The range is not decoration. A week assessed at 3,500-4,000 EUR/t and a week assessed at 3,740-3,760
have the same middle and mean entirely different things about how settled that market was. That is
the same quantity a forecast band tries to express, which makes the published range the honest thing
to calibrate a band against rather than a spread invented from the residuals.

`price_low` and `price_high` sit on exactly the basis `price` sits on - same currency, same unit,
same tax treatment - so anything that converts `price` must convert all three or none. Both are
nullable and stay null for every source that prints a single number, which is most of them. Null
means the publisher gave no range, never that the range was zero.
"""
from alembic import op
import sqlalchemy as sa

revision = "0044_an_assessment_carries_its_range"
down_revision = "0043_the_european_delivered_purchases_are_a_dap_market"
branch_labels = None
depends_on = None

TABLE = "price_observation"
COLUMNS = ("price_low", "price_high")


def _existing(bind) -> set[str]:
    inspector = sa.inspect(bind)
    if TABLE not in set(inspector.get_table_names()):
        return set()
    return {column["name"] for column in inspector.get_columns(TABLE)}


def upgrade():
    bind = op.get_bind()
    present = _existing(bind)
    if not present:
        print("[0044] no price_observation table; skipped")
        return
    for name in COLUMNS:
        if name in present:
            print(f"[0044] {name} already present; skipped")
            continue
        op.add_column(TABLE, sa.Column(name, sa.Float(), nullable=True))
        print(f"[0044] added {TABLE}.{name}")


def downgrade():
    bind = op.get_bind()
    present = _existing(bind)
    for name in COLUMNS:
        if name in present:
            op.drop_column(TABLE, name)
