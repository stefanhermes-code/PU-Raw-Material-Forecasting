"""What the engine calls a series, and which market that series belongs to.

The engine addresses everything it forecasts by an eight-field string - family, grade, geography,
basis, packaging, currency, unit, price kind - joined with `|`, with `UNKNOWN` standing in for any
field left empty. Nothing was wrong with the key except that it was written out four times
(`domain/schemas.py`, `forecasting/review_job.py`, `main.py`, and the store's own matching clause
in `db/repositories.py`), and the comparability group - the same list without the price kind -
three more times (`db/repositories.py`, `identity/resolver.py`, `bootstrap.py`). Seven copies of
two rules. `forecast_vintage.identity_key`, `model_fit_result.identity_id` and
`forecast_method_selection.identity_id` all hang off them, so a drift in any copy silently
re-points a forecast.

**The engine's series is not the application's market.** The customer pages key on four fields -
chemical, geography, the ruled terms, stated packaging - and `market_of` is that key, imported
rather than restated, so there is one definition of a market in this repository and the engine
reads it from the side that owns the rulings.

Measured against the live store on 27 October 2026: 360 stored series carrying a priced reading
collapse to 298 markets, and the eight-field key splits 30 of those markets in two or more. Every
one of the 30 splits is on grade (23), unit (11), price kind (12) or currency (2) - and no other
field. Those four are exactly the ones section A's remedy already sends out of the identity:
item 4 takes unit to one token, item 5 takes currency out, item 6 makes price kind the line within
a market, and section H gives grade a vocabulary. So the eight-field key narrows onto the market
key when those land, and nothing else stands in the way. Until then the two are kept side by side
here, named, with the gap measured rather than assumed.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:   # the market is the application's; see `market_of`
    from raw_material_forecasting.series_curation import MarketKey

# In order, and this order is the stored string. The last field is the price kind; everything
# before it is the comparability group.
FIELDS: tuple[str, ...] = (
    "product_family", "product_grade_or_chemistry", "market_geography", "commercial_basis",
    "packaging", "unit", "observation_type",
)

# Seven fields since migration 0059, not eight: **currency left** (masterplan A, item 5). A TDI
# China price quoted in yuan and one quoted in dollars are the same series - the conversion table
# already holds every reading in all three display currencies, keyed per reading, so nothing on a
# screen ever needed the series to say which currency it was in. Measured before it was removed: no
# two identities in the store differed only by currency, so taking it out merged nothing. What a
# reading is priced in is now on the reading, where it belongs.
COMPARABILITY_FIELDS: tuple[str, ...] = FIELDS[:-1]

# The pydantic identity calls the basis by its longer name; the ORM row and the connectors' dicts
# call it `commercial_basis`. One key, read off either.
_ALSO_CALLED = {"commercial_basis": "incoterm_or_commercial_basis"}

SILENCE = "UNKNOWN"


def _field(row: Any, name: str) -> str:
    """One field of `row`, whether it is an ORM row, a pydantic identity or a plain dict."""
    for key in (name, _ALSO_CALLED.get(name, name)):
        value = row.get(key) if isinstance(row, dict) else getattr(row, key, None)
        if value not in (None, ""):
            return str(value)
    return SILENCE


def _join(row: Any, fields: tuple[str, ...]) -> str:
    return "|".join(_field(row, name) for name in fields)


def series_key(row: Any) -> str:
    """The eight-field string the forecast, the fit and the selection are stored under."""
    return _join(row, FIELDS)


def comparability_group(row: Any) -> str:
    """The series key without the price kind: which series may be compared with which."""
    return _join(row, COMPARABILITY_FIELDS)


def market_of(row: Any, source_id: str = "") -> "MarketKey":
    """The market this series belongs to - the application's four-field key, not a copy of it.

    Imported here rather than at the top so the store's own write path can take `FIELDS` from this
    module without pulling the application's curation layer in behind it.
    """
    from raw_material_forecasting.series_curation import market_key
    return market_key(
        {"product_family": _as_value(_field(row, "product_family")),
         "market_geography": _as_value(_field(row, "market_geography")),
         "commercial_basis": _as_value(_field(row, "commercial_basis")),
         "packaging": _as_value(_field(row, "packaging"))},
        source_id)


def market_of_series_key(key: str, source_id: str = "") -> "MarketKey":
    """The market a STORED key belongs to, so `forecast_vintage` rows can be grouped by market."""
    parts = str(key).split("|")
    if len(parts) != len(FIELDS):
        raise ValueError(f"not a series key: {key!r}")
    return market_of(dict(zip(FIELDS, parts)), source_id)


def _as_value(text: str) -> str:
    """`UNKNOWN` back to the emptiness it stood for, so the market key sees what it expects."""
    return "" if text == SILENCE else text
