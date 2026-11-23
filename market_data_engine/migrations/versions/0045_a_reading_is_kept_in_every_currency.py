"""One reading, kept in every currency it can be read in (v0.4.263)

Stefan, 27 September 2026, after the Current Market card showed USD 17,533 for TDI China. That
number was SunSirs' yuan print for 25 September. The market was 2,617 dollars. A yuan price had
been drawn on a dollar axis because the page fetches the ECB rates in a separate call from the
prices, that call timed out while the engine was warming, and the empty rate table it fell back to
was then kept for the rest of the session. Every yuan reading passed through unconverted and
nothing on the page knew.

Stefan: convert it the moment the datapoint is created, store the result, and have the page read
the one the selector asks for. Nothing is computed while a page is drawing, so nothing can be
half-computed.

His second correction shapes this table. A price in another currency is not another market - same
product, same terms, same timeline, a different unit of account. So it does not become an identity
of its own, which is also what keeps it out of the line-drawing rule: the chart groups readings by
terms, packaging and region and pays no attention to currency, so a converted copy filed as its own
identity would have been averaged with the reading it came from. On 25 September that would have
drawn 10,075 - neither price, on a line that looks perfectly normal.

Each row carries the rate and the date it was quoted on, so a conversion can be rechecked against
the ECB without rerunning anything. A reading is also stored in its own currency, at a rate of 1,
so a reader never has to special-case the currency a market happens to be assessed in.

Where no rate exists - the SE Asia weeks from 2010 to 2013, which predate the earliest fixing we
hold - there is no row. A missing row is the honest answer and the page draws nothing, which is
what should have happened on the 25th.
"""
from alembic import op
import sqlalchemy as sa

revision = "0045_a_reading_is_kept_in_every_currency"
down_revision = "0044_an_assessment_carries_its_range"
branch_labels = None
depends_on = None

TABLE = "price_in_currency"


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if TABLE in set(inspector.get_table_names()):
        print(f"[0045] {TABLE} already present; skipped")
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("observation_id", sa.String(),
                  sa.ForeignKey("price_observation.id"), nullable=False, index=True),
        sa.Column("currency", sa.String(), nullable=False, index=True),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("price_low", sa.Float(), nullable=True),
        sa.Column("price_high", sa.Float(), nullable=True),
        # Units of `currency` per one unit of the currency the reading is assessed in, and the day
        # the ECB quoted it. A weekend or a holiday takes the last quote before it; nothing is
        # interpolated, because a rate is something that was quoted on a date.
        sa.Column("fx_rate", sa.Float(), nullable=False),
        sa.Column("fx_rate_date", sa.Date(), nullable=False),
        sa.UniqueConstraint("observation_id", "currency", name="uq_price_in_currency"),
    )
    print(f"[0045] created {TABLE}")


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if TABLE in set(inspector.get_table_names()):
        op.drop_table(TABLE)
