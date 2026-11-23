"""Whether a price has tax in it, in one place.

Chinese domestic chemical assessments are quoted 含税 - inclusive of the 13% VAT on chemicals, RMB
per tonne, ex-works. SCI99, Longzhong, SunSirs and ECHEMI all quote that way; it is the convention of
that market, not a property of any one publisher.

An internal purchase record is not covered by it. It carries whatever the buyer's own ledger carries,
and HTC's China figures are net - stated by Stefan, 14 September 2026.

So the test is who published the price, not what kind of record it is. That distinction matters from
the moment FoamPartner's Changzhou prices joined the consolidated market series: they are market
prices now by decision, and stripping 13% off a figure that never had it would take the series 13%
below the market for every month before 2024.

AND NOT EVERY PUBLISHER OF CHINESE PRICES FOLLOWS THE CHINESE CONVENTION
------------------------------------------------------------------------
ICIS assessed TDI in East, North and South China in yuan, 573 readings running 2015 to 2023, and
those figures are **net of VAT** - stated by Stefan, 29 October 2026. They are the only yuan history
the store holds before SunSirs begins in 2024.

This was asked because the implementation and the sentence above it did not agree. The rule says the
13% is the convention of that market; the test was for an ex-works basis. ICIS assesses China on DAP,
so it fell outside the test and was left gross - which happened to be the right answer, for the wrong
reason. Widening the test to "any Chinese domestic assessment in yuan", which is what the first
paragraph reads like, would have taken 13% off 573 readings that never had it.

So the ruling is written here as a ruling and tested as one. A publisher in `QUOTES_CHINA_NET` is net
whatever its delivery term, and the basis test below cannot reach it. Measuring could not settle this
and should not be asked to: the only net yuan series overlapping ICIS is FoamPartner's Changzhou book,
which is HTC's own buying, and across the 21 overlapping months ICIS runs a median 18% above it -
against 13% for the tax alone. A contract discount and a tax are tangled in that ratio and no
arithmetic separates them. It is a fact about what a publisher prints, and it came from Stefan.
"""
from __future__ import annotations

# The rate on chemicals in China.
CHINA_VAT_RATE = 0.13

# A source registered INTERNAL is HTC's own record, not a publication.
INTERNAL = "INTERNAL"

# Publishers whose Chinese prices are quoted net of VAT, whatever the delivery term. Stefan,
# 29 October 2026. One entry, and it needs a ruling of its own to gain another.
QUOTES_CHINA_NET = frozenset({"icis_history"})

# Delivery terms that mean the goods crossed a border, so China's VAT has not been charged on the
# figure: an import is taxed at customs separately, and an export is zero-rated.
#
# This replaced a whitelist of ex-works spellings, which was a proxy for "a sale between two
# Chinese parties" and a poor one. It missed every domestic price that was not written EXW -
# measured 29 October 2026, 104 readings, including a basis called CHINA_DOMESTIC_SELLER_QUOTE and
# JLC's propylene oxide line, which that same publisher quotes beside three EXW lines that were
# all being netted. The clearest case: on 16 October SunSirs printed TDI China at 17,900 yuan, and
# the store held it netted to 15,841 under `sunsirs_tdi` and kept at 17,900 under `import_sunsirs`
# and `import_htc_global`, because those two carry no delivery term at all.
#
# A term naming a border is a thing you can state and check. "Not ex-works" was not.
CROSSES_A_BORDER = frozenset({"CFR", "CIF", "CIP", "CPT", "C&F", "FAS", "FCA", "FOB", "DDU"})

# The quoting convention is a convention for QUOTING. It applies to what a publisher states about
# the market - an assessment, or a seller's asking price printed in a trade channel - and not to
# what somebody actually paid, which carries whatever their own ledger carries.
#
# `test_vat_stays_in_a_purchase_price` has asserted this since it was written, and it was passing
# because the one purchase record in it is quoted DAP and the old test only recognised EXW. The
# rule it encodes was never implemented: a Chinese purchase record written EXW would have had 13%
# taken off it. Widening the delivery-term test on 29 October 2026 is what made that visible.
#
# `rights_class == INTERNAL` below is a different test and both are needed. That one says whose
# book it is; this one says what kind of number it is. A supplier's invoice relayed by a third
# party is neither HTC's book nor a published assessment.
QUOTED_BY_A_PUBLISHER: tuple[str, ...] = ("assessed", "ask")


def _clean(value) -> str:
    return str(value or "").strip()


def carries_chinese_vat(*, currency: str | None, market_geography: str | None,
                        commercial_basis: str | None, rights_class: str | None,
                        source_id: str | None = None, price_kind: str | None = None) -> bool:
    """True when a stored price still has China's 13% VAT inside it.

    Five things have to hold together. The price is quoted in yuan, in a Chinese market, on terms
    that do not name a border; it is something a publisher stated about the market rather than
    something somebody paid; it is not out of HTC's own books; and its publisher follows the
    Chinese convention - ICIS does not.

    `source_id` and `price_kind` are optional so a caller that cannot supply them still gets the
    other tests. Every caller in the store path supplies both.
    """
    if _clean(source_id) in QUOTES_CHINA_NET:
        return False
    if _clean(rights_class).upper() == INTERNAL:
        return False
    if price_kind is not None and not quoted_by_a_publisher(price_kind):
        return False
    if _clean(currency).upper() != "CNY":
        return False
    if "china" not in _clean(market_geography).lower():
        return False
    return _clean(commercial_basis).upper() not in CROSSES_A_BORDER


def quoted_by_a_publisher(price_kind) -> bool:
    """Whether this kind of price is a publisher's statement about the market.

    Folded through `price_kinds.kind_of` so the raw stored spellings - "market_price",
    "transaction_price" - answer the same as the canonical ones.
    """
    from raw_material_forecasting import price_kinds

    return price_kinds.kind_of(price_kind) in QUOTED_BY_A_PUBLISHER


def net_of_vat(price: float) -> float:
    """The price with China's VAT taken out."""
    return price / (1.0 + CHINA_VAT_RATE)
