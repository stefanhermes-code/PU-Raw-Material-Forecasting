"""A Comext month is only a price above a market quantity (v0.5.46)

0049 did this for UN Comtrade and the handoff said in as many words that Eurostat Comext was
untouched and carried the same rule. It did. The investigation engine, run on 18 October 2026,
opened a case for six Comext series whose own readings disagreed by 25x to 645x, and the cause is
the one 0049 found in the other lane.

Value over quantity is exact and Eurostat's figures are honest. What the answer MEANS depends on
how much moved, and the gate asked only for a positive quantity - the line said so: "No minimum
volume." Read out of the kept Comext payloads and confirmed against the live Eurostat host on
19 October 2026:

    dimethyl terephthalate, EU exports, August 2010    EUR 18,934 for 82 kg   ->  230,902 EUR/t
    dimethyl terephthalate, EU exports, February 2026  EUR  2,180 for 10 kg   ->  218,000 EUR/t
    ethylene oxide, EU imports, January 2025           EUR 423,257 for 7.5 t  ->   56,135 EUR/t
    propylene oxide, EU imports, July 2000             EUR  6,704 for 304 kg  ->   22,053 EUR/t

Eighty-two kilograms is Europe's entire declared export of dimethyl terephthalate for that month.
A drum with some chemical in it, divided by the chemical.

THE MEASUREMENT. 5,811 extra-EU months across all twelve codes, each against its OWN opposite leg
in the SAME month - same chemical, same market, same month, so neither the level of a chemical nor
thirty years of price history can flatter it - using only months whose opposite leg itself moved
at least 1,000 t:

    under 1 t        7.18x the other leg      73% of months above 3x
    1 - 5 t          8.97x                    74%
    5 - 10 t        16.46x                    91%
    10 - 25 t        2.86x                    50%
    25 - 50 t        1.48x                    14%
    50 - 100 t       1.31x                     0%   (a quarter still above 1.5x)
    100 - 200 t      1.11x                     0%   (none above 1.5x)
    200 - 400 t      1.08x                     0%
    400 - 700 t      1.03x                     1%
    700 - 1,000 t    1.04x                     2%
    1,000 - 2,000 t  1.04x                     0%
    over 5,000 t     0.99x                     0%

100 tonnes a month is where the median lands on the other leg and the distortion stops.
`app.sources.connectors.eurostat_comext.MARKET_QUANTITY_TONNES` holds it for everything collected
from here on. This brings the readings already stored to the same rule.

What is removed: the PRICE rows whose own month moved less than 100 t - 240 of the 4,349 Comext
price rows that carry a record, measured on the live store on 19 October 2026:

    DMT Europe FOB                 172      EO Europe CIF                    30
    DMT Europe CIF                  30      MALEIC_ANHYDRIDE Europe FOB       7
    TEREPHTHALIC_ACID Europe FOB     1

What is NOT touched: every `eu_*` feature record stays exactly as it is, carrying the tonnage, the
unit value, the declared value and the quantity in hundreds of kilograms. Eighty-two kilograms of
dimethyl terephthalate did leave the EU in August 2010 and that is a true fact about trade. Only
the claim that its unit value is a market price is withdrawn, and a better rule can be applied to
the same records later without re-collecting anything.

Each price is matched to the feature record it was computed from, on geography, basis, observation
date and the unit value itself. That matched 4,349 of 4,349 with none left over. The 1,453 Comext
prices with no record - benzene, toluene, propylene oxide, diethylene glycol and adipic acid, whose
rows were written by the predecessor single-series connector on 2 October 2026 and which the trade
layer has not re-collected - cannot be evaluated here and are left alone. Four of them are under
the floor, all four between 758 and 1,601 EUR/t, so none is a visible fault; they will go when
those five codes are next collected, because the connector now applies the floor itself.

The currency conversions and reference points of a removed reading go with it, as 0050 established.

The downgrade cannot restore the rows. The feature records hold everything needed to rebuild them,
and rebuilding what was measured to be wrong is not something a downgrade should do by itself.
"""
import json

from alembic import op
import sqlalchemy as sa

revision = "0052_a_comext_month_needs_a_market_quantity"
down_revision = "0051_an_observation_we_cannot_explain_is_a_case"
branch_labels = None
depends_on = None

MARKET_QUANTITY_TONNES = 100.0


def _tonnage(bind) -> dict:
    """(geography, basis, day, unit value) -> tonnes, from the records the prices came from."""
    found = {}
    rows = bind.execute(sa.text(
        "SELECT data FROM feature_observation "
        "WHERE feature_type LIKE 'eu/_%/_record/_extra/_eu' ESCAPE '/'")).fetchall()
    for (data,) in rows:
        try:
            one = json.loads(data) if isinstance(data, str) else (data or {})
        except ValueError:
            continue
        value, tonnes = one.get("unit_value"), one.get("tonnes")
        if value in (None, "", "n/r") or tonnes is None:
            continue
        try:
            value, tonnes = float(value), float(tonnes)
        except (TypeError, ValueError):
            continue
        day = str(one.get("observation_date") or "")[:10]
        if not day:
            continue
        basis = "FOB" if one.get("flow") == "Export" else "CIF"
        # Every Comext price is the extra-EU total and its geography is Europe; the partner splits
        # are features only and never become prices.
        found[("Europe", basis, day, round(value, 2))] = tonnes
    return found


def upgrade() -> None:
    bind = op.get_bind()
    tonnage = _tonnage(bind)
    if not tonnage:
        return
    prices = bind.execute(sa.text("""
        SELECT o.id, o.identity_id, i.market_geography, UPPER(i.commercial_basis),
               o.observation_date, o.price
          FROM price_observation o JOIN market_identity i ON i.id = o.identity_id
         WHERE o.source_id LIKE 'comext%' AND o.price IS NOT NULL""")).fetchall()
    doomed, identities = [], set()
    for row_id, identity_id, geography, basis, observed, price in prices:
        day = str(observed)[:10]
        found = tonnage.get((geography, basis, day, round(float(price), 2)))
        if found is not None and found < MARKET_QUANTITY_TONNES:
            doomed.append(row_id)
            identities.add(identity_id)
    if not doomed:
        return
    gone = set(doomed)
    # A reference point names the readings it was computed from. One computed from a reading that
    # is going must go with it, or the store keeps an average of a number it no longer holds.
    stale = []
    for point_id, observation_ids in bind.execute(
            sa.text("SELECT id, observation_ids FROM market_reference_point "
                    "WHERE identity_id IN :ids").bindparams(
                        sa.bindparam("ids", expanding=True)), {"ids": sorted(identities)}).fetchall():
        try:
            named = json.loads(observation_ids) if isinstance(observation_ids, str) else (observation_ids or [])
        except ValueError:
            continue
        if any(str(one) in gone for one in named):
            stale.append(point_id)
    for table, column, ids in (("market_reference_point", "id", stale),
                               ("price_in_currency", "observation_id", doomed),
                               ("price_observation", "id", doomed)):
        for i in range(0, len(ids), 500):
            bind.execute(sa.text(f"DELETE FROM {table} WHERE {column} IN :ids")
                         .bindparams(sa.bindparam("ids", expanding=True)), {"ids": ids[i:i + 500]})


def downgrade() -> None:
    """Deliberately nothing. See the note at the top."""
