"""Production forecasts from the governed selection (Adaptive Forecast Method Selection, WP3, Task 7).

Acting-architect decision D5 (20 Oct 2026): foundation models stay off the interactive page path, so
the forecast of the method IN FORCE for a series and horizon is computed here, by a job that runs after
data collection in the review environment, and stored. The page reads what is stored.

For each series-horizon:

1. **What is in force.** An active administrator override, otherwise the active automatic selection
   (plan, Task 7: "Selection order: active manual override, otherwise active automatic selection").
   Neither: the pair is NOT GOVERNED and nothing is written - today's model list and pinned card stay
   exactly as they are.
2. **Run exactly that method**, on the history as it stood at the issue date (`history.build_history`,
   the same cutoff discipline as the backtest) and with the supporting series built by the SAME
   function the tournament uses at an origin (`tournament.supporting_at`). The method is examined and
   run on one recipe.
3. **SUCCEEDED** writes one immutable forecast vintage, state RELEASED, carrying the method id and
   version, the selection source, the review it rests on, the cutoff, and the conditional inputs used.
   **FAILED** (raised, declined, unavailable, or answered a different period) writes a FAILED run with
   the reason and the current automatic recommendation (Review Focus 3). No other method's number is
   ever written under the failed method's name, and nothing switches automatically.

Every run is a `governed_forecast_run` row, keyed so a repeat on the same day for the same method and
source is the same run, never a second one: append-only, like the vintages.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence

from sqlalchemy import select

from app.db.models import ForecastVintageModel, GovernedForecastRunModel, MarketIdentityModel
from app.domain.enums import ForecastMethodStatus, SelectionSource
from raw_material_forecasting.plausibility import refuse_value, usable_quantiles
from app.forecasting.adapter import MONTHLY, PERIOD_DAYS, Series, period_after, steps_for
from app.forecasting.adapters.base import ForecastContext, ForecastModelAdapter, ForecastOutput
from app.forecasting.adapters.catalogue import governed_adapters
from app.forecasting.domain import CONDITIONAL, ForecastMethod
from app.forecasting.governance import (
    ForecastMethodRegistry, ForecastOverrideRepository, ForecastSelectionRepository,
)
from app.forecasting.history import build_history
from app.forecasting.review_job import feature_rows as load_feature_rows, identity_key_of
from app.forecasting.store_series import forecast_rows
from app.forecasting.tournament import supporting_at
from app.forecasting.vintages import RELEASED, vintage_key
from raw_material_forecasting.units import display_unit

log = logging.getLogger(__name__)

SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"
NOT_GOVERNED = "NOT_GOVERNED"
# A stored governed forecast older than this (issue date) is shown as not current (WP3 gate condition 4).
STALE_AFTER_DAYS = 3


@dataclass(frozen=True)
class InForce:
    """The method in force for one series and horizon, and who put it there.

    `method` is None only when the chosen method's registry row is missing - which is reported as a
    failure of THAT choice, never by falling through to another (v0.5.63, WP3 gate)."""
    method: ForecastMethod | None
    source: SelectionSource
    review_id: str | None
    reason: str = ""
    method_id: str = ""

    def refusal(self) -> str:
        """Why the method in force may not produce a governed forecast now, or "" (WP3 gate condition 2).

        Only a Production Eligible, selectable method may be in force. A method demoted or retired after
        it was selected fails visibly until a review or an administrator puts another in force."""
        if self.method is None:
            return f"the method in force ({self.method_id}) is not in the governed registry"
        if self.method.status is not ForecastMethodStatus.PRODUCTION_ELIGIBLE or not self.method.selectable:
            return (f"{self.method.method_key} is {self.method.status.value}, no longer Production Eligible; "
                    f"it may not produce a governed forecast")
        return ""


def in_force(session, identity_id: str, horizon_weeks: int) -> InForce | None:
    """Override first, otherwise the automatic selection, otherwise nothing (plan, Task 7)."""
    registry = ForecastMethodRegistry(session)
    override = ForecastOverrideRepository(session).get_active(identity_id, horizon_weeks)
    if override is not None:
        return InForce(registry.get_by_id(override.method_id), SelectionSource.MANUAL_OVERRIDE, None,
                       override.reason, override.method_id)
    selection = ForecastSelectionRepository(session).get_active(identity_id, horizon_weeks)
    if selection is not None:
        return InForce(registry.get_by_id(selection.method_id), SelectionSource.AUTOMATIC, selection.review_id,
                       selection.reason, selection.method_id)
    return None


def examined_frequency(session, chosen: InForce, identity_id: str, horizon_weeks: int) -> str | None:
    """The frequency the method was examined at in the review its selection rests on (WP3 gate
    condition 5): production runs it at that frequency, not one re-chosen from today's data."""
    if not chosen.review_id or chosen.method is None:
        return None
    from app.db.models import ModelFitResultModel
    row = session.scalar(select(ModelFitResultModel).where(
        ModelFitResultModel.review_id == chosen.review_id, ModelFitResultModel.identity_id == identity_id,
        ModelFitResultModel.horizon_weeks == int(horizon_weeks), ModelFitResultModel.method_id == chosen.method.id))
    return (row.details or {}).get("frequency") if row is not None else None


def automatic_recommendation(session, identity_id: str, horizon_weeks: int) -> str | None:
    """The method the automatic selection would use here, whatever the override says."""
    selection = ForecastSelectionRepository(session).get_active(identity_id, horizon_weeks)
    if selection is None:
        return None
    method = ForecastMethodRegistry(session).get_by_id(selection.method_id)
    return method.method_key if method is not None else None


def identity_by_key(session, identity_key: str) -> MarketIdentityModel | None:
    """The identity a key names. Narrowed in the store by family and geography first (the page asks
    this on every forecast request), then matched on the whole key."""
    parts = identity_key.split("|")
    query = select(MarketIdentityModel)
    if len(parts) == 8:
        family, _grade, geography = parts[0], parts[1], parts[2]
        if family != "UNKNOWN":
            query = query.where(MarketIdentityModel.product_family == family)
        if geography != "UNKNOWN":
            query = query.where(MarketIdentityModel.market_geography == geography)
    return next((row for row in session.scalars(query) if identity_key_of(row) == identity_key), None)


def _as_datetime(day: date | datetime) -> datetime:
    if isinstance(day, datetime):
        return day if day.tzinfo else day.replace(tzinfo=timezone.utc)
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


def run_key(identity_key: str, horizon_weeks: int, as_of: date, method_key: str, source: SelectionSource,
            issue_date: date | None = None, lead: int | None = None) -> str:
    """One run per pair, data date, ISSUE date, method, source and LEAD (v0.5.63, WP3 gate condition
    1: keyed by the data date alone, one FAILED run blocked every retry until new prices arrived).

    The lead joined the key on 22 October 2026, when the slot stopped being the distance asked for.
    Without it, a run at a new lead collided with the morning's run at the old one and was dropped
    as a duplicate: 107 forecasts were computed and none were stored, and the page kept showing
    September. A different distance is a different claim, so it is part of what identifies the run.
    Omitted from the key when it equals the slot, so every key written before that day is unchanged.
    """
    issued = f"|issued {issue_date.isoformat()}" if issue_date is not None else ""
    led = f"|lead {int(lead)}w" if lead is not None and int(lead) != int(horizon_weeks) else ""
    return (f"{identity_key}|{int(horizon_weeks)}w|{as_of.isoformat()}{issued}"
            f"{led}|{method_key}|{source.value}")


@dataclass
class Prepared:
    """What a method is asked with, at the issue date. Built once per series, reused per horizon."""
    identity: MarketIdentityModel
    identity_key: str
    chemical: str
    history: Series
    priced_to: date
    issue_date: date


def prepare(identity: MarketIdentityModel, rows: Sequence[dict[str, Any]], issue_date: date, *,
            frequency: str | None = None) -> Prepared | None:
    """The history a method is asked with at the issue date: at the frequency it was examined at,
    everything readable on that day, ending on the newest price.

    UNTIL v1.2.116 THIS STOOD ON THE NEWEST *COMPLETE* PERIOD (WP3 gate condition 5). A period still
    being printed was left out, "exactly as a backtest origin is only asked once its period has
    ended", so that a half-month median or a late publisher's part-published week would not anchor
    the live number. The reasoning was about the MEDIAN of an unfinished period and it was right
    about that. Its cost was that a forecast issued on 23 November stood on October and reached one
    month further: Polyether Polyol China was forecast from 10,752 yuan while the market printed
    9,436.

    Measured on 38 weekly series, 2011-2023 (`scripts/latest_price_vs_month`), that starting point
    was off by 8.3-8.7% one month ahead against 5.0-5.3% from the newest print - closer in three
    market-months out of four, in every third of the month. Stefan, 24 November 2026: "Yes, build
    it."

    So the unfinished period is in, at its newest price and not at its median: `build_history`
    ends every history that way, for the tournament's origins as for this, which keeps the
    condition's own point - the method is asked the way it was examined. What a row may be seen on
    which day is still `visible_rows`' business and has not moved.
    """
    key = identity_key_of(identity)
    chemical = str(identity.product_family or "UNKNOWN")
    # The unit the STORE holds for this identity, not a literal. They agree since migration
    # 0057 made the stored token MT; before it the store said "t" and every label here said MT.
    held = display_unit(identity.unit)
    unit = f"{identity.currency}/{held}" if identity.currency else held
    built = build_history(rows, issue_date, identity_key=key, chemical=chemical, frequency=frequency, unit=unit)
    if not built.series.points:
        return None
    last = built.series.last_date
    from datetime import timedelta
    priced_to = last + timedelta(days=max(0, built.series.offset_days))
    return Prepared(identity, key, chemical, built.series, min(priced_to, issue_date), issue_date)


def periods_behind(anchor: date, issue_date: date, frequency: str) -> int:
    """How many of the series' own periods the newest data sits behind the day we are issuing on.

    Calendar months for a monthly series, not days divided by 30.44: anchored on 1 August and
    issuing on 22 October, the data is TWO months behind, and the day arithmetic rounds 2.7 up to
    three and sends every forecast a month too far out.
    """
    if frequency == MONTHLY:
        return max(0, (issue_date.year - anchor.year) * 12 + (issue_date.month - anchor.month))
    span = PERIOD_DAYS[frequency]
    return max(0, int((issue_date.toordinal() - anchor.toordinal()) // span))


def lead_weeks(slot_weeks: int, anchor: date, issue_date: date, frequency: str) -> int:
    """The horizon to ASK for, so a slot lands that many periods past TODAY rather than past the data.

    Stefan, 22 October 2026: "A forecast is for a period yet to come."

    The horizons were counted from the newest data month, and the data runs two to three months
    behind because Comext publishes at about two months and Comtrade at about three. Anchored on
    August and read on 22 October, the "1 month" slot answered September - a month already over -
    "2 months" answered October, the month in progress, and only "3 months" was ahead at all. Across
    the governed markets it was 1 of 39 with all three horizons in the future and 16 of 39 with
    none.

    So the SLOT keeps its name - 4, 8 and 12 weeks are what the selections and the stored runs are
    keyed to, and one, two and three months is what a reader is promised - while the LEAD asked of
    the method is the slot plus however far behind the data is. August data read in October answers
    November, December and January.

    The cost is real and is not hidden: a method chosen on its four-week score is being asked for
    thirteen, and its error at that distance is wider. That widening is what the band is for.
    """
    # The periods behind are ADDED to the slot rather than the whole lead being recomputed from
    # steps. Weeks to steps and back is not an identity - 8 weeks is 2 months, and 2 months back
    # into weeks is 9 - so recomputing moved every horizon by a week even when nothing was behind.
    behind = periods_behind(anchor, issue_date, frequency)
    if behind <= 0:
        return slot_weeks
    return slot_weeks + int(round(behind * PERIOD_DAYS[frequency] / 7.0))


def ask(adapter: ForecastModelAdapter, prepared: Prepared, horizon_weeks: int, *,
        related_rows: Mapping[str, Sequence[dict]] | None = None,
        feature_rows: Mapping[str, Sequence[dict]] | None = None) -> tuple[ForecastOutput, dict[str, Any]]:
    """Run one method for one horizon at the issue date, with the tournament's supporting-series recipe."""
    series = prepared.history
    related, features, stopped = supporting_at(
        series, prepared.issue_date, series.frequency,
        related_rows={k: v for k, v in (related_rows or {}).items() if k != prepared.identity_key},
        feature_rows=feature_rows)
    context = ForecastContext(prepared.identity_key, prepared.chemical, prepared.issue_date, series,
                              (horizon_weeks,), tuple(r.series for r in related), tuple(features))
    inputs = {"related": [r.as_dict() for r in related], "features": [f.key for f in features],
              "stopped": stopped, "history_last_period": series.last_date.isoformat(),
              "history_periods": len(series.points), "frequency": series.frequency}
    return adapter.forecast(context), inputs


class ProductionForecastService:
    """`generate(identity_key, horizon, issue_date)` - the governed forecast for one pair (plan, Task 7)."""

    def __init__(self, session_factory, *, adapters: Mapping[str, ForecastModelAdapter] | None = None) -> None:
        self.session_factory = session_factory
        self.adapters = dict(adapters) if adapters is not None else governed_adapters()

    def generate(self, identity_key: str, horizon_weeks: int, issue_date: date | None = None, *,
                 rows: Sequence[dict] | None = None, related_rows: Mapping[str, Sequence[dict]] | None = None,
                 feature_rows: Mapping[str, Sequence[dict]] | None = None) -> dict[str, Any]:
        issue_date = issue_date or datetime.now(timezone.utc).date()
        with self.session_factory() as session:
            identity = identity_by_key(session, identity_key)
            if identity is None:
                return {"identity_key": identity_key, "horizon_weeks": horizon_weeks, "status": FAILED,
                        "reason": "identity not found", "written": False}
            chosen = in_force(session, identity.id, horizon_weeks)
            if chosen is None:
                return {"identity_key": identity_key, "horizon_weeks": horizon_weeks, "status": NOT_GOVERNED,
                        "reason": "no override and no automatic selection in force", "written": False}
            recommendation = automatic_recommendation(session, identity.id, horizon_weeks)
            frequency = examined_frequency(session, chosen, identity.id, horizon_weeks)
        if chosen.method is None:
            log.warning("governed forecast for %s %sw: %s", identity_key, horizon_weeks, chosen.refusal())
            return {"identity_key": identity_key, "horizon_weeks": horizon_weeks, "status": FAILED,
                    "reason": chosen.refusal(), "written": False, "recommended_method_key": recommendation}
        refusal = chosen.refusal()
        if refusal:
            return self._record_failure(identity, identity_key, horizon_weeks, issue_date, chosen, refusal,
                                        recommendation, {}, issue_date=issue_date)
        if rows is None:
            rows = forecast_rows(self.session_factory, identity.id, all_versions=True)
        if chosen.method.kind == CONDITIONAL and related_rows is None:
            related_rows = all_rows(self.session_factory)
            feature_rows = load_feature_rows(self.session_factory, str(identity.product_family or "UNKNOWN"),
                                             until=issue_date)
        prepared = prepare(identity, rows, issue_date, frequency=frequency)
        method = chosen.method
        if prepared is None:
            return self._record_failure(identity, identity_key, horizon_weeks, issue_date, chosen,
                                        "no visible history at the issue date", recommendation, {},
                                        issue_date=issue_date)
        adapter = self.adapters.get(method.method_key)
        if adapter is None:
            return self._record_failure(identity, identity_key, horizon_weeks, prepared.priced_to, chosen,
                                        f"no runnable adapter for {method.method_key}", recommendation, {},
                                        issue_date=issue_date)
        # The slot is what the selection and the stored run are keyed to; the lead is what the
        # method is actually asked for, so the answer lands past today rather than past the data.
        lead = lead_weeks(horizon_weeks, prepared.history.last_date, issue_date,
                          prepared.history.frequency)
        output, inputs = ask(adapter, prepared, lead, related_rows=related_rows,
                             feature_rows=feature_rows if method.kind == CONDITIONAL else None)
        inputs = {**inputs, "slot_weeks": horizon_weeks, "lead_weeks": lead,
                  "periods_behind": periods_behind(prepared.history.last_date, issue_date,
                                                   prepared.history.frequency)}
        point = output.at(lead) if output.succeeded else None
        expected = period_after(prepared.history.last_date,
                                steps_for(lead, prepared.history.frequency,
                                          prepared.history.offset_days)[0], prepared.history.frequency)
        if point is None:
            why = output.reason or f"{output.status.value}: no answer for {lead} weeks"
            return self._record_failure(identity, identity_key, horizon_weeks, prepared.priced_to, chosen,
                                        f"{output.status.value}: {why}", recommendation, inputs,
                                        version=output.method_version, issue_date=issue_date)
        if point.period != expected:
            return self._record_failure(identity, identity_key, horizon_weeks, prepared.priced_to, chosen,
                                        f"answered period {point.period.isoformat()}; expected {expected.isoformat()}",
                                        recommendation, inputs, version=output.method_version, issue_date=issue_date)
        # The third of these guards, and the one that was missing. This is the ONLY path that writes
        # a RELEASED vintage, which is the lane the pinned card reads, and all fifteen of the
        # released forecasts carrying a negative band came through here (measured, 18 November
        # 2026). The guard in `forecast_identity` covers the page's own computed cards and the
        # candidate lane; it never saw this one.
        anchor = prepared.history.points[-1][1] if prepared.history.points else None
        refusal = refuse_value(point.value, anchor, lead)
        if refusal:
            return self._record_failure(identity, identity_key, horizon_weeks, prepared.priced_to, chosen,
                                        f"implausible: {refusal}", recommendation, inputs,
                                        version=output.method_version, issue_date=issue_date)
        return self._record_success(identity, identity_key, horizon_weeks, prepared, chosen, output, point, inputs)

    # ---------------------------------------------------------------- writing (append-only)

    def _existing(self, session, key: str) -> GovernedForecastRunModel | None:
        return session.scalar(select(GovernedForecastRunModel).where(GovernedForecastRunModel.run_key == key))

    def _held(self, session, key: str, version: str) -> GovernedForecastRunModel | None:
        """The successful run already stored under this key by the method AS IT NOW IS, if there is one.

        Among every attempt under the key, not only the first: a run by an earlier version of the
        method holds the first place and the corrected one is filed beside it (v1.2.120).
        """
        attempt, candidate = 1, key
        while (run := self._existing(session, candidate)) is not None:
            if run.status == SUCCEEDED and (run.method_version or "") == (version or ""):
                return run
            attempt += 1
            candidate = f"{key}#attempt {attempt}"
        return None

    def _record_success(self, identity, identity_key, horizon_weeks, prepared: Prepared, chosen: InForce,
                        output: ForecastOutput, point, inputs) -> dict[str, Any]:
        as_of = prepared.priced_to
        # The lead belongs in BOTH keys. It is the distance actually asked for, and on 22 October
        # the slot stopped being that distance. Without it here the morning's run at the old lead
        # owned the key, the new run was dropped as a duplicate, and - because the vintage key
        # collided too - the stored value was reused rather than replaced. 107 forecasts computed,
        # none stored, the page unchanged.
        lead = (inputs or {}).get("lead_weeks")
        key = run_key(identity_key, horizon_weeks, as_of, chosen.method.method_key, chosen.source,
                      prepared.issue_date, lead)
        with self.session_factory() as session:
            held = self._held(session, key, output.method_version)
            if held is not None:
                return _run_dict(held) | {"written": False, "already_present": True}
            key = self._free_key(session, key)
            led = f"|lead {int(lead)}w" if lead is not None and int(lead) != int(horizon_weeks) else ""
            vkey = (f"{vintage_key(identity_key, chosen.method.method_key, horizon_weeks, as_of)}"
                    f"{led}|governed|{chosen.source.value}")
            # An unusable band does NOT refuse the forecast. Only one method is in force per slot,
            # so refusing here would leave the pinned card blank rather than showing a sound point
            # estimate without an interval - the same judgement `plausibility` makes for the page,
            # and for the same reason.
            anchor = prepared.history.points[-1][1] if prepared.history.points else None
            band, withheld = usable_quantiles(
                {str(q): round(float(v), 4) for q, v in point.quantiles.items()}, anchor,
                int((inputs or {}).get("lead_weeks") or horizon_weeks))
            if withheld:
                log.warning("governed forecast band WITHHELD for %s %sw (%s): %s", identity_key,
                            horizon_weeks, chosen.method.method_key, withheld)
            vintage = session.scalar(select(ForecastVintageModel).where(ForecastVintageModel.vintage_key == vkey))
            # A stored forecast for a DIFFERENT period is a different claim, not this one again.
            #
            # The vintage key is the market, the method, the slot, the data date and the lead. All
            # five can stay the same while the month the forecast is FOR moves - which is what
            # happens when the rule that turns a lead into periods changes. On 23 November 2026
            # that rule changed twice (three horizons had been landing on two months; then "start
            # with next month"), and the governed forecast for Polyether Polyol China, anchored on
            # October, went on reading January, February, March: each night's run found the
            # vintage written under the old rule, took it for its own, and attached a new run to
            # it. It would have gone on doing so until November closed and the data date moved.
            # The page, the cards and the chart all read that vintage.
            #
            # So where the stored period is not the period just answered, the new answer is filed
            # under a key that says which period it is for. Every key written before this is
            # untouched, and the old claim stays in the record to be settled like any other.
            if vintage is not None and vintage.period is not None:
                stored = vintage.period if vintage.period.tzinfo else vintage.period.replace(tzinfo=timezone.utc)
                if stored != _as_datetime(point.period):
                    vkey = f"{vkey}|for {point.period.isoformat()}"
                    vintage = session.scalar(
                        select(ForecastVintageModel).where(ForecastVintageModel.vintage_key == vkey))
            # A method that changed is a different claim too (v1.2.120).
            #
            # 24 November 2026: "Last month's move, continued" stopped forecasting reversals and
            # became version 1.1. PO India CIF stood at +29%, +34% and +39% on one, and its data
            # date is 1 August: every key above was unchanged, so the corrected method would have
            # been handed the vintage version 1.0 wrote, tonight and every night until customs
            # published a new month. The version is what says a method is no longer the one that
            # made the stored claim, so it is what the new claim is filed under.
            if vintage is not None and (vintage.model_version or "") != (output.method_version or ""):
                vkey = f"{vkey}|v{output.method_version}"
                vintage = session.scalar(
                    select(ForecastVintageModel).where(ForecastVintageModel.vintage_key == vkey))
            if vintage is None:
                vintage = ForecastVintageModel(
                    vintage_key=vkey, identity_key=identity_key, model_key=chosen.method.method_key,
                    model_version=output.method_version, horizon_weeks=int(horizon_weeks),
                    as_of=_as_datetime(as_of), chemical=prepared.chemical,
                    currency=str(identity.currency or ""), value=float(point.value),
                    quantiles=band,
                    period=_as_datetime(point.period), steps=int(point.steps), resolution=str(point.resolution),
                    exact=bool(point.exact), calibration={}, observations=len(prepared.history.points),
                    anchor_value=float(prepared.history.points[-1][1]), state=RELEASED,
                    metadata_json={"governed": True, "cutoff": prepared.issue_date.isoformat(),
                                   "inputs": inputs, "diagnostics": dict(output.diagnostics),
                                   **({"band_withheld": withheld} if withheld else {})},
                    selected_method_id=chosen.method.id, selection_source=chosen.source.value,
                    review_id=chosen.review_id)
                session.add(vintage)
                session.flush()
            run = GovernedForecastRunModel(
                run_key=key, identity_id=identity.id, identity_key=identity_key, horizon_weeks=int(horizon_weeks),
                as_of=_as_datetime(as_of), method_id=chosen.method.id, method_key=chosen.method.method_key,
                method_version=output.method_version, selection_source=chosen.source.value,
                review_id=chosen.review_id, status=SUCCEEDED, reason="", vintage_id=vintage.id,
                recommended_method_key=None, inputs=inputs)
            session.add(run)
            session.commit()
            return _run_dict(run) | {"written": True, "value": vintage.value, "period": point.period.isoformat()}

    def _free_key(self, session, key: str) -> str:
        """The key for a new attempt: a FAILED run on the same day is kept as it was and the retry is
        recorded beside it as attempt 2, 3... (WP3 gate condition 1)."""
        attempt, candidate = 1, key
        while self._existing(session, candidate) is not None:
            attempt += 1
            candidate = f"{key}#attempt {attempt}"
        return candidate

    def _record_failure(self, identity, identity_key, horizon_weeks, as_of: date, chosen: InForce, reason: str,
                        recommendation: str | None, inputs, *, version: str = "",
                        issue_date: date | None = None) -> dict[str, Any]:
        key = run_key(identity_key, horizon_weeks, as_of, chosen.method.method_key, chosen.source,
                      issue_date, (inputs or {}).get("lead_weeks"))
        # The automatic recommendation is shown beside a failed OVERRIDE; when the automatic selection
        # itself failed there is no other governed answer to recommend until the next review.
        recommend = recommendation if recommendation and recommendation != chosen.method.method_key else None
        with self.session_factory() as session:
            held = self._existing(session, key)
            if held is not None and held.status == FAILED and held.reason == reason[:2000]:
                return _run_dict(held) | {"written": False, "already_present": True}
            key = self._free_key(session, key)
            run = GovernedForecastRunModel(
                run_key=key, identity_id=identity.id, identity_key=identity_key, horizon_weeks=int(horizon_weeks),
                as_of=_as_datetime(as_of), method_id=chosen.method.id, method_key=chosen.method.method_key,
                method_version=version or chosen.method.version, selection_source=chosen.source.value,
                review_id=chosen.review_id, status=FAILED, reason=reason[:2000], vintage_id=None,
                recommended_method_key=recommend, inputs=inputs)
            session.add(run)
            session.commit()
            log.warning("governed forecast FAILED for %s %sw (%s): %s", identity_key, horizon_weeks,
                        chosen.method.method_key, reason)
            return _run_dict(run) | {"written": True}


def _run_dict(run: GovernedForecastRunModel) -> dict[str, Any]:
    return {"identity_key": run.identity_key, "horizon_weeks": run.horizon_weeks,
            "as_of": run.as_of.date().isoformat() if run.as_of else None, "status": run.status,
            "method_key": run.method_key, "method_version": run.method_version,
            "selection_source": run.selection_source, "review_id": run.review_id, "reason": run.reason,
            "vintage_id": run.vintage_id, "recommended_method_key": run.recommended_method_key}


def all_rows(session_factory) -> dict[str, list[dict]]:
    """Every identity's rows, all versions, keyed by identity key - the related-series pool."""
    with session_factory() as session:
        identities = list(session.scalars(select(MarketIdentityModel)))
    out = {}
    for identity in identities:
        rows = forecast_rows(session_factory, identity.id, all_versions=True)
        if rows:
            out[identity_key_of(identity)] = rows
    return out


def produce_all(session_factory, *, issue_date: date | None = None,
                adapters: Mapping[str, ForecastModelAdapter] | None = None,
                progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Every series-horizon with something in force: generate its governed forecast. The job (D5)."""
    say = progress or (lambda _line: None)
    issue_date = issue_date or datetime.now(timezone.utc).date()
    with session_factory() as session:
        pairs = {(o.identity_id, o.horizon_weeks) for o in ForecastOverrideRepository(session).list_active()}
        pairs |= {(s.identity_id, s.horizon_weeks) for s in ForecastSelectionRepository(session).list_active()}
        keys = {row.id: identity_key_of(row) for row in session.scalars(select(MarketIdentityModel))}
        conditional = any((c := in_force(session, i, h)) is not None and c.method is not None
                          and c.method.kind == CONDITIONAL for i, h in pairs)
    say(f"{len(pairs)} series-horizon pairs have a governed method in force at {issue_date.isoformat()}.")
    service = ProductionForecastService(session_factory, adapters=adapters)
    pool = all_rows(session_factory) if conditional and pairs else None
    results = []
    for identity_id, horizon in sorted(pairs, key=lambda p: (keys.get(p[0], ""), p[1])):
        key = keys.get(identity_id)
        if key is None:
            continue
        with session_factory() as session:
            identity = session.get(MarketIdentityModel, identity_id)
            chemical = str(identity.product_family or "UNKNOWN") if identity else "UNKNOWN"
        features = load_feature_rows(session_factory, chemical, until=issue_date) if pool is not None else None
        result = service.generate(key, horizon, issue_date, rows=(pool or {}).get(key),
                                  related_rows=pool, feature_rows=features)
        results.append(result)
        say(f"{key} {horizon}w: {result['status']} {result.get('method_key', '')} {result.get('reason', '')[:120]}")
    return {"issue_date": issue_date.isoformat(), "pairs": len(pairs), "results": results,
            "succeeded": sum(1 for r in results if r["status"] == SUCCEEDED),
            "failed": sum(1 for r in results if r["status"] == FAILED)}


def latest_governed(session_factory, identity_key: str, horizons: Iterable[int] = (4, 8, 12)) -> dict[str, Any]:
    """What the page shows per horizon: the newest governed run, its vintage, or NOT GOVERNED."""
    out: dict[str, Any] = {"identity_key": identity_key, "horizons": {}}
    with session_factory() as session:
        identity = identity_by_key(session, identity_key)
        for weeks in horizons:
            chosen = in_force(session, identity.id, weeks) if identity is not None else None
            run = session.scalar(select(GovernedForecastRunModel).where(
                GovernedForecastRunModel.identity_key == identity_key,
                GovernedForecastRunModel.horizon_weeks == int(weeks)).order_by(
                # The newest RUN, by when it was made (third review, finding 1): ordering by data date put
                # an old refusal dated by its issue day above later successes dated by their data.
                GovernedForecastRunModel.created_at.desc()))
            if chosen is None:
                out["horizons"][str(weeks)] = {"status": NOT_GOVERNED}
                continue
            if chosen.method is None:
                out["horizons"][str(weeks)] = {"status": FAILED, "reason": chosen.refusal(),
                                               "in_force": {"method_key": chosen.method_id,
                                                            "source": chosen.source.value,
                                                            "review_id": chosen.review_id},
                                               "recommended_method_key": automatic_recommendation(
                                                   session, identity.id, weeks)}
                continue
            in_force_key = chosen.method.method_key
            entry: dict[str, Any] = {"in_force": {"method_key": in_force_key,
                                                  "source": chosen.source.value, "review_id": chosen.review_id}}
            # Only a run of the method NOW in force counts; a run of an earlier choice is history.
            if run is None or run.method_key != in_force_key or run.selection_source != chosen.source.value:
                entry["status"] = "PENDING"
                entry["reason"] = "the method in force has not been run yet"
            else:
                entry.update(_run_dict(run))
                # How current the stored answer is (WP3 gate condition 4): the day it was ISSUED and the
                # data date it stands on. The page must not present an old governed number as today's.
                issued = run.created_at if run.created_at.tzinfo else run.created_at.replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - issued).days
                data_age = (datetime.now(timezone.utc).date() - run.as_of.date()).days if run.as_of else None
                frequency = (run.inputs or {}).get("frequency", "M")
                allowed = {"D": 14, "W": 28, "M": 125}.get(frequency, 125)
                stale = []
                if age > STALE_AFTER_DAYS:
                    stale.append(f"issued {age} days ago; the production job has not run since")
                if data_age is not None and data_age > allowed:
                    stale.append(f"stands on data from {run.as_of.date().isoformat()}, {data_age} days old")
                entry["issued_at"] = issued.isoformat()
                entry["current"] = not stale
                entry["stale_reasons"] = stale
                if run.vintage_id:
                    vintage = session.get(ForecastVintageModel, run.vintage_id)
                    if vintage is not None:
                        entry["forecast"] = {"value": vintage.value, "period": vintage.period.date().isoformat(),
                                             "quantiles": dict(vintage.quantiles or {}),
                                             "model_version": vintage.model_version}
            out["horizons"][str(weeks)] = entry
    return out
