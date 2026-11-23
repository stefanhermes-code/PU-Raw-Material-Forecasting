"""A publisher restating a day keeps one price for it, not two.

The duplicate check in `_persist_price` matched on identity, source, date AND price, so a publisher
restating a day at a different figure inserted a second row beside the first. Consolidation takes one
value per source as the mean of that source's rows, so the revision was averaged with the number it
replaced and the governed value became a figure nobody had published.

Twenty-six publisher/date pairs were in that state, all of them a live connector re-reading the same
page later in the day: Longzhong's polyether polyol held five prints of 11 September 2026, from
11,400 to 12,700, averaging to 12,320.

Only live-connector rows are collapsed. Curated records carry `history:` or `po:` evidence ids and
are left exactly as they are: FoamPartner's polyether polyol holds two GRADES in the same month on
one identity, and KGI Thailand holds a history line and a purchase order for the same month at
different figures. Both are real questions, and neither is answered by keeping whichever row happened
to be written second.

Revision ID: 0038_a_revision_replaces_what_it_revises
Revises: 0037_derived_values_have_their_own_table
"""
from alembic import op
import sqlalchemy as sa

revision = "0038_a_revision_replaces_what_it_revises"
down_revision = "0037_derived_values_have_their_own_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # The newest row wins. `available_from` is identical across a day's re-reads, so insertion order
    # is what distinguishes them, and rowid is that order.
    doomed = bind.execute(sa.text("""
        SELECT rowid FROM price_observation
        WHERE COALESCE(evidence_id, '') NOT LIKE 'history:%'
          AND COALESCE(evidence_id, '') NOT LIKE 'po:%'
          AND rowid NOT IN (
            SELECT MAX(rowid) FROM price_observation
            WHERE COALESCE(evidence_id, '') NOT LIKE 'history:%'
              AND COALESCE(evidence_id, '') NOT LIKE 'po:%'
            GROUP BY identity_id, source_id, observation_date
          )
    """)).fetchall()
    if not doomed:
        return
    bind.execute(
        sa.text("DELETE FROM price_observation WHERE rowid IN :ids").bindparams(
            sa.bindparam("ids", value=tuple(row[0] for row in doomed), expanding=True))
    )


def downgrade() -> None:
    # A superseded price is not recoverable from here, and should not be: the row it replaced was
    # withdrawn by the publisher. `raw_evidence` still holds every page that was ever fetched.
    pass
