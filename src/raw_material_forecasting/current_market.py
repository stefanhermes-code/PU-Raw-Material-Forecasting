from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Sequence

from raw_material_forecasting import price_kinds
from raw_material_forecasting.market_basis import names_delivery_terms

UNKNOWN = "UNKNOWN"
UNKNOWN_PACKAGING_TOKENS = {"", "UNKNOWN", "UNSPECIFIED", "NOT SPECIFIED", "NOT_SPECIFIED", "N/A", "NA", "NONE", "NULL"}


def _date_only(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def business_day_age(value: Any, as_of: date) -> int | None:
    """Legacy compatibility helper; Current Market no longer uses freshness cutoffs."""
    observed = _date_only(value)
    if observed is None:
        return None
    if observed >= as_of:
        return 0
    age = 0
    cursor = observed
    while cursor < as_of:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            age += 1
    return age


def _dimension(value: Any) -> str:
    text = str(value).strip() if value is not None else ""
    return text or UNKNOWN


def canonical_packaging(value: Any) -> str:
    text = str(value).strip() if value is not None else ""
    return UNKNOWN if text.upper() in UNKNOWN_PACKAGING_TOKENS else text


# Purchase records (FP, FPB, KGI) are stored as `transaction_price`. They were kept out of every
# customer view as one buyer's prices. Stefan, 13 October 2026: they are past prices from those
# companies and are used where they help, like any other market evidence. Nothing is internal-only
# by type any more; the set is kept so the rule has one place to live if that changes.
# Kinds the market view leaves out entirely. Empty, and deliberately: an ask is kept off the
# customer pages by `market_selection_ui`'s published filter, not here, so the Market Intelligence
# page can still show a producer's posting as the evidence it is.
INTERNAL_OBSERVATION_TYPES: frozenset[str] = frozenset()


# A basis is what a customer would be quoted on. An identity without one cannot be sold, and in
# practice it has always been a duplicate of a sibling that does have one - "East China" arrived twice
# from SunSirs, once as EXW and once with nothing at all, and the empty one won the landing page.
UNQUOTABLE_BASIS_TOKENS = {"", "UNKNOWN", "NONE", "NULL", "NOT SPECIFIED", "NOT_SPECIFIED", "N/A"}


def basis_of(row: dict[str, Any]) -> str:
    value = row.get("commercial_basis")
    if value in (None, ""):
        value = row.get("incoterm_or_commercial_basis")
    return str(value or "").strip()


def quotable(row: dict[str, Any]) -> bool:
    """Whether this identity is one a customer could be given a price for."""
    return basis_of(row).upper() not in UNQUOTABLE_BASIS_TOKENS


def customer_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows fit to appear in a customer's market selection."""
    return [row for row in market_rows(rows) if quotable(row)]


def observation_type_of(row: dict[str, Any]) -> str:
    """The kind of price, as one of the four `price_kinds` holds (masterplan A, item 6).

    It used to default to the literal "market_price", which was one of the old seven and is no
    longer a value the store writes. `kind_of` folds any of the seven a stale caller still hands
    over, so this answers in the current vocabulary whatever it is given.
    """
    return price_kinds.kind_of(row.get("observation_type"))


def market_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows that may inform a published market view."""
    return [row for row in rows if observation_type_of(row) not in INTERNAL_OBSERVATION_TYPES]


def identity_dimensions(identity: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        _dimension(identity.get("product_family")),
        _dimension(identity.get("market_geography")),
        _dimension(identity.get("commercial_basis") or identity.get("incoterm_or_commercial_basis")),
        canonical_packaging(identity.get("packaging")),
    )


def cascade_options(
    identities: Iterable[dict[str, Any]],
    *,
    chemical: str | None = None,
    geography: str | None = None,
    incoterm: str | None = None,
) -> dict[str, tuple[str, ...]]:
    rows = customer_rows(identities)
    chemicals = tuple(sorted({identity_dimensions(row)[0] for row in rows}))

    by_chemical = [row for row in rows if chemical is None or identity_dimensions(row)[0] == chemical]
    geographies = tuple(sorted({identity_dimensions(row)[1] for row in by_chemical}))

    by_geography = [
        row
        for row in by_chemical
        if geography is None or identity_dimensions(row)[1] == geography
    ]
    incoterms = tuple(sorted({identity_dimensions(row)[2] for row in by_geography}))

    by_incoterm = [
        row
        for row in by_geography
        if incoterm is None or identity_dimensions(row)[2] == incoterm
    ]
    packaging = tuple(sorted({identity_dimensions(row)[3] for row in by_incoterm}))

    return {
        "chemicals": chemicals,
        "geographies": geographies,
        "incoterms": incoterms,
        "packaging": packaging,
    }


def preferred_identity(identities: Iterable[dict[str, Any]],
                       observations: Iterable[dict[str, Any]] | None = None,
                       references: Iterable[dict[str, Any]] | None = None,
                       as_of: date | None = None) -> dict[str, Any] | None:
    """The market the page should open on.

    It used to be `references[0]` - the first entry of a list built by walking identities in
    `product_family, id` order, where `id` is a UUID. The landing market was therefore chosen by a
    random string, and it chose TDI East China: two observations, no incoterm, seven weeks stale,
    while TDI China EXW sat alongside with sixty-one observations and that day's price.

    The replacement ranks by evidence, in the order a person would: how recent the newest reading is,
    then how many independent publishers stand behind it, then how much history there is. Only
    identities a customer could actually be quoted on are eligible.
    """
    return best_row(customer_rows(identities), observations, references, as_of=as_of)


# How far behind today a market may be and still count as current, when nothing better is known
# about its publisher's cadence.
CURRENT_WITHIN_DAYS = 7
# A publisher is judged against its own rhythm. Two missed prints is late; one is a holiday.
MISSED_PRINTS_ALLOWED = 2


def _typical_gap(days: Sequence[date]) -> float:
    """Roughly how often this publisher prints, in days, from the series itself.

    The median gap rather than the mean: one long break in an otherwise daily series is a holiday or
    an outage, and should not turn a daily publisher into a monthly one.
    """
    ordered = sorted(set(days))
    if len(ordered) < 2:
        return float(CURRENT_WITHIN_DAYS)
    gaps = sorted((ordered[i + 1] - ordered[i]).days for i in range(len(ordered) - 1))
    return float(gaps[len(gaps) // 2]) or 1.0


def best_row(rows: Sequence[dict[str, Any]],
             observations: Iterable[dict[str, Any]] | None = None,
             references: Iterable[dict[str, Any]] | None = None,
             as_of: date | None = None) -> dict[str, Any] | None:
    """The best-evidenced market among `rows`: current, then corroborated, then deep.

    Recency is a threshold rather than a ranking. Ranking on it outright says a market whose single
    reading arrived at 03:25 this morning beats five years of daily history that last printed
    yesterday, which is exactly what happened the day the producer-posting lane began collecting:
    pMDI China moved from EXW with fifty readings to a producer list price with one, and drew a chart
    with a single dot on it. Among markets that are current, the one with the most publishers behind
    it wins, then the one with the most history. Recency decides only a tie.
    """
    rows = list(rows)
    if not rows:
        return None

    latest: dict[str, date] = {}
    sources: dict[str, set[str]] = {}
    counts: dict[str, int] = {}
    seen: dict[str, list[date]] = {}
    for observation in observations or ():
        key = str(observation.get("identity_key") or "")
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
        source = str(observation.get("source_id") or "")
        if source:
            sources.setdefault(key, set()).add(source)
        day = _date_only(observation.get("observation_date") or observation.get("publication_date"))
        if day:
            seen.setdefault(key, []).append(day)
            if key not in latest or day > latest[key]:
                latest[key] = day

    referenced = {str(row.get("identity_key") or "") for row in references or ()}
    today = as_of or date.today()

    def current(key: str) -> int:
        """Whether this market is still being published, judged against its own cadence.

        Measured against today, and against the publisher's own rhythm rather than against the
        freshest market on the page. Both of those were wrong and they were wrong together: TDI
        Europe opened on an ECHEMI CFR series of five identical prints that stopped on 11 September
        when ECHEMI blocked us, in front of a businessanalytiq assessment that is monthly, moves,
        and collected this morning. A daily publisher ten days silent is stale; a monthly one twenty
        days old is on time.
        """
        day = latest.get(key)
        if not day:
            return 0
        allowed = max(CURRENT_WITHIN_DAYS, MISSED_PRINTS_ALLOWED * _typical_gap(seen.get(key, ())))
        return 1 if (today - day).days <= allowed else 0

    def rank(row: dict[str, Any]):
        key = str(row.get("identity_key") or "")
        day = latest.get(key)
        return (
            1 if key in referenced or not referenced else 0,   # a published reference first
            1 if names_delivery_terms(row.get("commercial_basis")) else 0,  # a market before an index
            current(key),                                       # then anything still being published
            len(sources.get(key, ())),                          # then the best corroborated
            counts.get(key, 0),                                 # then the longest history
            day or date.min,                                    # and recency breaks a tie
        )

    return max(rows, key=rank)


# Which element of `identity_dimensions` each selector reads.
_DIMENSION_INDEX = {"chemical": 0, "geography": 1, "incoterm": 2, "packaging": 3}


def best_option(identities: Iterable[dict[str, Any]], dimension: str, *,
                observations: Iterable[dict[str, Any]] | None = None,
                references: Iterable[dict[str, Any]] | None = None,
                chemical: str | None = None, geography: str | None = None,
                incoterm: str | None = None, as_of: date | None = None) -> str | None:
    """Which value a selector should open on, given the choices already made above it.

    The same evidence rule as the landing market, applied one dimension at a time. Returns None when
    nothing in the store matches the narrowing, and the caller keeps its own fallback.
    """
    index = _DIMENSION_INDEX[dimension]
    rows = [
        row for row in customer_rows(identities)
        if (chemical is None or identity_dimensions(row)[0] == chemical)
        and (geography is None or identity_dimensions(row)[1] == geography)
        and (incoterm is None or identity_dimensions(row)[2] == incoterm)
    ]
    best = best_row(rows, observations, references, as_of=as_of)
    return identity_dimensions(best)[index] if best else None


def find_identity(
    identities: Iterable[dict[str, Any]],
    chemical: str,
    geography: str,
    incoterm: str,
    packaging: str | None = None,
    observations: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """The market for this selection. Packaging is matched only when a caller asks for it.

    Stefan, 23 September 2026: no price we hold is packaging-specific - 68 of 69 markets carry no
    packaging at all - so the customer screens stopped asking for it. The field stays in the store
    and this argument stays here, for the day a packaging-specific price arrives.

    `observations` is what makes the choice between several series a decision rather than an
    accident. Pass it and a series still being priced is preferred, then the one with the most
    readings; leave it out and the old packaging-alias order stands, which is all the three tests
    that call this without it ever needed.

    WHY IT HAD TO BECOME A DECISION (Stefan, 2 November 2026)
    ---------------------------------------------------------
    "No there is current market price. Check again." The Current Market card read

        MARKET PRICE   USD 1,959 / MT   Aliphatic Polyester Polyol China   PRICED 2026-10-30

    while the Forecast page, on the same market on the same day, said "Market indication. There is
    not yet enough price history for a forecast."

    `ALIPHATIC_POLYESTER_POLYOL | China | EXW` holds five series - two `deal` series of 41 monthly
    readings ending **2017-05-01**, and three `assessed` series of nine readings that began on
    2026-09-28. None of them carries packaging, so every candidate scored 0 on the packaging rank
    and `min` returned **whichever happened to be first in the list**. It returned a 2017 series.

    So the card took its "Market price" kicker from a series that stopped nine years ago, while
    taking the price itself from the 2026 readings - which is why it could show a 2026 date beside
    a claim resting on 2017. The label was decided by list order.

    This is the same rule as `app.forecast_identity_for` (v1.2.57), which had the same defect with a
    different wrong tie-break: there it was the reading count, here there was no tie-break at all.
    Three places choose which stored series IS a market, and they disagreed; `identity_keys[0]`,
    the third, is written up in the masterplan and not Stefan's to lose by accident.
    """
    target = (chemical, geography, incoterm, packaging)
    candidates = [row for row in customer_rows(identities)
                  if identity_dimensions(row)[:3] == target[:3]
                  and (packaging is None or identity_dimensions(row)[3] == packaging)]
    if not candidates:
        return None

    def packaging_alias_rank(row: dict[str, Any]) -> int:
        raw = _dimension(row.get("packaging"))
        return 0 if raw == canonical_packaging(row.get("packaging")) else 1

    if observations is None:
        return min(candidates, key=packaging_alias_rank)

    held: dict[str, int] = {}
    newest: dict[str, str] = {}
    for row in observations:
        if row.get("price") is None:
            continue
        key = str(row.get("identity_key"))
        held[key] = held.get(key, 0) + 1
        day = str(row.get("observation_date") or "")[:10]
        if day > newest.get(key, ""):
            newest[key] = day

    def rank(row: dict[str, Any]) -> tuple:
        key = str(row.get("identity_key"))
        return (-_priced_this_year(newest.get(key, "")), -held.get(key, 0),
                packaging_alias_rank(row))

    return min(candidates, key=rank)


#: The year from which a series counts as one we still price. The same number the Forecast page
#: states as `FORECAST_FROM_YEAR`, kept here because this module cannot import the page.
PRICED_FROM_YEAR = 2026


def _priced_this_year(newest_day: str) -> int:
    year = str(newest_day or "")[:4]
    return 1 if year.isdigit() and int(year) >= PRICED_FROM_YEAR else 0



def _canonical_full_identity(identity: dict[str, Any]) -> tuple[str, str, str, str, str, str, str]:
    return (
        _dimension(identity.get("product_family")),
        _dimension(identity.get("product_grade_or_chemistry")),
        _dimension(identity.get("market_geography")),
        _dimension(identity.get("commercial_basis") or identity.get("incoterm_or_commercial_basis")),
        canonical_packaging(identity.get("packaging")),
        _dimension(identity.get("currency")),
        _dimension(identity.get("unit")),
    )


def equivalent_identity_keys(
    identities: Iterable[dict[str, Any]],
    selected_identity: dict[str, Any],
) -> tuple[str, ...]:
    """Every stored series that is the selected market, **the selected one first**.

    One market can be held as several series. Packaging aliases are the reason this function
    exists - a legacy "UNSPECIFIED" beside a canonical blank is one market written twice - and
    `observation_type` is the other: an `assessed` series and a `deal` series of the same
    chemical, grade, geography, basis, currency and unit are two records of one market, kept
    apart because an assessment and a transaction are not the same kind of price.

    Callers use the set two ways, and the second needs an order. `build_current_market_state`
    pools every key in it for today's price, which is the whole point. But both pages then take
    `identity_keys[0]` as THE series to speak for the market - the Forecast page hands it to
    `_load_forecast`, and `build_current_market_state` reports it and reads its currency, unit
    and origin - so the first element is a decision, not a detail.

    THE PICK WAS MADE AND THEN DISCARDED (Stefan, 3 November 2026: "Yes, fix those five")
    -------------------------------------------------------------------------------------
    This returned `sorted(...)`, so the series the caller had just chosen was replaced by
    whichever key sorted first. The keys end in the observation type, so `assessed` beat `deal`
    every time, and the Forecast page ran on the assessed series whatever `forecast_identity_for`
    had decided. Measured against the live store on 3 November 2026, four of the 59 lanes the
    page offers forecast a series the pick did not choose, and on every one of them the chosen
    series was the long record and the alphabetical winner a stub:

        POLYETHER_POLYOL | India         | CIF    assessed    2 readings, Aug 2026 only
                                                  deal      114 readings, 2000-01 .. 2026-02
        POLYETHER_POLYOL | Japan         | CIF    assessed    1 reading,  Aug 2026 only
                                                  deal      114 readings, 2000-01 .. 2026-08
        POLYETHER_POLYOL | United States | CIF    assessed    1 reading,  Aug 2026 only
                                                  deal       81 readings, 2000-01 .. 2026-07
        POLYETHER_POLYOL | United States | FOB    assessed    1 reading,  Aug 2026 only
                                                  deal      100 readings, 2000-01 .. 2026-07

    All four showed the reader no forecast at all - one reading cannot clear `enough_history` -
    while the series beside it carried a fitted model, and on three of them a **SUCCEEDED governed
    forecast already computed and stored**. Japan CIF, United States CIF and United States FOB
    had an answer in the store that no customer could see.

    This is the third and last copy of one rule. `forecast_identity_for` had it with the reading
    count as its only tie-break (fixed v1.2.57); `find_identity` had it with no tie-break at all
    (fixed v1.2.58); here the pick was made correctly and then thrown away. Each was written
    where it was needed and none knew about the others.

    On Current Market nothing moves: measured the same day, the chosen series was already the
    alphabetical first on all 13 cards. What changes there is that it no longer depends on that.
    """
    target = _canonical_full_identity(selected_identity)
    keys = [
        str(row.get("identity_key"))
        for row in customer_rows(identities)
        if row.get("identity_key") and _canonical_full_identity(row) == target
    ]
    chosen = str(selected_identity.get("identity_key") or "")
    rest = sorted(dict.fromkeys(key for key in keys if key != chosen))
    # Only when the selection is itself one of the customer's series. It is not when the caller
    # hands in a row the entitlement filter dropped, and then the old order is all there is.
    return (chosen, *rest) if chosen in set(keys) else tuple(rest)

def same_kind_of_price(
    identities: Iterable[dict[str, Any]],
    identity_keys: Sequence[str],
) -> tuple[str, ...]:
    """The keys of `identity_keys` that are the same KIND of price as the first one.

    `equivalent_identity_keys` crosses two dimensions, and they are not alike. Packaging aliases
    are one market written twice and must be pooled - that is why the set exists. But
    `observation_type` is also outside the canonical identity, so an `assessed` series and a
    `deal` series of the same chemical, grade, geography, basis, currency and unit land in one
    set, and they are two different prices for one market rather than two spellings of one price.
    An assessment is what a publisher says the market is; a deal is what somebody paid.

    Averaging them is not a market level. Pool the whole set and the number that comes out belongs
    to neither series, and the difference shows up as a move on any screen that puts it beside a
    figure built from one of them.

    IT DID (measured 3 November 2026, immediately after the pick was honoured)
    --------------------------------------------------------------------------
    Four Polyether Polyol lanes gained a forecast once `equivalent_identity_keys` stopped
    discarding the pick. Their banners then read today's price pooled across both kinds against a
    forecast built from the `deal` series alone:

        lane                                  pooled "today"   the deal series   on the banner
        POLYETHER_POLYOL | India | CIF           3,189.79          1,313.22          -57.9%
        POLYETHER_POLYOL | Japan | CIF           3,576.95          2,873.91          -20.0%
        POLYETHER_POLYOL | United States | CIF   3,112.60          2,970.57           -7.3%
        POLYETHER_POLYOL | United States | FOB   4,235.50          4,069.00           -3.5%

    None of those four moves happened. On one kind of price throughout, the same four banners read
    +2.4%, -0.4%, -2.8% and +0.5%.

    Of the 59 lanes the Forecast page offers, 5 hold more than one kind of price and all five are
    Polyether Polyol. Of the 13 markets on Current Market, **none** does, so this rule costs
    nothing there. It is applied on both pages anyway: a rule that happens to be a no-op is still
    the rule, and this application has now been bitten three times by one rule kept in several
    copies, each written where it was needed.

    The chart is left on the whole set deliberately. Two kinds of price for one market are worth
    SEEING side by side, each in its own colour, which is what Current Market's caption already
    says. It is the single headline figure that cannot be an average of both.
    """
    keys = tuple(str(value) for value in identity_keys)
    if len(keys) < 2:
        return keys
    # `observation_type_of`, not the raw field: the module folds a stale label to one of the four
    # kinds everywhere else it asks this question, and two spellings of one kind are one kind.
    kinds = {str(row.get("identity_key")): observation_type_of(row) for row in identities}
    wanted = kinds.get(keys[0], "")
    return tuple(key for key in keys if kinds.get(key, "") == wanted) or keys


def verification_level(source_count: int) -> str:
    if source_count <= 0:
        return "Unavailable"
    if source_count == 1:
        return "Limited"
    if source_count == 2:
        return "Moderate"
    return "Strong"


@dataclass(frozen=True)
class CurrentMarketState:
    identity_key: str
    is_current: bool
    current_value: float | None
    current_date: str | None
    latest_value: float | None
    latest_date: str | None
    reference_age_business_days: int | None
    verification: str
    evidence_low: float | None
    evidence_high: float | None
    currency: str | None
    unit: str | None
    source_count: int
    # Whether we can say where the material was made. A CFR or CIF price is quoted at the buyer's
    # end and its publisher states no origin, so it is a real market indication but not something
    # HTC can govern - see `attributed_origin` below.
    origin: str | None = None
    attributed: bool = True


UNATTRIBUTED_ORIGINS = {"", "UNKNOWN", "UNSPECIFIED", "NOT SPECIFIED", "NOT_SPECIFIED"}


def evidence_spread(state: "CurrentMarketState") -> tuple[float, float] | None:
    """How far the lowest and highest publisher sit from the price we publish, in per cent.

    Two numbers in a currency say how far apart the publishers are. They do not say whether that is
    a market in agreement or a market that has come apart, because the answer depends on the level:
    37 dollars on 2,700 is a rounding difference and 37 dollars on 300 is a different market. The
    per cent answers it at a glance and is the same question a reader asks of the forecast band.

    Signed against the published price, so the two sides can differ - the mean of four publishers
    does not sit halfway between the lowest and the highest unless the middle two are symmetric.
    """
    value = state.current_value
    if not state.is_current or not value:
        return None
    if state.evidence_low is None or state.evidence_high is None:
        return None
    return ((state.evidence_low - value) / value * 100.0,
            (state.evidence_high - value) / value * 100.0)


def attributed_origin(rows: Iterable[dict[str, Any]]) -> tuple[str | None, bool]:
    """(origin, whether it is attributable) for a set of rows describing one identity.

    Attribution is what separates a price HTC can govern from one it can only quote. China EXW and
    China FOB are quoted at the seller's end, so the publisher's geography IS the origin. A CFR or
    CIF price is quoted at the buyer's end and says nothing about who made the material - it is a
    real observation of a market, but not a governed price for anyone's material.

    When no row carries an origin at all - an engine older than v0.4.70 - nothing is demoted. The
    engine is the authority, and silence must not be read as "unattributed".
    """
    origins = {str(row.get("origin") or "").strip() for row in rows}
    origins.discard("")
    if not origins:
        return None, True
    known = {value for value in origins if value.upper() not in UNATTRIBUTED_ORIGINS}
    if not known:
        return sorted(origins)[0], False
    return sorted(known)[0], True


def _iso_date(value: Any) -> str | None:
    parsed = _date_only(value)
    return parsed.isoformat() if parsed is not None else None


def _row_recency_key(row: dict[str, Any]) -> tuple[datetime, str]:
    value = row.get("available_from") or row.get("publication_date") or row.get("observation_date")
    text = str(value or "").replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = datetime.min
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed, str(row.get("observation_id") or "")


def _source_windows(snapshot) -> dict[str, int]:
    """How long each source's last print stays current, from its own publication cadence."""
    from raw_material_forecasting.source_status import window_by_source

    sources = [dict(row) for row in getattr(snapshot, "sources", []) or []]
    return window_by_source(sources) if sources else {}


def _contributing_rows(rows: list[dict[str, Any]], *, windows: dict[str, int] | None = None,
                       default_window: int = 14) -> list[dict[str, Any]]:
    """Each source's most recent price, provided it is still fresh. One row per source.

    The previous rule kept only rows carrying the single most recent observation date across the whole
    identity, and discarded every source that had not published that same day. That made the headline
    reference move with *publication timing* rather than with the market: on a day all four China EXW
    publishers post, the reference is a four-source mean and reads "Strong"; the next day one posts
    and the headline jumps to that publisher's number and reads "Limited", with no market event behind
    it. It also biased the reference toward whichever source publishes most often.

    A source's own cadence decides how long its last print stays current - a monthly publisher has not
    gone stale because it did not print today - which is the same rule the price matrix already uses.
    """
    windows = windows or {}
    dated = [(row, _date_only(row.get("observation_date"))) for row in rows if row.get("price") is not None]
    dated = [(row, day) for row, day in dated if day is not None]
    if not dated:
        return []

    latest_by_source: dict[str, tuple[dict[str, Any], date]] = {}
    for row, day in dated:
        source_id = str(row.get("source_id") or "")
        if not source_id:
            continue
        current = latest_by_source.get(source_id)
        if current is None or (day, _row_recency_key(row)) > (current[1], _row_recency_key(current[0])):
            latest_by_source[source_id] = (row, day)

    if not latest_by_source:
        return []

    # Freshness is measured against the newest print we hold, not against the wall clock: a snapshot
    # read at the weekend must not quietly drop every source.
    anchor = max(day for _row, day in latest_by_source.values())
    fresh = {
        source_id: row
        for source_id, (row, day) in latest_by_source.items()
        if (anchor - day).days <= windows.get(source_id, default_window)
    }
    return [fresh[source_id] for source_id in sorted(fresh)]


@dataclass(frozen=True)
class ReferencePoint:
    """One day of the market reference for a selection, and how many sources stood behind it."""
    day: str
    value: float
    sources: int


def reference_history(snapshot, identity_key: str | Iterable[str]) -> list[ReferencePoint]:
    """The reference for the selected market, day by day.

    Built with the rule the headline uses, applied as at each day rather than only today: each
    source's most recent print that was still current on that day, one row per source, averaged. So
    the last point on the line is the number printed above it, by construction rather than by
    coincidence, and a day when one publisher was quiet does not jump the line to that publisher's
    number.
    """
    identity_keys = {identity_key} if isinstance(identity_key, str) else {str(key) for key in identity_key}
    rows = [
        dict(row) for row in market_rows(getattr(snapshot, "observations", []))
        if row.get("identity_key") in identity_keys and row.get("price") is not None
    ]
    dated = [(row, _date_only(row.get("observation_date"))) for row in rows]
    dated = [(row, day) for row, day in dated if day is not None]
    if not dated:
        return []

    windows = _source_windows(snapshot)
    points: list[ReferencePoint] = []
    for day in sorted({day for _row, day in dated}):
        upto = [row for row, row_day in dated if row_day <= day]
        contributors = _contributing_rows(upto, windows=windows)
        if not contributors:
            continue
        prices = [float(row["price"]) for row in contributors]
        points.append(ReferencePoint(day=day.isoformat(),
                                     value=sum(prices) / len(prices),
                                     sources=len(prices)))
    return points


def state_from_governed_series(points, identity: dict) -> CurrentMarketState:
    """The customer page, built from the engine's governed series rather than from the raw prints.

    The raw prints are stored exactly as the publisher wrote them, which for Chinese domestic
    assessments means inclusive of 13% VAT. The governed series takes the tax out, and the forecast
    is built on the governed series. Reading the page off the prints put the same market at USD 2,698
    here and USD 2,387 on the next screen, both labelled the market price.

    One difference comes with this and it is deliberate. The prints were carried forward inside each
    publisher's own freshness window, so a day when one publisher was quiet still showed three
    sources. A governed date carries only the sources that printed on it, so the source count - and
    with it the verification level - now moves with who actually published that day. That is the
    engine's rule, applied on the screen as well as in the backtest, rather than two rules.
    """
    identity = dict(identity or {})
    rows = [dict(point) for point in points or [] if point.get("reference_value") is not None]
    if not rows:
        return CurrentMarketState(
            identity_key=str(identity.get("identity_key") or ""),
            is_current=False, current_value=None, current_date=None,
            latest_value=None, latest_date=None, reference_age_business_days=None,
            verification="Unavailable", evidence_low=None, evidence_high=None,
            currency=str(identity.get("currency") or "") or None,
            unit=str(identity.get("unit") or "") or None,
            source_count=0,
            origin=str(identity.get("origin") or "") or None,
            attributed=attributed_origin([identity])[1],
        )

    rows.sort(key=lambda row: str(row.get("reference_date") or ""))
    newest = rows[-1]
    value = float(newest["reference_value"])
    sources = int(newest.get("sources") or 1)
    origin, attributed = attributed_origin([identity])
    return CurrentMarketState(
        identity_key=str(identity.get("identity_key") or ""),
        is_current=True,
        current_value=value,
        current_date=str(newest.get("reference_date") or "")[:10] or None,
        latest_value=value,
        latest_date=str(newest.get("reference_date") or "")[:10] or None,
        reference_age_business_days=None,
        verification=verification_level(sources),
        evidence_low=float(newest.get("low", value)),
        evidence_high=float(newest.get("high", value)),
        currency=str(newest.get("currency") or identity.get("currency") or "") or None,
        unit=str(identity.get("unit") or "") or None,
        source_count=sources,
        origin=origin,
        attributed=attributed,
    )


def governed_history(points) -> list[ReferencePoint]:
    """The governed series as the chart wants it."""
    rows = [dict(point) for point in points or [] if point.get("reference_value") is not None]
    rows.sort(key=lambda row: str(row.get("reference_date") or ""))
    return [ReferencePoint(day=str(row["reference_date"])[:10],
                           value=float(row["reference_value"]),
                           sources=int(row.get("sources") or 1))
            for row in rows]


class _ReadingsInPlaceOf:
    """The snapshot as it stands, with the readings handed in where its own were."""

    def __init__(self, snapshot: Any, readings: Sequence[dict[str, Any]]) -> None:
        self._snapshot = snapshot
        self.observations = readings

    def __getattr__(self, name: str) -> Any:
        # Guarded, because `__getattr__` asking for `_snapshot` before `__init__` has set it would
        # ask itself for ever rather than raising.
        if name == "_snapshot":
            raise AttributeError(name)
        return getattr(self._snapshot, name)


def with_readings(snapshot: Any, readings: Sequence[dict[str, Any]]) -> Any:
    """`snapshot` with `readings` in place of its own. Everything else is read straight through.

    `build_current_market_state` is handed a snapshot and does its own filtering by identity key,
    so the only way to give it real prices without giving it every price in the store is to hand it
    a snapshot whose readings are already the market's. That is what a page does once it knows
    which market it is about: its snapshot carries the index, whose prices are 1 rather than
    values, and a banner built from that would read 1.

    Nothing is copied and nothing is written back, so there is one set of identities and one set of
    sources, not a second that could drift from the first.
    """
    return _ReadingsInPlaceOf(snapshot, readings)


def build_current_market_state(snapshot, identity_key: str | Iterable[str], *, as_of: date | None) -> CurrentMarketState:
    del as_of  # Freshness is judged against the newest print we hold, not against the wall clock.
    if isinstance(identity_key, str):
        identity_keys = (identity_key,)
    else:
        identity_keys = tuple(str(value) for value in identity_key)
    primary_identity_key = identity_keys[0] if identity_keys else ""
    identity_key_set = set(identity_keys)
    identities = [dict(row) for row in getattr(snapshot, "identities", [])]
    observations = [
        dict(row)
        for row in getattr(snapshot, "observations", [])
        if row.get("identity_key") in identity_key_set
    ]
    identity = next((row for row in identities if row.get("identity_key") == primary_identity_key), {})
    contributors = _contributing_rows(observations, windows=_source_windows(snapshot))

    if not contributors:
        return CurrentMarketState(
            identity_key=primary_identity_key,
            is_current=False,
            current_value=None,
            current_date=None,
            latest_value=None,
            latest_date=None,
            reference_age_business_days=None,
            verification="Unavailable",
            evidence_low=None,
            evidence_high=None,
            currency=str(identity.get("currency") or "") or None,
            unit=str(identity.get("unit") or "") or None,
            source_count=0,
            origin=str(identity.get("origin") or "") or None,
            attributed=attributed_origin([identity])[1],
        )

    prices = [float(row["price"]) for row in contributors]
    value = sum(prices) / len(prices)
    # Dated to the newest observation that actually contributed, not to whichever row sorts first.
    newest = max(contributors, key=lambda row: (_date_only(row.get("observation_date")) or date.min,
                                                _row_recency_key(row)))
    reference_date = _iso_date(newest.get("observation_date"))
    currency = str(contributors[0].get("currency") or identity.get("currency") or "") or None
    unit = str(contributors[0].get("unit") or identity.get("unit") or "") or None
    source_count = len(contributors)
    origin, attributed = attributed_origin(list(contributors) + ([identity] if identity else []))

    return CurrentMarketState(
        identity_key=primary_identity_key,
        is_current=True,
        current_value=value,
        current_date=reference_date,
        latest_value=value,
        latest_date=reference_date,
        reference_age_business_days=None,
        verification=verification_level(source_count),
        evidence_low=min(prices),
        evidence_high=max(prices),
        currency=currency,
        unit=unit,
        source_count=source_count,
        origin=origin,
        attributed=attributed,
    )


# The engine's rule for whether a series carries enough history to forecast, mirrored here because
# the application reaches the engine over HTTP and cannot import it. Test
# `test_the_history_rule_is_the_engines` holds the two copies equal.
HISTORY_MIN_POINTS = {"daily": 30, "weekly": 12, "monthly": 6}
HISTORY_MIN_DENSITY = {"daily": 0.5, "weekly": 0.6, "monthly": 0.6}
# Europe TDI, pMDI and polyol read the published series and the card's changes: three months for the
# weighted average and one for the first error (Stefan, 22 September 2026).
EUROPE_MIN_MONTHS = 4


# How long a market has to go unpriced before what comes after it is a fresh record rather than a
# continuation of the old one.
#
# WHY THE RULE NEEDED THIS (Stefan, 2 November 2026)
# --------------------------------------------------
# "Check Aliphatic Polyester, there is current market price but no forecast?" - then, when told the
# market was too young: "No there is current market price. Check again." He was right twice.
#
# `ALIPHATIC_POLYESTER_POLYOL | China | EXW` holds 49 drawn readings over 43 distinct months. Six
# months is the bar, so the card read **"Market price"**, USD 1,959/MT, PRICED 2026-10-30. But 41 of
# those months run January 2014 to May 2017 and the other two are September and October 2026. The
# hole between them is **3,437 days - nine years and five months**. On a market we have been
# collecting for five weeks, the card was telling a customer we know it well, on the strength of a
# series that stopped before the pandemic. The Forecast page, reading one series rather than the
# lane, said "Market indication. There is not yet enough price history for a forecast."
#
# Two pages, one market, one day, opposite answers - and
# `test_a_market_indication_is_a_short_history.py` asserts they never disagree.
#
# **Counting distinct months is not the defect; counting them wherever they fall is.** A reading on
# the far side of a multi-year silence is not part of what we know about this market now, and no
# method is going to fit across the hole: `FORECAST_GAP_DAYS = 45` already stops the chart drawing
# through one.
#
# TWO YEARS, AND THE THRESHOLD IS NOT DELICATE
# ---------------------------------------------
# Measured against the live store on 2 November 2026. Of the 13 markets on Current Market, the rule
# changes **two** at every threshold from 180 days to 730 - Aliphatic Polyester Polyol China EXW
# (3,437-day hole) and TDI Southeast Asia CFR (1,125). Nothing else is near: the next largest holes
# belong to TDI China EXW (1,552 days) and Polyether Polyol China EXW (1,522), and both keep 83 and
# 51 months AFTER their hole, so they are untouched.
#
# On the Forecast page's 59 markets the choice does matter, which is why it is two years and not
# six months: 180 days would also take Diethylene Glycol Japan CIF, Polyether Polyol China FOB and
# Polyether Polyol India CIF, whose gaps are an arrears pattern rather than a stopped feed. At 730
# days exactly the same two markets change as on the card.
#
# **No market that carries a forecast today loses it.** Checked directly across all 77 markets with
# a method in force and all 322 stored series: zero. Of the 20 series that stop reading as enough
# history, 19 are dead records last priced between 2013 and 2025, already excluded from both pages
# by their own year and collection rules; the twentieth is TDI Southeast Asia CFR, which has never
# had a method selection in sixteen years - and this is why.
RESTART_GAP_DAYS = 730


def _since_the_restart(dates: list[date]) -> list[date]:
    """The readings from the newest unbroken run, where a hole of `RESTART_GAP_DAYS` breaks it.

    Sparse is not broken. A monthly series that skips months stays one record however many it
    skips, which is what keeps China TDI's 80 months over 153 a forecastable market. A series that
    stopped for years and started again is two records, and only the live one says what we know.
    """
    if not dates:
        return []
    for index in range(len(dates) - 1, 0, -1):
        if (dates[index] - dates[index - 1]).days > RESTART_GAP_DAYS:
            return dates[index:]
    return dates


def _history_period(day: date, frequency: str) -> date:
    if frequency == "daily":
        return day
    if frequency == "weekly":
        return day - timedelta(days=day.weekday())
    return date(day.year, day.month, 1)


def _history_span(first: date, last: date, frequency: str) -> int:
    if frequency == "daily":
        return (last - first).days + 1
    if frequency == "weekly":
        return (last.toordinal() - first.toordinal()) // 7 + 1
    return (last.year - first.year) * 12 + (last.month - first.month) + 1


def enough_history(days, minimum_months: int | None = None, *,
                   across_a_restart: bool = False) -> bool:
    """Whether a market has the price history a forecast needs. Below it, the card says
    "Market indication" (Stefan, 21 September 2026).

    The engine's minimum counts: 6 months of readings, or 12 weeks / 30 days of dense recent
    collection. Coverage is not required of the months. The engine does not require it either:
    a monthly series that fails the coverage test is still forecast monthly, which is how China
    TDI, with 80 months over 153, carries a forecast.

    **Only the record since the market last started being priced counts** - see `RESTART_GAP_DAYS`
    below. Sparse is not the same as broken, and this rule separates the two.

    TWO QUESTIONS, NOT ONE (Stefan, 3 November 2026)
    ------------------------------------------------
    *"Of course it can be used as history for forecasting and used for backtracking/testing various
    forecast methods on."*

    The restart rule was written in v1.2.58 for the price CARD, and it is right there: a market we
    stopped pricing for years and have assessed ourselves for five weeks is a market we can quote
    and not one we can claim to know, so the card reads "Market indication". That is a statement
    about **our own current record**.

    A forecast asks something else: is there enough record to fit a method on, and to test methods
    against. An earlier record of the same market, nine years back, answers that - it carries the
    market's shape, which is what a method is fitted on - without making our five weeks of
    assessment any older. Stefan had already ruled this on 13 October 2026, and `eligibility.py`
    quotes him: *"Each individual series may be not eligible but glued together they could very
    well be."*

    So `across_a_restart=True` is the forecast's question and the default is the card's. The two
    callers that pass it are `app._forecast_history_enough` and `eligibility.judge`, and both are
    reading a history `store_series.forecast_rows` has already glued - see `earlier_record`.
    """
    dates = sorted({d for d in (_date_only(value) for value in days or []) if d is not None})
    if not across_a_restart:
        dates = _since_the_restart(dates)
    if not dates:
        return False
    months = HISTORY_MIN_POINTS["monthly"] if minimum_months is None else minimum_months
    if len({_history_period(d, "monthly") for d in dates}) >= months:
        return True
    for frequency in ("daily", "weekly"):
        periods = sorted({_history_period(d, frequency) for d in dates})
        span = _history_span(periods[0], periods[-1], frequency)
        if (len(periods) >= HISTORY_MIN_POINTS[frequency]
                and len(periods) / span >= HISTORY_MIN_DENSITY[frequency]):
            return True
    return False
