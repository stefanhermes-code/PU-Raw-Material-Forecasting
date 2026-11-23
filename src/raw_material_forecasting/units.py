"""The one token this application measures a quantity in, on the screen and in the store.

The polyurethane trade says MT. Not "t", not "tonne", not "metric tonne" - MT, in every quotation,
contract and market report anybody in it reads. "Ton" is worse than imprecise: it is 907 kg in the
United States and 1,016 kg in the United Kingdom, so a price per "ton" is a price per one of three
different things.

**Until 28 October 2026 this module said the opposite about the store**: that the screen says MT
while the store keeps "t", because rewriting the column would split every series in two. The
second half of that was never measured, and when it was, it was false - no two identities in the
store differ only by their unit, so folding "t" to MT merges nothing and splits nothing. The
reason it had to change is masterplan A item 4: one token, applied in the store and read from the
store wherever it is printed. A display rule laid over a store that disagrees is exactly the
second definition A is about. Migration 0057 moved the 354 stored identities, their comparability
groups and the forecast vintages' keys in one transaction.

`canonical_unit` is what the store writes; `display_unit` is what a reader sees. They differ only
in what they accept: the store takes a bare token, the screen also takes a currency pair
("EUR/metric tonne" -> "EUR/MT"). Both leave alone anything that does not name a tonne - an index
has no unit and says so in its own words ("Index 1982=100"), and inventing one for it would be
worse than printing it.

Five modules held their own list of tonne spellings and no two lists matched: the store's had
eleven, this module's regex eight, `document_reader` seven, `tdi_market_view` four. A spelling in
one and not another is a market in one and not another. One list, here.
"""
from __future__ import annotations

import re

MT = "MT"

# t, mt, ton, tons, tonne, tonnes, metric ton, metric tonne, metric tons - alone or as the
# denominator of a currency pair. Anchored so "t" inside another word is left where it is.
_TONNE = r"(?:metric\s*)?(?:tonnes?|tons?|mts?|t)"
_ALONE = re.compile(rf"^\s*/?\s*{_TONNE}\s*$", re.I)
_PAIR = re.compile(rf"^\s*(?P<lead>[A-Za-z]{{3}})\s*/\s*{_TONNE}\s*$", re.I)
# A unit written behind an amount, as `with_unit` writes it, for `without_unit` to take off again.
_SUFFIX = re.compile(rf"\s*/\s*(?:{_TONNE}|[A-Za-z]{{1,12}})\s*$", re.I)


def is_tonne(value) -> bool:
    """Whether this token names a metric tonne, however it is spelled."""
    return bool(_ALONE.match(str(value or "")))


def canonical_unit(value) -> str:
    """The unit as the STORE keeps it: MT for every spelling of a tonne.

    Nothing that does not name a tonne is touched, and nothing is invented. A value this does not
    recognise is written exactly as it arrived, so a connector sending something new is visible in
    the store rather than silently relabelled MT - and a connector sending nothing stores nothing,
    because a missing unit is a missing unit, not a tonne. The screen is where emptiness becomes
    MT, since a price has to be printed per something; see `display_unit`.
    """
    text = str(value or "").strip()
    return MT if is_tonne(text) else text


def display_unit(value) -> str:
    """The unit as a reader should see it: "t" -> "MT", "EUR/metric tonne" -> "EUR/MT"."""
    text = str(value or "").strip()
    if not text:
        return MT
    if _ALONE.match(text):
        return MT
    pair = _PAIR.match(text)
    if pair:
        return f"{pair.group('lead').upper()}/{MT}"
    return text


def per_unit(currency=None, unit=None) -> str:
    """The denominator on a price: "USD / MT", or "/ MT" with no currency to name."""
    code = str(currency or "").strip().upper()
    held = display_unit(unit)
    return f"{code} / {held}" if code else f"/ {held}"


def pair(currency=None, unit=None) -> str:
    """The same denominator with no spaces, for a chart axis or a hover label: "USD/MT"."""
    code = str(currency or "").strip().upper()
    held = display_unit(unit)
    return f"{code}/{held}" if code else held


def with_unit(amount: str, unit=None) -> str:
    """An already-formatted amount with its unit behind it: "CNY 16,850 / MT"."""
    return f"{amount} / {display_unit(unit)}"


def without_unit(text) -> str:
    """The same amount with the unit taken off again, whatever unit `with_unit` put there.

    The forecast banner prints amounts with the unit and the explainer prints them without, in a
    sentence that already says "per MT". Both used the literal " / MT" - one appending it, the
    other stripping that exact string - so the day the printed unit changed, the explainer would
    have gone on stripping a suffix that was no longer there and printed it twice.
    """
    return _SUFFIX.sub("", str(text or "")).strip()


