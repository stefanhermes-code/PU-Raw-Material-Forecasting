"""The Model Fit Review as a job: off the page path, off the engine start (Charlie, 19 Oct 2026, 4.2).

`run_review` reads the store the way `/forecast` reads it (`store_series.forecast_rows`), judges
each series for eligibility, runs the governed tournament on the eligible ones with every method
the governance registry lists as selectable or challenging, and returns the rankings and proposed
selections. It WRITES NOTHING to the governance tables: Workpackage 2 ends with a deterministic
proposal per series and horizon; committing one is Workpackage 4's atomic review.

It is a plain function of a session factory so it can run from `python -m app.cli review` on
Stefan's PC, from a scheduled task, or from a worker elsewhere - the datastore is the one thing it
needs, and that is Supabase wherever it runs. A foundation model runs here only when its runtime is
installed where this runs; otherwise it is UNAVAILABLE in the result, which is a finding, not a
failure.

Related series for the conditional methods are the other identities in the store, rebuilt at every
origin from their own availability; features are the `feature_observation` series by type and the
`event_record` counts for the identity's chemical, each cut at the origin the same way.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any, Callable, Iterable

from sqlalchemy import or_, select

from app.db.models import EventRecordModel, FeatureObservationModel, MarketIdentityModel
from app.domain.enums import ForecastMethodStatus
from app.domain.series_key import market_of, series_key
from app.forecasting.adapters.catalogue import governed_adapters
from app.forecasting.domain import ModelFitResult
from app.forecasting.eligibility import Candidate, eligible_series
from app.forecasting.governance import ForecastMethodRegistry
from app.forecasting.selection import propose
from app.forecasting.service import _as_date
from app.forecasting.store_series import forecast_rows
from app.forecasting.tournament import TournamentResult, run_tournament

log = logging.getLogger(__name__)


def identity_key_of(row: MarketIdentityModel) -> str:
    """The key `app.main` uses, because it is now the same function rather than the same text."""
    return series_key(row)


def _features_for(chemical: str) -> frozenset[str]:
    """The families whose features may drive this market: itself and everything upstream of it.

    The route graph already answers this - `upstream_candidates` walks the chains and traverses
    unpriced intermediates without counting them, so chlorine reaches TDI through phosgene as one
    priced step. Read from the same seed the application reads, not re-stated here.

    Loaded once. The seed is a file on disk and the review asks this question per market.
    """
    from raw_material_forecasting.chemical_routes import load_graph
    from raw_material_forecasting.chemicals import canonical_code

    code = canonical_code(chemical)
    if not code:
        return frozenset()
    global _ROUTE_GRAPH
    if _ROUTE_GRAPH is None:
        try:
            _ROUTE_GRAPH = load_graph()
        except Exception:        # pragma: no cover - no seed, so only the market's own features
            return frozenset({code})
    return frozenset({code, *(one.chemical for one in _ROUTE_GRAPH.upstream_candidates(code))})


_ROUTE_GRAPH = None

# Event kinds that describe the chain, or an input to all of it, rather than one chemical. An event
# of one of these kinds with no material reaches every market; anything else with no material
# reaches none, because it is about no chemical.
ABOUT_THE_WHOLE_CHAIN = frozenset({"SUPPLY_CONDITION"})

# One series for all of them, since the content is the same whichever market is asking.
MARKET_WIDE_EVENTS = "events:the whole chain"


def _spellings_reaching(session, material_of, wanted: frozenset[str]) -> list[str]:
    """The stored spellings of `material` whose rows may reach a market wanting `wanted`.

    The filter has to run in the database - that is the whole point of it - but the vocabulary must
    not move there with it. A material is written three ways in this store, `family_of` is the one
    thing that knows that, and a second vocabulary in SQL would drift from it exactly as the two
    copies of the FX rule drifted before `app/fx/rates.py` became the only one.

    So the question is turned round: ask the store which spellings it actually holds - 18 of them,
    one short string each - and fold those through `family_of` here. The answer is a list of
    literals the query can match on, and `family_of` stays the only vocabulary.

    A spelling that folds to NOTHING is kept. `feature_rows` treats an unrecognised material the
    same way it treats a row with no material at all, and that row reaches every market; dropping
    it here would change what a forecast is handed, which this is not allowed to do.
    """
    from raw_material_forecasting.chemicals import family_of

    reaching = []
    for (value,) in session.execute(select(material_of).distinct()):
        if value is None:
            continue
        family = family_of(value)
        if not family or family in wanted:
            reaching.append(value)
    return reaching


def feature_rows(session_factory, chemical: str, *, until: date) -> dict[str, list[dict]]:
    """The features that may drive THIS market, and two counts of the events that may.

    **The features had no chemical filter at all** - the events two loops down always had one, and
    nobody noticed the features did not. So every forecast was handed the whole feature store:
    measured 28 October 2026, **117,148 rows across 1,797 feature types**, of which 18 were TDI's.
    The related-market and feature-using methods were offered EU customs volumes for ethylene glycol
    as candidate drivers of a Chinese TDI price.

    **A market's own features are not enough either, and the measurement says so.** Filtering to the
    chemical alone leaves aniline, graft polyol, HR polyol, PHD, PIPA, visco polyol, rigid polyol and
    the aliphatic polyester polyol with **nothing**, and leaves TDI with 18 rows while toluene - the
    thing TDI is made from - has 10,151 sitting unused. A feedstock's price and trade volume are
    exactly what drives the material above it; that is what the whole chain work is for.

    So the rule is **this chemical and everything upstream of it**, read from the route graph:

        TDI                 18 -> 10,180     its own, plus toluene's
        PMDI                13 ->  5,659     aniline and benzene
        POLYETHER_POLYOL 11,720 -> 35,477    plus PO, EO, glycerol
        HDI, IPDI            0 ->     11     nothing upstream of them is priced yet

    The material is in `data["material"]`, written three ways - snake-case trade names, store codes,
    and `POLYOL` which migration 0035 retired - so it folds through `chemicals.family_of`, which
    reads `DISPLAY_NAMES` backwards rather than keeping a second vocabulary.

    A row with **no material at all** is offered to every market, which is what has always happened
    to it and the honest answer for a driver series somebody seeded deliberately: eleven of the
    117,148 have none, and refusing them would quietly remove a predictor nobody has attributed yet.
    `report["features_unattributed"]` counts them so they can be.
    """
    from raw_material_forecasting.chemicals import canonical_code, family_of

    wanted = _features_for(chemical)
    out: dict[str, list[dict]] = defaultdict(list)
    with session_factory() as session:
        # FOUR VALUES PER ROW, NOT THE ROW. The store is Supabase, so every column this loop is
        # handed crosses the network and is billed as egress. `select(FeatureObservationModel)`
        # fetched all seven, and `data` - a JSON blob averaging 1,425 bytes, of which this loop
        # reads one key - was nearly all of it: 117,162 rows came to 167 MB a call.
        #
        # Measured 5 November 2026, after Supabase cut the grace period on 662 GB against the 275
        # GB the Pro plan includes: this one statement had returned 685,916,507 rows over 5,894
        # calls since 6 September, about 910 GB, which is the whole of the overage. The engine runs
        # on Stefan's PC against a database in Singapore; a loop that reads a table and filters it
        # in Python is a network transfer here, not a memory operation.
        #
        # The material is extracted in the query and the blob left where it lies.
        material_of = FeatureObservationModel.data["material"].as_string()
        readable = _spellings_reaching(session, material_of, wanted)
        features = (
            select(FeatureObservationModel.feature_type, FeatureObservationModel.value,
                   FeatureObservationModel.available_from, material_of)
            .where(FeatureObservationModel.value.is_not(None),
                   or_(material_of.is_(None), material_of.in_(readable)))
            .order_by(FeatureObservationModel.available_from)
        )
        for feature_type, value, available_from, material_name in session.execute(features):
            day = _as_date(available_from)
            if day is None or day > until:
                continue
            material = family_of(material_name)
            if material and material not in wanted:
                continue
            out[f"feature:{feature_type}"].append(
                {"observation_date": day, "native_price": float(value), "available_from": available_from})
        # Two counts, not one, and not one merged.
        #
        # The events loop had the filter the features loop was missing, and that is why nobody
        # looked at it. It has two faults of its own, measured 29 October 2026 against the live
        # store, where 393 events sit.
        #
        # **It compared raw strings.** The features loop folds through `family_of`; this did
        # `.strip().upper()`. So the 24 events stored under `POLYOL`, which migration 0035 retired,
        # reached no market at all - polyether polyol's forecast counted 25 events where 49 were
        # about it.
        #
        # **And a market's own events are not all that drive it**, for the same reason its own
        # features are not: an outage at an aniline plant moves pMDI. Counting only its own leaves
        # eight markets on zero - PHD and PIPA polyol would see 115 events each, graft polyol 65
        # against the 1 it sees now, HR and visco polyol 20. 223 events reach a forecast today;
        # 698 reach one when the chain is read.
        #
        # They are kept APART rather than added together. An event count is "how much happened",
        # and folding 114 upstream events in with a market's own would bury its own plant outage in
        # a number nearly identical across every polyol market. Two series lets a method weigh them
        # differently; one series decides for it.
        # And a third count: what is happening to the chain itself.
        #
        # 41 of the 393 events name no chemical and are not meant to. `intelligence_bridge` writes
        # them on purpose, and its own comment says why: an exposure graded for "Catalysts,
        # surfactants and additives" or "Packaging / DG services" names no chemical "because they
        # are inputs to all of it", and a condition that grades the chain without grading the
        # inputs still carries the reading - "dropping it would leave the forecaster a four-month
        # hole in a disruption that ran through all of it."
        #
        # It left exactly that hole, because this function counted nothing with an empty material.
        # The Strait of Hormuz disruption is written per chemical from 3 July 2026 onward; the 41
        # chain and input-group rows run from 25 April, so the first ten weeks of it reached no
        # forecast at all. `market_intelligence.exposures_for` has always applied the right rule on
        # the page and in the Word report - "this market's own input group, plus every group that
        # names no chemical" - so the page and the forecast disagreed about the same disruption.
        #
        # Stefan, 29 October 2026: intelligence that names no chemical "is relevant information and
        # should be part of the application".
        #
        # A third series, for the reason the other two are separate: an event count says how much
        # happened, and a chain-wide condition is not the same signal as a market's own plant
        # outage. Company news is NOT this - "BASF CEO to become CEFIC president" names no chemical
        # because it is about no chemical, and it reaches no count. The test is the event's kind.
        own = canonical_code(chemical)
        # `wanted` is this same set - the chemical and everything above it. Asking for it twice was
        # redundant rather than slow (the graph walk is 0.04 ms), but one call is one answer.
        upstream = wanted - {own}
        mine: dict[date, int] = defaultdict(int)
        above: dict[date, int] = defaultdict(int)
        chain: dict[date, int] = defaultdict(int)
        # Three values per row here too, and for the same reason. 499 events is not what blew the
        # egress budget, but `data` on an event is the article it was read from and this loop has
        # never looked at it.
        reaching = _spellings_reaching(session, EventRecordModel.material, wanted)
        records = (
            select(EventRecordModel.event_type, EventRecordModel.material,
                   EventRecordModel.available_from)
            .where(or_(EventRecordModel.material.is_(None),
                       EventRecordModel.material.in_(reaching)))
            .order_by(EventRecordModel.available_from)
        )
        for event_type, event_material, available_from in session.execute(records):
            day = _as_date(available_from)
            if day is None or day > until:
                continue
            material = family_of(event_material)
            if not material:
                # Only the kinds that describe the chain. Everything else that names no chemical is
                # about no chemical, and stays on the page and in the reports where it belongs.
                if (event_type or "").strip().upper() in ABOUT_THE_WHOLE_CHAIN:
                    chain[day] += 1
            elif material == own:
                mine[day] += 1
            elif material in upstream:
                above[day] += 1
        for key, counts in ((f"events:{chemical}", mine),
                            (f"events:upstream:{chemical}", above),
                            (MARKET_WIDE_EVENTS, chain)):
            for day, count in sorted(counts.items()):
                out[key].append({"observation_date": day, "native_price": float(count),
                                 "available_from": day})
    return dict(out)


def method_statuses(session_factory) -> dict[str, ForecastMethodStatus]:
    with session_factory() as session:
        return {m.method_key: m.status for m in ForecastMethodRegistry(session).list_all()}


def run_review(session_factory, *, cutoff: date, identity_keys: Iterable[str] | None = None,
               method_keys: Iterable[str] | None = None, horizons: tuple[int, ...] = (4, 8, 12),
               max_origins: int | None = None, related: bool = True, features: bool = True,
               max_related: int = 3, registry=None,
               progress: Callable[[str], None] | None = None,
               on_series: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Judge, rank and propose for every identity (or the ones named).

    `progress` (v0.5.57) receives one plain line per step, so a person watching the window sees the
    review move: reading the store, then each market with its number, time taken and proposal.

    `on_series` receives each market's entry the moment it is finished, so the caller can store it
    then rather than at the end. Stefan, 29 October 2026: "when found then write it, no need to
    wait till all is finished. Such a BS." He is right - a review of 80 markets takes most of a day,
    and holding every result until the last one means a crash at market 70 throws away all seventy.
    The CHOICE still has to be all-or-nothing in one sense, which `review.py` keeps: a run that did
    not finish must not reset the quarterly clock. But the fits are the expensive part and there was
    never a reason to keep them in memory.
    """
    say = progress or (lambda _line: None)
    statuses = method_statuses(session_factory)
    catalogue = governed_adapters(registry)
    chosen = list(method_keys) if method_keys is not None else [
        key for key, status in statuses.items()
        if status in (ForecastMethodStatus.PRODUCTION_ELIGIBLE, ForecastMethodStatus.CHALLENGER)]
    # The flat controls are scored as references at every review, whatever their status.
    for control in ("naive_last", "seasonal_naive"):
        if control in catalogue and control not in chosen:
            chosen.append(control)
    missing = [key for key in chosen if key not in catalogue]
    adapters = [catalogue[key] for key in chosen if key in catalogue]

    with session_factory() as session:
        identities = list(session.scalars(select(MarketIdentityModel)))
    # Stored SERIES, not markets: the eight-field key splits some markets over grade, unit, price
    # kind or currency, and the review has always counted the split halves as two. Say which it
    # means, and carry the market alongside so the report can be read either way.
    market_by_key: dict[str, str] = {}
    rows_by_key: dict[str, list[dict]] = {}
    chemical_by_key: dict[str, str] = {}
    for identity in identities:
        key = identity_key_of(identity)
        rows_by_key[key] = forecast_rows(session_factory, identity.id, all_versions=True)
        chemical_by_key[key] = str(identity.product_family or "UNKNOWN")
        market_by_key[key] = " | ".join(market_of(identity))
    say(f"Reading the price history of {len(identities)} stored series "
        f"({len(set(market_by_key.values()))} markets) from the store ...")
    wanted = set(identity_keys) if identity_keys is not None else None
    candidates = [Candidate(key, chemical_by_key[key], rows, maintained=True)
                  for key, rows in rows_by_key.items() if rows and (wanted is None or key in wanted)]
    verdicts = eligible_series(candidates, cutoff)
    admitted = [v for v in verdicts if v.eligible]
    # The caption names the rule, so it has to be the rule. It read "24 months, at most 10%
    # missing" for the whole of the 29 October run, after that pair had been struck - which is the
    # same defect as every other one in this audit, in a progress line.
    say(f"{len(admitted)} of {len(verdicts)} series qualify for the review "
        f"(six months of readings, coverage not required, current and still priced); "
        f"{len(adapters)} methods each.")

    report: dict[str, Any] = {
        "cutoff": cutoff.isoformat(), "started_at": datetime.now(timezone.utc).isoformat(),
        "horizons": list(horizons), "methods": [a.method_key for a in adapters],
        "methods_without_adapter": missing, "statuses": {k: v.value for k, v in statuses.items()},
        "eligibility": [v.as_dict() for v in verdicts], "series": [],
        "markets": sorted({market_by_key[v.identity_key] for v in verdicts
                           if v.identity_key in market_by_key}),
    }
    shared: dict = {}          # supporting series as readable at each asked date, across all targets (v0.5.62)
    for number, verdict in enumerate(admitted, start=1):
        key = verdict.identity_key
        began = time.perf_counter()
        say(f"[{number}/{len(admitted)}] {key} ...")
        related_rows = ({other: rows for other, rows in rows_by_key.items() if other != key and rows}
                        if related else {})
        feature_series = feature_rows(session_factory, chemical_by_key[key], until=cutoff) if features else {}
        log.info("review: %s (%d related candidates, %d feature series)", key, len(related_rows), len(feature_series))
        # Score each market at the distance it is ACTUALLY asked for, not at the slot's name.
        #
        # A slot is 4, 8 or 12 weeks, but the data runs behind - two to three months on most of
        # these markets - so production asks the method for the slot PLUS the lag, and a market
        # three months behind is answered at 16, 21 and 25 weeks. Scoring every market at 4/8/12
        # measured a distance none of them use, and picked the method on it.
        #
        # The lag differs per market, so the horizons do: 1 behind on 3 pairs, 2 on 66, 3 on 44.
        # Same cost as before - three horizons per market - at the right three.
        from app.forecasting.production import lead_weeks   # local: production imports
                                                            # this module, so a top-level
                                                            # import would be circular.
        anchor = verdict.history.series.last_date
        leads = {slot: lead_weeks(slot, anchor, cutoff, verdict.history.frequency)
                 for slot in horizons} if anchor else {slot: slot for slot in horizons}
        result = run_tournament(
            rows_by_key[key], identity_key=key, chemical=chemical_by_key[key], cutoff=cutoff, adapters=adapters,
            horizons=tuple(dict.fromkeys(leads.values())), max_origins=max_origins,
            frequency=verdict.history.frequency,
            related_rows=related_rows, feature_rows=feature_series, max_related=max_related, cache=shared)
        # Back to slots before anything downstream sees it. Proposals, fit rows and selections are
        # keyed by horizon, and production looks a selection up by its SLOT - key them by the lead
        # and `in_force` finds nothing at all.
        result.ranking = {slot: result.ranking.get(lead, []) for slot, lead in leads.items()}
        result.horizons = tuple(leads)
        proposals = propose(result, statuses)
        picked = ", ".join(f"{p.horizon_weeks}w {p.method_key or '-'}" for p in proposals) or "no proposal"
        say(f"[{number}/{len(admitted)}] {key} done in {time.perf_counter() - began:.0f}s: {picked}")
        entry = {
            "identity_key": key, "market": market_by_key.get(key, ""),
            "chemical": chemical_by_key[key], "duplicate_of": verdict.duplicate_of,
            "tournament": result.as_dict(), "proposals": [p.as_dict() for p in proposals],
        }
        report["series"].append(entry)
        if on_series is not None:
            # Stored now, not at the end. A failure here is the caller's to handle; it must not
            # lose the markets already done, which is the whole point of writing as we go.
            on_series(entry)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    return report


def fit_results(result: TournamentResult, review_id: str, identity_id: str,
                method_ids: dict[str, str]) -> list[ModelFitResult]:
    """The review's rows for one series, ready for `ModelFitReviewRepository.add_result` (WP4 writes them)."""
    out = []
    for weeks, rows in result.ranking.items():
        for score in rows:
            method_id = method_ids.get(score.method_key)
            if method_id is None:
                continue
            counts = score.statuses
            status = ("SUCCEEDED" if score.ranked else
                      max(counts, key=counts.get) if counts else "INELIGIBLE")
            out.append(ModelFitResult(
                review_id=review_id, identity_id=identity_id, horizon_weeks=weeks, method_id=method_id,
                method_version=score.method_version, execution_status=status, matched_mae=score.mae,
                directional_accuracy=score.directional_accuracy, matched_dates=score.matched_dates,
                rank=score.rank, details={**score.as_dict(), "own_dates": score.own_dates,
                                          "frequency": result.frequency}))
    return out


def seconds_by_method(report: dict[str, Any]) -> list[tuple[str, float, int]]:
    """(method, seconds, fits) over the whole run, dearest first (masterplan H's stopwatch).

    The review records one elapsed time for the run and `TournamentResult` now records the seconds
    each method spent inside its own `forecast`. Summed here, this is the first answer to where the
    6.41 hours went - and so the first sound basis for dropping a method from the review rather than
    guessing from the count of its fits.
    """
    seconds: dict[str, float] = {}
    fits: dict[str, int] = {}
    for entry in report.get("series") or ():
        tournament = entry.get("tournament") or {}
        for method, spent in (tournament.get("seconds_by_method") or {}).items():
            seconds[method] = round(seconds.get(method, 0.0) + float(spent), 3)
        for method, counts in (tournament.get("statuses") or {}).items():
            fits[method] = fits.get(method, 0) + sum(counts.values())
    return sorted(((m, s, fits.get(m, 0)) for m, s in seconds.items()),
                  key=lambda row: -row[1])


def summary_lines(report: dict[str, Any]) -> list[str]:
    """What a person reads at the end of a run."""
    lines = [f"Model Fit Review (proposal only) at cutoff {report['cutoff']}",
             f"methods: {', '.join(report['methods'])}"]
    spent = seconds_by_method(report)
    if spent:
        total = sum(s for _m, s, _f in spent)
        lines.append(f"time inside the methods: {total / 3600:.2f} hours, dearest first")
        for method, seconds, fits in spent:
            share = (100.0 * seconds / total) if total else 0.0
            each = (seconds / fits) if fits else 0.0
            lines.append(f"  {method:28s} {seconds / 60:7.1f} min  {share:5.1f}%  "
                         f"{fits:5d} fits  {each:6.2f} s each")
    if report["methods_without_adapter"]:
        lines.append(f"no adapter for: {', '.join(report['methods_without_adapter'])}")
    rejected = [v for v in report["eligibility"] if not v["eligible"]]
    lines.append(f"series judged: {len(report['eligibility'])}, eligible: {len(report['eligibility']) - len(rejected)}")
    for v in rejected:
        lines.append(f"  not ranked: {v['identity_key']}: {'; '.join(v['reasons'])}")
    for entry in report["series"]:
        t = entry["tournament"]
        lines.append(f"{entry['identity_key']} [{t['frequency']}] origins {len(t['origins'])}, skipped {len(t['skipped'])}")
        unavailable = sorted(k for k, s in t["statuses"].items() if set(s) == {"UNAVAILABLE"})
        if unavailable:
            lines.append(f"  unavailable here: {', '.join(unavailable)}")
        for p in entry["proposals"]:
            if p["method_key"]:
                lines.append(f"  {p['horizon_weeks']:>2} weeks -> {p['method_key']} (MAE {p['mae']}, "
                             f"{p['matched_dates']} matched dates" + (f", challenger ahead: {p['challenger_ahead']}" if p["challenger_ahead"] else "")
                             + ("" if p["beats_flat_control"] else ", does not beat no-change") + ")")
            else:
                lines.append(f"  {p['horizon_weeks']:>2} weeks -> {p['reason']}")
    return lines
