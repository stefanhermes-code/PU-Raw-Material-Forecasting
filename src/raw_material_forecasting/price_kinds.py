"""What kind of price a reading is - the line within a market, and the only list of the kinds.

Masterplan A, item 6. Stefan, on the old field: *"I am not sure what that means."* It was called
`observation_type`, which says nothing about what it holds, and it held seven values that were not
mutually exclusive, because the field was asking three questions at once:

    what is the number?    an assessment, a contract, an executed deal, an ask
    how was it formed?     assessed, negotiated, by formula
    what period does it cover?   a day, a week, a month, a quarter, a year

A formula price *is* a contract price - the formula is how it was agreed. A quarterly average is an
average *of* something, so it needs the other answer too. A producer's posting and a seller's quote
are both asks, and the customer pages already filtered them out together, which is the application
admitting they are one class. Three of the seven came from one publisher, `icis_history`, so the
extractor was choosing between `contract_price`, `formula_based` and `transaction_price` for prints
of the same kind.

So the kind is **four values**, and the other two questions became properties of the reading:

    assessed   a publisher's assessment of the market      <- the headline line
    contract   a price agreed between buyer and seller
    deal       a trade that happened, customs unit values included
    ask        a posting or an offer; nobody traded on it   -> never published

**Ruled by Stefan, 27 October 2026: two lines of one market.** *"Contract and spot shown
separately."* So the kind is not an identity of its own and not something to average away. The
market is chemical + geography + terms + packaging (`series_curation.market_key`); a **line** is
that market plus the kind; a reading belongs to a line. A forecast is made per line, because
splicing a contract formula onto a spot assessment would produce a shape neither publisher ever
quoted.

Measured over the live store on 28 October 2026, before the collapse:

    was                  identities  readings   becomes
    market_price, customs lanes  176    18,234   deal      (comtrade_*, comext_*)
    market_price, everything else 141    12,599   assessed
    transaction_price             28      1,766   deal
    contract_price                 5      1,807   contract  (formed: negotiated)
    formula_based                  3        879   contract  (formed: formula)
    producer_price                 4        136   ask
    seller_quote                   1         30   ask
    quarterly_average              3         11   assessed  (period: quarter)

and afterwards: deal 20,000 readings, assessed 12,610, contract 2,686, ask 166 - 35,462, which is
every priced reading in the store. **No identity mixed two kinds** (0 of the 317 `market_price`
identities held both customs and assessed readings) and **the collapse merged nothing** (376
identities stay 376 distinct on the store's match fields), both checked before migration 0058 ran.

Two things the data forced that Stefan's lists did not name, flagged rather than invented quietly:

- **`period` needs `year`.** 2,013 readings come from `comtrade_annual_*`, one stamp per year.
- **`formed` has no value for an ask.** A posting is neither assessed nor negotiated nor set by a
  formula - the seller simply posted it. Rather than force one of the three or invent a fourth, an
  ask's `formed` is left empty, and this is the one open question in item 6.
"""
from __future__ import annotations

import re
from typing import Any

# The four. In the order a market's lines are read: the assessed line is the headline, and an ask
# is not a line at all.
ASSESSED = "assessed"
CONTRACT = "contract"
DEAL = "deal"
ASK = "ask"
KINDS: tuple[str, ...] = (ASSESSED, CONTRACT, DEAL, ASK)

# What a customer page may show. An ask is a posting nobody traded on; averaged into a market line
# it would move the line with no trade behind it.
PUBLISHED: tuple[str, ...] = (ASSESSED, CONTRACT, DEAL)

# The kind a market's headline is taken from, and the one a reading falls back to when nothing says
# otherwise: a publisher's assessment of the market.
HEADLINE = ASSESSED

# How the number came to be. Not "what it is" - that is the kind above.
ASSESSED_BY_A_PUBLISHER = "assessed"
NEGOTIATED = "negotiated"
BY_FORMULA = "formula"
FORMED: tuple[str, ...] = (ASSESSED_BY_A_PUBLISHER, NEGOTIATED, BY_FORMULA)

# What stretch of time one reading speaks for. `year` is here because the store holds 2,013 annual
# customs readings; `week` because ICIS prints weekly.
DAY, WEEK, MONTH, QUARTER, YEAR = "day", "week", "month", "quarter", "year"
PERIODS: tuple[str, ...] = (DAY, WEEK, MONTH, QUARTER, YEAR)

# The seven values that were in the store, and what each becomes. `market_price` is the one that
# needs the reading's source as well, because it held both a publisher's assessment and a customs
# unit value - see `kind_of`.
WAS: dict[str, str] = {
    "market_price": ASSESSED,
    "contract_price": CONTRACT,
    "formula_based": CONTRACT,
    "transaction_price": DEAL,
    "producer_price": ASK,
    "seller_quote": ASK,
    "quarterly_average": ASSESSED,
}

# How each of the seven was formed. An ask has no entry: see the module docstring.
WAS_FORMED: dict[str, str] = {
    "market_price": ASSESSED_BY_A_PUBLISHER,
    "quarterly_average": ASSESSED_BY_A_PUBLISHER,
    "contract_price": NEGOTIATED,
    "transaction_price": NEGOTIATED,
    "formula_based": BY_FORMULA,
}

# A customs declaration is a trade that happened, so its unit value is a deal however it was filed.
CUSTOMS_LANES = re.compile(r"^(comtrade_|comext_)", re.I)

# What period a publisher's print covers, read off the stamps of all 35,462 readings on 28 October
# 2026: every reading from these sources falls on the first of its period, and the gap between
# consecutive stamps says which period it is. Everything not named here is a daily print, which is
# what the remaining 32 sources measured as.
_PERIOD_BY_SOURCE: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^comtrade_annual_", re.I), YEAR),
    (re.compile(r"^(comtrade_monthly_|comext_)", re.I), MONTH),
    (re.compile(r"^(europe_published_ddp|europe_delivered_purchases|fnag_switzerland"
                r"|foampartner_china|kgi_thailand|imarc_|procurementresource_"
                r"|import_procurement_resource|import_sunsirs_priceseek)", re.I), MONTH),
    (re.compile(r"^chemanalyst_", re.I), QUARTER),
    (re.compile(r"^(icis_history|import_longzhong_mysteel)$", re.I), WEEK),
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def kind_of(stored: Any, source_id: Any = "") -> str:
    """The kind of price a reading is, from what the store holds and which publisher sent it.

    The source matters for one value only. `market_price` held two different things: a publisher's
    assessment of the market, and a customs unit value - 18,234 of them, which are trades that
    happened and so are deals. Nothing else in the seven needed the source.

    A value this does not recognise falls back to the headline kind rather than raising, because a
    reading with an odd label is still a price and dropping it would be worse than filing it as an
    assessment; `KINDS` is the list to check against when that matters.
    """
    was = _text(stored)
    if was in KINDS:
        return was
    if was == "market_price" and CUSTOMS_LANES.match(_text(source_id)):
        return DEAL
    return WAS.get(was, HEADLINE)


def formed_of(stored: Any, source_id: Any = "") -> str:
    """How the number was formed, or "" where none of the three applies - which is every ask.

    The source matters for the same reading `kind_of` needs it for. A customs unit value is a
    trade that happened: the price in it was negotiated between a buyer and a seller, not assessed
    by a publisher, however the declaration was filed. Giving those 18,234 readings
    `formed = assessed` while calling them deals would have been the old field's fault in a new
    field - one column saying two things.
    """
    was = _text(stored)
    if was in FORMED:
        return was
    if was == "market_price" and CUSTOMS_LANES.match(_text(source_id)):
        return NEGOTIATED
    return WAS_FORMED.get(was, "")


def period_of(source_id: Any) -> str:
    """The stretch of time one of this publisher's readings speaks for."""
    source = _text(source_id)
    for pattern, period in _PERIOD_BY_SOURCE:
        if pattern.match(source):
            return period
    return DAY


def is_published(kind: Any) -> bool:
    """Whether a reading of this kind may reach a customer page. An ask may not."""
    return kind_of(kind) in PUBLISHED


def display_kind(kind: Any) -> str:
    """The line's name as a buyer reads it on the page."""
    return {ASSESSED: "Assessed", CONTRACT: "Contract", DEAL: "Deal", ASK: "Ask"}.get(
        kind_of(kind), "Assessed")


def line_order(kind: Any) -> int:
    """Where a line sits among a market's lines: the assessed line first, as the headline."""
    return KINDS.index(kind_of(kind))
