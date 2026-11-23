"""Turn an identity's stored observations into a forecast, through the registry.

This is the only place that knows both the store and the model registry, and it is deliberately thin:
build the series at the finest frequency the data genuinely supports, hand it to every model the
licence gate allows, and return what each said. It chooses nothing. Which model a customer sees is a
release decision made on backtest evidence, not a preference expressed here.

Prices stay in the identity's own currency. An identity is single-currency by construction, so
forecasting in it avoids dragging FX into the model - and the display layer already converts at each
observation's own dated rate, which is the only conversion that has ever been correct here.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from statistics import median
from typing import Any, Iterable, Sequence

from app.forecasting import calibration as calibration_module
from app.forecasting.europe_chain import CHAINED_SOURCE
from app.forecasting.adapter import (
    DAILY, DEFAULT_HORIZONS, MONTHLY, WEEKLY, ForecastRequest, Purpose, Series,
)
from raw_material_forecasting.plausibility import refuse_value, usable_quantiles
from app.forecasting.registry import ModelRegistry, default_registry
from raw_material_forecasting.units import MT, display_unit


def _label_unit(identity_key: str, currency: str | None) -> str:
    """The label a Series carries: the currency over the unit the STORE holds for that series.

    The unit is the seventh field of the eight-field series key (`app.domain.series_key.FIELDS`),
    read off the string because that is what this function is handed. A key that is not the eight
    fields - a reference series, a test stub - falls back to MT rather than guessing.
    """
    parts = str(identity_key or "").split("|")
    held = display_unit(parts[6]) if len(parts) == 8 else MT
    return f"{currency}/{held}" if currency else held

# A series has to be genuinely at a frequency before it is treated as being at one. Monthly history
# bucketed into weeks passes a count test while sharing almost no weeks with anything - the same trap
# the correlation work hit, and the same rule fixes it here.
# There is no "model that reads the chained Europe history" any more, and that line is the whole of
# masterplan F. The chain is part of the series `store_series.forecast_rows` returns, so every model
# fits on it and this module has no special case. `CHAINED_MODEL = "weighted_error"` named the one
# method of eighteen the glue ever reached - and the one the review has never chosen for anything.

# A market is forecast while it is still being priced (Stefan, 23 September 2026). The European
# delivered purchases stop in January 2017; carrying three months forward from there would be a
# claim about 2017 recorded as if it were made today, and the vintage store would score it.
PRICED_FROM_YEAR = 2026

MIN_POINTS = {DAILY: 30, WEEKLY: 12, MONTHLY: 6}
MIN_DENSITY = {DAILY: 0.5, WEEKLY: 0.6, MONTHLY: 0.6}


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return None


def _period(day: date, frequency: str) -> date:
    if frequency == DAILY:
        return day
    if frequency == WEEKLY:
        return day - timedelta(days=day.weekday())
    return date(day.year, day.month, 1)


def _span(first: date, last: date, frequency: str) -> int:
    if frequency == DAILY:
        return (last - first).days + 1
    if frequency == WEEKLY:
        return (last.toordinal() - first.toordinal()) // 7 + 1
    return (last.year - first.year) * 12 + (last.month - first.month) + 1


def bucket(rows: Iterable[dict[str, Any]], frequency: str) -> dict[date, float]:
    """One value per period, the median of the prints inside it.

    The median rather than the mean because a single mistyped print should not move a period, and
    two publishers quoting the same market on the same day are one price with two witnesses.
    """
    buckets: dict[date, list[float]] = {}
    for row in rows:
        day = _as_date(row.get("observation_date"))
        price = row.get("native_price", row.get("price"))
        if day is None or price is None:
            continue
        buckets.setdefault(_period(day, frequency), []).append(float(price))
    return {day: float(median(values)) for day, values in sorted(buckets.items())}


def availability_by_period(rows: Iterable[dict[str, Any]], frequency: str) -> dict[date, date]:
    """When each period's value could first have been read, as opposed to what date it refers to.

    A customs month is published two or three months after the month it describes, so the two dates
    are not the same fact and only one of them belongs in a backtest. The period is known once the
    last print inside it has arrived - the LATEST availability, not the earliest, because the value
    is the consolidation of all of them.

    A row without availability contributes nothing; a period made only of such rows is absent here,
    and the caller treats absent as unknown rather than as available.
    """
    out: dict[date, date] = {}
    for row in rows:
        day = _as_date(row.get("observation_date"))
        moment = _as_date(row.get("available_from")) or _as_date(row.get("publication_date"))
        if day is None or moment is None:
            continue
        period = _period(day, frequency)
        if period not in out or moment > out[period]:
            out[period] = moment
    return out


def best_frequency(rows: Sequence[dict[str, Any]]) -> tuple[str, dict[date, float], dict[str, Any]]:
    """The finest frequency this series actually supports, with the reasoning kept.

    Finest rather than fixed, because the horizons are calendar weeks and the same three cards get
    sharper as collection matures. The report of what was rejected and why is returned with it, so a
    screen can say "monthly, because the weekly series is nine weeks short" instead of just showing
    a coarser answer.
    """
    considered: list[dict[str, Any]] = []
    for frequency in (DAILY, WEEKLY, MONTHLY):
        points = bucket(rows, frequency)
        if not points:
            continue
        days = sorted(points)
        span = _span(days[0], days[-1], frequency)
        density = len(points) / span if span else 0.0
        enough = len(points) >= MIN_POINTS[frequency]
        dense = density >= MIN_DENSITY[frequency]
        considered.append({
            "frequency": frequency, "points": len(points), "span": span,
            "density": round(density, 2), "usable": bool(enough and dense),
            "short_by": max(0, MIN_POINTS[frequency] - len(points)),
        })
        if enough and dense:
            return frequency, points, {"chosen": frequency, "considered": considered}
    return MONTHLY, bucket(rows, MONTHLY), {"chosen": None, "considered": considered}


def _calibration_fit(model, series: Series, weeks: int):
    """A callable the calibrator can roll: train on a prefix, return (forecast, low, high) offsets.

    The model is asked in exactly the way the page asks it, so what is measured is what a reader is
    shown - not an approximation of it standing in for the real thing.
    """
    def fit(train_points):
        if len(train_points) < 4:
            return None
        window = Series(series.key, series.chemical, series.frequency, tuple(train_points),
                        # The same offset the live forecast uses. Without it the band would be
                        # measured three steps out and printed around a forecast four steps out.
                        unit=series.unit, offset_days=series.offset_days)
        try:
            result = model.forecast(ForecastRequest(series=window, as_of=window.last_date,
                                                    horizons=(weeks,)))
        except Exception:
            return None                      # a window this model cannot fit is simply not an origin
        if not result.points:
            return None
        point = result.points[0]
        low = point.quantiles.get(0.1)
        high = point.quantiles.get(0.9)
        if low is None or high is None:
            return None
        return float(point.value), float(low) - float(point.value), float(high) - float(point.value)
    return fit


def _calibrated_points(model, series: Series, result, availability: dict[date, date] | None = None) -> tuple[list[dict], dict]:
    """The model's points with their bands corrected to what actually held, and the evidence why.

    The interval was a claim nobody had checked. On TDI China it claimed 80% and delivered 56%, 60%
    and 50% at one, two and three months - so the page was promising a range that held about half
    the time, and the gap got worse with distance. Each horizon is now rolled through its own
    backtest and the band widened to the narrowest width that would have covered.
    """
    points: list[dict] = []
    calibrations: dict[str, dict] = {}
    widest_low = widest_high = 0.0          # running maxima: see the monotonicity note below
    for point in sorted(result.points, key=lambda item: item.steps):
        calibration = calibration_module.calibrate(
            list(series.points), point.steps, _calibration_fit(model, series, point.horizon_weeks),
            actual_of=lambda item: item[1],
            # Without this the roll trains on months that had not been published at the origin it
            # is pretending to stand on, and the band it cuts is too narrow for the error a
            # forecaster would really have made.
            available_of=(lambda item: availability.get(item[0])) if availability else None,
        )
        calibrations[str(point.horizon_weeks)] = calibration.as_dict()
        quantiles = {q: v for q, v in point.quantiles.items()}
        # The band is the errors this model actually made at this horizon, cut at the percentiles
        # that leave 80% in the middle. It used to be the model's band around its own fit, scaled
        # up on a grid until its backtested coverage reached the target - two approximations in
        # series, which on TDI China printed -24%/+26% where the errors themselves say -14%/+14%.
        #
        # The scale correction stays for a model with too few origins to measure a band from.
        measured = next((band for coverage, *band in calibration.bands if coverage == 0.80), None)
        if calibration.tested and measured is not None:
            low_share, high_share = measured
            quantiles[0.1] = point.value * (1.0 + low_share)
            quantiles[0.9] = point.value * (1.0 + high_share)
        elif calibration.tested and calibration.scale != 1.0:
            quantiles = calibration_module.apply(quantiles, point.value, calibration.scale)

        # Uncertainty cannot fall with distance. Each horizon is calibrated on its own backtest and
        # those land on different scales - on TDI China 2.5, 1.75 and 2.0 - which made the two-month
        # band NARROWER than the one-month band. Nothing about the market says two months out is
        # better known than one; it is fourteen origins disagreeing with sixteen. So a band never
        # shrinks: each horizon carries at least the width of the one before it.
        low, high = quantiles.get(0.1), quantiles.get(0.9)
        if low is not None and high is not None:
            widest_low = max(widest_low, point.value - float(low))
            widest_high = max(widest_high, float(high) - point.value)
            # A price cannot be negative. A band that reaches below zero is arithmetic outrunning
            # the thing it describes, and printing it would discredit every number beside it.
            quantiles[0.1] = max(0.0, point.value - widest_low)
            quantiles[0.9] = point.value + widest_high

        points.append({
            "horizon_weeks": point.horizon_weeks,
            "value": round(point.value, 2),
            "period": point.period.isoformat(),
            "steps": point.steps,
            # Periods ahead of the last price, which is what the card is headed with.
            "ahead": point.steps_ahead or point.steps,
            "resolution": point.resolution,
            "exact": point.exact,
            "note": point.resolution_note,
            "quantiles": {str(q): round(v, 2) for q, v in quantiles.items()},
            "calibration": calibrations[str(point.horizon_weeks)],
        })
    points.sort(key=lambda item: item["horizon_weeks"])
    return points, calibrations


def _latest_print(rows: Sequence[dict[str, Any]],
                  on_or_before: date | None = None) -> dict[str, Any] | None:
    """The newest single observation, with its date and how many publishers agreed that day.

    The count matters: one publisher moving is a publisher moving, and four moving together is the
    market. On 11 September 2026 four independent publishers printed the jump on the same day.

    `on_or_before` is not optional in spirit. A backtest asks for a forecast as of a date in the
    past, and handing it the newest price in the file would date the claim after its own as-of and
    quietly feed the reader a number the model never saw. The first version of this did exactly that
    and a test written months earlier caught it.
    """
    dated = [(day, float(row.get("native_price", row.get("price")) or 0), row.get("source_id") or "")
             for row in rows
             if (day := _as_date(row.get("observation_date"))) is not None
             and row.get("native_price", row.get("price")) is not None
             and (on_or_before is None or day <= on_or_before)]
    if not dated:
        return None
    last = max(day for day, _value, _source in dated)
    same_day = [(value, source) for day, value, source in dated if day == last]
    return {
        "date": last.isoformat(),
        "value": round(median([value for value, _source in same_day]), 2),
        "publishers": len({source for _value, source in same_day if source}),
    }


def forecast_identity(rows: Sequence[dict[str, Any]], *, identity_key: str, chemical: str,
                      currency: str = "", horizons: tuple[int, ...] = DEFAULT_HORIZONS,
                      registry: ModelRegistry | None = None,
                      purpose: Purpose = Purpose.COMMERCIAL,
                      as_of: date | None = None,
                      history_points: int = 60) -> dict[str, Any]:
    """Every eligible model's answer for one identity. Chooses no champion.

    The Europe chain used to arrive here as a `chained=` argument and go to one model. It is in
    `rows` now, because `store_series.forecast_rows` glues it (masterplan F), so there is nothing
    special to do with it and every method is scored on the same history.
    """
    registry = registry or default_registry()
    frequency, history, resolution = best_frequency(rows)
    # When each period became readable, for the rolling backtest behind the bands. Empty when the
    # rows carry no availability, and then the roll behaves exactly as it did before.
    availability = availability_by_period(rows, frequency)
    # NOT `points`. It used to be, and the model loop below rebinds `points` to a model's forecast
    # points - so `"observations": len(points)` at the end reported how many horizons the LAST model
    # answered, not how much history it read. On 14 September 2026 the customer screen said "Based on
    # 1 periods of consolidated price history" because the last model in the registry declined two of
    # its three horizons. Twenty-eight months of history, reported as one.
    points = history
    if not points:
        return {"identity_key": identity_key, "status": "no_history", "resolution": resolution,
                "horizons": list(horizons), "models": []}

    # How far the last price sits past the last bucket. A market collected daily and priced on the
    # 30th is 29 days past a bucket keyed to the 1st, so three monthly steps from the bucket land
    # two months and one day after the price. Stefan, 30 September 2026, looking at TDI China: the
    # three months have to be three months.
    #
    # On a weekly or daily series the models add this to the distance they cover. On a monthly one
    # they do not, since 23 November 2026: the months forecast are the next three, and the page
    # draws each on the price's own day of its month, which is what makes three months three
    # months without skipping the month a buyer is about to buy in. See `adapter.steps_for`.
    newest = max((_as_date(row.get("observation_date")) for row in rows
                  if _as_date(row.get("observation_date")) is not None
                  and (as_of is None or _as_date(row.get("observation_date")) <= as_of)),
                 default=None)
    last_bucket = max(history) if history else None
    offset_days = ((newest - last_bucket).days
                   if newest is not None and last_bucket is not None and newest > last_bucket else 0)
    series = Series(identity_key, chemical, frequency, tuple(history.items()),
                    # The unit the store holds, read off the identity key's seventh field,
                    # rather than a literal. The old comment here said the store's token was left
                    # alone because rewriting it would split every series in two; measured, no two
                    # identities differ only by unit, and migration 0057 moved them all to MT.
                    unit=_label_unit(identity_key, currency),
                    offset_days=offset_days)
    # The model runs from the last BUCKET; the claim is dated by the last PRINT.
    #
    # A monthly bucket is keyed to the first of its month, so `series.last_date` is 1 September even
    # when the newest price in it was struck on the 13th. Reporting that as the as-of put "Forecast
    # starts - 1 Sep 2026" on a screen refreshed on the 14th, which reads as a forecast made a
    # fortnight ago and is the opposite of what happened: it was made today, on data through the
    # 13th. The bucket is the model's business. The date on the claim is the reader's.
    cutoff = as_of or series.last_date
    request = ForecastRequest(series=series.up_to(cutoff), as_of=cutoff, horizons=horizons)
    # Capped at the caller's as-of, NOT at the bucket. The bucket is keyed to the first of its month
    # and holds every print in that month, so capping at the key would hide the prints the model
    # actually read - the September problem in reverse. A caller that names no as-of read everything.
    latest = _latest_print(rows, as_of)
    # A caller that NAMES its as-of owns the date - that is a backtest asking what could have been
    # said on a particular day, and answering with the date of the nearest print instead would move
    # the goalposts of its own question. Only the live path, which passes no as-of, is dated by the
    # newest price it read.
    priced_to = as_of or _as_date((latest or {}).get("date")) or cutoff

    # The last value the models themselves saw. Computed here rather than in the payload below
    # because the plausibility guard judges every forecast against it, and a guard reading a second,
    # separately derived anchor could disagree with the one printed beside the number it refused.
    anchored = series.up_to(cutoff).points
    anchor_value = round(anchored[-1][1], 2) if anchored else None

    models: list[dict[str, Any]] = []
    # Only on the live path. A backtest names its own as-of and is asking what could have been said
    # on a day in the past, which is exactly the question this rule must not answer with silence.
    stale = as_of is None and cutoff is not None and cutoff.year < PRICED_FROM_YEAR
    for spec in ([] if stale else registry.candidates(purpose=purpose)):
        try:
            model = registry.create(spec.key, purpose=purpose)
            result = model.forecast(request)
        except Exception as exc:                  # one model must never take the page down
            models.append({"model": spec.key, "name": spec.name, "status": "failed",
                           "error": str(exc)})
            continue
        if not result.points:
            models.append({"model": spec.key, "name": spec.name, "status": "declined",
                           "reason": result.metadata.get("reason", ""),
                           "is_control": spec.is_control})
            continue
        try:
            points, calibrations = _calibrated_points(
                model, series.up_to(cutoff), result, availability=availability)
        except Exception as exc:      # calibration must never take a forecast off the page
            points = [
                {
                    "horizon_weeks": point.horizon_weeks,
                    "value": round(point.value, 2),
                    "period": point.period.isoformat(),
                    "steps": point.steps,
                    "resolution": point.resolution,
                    "exact": point.exact,
                    "note": point.resolution_note,
                    "quantiles": {str(q): round(v, 2) for q, v in point.quantiles.items()},
                    "calibration": {"tested": False, "error": str(exc)[:120]},
                }
                for point in result.points
            ]
            calibrations = {}
        # Nothing impossible leaves this function. Every card in the application and the vintage
        # recorder both publish a model only when its status is "ok", so a refusal here keeps the
        # number off the screen AND out of the store in one move - and carries the reason, which a
        # silent drop would not. See `app/forecasting/plausibility.py` for what the band is measured
        # from and why the centre and the interval are judged separately.
        refusals = [reason for point in points
                    if (reason := refuse_value(point["value"], anchor_value,
                                               point["horizon_weeks"]))]
        if refusals:
            models.append({"model": spec.key, "name": spec.name, "status": "implausible",
                           "reason": refusals[0], "refused": len(refusals),
                           "is_control": spec.is_control})
            continue
        for point in points:
            kept, withheld = usable_quantiles(point.get("quantiles"), anchor_value,
                                              point["horizon_weeks"])
            if withheld:
                point["quantiles"] = kept
                point["quantiles_withheld"] = withheld
        models.append({
            "model": spec.key,
            # What a reader is shown. The key stays an identifier; it is in the vintage store's
            # primary key and in the application's pinned card model, so it cannot be prettified.
            "name": spec.name,
            "explanation": spec.explanation,
            "family": spec.family,
            "version": spec.version,
            "is_control": spec.is_control,
            "status": "ok",
            "metadata": dict(result.metadata),
            "calibration": calibrations,
            "points": points,
        })
    return {
        "identity_key": identity_key,
        "chemical": chemical,
        "currency": currency,
        # The unit the series is in. The card used to print "/ MT" as a literal and never read
        # anything, so nothing on any screen could ever have shown that the store disagreed with
        # it - which it did, holding "t", until migration 0057 (masterplan A, item 4).
        "unit": series.unit,
        # What the claim is dated by: the newest price it read.
        "as_of": priced_to.isoformat() if priced_to else None,
        # The period the model actually forecast from, which is the bucket. Kept because the two are
        # different facts and a screen that conflates them is what caused this.
        "period_start": cutoff.isoformat() if cutoff else None,
        # The last value the models themselves saw. A card comparing its forecast against a headline
        # reference struck on a different day would report a difference of days as a market move.
        "anchor_value": anchor_value,
        # How many months the Europe method read: the published series and the card's changes.
        # How many of the history's months came from the published series rather than from this
        # market's own readings (masterplan F). Reported for the same reason it always was - a
        # reader of the payload can see how much of the history is chained - but it is counted off
        # the rows now, which is where the chain lives.
        "chained_months": sum(1 for row in rows
                              if str(row.get("source_id") or "") == CHAINED_SOURCE),
        # The series the models actually saw, so a chart drawn from this cannot disagree with the
        # numbers on the cards beside it.
        "history": [[day.isoformat(), round(value, 2)]
                    for day, value in list(series.up_to(cutoff).points)[-history_points:]],
        # Every governed reading, one per date, for the chart.
        #
        # `history` is the model's input: monthly buckets, each the median of its month. That is the
        # right series to fit on and the wrong one to draw. In September 2026 China TDI was read on
        # seventeen days and rose 6.6% across them, 14,688 to 15,664 - and the bucket put a single
        # dot at 14,926, the median of a month that was not over. A chart drawn from it showed the
        # market 4.7% below where it was and hid every reading behind it.
        #
        # The model still fits on buckets. The chart draws what we hold.
        "readings": [[day.isoformat(), round(value, 2)]
                     for day, value in sorted(bucket(rows, DAILY).items())
                     if as_of is None or day <= as_of],
        # The most recent PRINT, not the most recent bucket.
        #
        # A monthly series answers in months, so the cards anchor on the month's median - which is
        # the right number to forecast from and the wrong one to show a buyer on its own. On 13
        # September 2026 the September bucket was 16,850 and the market had printed 17,367 two days
        # earlier: the screen was 3.1% behind a market that had just moved 3.6% in two days, and it
        # had no way to say so. Both numbers now travel to the card.
        "latest": latest,
        "observations": len(history),
        "status": ("not_priced" if stale
                   else "ok" if any(m["status"] == "ok" for m in models) else "no_model"),
        # The last year this market was priced in, against the year a forecast starts from.
        "priced_from_year": PRICED_FROM_YEAR,
        "resolution": resolution,
        "horizons": list(horizons),
        # No champion is named. Until a backtest has qualified one, the only honest label on a screen
        # is that these are benchmark controls, and the release gate says so rather than this.
        "released": False,
        "release_reason": "No model has been qualified by a rolling-origin backtest yet.",
        "models": models,
    }
