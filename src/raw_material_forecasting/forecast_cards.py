"""The three horizon cards on the Forecast page.

What a reader is owed by a forecast card is the RANGE. The card used to lead with a single number,
and on a level-only model that number is identical at four, eight and twelve weeks - which is
arithmetically correct and reads as a broken screen. The thing that actually differs across the three
horizons was the band, and the band was in the small print under a horizontal rule.

So the range leads, and every number on the card says what it is. The forecast stays, because a buyer
needs a figure to put in a calculation, but it is labelled "Forecast" and never left as a bold number
between two others for the reader to interpret.

The width is printed as a percentage as well, because that is what makes the widening legible. A
reader sees in one glance across three cards that the further out, the less is known, without
comparing four-digit numbers.

The width is printed as TWO percentages, down and up, and never as a single "+/-", because the band
is not symmetric and the forecast does not sit in the middle of it - on TDI China it sits 139 CNY
above the middle at the first horizon. Printing "+/-12%" would flatten that into a symmetry the data
does not have, and calling the forecast the "midpoint" would be simply false. A reader who checks the
arithmetic must find it correct; one who checked this card found it did not add up, and was right.

WHY the band is asymmetric is worth stating accurately, because the obvious story is wrong. The model
is not biased downwards: its mean error is -3 CNY on a ~16,000 price, with 13 misses low against 14
high. The asymmetry is in the shape - a median of +262 against that mean, so many small overshoots
and a few large undershoots - and it rests on very little. There are 27 errors in the whole history,
so the 10th percentile is interpolated between the 3rd and 4th smallest of them, and those are three
sharp monthly drops (Jun 2024, May 2026, Jun 2026). Three observations decide the lower bound of
every card. That is a fair account of what happened before; it is not a probability, and nothing on
the card should imply that it is.

Nothing here decides what the numbers are. The models produce them; this module decides what a
person sees first.
"""
from __future__ import annotations

import html as _html
from dataclasses import dataclass

from raw_material_forecasting import plausibility
from raw_material_forecasting.units import display_unit

# The title states the horizon at the resolution the data ANSWERED at, not the one it was asked in.
#
# The engine is asked for 4, 8 and 12 weeks. When a series only qualifies at monthly - TDI China has
# a daily density of 0.04 and weekly of 0.21, so only monthly is usable - it answers 1, 2 and 3
# months and records exact=False to say the horizon is not the one requested. A card headed
# "4 Week Forecast" over a figure that is really one month out claims a precision the history cannot
# support. The horizons stay contracted in weeks internally, where the backtest reads them.
_RESOLUTION_UNIT: dict[str, str] = {"D": "Day", "W": "Week", "M": "Month"}

HORIZON_TITLES: dict[int, str] = {4: "4 Week Forecast", 8: "8 Week Forecast",
                                 12: "12 Week Forecast"}


def governed_point(payload: dict, weeks: int) -> dict | None:
    """The stored forecast of the method in force at this horizon, or None.

    Only SUCCEEDED counts. PENDING means the method in force has not been run yet, FAILED means it
    ran and refused, NOT_GOVERNED means this pair has no selection - in all three the page keeps
    doing what it did before, which is what decision D5 says should happen.

    `period` comes across as it was stored. Nothing about the METHOD crosses into what a reader
    sees: Stefan's rule is that no sourcing behind a number is shown, for a forecast or for a
    current price, so the method key travels only far enough to match the point internally.

    THE BAND IS CHECKED ON THE WAY OUT. The engine refuses to store an impossible interval from
    v1.2.84, but this reads the NEWEST stored governed forecast and the ones already in the store
    stay as they are - they are the record of what was published. On 18 November 2026 the newest
    China DEG forecast was written the day before with a tenth-percentile band of -8,483 CNY/MT,
    and it would have gone on being drawn until that market next priced. The record keeps what was
    said; the screen does not draw a negative price.

    It lives here rather than in `app.py` so that it can be tested without executing the page: a
    second execution of `app.py` in one test session is enough to disturb another page test.
    """
    entry = ((payload.get("governed") or {}).get("horizons") or {}).get(str(weeks)) or {}
    if entry.get("status") != "SUCCEEDED":
        return None
    stored = entry.get("forecast") or {}
    if stored.get("value") is None:
        return None
    band, _withheld = plausibility.usable_quantiles(stored.get("quantiles") or {},
                                                    payload.get("anchor_value"), weeks)
    return {"value": stored.get("value"), "quantiles": band,
            "period": stored.get("period"), "horizon_weeks": weeks,
            "model": entry.get("method_key") or "", "governed": True,
            "governed_as_of": entry.get("as_of"), "governed_current": entry.get("current")}


def horizon_title(weeks: int, point: dict | None = None) -> str:
    """"1 Month Forecast" when the series answered monthly; "4 Week Forecast" when it answered weekly."""
    if point:
        # Ahead of the price, like the selector beside it.
        steps = int(point.get("ahead") or point.get("steps") or 0)
        unit = _RESOLUTION_UNIT.get(str(point.get("resolution") or "").upper())
        if steps and unit:
            # "2 Month Forecast", the way the application already writes "4 Week Forecast":
            # the unit is adjectival here, so it does not take a plural.
            return f"{steps} {unit} Forecast"
    return HORIZON_TITLES.get(weeks, f"{weeks} Week Forecast")

LOW_QUANTILE = "0.1"
HIGH_QUANTILE = "0.9"

# Below this, a move is not a move: it is the difference between two roundings.
FLAT_PERCENT = 0.5


@dataclass(frozen=True)
class CardContent:
    title: str
    status: str
    headline: str          # the range, or the forecast when no range could be computed
    supporting: str        # the forecast and how it sits against the last price anybody printed
    latest: str            # that last print, with its date
    footnote: str          # how wide the range is, and the date it lands on
    has_range: bool


def _pct(value: float) -> str:
    return f"{value:,.0f}%"


def skew(point: dict) -> tuple[float, float] | None:
    """(downside %, upside %) of the band against the forecast, or None when there is no band."""
    quantiles = point.get("quantiles") or {}
    low, high = quantiles.get(LOW_QUANTILE), quantiles.get(HIGH_QUANTILE)
    value = float(point.get("value") or 0)
    if low is None or high is None or not value:
        return None
    return (float(low) / value - 1) * 100, (float(high) / value - 1) * 100


def latest_line(latest: dict | None, show) -> str:
    """The last price anybody actually printed, and when.

    A monthly series forecasts from the month's median, which is the right number to model and the
    wrong one to show alone. In September 2026 the month's median was 16,850 while the market had
    printed 17,367 two days before - the card was 3.1% behind a market that had just moved 3.6% in
    two days, and nothing on it said so.

    The publisher count is there because one publisher moving is a publisher moving and four moving
    together is the market.
    """
    if not latest or latest.get("value") is None:
        return ""
    when = _pretty(latest.get("date"))
    publishers = int(latest.get("publishers") or 0)
    agreement = f" · {publishers} publishers" if publishers > 1 else ""
    return f"Last print {show(float(latest['value']))}" + (f" · {when}" if when else "") + agreement


def _pretty(value) -> str:
    from datetime import date as _date
    try:
        return _date.fromisoformat(str(value)[:10]).strftime("%-d %b %Y")
    except Exception:
        try:
            return _date.fromisoformat(str(value)[:10]).strftime("%d %b %Y").lstrip("0")
        except Exception:
            return ""


def card_content(point: dict | None, *, weeks: int, anchor: float | None,
                 show, landing: str, latest: dict | None = None,
                 unit: str | None = None) -> CardContent:
    """What the card says, given one model point.

    `show` formats an amount in the reader's currency; it is passed in rather than imported so this
    stays free of the application's FX table and can be exercised on its own.

    The comparison is against the LAST PRINT where there is one, not against the month's median. A
    buyer reading "against today" means against the price on the screen today.
    """
    title = horizon_title(weeks, point)
    printed = latest_line(latest, show)
    # What the series is actually in, not a literal. `display_unit` turns whatever the store holds
    # into the spelling the trade reads, and an empty unit into MT, so a caller with nothing to
    # pass gets the old wording.
    per = display_unit(unit)
    if point is None:
        return CardContent(title, "IN PREPARATION", "In preparation",
                           "This horizon opens as the price history builds.", printed, "",
                           has_range=False)

    value = float(point["value"])
    quantiles = point.get("quantiles") or {}
    low, high = quantiles.get(LOW_QUANTILE), quantiles.get(HIGH_QUANTILE)

    # Against the last print when there is one; the month's median is the fallback.
    against = float((latest or {}).get("value") or 0) or (float(anchor) if anchor else 0.0)
    move = ""
    if against:
        delta = (value / against - 1) * 100
        label = "the last print" if (latest or {}).get("value") else "today"
        move = (f"level with {label}" if abs(delta) < FLAT_PERCENT
                else f"{abs(delta):.1f}% {'below' if delta < 0 else 'above'} {label}")

    if low is None or high is None:
        # No band. The single number goes first, and the card says the band is missing rather than
        # letting the reader assume the forecast is precise.
        supporting = move or ""
        return CardContent(title, "INDICATIVE", f"{show(value)} / {per}", supporting, printed,
                           f"No range yet - too little history at this horizon. For {landing}.",
                           has_range=False)

    down, up = skew(point)
    # "Forecast", not "midpoint": the number is the model's central estimate and it does not sit in
    # the middle of the band, because the band is not symmetric.
    supporting = f"Forecast {show(value)}" + (f" · {move}" if move else "")
    return CardContent(
        title, "INDICATIVE",
        f"{show(low)} – {show(high)}",
        supporting,
        printed,
        f"Range {_pct(down)} to +{_pct(up)} · {coverage_note(point)} · for {landing}.",
        has_range=True,
    )


def coverage_note(point: dict) -> str:
    """How often this range actually held, in counts.

    The page used to say the price lands inside "about eight times in ten". Nobody had measured it.
    When it was measured - rolling the origin through the history and refitting at each one - the
    band held 56%, 60% and 50% of the time at one, two and three months. The width is now calibrated
    so it does hold, and what is printed is the count that was observed, not a probability: "held 13
    of the last 16 tests" is a claim a reader can weigh. "Eight times in ten" is one they cannot.
    """
    calibration = point.get("calibration") or {}
    if not calibration.get("tested"):
        return "not enough history to test this range yet"
    inside, origins = calibration.get("inside"), calibration.get("origins")
    if inside is None or not origins:
        return "not enough history to test this range yet"
    return f"held {inside} of the last {origins} tests"


def card_html(content: CardContent) -> str:
    def esc(value: str) -> str:
        return _html.escape(str(value or ""))

    headline_class = "value range" if content.has_range else "value"
    latest = (f'<div class="delta flat" style="opacity:.72">{esc(content.latest)}</div>'
              if content.latest else "")
    return (
        '<div class="forecast-card">'
        '<div class="card-top">'
        f'<div class="card-title">{esc(content.title)}</div>'
        f'<div class="status">{esc(content.status)}</div>'
        "</div>"
        f'<div class="{headline_class}">{esc(content.headline)}</div>'
        f'<div class="delta flat">{esc(content.supporting)}</div>'
        f'{latest}'
        f'<div class="meta">{esc(content.footnote) or "&nbsp;"}</div>'
        "</div>"
    )


def move_html(move: float | None) -> str:
    """The banner's direction: up, down, or - where the forecast IS the reference - neither.

    A move that prints as 0.0% used to be drawn as a red rise, because zero is not below zero.
    Since v1.2.121 a forecast can equal the market price it is read against, and "up 0.0%" beside
    two identical figures is the page arguing with itself.
    """
    if move is None:
        return ""
    if round(abs(move), 1) == 0.0:
        return '<div class="figure banner-move">&#9644; no change</div>'
    return (f'<div class="figure banner-move {"up" if move >= 0 else "down"}">'
            f'{"&#9650;" if move >= 0 else "&#9660;"} {abs(move):.1f}%</div>')


def flat_note(points: list[dict], metadata: dict | None = None) -> str:
    """Why the same figure appears on all three cards, when it does.

    It is arithmetically correct and it reads as a broken screen - which is a fair reaction, and the
    third time it has been raised. Exponential smoothing carries a trend forward only when the trend
    earns its extra parameters against the history; on twenty-eight months of Chinese TDI it does
    not, so the model holds the level and the level is the same however far out you ask.

    Said once under the row rather than three times on the cards, because repeating it on each card
    would make it look like three separate findings.
    """
    values = [round(float(point.get("value") or 0), 2) for point in points or ()]
    if len(values) < 2 or len(set(values)) != 1:
        return ""
    form = str((metadata or {}).get("form") or "")
    if form and "Ad" in form:
        if not (metadata or {}).get("trend_carried"):
            return ""
        # Since v1.2.122 a trend moves all three cards by the same amount, so the question "why
        # is it the same figure" comes up for this form too, and the answer is a different one.
        start = ("the newest market price" if (metadata or {}).get("starts_from") == "newest price"
                 else "the recent level")
        return (f"The same figure at all three horizons: the price has been drifting, and the "
                f"forecast is {start} with half of one month's drift added. Measured on past "
                f"prices, a drift carries for about a month and then gives some back, so nothing "
                f"more is added further out. What changes with distance is the range around it.")
    if (metadata or {}).get("starts_from") == "newest price":
        # Since v1.2.121 the figure is also the market price printed above it, and a reader who
        # sees today's price three times over is owed the reason more than anyone.
        return ("The same figure at all three horizons, and it is the newest market price: this "
                "market is priced through the month, so the forecast starts from that price, and "
                "the model finds no trend in the history strong enough to carry forward from it. "
                "What changes with distance is the range around it.")
    return ("The same figure at all three horizons: the model finds no trend in the history strong "
            "enough to carry forward, so it holds the level. What changes with distance is the "
            "range around it.")


def widths(points: list[dict]) -> list[tuple[int, float]]:
    """(horizon, full band width as a percentage of the forecast) for each point that carries one.

    Used to check the one property a reader is entitled to assume: further out is less certain.
    """
    out = []
    for point in points:
        quantiles = point.get("quantiles") or {}
        low, high = quantiles.get(LOW_QUANTILE), quantiles.get(HIGH_QUANTILE)
        value = float(point.get("value") or 0)
        if low is None or high is None or not value:
            continue
        out.append((int(point["horizon_weeks"]), (float(high) - float(low)) / value * 100))
    return sorted(out)


def widening(points: list[dict]) -> bool:
    """True when every longer horizon is at least as uncertain as the one before it."""
    measured = [width for _weeks, width in widths(points)]
    return all(later >= earlier - 1e-9 for earlier, later in zip(measured, measured[1:]))


def horizon_label(weeks: int, points: list[dict] | None = None) -> str:
    """The short form for a selector: "3 months" when the series answered monthly, else "12 weeks".

    Counted from the PRICE, not from the bucket. On a market collected daily the model takes an
    extra step to reach three months past the last price, and heading that card "4 months" would
    answer a three-month question with a four-month label. `ahead` is what the reader is standing
    on; `steps` is what the model did. Older payloads carry only `steps` and read as they did.
    """
    for point in points or ():
        if int(point.get("horizon_weeks") or 0) != weeks:
            continue
        steps = int(point.get("ahead") or point.get("steps") or 0)
        unit = _RESOLUTION_UNIT.get(str(point.get("resolution") or "").upper())
        if steps and unit:
            return f"{steps} {unit.lower()}{'s' if steps != 1 else ''}"
    return f"{weeks} weeks"


# --- the banner -----------------------------------------------------------------------------------
#
# Three cards became one, and then one card became one month.
#
# The cards carried the same forecast number three times over, because this market has no direction
# the model can follow and the centre is identical at every horizon. One row per month put the only
# thing that differed - the range - in a column that could be read down.
#
# Then the rows went too. Stefan, 16 September 2026: "When I am a buyer I only look ahead one month,
# maximum." Two and three months stay on the chart, where the point is that the work looks further
# than the banner does; on the banner they were three rows of the same number under three widening
# bands, and he read that as noise rather than information.
#
# What is left is the month a buyer plans: today's price, the forecast for next month, and how wide
# the band has to be to hold the price at each of two stated probabilities. Nothing on it is ours -
# no publisher counts, no backtest origins, no model name.
#
# The probabilities replace the two hit-rate columns. "Within 15% held 78% of the time" and "an 80%
# band is 14% wide" are the same measurement read from opposite ends, and printing both put two
# answers to one question on a single line. The reader asks how sure, and is told how wide.

BANNER_COVERAGES: tuple[str, ...] = ("80", "90")

# Below this many trials the band is not drawn. Not the old gate of eight (17 September 2026: six
# trials is not nothing, and hiding the band was the worse answer) - three, because one or two
# misses are numbers, not a distribution, and a percentile cut through them printed Adipic Acid
# China as USD 1,350 with a band of 1,421-1,424. The forecast itself still shows.
MIN_TRIALS_FOR_A_BAND = 3

# What the banner says about where the number came from.
#
# It used to print how many rolling origins the band was measured on - "Measured on 74 past
# forecasts." Stefan, 24 September 2026: that is our bookkeeping on a customer screen, and a
# forecast is calculated rather than measured. The count also tells a competitor how the work is
# done, and how the work is done is not on the page.
#
# What is left says the methods belong to this industry and says nothing about their inputs.
METHOD_NOTE = ("Calculated with forecasting methods developed and validated for polyurethane "
               "raw materials.")


@dataclass(frozen=True)
class BannerRow:
    """One stated probability, and the band that held the price that often.

    80% means what it says: of the past forecasts we tested, 80 in 100 landed inside this band. That
    is a description of the record and it is true whether the record is long or short.

    How MUCH record there is behind it is a separate fact, and it belongs beside the table rather
    than inside the label - see `trials` on BannerContent. For about an hour on 17 September 2026
    this row printed the coverage that could be inferred from the trial count instead, so TDI read
    73% where it had always read 80%. Same band, different question answered, and the reader was
    given no way to tell which.
    """
    coverage: str              # "80"
    low: str
    high: str
    low_value: float | None = None
    high_value: float | None = None
    # How far the band reaches below and above the forecast, as a share of the forecast. Negative
    # below, positive above. These are what the two halves of the bar are labelled with, and the
    # widths they are drawn at, so the lopsidedness of a measured band is visible rather than
    # inferred from four numbers in a table. None when the band is untested.
    down: float | None = None
    up: float | None = None

    @property
    def drawable(self) -> bool:
        return self.down is not None and self.up is not None


@dataclass(frozen=True)
class BannerContent:
    title: str
    as_at: str
    today: str                 # the consolidated market price for the day
    forecast: str
    month: str                 # the month the forecast is for: "October 2026"
    rows: tuple[BannerRow, ...]
    # The price the forecast is measured from, and the month it belongs to.
    #
    # Today's price was taken off the banner on 24 September 2026 because it could not be compared
    # with the figure beside it: today is the latest print and the forecast is built from the
    # month's bucket, so TDI China read 2,311 today against 2,259 for October - a 2.2% fall on a
    # model that had moved four yuan on fifteen thousand. Removing it also removed the only
    # reference point, and a forecast with nothing to read it against cannot show a direction,
    # which is the part a buyer acts on.
    #
    # So this is the bucket the model actually forecast from, not the latest print. Same basis as
    # the forecast, so the comparison holds and the percentage means what it says.
    previous: str = "-"
    previous_month: str = "-"
    previous_value: float | None = None
    # The day of the market price the forecast is read against, where there is one: "23 Nov 2026".
    #
    # Since 24 November 2026 the figure beside the forecast is the price Current Market shows, on
    # the day it shows it, and the percentage is measured from it - Stefan: "a customer would
    # expect this to be the same number and any calculations coming from this to stem from there."
    # The September reasoning above was right about the arithmetic and wrong about the reader: a
    # month's median is a number no page of this application shows anywhere else, so nobody could
    # check it, and on Polyether Polyol China it read USD 1,566 beside a market at 1,413.
    #
    # Empty where the market has no current price. The bucket then stands, labelled as what it
    # is, exactly as before.
    previous_day: str = ""
    coverages: tuple[str, ...] = BANNER_COVERAGES
    forecast_value: float | None = None
    currency: str = ""         # the currency the reader is being shown, e.g. "USD"
    # How many past forecasts the bands were measured on. Kept because the figure is real and the
    # performance screen reads the same evidence; the banner stopped printing it on 24 September
    # 2026, because a trial count is ours and not the reader's - see METHOD_NOTE.
    trials: int | None = None

    # The forecast against the price it was measured from, in per cent. Computed where both raw
    # figures are in hand rather than from the converted ones, so it survives a reader whose
    # currency the page has not been asked to convert into.
    move: float | None = None

    @property
    def gap_months(self) -> int | None:
        """Calendar months between the price the forecast starts from and the month it lands in.

        One on a market that publishes on time. Three where the customs record runs two months
        behind, because the newest price is already two months old before the horizon is counted.
        Zero or None when either month is unknown.
        """
        from datetime import datetime
        try:
            start = datetime.strptime(self.previous_month, "%B %Y")
            end = datetime.strptime(self.month, "%B %Y")
        except (ValueError, TypeError):
            return None
        return (end.year - start.year) * 12 + end.month - start.month


def answers_by_month(payload: dict, point: dict | None = None) -> bool:
    """Whether this market's forecast is one figure per month.

    A point the engine computed says so itself. A governed point read back from the store carries
    no resolution, so the market's own chosen frequency answers for it - and where the series was
    too thin to choose one, the engine fell back to months, which is what None means there.
    """
    stated = str((point or {}).get("resolution") or "").upper()
    if stated:
        return stated == "M"
    chosen = ((payload or {}).get("resolution") or {}).get("chosen")
    return chosen in (None, "M")


def shown_day(period, priced_on, *, monthly: bool):
    """The day a forecast point is drawn on and dated with. `period` itself is never changed.

    A monthly forecast is filed under the first of its month, because that is the key a claim is
    settled against when the month closes. Drawn there, a point is only "a month ahead" for a
    market that prices on the 1st. TDI China, priced 22 November: its December figure sat on
    1 December, nine days on, under a card headed "1 Month Forecast" - and its three-month figure
    on 1 February, ten weeks out. Stefan said exactly this on 30 September 2026 about a price
    struck on the 30th: three months has to be three months.

    The engine answered that by moving every horizon a month further, which made the distances
    right and dropped December from a report dated 23 November. On that day Stefan chose the
    months - "Start with next month" - so the distance is put right here instead: the December
    figure is drawn on 22 December, January's on 22 January, February's on 22 February. One, two
    and three months from the price, each inside the month it speaks for.

    A month too short for the day - the 31st, in February - takes its last day. A market that
    prices on the 1st is drawn exactly where it always was. Weekly and daily forecasts are already
    dated to the day and pass straight through.
    """
    import calendar
    from datetime import date as _date

    def _day(value):
        try:
            return _date.fromisoformat(str(value)[:10])
        except Exception:
            return None

    filed, priced = _day(period), _day(priced_on)
    if filed is None or priced is None or not monthly:
        return filed
    last = calendar.monthrange(filed.year, filed.month)[1]
    return filed.replace(day=min(priced.day, last))


def _month_name(value) -> str:
    """The month a horizon lands in, as a reader names it: "October 2026"."""
    from datetime import date as _date
    try:
        return _date.fromisoformat(str(value)[:10]).strftime("%B %Y")
    except Exception:
        return "-"


def band_for(point: dict, coverage: str) -> tuple[float, float] | None:
    """(low, high) as fractions of the forecast at this probability, or None when untested.

    Measured off the errors the model actually made at this horizon, not drawn around its own fit.
    Untested is None rather than a number: a band off four trials is not evidence.
    """
    calibration = point.get("calibration") or {}
    if not calibration.get("tested"):
        # A GOVERNED forecast carries the band its own method produced, and that is the band to
        # show. Stefan, 22 October 2026: use the governed method's own stored quantiles, not the
        # old model's calibration - a band measured off momentum's errors says nothing about what
        # TimesFM knows, and applying one to the other's centre is two methods in one number.
        #
        # The stored band is 0.1 and 0.9: the 80% interval, and nothing else. All 233 governed
        # vintages carry those three quantiles and no others, so there is NO 90% band to show and
        # this returns None for it. Scaling the 80% out to a 90% would invent a width the method
        # never produced.
        if coverage == "80" and point.get("governed"):
            quantiles = point.get("quantiles") or {}
            low, high, value = (quantiles.get(LOW_QUANTILE), quantiles.get(HIGH_QUANTILE),
                                point.get("value"))
            if low is not None and high is not None and value:
                return (min(float(low) / float(value) - 1.0, 0.0),
                        max(float(high) / float(value) - 1.0, 0.0))
        return None
    origins = calibration.get("origins")
    if origins is not None and int(origins) < MIN_TRIALS_FOR_A_BAND:
        return None
    band = (calibration.get("bands") or {}).get(coverage)
    if not band or len(band) != 2:
        return None
    # The band always contains the forecast (see the engine's error_band). Applied here as well
    # because a forecast kept from before 17 October 2026 still carries the band it was cut with.
    return min(float(band[0]), 0.0), max(float(band[1]), 0.0)


def trials_for(point: dict) -> int | None:
    """How many past forecasts the bands on this point were measured on."""
    calibration = (point or {}).get("calibration") or {}
    origins = calibration.get("origins")
    return int(origins) if origins else None


def banner_content(payload: dict, points: list[dict], *, show,
                   today: float | None, as_at, title: str = "",
                   amount=None, currency: str = "", show_today=None,
                   market_price: tuple[str, float] | None = None) -> BannerContent:
    """The banner: today, next month's forecast, and a band per stated probability.

    `show` formats an amount in the reader's currency. `show_today` formats one that is ALREADY in
    it - the snapshot behind today's price is converted by the page before it arrives here, and
    running it through `show` converted it twice and printed USD 392 on a market near USD 2,630.

    Only the FIRST point is used. The page still asks the engine for three horizons and still draws
    all three on the chart; the banner is the month a buyer plans.

    `market_price` is the day and the figure Current Market prints for this market, ALREADY in the
    reader's currency - `market_lines.headline_price`. Where it is given it is the reference: the
    figure printed beside the forecast and the one the percentage is measured from. Where it is
    not - a market nobody has priced inside the current window - the month's bucket stands in.
    """
    convert = amount if amount is not None else (lambda _value: None)
    point = points[0] if points else None
    forecast = point.get("value") if point else None
    rows: list[BannerRow] = []
    for coverage in BANNER_COVERAGES:
        band = band_for(point, coverage) if point else None
        if band is None or forecast is None:
            # A GOVERNED forecast carries the band its own method produced, and for most of them
            # that is 0.1 and 0.9 - the 80% interval and nothing wider. TimesFM 2.5, the method in
            # force on more markets than any other, exposes a fixed grid of deciles and cannot be
            # asked for 0.05 and 0.95 at all.
            #
            # So the 90% row is not missing data, it is a band that does not exist, and the row is
            # left out rather than printed as "Not measured yet" - which says it is coming, and it
            # is not. Stefan saw that text on a TimesFM forecast on 22 October 2026.
            if point and point.get("governed"):
                continue
            rows.append(BannerRow(coverage=coverage, low="-", high="-"))
            continue
        low, high = float(forecast) * (1.0 + band[0]), float(forecast) * (1.0 + band[1])
        rows.append(BannerRow(coverage=coverage, low=show(low), high=show(high),
                              low_value=convert(low), high_value=convert(high),
                              down=float(band[0]) * 100.0, up=float(band[1]) * 100.0))
    # The bucket the model forecast from, which is the only figure on the same basis as the forecast.
    anchor = payload.get("anchor_value")
    anchor_value = float(anchor) if anchor is not None else None
    if market_price is not None and market_price[1]:
        priced_on, priced_at = market_price[0], float(market_price[1])
        # Measured between the two figures as printed, both in the reader's currency, so the
        # percentage is the one a reader gets with a calculator.
        shown_forecast = convert(float(forecast)) if forecast is not None else None
        if shown_forecast is None and forecast is not None:
            shown_forecast = float(forecast)
        return BannerContent(
            title=title or str(payload.get("label") or "").strip(),
            as_at=_pretty(as_at),
            today=(show_today or show)(float(today)) if today is not None else "-",
            forecast=show(float(forecast)) if forecast is not None else "-",
            month=_month_name(point.get("period")) if point else "-",
            previous=(show_today or show)(priced_at),
            previous_month=_month_name(priced_on),
            previous_value=priced_at,
            previous_day=_pretty(priced_on),
            move=((shown_forecast / priced_at - 1.0) * 100.0
                  if shown_forecast is not None else None),
            rows=tuple(rows),
            forecast_value=convert(float(forecast)) if forecast is not None else None,
            currency=currency,
            trials=trials_for(point) if point else None,
        )
    return BannerContent(
        title=title or str(payload.get("label") or "").strip(),
        as_at=_pretty(as_at),
        today=(show_today or show)(float(today)) if today is not None else "-",
        forecast=show(float(forecast)) if forecast is not None else "-",
        month=_month_name(point.get("period")) if point else "-",
        previous=show(anchor_value) if anchor_value is not None else "-",
        previous_month=_month_name(payload.get("period_start")),
        previous_value=convert(anchor_value) if anchor_value is not None else None,
        move=((float(forecast) / anchor_value - 1.0) * 100.0
              if forecast is not None and anchor_value else None),
        rows=tuple(rows),
        forecast_value=convert(float(forecast)) if forecast is not None else None,
        currency=currency,
        trials=trials_for(point) if point else None,
    )


def reference_label(content: BannerContent) -> str:
    """What the figure beside the forecast is called: the market price and its day, or the month."""
    if content.previous_day:
        return f"Market price · {content.previous_day}"
    return f"Last published · {content.previous_month}"


def banner_html(content: BannerContent) -> str:
    def esc(value) -> str:
        return _html.escape(str(value or ""))

    def bar(row: BannerRow) -> str:
        """One probability as a bar: how far down, how far up, split at the forecast.

        Stefan, 3 October 2026, drew it. The four numbers in the table it replaces said the same
        thing and said nothing about the shape: our bands are cut from the errors the model actually
        made, so they are lopsided, and on TDI China the 90% band reaches 17% down and 23% up. A
        buyer reads that off a picture in a second and off a table not at all.

        The two halves are drawn in proportion to those distances, so the split sits where the
        forecast sits. Red is the upside, because the high side is the one that costs money.
        """
        label = f"{esc(row.coverage)}% certain"
        if not row.drawable:
            return (f'<div class="band"><div class="band-label">{label}</div>'
                    '<div class="band-none">Not measured yet</div></div>')
        down, up = abs(float(row.down)), abs(float(row.up))
        # A band with no width on one side would collapse that half to a sliver with a number in it.
        # One per cent of floor keeps the label readable without telling a lie worth noticing.
        left = max(down, (down + up) * 0.01) or 1.0
        right = max(up, (down + up) * 0.01) or 1.0
        return (
            '<div class="band">'
            f'<div class="band-label">{label}</div>'
            '<div class="band-top">'
            f'<div class="band-top-left" style="flex:{left:.4f}">'
            f'<span>{esc(row.low)}</span><span class="band-at">Forecast</span></div>'
            f'<div class="band-top-right" style="flex:{right:.4f}">{esc(row.high)}</div>'
            "</div>"
            '<div class="band-bar">'
            f'<div class="band-down" style="flex:{left:.4f}">{row.down:+.1f}%</div>'
            f'<div class="band-up" style="flex:{right:.4f}">{row.up:+.1f}%</div>'
            "</div>"
            "</div>"
        )

    body = "".join(bar(row) for row in content.rows)
    return (
        '<div class="forecast-banner">'
        '<div class="banner-head">'
        # The publication date used to sit here. It now sits on the figure it describes - "Last
        # published · March 2026" - and a date in the corner with nothing to attach it to was read
        # as the date of the forecast. It stays in `as_at` for the explainer's footer.
        f'<div class="banner-title">{esc(content.title)}</div>'
        "</div>"
        '<div class="banner-figures">'
        # Today's price is still not here, and for the reason it was removed on 24 September 2026:
        # it is the latest print while the forecast is built from the month's bucket, so the two
        # could not be compared and the difference read as a movement that the model had not made.
        #
        # What is here instead is the bucket the model forecast FROM - same basis, so the comparison
        # holds. Without it the banner had no reference at all, and a forecast you cannot read
        # against anything cannot show a direction, which is the part a buyer acts on.
        #
        # And since 24 November 2026, where the market has a current price, the reference IS that
        # price - the figure and the day Current Market prints - because a reader holds the two
        # pages side by side and a median he can find nowhere else is not a reference to him.
        + (f'<div class="figure">'
           f'<div class="label">{esc(reference_label(content))}</div>'
           f'<div class="value">{esc(content.previous)}</div></div>'
           if content.previous_value is not None else "")
        + f'<div class="figure"><div class="label">{esc(content.month)}</div>'
          f'<div class="value">{esc(content.forecast)}</div></div>'
        + move_html(content.move)
        + "</div>"
        f'<div class="banner-bands">{body}</div>'
        + f'<div class="banner-trials">{esc(METHOD_NOTE)}</div>'
        + "</div>"
    )


# Which model a market's card shows.
#
# Stefan, 11 October 2026, after the rolling backtest was put against the published method: on TDI
# China a trend model was 4 to 8 per cent WORSE than carrying the last price forward, and called
# the direction right about a third of the time. The engine already knows this - it serves the
# controls beside the candidates, each with its own calibrated record, and its own registry says
# "losing to naive is the one thing that keeps a model out regardless of anything else about it".
#
# What was missing is that the application pinned one model for every market. Momentum earned its
# registration on 151 months of European TDI; the China screen showed it anyway. So the choice is
# made per market now, from the record in the payload.
#
# Three rules, and they are deliberately conservative:
#
#   - A candidate has to be tested at the lead horizon, and so does the control, or there is no
#     comparison to make.
#   - A candidate scored on fewer origins than the control is being marked on a different exam
#     (the engine's own comparison harness names this trap), so it does not qualify.
#   - A candidate must be strictly better than the control where both were tested.
#
# Where nothing has been tested the pinned model stands, because changing a market on no evidence
# is the same mistake in the other direction.
LEAD_HORIZON = 4
HIT_TOLERANCE = "10"


def _record(model: dict, weeks: int) -> dict | None:
    held = (model.get("calibration") or {}).get(str(weeks))
    if isinstance(held, dict):
        return held
    for point in model.get("points") or ():
        if int(point.get("horizon_weeks") or 0) == weeks:
            found = point.get("calibration")
            return found if isinstance(found, dict) else None
    return None


def _hits(record: dict | None) -> float | None:
    if not record or not record.get("tested"):
        return None
    share = (record.get("hits") or {}).get(HIT_TOLERANCE)
    return None if share is None else float(share)


def _origins(record: dict | None) -> int:
    return int((record or {}).get("origins") or 0)


def beats_the_control(candidate: dict, control: dict, horizons=(4, 8, 12)) -> bool:
    """Whether this candidate's own record puts it ahead of carrying the price forward."""
    lead = _hits(_record(candidate, LEAD_HORIZON)), _hits(_record(control, LEAD_HORIZON))
    if lead[0] is None or lead[1] is None:
        return False
    if _origins(_record(candidate, LEAD_HORIZON)) < _origins(_record(control, LEAD_HORIZON)):
        return False
    if lead[0] <= lead[1]:
        return False
    for weeks in horizons:
        mine, theirs = _hits(_record(candidate, weeks)), _hits(_record(control, weeks))
        if mine is None or theirs is None:
            continue
        if mine < theirs:
            return False
    return True


def card_model(payload: dict, *, pinned: str, chained: str = "",
               horizons=(4, 8, 12)) -> str:
    """The model this market's card shows, decided on this market's own record.

    **A method governance has made INACTIVE is never the answer** (masterplan H, v1.2.16). The page
    runs five models where a market has no chosen method and picks its own winner, and three of the
    five are controls - of which `naive_last` and `seasonal_naive` are INACTIVE in
    `forecast_method`. So where no candidate beat the control, the card published a number from a
    method the review had already retired, and nothing on the screen said so.

    The control is still COMPUTED and still the exam every candidate has to pass: a champion that
    has not been compared with "carry yesterday's price forward" has not been shown to be worth
    anything. It simply cannot be the number a reader is given. `may_be_published` is set per model
    by the engine from `forecast_method`, which is the one authority on what may run; a payload with
    no such mark is taken as permitting everything, so an older engine behaves exactly as it did.
    """
    models = [one for one in (payload or {}).get("models") or ()
              if one.get("status") == "ok"]
    allowed = [one for one in models if one.get("may_be_published", True)]
    if chained and any(one.get("model") == chained for one in allowed):
        return chained
    controls = [one for one in models if one.get("is_control")]
    candidates = [one for one in allowed if not one.get("is_control")]
    if not controls:
        return pinned
    # The exam is set by every control, retired or not - that is what a benchmark is for.
    best_control = max(controls, key=lambda one: (_hits(_record(one, LEAD_HORIZON)) or -1.0))
    if _hits(_record(best_control, LEAD_HORIZON)) is None:
        return pinned
    winners = [one for one in candidates
               if beats_the_control(one, best_control, horizons=horizons)]
    if not winners:
        # Nothing beat the control. Where the best control may itself be published it is the honest
        # answer; where governance has retired it, the card falls back to the pinned method rather
        # than publishing a retired one.
        if best_control.get("may_be_published", True):
            return str(best_control.get("model") or pinned)
        publishable_controls = [one for one in controls if one.get("may_be_published", True)]
        if publishable_controls:
            return str(max(publishable_controls,
                           key=lambda one: _hits(_record(one, LEAD_HORIZON)) or -1.0
                           ).get("model") or pinned)
        return pinned
    return str(max(winners,
                   key=lambda one: _hits(_record(one, LEAD_HORIZON)) or 0.0).get("model") or pinned)


def how_this_was_chosen(payload: dict, weeks: int) -> str:
    """Which of the two regimes a reader is looking at, in one line, or "" when there is no number.

    Masterplan H: *"For 40 markets the card shows the method chosen on evidence. For 49 it
    re-forecasts with five models and picks its own winner. The page does not say which you are
    reading."* It says now. No method is named either way - Stefan's rule of 23 October 2026 is that
    no sourcing behind a number reaches a customer - so this says how the choice was MADE, which is
    a fact about the application rather than about the market.
    """
    entry = ((payload or {}).get("governed") or {}).get("horizons") or {}
    status = str((entry.get(str(weeks)) or {}).get("status") or "")
    if status == "SUCCEEDED":
        return "Method chosen on this market's own measured record."
    if status in ("PENDING", "FAILED"):
        return ("The method chosen for this market has not produced an answer here yet, so the "
                "figure is the best of the challengers on this market's record.")
    return ("No method has been chosen for this market yet. The figure is the best of the "
            "challengers on this market's own record, and it is not a governed forecast.")
