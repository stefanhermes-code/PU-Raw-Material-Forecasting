"""Delivered is DAP, a placeholder is not an origin, and a reading is held once (v0.4.271)

Stefan, 23 September 2026, ruled that a delivered purchase price is DAP. The DAP identities were
written and the DELIVERED ones were left in place, so every European purchase series was held twice
with the same dates and the same prices - 21 identities, 732 readings, one duplicate for every
reading. Separately, two spellings of "origin not stated" were both in use, NULL and the literal word
UNKNOWN, so twelve markets were held as two identities each. Origin is not part of a line's key, so
both copies fell into the same bucket and the duplicated publisher counted twice in the average:
European TDI was shown 13 USD/MT high in August 2026 and 13 low in September, turning a 40 USD fall
into a 67 USD fall.

Stefan, 30 September 2026: de-duplicate it.

This migration does three things and nothing else. No reading is corrected, re-priced or re-dated;
only copies are removed.

  1. DELIVERED becomes DAP, which is the term he ruled.
  2. A placeholder in origin or destination becomes NULL, so "not stated" has one spelling.
  3. Identities that are then identical are folded into one, and where a market holds two readings
     from one publisher for one day at one price, one of them is kept.

The two places that wrote these copies are closed in the same version: `ensure_identity` clears a
placeholder before it matches, so UNKNOWN can never be stored as an origin again, and the purchase
loader files an unstated term as DAP rather than DELIVERED, so it finds the rows it wrote last time.
"""
from alembic import op
import sqlalchemy as sa

revision = "0046_one_identity_per_market_and_one_reading_per_day"
down_revision = "0045_a_reading_is_kept_in_every_currency"
branch_labels = None
depends_on = None

MEANS_NOTHING = ("", "UNKNOWN", "NONE", "N/A", "NA", "NOT SPECIFIED", "NOT_SPECIFIED", "UNSPECIFIED")

MATCH = ("product_family", "product_grade_or_chemistry", "origin", "destination",
         "market_geography", "commercial_basis", "packaging", "currency", "unit",
         "observation_type")


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if not {"market_identity", "price_observation"} <= tables:
        return
    touched: set[str] = set()

    # 1. Delivered is DAP.
    renamed = bind.execute(sa.text(
        "UPDATE market_identity SET commercial_basis = 'DAP' WHERE commercial_basis = 'DELIVERED'"
    )).rowcount

    # 2. A placeholder is not an origin.
    holes = ", ".join(":p%d" % i for i in range(len(MEANS_NOTHING)))
    params = {"p%d" % i: value for i, value in enumerate(MEANS_NOTHING)}
    cleared = 0
    for column in ("origin", "destination"):
        cleared += bind.execute(sa.text(
            "UPDATE market_identity SET %s = NULL WHERE upper(trim(coalesce(%s, ''))) IN (%s)"
            % (column, column, holes)), params).rowcount

    # 3. One identity per market.
    keys = ", ".join(MATCH)
    groups = list(bind.execute(sa.text(
        "SELECT group_concat(id, '|') FROM market_identity GROUP BY %s HAVING count(*) > 1" % keys)))
    folded = 0
    for (joined,) in groups:
        ids = joined.split("|")
        # The identity with the most readings keeps the market; ties go to the lowest id, so the
        # result does not depend on the order the rows came back in.
        counts = {i: bind.execute(sa.text(
            "SELECT count(*) FROM price_observation WHERE identity_id = :i"), {"i": i}).scalar()
            for i in ids}
        keep = sorted(ids, key=lambda i: (-counts[i], i))[0]
        losers = [i for i in ids if i != keep]
        touched.add(keep)
        for loser in losers:
            bind.execute(sa.text("UPDATE price_observation SET identity_id = :k WHERE identity_id = :l"),
                         {"k": keep, "l": loser})
            if "derived_value" in tables:
                bind.execute(sa.text("UPDATE derived_value SET identity_id = :k WHERE identity_id = :l"),
                             {"k": keep, "l": loser})
            if "market_reference_point" in tables:
                bind.execute(sa.text("DELETE FROM market_reference_point WHERE identity_id = :l"),
                             {"l": loser})
            bind.execute(sa.text("DELETE FROM market_identity WHERE id = :l"), {"l": loser})
            folded += 1

    # 4. One reading per market, publisher, day and price.
    doubles = list(bind.execute(sa.text(
        "SELECT identity_id, source_id, observation_date, price, group_concat(id, '|') "
        "FROM price_observation GROUP BY identity_id, source_id, observation_date, price "
        "HAVING count(*) > 1")))
    dropped = 0
    for identity_id, _source, _day, _price, joined in doubles:
        ids = sorted(joined.split("|"))
        losers = ids[1:]
        touched.add(identity_id)
        for loser in losers:
            if "price_in_currency" in tables:
                bind.execute(sa.text("DELETE FROM price_in_currency WHERE observation_id = :o"),
                             {"o": loser})
            bind.execute(sa.text("DELETE FROM price_observation WHERE id = :o"), {"o": loser})
            dropped += 1

    # Every reference point on a market that changed is stale; the engine recomputes them.
    if "market_reference_point" in tables and touched:
        for identity_id in touched:
            bind.execute(sa.text("DELETE FROM market_reference_point WHERE identity_id = :i"),
                         {"i": identity_id})

    print("[0046] DELIVERED identities renamed to DAP: %d" % renamed)
    print("[0046] placeholder origin/destination cleared: %d" % cleared)
    print("[0046] duplicate identities folded away: %d" % folded)
    print("[0046] duplicate readings dropped: %d" % dropped)


def downgrade():
    pass
