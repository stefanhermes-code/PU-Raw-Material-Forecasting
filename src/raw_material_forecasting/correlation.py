"""Do the chemicals on a raw material's production route carry price information about it?

This is the reason the chemical route graph exists. The graph says which chemicals are upstream of
TDI; this module tests whether their prices actually move ahead of TDI's, and - just as important -
reports what history is missing before that question can be answered at all.

Three rules it will not bend, and one it used to.

**Correlate changes, not levels.** Two price series that both drifted upward over three years
correlate above 0.9 whether or not one has anything to do with the other. Every coefficient here is
computed on month-over-month log changes, which is the part that carries information.

**Never pool two identities into one series.** BASF TDI and Wanhua TDI are two contracts. China EXW
and Thailand delivered are two markets. A series is one governed identity, and identities are
compared, not merged.

**Convert with the rate of the price's own date.** A THB price from July 2021 is a July 2021 dollar
amount. Converting a decade of history at today's rate manufactures a currency trend and hands it to
the correlation as if it were a price signal - on the CNY history alone that is several per cent of
entirely spurious co-movement. `fx.FxTable` already holds every ECB fixing, so each observation is
converted on its own date.

**Grade the evidence; do not throw it away.** This module used to refuse outright below 24
overlapping months. That was wrong. Twenty-three months is not nothing, and a rule that discards it
also discards the 1990s, every thin regional series, and every chemical that has only just started
being collected - exactly the history worth gathering while it can still be found. A sample size is
not a pass/fail gate, it is an uncertainty, so every result now carries one: the coefficient, the
number of months behind it, and a 95% confidence interval computed by Fisher transform. A wide
interval says "not yet" far more usefully than a blank cell, and it says it in a way that keeps
improving as data arrives instead of flipping at an arbitrary line.

The only hard floor left is arithmetic: below `MIN_CORRELATION_MONTHS` the interval is not defined.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from math import log, sqrt, tanh
from statistics import median
from typing import Any, Iterable, Sequence

from .chemical_routes import ChemicalRouteGraph
from .current_market import INTERNAL_OBSERVATION_TYPES  # noqa: F401  (documented relationship)
from .fx import FxTable, as_date as _as_date, normalise_currency as _norm

# The sample below which a correlation is not arithmetic any more - a Fisher interval needs n > 3.
# This is a floor, not a quality bar: everything above it is reported with its uncertainty attached.
#
# Four, not six. Six was a quality judgement wearing an arithmetic one's clothes; the interval
# exists from four changes and is simply very wide there, which is what the reader should be shown
# rather than protected from (Stefan, 19 September 2026).
MIN_CORRELATION_MONTHS = 4

# A SEARCHED lag needs more than a reported correlation does, and for a different reason.
#
# Testing seven lags and keeping the largest coefficient is a search. On a four-point slice the
# largest of seven is near 1.0 as a matter of arithmetic, not of signal - chlorine against TDI 65/35
# once reported +0.95 at lag 5 on three months. Lag zero is not searched and needs only the floor
# above; a lag that won a search has to stand on a sample the search could not manufacture.
#
# This is not a quantity gate. It is the multiple-comparison correction, expressed as a sample size.
MIN_SEARCHED_LAG_MONTHS = 6
# Where the interval becomes narrow enough that the coefficient is worth acting on rather than
# watching. Nothing is hidden below it; it only sets the words used to describe the result.
CONFIDENT_MONTHS = 36
# Kept for callers that still name it. It is a reporting threshold now, never a gate.
MIN_OVERLAP_MONTHS = 24
# Lags tested, in months. Upstream prices lead downstream ones; a negative lead would mean the raw
# material moves first, which is a different claim and is not what this searches for.
MAX_LAG_MONTHS = 6
# Series shorter than this are not worth aligning at all.
# Kept as a description, not a gate: a series shorter than this is reported as short, not dropped.
MIN_SERIES_MONTHS = 6

# The sixth copy of this list, and two spellings short of the store's. One list, in `units`.
TONNE_UNITS = frozenset(u.upper() for u in
                        ("t", "mt", "mts", "ton", "tons", "tonne", "tonnes",
                         "metric ton", "metric tons", "metric tonne", "metric tonnes"))
KILO_UNITS = {"KG", "KILO", "KILOGRAM", "KILOGRAMME"}


MONTHLY = "M"
WEEKLY = "W"
# A pair is limited by its coarser side, so weekly only pays where both series are weekly. It pays a
# lot where it does: the same calendar sampled weekly carries roughly four times the observations,
# and the interval narrows with the square root of that.
# Likewise - it decides whether a series is READ weekly or monthly, never whether it is kept.
MIN_SERIES_WEEKS = 12
# Counting weeks is not enough. A monthly series bucketed into weeks yields one point in four and
# passes a count test while being monthly data wearing weekly clothes: TDI 65/35 shows 36 "weeks"
# across 161, and correlating two such series would mostly find weeks where only one of them has a
# number. A weekly series has to actually be weekly.
MIN_WEEKLY_DENSITY = 0.6
# Lags are searched in periods, so the ceiling has to be expressed per frequency or a weekly search
# would reach back only six weeks where the monthly one reaches six months.
MAX_LAG_WEEKS = 13
# Nothing is correlated daily - `_week` says why, and the noise an assessor leaves in the last digit
# does not divide by four when the sampling does. But a daily print is still worth counting: it is
# the only thing that makes that argument testable, and a series that has not missed a business day
# since September is a different asset from one that prints on Fridays.
#
# This is a question about cadence, not about readiness, so it does not borrow the weekly lane's
# twelve weeks. That bar asks whether a series is long enough to correlate; this one asks only
# whether the publisher prints every day, and twelve weeks of it answered "no" about six feedstocks
# that have not missed a day since September purely because they are eight business days too young.
# Four weeks is the shortest window that separates the two cadences at all - twenty business days
# against four weekly prints - and below it the question cannot be answered either way.
MIN_DAILY_BUSINESS_DAYS = 20
MIN_DAILY_DENSITY = MIN_WEEKLY_DENSITY


# ----------------------------------------------------------------------------- series construction

REFERENCE_TYPE = "reference_series"


@dataclass(frozen=True)
class MonthlySeries:
    """One series, one value per month.

    For a price series that value is USD per tonne. For a reference series it is whatever the
    publisher's own unit is - an index level, usually - and it is never converted, because an index
    has no currency to convert. Both work here because correlation reads month-over-month log
    changes, and the log change of a true price index is the log change of the price it tracks.
    """
    identity_key: str
    chemical: str                 # the route-graph node code this series belongs to
    label: str
    observation_type: str
    market_geography: str
    currency: str                 # the currency it was published/paid in; "" for an index
    points: dict[date, float]     # first of month -> USD/t, or the series' own index level
    unconverted: int = 0
    quality: str = "STRONG"       # STRONG | USABLE_PROXY | WEAK_PROXY
    unit: str = "t"
    is_price: bool = True
    frequency: str = MONTHLY       # "M" or "W" - the period each point covers

    @property
    def is_reference(self) -> bool:
        return self.observation_type == REFERENCE_TYPE

    @property
    def months(self) -> int:
        return len(self.points)

    @property
    def first(self) -> date | None:
        return min(self.points) if self.points else None

    @property
    def last(self) -> date | None:
        return max(self.points) if self.points else None


def _month(value: Any) -> date | None:
    day = _as_date(value)
    return date(day.year, day.month, 1) if day else None


def _week(value: Any) -> date | None:
    """The Monday of the week a date falls in.

    Weeks rather than months because the Chinese assessments move most days but carry an assessor's
    noise in the last digit - two publishers sat 0.9% apart on the same day while agreeing on
    direction. A week averages that out and still keeps four times the resolution of a month.
    """
    day = _as_date(value)
    return day - timedelta(days=day.weekday()) if day else None


def _period(value: Any, frequency: str) -> date | None:
    return _week(value) if frequency == WEEKLY else _month(value)


def _period_index(day: date, frequency: str) -> int:
    return day.toordinal() // 7 if frequency == WEEKLY else _month_index(day)


def _period_start(index: int, frequency: str) -> date:
    if frequency == WEEKLY:
        return date.fromordinal(index * 7)
    return date(index // 12, index % 12 + 1, 1)


def _to_tonne(price: float, unit: Any) -> float | None:
    code = str(unit or "").strip().upper()
    if code in TONNE_UNITS:
        return price
    if code in KILO_UNITS:
        return price * 1000.0
    return None


def _label(row: dict[str, Any], chemical_name: str | None = None) -> str:
    """A readable series name. The chemical keeps the name the seed gives it - chemical names are not
    title-cased, because TDI is TDI and pMDI is pMDI."""
    parts = [chemical_name or str(row.get("product_family") or "?")]
    grade = str(row.get("product_grade_or_chemistry") or "").strip()
    if grade:
        parts.append(grade)
    geography = str(row.get("market_geography") or "").strip()
    if geography:
        parts.append(geography)
    return " · ".join(parts)


def chemical_of(row: dict[str, Any], graph: ChemicalRouteGraph) -> str | None:
    """Which route-graph node this observation belongs to, by code or alias.

    Returns None when the product family is not in the graph. That is not a failure: it means the
    graph does not yet describe this material, and the caller should say so rather than guess.
    """
    family = str(row.get("product_family") or "").strip().upper().replace(" ", "_")
    if not family:
        return None
    if family in graph.nodes:
        return family
    for code, node in graph.nodes.items():
        if any(family == str(alias).strip().upper().replace(" ", "_") for alias in node.aliases):
            return code
    return None


def weekly_density(points: dict[date, float]) -> float:
    """What share of the weeks between the first and last point actually carry one.

    A real weekly series is near 1.0. A monthly series bucketed into weeks sits near 0.23, and that
    is the difference between weekly resolution and the appearance of it.
    """
    if len(points) < 2:
        return 0.0
    days = sorted(points)
    span = _period_index(days[-1], WEEKLY) - _period_index(days[0], WEEKLY) + 1
    return len(points) / span if span > 0 else 0.0


def build_series(observations: Iterable[dict[str, Any]], fx: FxTable, graph: ChemicalRouteGraph,
                 *, target_currency: str = "USD", frequency: str = MONTHLY) -> list[MonthlySeries]:
    """USD/t series, one per identity, from raw engine observations, at the frequency asked for.

    Within a month a series may hold several prints; their median is taken, because a mean lets one
    outlying print move the month and the median does not. Observations whose unit cannot be
    normalised, or whose currency has no rate on their own date, are dropped and counted - never
    silently converted at some other date's rate.
    """
    buckets: dict[str, dict[str, Any]] = {}
    for row in observations:
        key = str(row.get("identity_key") or "")
        price = row.get("price")
        month = _period(row.get("observation_date") or row.get("publication_date"), frequency)
        if not key or price is None or month is None:
            continue
        per_tonne = _to_tonne(float(price), row.get("unit"))
        if per_tonne is None:
            continue
        bucket = buckets.setdefault(key, {"row": row, "months": {}, "unconverted": 0,
                                           "by_month": {}})
        converted = fx.convert(per_tonne, row.get("currency"), target_currency,
                               _as_date(row.get("observation_date") or row.get("publication_date")))
        if converted is None:
            bucket["unconverted"] += 1
            continue
        bucket["months"].setdefault(month, []).append(converted[0])
        if frequency != MONTHLY:
            coarser = _period(row.get("observation_date") or row.get("publication_date"), MONTHLY)
            if coarser is not None:
                bucket["by_month"].setdefault(coarser, []).append(converted[0])

    series: list[MonthlySeries] = []
    for key, bucket in buckets.items():
        row = bucket["row"]
        points = {month: float(median(values)) for month, values in bucket["months"].items()}
        if not points:
            # Not a length gate: a series with no convertible point is not short, it is absent.
            # Every price was dropped for an unreadable unit or a currency with no rate on its own
            # date, and reporting an empty line as a series would hide that.
            continue
        read_as = frequency
        if frequency == WEEKLY and weekly_density(points) < MIN_WEEKLY_DENSITY:
            # Thirty-six monthly prints spread across a hundred and sixty weeks are not a weekly
            # series, and reading them as one puts a weekly label on monthly resolution. That much
            # was right. Dropping them was not.
            #
            # Stefan, 11 October 2026: "I want to kill this data sufficiency gate, we are sitting
            # on eligible data and not use it because of this." Asking the weekly lane for series
            # discarded 41 of 83 - every monthly series in the store - when `pair` already limits
            # a mixed pair to its coarser side and would have read them correctly. So a series is
            # read at the resolution it actually has, and says which that is.
            points = {month: float(median(values))
                      for month, values in (bucket["by_month"] or {}).items()}
            read_as = MONTHLY
            if not points:
                continue
        # No length gate. A series is not dropped for being short.
        #
        # This dropped anything under six months, so a chemical collected last week did not exist on
        # the study page at all - not as a weak result, not as a row saying how much history it has,
        # simply absent. Stefan, 19 September 2026: a gate is for incorrect data, not for the
        # quantity of it; a method that needs more data will fail on its own and say so.
        #
        # What replaces it is arithmetic honesty further down: every pair is reported with its own
        # sample size and interval, and the only floor left is the one below which a correlation has
        # no arithmetic at all.
        code = chemical_of(row, graph)
        node = graph.node(code) if code else None
        series.append(MonthlySeries(
            identity_key=key,
            chemical=code or str(row.get("product_family") or "UNKNOWN"),
            label=_label(row, node.display_name if node else None),
            observation_type=str(row.get("observation_type") or "market_price"),
            market_geography=str(row.get("market_geography") or "UNKNOWN"),
            currency=_norm(row.get("currency")),
            points=dict(sorted(points.items())),
            unconverted=int(bucket["unconverted"]),
            frequency=read_as,
        ))
    return sorted(series, key=lambda s: (s.chemical, s.label))


def reference_series_to_monthly(payload: Iterable[dict[str, Any]]) -> list[MonthlySeries]:
    """Turn the engine's /reference-series payload into series the study can use.

    No FX and no unit normalisation, deliberately. These values are not money; converting them would
    be meaningless and rescaling them would be pointless, since a linear rescaling cannot change a
    correlation. What does travel with them is the quality tier, because a grouped chlor-alkali index
    standing in for chlorine is a weak proxy for the whole of its life and every screen that shows it
    has to say so.
    """
    out: list[MonthlySeries] = []
    for row in payload:
        points: dict[date, float] = {}
        for observation in row.get("observations") or ():
            day = _as_date(observation.get("date"))
            value = observation.get("value")
            if day is None or value is None:
                continue
            points[date(day.year, day.month, 1)] = float(value)
        # Reference series are not dropped for being short either, for the reason above.
        geography = str(row.get("geography") or "UNKNOWN")
        out.append(MonthlySeries(
            identity_key=str(row.get("series_id") or ""),
            chemical=str(row.get("chemical") or "UNKNOWN").strip().upper().replace(" ", "_"),
            label=f"{row.get('label') or row.get('chemical')} · {geography}",
            observation_type=REFERENCE_TYPE,
            market_geography=geography,
            currency="",
            points=dict(sorted(points.items())),
            quality=str(row.get("quality") or "USABLE_PROXY"),
            unit=str(row.get("unit") or ""),
            is_price=not bool(row.get("is_index", True)),
        ))
    return sorted(out, key=lambda s: (s.chemical, s.label))


# ----------------------------------------------------------------------------- statistics

def _month_index(day: date) -> int:
    return day.year * 12 + (day.month - 1)


def log_changes(points: dict[date, float]) -> dict[date, float]:
    """Month-over-month log change, only across consecutive months.

    A gap in the series breaks the chain: the change from January to June is not a monthly change,
    and treating it as one would put a six-month move into a one-month sample.
    """
    out: dict[date, float] = {}
    months = sorted(points)
    for previous, current in zip(months, months[1:]):
        if _month_index(current) - _month_index(previous) != 1:
            continue
        before, after = points[previous], points[current]
        if before <= 0 or after <= 0:
            continue
        out[current] = log(after / before)
    return out


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    dx = [x - mean_x for x in xs]
    dy = [y - mean_y for y in ys]
    denominator = sqrt(sum(v * v for v in dx)) * sqrt(sum(v * v for v in dy))
    if denominator == 0:
        return None
    return sum(a * b for a, b in zip(dx, dy)) / denominator


def _shift(changes: dict[date, float], lag: int, frequency: str = MONTHLY) -> dict[date, float]:
    """Move an upstream series forward by `lag` periods, so period P lines up with P+lag."""
    out: dict[date, float] = {}
    for day, value in changes.items():
        out[_period_start(_period_index(day, frequency) + lag, frequency)] = value
    return out


def fisher_interval(coefficient: float, months: int, *, z: float = 1.96) -> tuple[float, float] | None:
    """95% confidence interval for a correlation, via the Fisher z transform.

    This is what replaces the old pass/fail gate. On 23 months an r of 0.44 comes back as roughly
    0.04 to 0.71 - real, positive, and far too wide to bet on. On 60 months the same 0.44 narrows to
    about 0.22 to 0.62. The number of months stops being a rule and becomes what it always was: the
    width of the answer.
    """
    if months < MIN_CORRELATION_MONTHS or abs(coefficient) >= 1.0:
        return None
    transformed = 0.5 * log((1 + coefficient) / (1 - coefficient))
    spread = z / sqrt(months - 3)
    low, high = transformed - spread, transformed + spread
    return (tanh(low), tanh(high))


@dataclass(frozen=True)
class LagResult:
    lag_months: int
    overlap_months: int
    coefficient: float | None

    @property
    def interval(self) -> tuple[float, float] | None:
        if self.coefficient is None:
            return None
        return fisher_interval(self.coefficient, self.overlap_months)

    @property
    def excludes_zero(self) -> bool:
        """Whether the interval sits wholly above or wholly below zero.

        Not a verdict - seven lags were searched, so one interval clearing zero by a little is the
        sort of thing a search produces on its own. It is one input to the grade, not the grade.
        """
        interval = self.interval
        return bool(interval and (interval[0] > 0) == (interval[1] > 0))

    @property
    def confidence(self) -> str:
        """How much weight this result carries. Nothing is hidden; the words carry the caution."""
        if self.coefficient is None:
            return "none"
        interval = self.interval
        if interval is None:
            return "indicative"
        width = interval[1] - interval[0]
        # Sample size and interval width are both required. A near-perfect coefficient on nine
        # months produces a narrow interval and is still nine months - the width alone would let a
        # deterministic artefact call itself provisional.
        if self.overlap_months >= CONFIDENT_MONTHS and width <= 0.5 and self.excludes_zero:
            return "established"
        if self.overlap_months >= MIN_OVERLAP_MONTHS and width <= 0.8 and self.excludes_zero:
            return "provisional"
        if self.overlap_months >= MIN_OVERLAP_MONTHS // 2 and self.excludes_zero:
            return "weak"
        return "indicative"


@dataclass(frozen=True)
class PairResult:
    """One upstream series tested against one raw-material series."""
    target: MonthlySeries
    driver: MonthlySeries
    priced_steps: int
    route_ids: tuple[str, ...]
    status: str                       # "tested" | "too_short" | "no_overlap"
    overlap_months: int
    months_short: int                 # months short of CONFIDENT_MONTHS; 0 once there is enough
    best: LagResult | None = None
    by_lag: tuple[LagResult, ...] = ()

    @property
    def reportable(self) -> bool:
        """Whether a number exists. Whether to believe it is `confidence`, which is a separate
        question and is never answered by hiding the number."""
        return self.status == "tested" and self.best is not None and self.best.coefficient is not None

    @property
    def at_lag_zero(self) -> LagResult | None:
        """The un-searched result. Comparing it with `best` shows how much the lag search bought."""
        return next((row for row in self.by_lag if row.lag_months == 0), None)

    @property
    def coherent(self) -> bool:
        """Whether the lag profile holds one sign, or flips about.

        A real transmission relationship decays across neighbouring lags; it does not swing from
        +0.57 to -0.58 and back. A profile that changes sign more than once is describing noise,
        whatever its largest value happens to be.
        """
        signs = [1 if r.coefficient > 0 else -1
                 for r in self.by_lag if r.coefficient is not None and abs(r.coefficient) >= 0.2]
        flips = sum(1 for a, b in zip(signs, signs[1:]) if a != b)
        return flips <= 1

    @property
    def confidence(self) -> str:
        grade = self.best.confidence if self.best else "none"
        if grade in {"established", "provisional"} and not self.coherent:
            # Downgrade rather than hide: the number stands, the claim about it does not.
            grade = "weak"
        # A proxy caps the claim however much history sits behind it. Thirty years of a grouped
        # chlor-alkali index still is not a chlorine price, and no sample size makes it one.
        ceiling = QUALITY_CEILING.get(self.driver.quality)
        if ceiling and _GRADE_ORDER.index(grade) > _GRADE_ORDER.index(ceiling):
            return ceiling
        return grade


def _select_lag(results: Sequence[LagResult]) -> LagResult | None:
    """Pick the lag to report - by cost transmission, not by absolute size.

    Taking the largest |r| across seven lags looked reasonable and is not. On the first real pack of
    upstream data the naphtha/toluene profile ran +0.44, +0.57, -0.29, -0.58, 0.00, +0.34, +0.17 over
    23 months. The largest magnitude is -0.58 at lag 3, which would have been reported as "naphtha
    moves against toluene with a three-month lead" - the opposite of the chemistry, chosen because a
    short sample produces large numbers in both directions.

    A feedstock passing cost through to its product moves it in the SAME direction. So the reported
    lag is the strongest positive one. A negative coefficient is not evidence of inverse
    transmission, it is evidence of noise, and it stays visible in `by_lag` where it belongs.
    """
    scored = [r for r in results if r.coefficient is not None]
    if not scored:
        return None
    positive = [r for r in scored if r.coefficient > 0]
    if positive:
        return max(positive, key=lambda r: r.coefficient)
    # Nothing positive at any lag: report lag zero, which says "no transmission visible" honestly.
    return next((r for r in scored if r.lag_months == 0), scored[0])


def correlate_pair(target: MonthlySeries, driver: MonthlySeries, *, priced_steps: int = 0,
              route_ids: tuple[str, ...] = (), max_lag: int | None = None,
              min_overlap: int = MIN_OVERLAP_MONTHS) -> PairResult:
    """Correlate one driver against one target across lags 0..max_lag.

    Every result is returned with its sample size and confidence interval; nothing is withheld for
    being short. Testing seven lags and keeping the largest coefficient is still a search rather than
    a finding, so the result carries every lag beside the best one, and `at_lag_zero` gives the
    un-searched number for comparison.
    """
    # Both sides must be sampled the same way. A weekly series correlated against a monthly one is
    # not a finer measurement, it is a mismatch, and the pair is limited by its coarser side.
    frequency = target.frequency if target.frequency == driver.frequency else MONTHLY
    if max_lag is None:
        max_lag = MAX_LAG_WEEKS if frequency == WEEKLY else MAX_LAG_MONTHS
    target_changes = log_changes(target.points)
    driver_changes = log_changes(driver.points)
    base_overlap = len(set(target_changes) & set(driver_changes))
    if base_overlap < MIN_CORRELATION_MONTHS:
        # Not a quality judgement - below this the interval has no arithmetic. But a pair with two
        # overlapping months has two, and reporting that as "No overlap" is how a screen tells a
        # reader we hold nothing while we are holding something. The count travels with the status.
        return PairResult(target, driver, priced_steps, route_ids,
                          "too_short" if base_overlap else "no_overlap", base_overlap,
                          max(0, CONFIDENT_MONTHS - base_overlap))

    results: list[LagResult] = []
    for lag in range(0, max_lag + 1):
        shifted = _shift(driver_changes, lag, frequency)
        months = sorted(set(target_changes) & set(shifted))
        coefficient = pearson([shifted[m] for m in months], [target_changes[m] for m in months])
        results.append(LagResult(lag, len(months), coefficient))

    # A lag is only eligible on its own sample. Shifting a driver back six months can leave three
    # overlapping months, and three months produce large coefficients as a matter of arithmetic:
    # chlorine against TDI 65/35 reported +0.95 at lag 5 on three months, with no interval and
    # nothing on the screen to say the sample had collapsed. The unshifted overlap clearing the floor
    # says nothing about the slice the search actually reports. Every lag stays in `by_lag`; this
    # decides only which one may be quoted as the answer.
    eligible = [r for r in results
                if r.overlap_months >= (MIN_CORRELATION_MONTHS if r.lag_months == 0
                                        else MIN_SEARCHED_LAG_MONTHS)]
    best = _select_lag(eligible)
    if best is None:
        # Every lag's own slice was too short to be arithmetic, even though the unshifted overlap
        # was not. Reported with the overlap it does have rather than as nothing at all.
        return PairResult(target, driver, priced_steps, route_ids,
                          "too_short" if base_overlap else "no_overlap", base_overlap,
                          max(0, CONFIDENT_MONTHS - base_overlap))
    return PairResult(target, driver, priced_steps, route_ids, "tested", base_overlap,
                      max(0, CONFIDENT_MONTHS - base_overlap), best=best, by_lag=tuple(results))


# ----------------------------------------------------------------------------- route-driven study

def study(target_chemical: str, series: Sequence[MonthlySeries], graph: ChemicalRouteGraph, *,
          max_priced_steps: int = 2, max_lag: int = MAX_LAG_MONTHS,
          min_overlap: int = MIN_OVERLAP_MONTHS) -> list[PairResult]:
    """Test every series of every chemical upstream of `target_chemical` against every target series.

    The route graph decides what is tested. Correlating a raw material against everything in the
    store would find relationships that are pure coincidence; restricting the search to chemicals
    that are actually upstream in production is the only thing keeping the false-positive count down.
    """
    targets = [s for s in series if s.chemical == target_chemical]
    if not targets:
        return []
    candidates = {c.chemical: c for c in graph.upstream_candidates(target_chemical,
                                                                   max_priced_steps=max_priced_steps)}
    results: list[PairResult] = []
    for target in targets:
        for driver in series:
            candidate = candidates.get(driver.chemical)
            if candidate is None or driver.identity_key == target.identity_key:
                continue
            results.append(correlate_pair(target, driver, priced_steps=candidate.priced_steps,
                                     route_ids=candidate.route_ids, max_lag=max_lag,
                                     min_overlap=min_overlap))
    results.sort(key=lambda r: (
        0 if r.reportable else 1,
        -abs(r.best.coefficient) if r.reportable and r.best and r.best.coefficient is not None else 0,
        r.driver.chemical,
    ))
    return results


def _prints_daily(days: set) -> bool:
    """Whether a market prints on most business days, over enough calendar to tell.

    Weekends are excluded on both sides rather than counted as misses: a publisher who never prints
    on a Saturday is not a publisher with a gap. A holiday still counts against the density, which
    is the same tolerance the weekly rule gives a publisher's fortnight off.
    """
    if len(days) < 2:
        return False
    ordered = sorted(days)
    held = sum(1 for day in days if day.weekday() < 5)
    if held < MIN_DAILY_BUSINESS_DAYS:
        return False
    business, cursor = 0, ordered[0]
    while cursor <= ordered[-1]:
        if cursor.weekday() < 5:
            business += 1
        cursor += timedelta(days=1)
    return bool(business) and held / business >= MIN_DAILY_DENSITY


def weekly_readiness(observations: Iterable[dict[str, Any]], fx: FxTable,
                     graph: ChemicalRouteGraph) -> dict[str, Any]:
    """How close the weekly lane is to being usable, per chemical.

    The daily collectors began on 27 August 2026, so the weekly lane is being planted rather than
    harvested. This is what says so honestly on the page instead of an empty table: how many of
    each chemical's markets are genuinely weekly, how far along the furthest one is, and how many
    weeks it still needs.

    Weekly is a property of a market, not of a family. A family row counts the markets that pass
    the same test `build_series` applies - at least `MIN_SERIES_WEEKS` weeks, at least
    `MIN_WEEKLY_DENSITY` of the weeks in its own span carrying a price - and never averages a
    weekly market together with the monthly trade series that share its product family.
    """
    rows: list[dict[str, Any]] = []
    for series in build_series(observations, fx, graph, frequency=WEEKLY):
        rows.append({"chemical": series.chemical, "label": series.label, "weeks": series.months,
                     "from": series.first.isoformat() if series.first else "",
                     "to": series.last.isoformat() if series.last else ""})

    # Per market, not per family. Pooling a family's markets into one set of weeks answers the
    # wrong question: POLYETHER_POLYOL's six weekly markets sat inside thirty-eight monthly trade
    # series running back to the 2000s, and the pooled span of 1,404 weeks diluted them to 0.55 -
    # so the page said "cannot be made weekly" about a family that already holds weekly markets.
    # It flatters in the other direction too: one weekly market out of three carried GRAFT_POLYOL.
    weeks_by_market: dict[str, set] = {}
    days_by_market: dict[str, set] = {}
    family_of: dict[str, str] = {}
    for row in observations or ():
        day = _week(row.get("observation_date") or row.get("publication_date"))
        family = str(row.get("product_family") or "").strip().upper()
        market = str(row.get("identity_key") or "")
        if day is None or not family or not market:
            continue
        weeks_by_market.setdefault(market, set()).add(day)
        exact = _as_date(row.get("observation_date") or row.get("publication_date"))
        if exact is not None:
            days_by_market.setdefault(market, set()).add(exact)
        family_of[market] = family

    markets_by_family: dict[str, list[tuple[float, int, int]]] = {}
    daily_by_family: dict[str, int] = {}
    for market, weeks in weeks_by_market.items():
        if _prints_daily(days_by_market.get(market) or set()):
            daily_by_family[family_of[market]] = daily_by_family.get(family_of[market], 0) + 1
        ordered = sorted(weeks)
        span = _period_index(ordered[-1], WEEKLY) - _period_index(ordered[0], WEEKLY) + 1
        # One week is not a frequency. `weekly_density` says the same of a single point, and
        # without this a collector that printed once last Tuesday reports a density of 1.00 and
        # carries its whole family into "collecting weekly".
        density = len(weeks) / span if span and len(weeks) >= 2 else 0.0
        markets_by_family.setdefault(family_of[market], []).append((density, len(weeks), span))

    growing = []
    for family, markets in markets_by_family.items():
        weekly = [one for one in markets
                  if one[0] >= MIN_WEEKLY_DENSITY and one[1] >= MIN_SERIES_WEEKS]
        # Whichever market is furthest along, because weeks still needed is counted off that one.
        # Among those weekly enough to count, that is the longest; where none is, the densest, and
        # the row says monthly either way.
        dense = [one for one in markets if one[0] >= MIN_WEEKLY_DENSITY]
        density, weeks_held, span = (max(dense, key=lambda one: (one[1], one[0])) if dense
                                     else max(markets))
        growing.append({
            "chemical": family,
            "markets": len(markets),
            "weekly_markets": len(weekly),
            "daily_markets": daily_by_family.get(family, 0),
            "weeks_held": weeks_held,
            "weeks_in_span": span,
            "density": round(density, 2),
            "weeks_short": 0 if weekly else max(0, MIN_SERIES_WEEKS - weeks_held),
            "reading": ("weekly" if weekly
                        else "collecting weekly, not yet long enough"
                        if density >= MIN_WEEKLY_DENSITY
                        else "monthly only — no weekly market held"),
        })
    growing.sort(key=lambda r: (-r["daily_markets"], -r["weekly_markets"], -r["density"],
                                -r["weeks_held"]))
    return {
        "usable_series": rows,
        "minimum_weeks": MIN_SERIES_WEEKS,
        "coverage": growing,
        "ready": bool(rows),
    }


# ----------------------------------------------------------------------------- who moves first

@dataclass(frozen=True)
class LeadDiagnostic:
    """Whether the raw material moves ahead of a feedstock, rather than after it.

    The headline answer searches only for the feedstock leading, because that is the cost question.
    This is the other question, and it is a different claim: a producer who prices on the expectation
    of dearer feedstock moves before the cost arrives. It is reported as a diagnostic and never mixed
    into the headline, because doubling the lags searched doubles the chances of a coincidence
    looking impressive.
    """
    driver: str
    target_label: str
    best_driver_lead: LagResult | None      # feedstock first
    best_target_lead: LagResult | None      # raw material first
    verdict: str

    @property
    def separable(self) -> bool:
        """Whether the two sides differ by enough to prefer one. At monthly resolution, a producer
        passing cost through quickly and a producer pricing on the news both land in month zero, and
        no amount of arithmetic separates them."""
        return self.verdict not in {"cannot tell", "nothing either way"}


def lead_diagnostic(target: MonthlySeries, driver: MonthlySeries, *,
                    max_lag: int | None = None) -> LeadDiagnostic:
    """Test both directions and say which, if either, the data prefers."""
    frequency = target.frequency if target.frequency == driver.frequency else MONTHLY
    if max_lag is None:
        max_lag = MAX_LAG_WEEKS if frequency == WEEKLY else MAX_LAG_MONTHS
    target_changes = log_changes(target.points)
    driver_changes = log_changes(driver.points)

    def _at(lag: int) -> LagResult:
        shifted = _shift(driver_changes, lag, frequency)
        months = sorted(set(target_changes) & set(shifted))
        return LagResult(lag, len(months),
                         pearson([shifted[m] for m in months], [target_changes[m] for m in months]))

    eligible = [_at(lag) for lag in range(-max_lag, max_lag + 1)]
    eligible = [row for row in eligible
                if row.coefficient is not None and row.overlap_months >= MIN_CORRELATION_MONTHS]
    ahead = [row for row in eligible if row.lag_months > 0 and row.coefficient > 0]
    behind = [row for row in eligible if row.lag_months < 0 and row.coefficient > 0]
    best_ahead = max(ahead, key=lambda r: r.coefficient) if ahead else None
    best_behind = max(behind, key=lambda r: r.coefficient) if behind else None

    if best_ahead is None and best_behind is None:
        verdict = "nothing either way"
    elif best_behind is None:
        verdict = "feedstock first"
    elif best_ahead is None:
        verdict = "raw material first"
    else:
        # Only prefer a side when its interval clears the other side's coefficient. Anything less is
        # two numbers of the same size on opposite sides of zero lag, which is noise wearing a shape.
        low, _high = best_ahead.interval or (None, None)
        low_behind, _ = best_behind.interval or (None, None)
        if low is not None and low > best_behind.coefficient:
            verdict = "feedstock first"
        elif low_behind is not None and low_behind > best_ahead.coefficient:
            verdict = "raw material first"
        else:
            verdict = "cannot tell"
    return LeadDiagnostic(driver=driver.chemical, target_label=target.label,
                          best_driver_lead=best_ahead, best_target_lead=best_behind,
                          verdict=verdict)


def lead_rows(diagnostics: Sequence[LeadDiagnostic], graph: ChemicalRouteGraph) -> list[dict[str, Any]]:
    rows = []
    for row in diagnostics:
        def _cell(result: LagResult | None) -> str:
            if result is None:
                return "—"
            return f"{result.coefficient:+.2f} at {abs(result.lag_months)} month(s), {result.overlap_months} months"
        rows.append({
            "Feedstock": _display(graph, row.driver),
            "Against": row.target_label,
            "Feedstock moves first": _cell(row.best_driver_lead),
            "Raw material moves first": _cell(row.best_target_lead),
            "Reading": {
                "feedstock first": "cost passing through",
                "raw material first": "priced ahead of the cost",
                "cannot tell": "both sides look the same — months are too coarse to separate them",
                "nothing either way": "no positive relationship at any lag",
            }[row.verdict],
        })
    return rows


# ----------------------------------------------------------------------------- the chain, link by link

def _display(graph: ChemicalRouteGraph, code: str) -> str:
    """The chemical's label. Duplicated rather than imported from the view, because the statistics
    must never depend on the presentation layer."""
    node = graph.node(code)
    return node.label if node else code.replace("_", " ").title()


@dataclass(frozen=True)
class LinkResult:
    """One step of the production chain, measured on its own terms."""
    link: Any                          # chemical_routes.PricedLink
    from_label: str
    to_label: str
    best: PairResult | None            # the pair of series that shared the most months
    alternatives: int                  # how many other series pairs were available for this step

    @property
    def reportable(self) -> bool:
        return bool(self.best and self.best.reportable)

    @property
    def lag(self) -> int | None:
        return self.best.best.lag_months if self.reportable else None


def chain_study(target_chemical: str, series: Sequence[MonthlySeries], graph: ChemicalRouteGraph, *,
                max_priced_steps: int = 3, max_lag: int = MAX_LAG_MONTHS) -> list[LinkResult]:
    """Measure each step between two quoted chemicals, rather than every chemical against the end.

    Testing crude oil, naphtha and toluene each directly against TDI reports one signal travelling
    down a chain as three independent findings. It is not three: crude moves naphtha, naphtha moves
    toluene, toluene moves TDI. Measuring the steps separates the transmission that actually happens
    at each stage from its accumulated echo at the bottom, and it makes the lags checkable - a path's
    lag should be near the sum of its links', and nothing imposes that but the data.
    """
    by_chemical: dict[str, list[MonthlySeries]] = {}
    for row in series:
        by_chemical.setdefault(row.chemical, []).append(row)

    out: list[LinkResult] = []
    for link in graph.priced_links(target_chemical, max_priced_steps=max_priced_steps):
        drivers = by_chemical.get(link.from_chemical, [])
        products = by_chemical.get(link.to_chemical, [])
        pairs = [correlate_pair(product, driver, max_lag=max_lag)
                 for driver in drivers for product in products]
        tested = [pair for pair in pairs if pair.reportable]
        # The pair with the most shared months, not the largest coefficient: choosing by size of the
        # answer is how a search dresses itself up as a finding.
        best = max(tested, key=lambda pair: pair.best.overlap_months) if tested else None
        out.append(LinkResult(
            link=link,
            from_label=_display(graph, link.from_chemical),
            to_label=_display(graph, link.to_chemical),
            best=best,
            alternatives=max(0, len(tested) - 1),
        ))
    return out


def lag_consistency(links: Sequence[LinkResult], direct: Sequence[PairResult],
                    graph: ChemicalRouteGraph, target_chemical: str) -> list[dict[str, Any]]:
    """Does a chemical's direct lag to the target match the sum of the lags along its path?

    An independent check that nobody imposed: if the links are real transmission, walking them should
    take about as long as the direct measurement says. A gross mismatch means the direct correlation
    is picking up something other than cost passing down this chain. Monthly data cannot resolve
    better than a month or so, so this rules out contradiction rather than confirming mechanism.
    """
    measured = {row.link.from_chemical: row for row in links if row.reportable}
    downstream = {row.link.from_chemical: row.link.to_chemical for row in links}

    best_direct: dict[str, PairResult] = {}
    for pair in direct:
        if not pair.reportable:
            continue
        held = best_direct.get(pair.driver.chemical)
        if held is None or pair.best.overlap_months > held.best.overlap_months:
            best_direct[pair.driver.chemical] = pair

    out: list[dict[str, Any]] = []
    for chemical, pair in sorted(best_direct.items()):
        steps, code, total, walked = [], chemical, 0, True
        while code != target_chemical:
            row = measured.get(code)
            if row is None:
                walked = False
                break
            steps.append(f"{row.from_label} → {row.to_label} ({row.lag})")
            total += row.lag or 0
            code = downstream.get(code, target_chemical)
        if not walked or not steps:
            continue
        out.append({
            "Chemical": _display(graph, chemical),
            "Lag along the chain": total,
            "Lag measured directly": pair.best.lag_months,
            "Difference": abs(total - pair.best.lag_months),
            "Steps": " · ".join(steps),
        })
    return out


def link_rows(links: Sequence[LinkResult], graph: ChemicalRouteGraph) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in links:
        via = " → ".join(_display(graph, code) for code in row.link.via)
        entry: dict[str, Any] = {
            "Step": f"{row.from_label} → {row.to_label}",
            "Through": via or "direct",
            "Months": row.best.best.overlap_months if row.reportable else 0,
            "Lag (months)": str(row.lag) if row.reportable else "—",   # text: shares a column with the dash
        }
        if row.reportable:
            best = row.best.best
            low, high = best.interval or (None, None)
            entry["Moves together"] = round(best.coefficient, 3)
            entry["Could really be"] = (f"{low:+.2f} to {high:+.2f}" if low is not None else "—")
            entry["Weight"] = row.best.confidence
        else:
            entry["Moves together"] = "—"
            entry["Could really be"] = "—"
            entry["Weight"] = "no overlapping history" if row.best else "no readings"
        rows.append(entry)
    return rows


# ----------------------------------------------------------------------------- what is missing

@dataclass(frozen=True)
class Requirement:
    """One upstream chemical, and what stands between it and a usable correlation."""
    chemical: str
    display_name: str
    cas: str
    priced_steps: int
    route_ids: tuple[str, ...]
    status: str                # "ready" | "short_history" | "no_overlap" | "missing"
    months_held: int
    best_overlap: int
    months_short: int
    note: str = ""
    series_labels: tuple[str, ...] = ()


def data_requirements(target_chemical: str, series: Sequence[MonthlySeries], graph: ChemicalRouteGraph,
                      *, max_priced_steps: int = 2,
                      min_overlap: int = MIN_OVERLAP_MONTHS) -> list[Requirement]:
    """For each chemical upstream of the target: what is held, and what would sharpen it?

    This is the shopping list, and it is a list of improvements rather than of blockers. `missing`
    means the store holds nothing for that chemical. `testable` means a correlation can already be
    computed - the confidence attached to it is a separate matter, reported beside the number, and it
    narrows as history arrives rather than switching on at a threshold.
    """
    targets = [s for s in series if s.chemical == target_chemical]
    by_chemical: dict[str, list[MonthlySeries]] = {}
    for row in series:
        by_chemical.setdefault(row.chemical, []).append(row)

    # Per target series, never their union. The study correlates one driver against ONE raw-material
    # series, so the months that count are the months shared with that one series. Pooling them
    # counted toluene as overlapping TDI for 85 months when no single pair reached 34, and every
    # feedstock then read "Firm" on a screen where every result graded indicative.
    target_month_sets = [set(log_changes(row.points)) for row in targets]
    target_months: set[date] = set().union(*target_month_sets) if target_month_sets else set()

    out: list[Requirement] = []
    for candidate in graph.upstream_candidates(target_chemical, max_priced_steps=max_priced_steps):
        node = graph.node(candidate.chemical)
        display = node.label if node else candidate.chemical.replace("_", " ")
        cas = node.cas if node else ""
        held = by_chemical.get(candidate.chemical, [])
        if not held:
            out.append(Requirement(
                candidate.chemical, display, cas, candidate.priced_steps, candidate.route_ids,
                "missing", 0, 0, CONFIDENT_MONTHS,
                note="No readings held. Needs a source before any correlation can be attempted.",
            ))
            continue

        months_held = max(row.months for row in held)
        best_overlap = 0
        for row in held:
            driver_months = set(log_changes(row.points))
            for months in target_month_sets:
                best_overlap = max(best_overlap, len(driver_months & months))
        labels = tuple(row.label for row in held)

        if not target_months:
            status, note = "no_overlap", "No raw-material history to test against."
        elif best_overlap >= CONFIDENT_MONTHS:
            status, note = "ready", "Enough overlapping history for a firm answer."
        elif best_overlap >= MIN_CORRELATION_MONTHS:
            status = "testable"
            note = (f"Testable now on {best_overlap} overlapping months; "
                    f"{CONFIDENT_MONTHS - best_overlap} more would narrow the answer.")
        elif best_overlap == 0:
            status = "no_overlap"
            note = "History exists but covers a different period from the raw material - the two never overlap."
        else:
            status = "short_history"
            note = (f"Overlaps for {best_overlap} months, too few to compute an interval; "
                    f"{MIN_CORRELATION_MONTHS - best_overlap} more would make it measurable.")
        out.append(Requirement(
            candidate.chemical, display, cas, candidate.priced_steps, candidate.route_ids, status,
            months_held, best_overlap, max(0, CONFIDENT_MONTHS - best_overlap), note, labels,
        ))

    order = {"ready": 0, "testable": 1, "short_history": 2, "too_short": 3, "no_overlap": 4,
             "missing": 5}
    return sorted(out, key=lambda r: (r.priced_steps, order.get(r.status, 9), r.chemical))


def requirement_rows(requirements: Iterable[Requirement]) -> list[dict[str, Any]]:
    """Display rows, in the order a reader needs them: what, how far upstream, and what is missing."""
    return [
        {
            "Chemical": r.display_name,
            # A stream or a polymer has no single registry number, and inventing one to fill the
            # column would be worse than leaving it empty.
            "CAS": r.cas or "—",
            "Steps upstream": r.priced_steps,
            "Status": {
                "ready": "Firm", "testable": "Testable now", "short_history": "Very short",
                "too_short": "Too short to measure", "no_overlap": "No overlap",
                "missing": "No data",
            }.get(r.status, r.status),
            "Months held": r.months_held,
            "Overlapping months": r.best_overlap,
            "Months to a firm answer": r.months_short,
            "What is needed": r.note,
        }
        for r in requirements
    ]


def _last_read(series: MonthlySeries) -> str:
    """The newest period this series holds, as a date a reader can judge for themselves.

    Not a staleness verdict: what counts as stale depends on the publisher's own cadence, and the
    forecast layer already rules on it (`eligibility.CURRENT_WITHIN`). This is the fact underneath,
    on a page whose whole job is to report what was measured and on what.
    """
    return series.last.isoformat() if series.last else "—"


def result_rows(results: Iterable[PairResult]) -> list[dict[str, Any]]:
    """One row per tested pair. The number, its uncertainty and its provenance travel together.

    A coefficient printed without its sample size and interval is the thing that gets quoted in a
    meeting six months later with neither. So the range is its own column, not a footnote.

    Three columns are here because the 19 November 2026 export went out without them and was read
    exactly as that absence invited:

    - **the identity keys**, because the labels are not unique. Four different China polyether
      polyol markets export as one name, carrying r from 0.25 to 0.78 against the same driver, and
      nothing in the file says which market each row is.
    - **the lead-0 figure**, because `Lead (months)` reports the best of seven searched lags.
      `at_lag_zero` is the un-searched comparison and exists for this; shipping the winner alone
      turns a search into what reads like a finding.
    - **`Sample confidence`**, named so it cannot be read as strength. It grades how much history
      the pair was measured on, not whether anything was found, which is why a row can say
      "Established" beside "No relationship visible in this sample". The screen was fixed when that
      pair of columns read as a contradiction; this file kept them.
    """
    rows: list[dict[str, Any]] = []
    for result in results:
        row: dict[str, Any] = {
            "Raw material": result.target.label,
            "Raw material series": result.target.identity_key,
            "Driver": result.driver.label,
            "Driver series": result.driver.identity_key,
            "Steps upstream": result.priced_steps,
            "Months": result.overlap_months,
            # When each side last printed. The 19 November export named the months of overlap and
            # never the dates, so a relationship measured on 83 months to August 2023 and one
            # measured on 83 months to last week read identically. Crude pMDI North America stopped
            # printing in 2023 - `icis_history` is a one-off load, not a feed - and it took a
            # backtest to notice, because nothing on the row said so.
            "Raw material last read": _last_read(result.target),
            "Driver last read": _last_read(result.driver),
            "Driver basis": _basis(result.driver),
        }
        best = result.best
        unsearched = result.at_lag_zero
        if result.reportable and best is not None and best.coefficient is not None:
            interval = best.interval
            row["Lead (months)"] = str(best.lag_months)   # text: shares a column with the dash
            row["Correlation"] = round(best.coefficient, 2)
            row["Range"] = (f"{interval[0]:.2f} to {interval[1]:.2f}" if interval else "—")
            # Beside the searched winner, so a lead that the search bought cannot be read as one
            # the data offered. Equal to "Correlation" wherever the best lag was zero anyway.
            row["Correlation at lead 0"] = (round(unsearched.coefficient, 2)
                                            if unsearched is not None
                                            and unsearched.coefficient is not None else "—")
            row["Months at lead 0"] = unsearched.overlap_months if unsearched is not None else "—"
            grade = result.confidence
            row["Sample confidence"] = CONFIDENCE_WORDS.get(grade, grade)
            row["Reading"] = _verdict(best.coefficient, grade)
            if not result.coherent:
                row["Reading"] += "; the relationship does not hold across nearby lead times"
        else:
            row["Lead (months)"] = "—"
            row["Correlation"] = "—"
            row["Range"] = "—"
            row["Correlation at lead 0"] = "—"
            row["Months at lead 0"] = "—"
            row["Sample confidence"] = "Not measurable"
            row["Reading"] = (
                "The two series never overlap" if result.overlap_months == 0
                else f"Only {result.overlap_months} overlapping months - too few to measure"
            )
        rows.append(row)
    return rows


# How far a series of each quality is allowed to be believed, whatever its sample size.
QUALITY_CEILING = {"WEAK_PROXY": "indicative", "USABLE_PROXY": "provisional"}
_GRADE_ORDER = ["none", "indicative", "weak", "provisional", "established"]

CONFIDENCE_WORDS = {
    "established": "Established",
    "provisional": "Provisional",
    "weak": "Weak",
    "indicative": "Indicative only",
    "none": "Not measurable",
}


def _basis(series: MonthlySeries) -> str:
    """What the driver actually is, in the row where its number appears.

    A reader who sees "Toluene 0.57" and is not told it was measured on a US aromatics basket has
    been given a number and denied the thing that decides what it means.
    """
    if not series.is_reference:
        return "Market price"
    words = {"STRONG": "Benchmark price", "USABLE_PROXY": "Index (proxy)",
             "WEAK_PROXY": "Index (weak proxy)"}
    return words.get(series.quality, "Index")


def _verdict(coefficient: float, confidence: str = "indicative") -> str:
    """Plain words for a coefficient on monthly changes, where 0.6 is already a strong result.

    The strength of the relationship and the strength of the evidence are two different things and
    are said separately: a 0.7 on nine months is a strong relationship in a sample too small to
    trust, and collapsing those into one word is how a thin result gets acted on.
    """
    magnitude = abs(coefficient)
    direction = "moves with" if coefficient > 0 else "moves against"
    if magnitude >= 0.6:
        strength = f"Strong: {direction} the raw material"
    elif magnitude >= 0.4:
        strength = f"Moderate: {direction} the raw material"
    elif magnitude >= 0.25:
        strength = f"Weak: {direction} the raw material"
    else:
        strength = "No relationship visible in this sample"
    if confidence in {"indicative", "weak"} and magnitude >= 0.25:
        return strength + ", on too little history to rely on yet"
    return strength
