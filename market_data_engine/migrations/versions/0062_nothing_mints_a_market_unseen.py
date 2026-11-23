"""Nothing mints a market unseen: the quarantine, and the stock it would have caught (v1.2.11)

Masterplan D. `ensure_identity` was get-or-create on nine fields and nothing stood between a
publisher's printed string and a new market. Most of the *folds* arrived with A - MT for every
tonne spelling (0057), four price kinds for seven (0058), a grade code or nothing (0060), one
spelling of silence for packaging, a province folded to its country. What did not exist was the
**gate** and somewhere for a refused reading to go.

Three things move.

**1. `quarantined_reading`** - one row per (source, field, value), not per reading, because a
document read wrongly is read wrongly on every row and a connector sending an unknown label sends it
daily. The row keeps the publisher's own word, the reason, the document and page it came from, one
whole example reading, how many readings it has stopped, and the first and last date it was seen.
It is a queue: `settled_at` marks a refusal dealt with once the value is named or the connector
fixed.

**2. HDI and IPDI become families the application names.** Both are aliphatic isocyanates, both are
PU raw materials, both arrived in the October 2026 document imports with two readings each - and
both sat in the store as a family nothing had a name for, so every page said "Chemical not named in
the application" and no screen showed them. Stefan's standing rule of 26 September 2026: *"The more
chemicals we can add that are PU Raw Materials the better."* Nothing in the store changes; the
vocabulary does, and this migration records it because the vocabulary is now what the door enforces.

**3. `USA` becomes `United States`.** Two identities, two readings, one from
`import_procurement_resource` in August 2026 - the same place written twice, which is two markets.
`United States` is the spelling the rest of the store uses (24 identities) and the one in the
geography list the selectors are ordered by. Measured before this ran: no `United States` identity
shares the other eight key fields with either `USA` row, so nothing merges and no key is re-written.

**What is NOT repaired here, and why.** Four identities and five readings carry `MDI` as a family -
the store prices PMDI and PURE_MDI separately and "MDI" does not say which. They are left exactly
where they are, because filing them under either would be inventing the answer and blanking the
family would lose the reading. From now on such a reading is refused at the door and appears in the
quarantine with the reason; the four already in the store are listed by this migration's output so
they are not forgotten, and every page already says why they are not published.

**What the gate would have refused in today's store, measured 28 October 2026 over 343 identities
and 35,462 readings: `MDI` and nothing else.** Every unit is already `MT` and every price kind is
already one of the four, because A's migrations folded them. So the quarantine starts empty but for
what the next collection brings, which is the right shape for it - it is the door, not a cleanup.

Also left: the eight identities on `East China`, `North China` and `South China`, 790 readings of
ICIS's three separate Chinese regional assessments. `national()` folds those labels to China on
write, so nothing new can join them - but they are not the SunSirs case the fold was written for
(one publisher printing "China" on most days and "East China" on a few). ICIS assesses three
regions, with different values and different date ranges, and folding them would average three
assessments into one series. That is a ruling of its own and it belongs with F, where the gluing
rules live; it is recorded in the plan as a known defect rather than guessed at here.

Idempotent: a second run finds the table present, no `USA` left, and nothing to say.
"""
from alembic import op
import sqlalchemy as sa

revision = "0062_nothing_mints_a_market_unseen"
down_revision = "0061_a_regional_assessment_names_neither_end"
branch_labels = None
depends_on = None

# Written out rather than imported, as every migration in this chain writes its vocabulary out.
_WAS, _IS = "USA", "United States"
_AMBIGUOUS = ("MDI",)


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _create_the_quarantine(bind, tables: set[str]) -> bool:
    if "quarantined_reading" in tables:
        return False
    op.create_table(
        "quarantined_reading",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("source_id", sa.String, nullable=False, server_default=""),
        sa.Column("field", sa.String, nullable=False),
        sa.Column("value", sa.String, nullable=False, server_default=""),
        sa.Column("reason", sa.Text, nullable=False, server_default=""),
        sa.Column("example", sa.JSON, nullable=True),
        sa.Column("document", sa.Text, nullable=False, server_default=""),
        sa.Column("page", sa.String, nullable=False, server_default=""),
        sa.Column("readings", sa.Integer, nullable=False, server_default="0"),
        sa.Column("first_seen", sa.String, nullable=False, server_default=""),
        sa.Column("last_seen", sa.String, nullable=False, server_default=""),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
    )
    # One row per (source, field, value) is the whole design, so the database holds it rather than
    # the writer remembering to.
    op.create_index("ix_quarantined_reading_refusal", "quarantined_reading",
                    ["source_id", "field", "value"], unique=True)
    return True


def _one_spelling_of_one_country(bind) -> tuple[int, int]:
    """`USA` -> `United States`, merging into an existing row where one matches."""
    rows = bind.execute(sa.text(
        "SELECT id, product_family, product_grade_or_chemistry, origin, destination, "
        "commercial_basis, packaging, unit, observation_type FROM market_identity "
        "WHERE market_geography = :was"), {"was": _WAS}).mappings().all()
    moved, merged = 0, 0
    for row in rows:
        same = bind.execute(sa.text(
            "SELECT id FROM market_identity WHERE market_geography = :now "
            "AND product_family = :family "
            "AND COALESCE(product_grade_or_chemistry,'') = COALESCE(:grade,'') "
            "AND COALESCE(origin,'') = COALESCE(:origin,'') "
            "AND COALESCE(destination,'') = COALESCE(:destination,'') "
            "AND COALESCE(commercial_basis,'') = COALESCE(:basis,'') "
            "AND COALESCE(packaging,'') = COALESCE(:packaging,'') "
            "AND COALESCE(unit,'') = COALESCE(:unit,'') "
            "AND COALESCE(observation_type,'') = COALESCE(:kind,'')"),
            {"now": _IS, "family": row["product_family"],
             "grade": row["product_grade_or_chemistry"], "origin": row["origin"],
             "destination": row["destination"], "basis": row["commercial_basis"],
             "packaging": row["packaging"], "unit": row["unit"],
             "kind": row["observation_type"]}).scalar()
        if same is None:
            bind.execute(sa.text(
                "UPDATE market_identity SET market_geography = :now, "
                "destination = CASE WHEN destination = :was THEN :now ELSE destination END, "
                "origin = CASE WHEN origin = :was THEN :now ELSE origin END WHERE id = :id"),
                {"now": _IS, "was": _WAS, "id": row["id"]})
            moved += 1
            continue
        for table in ("price_observation", "derived_value", "forecast_method_override",
                      "forecast_method_selection", "governed_forecast_run", "model_fit_result"):
            bind.execute(sa.text(
                f"UPDATE {table} SET identity_id = :keep WHERE identity_id = :dead"),
                {"keep": same, "dead": row["id"]})
        bind.execute(sa.text("DELETE FROM market_reference_point WHERE identity_id = :dead"),
                     {"dead": row["id"]})
        bind.execute(sa.text("DELETE FROM market_identity WHERE id = :dead"), {"dead": row["id"]})
        merged += 1
    return moved, merged


def _what_is_left_ambiguous(bind) -> list[str]:
    said = []
    for family in _AMBIGUOUS:
        rows = bind.execute(sa.text(
            "SELECT COUNT(DISTINCT i.id) AS identities, COUNT(o.id) AS readings "
            "FROM market_identity i LEFT JOIN price_observation o ON o.identity_id = i.id "
            "WHERE i.product_family = :family"), {"family": family}).mappings().first()
        if rows and rows["identities"]:
            said.append(f'{family}: {rows["identities"]} identities, {rows["readings"]} readings')
    return said


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)
    made = _create_the_quarantine(bind, tables)
    if "market_identity" not in tables:
        print(f"0062: quarantine table {'created' if made else 'already present'}")
        return
    moved, merged = _one_spelling_of_one_country(bind)
    left = _what_is_left_ambiguous(bind)
    print(f"0062: quarantine table {'created' if made else 'already present'}; "
          f"{moved} identities moved from {_WAS} to {_IS}, {merged} merged into one that was "
          f"already there; left for a reader to name - {'; '.join(left) or 'nothing'}")


def downgrade() -> None:
    """The quarantine goes. `USA` is not restored: it was a misspelling of one country, and
    writing it back would mint the second market again."""
    bind = op.get_bind()
    if "quarantined_reading" in _tables(bind):
        op.drop_index("ix_quarantined_reading_refusal", table_name="quarantined_reading")
        op.drop_table("quarantined_reading")
