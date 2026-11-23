"""An observation we cannot explain is a case, not a reject (v0.5.42)

Stefan, 17 October 2026: "If it is unattributed you do not throw away the observation. You dig
deeper... You need to use the 5 times why method before you give up. Not one enquiry." And on the
shape: "not mechanically, meaning now you use it for this plant price increase, but it needs to
work for any unknown item." And on the map that answers it: "chemical, feedstocks, geography,
capacity, plants. Isnt that the map you need?"

Three tables.

`investigation_case` holds the observation verbatim, in its own language, with the gap named
precisely - ACTOR, AMOUNT, DATE, PLACE, CAUSE, SCOPE or MARKET - because the whole chain of
questions is generated from which kind of thing is missing. There is deliberately no REJECTED
state. A case ends RESOLVED, NARROWED, or EXPLAINED, and the last of those says WHY the answer does
not exist, which is a collection requirement rather than a failure.

`investigation_enquiry` holds the chain, written out as pending the moment the case opens, so the
next question exists before anyone goes looking for it. A step with no capability behind it is
UNANSWERABLE from the start - that is honest, and it is where a collection requirement comes from.

`investigation_candidate` holds the suspects, which come from our own map. "How many northwest TDI
plants are there?" - the roster answers it with Wanhua Xinjiang at 200 kt, Gansu Yinguang Juyin at
150 and Lanzhou Petrochemical at 50, so a case opens with three named plants instead of a blank.

Nothing is removed and nothing existing is altered.
"""
from alembic import op
import sqlalchemy as sa

revision = "0051_an_observation_we_cannot_explain_is_a_case"
# Behind 0050, which cleaned up after 0049: that migration deleted 6,247 Comtrade
# price rows and left 24,945 currency conversions and 144 reference points pointing
# at rows that no longer existed, because SQLite does not enforce the foreign keys
# the models declare. This one only creates tables.
down_revision = "0050_a_deleted_reading_takes_its_derived_rows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "investigation_case",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("gap", sa.String(), nullable=False, index=True),
        sa.Column("observed", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_id", sa.String(), nullable=True, index=True),
        sa.Column("evidence_id", sa.String(), sa.ForeignKey("raw_evidence.id"), nullable=True),
        sa.Column("origin_kind", sa.String(), nullable=False),
        sa.Column("origin_ref", sa.String(), nullable=True),
        sa.Column("chemical", sa.String(), nullable=True, index=True),
        sa.Column("geography", sa.String(), nullable=True, index=True),
        sa.Column("state", sa.String(), nullable=False, server_default="OPEN", index=True),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data", sa.JSON(), nullable=True),
    )
    op.create_table(
        "investigation_enquiry",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("case_id", sa.String(), sa.ForeignKey("investigation_case.id"), nullable=False,
                  index=True),
        sa.Column("question", sa.String(), nullable=False),
        sa.Column("step", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("asked", sa.Text(), nullable=False),
        sa.Column("capability", sa.String(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=False, server_default="PENDING", index=True),
        sa.Column("found", sa.Text(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("ran_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "investigation_candidate",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("case_id", sa.String(), sa.ForeignKey("investigation_case.id"), nullable=False,
                  index=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("owner", sa.String(), nullable=True),
        sa.Column("chemical", sa.String(), nullable=True),
        sa.Column("geography", sa.String(), nullable=True),
        sa.Column("province", sa.String(), nullable=True),
        sa.Column("capacity_kt", sa.Float(), nullable=True),
        sa.Column("state", sa.String(), nullable=False, server_default="CANDIDATE", index=True),
        sa.Column("why", sa.Text(), nullable=True),
        sa.Column("from_source", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("investigation_candidate")
    op.drop_table("investigation_enquiry")
    op.drop_table("investigation_case")
