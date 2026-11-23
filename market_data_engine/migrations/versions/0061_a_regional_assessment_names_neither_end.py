"""A regional assessment names neither end of the journey (v1.2.10)

Masterplan C. The side rule - which end of a journey a commercial basis quotes a price at, and so
which of an identity's two geography fields carries the meaning - was kept **twice**, and the two
copies disagreed on seven terms:

    names an origin        app: EXW FAS FCA FOB
                        engine: EXW FAS FCA FOB  + EX-WORKS EXWORKS

    names a destination    app: CFR CIF CIP CPT DAP DDP DPU DELIVERED  + DEL FD
                        engine: CFR CIF CIP CPT DAP DDP DPU DELIVERED  + DDU C&F REGIONAL_MARKET

Five of the seven were latent. One was not: **REGIONAL_MARKET**. The engine's copy had it on the
destination side, arguing that a regional assessment is a price *in* a market, and the live writer
(`persistence/identity.py` -> `domain/origin.derive`) used the engine's copy, so all 31 regional
assessments in the store carry `origin NULL, destination = <their own region>` - which in this table
reads exactly like a CIF import price.

**Ruled 28 October 2026: a regional assessment names neither end.** IMARC and BusinessAnalytiq print
a price level for a region. There is no consignment and no journey, so there is no destination; and
`destination` is a journey field, not a second place to keep the geography. The proof that writing it
there misleads is the engine's own prose for these columns, `attribution_note`, which turned
`destination = Europe` into *"Delivered into Europe; the publisher does not state where the material
was made."* - false of a regional index, and deleted in the same change. Nothing is lost by saying
nothing: the region is in `market_geography`, which is where every reader already looks.

So this migration clears the destination on every identity whose basis names neither end and whose
destination says no more than its own geography does. Measured before it ran: **31 identities, all
REGIONAL_MARKET, every one with `destination = market_geography`**, 1 to 7 readings each, and 29 of
the 31 are markets Stefan has already taken off both customer pages by hand (section G).

**Nothing downstream moves.** The series key is seven fields and the comparability group six; neither
holds origin or destination, so no key is re-written and `forecast_vintage` is untouched. What does
read these columns is `MATCH_FIELDS` in the live store - which is why this has to be a migration and
not only a code change: leave the 31 rows as they are, and the next collection from IMARC would mint
a *second* identity beside each one with the destination empty. That is the fault migration 0046
cleaned up on 30 September 2026 (twelve markets held as pairs, the duplicated publisher counted twice
in the day's average), and the rule that produced it is what C removes.

Measured the same day: zero groups of identities in the store differ only in origin or destination,
and after A's merges no two identities share the seven key fields at all. So clearing a destination
cannot merge two identities into one here, and the migration does not need to.

Idempotent: a second run finds nothing left to clear.
"""
from alembic import op
import sqlalchemy as sa

revision = "0061_a_regional_assessment_names_neither_end"
down_revision = "0060_a_grade_is_a_grade_and_a_product_is_an_example"
branch_labels = None
depends_on = None

# Written out rather than imported, for the reason every migration in this chain writes its
# vocabulary out: REGIONAL_MARKET has to mean the same thing in five years whatever
# `market_basis.NAMES_NEITHER_END` says then. `tests/test_one_side_rule.py` pins the two together
# for today.
NAMES_NEITHER_END = ("REGIONAL_MARKET", "REGIONAL MARKET")


def _folded(value) -> str:
    return str(value or "").strip().casefold()


def upgrade() -> None:
    bind = op.get_bind()
    if "market_identity" not in set(sa.inspect(bind).get_table_names()):
        return
    rows = bind.execute(sa.text(
        "SELECT id, commercial_basis, market_geography, origin, destination FROM market_identity "
        "WHERE COALESCE(destination, '') <> ''")).mappings().all()
    cleared, kept = 0, []
    for row in rows:
        if str(row["commercial_basis"] or "").strip().upper() not in NAMES_NEITHER_END:
            continue
        # A destination that says something the geography does not is a publisher's own statement,
        # and this rule only ever filled silence - so it is left exactly as it was found.
        if _folded(row["destination"]) != _folded(row["market_geography"]):
            kept.append(f'{row["market_geography"]} -> {row["destination"]}')
            continue
        bind.execute(sa.text("UPDATE market_identity SET destination = NULL WHERE id = :id"),
                     {"id": row["id"]})
        cleared += 1
    print(f"0061: {cleared} regional assessments no longer claim a destination; "
          f"{len(kept)} left because the publisher stated one {kept or ''}")


def downgrade() -> None:
    """Put the region back in the destination column, which is where 0011 and the engine's old
    copy of the rule had it."""
    bind = op.get_bind()
    if "market_identity" not in set(sa.inspect(bind).get_table_names()):
        return
    for name in NAMES_NEITHER_END:
        bind.execute(sa.text(
            "UPDATE market_identity SET destination = market_geography "
            "WHERE UPPER(TRIM(commercial_basis)) = :basis AND COALESCE(destination, '') = ''"),
            {"basis": name})
