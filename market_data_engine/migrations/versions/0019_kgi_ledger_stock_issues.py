"""KGI Thailand: the ledger rows that were never prices (v0.4.133)

The KGI history came out of two SAP workbooks, `TDI Pricing since 2021 - BASF.xlsx` and
`- Wanhua.xlsx`. Both are inventory ledger extracts, and two different things share the
"Price after Disc." column.

A row whose counterparty is the supplier - NAGASE, BASF Thailand, Chemical Brothers - is a purchase:
a price that was negotiated and paid. A row whose counterparty is the internal stock account
"Raw Materials  Foam" is an issue of stock to production, and the figure beside it is the moving
average carrying value of whatever is still in the tank. The extractor read both as prices.

What that produced: the Wanhua series showed 64.208 baht in thirteen consecutive months, July 2021
to July 2022. There was one purchase - the July 2021 order, 64.200 - and the next twelve rows were
that tank being drawn down at cost. Thirteen of the series' eighteen points were one price. Across
the two TDI sheets 233 of 295 rows were stock issues; on the Shell polyol sheet, 209 of 389.

`scripts/extract_historical_prices.py` now skips them and `data/research/historical_rm_native.csv`
has been rebuilt: 61 KGI rows become 30. This clears what the old file put in the store so the
corrected file loads over an empty lane rather than beside it - the loader is idempotent by
evidence id, and an evidence id it has already seen is one it leaves alone.

The purchase orders go with them. They are keyed by PO number and would be read as already present,
which would skip the supersession pass that now lets an order replace the receipt posting recording
the same trade. Deleted here, both lanes rebuild in order on the next start.

Nothing is lost that is not on a document: every row this removes is re-derived from the workbooks
and the signed PDFs at startup.
"""
from alembic import op
import sqlalchemy as sa

revision = "0019_kgi_ledger_stock_issues"
down_revision = "0018_kgi_one_series_per_supplier"
branch_labels = None
depends_on = None

SOURCE = "kgi_thailand"


def upgrade():
    bind = op.get_bind()
    if "price_observation" not in set(sa.inspect(bind).get_table_names()):
        return

    removed = bind.execute(
        sa.text("DELETE FROM price_observation WHERE source_id = :source"),
        {"source": SOURCE},
    ).rowcount

    # An identity left with no observations is a series that no longer exists. Leave the row rather
    # than delete it: the loaders re-create the ones that are still real within the same start, and
    # a merged identity from 0018 is the row the purchase orders are about to land on again.
    if removed:
        print(f"0019_kgi_ledger_stock_issues: {removed} KGI observations cleared for reload")


def downgrade():
    pass
