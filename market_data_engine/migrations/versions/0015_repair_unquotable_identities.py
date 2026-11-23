"""repair identities a customer could never be quoted on (v0.4.87)

Three things found while asking why the application opened on "TDI · East China".

**An identity with no commercial basis.** SunSirs produced East China twice - once as EXW and once
with nothing at all - and the empty one carried the same two prices. An identity without a basis
cannot be sold and is always a duplicate of the sibling that has one, so its observations move across
and it is removed.

**Observations with no observation_date.** Two East China rows had none, which is why the page showed
a reference dated 1 September while the newest actual reading was 24 July. Where the publication date
is known it is used; a reading with neither date is evidence of nothing and is removed.

**A free-text incoterm.** `htc_legacy_pmdi_research` carried "China domestic benchmark/market avg" in
the field that holds an Incoterm, and it would have appeared in the customer's Incoterm list the day
PMDI is switched on. It is a regional market average, which the engine already governs as
REGIONAL_MARKET.
"""
from alembic import op
import sqlalchemy as sa

revision = "0015_repair_unquotable_identities"
down_revision = "0014_reference_series"
branch_labels = None
depends_on = None

UNQUOTABLE = ("", "UNKNOWN", "NONE", "NULL", "NOT SPECIFIED", "NOT_SPECIFIED", "N/A")
FREE_TEXT_BASIS = "China domestic benchmark/market avg"
GOVERNED_BASIS = "REGIONAL_MARKET"

_TWIN = ("product_family", "product_grade_or_chemistry", "market_geography",
         "packaging", "currency", "unit", "observation_type")


def _rows(bind, sql, params=None):
    return list(bind.execute(sa.text(sql), params or {}).mappings())


def upgrade() -> None:
    bind = op.get_bind()

    # 1. An observation with no date, where the publication date is known.
    bind.execute(sa.text(
        "UPDATE price_observation SET observation_date = publication_date "
        "WHERE observation_date IS NULL AND publication_date IS NOT NULL"
    ))
    bind.execute(sa.text("DELETE FROM price_observation WHERE observation_date IS NULL"))

    # 2. Free text where an Incoterm belongs.
    bind.execute(
        sa.text("UPDATE market_identity SET commercial_basis = :governed WHERE commercial_basis = :free"),
        {"governed": GOVERNED_BASIS, "free": FREE_TEXT_BASIS},
    )

    # 3. Identities with no basis: move their observations onto the twin that has one, then remove.
    placeholders = ", ".join(f":u{i}" for i in range(len(UNQUOTABLE)))
    params = {f"u{i}": value for i, value in enumerate(UNQUOTABLE)}
    unquotable = _rows(bind, (
        "SELECT * FROM market_identity WHERE commercial_basis IS NULL "
        f"OR TRIM(UPPER(COALESCE(commercial_basis,''))) IN ({placeholders})"), params)

    for identity in unquotable:
        match = " AND ".join(
            f"{column} IS :v_{column}" if identity[column] is None else f"{column} = :v_{column}"
            for column in _TWIN
        )
        values = {f"v_{column}": identity[column] for column in _TWIN}
        twin = _rows(bind, (
            f"SELECT id FROM market_identity WHERE {match} AND id <> :self "
            "AND commercial_basis IS NOT NULL AND TRIM(commercial_basis) <> '' "
            "ORDER BY id LIMIT 1"), {**values, "self": identity["id"]})
        if not twin:
            continue                       # no sibling to inherit it; leave the row alone
        target = twin[0]["id"]

        # Never create a duplicate reading on the twin: the same evidence id is the same observation.
        held = {row["evidence_id"] for row in _rows(
            bind, "SELECT evidence_id FROM price_observation WHERE identity_id = :id", {"id": target})}
        for observation in _rows(
                bind, "SELECT id, evidence_id FROM price_observation WHERE identity_id = :id",
                {"id": identity["id"]}):
            if observation["evidence_id"] in held:
                bind.execute(sa.text("DELETE FROM price_observation WHERE id = :id"),
                             {"id": observation["id"]})
            else:
                bind.execute(
                    sa.text("UPDATE price_observation SET identity_id = :target WHERE id = :id"),
                    {"target": target, "id": observation["id"]})
        bind.execute(sa.text("DELETE FROM market_reference_point WHERE identity_id = :id"),
                     {"id": identity["id"]})
        bind.execute(sa.text("DELETE FROM market_identity WHERE id = :id"), {"id": identity["id"]})


def downgrade() -> None:
    # Re-splitting an identity that should never have existed, and re-blanking dates, would put back
    # exactly the state this repaired.
    pass
