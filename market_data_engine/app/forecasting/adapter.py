"""The one contract every forecasting model implements.

The architecture is deliberately vendor-neutral: Chronos-class, TimesFM-class and whatever follows
them are plug-ins, and none of them becomes the architecture. That only holds if the contract is
narrow enough that a statistical control and a foundation model are interchangeable behind it.

So a model receives an as-of dataset, a horizon list, optional covariates and known-future variables,
and a configuration; and returns forecast quantiles or a central forecast plus metadata. Nothing
else. A model that needs a GPU and a model that is four lines of arithmetic look identical to the
orchestrator.

The one thing the contract enforces rather than documents is **as-of discipline**. A request cannot
be constructed carrying a data point later than its own as-of date. That is the property that
separates a backtest from a curve fitted to its own answer, and leaving it to each model to respect
would mean discovering a violation by noticing that the results were too good.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from raw_material_forecasting.units import MT

DEFAULT_QUANTILES: tuple[float, ...] = (0.1, 0.5, 0.9)
# Horizons are CALENDAR WEEKS, never a count of periods. Four weeks is twenty-eight days whether the
# series behind it is daily, weekly or monthly, and the product has asked the same question on the
# same three cards since before any of this existed.
#
# Expressing a horizon in periods would bake today's data frequency into the question. A monthly
# series would answer "four months" under a card that says four weeks, and the day the collectors
# mature into a genuine weekly series every horizon would silently change meaning. The model resolves
# a calendar horizon against its own frequency and reports what resolution it managed - so the
# question stays fixed and the answer gets sharper as the data arrives.
DEFAULT_HORIZONS: tuple[int, ...] = (4, 8, 12)

WEEKLY = "W"
MONTHLY = "M"
DAILY = "D"

# Days a period covers, for turning a calendar horizon into steps of this series.
PERIOD_DAYS = {DAILY: 1.0, WEEKLY: 7.0, MONTHLY: 365.25 / 12}


class LicenceClass(str, Enum):
    """What a model may be used for, decided before it is ever scored.

    A model's licence is not a property of its accuracy and must never be traded against it.
    TimesFM 3.0 arrived under a non-commercial licence after the design named the family; that is
    exactly the event this exists to catch.
    """

    COMMERCIAL = "COMMERCIAL"          # may produce forecasts customers see
    RESEARCH_ONLY = "RESEARCH_ONLY"    # may be benchmarked, never shipped
    UNVERIFIED = "UNVERIFIED"          # licence not yet established: treated as research-only


class Purpose(str, Enum):
    RESEARCH = "RESEARCH"
    COMMERCIAL = "COMMERCIAL"


class LicenceRefused(RuntimeError):
    """Raised when a model is asked to do something its licence does not permit."""


class AsOfViolation(ValueError):
    """Raised when a request would hand a model information from after its own as-of date."""


@dataclass(frozen=True)
class Series:
    """One series, ordered, at one frequency.

    `points` are (period start, value). A weekly series is keyed by Monday, a monthly one by the
    first of the month - the same convention the correlation work uses, so a series can cross between
    them without translation.
    """

    key: str
    chemical: str
    frequency: str
    points: tuple[tuple[date, float], ...]
    unit: str = f"USD/{MT}"
    label: str = ""
    # Days between the last bucket's start and the last price actually printed. A monthly bucket is
    # keyed to the 1st, so a market collected daily and last priced on the 30th sits 29 days past
    # its own last period. Counting a horizon from the bucket then delivers a "three month"
    # forecast two months and one day after the price a reader is standing on - TDI China on
    # 30 September 2026 answered 1 Oct, 1 Nov, 1 Dec. Stefan saw it on the chart and asked for the
    # three months to be three months. Zero wherever the publisher prints on the 1st, which is 69
    # of the 84 markets in the store.
    offset_days: int = 0

    def __post_init__(self) -> None:
        days = [day for day, _ in self.points]
        if days != sorted(days):
            object.__setattr__(self, "points", tuple(sorted(self.points, key=lambda p: p[0])))

    @property
    def dates(self) -> tuple[date, ...]:
        return tuple(day for day, _ in self.points)

    @property
    def values(self) -> tuple[float, ...]:
        return tuple(value for _, value in self.points)

    @property
    def last_date(self) -> date | None:
        return self.points[-1][0] if self.points else None

    def up_to(self, as_of: date) -> "Series":
        """The series as it stood on `as_of`. This is how a caller makes a request legal."""
        return Series(self.key, self.chemical, self.frequency,
                      tuple((day, value) for day, value in self.points if day <= as_of),
                      self.unit, self.label, self.offset_days)


@dataclass(frozen=True)
class ForecastRequest:
    """Everything a model is allowed to see, and nothing else."""

    series: Series
    as_of: date
    horizons: tuple[int, ...] = DEFAULT_HORIZONS      # calendar weeks
    quantiles: tuple[float, ...] = DEFAULT_QUANTILES
    covariates: tuple[Series, ...] = ()
    # Variables whose future values are legitimately known - announced maintenance, a contracted
    # freight rate. These may extend beyond as_of; that is the whole point of them, and it is why
    # they are a separate field rather than a covariate with looser rules.
    known_future: tuple[Series, ...] = ()
    config: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.horizons or any(h < 1 for h in self.horizons):
            raise ValueError("horizons must be one or more calendar weeks, each at least 1")
        if any(not 0.0 < q < 1.0 for q in self.quantiles):
            raise ValueError("quantiles must lie strictly between 0 and 1")
        for series in (self.series, *self.covariates):
            late = [day for day in series.dates if day > self.as_of]
            if late:
                raise AsOfViolation(
                    f"{series.key} carries {len(late)} point(s) after as_of {self.as_of.isoformat()}"
                    f", first {late[0].isoformat()}. Call Series.up_to(as_of) before building the "
                    "request - a model must never be handed information it could not have had."
                )


@dataclass(frozen=True)
class ForecastPoint:
    """One answer, and an honest account of what was actually asked of the data.

    `horizon_weeks` is the question - the card on the screen. `steps` and `resolution` are what the
    series could do about it, and `exact` says whether those two coincide. A monthly series answering
    a four-week card lands one month out and says so, rather than relabelling a month as four weeks.
    """

    horizon_weeks: int
    period: date
    value: float
    quantiles: Mapping[float, float] = field(default_factory=dict)
    steps: int = 1
    resolution: str = MONTHLY
    exact: bool = False
    # Periods ahead of the last PRICE, which is what a card is headed with. `steps` is periods ahead
    # of the last bucket, which is what the model took. On a market priced on the 1st they are the
    # same number; on one priced on the 30th the model takes one more step to reach the same place,
    # and a card headed "4 months" over a three-month question would be wrong in the other
    # direction. Zero means it was never set, and the label falls back to `steps`.
    steps_ahead: int = 0

    @property
    def resolution_note(self) -> str:
        if self.exact:
            return ""
        days = round(self.steps * PERIOD_DAYS[self.resolution])
        word = {DAILY: "daily", WEEKLY: "weekly", MONTHLY: "monthly"}[self.resolution]
        return (f"asked for {self.horizon_weeks} weeks; the series is {word}, so the nearest it can "
                f"answer is {days} days")


@dataclass(frozen=True)
class ModelSpec:
    """What the registry knows about a model before it is ever run."""

    key: str
    family: str
    version: str
    licence: str
    licence_class: LicenceClass

    # What a person is told this model is, and what it assumes.
    #
    # `key` is an identifier - it is in the vintage store's primary key and in the application's
    # pinned card model, so it never changes. That makes it the wrong thing to put in front of a
    # reader, and "naive_last" and "ets" were in front of readers on the Forecast Performance screen.
    # A buyer cannot weigh a method whose name they have to already know; they can weigh "no change"
    # against "last move continues".
    display_name: str = ""
    # How the method works, in words a buyer reads once. Not what it is called in forecasting - that
    # is `family`, and "ETS" or "AR(1)" tells a purchasing manager nothing they can act on. Two
    # sentences at most, present tense, no arithmetic. The test is whether someone who has never seen
    # a forecasting model could say back what this one does.
    explanation: str = ""

    runtime: str = "in-process"          # in-process | worker | gpu-worker
    supports_quantiles: bool = False
    supports_covariates: bool = False
    supports_multivariate: bool = False
    is_control: bool = False             # a permanent benchmark rather than a candidate
    note: str = ""

    @property
    def commercial(self) -> bool:
        return self.licence_class is LicenceClass.COMMERCIAL

    @property
    def name(self) -> str:
        """What to print. Falls back to the key so an unnamed model is visibly unnamed."""
        return self.display_name or self.key


@dataclass(frozen=True)
class ForecastResult:
    model: ModelSpec
    as_of: date
    points: tuple[ForecastPoint, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def at(self, horizon_weeks: int) -> ForecastPoint | None:
        return next((p for p in self.points if p.horizon_weeks == horizon_weeks), None)


@runtime_checkable
class ForecastModel(Protocol):
    """A model is anything with a spec and a forecast method."""

    spec: ModelSpec

    def forecast(self, request: ForecastRequest) -> ForecastResult: ...


def period_after(day: date, steps: int, frequency: str) -> date:
    """The period start `steps` ahead, in the series' own frequency."""
    if frequency == DAILY:
        return date.fromordinal(day.toordinal() + steps)
    if frequency == WEEKLY:
        return date.fromordinal(day.toordinal() + 7 * steps)
    index = (day.year * 12 + day.month - 1) + steps
    return date(index // 12, index % 12 + 1, 1)


def steps_for(horizon_weeks: int, frequency: str, offset_days: int = 0) -> tuple[int, bool]:
    """How many periods of this series cover a calendar horizon, and whether they cover it exactly.

    Never fewer than one: a card asking four weeks of a monthly series is answered one month out,
    which is the closest honest thing the data can say. The second value is what stops that being
    passed off as four weeks.

    `offset_days` is how far the last PRICE sits past the last BUCKET.

    **On a monthly series it no longer moves anything** (Stefan, 23 November 2026: "Start with next
    month"). One, two and three months are the next three calendar months after the month the
    price was struck in, whatever day of it that was. Three rulings got here:

    30 September 2026 - a market priced on the 30th drew its one-month point on the 1st, a day
    later, and its three-month point two months and a day out. The offset was added to the
    distance, so late in a month every horizon moved one month further.

    23 November, morning - the offset and the horizon were rounded as one sum, so each horizon
    decided for itself whether to move. For a price struck on the 19th to the 23rd two of them
    landed on one month: Polyether Polyol China read January, January, February. Rounding them
    apart made it January, February, March.

    23 November, afternoon - which is three different months and still no December, on a report
    dated 23 November. A buyer standing in November is buying December. So the months are simply
    the next three, and what 30 September was really objecting to - a point drawn the day after
    the price and called a month ahead - is answered where it arose, in where the point is DRAWN:
    on the price's own day of the month, one, two and three months on (see
    `raw_material_forecasting.forecast_cards.shown_day`). The period stays the first of its month,
    because that is the key a claim is settled against.

    A weekly or daily series still counts the offset. There it is a matter of days, a step is
    short enough for the rounding to be right, and nothing about it was ever in question.
    """
    span = PERIOD_DAYS[frequency]
    target = horizon_weeks * 7.0 + max(0, offset_days)
    ahead = max(1, round(horizon_weeks * 7.0 / span))
    # Rounded apart from the horizon, never as one sum with it: as one sum each horizon decides
    # for itself whether the offset costs it a step, and two of them can land on one period.
    moved = 0 if frequency == MONTHLY else round(max(0, offset_days) / span)
    steps = ahead + moved
    return steps, abs(steps * span - target) < 0.5
