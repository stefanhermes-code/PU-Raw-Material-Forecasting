"""Which readings a market's line is drawn from, and which are set aside.

Stefan, 26 September 2026, going through TDI China EXW reading by reading. The store holds every
observation we have ever taken and keeps them all; this file decides which of them a customer's
line is drawn from. Nothing here deletes anything - a reading that is set aside is still evidence,
still in the store, and still exportable. It simply stops being drawn.

Every rule carries the reason it exists. A rule without a reason is a number somebody will restore
by accident in six months.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from raw_material_forecasting import customer_controls
from raw_material_forecasting.market_basis import stated_packaging

# The day our own daily collection began. Before this date every reading in the store was either
# assessed by somebody else or backfilled from a publisher's own history pages; from this date on
# they are readings we took ourselves, that morning, from the live page.
#
# Stefan, 26 September 2026: "The current data points start 27 august 2026." Longzhong first
# answered on the 27th, SCI99 on the 31st, the rest in the first half of September.
COLLECTION_BEGAN = "2026-08-27"


@dataclass(frozen=True)
class Alias:
    """One publisher's series read as another set of terms, because they are the same market."""

    chemical: str
    geography: str
    source_id: str
    published_as: str
    read_as: str
    reason: str


@dataclass(frozen=True)
class Cut:
    """Readings set aside from a market's line.

    An empty chemical, geography or basis means "any": a cut that names only a source sets that
    source aside everywhere.
    """

    source_id: str
    reason: str
    chemical: str = ""
    geography: str = ""
    basis: str = ""
    after: str = ""          # set aside readings dated strictly after this day
    before: str = ""         # set aside readings dated strictly before this day
    days: tuple[str, ...] = ()   # set aside these exact days
    # The last day the ruling judged. A ruling made on what the data showed then covers the
    # readings it looked at; a reading dated after this is evaluated afresh rather than set aside
    # for ever (Stefan, 13 October 2026: "these past decisions must be evaluated continuously").
    # Empty for structural rulings - a series that duplicates another, an overlap already merged -
    # where new readings would carry the same fault.
    judged_until: str = ""


ALIASES: tuple[Alias, ...] = (
    Alias(
        chemical="TDI", geography="China", source_id="icis_history",
        published_as="CFR", read_as="EXW",
        reason=(
            "CFR China & Hong Kong is the price of Chinese TDI at origin, not an import into China. "
            "Measured against the domestic assessment over 402 weeks the median difference is 16 "
            "USD/MT, under one percent, and China imported at most 3% of its own nameplate in the "
            "years this covers. Stefan, 26 September 2026: it is the same as EXW."),
    ),
    Alias(
        chemical="PMDI", geography="China", source_id="icis_history",
        published_as="CFR", read_as="EXW",
        reason=(
            "The same reading as TDI, on the same reasoning. There is no domestic Chinese pMDI "
            "assessment in the record to measure this one against - the heading is CFR/CHINA and "
            "nothing else prices the market in those years - so the case is the trade rather than "
            "an overlap: China is the largest pMDI producer in the world and a net exporter, so a "
            "CFR China number for Chinese material is the price at origin with a notional freight "
            "on it. Stefan, 26 September 2026: enough to alias it the way TDI was."),
    ),

    Alias(
        chemical="TDI", geography="Europe", source_id="imarc_tdi",
        published_as="REGIONAL_MARKET", read_as="CFR",
        reason=(
            "One European market, not two. The regional assessment prints a monthly number for "
            "the region with no delivery terms on it; the daily publisher prints a CFR number "
            "into Europe. On the one month both priced - September 2026 - they are 30 USD/MT "
            "apart, 1.12%, while the two assessment publishers differ from each other by 80 "
            "USD/MT in August. The gap between the two series is smaller than the gap inside "
            "one of them. Stefan, 28 September 2026, having read both: \"So merge them\". "
            "Read on CFR because that is the only terms either side states, and a European "
            "buyer needs a delivered market rather than a number with no terms."),
    ),
    Alias(
        chemical="TDI", geography="Europe", source_id="businessanalytiq_tdi",
        published_as="REGIONAL_MARKET", read_as="CFR",
        reason="The second publisher of the same monthly assessment, on the same ruling.",
    ),
)



@dataclass(frozen=True)
class Terms:
    """What terms a series is actually on, where the publisher's own word is not one of them.

    Stefan, 28 September 2026: "A label is not pure cosmetic, nor is it a command to merge."

    So this is a ruling about what a series IS, carrying its evidence: FD is free delivered and
    what it describes is DAP; a regional assessment printed with no terms is read as delivered. It
    governs the terms wherever they are stated - the selector, the legend, an export, a report -
    because it is a claim about the market rather than a coat of paint.

    What it does not do is decide which readings share a line. `Alias` below is the merging
    command, and it is the only thing that moves a reading onto another market's line.
    """

    chemical: str
    geography: str
    source_id: str
    published_as: str
    terms_are: str
    reason: str

    # An empty chemical, geography or source_id means ANY, so a ruling about a KIND of price can be
    # written once. Without it a rule has to name every chemical, geography and publisher it covers,
    # and the regional-assessment ruling below proved what that costs: its own reason said "across
    # the board" while the list underneath reached TDI and polymeric MDI only, and benzene, propylene
    # oxide, adipic acid, toluene and pure MDI went on being published as REGIONAL_MARKET for three
    # weeks (Stefan, 16 October 2026: "I thought we had taken that out and changed to DAP").
    #
    # Specific rules are matched first, so naming a chemical still beats the general case.


TERMS: tuple[Terms, ...] = tuple(
    Terms(chemical=chemical, geography="Europe", source_id="icis_history",
          published_as="FD", terms_are="DAP",
          reason=("Free delivered is the publisher's shorthand for a contract price delivered to "
                  "the buyer's works. It is not an Incoterm; what it describes is DAP. Stefan, "
                  "28 September 2026: \"I want FD to be replaced with DAP, everywhere.\" A ruling on "
                  "the terms, not a merge: the European contract series and the purchase book "
                  "stay two lines."))
    for chemical in ("TDI", "PURE_MDI", "PMDI", "POLYETHER_POLYOL")
) + tuple(
    Terms(chemical=chemical, geography=geography, source_id=source,
          published_as="REGIONAL_MARKET", terms_are="DAP",
          reason=("A monthly price level for a whole region, published with no terms on it. Read "
                  "as delivered: Stefan, 28 September 2026, across the board. Not CFR, which "
                  "would claim the material was imported - Sadara makes TDI and MDI inside the "
                  "Middle East - and not EXW, which would claim a price at the gate with no "
                  "freight in it."))
    for chemical, geography, source in (("", "", ""),)
)

# Two series in one market: where they overlap in time they are one market and merge, and where
# they do not the chart bridges the gap between them. Stefan, 28 September 2026: "on overlap,
# merge, on gap, bridge."
#
# Both European pairs overlap, so both merge. Polymeric MDI: the contract assessment runs May 2015
# to August 2023 and the purchase book November 2013 to January 2017, overlapping twenty months.
# Polyether polyol: January 2014 to September 2020 against the same purchase window, overlapping
# thirty-seven. Where both price a day, the day carries their average - which is what a merge means
# here, and the two sit 8 to 10% apart on the months they share.
ALIASES = ALIASES + tuple(
    Alias(chemical=chemical, geography="Europe", source_id="icis_history",
          published_as="FD", read_as="DAP",
          reason=("The contract assessment and the purchase book are one European market on "
                  "delivered terms, and they overlap. Stefan, 28 September 2026: \"on overlap, "
                  "merge, on gap, bridge.\" He ruled it having read the gap between them: the "
                  "purchases sit 149 EUR/MT above the assessment on polyether polyol and 136 to "
                  "173 below it on polymeric MDI, one buyer's book against a published market."))
    for chemical in ("PMDI", "POLYETHER_POLYOL")
)


# The same rule on the gap side. European polymeric MDI on delivered terms stops on 9 August 2023;
# the monthly assessment picks the market up on 1 August 2026. They do not overlap, so nothing is
# averaged - they are one line with three years unmeasured between them, and the chart draws that
# stretch as the dotted bridge it already uses for a hole in a series. Stefan, 28 September 2026:
# "on overlap, merge, on gap, bridge."
ALIASES = ALIASES + (
    Alias(chemical="PMDI", geography="Europe", source_id="imarc_mdi",
          published_as="REGIONAL_MARKET", read_as="DAP",
          reason=("The monthly assessment carries European polymeric MDI on from where the "
                  "contract series stops. No overlap, so no reading is averaged with another: the "
                  "three years between them are bridged."),),
)


# European TDI: the history behind the line we are collecting.
#
# Stefan, 29 September 2026, looking at a Current Market chart that opened in December 2025 while
# twelve years of the same market sat on another page: "when a graph is selected because we collect
# the history of this needs to be in the graph, why else would we have a bridge."
#
# Which history. Two candidates, both measured with the euro series converted at the ECB rate of
# the day. The DDP series is not this market: 3,436 against 2,640 in December, 3,207 against 2,540
# in March, 3,780 against 2,530 in July - 21 to 33% above the CFR line on every month both price.
# It stays its own line. The contract series is: it ends on 9 August 2023 at 2,370 EUR, which is
# 2,599 USD, and the CFR line opens 27 months later at 2,640, 1.6% higher.
#
# No overlap between them, so nothing is averaged: the 27 months are bridged.
#
# What this asserts, and it is worth stating plainly: the contract series is read on the terms of
# the line we collect, which is CFR into Europe, rather than on the delivered terms its publisher
# printed. The two levels meet within 1.6%; the inland leg between a European port and a buyer's
# works is the difference, and it is inside that.
ALIASES = ALIASES + (
    Alias(chemical="TDI", geography="Europe", source_id="icis_history",
          published_as="FD", read_as="CFR",
          reason=("The European contract history joined to the market we collect. Ends 9 August "
                  "2023 at 2,370 EUR (2,599 USD); the collected line opens 1 December 2025 at "
                  "2,640 USD, 1.6% higher, with 27 months bridged between them. DDP was the other "
                  "candidate and is 21 to 33% above this line every month it prints, so it keeps "
                  "its own. Stefan, 29 September 2026."),),
)


# Southeast Asian TDI: the regional assessment glued onto the CFR line.
#
# Set aside on 28 September 2026 ("So drop the regional assessment") because three of its four prints
# fell inside the three-year hole in the CFR series and the one shared month was 6.48% apart. On
# 13 October 2026 Stefan ruled that a series comes back when it improves the historic view or the
# forecast, and the eligibility table measured that it adds 3 of its 4 months to a market that has
# no reading for them. Stefan, 13 October 2026: "Bring the SEA back." Glued rather than drawn as a
# line of its own, because the months it adds are the hole in the CFR history; on the month both
# price the day carries their average ("on overlap, merge, on gap, bridge").
ALIASES = ALIASES + tuple(
    Alias(chemical="TDI", geography="Southeast Asia", source_id=source,
          published_as="REGIONAL_MARKET", read_as="CFR",
          reason=("Fills months inside the hole in the Southeast Asia CFR history. Brought back "
                  "and glued onto CFR, Stefan, 13 October 2026."))
    for source in ("imarc_tdi", "businessanalytiq_tdi")
)

CUTS: tuple[Cut, ...] = (
    Cut(source_id="europe_delivered_purchases",
        reason=("The same purchases counted twice. Every one of its 117 monthly values is the "
                "arithmetic mean of fnag_switzerland for that month - all 39 months, all three "
                "chemicals - so it drew a second European line from the first one's own numbers. "
                "Stefan, 26 September 2026: remove.")),
    # The Southeast Asia regional assessment was set aside here on 28 September 2026. Brought back
    # on 13 October 2026 and glued onto the CFR line: see the alias below.
    Cut(chemical="TDI", geography="South Asia", basis="CFR", source_id="echemi_tdi",
        judged_until="2026-09-28",
        reason=("Five readings, one publisher, 2,600 USD/MT on every one of the five days, and no "
                "history at all behind them. The same publisher priced Southeast Asia on the same "
                "five days at 2,300 - a flat 300 USD/MT, 13.04%, apart on all five, with no date "
                "on which the two agree. Stefan, 28 September 2026, having read both: \"the "
                "series with 5 data points I think we should drop\".")),
    Cut(chemical="TDI", geography="Europe", basis="DAP", source_id="fnag_switzerland",
        reason=("European TDI on delivered terms was drawn from 78 purchase readings running "
                "November 2013 to January 2017. The licensed contract assessment covers the same "
                "market on the same terms with 440 readings to August 2023, and the two overlap "
                "almost completely. Stefan, 28 September 2026: \"since the overlap is almost "
                "100% yes we are dropping the DAP series and relabel the FD as DAP\". The "
                "purchase readings stay in the store as HTC's own evidence; they stop being "
                "drawn.")),
    Cut(chemical="TDI", geography="China", basis="EXW", source_id="foampartner_china",
        after="2015-05-01",
        reason=("ICIS takes over as the spine from 6 May 2015, weekly and unbroken. A monthly "
                "purchase record alongside it adds nothing and draws a second line. The join is "
                "11,365 CNY on 1 May against ICIS five days later, a 4.5% step.")),
    Cut(chemical="TDI", geography="China", basis="EXW", source_id="kgi_thailand",
        before="2023-08-10",
        reason=("KGI runs alongside ICIS for two years before ICIS stops. Its job is to carry the "
                "line after 9 August 2023, not to draw a second one before it. Over the nine months "
                "they overlap KGI sits at 1.058 of ICIS, and the join itself is +1.4%.")),
    Cut(chemical="TDI", geography="China", basis="EXW", source_id="sunsirs_tdi",
        days=("2024-01-05", "2024-02-09", "2024-03-08", "2024-06-07"),
        reason=("These four sit on top of KGI, which already carries January to July 2024 on the "
                "same footing as the ICIS stretch before it. SunSirs picks the line up on 9 August "
                "2024, where the step to KGI's last reading is 0.4%.")),
    # judged_until is the last of the five readings the ruling describes (7 to 11 September 2026);
    # the date of the ruling itself is not recorded.
    Cut(chemical="TDI", geography="China", basis="FOB", source_id="echemi_tdi",
        judged_until="2026-09-11",
        reason=("2,250 USD on all five days it was published. ECHEMI prints a standing export price "
                "list beside its assessed domestic price; a number that does not move for a week is "
                "a list, not a market.")),
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _day(value: Any) -> str:
    return _text(value)[:10]


def basis_for(identity: dict[str, Any], source_id: str) -> str:
    """The terms a reading is read on, after any alias."""
    published = _text(identity.get("commercial_basis"))
    for alias in ALIASES:
        if (_text(identity.get("product_family")) == alias.chemical
                and _text(identity.get("market_geography")) == alias.geography
                and _text(source_id) == alias.source_id
                and published == alias.published_as):
            return alias.read_as
    return published


# The application's definition of a market. One function, and the only one.
#
# These four fields are what `customer_catalogue` has always keyed on, and the 290 markets on Data
# & Sources are what they produce. The key was never written down, though - six places built the
# same tuple inline, and two of them (the Data & Sources feed rows and its monthly price matrix)
# read the raw packaging where the other four read the stated one, so a store holding `UNKNOWN`
# beside `` would have counted one market as two on that page alone.
#
# It is computed PER OBSERVATION, not per identity, because `basis_for` reads the source: an alias
# can move one identity between markets depending on which publisher the reading came from. A
# caller with no reading in hand passes no source_id and gets the identity's own basis.
MarketKey = tuple[str, str, str, str]


def market_key(identity: dict[str, Any], source_id: str = "") -> MarketKey:
    """(chemical, geography, terms, packaging) - the four fields that make one market."""
    return (
        _text(identity.get("product_family")),
        _text(identity.get("market_geography")),
        basis_for(identity, source_id),
        stated_packaging(identity.get("packaging")),
    )


def control_key(key: MarketKey) -> MarketKey:
    """The same market as `customer_controls` files it: terms upper case, the rest untouched.

    Stefan's Yes and No are stored in a JSON file keyed by the four fields with the terms
    upper-cased, so a choice written when a page printed "cfr" still matches a reading read as
    CFR. That is a spelling of the market key, not a second key - but three places built it from
    the identity on their own, and one of them (`is_drawn`) reached past `market_key` to do it.
    """
    chemical, geography, terms, packaging = key
    return (chemical, geography, terms.upper(), packaging)


def terms_of(identity: dict[str, Any], source_id: str) -> str:
    """The terms this series is on. Never decides which readings share a line."""
    basis = basis_for(identity, source_id)
    chemical = _text(identity.get("product_family"))
    geography = _text(identity.get("market_geography"))
    source = _text(source_id)

    def matches(rule: Terms) -> bool:
        return (basis == rule.published_as
                and rule.chemical in ("", chemical)
                and rule.geography in ("", geography)
                and rule.source_id in ("", source))

    # Named rules first, then the ones that speak for a kind of price.
    for rule in sorted(TERMS, key=lambda r: not (r.chemical and r.geography and r.source_id)):
        if matches(rule):
            return rule.terms_are
    return basis


def terms_named(chemical: str, geography: str, basis: str) -> str:
    """The terms a market's series carries, where no source is in hand.

    A market's rulings agree or there is nothing to state: two publishers of one series ruled
    differently would make the selector say two things about one entry.

    It has to read a ruling the same way `terms_of` does, wildcards and all. It did not: it matched
    a chemical and a geography by name, so the general regional-assessment ruling - the one that
    says every price published with no terms on it is read as delivered - was invisible to it, and
    the selector went on offering "Regional assessment" as a set of terms for 22 markets whose own
    readings were already being read as DAP.
    """
    chemical, geography, basis = _text(chemical), _text(geography), _text(basis)
    named = [rule for rule in TERMS
             if rule.published_as == basis
             and rule.chemical in ("", chemical)
             and rule.geography in ("", geography)]
    # A ruling that names this market beats one that speaks for a kind of price, as in `terms_of`.
    names = {rule.terms_are for rule in
             ([r for r in named if r.chemical and r.geography] or named)}
    return names.pop() if len(names) == 1 else basis


def is_drawn(identity: dict[str, Any], source_id: str, observation_date: Any) -> bool:
    """Whether this reading is drawn on the market's line."""
    day = _day(observation_date)
    basis = basis_for(identity, source_id)
    # Stefan's own Yes on the Data & Sources table puts a series on the customer pages, and with it
    # the readings a ruling had set aside (13 October 2026: "how else am I going to take control").
    forced_in = customer_controls.shown()
    if forced_in and control_key(market_key(identity, source_id)) in forced_in:
        return True
    for cut in CUTS:
        if _text(source_id) != cut.source_id:
            continue
        if cut.chemical and _text(identity.get("product_family")) != cut.chemical:
            continue
        if cut.geography and _text(identity.get("market_geography")) != cut.geography:
            continue
        if cut.basis and basis != cut.basis:
            continue
        if cut.judged_until and day > cut.judged_until:
            continue       # newer than what the ruling judged: drawn, and judged in the table
        if not (cut.days or cut.after or cut.before):
            return False   # a cut with no date bound sets the source aside entirely
        if cut.days and day in cut.days:
            return False
        if cut.after and day > cut.after:
            return False
        if cut.before and day < cut.before:
            return False
    return True


def ruling_for(identity: dict[str, Any], source_id: str, observation_date: Any) -> "Cut | None":
    """The ruling that sets this reading aside, or None when it is drawn."""
    if is_drawn(identity, source_id, observation_date):
        return None
    day = _day(observation_date)
    basis = basis_for(identity, source_id)
    for cut in CUTS:
        if cut.judged_until and day > cut.judged_until:
            continue
        if (_text(source_id) == cut.source_id
                and (not cut.chemical or _text(identity.get("product_family")) == cut.chemical)
                and (not cut.geography or _text(identity.get("market_geography")) == cut.geography)
                and (not cut.basis or basis == cut.basis)):
            return cut
    return None


def forced_onto_pages(identity: dict[str, Any]) -> bool:
    """Whether Stefan has put this identity's series on the customer pages by hand.

    Matched on chemical, geography and packaging, and on terms either as published or as an alias
    reads them, because the table names a series by the terms it is drawn on.
    """
    wanted = customer_controls.shown()
    if not wanted:
        return False
    # No source in hand, so `market_key` returns the terms the publisher printed - which is what
    # this wants: the aliases are tried separately, just below.
    chemical, geography, published, packaging = control_key(market_key(identity))
    for w_chemical, w_geography, w_terms, w_packaging in wanted:
        if (w_chemical, w_geography, w_packaging) != (chemical, geography, packaging):
            continue
        if published == w_terms:
            return True
        if any(alias.chemical == chemical and alias.geography == geography
               and alias.published_as.upper() == published and alias.read_as.upper() == w_terms
               for alias in ALIASES):
            return True
    return False


def set_aside(identity: dict[str, Any], source_id: str, observation_date: Any) -> str:
    """Why a reading is not drawn, for the record. Empty when it is drawn."""
    if is_drawn(identity, source_id, observation_date):
        return ""
    day = _day(observation_date)
    basis = basis_for(identity, source_id)
    for cut in CUTS:
        if cut.judged_until and day > cut.judged_until:
            continue
        if (_text(source_id) == cut.source_id
                and (not cut.chemical or _text(identity.get("product_family")) == cut.chemical)
                and (not cut.geography or _text(identity.get("market_geography")) == cut.geography)
                and (not cut.basis or basis == cut.basis)):
            return cut.reason
    return "set aside"
