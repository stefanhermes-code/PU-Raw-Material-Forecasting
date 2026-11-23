"""Which chemicals in a geography have gone quiet, measured against their own rhythm.

The population is `populations.PAIRS`: a chemical in a geography, which is a market seen at two of
its four fields. It is deliberately coarser than a market - a chemical nobody prices anywhere is
the question Stefan asked, and a market that is quiet on one set of terms while another publisher
prices it on others is a feed question, which the collected-feeds panel answers. The watch said
"series" and its own docstring said "market"; it is neither (masterplan B, v1.2.9).

Stefan, 11 October 2026, asking why four of five chemicals in a quarterly report started in
September. The answer was that nothing earlier existed - polyether polyol in China stops on
10 April and restarts on 8 September, propylene oxide stops in August 2023 and restarts this
September - and the reason nobody knew is that nothing in the application was watching.

A series stopping is not a reporting fault and not a data error. It is a collection event, and it
is invisible until somebody asks a question that happens to depend on it.

The rhythm is read off the series itself. Stefan's standing rule: "not assume all is daily to make
something declare it is missing, that is a basic mistake you make all the time." A monthly
publisher is not late on day eight. So the cadence is the median gap between that series' own last
readings, and lateness is a multiple of it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median
from typing import Any, Iterable, Sequence

from .trade_feeds import arrival_day

# How many of the most recent gaps decide the rhythm. Enough to survive one irregular print,
# short enough that a publisher who moved from monthly to weekly is judged on what it does now.
RECENT_GAPS = 12
# A reading is expected every `cadence` days. Past twice that it is late; past four times it has
# stopped being a rhythm at all; past a year it is history rather than a gap.
LATE_AT = 2.0
QUIET_AT = 4.0
STOPPED_DAYS = 365
# Nothing is called late inside this many days, whatever its cadence says. A daily publisher that
# skips a long weekend has not stopped collecting.
FLOOR_DAYS = 5

CURRENT, LATE, QUIET, STOPPED = "current", "late", "quiet", "stopped"
WORST_FIRST = {STOPPED: 0, QUIET: 1, LATE: 2, CURRENT: 3}


@dataclass(frozen=True)
class Watch:
    chemical: str
    geography: str
    last_day: date
    readings: int
    cadence_days: int
    days_since: int
    state: str
    # What the newest figure is ABOUT, which for a customs feed is not the day it could first be
    # seen. Both are needed and they are not interchangeable: lateness is judged on `last_day`,
    # because Comtrade's December is not late in January, and a reader is shown `covers_to`,
    # because "last reading 1 March 2026" is a day no reading carries.
    #
    # Added 5 November 2026. The quiet panel printed `last_day` under the heading "Last reading"
    # and the hole beside it off `observation_date`, so one row showed two dates measured from two
    # different clocks: South Korea read "last reading 2026-03-01" when its newest customs month
    # is December 2025, three months earlier. Same shape as the defects of v1.2.61 and v1.2.62 -
    # one rule, two copies, and the reader shown the wrong copy.
    covers_to: date | None = None

    @property
    def overdue_by(self) -> int:
        """Days past when the next reading was due, by its own rhythm."""
        return max(0, self.days_since - self.cadence_days)

    @property
    def figure_day(self) -> date:
        """The day to SHOW a reader: what the newest figure is about."""
        return self.covers_to or self.last_day

    @property
    def lagged(self) -> bool:
        """Whether this market's newest figure arrived later than the period it describes."""
        return self.covers_to is not None and self.covers_to != self.last_day

    @property
    def expected(self) -> str:
        return rhythm_name(self.cadence_days)


def rhythm_name(cadence_days: int) -> str:
    """A gap in days, in the words a reader uses. Said here rather than wherever it is printed, so
    that two screens cannot call the same 30-day gap by two different names."""
    if cadence_days <= 0:
        return "no rhythm yet"
    if cadence_days <= 2:
        return "daily"
    if cadence_days <= 10:
        return "weekly"
    if cadence_days <= 45:
        return "monthly"
    if cadence_days <= 120:
        return "quarterly"
    return f"every {cadence_days} days"


def _days(values: Iterable[Any]) -> list[date]:
    out = []
    for value in values or ():
        try:
            out.append(date.fromisoformat(str(value)[:10]))
        except ValueError:
            continue
    return sorted(set(out))


def cadence_of(days: Sequence[date]) -> int:
    """The gap actually kept, in days, read off the last readings themselves."""
    if len(days) < 2:
        return 0
    gaps = [(b - a).days for a, b in zip(days, days[1:]) if (b - a).days > 0]
    if not gaps:
        return 0
    return max(1, int(round(median(gaps[-RECENT_GAPS:]))))


def state_of(days_since: int, cadence: int) -> str:
    if cadence <= 0:
        return CURRENT
    if days_since >= STOPPED_DAYS:
        return STOPPED
    if days_since > max(cadence * QUIET_AT, FLOOR_DAYS):
        return QUIET
    if days_since > max(cadence * LATE_AT, FLOOR_DAYS):
        return LATE
    return CURRENT


def watch(rows: Iterable[dict[str, Any]], *, as_of: date | None = None) -> list[Watch]:
    """One row per chemical in a geography, worst first.

    `rows` are observations carrying `product_family`, `market_geography` and `observation_date`.
    One with a single reading has no rhythm to be late against and is reported as current.
    """
    today = as_of or date.today()
    held: dict[tuple[str, str], list[Any]] = {}
    # The same readings on the other clock: the period each figure describes. Kept beside the
    # arrival days rather than recomputed by whoever draws the table, so the two cannot drift.
    covers: dict[tuple[str, str], list[Any]] = {}
    for row in rows or ():
        chemical = str(row.get("product_family") or "").strip()
        geography = str(row.get("market_geography") or "").strip()
        if not chemical or not geography:
            continue
        # A customs figure is judged on when it was released, not on the month it describes:
        # Eurostat publishes about ten weeks after the month ends (see trade_feeds).
        held.setdefault((chemical, geography), []).append(
            arrival_day(row) or row.get("observation_date") or row.get("publication_date"))
        covers.setdefault((chemical, geography), []).append(
            row.get("observation_date") or row.get("publication_date"))
    out: list[Watch] = []
    for (chemical, geography), values in held.items():
        days = _days(values)
        if not days:
            continue
        cadence = cadence_of(days)
        since = (today - days[-1]).days
        about = _days(covers.get((chemical, geography)) or ())
        out.append(Watch(chemical=chemical, geography=geography, last_day=days[-1],
                         readings=len(days), cadence_days=cadence, days_since=max(0, since),
                         state=state_of(max(0, since), cadence),
                         covers_to=about[-1] if about else days[-1]))
    return sorted(out, key=lambda one: (WORST_FIRST.get(one.state, 9), -one.days_since,
                                        one.chemical, one.geography))


def gaps_in(rows: Iterable[dict[str, Any]], *, chemical: str, geography: str,
            smallest: int = 45) -> list[tuple[date, date, int]]:
    """Holes inside a series' own history: (last reading before, first after, days).

    A series can be collected today and still have lost a year in the middle, which is what
    polyether polyol in China did between April and September 2026. A hole is worth seeing even
    once the series is current again.
    """
    days = _days(row.get("observation_date") or row.get("publication_date") for row in rows or ()
                 if str(row.get("product_family") or "").strip() == chemical
                 and str(row.get("market_geography") or "").strip() == geography)
    out = []
    for before, after in zip(days, days[1:]):
        span = (after - before).days
        if span >= smallest:
            out.append((before, after, span))
    return sorted(out, key=lambda one: -one[2])
