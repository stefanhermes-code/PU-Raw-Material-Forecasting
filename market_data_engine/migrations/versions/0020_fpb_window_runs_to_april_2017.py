"""FoamPartner China: the history runs to April 2017, not February (v0.4.141)

0018 recorded the FPB window as January 2014 to February 2017 and marked the source CLOSED. February
was where the FPB workbook stopped, not where the buying stopped. The KGI procurement worksheet
carries a block of FPB's own China prices - "Preise FPB (RMB/kg)" - that agrees with the workbook to
the cent on all 95 overlapping months and then runs two months further: March and April 2017 for
T80, March to May for the polyols.

Those months are now loaded, so the note that tells the freshness checks what CLOSED means has to
say the right end date. The source stays closed and inactive; only the window changes.
"""
from alembic import op
import sqlalchemy as sa

revision = "0020_fpb_window_runs_to_april_2017"
down_revision = "0019_kgi_ledger_stock_issues"
branch_labels = None
depends_on = None

SOURCE = "foampartner_china"
NOTE = ("Purchase history runs January 2014 to April 2017 and is CLOSED. Changzhou plant, Chinese "
        "origin, delivered China DAP. Transaction prices, not market assessments: stored as "
        "transaction_price so they never reach a customer-facing reference.")


def upgrade():
    bind = op.get_bind()
    if "source_registry" not in set(sa.inspect(bind).get_table_names()):
        return
    bind.execute(sa.text("UPDATE source_registry SET notes = :note WHERE source_id = :id"),
                 {"note": NOTE, "id": SOURCE})


def downgrade():
    pass
