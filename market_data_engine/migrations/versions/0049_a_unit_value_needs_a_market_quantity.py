"""A customs unit value is only a price above a market quantity (v0.5.38)

A unit value is the declared trade value over the declared net weight. The division is exact and
the inputs are honest; what it MEANS depends on how much moved. Türkiye's entire declared propylene
oxide import for 2004 was 21 kg for USD 754, which the arithmetic faithfully reports as 35,905
USD/t. The gate in the trade lane asked only for a positive weight, and said so in as many words:
"No minimum volume applies."

What that put on customer pages, measured on 17 October 2026: benzene Türkiye CIF at 52,227 USD/t
against its own FOB leg of 921, ethylene oxide Japan CIF at 59,751, propylene oxide Türkiye at
21,000. Nine of them were offered to customers.

Measured over the 19,151 Comtrade rows then held, each against its own material's level (the median
unit value of shipments above 1,000 t, which recovers benzene at 766, toluene 770, ethylene oxide
1,999, propylene oxide 1,325, polyether polyol 2,231 - all correct):

    under 100 kg        34.58x the level      90% of rows above 3x
    0.1 - 1 t           12.50x                80%
    1 - 10 t             3.16x                51%
    10 - 100 t           1.45x                15%
    100 - 1,000 t        1.25x                 7%
    1,000 - 10,000 t     1.02x                 1%
    over 10,000 t        0.98x                 0%

`app.trade.comtrade.MARKET_QUANTITY_TONNES` now holds that floor at 1,000 t for everything collected
from here on. This brings the readings already stored to the same rule.

What is removed: the PRICE rows whose own declaration was under the floor - 6,247 of the 17,623
Comtrade price rows, 35%.

What is NOT touched, and this matters: every `ct_*` feature record stays exactly as it is, carrying
the tonnage, the unit value, the declared value and the weight. A 21 kg import did happen and that
is a true fact about trade. Only its unit value stops being offered as a market price, and a better
rule can be applied to the same records later without re-collecting anything.

Each price is matched to the feature it was computed from on reporter, basis, observation date and
the unit value itself - an exact match on all 17,623 rows, with none left over.

The downgrade cannot restore the rows. The feature records hold everything needed to rebuild them,
and rebuilding what was measured to be wrong is not something a downgrade should do by itself.
"""
import json

from alembic import op
import sqlalchemy as sa

revision = "0049_a_unit_value_needs_a_market_quantity"
down_revision = "0048_trade_figures_are_market_prices"
branch_labels = None
depends_on = None

MARKET_QUANTITY_TONNES = 1000.0


def _tonnage(bind) -> dict:
    """(reporter, basis, day, unit value) -> tonnes, from the records the prices came from."""
    found = {}
    rows = bind.execute(sa.text(
        "SELECT data FROM feature_observation WHERE feature_type LIKE 'ct\\_%\\_tonnes' ESCAPE '\\'"
    )).fetchall()
    for (data,) in rows:
        try:
            one = json.loads(data) if isinstance(data, str) else (data or {})
        except ValueError:
            continue
        if one.get("partner") != "World":
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
        found[(one.get("reporter"), basis, day, round(value, 2))] = tonnes
    return found


def upgrade() -> None:
    bind = op.get_bind()
    tonnage = _tonnage(bind)
    if not tonnage:
        return
    prices = bind.execute(sa.text("""
        SELECT o.id, i.market_geography, UPPER(i.commercial_basis),
               substr(o.observation_date, 1, 10), o.price
          FROM price_observation o JOIN market_identity i ON i.id = o.identity_id
         WHERE o.source_id LIKE '%comtrade%' AND o.price IS NOT NULL""")).fetchall()
    doomed = []
    for row_id, geography, basis, day, price in prices:
        found = tonnage.get((geography, basis, day, round(float(price), 2)))
        if found is not None and found < MARKET_QUANTITY_TONNES:
            doomed.append(row_id)
    for i in range(0, len(doomed), 500):
        batch = doomed[i:i + 500]
        bind.execute(sa.text("DELETE FROM price_observation WHERE id IN :ids")
                     .bindparams(sa.bindparam("ids", expanding=True)), {"ids": batch})


def downgrade() -> None:
    """Deliberately nothing. See the note at the top."""
