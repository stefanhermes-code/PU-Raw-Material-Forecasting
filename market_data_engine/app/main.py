from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import logging
import os
import threading

import time

from fastapi import Body, FastAPI, HTTPException
from sqlalchemy import select

from app.config import Settings
from app.db.models import Base, EventRecordModel, FeatureObservationModel, IngestionRunModel, MarketIdentityModel, MarketReferencePointModel, PriceInCurrencyModel, PriceObservationModel, QualityIssueModel, QuarantinedReadingModel, SourceRegistryModel
from app.db.session import create_db_engine
from app.domain.series_key import series_key
from app.persistence.service import PersistentMarketDataService
from app.fx.in_every_currency import CURRENCIES, convert_readings
from app.forecasting.readiness import assess_readiness
from app.forecasting.backtest import backtest_baselines
from app.forecasting import europe_chain, store_series
from app.sources.registry import SourceRegistry
from raw_material_forecasting import price_kinds

def application_version() -> str:
    root_version = Path(__file__).resolve().parents[2] / "VERSION"
    return root_version.read_text(encoding="utf-8").strip()


VERSION = application_version()


# The two European bases that carry their own history rather than being chained to the published
# series: DDP is that series, and DAP is the delivered purchase history. One copy, where the chain
# is built (masterplan F) - this name is kept because it is what the module exported.
CHAIN_EXEMPT_BASES = europe_chain.EXEMPT_BASES

# identity_key -> (evidence fingerprint, the payload computed from it). See /forecast.
_FORECAST_CACHE: dict[str, tuple[tuple, dict]] = {}

# What the warm-up is doing, for the gauge to read. Held here rather than reported by whoever asked
# for it: the helper that used to prepare the forecasts over HTTP stopped answering twice on
# 23 September 2026, and a gauge that depends on a process nobody can see is a gauge that hangs.
_WARMUP: dict[str, object] = {"running": False, "prepared": 0, "failed": 0, "total": 0,
                              "started_at": None, "finished_at": None}
_WARMUP_LOCK = threading.Lock()


def _priced(price, identity, wanted: str, stored) -> dict:
    """What a reading costs, in the currency the reader asked for.

    Asked for nothing, or asked for the currency the market is assessed in: the reading as stored.
    Asked for another: the value written when the datapoint was created, with the rate and the day
    it was quoted, so it can be rechecked without rerunning anything. Asked for one we could not
    convert into: a null price and `fx_unconverted`, never the number it is assessed in. A reading
    the page cannot honestly show is one the page does not draw.
    """
    assessed_in = (identity.currency or "").upper()
    if not wanted or wanted == assessed_in:
        return {"price": price.price, "price_low": price.price_low, "price_high": price.price_high,
                "currency": identity.currency, "source_currency": identity.currency,
                "source_price": price.price}
    if stored is None:
        return {"price": None, "price_low": None, "price_high": None,
                "currency": wanted, "source_currency": identity.currency,
                "source_price": price.price, "fx_unconverted": True}
    return {"price": stored.price, "price_low": stored.price_low, "price_high": stored.price_high,
            "currency": wanted, "source_currency": identity.currency,
            "source_price": price.price, "fx_rate": stored.fx_rate,
            "fx_rate_date": stored.fx_rate_date.isoformat() if stored.fx_rate_date else None}


# What a `source_registry` row that the code registry does not know about is, once it is settled
# that nothing fetches it. Two answers, because the two read differently to a person: a document
# that arrived once was imported, and a record kept by hand or derived in the store is simply not
# collected. Masterplan B, v1.2.9.
_ARRIVED_AS_A_DOCUMENT = frozenset({
    "MANUAL_IMPORT", "LICENSED_IMPORT", "CURATED_IMPORT", "INTERNAL_IMPORT", "PURCHASE_RECORD"})


def _status_of_an_unfetched_row(row: SourceRegistryModel) -> str:
    """`MANUAL_IMPORT` for a source whose documents arrived once, `MONITOR_ONLY` otherwise.

    Measured 28 October 2026: `/sources` returned 102 rows, 88 from the code registry and 14 from
    the database alone. Nine of the 14 arrived as documents; the other five - producer notices read
    by hand, two closed purchase histories, one procurement lane and the DDP reference series - are
    kept, not fetched. Thirteen of the 14 already read as unfetched because their refresh frequency
    said MANUAL or NONE; `europe_published_ddp` said MONTHLY and did not.
    """
    method = str(getattr(row, "collection_method", "") or "").upper()
    return "MANUAL_IMPORT" if method in _ARRIVED_AS_A_DOCUMENT else "MONITOR_ONLY"


def _recent_days(session, column, keep: int) -> dict[str, list[str]]:
    """The last `keep` days each source has a reading on, oldest first, one entry per source.

    Days, not readings: a source that prints eleven grades on the same morning kept one rhythm that
    morning, not eleven. The gaps between these days are what say how often a publisher prints, and
    a publisher that has not printed for longer than its own gap is the one that has stopped.

    Ranked in the database rather than fetched and trimmed here: without the window function this is
    every date of every reading, and `icis_history` alone holds 14,550.
    """
    from sqlalchemy import func

    day = func.date(column).label("day")
    distinct = (select(PriceObservationModel.source_id.label("source_id"), day)
                .where(column.is_not(None))
                .group_by(PriceObservationModel.source_id, day).subquery())
    ranked = (select(distinct.c.source_id, distinct.c.day,
                     func.row_number().over(partition_by=distinct.c.source_id,
                                            order_by=distinct.c.day.desc()).label("place"))
              .subquery())
    out: dict[str, list[str]] = {}
    for source_id, value in session.execute(
            select(ranked.c.source_id, ranked.c.day).where(ranked.c.place <= keep)
            .order_by(ranked.c.source_id, ranked.c.day)).all():
        out.setdefault(str(source_id), []).append(str(value)[:10])
    return out


def _just_the_day(moment) -> str | None:
    """Ten characters of a stamp, the way the index's readers take it.

    The two that read these dates are `market_lines._day`, which is `str(value or "")[:10]`, and
    `current_market._date_only`, which parses either form. So the time of day on an index reading
    was never read by anything - 35,515 readings carrying `T00:00:00+00:00` twice over, 15
    characters apiece. Written here rather than in the client, because the saving is the bytes on
    the wire.

    `str(...)[:10]` and not `.date().isoformat()`: that is the readers' own rule, and it holds for a
    date, a datetime and a string alike rather than only for the type the store happens to use.
    """
    return None if moment is None else str(moment)[:10]


def _identity_key(row: MarketIdentityModel) -> str:
    return series_key(row)


try:  # OS certificate store (same trust as Edge) for publisher and FX provider TLS
    import truststore

    truststore.inject_into_ssl()
except Exception:  # pragma: no cover
    pass


# The first year the Forecast page offers (FORECAST_FROM_YEAR in app.py).
WARM_UP_FROM_YEAR = 2026
WARM_UP_PAUSE_SECONDS = 0.25
# A market whose forecast takes longer than this is named in the log (v0.5.51).
SLOW_MARKET_SECONDS = 120


def create_app(database_url: str | None = None, raw_root=None) -> FastAPI:
    settings = Settings()
    url = database_url or settings.database_url
    from app.forecasting import kept

    kept_folder = kept.folder_for(url)
    raw_path = Path(raw_root or settings.raw_store_root) / "runtime"
    if url.startswith("sqlite") and "///" in url:
        sqlite_path = url.split("///", 1)[1]
        if sqlite_path and sqlite_path != ":memory:":
            Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_db_engine(url)
    if url.startswith("sqlite"):
        Base.metadata.create_all(engine)

    from sqlalchemy.orm import sessionmaker

    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    registry = SourceRegistry.expanded()

    application = FastAPI(title="PU Raw Material Forecasting", version=VERSION)
    application.state.session_factory = session_factory
    application.state.registry = registry
    application.state.market_data_service = PersistentMarketDataService(session_factory, raw_path, registry)

    def _store_import(batch):
        from app.importing.service import store

        return store(application.state.market_data_service, batch)

    application.state.market_data_service_import = _store_import

    # Every startup import records what it did. A swallowed exception and an empty folder used to
    # look identical to "there is no data yet", which is how a path bug survived four releases while
    # the page politely reported that history was still being gathered.
    import_status: dict[str, dict] = {"housekeeping": {"status": "pending"}}

    def _housekeeping() -> None:
        """The work the engine used to do before it answered its first request: history imports,
        reference packs, repairs, the consolidated series, the producer-signal feed, and - when
        FORECAST_WARM_UP=1 - prices in every currency and the forecast warm-up.

        v0.5.40: run AFTER the service is listening, not before. Against a SQLite file this took
        seconds; against Supabase every record check is a round trip to Singapore and the same
        work takes minutes, during which a launcher polling /health saw a dead engine. Each step is
        idempotent, so an answer served while it runs is a correct answer over yesterday's
        housekeeping; /import-status says where it stands.
        """
        import_status["housekeeping"] = {"status": "running"}
        # The stored source registry follows the code's, once, here (v0.5.51) - not on every
        # /sources read. Only rows that differ are written, so a settled store takes no lock.
        try:
            import_status["source_registry"] = {
                "rows_changed": application.state.market_data_service.sync_sources()}
        except Exception as exc:  # pragma: no cover - the registry must not stop the service
            import_status["source_registry"] = {"status": "failed", "error": str(exc)}
            logging.getLogger(__name__).warning("source registry sync failed", exc_info=True)
        # Issuers whose documents arrive by hand are created while the engine is running, so they live
        # in the database and not in this repository. Without reading them back, their own readings
        # would have no source definition after a restart and the next import from the same issuer
        # would look like a new one.
        try:
            from app.importing.service import imported_definitions

            with session_factory() as session:
                for definition in imported_definitions(session.scalars(select(SourceRegistryModel))):
                    registry.add(definition)
        except Exception:                      # a store without the table yet must not block startup
            pass

        # HTC's own purchase history: FoamPartner China 2014-2017 and KGI Thailand 2021-2023. Loading it
        # here rather than in a script is deliberate - the engine is where the database and the network
        # both are, and the import is idempotent, so a start that has already loaded it does nothing.
        # Never allowed to break startup.
        # Every startup import records what it did. A swallowed exception and an empty folder used to
        # look identical to "there is no data yet", which is how a path bug survived four releases while
        # the page politely reported that history was still being gathered.

        try:
            from app.fx.ecb import backfill_ecb_rates
            from app.history.transactions import HISTORY_FILES, import_transactions

            # The history is quoted in CNY, THB and EUR, so it can only be shown in USD if the ECB rates
            # reach back as far as the prices do.
            import_status["fx_backfill"] = backfill_ecb_rates(session_factory)
            # One entry per extraction file, so a missing one is named rather than silently skipping the
            # rest: FNAG's European book arrived after the China one and must not be able to take it
            # down on its way in.
            loaded = {}
            for path in HISTORY_FILES:
                if not path.exists():
                    loaded[path.name] = {"status": "missing_file", "path": str(path)}
                    logging.getLogger(__name__).warning("historical price file not found: %s", path)
                    continue
                loaded[path.name] = import_transactions(session_factory, path)
            import_status["transactions"] = loaded
        except Exception as exc:  # pragma: no cover - historical import must not stop the service
            import_status["transactions"] = {"status": "failed", "error": str(exc)}
            logging.getLogger(__name__).warning("historical transaction import failed", exc_info=True)

        # The signed purchase orders, loaded straight after the ERP extract and never before it: an
        # order supersedes the receipt posting that records the same trade, so the postings have to be
        # in the store for it to find them. Idempotent by PO number. Never allowed to break startup.
        try:
            from app.history.purchase_orders import DEFAULT_FILE as PO_FILE, import_purchase_orders

            if not PO_FILE.exists():
                import_status["purchase_orders"] = {"status": "missing_file", "path": str(PO_FILE)}
                logging.getLogger(__name__).warning("purchase order file not found: %s", PO_FILE)
            else:
                import_status["purchase_orders"] = import_purchase_orders(session_factory)
        except Exception as exc:  # pragma: no cover - a PO import must not stop the service
            import_status["purchase_orders"] = {"status": "failed", "error": str(exc)}
            logging.getLogger(__name__).warning("purchase order import failed", exc_info=True)

        # Signed purchase orders. A separate lane from the ERP extract because a PO is one negotiation on
        # one dated document rather than a month summarised afterwards, and because every one of them
        # carries its own arithmetic - which the loader checks before it files anything.
        try:
            from app.history.purchase_orders import import_purchase_orders

            import_status["purchase_orders"] = import_purchase_orders(session_factory)
        except Exception as exc:  # pragma: no cover - a PO must never stop the service
            import_status["purchase_orders"] = {"status": "failed", "error": str(exc)}
            logging.getLogger(__name__).warning("purchase order import failed", exc_info=True)

        # Reference series: upstream history that is not a price - PPI indices, proxies, benchmarks from
        # other markets. Held apart from price_observation on purpose, so nothing on the price path can
        # read an index as if it were money. Loaded from long-format packs in data/reference/.
        # Never allowed to break startup.
        try:
            from app.reference.importer import DEFAULT_DIR, PACKS, import_reference_packs

            packs = import_reference_packs(session_factory)
            import_status["reference_packs"] = {
                "status": "ok" if packs else "no_packs",
                "directory": str(DEFAULT_DIR),
                "named_packs": [str(pack.path) for pack in PACKS],
                "packs": packs,
            }
            if not packs:
                logging.getLogger(__name__).warning("no reference packs found in %s", DEFAULT_DIR)
        except Exception as exc:  # pragma: no cover - a reference pack must not stop the service
            import_status["reference_packs"] = {"status": "failed", "error": str(exc)}
            logging.getLogger(__name__).warning("reference pack import failed", exc_info=True)

        # Reference points built from more than one day's readings. The rule that prevents it was
        # written thirty-nine minutes after the worst of them was calculated, so the builder is sound and
        # the store carries what it wrote beforehand. Repairing at startup rather than in a script means
        # a restart is all it takes; it is idempotent, so a start with nothing to repair does nothing.
        # Never allowed to break startup.
        try:
            from app.reference.repair import repair_blended_references

            import_status["reference_repair"] = repair_blended_references(session_factory)
        except Exception as exc:  # pragma: no cover - a repair must not stop the service
            import_status["reference_repair"] = {"status": "failed", "error": str(exc)}
            logging.getLogger(__name__).warning("reference repair failed at startup", exc_info=True)

        # The consolidated series. Four publishers quote China TDI ex-works and they disagree by about
        # half a percent; none of them is the market. The engine carries one governed value per date,
        # built from every publisher that printed - written beside the raw prints, never over them.
        #
        # Here as well as after each collection, because the history that arrives through the loaders
        # and the migrations never passes through a collection run. Never allowed to break startup.
        try:
            from app.reference.series import rebuild_all_market_series

            import_status["consolidated_series"] = {
                "status": "ok", "series": rebuild_all_market_series(session_factory)}
        except Exception as exc:  # pragma: no cover - consolidation must not stop the service
            import_status["consolidated_series"] = {"status": "failed", "error": str(exc)}
            logging.getLogger(__name__).warning("consolidated series rebuild failed", exc_info=True)

        # The method watch. A market's forecast method is re-checked when a reading arrives that the
        # choice has not seen - which is what Stefan asked for on 29 October 2026: "a continuously
        # monitoring mechanism in place, inside the application. No outside batch files, not historic
        # reviews, just get rid of these."
        #
        # Started here because this is where the application starts, so nothing outside it has to
        # fire. It replaces a three-month calendar that nothing ever triggered: measured the same
        # day, `due_at` was 20 January 2027 and exactly one review had ever run, because the only
        # way to start one was a batch file somebody had to double-click. Both review batch files
        # are deleted and the cadence setting is gone.
        #
        # A daemon thread, one market per tick, fifteen minutes apart, and it stands aside while any
        # review is running - so it never competes with a baseline run or with collection. Never
        # allowed to break startup.
        try:
            from app.forecasting.watch import start as start_the_method_watch

            def _watch_said(did: dict) -> None:
                import_status["method_watch"] = {"status": "watching", **did}

            def _engine_has_finished_starting() -> bool:
                return str(import_status.get("housekeeping", {}).get("status")) in ("done", "skipped")

            start_the_method_watch(session_factory, report=_watch_said,
                                   ready=_engine_has_finished_starting)
            import_status["method_watch"] = {"status": "watching",
                                             "state": "waiting for the engine to finish starting"}
        except Exception as exc:  # pragma: no cover - the watch must not stop the service
            import_status["method_watch"] = {"status": "failed", "error": str(exc)}
            logging.getLogger(__name__).warning("method watch did not start", exc_info=True)

        # The producer-signal feed is a projection of stored events, so refresh it whenever the engine
        # starts. Writing it only at the end of `app.cli collect` meant that on a day when nothing was
        # due, no collection ran and the feed was never written at all - which is exactly what happened
        # on the first v0.4.59 start. Never allowed to break startup.
        try:
            from app.producer_signals.export import export_producer_signals

            export_producer_signals(session_factory)
        except Exception:  # pragma: no cover - a feed problem must not stop the service
            logging.getLogger(__name__).warning("producer-signal feed refresh failed at startup", exc_info=True)
        # The governed bootstrap readings (data/bootstrap_observations.json). v0.5.51: here, not in
        # the launcher. `app.cli bootstrap` imported app.main, which built a second engine - with
        # its own housekeeping and warm-up thread - inside the launcher process on every start,
        # and the 63 readings it checks cost a round trip each against Supabase while the person
        # waited. Idempotent: a reading already held is not inserted twice. Never breaks startup.
        try:
            from app.bootstrap import bootstrap_file

            bootstrap_path = Path(os.getenv("BOOTSTRAP_FILE") or "data/bootstrap_observations.json")
            if bootstrap_path.is_file():
                import_status["bootstrap"] = bootstrap_file(session_factory, registry, bootstrap_path)
            else:
                import_status["bootstrap"] = {"status": "missing_file", "path": str(bootstrap_path)}
        except Exception as exc:  # pragma: no cover - the bootstrap must not stop the service
            import_status["bootstrap"] = {"status": "failed", "error": str(exc)}
            logging.getLogger(__name__).warning("bootstrap readings failed", exc_info=True)

        # The governed method registry (Adaptive Forecast Method Selection CR, Workpackage 1).
        # Idempotent: registers what is missing, never changes a status an administrator set.
        # Never allowed to break startup, and it changes no live forecast.
        try:
            from app.forecasting.governance import seed_methods
            from app.forecasting.registry import default_registry

            with session_factory() as session:
                import_status["forecast_methods"] = seed_methods(session, default_registry())
                session.commit()
        except Exception as exc:  # pragma: no cover - the registry must not stop the service
            import_status["forecast_methods"] = {"status": "failed", "error": str(exc)}
            logging.getLogger(__name__).warning("forecast method registry seed failed", exc_info=True)

        if os.getenv("FORECAST_WARM_UP") == "1":
            # Currencies before forecasts, and before the warm-up thread takes the session pool. The
            # page reads stored values now, so a reading with none is a reading that does not appear.
            try:
                logging.getLogger(__name__).info(
                    "prices in every currency: %s", _convert_readings_now())
            except Exception as exc:  # pragma: no cover - startup must not be stopped by this
                logging.getLogger(__name__).warning(
                    "prices in every currency failed at startup: %s", exc)
            _start_warm_up()
        import_status["housekeeping"] = {"status": "done"}

    # WP3: governed forecasts and administrator overrides (app/api/forecast_methods.py).
    from app.api.forecast_methods import make_router as _forecast_methods_router
    application.include_router(_forecast_methods_router(session_factory))
    # WP5: Method Fit - why the method in force is in force (app/api/model_fit.py).
    from app.api.model_fit import make_router as _model_fit_router
    application.include_router(_model_fit_router(session_factory))

    @application.get("/forecast-methods")
    def forecast_methods():
        """The governed method registry: every method, its status and its history. Read-only."""
        from app.forecasting.governance import ForecastMethodRegistry

        with session_factory() as session:
            registry = ForecastMethodRegistry(session)
            return [{"method_key": m.method_key, "name": m.name, "kind": m.kind,
                     "status": m.status.value, "version": m.version,
                     "is_flat_control": m.is_flat_control, "selectable": m.selectable,
                     "commercial_use": m.commercial_use, "config": m.config, "notes": m.notes,
                     "history": registry.history(m.method_key)}
                    for m in registry.list_all()]

    @application.get("/health")
    def health():
        return {"status": "ok", "version": VERSION, "housekeeping": import_status["housekeeping"]["status"]}

    fx_refresh_state = {"last_attempt": None}

    @application.get("/fx/rates")
    def fx_rates(currencies: str | None = None):
        """ECB daily rates (EUR base). Refreshes from the provider when stale, at most once per hour."""
        from datetime import datetime, timedelta, timezone

        from app.fx.ecb import DEFAULT_CURRENCIES, is_stale, list_rates, refresh_ecb_rates

        wanted = tuple(c.strip().upper() for c in currencies.split(",") if c.strip()) if currencies else DEFAULT_CURRENCIES
        status = None
        now = datetime.now(timezone.utc)
        last = fx_refresh_state["last_attempt"]
        if is_stale(session_factory) and (last is None or now - last > timedelta(hours=1)):
            fx_refresh_state["last_attempt"] = now
            status = refresh_ecb_rates(session_factory, currencies=tuple(sorted(set(DEFAULT_CURRENCIES) | set(wanted))))
        rows = list_rates(session_factory, currencies=wanted)
        return {"base": "EUR", "source": "ECB via frankfurter.dev", "latest_date": rows[-1]["date"] if rows else None,
                "refresh": status, "rates": rows}

    @application.post("/fx/refresh")
    def fx_refresh():
        from app.fx.ecb import refresh_ecb_rates

        return refresh_ecb_rates(session_factory)

    @application.get("/sources")
    def sources():
        """Registry + what actually happened: last attempt, its outcome, and the evidence on hand per source."""
        from sqlalchemy import func

        due_ids = set(application.state.market_data_service.due_source_ids())
        with session_factory() as session:
            persisted = {row.source_id: row for row in session.scalars(select(SourceRegistryModel))}
            latest_runs = {}
            for run in session.scalars(select(IngestionRunModel).order_by(IngestionRunModel.started_at)):
                latest_runs[run.source_id] = run  # ordered ascending, so the last one wins
            evidence = {
                source_id: (int(count), latest)
                for source_id, count, latest in session.execute(
                    select(PriceObservationModel.source_id, func.count(PriceObservationModel.id), func.max(PriceObservationModel.observation_date))
                    .group_by(PriceObservationModel.source_id)
                ).all()
            }
            # The days this source's readings carry, so the reader can measure each publisher
            # against its own rhythm instead of against a wall clock shared by all of them. Stefan's
            # standing rule, which `collection_watch` already keeps for the chemical-by-geography
            # watch: "not assume all is daily to make something declare it is missing". A monthly
            # publisher is not late on day fifteen.
            #
            # Sixteen is more than any reader needs - the application's median is taken over twelve
            # gaps - so the window that decides lateness stays the reader's, not this endpoint's.
            recent_observed = _recent_days(session, PriceObservationModel.observation_date, 16)
            recent_published = _recent_days(session, PriceObservationModel.publication_date, 16)

            # How often we reached it, over a window rather than on the last attempt alone. The
            # publisher scorecard (Stefan, 2 October 2026) measures reachability as a ratio, the way
            # a supplier scorecard measures OTIF, and one attempt tells you nothing about a ratio.
            since = datetime.now(timezone.utc) - timedelta(days=90)
            attempts: dict[str, list[int]] = {}
            for source_id, state, count in session.execute(
                    select(IngestionRunModel.source_id, IngestionRunModel.state,
                           func.count(IngestionRunModel.id))
                    .where(IngestionRunModel.started_at >= since)
                    .group_by(IngestionRunModel.source_id, IngestionRunModel.state)).all():
                row = attempts.setdefault(str(source_id), [0, 0])
                row[0] += int(count)
                if str(state) in {"SUCCESS", "PARTIAL"}:
                    row[1] += int(count)
            rows = []
            for source in registry.list():
                item = asdict(source)
                row = persisted.get(source.source_id)
                run = latest_runs.get(source.source_id)
                count, latest = evidence.get(source.source_id, (0, None))
                item["last_successful_collection"] = row.last_successful_collection if row is not None else None
                item["is_due"] = source.source_id in due_ids
                item["last_attempt_at"] = run.started_at if run is not None else None
                item["last_attempt_state"] = run.state if run is not None else None
                item["last_attempt_error"] = run.error if run is not None else None
                item["observation_count"] = count
                item["latest_observation_date"] = latest
                tried, reached = attempts.get(source.source_id, (0, 0))
                item["attempts_90d"] = tried
                item["reached_90d"] = reached
                item["recent_observation_days"] = recent_observed.get(source.source_id, [])
                item["recent_publication_days"] = recent_published.get(source.source_id, [])
                rows.append(item)

            # Sources that live in the database and not in the code registry.
            #
            # `icis_history`, `foampartner_china`, `kgi_thailand` and `europe_published_ddp` were
            # registered by an import rather than written into `SourceRegistry`, so this endpoint
            # did not know they existed. The application asks this endpoint how a source is
            # collected in order to tell assessed history from today's collection, got no answer
            # for any of them, and drew eight years of ICIS on the chart labelled as the stitched
            # bridge. A source that has written 13,823 observations is not invisible.
            known = {item["source_id"] for item in rows}
            for source_id, row in sorted(persisted.items()):
                if source_id in known:
                    continue
                run = latest_runs.get(source_id)
                count, latest = evidence.get(source_id, (0, None))
                rows.append({
                    "source_id": row.source_id,
                    "publisher": row.publisher,
                    "source_name": row.source_name,
                    "base_url": row.base_url,
                    "rights_class": row.rights_class,
                    "rights_status": row.rights_status,
                    "collection_method": row.collection_method,
                    "refresh_frequency": row.refresh_frequency,
                    # A row that exists only here has no collector in the code registry, so nothing
                    # fetches it, whatever its `refresh_frequency` says. Said out loud (masterplan
                    # B, v1.2.9) because `source_registry` has no `collector_status` column and the
                    # key was simply absent: the reader of this endpoint then judged "do we fetch
                    # it" on the refresh frequency alone, and `europe_published_ddp` - MONTHLY, no
                    # collector, a reference series derived from the store - was counted among the
                    # feeds we collect and could be reported late. 451 readings and three feed rows
                    # on 28 October 2026.
                    "collector_status": _status_of_an_unfetched_row(row),
                    "active": row.active,
                    "reliability_level": row.reliability_level,
                    "notes": row.notes,
                    "last_successful_collection": row.last_successful_collection,
                    "is_due": False,
                    "last_attempt_at": run.started_at if run is not None else None,
                    "last_attempt_state": run.state if run is not None else None,
                    "last_attempt_error": run.error if run is not None else None,
                    "observation_count": count,
                    "latest_observation_date": latest,
                    "recent_observation_days": recent_observed.get(source_id, []),
                    "recent_publication_days": recent_published.get(source_id, []),
                    # A row that exists only in the store carries no registered period, so nothing
                    # here says it is closed. It is judged on its readings like any open feed.
                    "covers_until": None,
                })
            return rows

    @application.get("/observations")
    def observations(currency: str = "", compact: bool = False, lean: bool = False,
                     identity_keys: str = ""):
        """Every reading. With `currency`, each one already in it.

        The conversion is not done here - it was done when the datapoint was created and is read
        back from the store (Stefan, 27 September 2026). A reading with no value in the asked-for
        currency comes back with a null price and says so, rather than handing back the number it
        is assessed in for something else to draw on the wrong axis. That is what put USD 17,533 on
        the Current Market card for a market that was 2,617 dollars.

        COLUMNS, NOT ENTITIES
        ---------------------
        This selected two mapped entities and joined them, so answering it built **71,030 ORM
        objects** - one PriceObservation and one MarketIdentity per reading, the identity rebuilt
        from scratch for each of the 35,515 rows although there are only 339 of them. Measured
        against the live store on 30 October 2026:

            ORM entities, joined   4.08 s   +  1.61 s for the prices in the display currency
            columns, no join       2.02 s   +  0.61 s, and 0.06 s to read all 339 identities once

        The identities are read once into a dictionary and looked up per row, which is what the
        join was doing 35,515 times. A row whose identity is missing is skipped, exactly as the
        inner join skipped it.

        `compact` LEAVES OUT WHAT THE CALLER ALREADY HAS
        ------------------------------------------------
        Stefan, 30 October 2026: *"The page still pulls all 35,515 readings to fill dropdowns,
        4.5 MB of it identity fields repeated per row that the page already holds."*

        He is right, and `evidence_id` is worse: a 64-character hash on every row, **2.34 MB, 16.4%
        of the payload**, and the application reads it off an observation nowhere at all. Measured
        field by field, what a caller holding `/identities` already knows comes to **7.03 MB of the
        27.9**.

        So `compact=1` omits `evidence_id` and every field that belongs to the identity rather than
        to the reading. The caller joins them back from `/identities`, keyed on `identity_id`, and
        sees exactly the same rows it saw before - `market_data_engine_client.snapshot` does that,
        so no page changes. Without `compact` the answer is unchanged, for anything else that asks.

        `lean` IS WHAT THE DROPDOWNS ACTUALLY NEED
        -------------------------------------------
        Every selector on a price page is decided by five functions - `current_market.best_row` and
        `market_lines.drawable_selections`, `priced_recently`, `priced_since` and
        `geographies_with_a_market`. Read on 30 October 2026, between them they touch exactly four
        fields of a reading:

            identity_key, source_id, observation_date  (publication_date as best_row's fallback)
            price - and ONLY as `if row.get("price") is None: continue`, never as a value

        So the page was fetching 35,515 prices, converting every one of them into the display
        currency, and then using none of the numbers to draw a dropdown. Measured on the live store:

            28.4 MB   the readings as they were that morning
            15.2 MB   `compact`
             2.9 MB   `lean`, grouped by market, with full ISO timestamps
             1.8 MB   `lean` with the two dates cut to ten characters apiece

        Grouped because the 55-character `identity_key` is then written once per market - 322 of
        them - instead of once per reading. And `lean` needs no currency at all, so it skips the
        `price_in_currency` query (0.61 s) and the page skips converting 35,515 rows.

        THE SAME QUERY, DELIBERATELY
        -----------------------------
        `lean` is built from the rows the full form is built from, in the same pass. It is a
        projection of one answer, not a second definition of what a reading is. Every defect this
        audit has turned up is one rule with two copies and the one that runs being the one nobody
        ruled on - consolidation, the FX fixings, the history rule, `MIN_TRAIN`. A second query here
        that drifted by one `WHERE` would put a market in the dropdown that the chart cannot draw.

        `identity_keys` narrows the full answer to named markets, comma separated, so the chart can
        ask for the one market it is drawing instead of all of them. The caller says which keys it
        wants; no rule about what belongs to a market moves into the engine.
        """
        wanted = (currency or "").strip().upper()
        with session_factory() as session:
            identities = {row.id: row for row in session.scalars(select(MarketIdentityModel))}
            rows = session.execute(
                select(PriceObservationModel.id, PriceObservationModel.source_id,
                       PriceObservationModel.evidence_id, PriceObservationModel.identity_id,
                       PriceObservationModel.price, PriceObservationModel.price_low,
                       PriceObservationModel.price_high, PriceObservationModel.observation_date,
                       PriceObservationModel.publication_date,
                       PriceObservationModel.available_from, PriceObservationModel.weight)
                .order_by(PriceObservationModel.available_from, PriceObservationModel.id)
            ).all()
            in_currency = {}
            if wanted:
                in_currency = {
                    row.observation_id: row for row in session.execute(
                        select(PriceInCurrencyModel.observation_id, PriceInCurrencyModel.price,
                               PriceInCurrencyModel.price_low, PriceInCurrencyModel.price_high,
                               PriceInCurrencyModel.fx_rate, PriceInCurrencyModel.fx_rate_date)
                        .where(PriceInCurrencyModel.currency == wanted)).all()
                }
            wanted_keys = {part.strip() for part in identity_keys.split(",") if part.strip()}
            if lean:
                # One entry per market, each reading a 4-tuple:
                #   [observation_date, publication_date, source_id, 1 if it has a price else 0]
                # The two dates as ten characters, not as timestamps - see `_just_the_day`.
                # `publication_date` is carried because `best_row` falls back to it when a reading
                # has no observation date; leaving it out would quietly change which market a page
                # opens on.
                by_market: dict[str, list] = {}
                for price in rows:
                    identity = identities.get(price.identity_id)
                    if identity is None:
                        continue
                    key = _identity_key(identity)
                    if wanted_keys and key not in wanted_keys:
                        continue
                    by_market.setdefault(key, []).append(
                        [_just_the_day(price.observation_date),
                         _just_the_day(price.publication_date), price.source_id,
                         0 if price.price is None else 1])
                return [{"identity_key": key, "readings": readings}
                        for key, readings in by_market.items()]
            if compact:
                return [
                    {
                        "observation_id": price.id,
                        "source_id": price.source_id,
                        "identity_id": price.identity_id,
                        **_priced(price, identity, wanted, in_currency.get(price.id)),
                        "observation_date": price.observation_date,
                        "publication_date": price.publication_date,
                        "available_from": price.available_from,
                        "weight": price.weight,
                    }
                    for price in rows
                    for identity in (identities.get(price.identity_id),) if identity is not None
                    if not wanted_keys or _identity_key(identity) in wanted_keys
                ]
            return [
                {
                    "observation_id": price.id,
                    "source_id": price.source_id,
                    "evidence_id": price.evidence_id,
                    "identity_id": identity.id,
                    "identity_key": _identity_key(identity),
                    "product_family": identity.product_family,
                    "product_grade_or_chemistry": identity.product_grade_or_chemistry,
                    # Origin is what says whether this price can be sold to a customer entitled to a
                    # given country of manufacture. Without it the observation feed cannot be scoped.
                    "origin": identity.origin,
                    "destination": identity.destination,
                    "market_geography": identity.market_geography,
                    "commercial_basis": identity.commercial_basis,
                    "packaging": identity.packaging,
                    "currency": identity.currency,
                    "unit": identity.unit,
                    # Without this every consumer defaults to "market_price", which would let HTC's
                    # own purchase history be read as a published assessment.
                    "observation_type": identity.observation_type,
                    **_priced(price, identity, wanted, in_currency.get(price.id)),
                    "observation_date": price.observation_date,
                    "publication_date": price.publication_date,
                    "available_from": price.available_from,
                    "weight": price.weight,
                }
                for price in rows
                for identity in (identities.get(price.identity_id),) if identity is not None
                if not wanted_keys or _identity_key(identity) in wanted_keys
            ]

    @application.get("/features")
    def features():
        with session_factory() as session:
            rows = list(
                session.scalars(
                    select(FeatureObservationModel).order_by(
                        FeatureObservationModel.available_from, FeatureObservationModel.id
                    )
                )
            )
            return [
                {
                    "feature_id": row.id,
                    "source_id": row.source_id,
                    "evidence_id": row.evidence_id,
                    "feature_type": row.feature_type,
                    "value": row.value,
                    "data": row.data,
                    "available_from": row.available_from,
                }
                for row in rows
            ]

    @application.get("/trade-layer")
    def trade_layer():
        """Every trade-layer variable the forecasting engine can ask for, one row per series.

        Each row names its source, material, flow, partner, frequency, Price / Data Basis, the
        forecast products it is a CANDIDATE input to (never assumed predictive), and its span.
        """
        from app.trade import materials as trade_materials
        with session_factory() as session:
            rows = list(session.scalars(select(FeatureObservationModel).where(
                FeatureObservationModel.source_id.like("comext_%") | FeatureObservationModel.source_id.like("comtrade_%"))))
        series: dict[str, dict] = {}
        for row in rows:
            data = row.data or {}
            period = str(data.get("observation_period") or data.get("observation_month") or "")
            entry = series.setdefault(row.feature_type, {
                "feature_type": row.feature_type, "source_id": row.source_id,
                "material": data.get("material"), "flow": data.get("flow"), "partner": data.get("partner"),
                "reporter": data.get("reporter"), "frequency": data.get("frequency"),
                "data_basis": data.get("data_basis"), "primary_source": data.get("primary_source"),
                "cn_code": data.get("cn_code"), "hs_code": data.get("hs_code"),
                "candidate_for": list(getattr(trade_materials.BY_KEY.get(data.get("material")), "forecast_targets", ()) or ()),
                "observations": 0, "with_value": 0, "first_period": None, "last_period": None})
            entry["observations"] += 1
            if row.value is not None:
                entry["with_value"] += 1
            if period:
                entry["first_period"] = min(filter(None, [entry["first_period"], period]))
                entry["last_period"] = max(filter(None, [entry["last_period"], period]))
        return sorted(series.values(), key=lambda e: e["feature_type"])

    @application.get("/trade-layer/series")
    def trade_layer_series(feature_type: str):
        """One trade-layer variable, oldest period first, with the record each value came from."""
        with session_factory() as session:
            rows = list(session.scalars(select(FeatureObservationModel).where(
                FeatureObservationModel.feature_type == feature_type)))
        out = [{"period": str((r.data or {}).get("observation_period") or (r.data or {}).get("observation_month") or ""),
                "value": r.value, "available_from": r.available_from, "source_id": r.source_id,
                "evidence_id": r.evidence_id, "data": r.data} for r in rows]
        return sorted(out, key=lambda r: r["period"])

    @application.get("/imports")
    def imports():
        """What each startup import actually did.

        Exists because a silent failure and an empty dataset are indistinguishable on screen, and
        the screen is where anyone would notice. A caller can now tell "nothing to load" from
        "could not load".
        """
        return import_status

    @application.get("/coverage")
    def coverage(days: int = 30):
        """Which trading days each daily series is missing.

        A gap used to be invisible: the scheduler recovers the source on the next run but not the
        day, so thirty-three missed trading days, 13 July to 26 August 2026, sat in the store
        unreported and quietly halved a correlation sample.
        """
        from app.history.backfill import coverage_summary, daily_coverage

        rows = daily_coverage(session_factory, window_days=days)
        return {"summary": coverage_summary(rows), "series": rows}

    @application.get("/reference-series")
    def reference_series(internal: bool = False):
        """Non-price history for the correlation work.

        A separate endpoint from /observations because it is a separate kind of evidence: every row
        here carries its own unit and its quality tier, and none of it may be shown as a price.

        `internal` asks for the series HTC holds under terms that do not allow showing them on - the
        EUROPUR report chief among them. Off by default, so an older client, a cached call or a new
        screen that has not thought about rights gets the public set.
        """
        from app.reference.importer import list_reference_series

        return list_reference_series(session_factory, include_internal=bool(internal))

    @application.get("/identities")
    def identities():
        with session_factory() as session:
            rows = list(session.scalars(select(MarketIdentityModel).order_by(MarketIdentityModel.product_family, MarketIdentityModel.id)))
            return [
                {
                    "identity_id": row.id,
                    "identity_key": _identity_key(row),
                    "product_family": row.product_family,
                    "product_grade_or_chemistry": row.product_grade_or_chemistry,
                    "origin": row.origin,
                    "destination": row.destination,
                    "market_geography": row.market_geography,
                    "commercial_basis": row.commercial_basis,
                    "packaging": row.packaging,
                    "currency": row.currency,
                    "unit": row.unit,
                    "observation_type": row.observation_type,
                    "comparability_group": row.comparability_group,
                }
                for row in rows
            ]

    @application.get("/quarantine")
    def quarantine_queue(open_only: bool = True):
        """Readings that did not become markets, with the publisher's own words.

        Masterplan D, v1.2.11. A value a gated field cannot hold does not mint a market and is not
        dropped either: it is here, with the reason, the document it came from and one whole example
        reading. One row per (source, field, value) rather than per reading, so the fact a reader
        needs - this publisher writes this value and we do not know it - is not buried under a
        thousand copies of itself.
        """
        from app.persistence import quarantine as queue

        with session_factory() as session:
            rows = (queue.open_rows(session) if open_only
                    else list(session.scalars(select(QuarantinedReadingModel))))
            return queue.as_rows(rows)

    @application.get("/quality")
    def quality():
        with session_factory() as session:
            rows = list(session.scalars(select(QualityIssueModel).order_by(QualityIssueModel.created_at, QualityIssueModel.id)))
            return [
                {
                    "quality_issue_id": row.id,
                    "source_id": row.source_id,
                    "issue_type": row.issue_type,
                    "severity": row.severity,
                    "details": row.details,
                    "created_at": row.created_at,
                }
                for row in rows
            ]

    # ------------------------------------------------------------------ the investigation
    #
    # An observation we cannot explain is a case with a chain of questions against it, not a
    # reject (v0.5.42). These endpoints are what lets a person see the cases and work them: the
    # engine owns the tables, so the application asks over HTTP exactly as it does for prices.

    def _case_as_dict(session, case) -> dict:
        from app.investigation import store as cases_store

        return {
            "id": case.id, "gap": case.gap, "state": case.state,
            "observed": case.observed,
            "observed_at": case.observed_at.isoformat() if case.observed_at else None,
            "chemical": case.chemical, "geography": case.geography,
            "source_id": case.source_id, "origin_kind": case.origin_kind,
            "answer": case.answer,
            "opened_at": case.opened_at.isoformat() if case.opened_at else None,
            "updated_at": case.updated_at.isoformat() if case.updated_at else None,
            "data": case.data or {},
            "enquiries": [
                {"id": e.id, "question": e.question, "step": e.step, "asked": e.asked,
                 "capability": e.capability, "outcome": e.outcome, "found": e.found,
                 "data": e.data or {},
                 "ran_at": e.ran_at.isoformat() if e.ran_at else None}
                for e in cases_store.enquiries(session, case.id)],
            "candidates": [
                {"id": c.id, "name": c.name, "owner": c.owner, "province": c.province,
                 "capacity_kt": c.capacity_kt, "state": c.state, "why": c.why,
                 "from_source": c.from_source}
                for c in cases_store.candidates(session, case.id)],
        }

    @application.get("/investigation/cases")
    def investigation_cases(state: str = "", gap: str = ""):
        """Every case, with its chain and its suspects. Filterable by state and by gap."""
        from app.investigation import store as cases_store

        with session_factory() as session:
            found = cases_store.cases(session, state=state or None, gap=gap or None)
            return [_case_as_dict(session, case) for case in found]

    @application.post("/investigation/open")
    def investigation_open():
        """Open cases from what the engine already holds, then run the enquiries that are free.

        Idempotent: the same observation does not open a second case, so this can be pressed as
        often as anybody likes.
        """
        from app.investigation import opening
        from app.investigation import store as cases_store

        with session_factory() as session:
            from_events = opening.from_unattributed_events(session)
            from_prices = opening.from_implausible_levels(session)
            session.commit()
            worked = 0
            for case in cases_store.cases(session, state="OPEN"):
                worked += opening.work_the_free_enquiries(session, case.id)
            session.commit()
            misfiled = opening.assessments_misfiled(session)
            return {"from_events": len(from_events), "from_prices": len(from_prices),
                    "enquiries_run": worked, "assessments_misfiled": len(misfiled),
                    "cases": len(cases_store.cases(session))}

    @application.post("/investigation/cases/{case_id}/work")
    def investigation_work(case_id: str):
        """Run every pending enquiry on this case that needs nothing but what we already hold."""
        from app.investigation import opening

        with session_factory() as session:
            try:
                ran = opening.work_the_free_enquiries(session, case_id)
            except KeyError:
                raise HTTPException(status_code=404, detail="no such case")
            session.commit()
            return {"enquiries_run": ran}

    @application.post("/investigation/cases/{case_id}/close")
    def investigation_close(case_id: str, payload: dict):
        """End a case on an answer or on a reason. There is no ending that says nothing."""
        from app.investigation import store as cases_store

        with session_factory() as session:
            try:
                case = cases_store.close(session, case_id,
                                         state=str(payload.get("state") or ""),
                                         answer=str(payload.get("answer") or ""))
            except KeyError:
                raise HTTPException(status_code=404, detail="no such case")
            except ValueError as problem:
                raise HTTPException(status_code=400, detail=str(problem))
            session.commit()
            return {"id": case.id, "state": case.state, "answer": case.answer}

    @application.post("/investigation/cases/{case_id}/reopen")
    def investigation_reopen(case_id: str):
        """Put a closed case back to work. A case is never deleted, so it can always come back."""
        from app.db.models import InvestigationCaseModel

        with session_factory() as session:
            case = session.get(InvestigationCaseModel, case_id)
            if case is None:
                raise HTTPException(status_code=404, detail="no such case")
            case.state = "OPEN"
            case.answer = None
            session.commit()
            return {"id": case.id, "state": case.state}

    @application.post("/investigation/candidates/{candidate_id}/rule-out")
    def investigation_rule_out(candidate_id: str, payload: dict):
        """Rule a suspect out, saying what ruled it out. Nothing is removed."""
        from app.investigation import store as cases_store

        why = str(payload.get("why") or "").strip()
        if not why:
            raise HTTPException(status_code=400,
                                detail="say what ruled it out; a suspect is not dropped silently")
        with session_factory() as session:
            try:
                cases_store.rule_out(session, candidate_id, why)
            except KeyError:
                raise HTTPException(status_code=404, detail="no such candidate")
            session.commit()
            return {"id": candidate_id, "state": "RULED_OUT", "why": why}

    @application.get("/events")
    def events():
        with session_factory() as session:
            rows = list(session.scalars(select(EventRecordModel).order_by(EventRecordModel.available_from, EventRecordModel.id)))
            return [
                {
                    "event_id": row.id,
                    "source_id": row.source_id,
                    "evidence_id": row.evidence_id,
                    "event_type": row.event_type,
                    "material": row.material,
                    "state": row.state,
                    "data": row.data,
                    "available_from": row.available_from,
                }
                for row in rows
            ]

    def _identity_by_key(identity_key: str) -> MarketIdentityModel | None:
        with session_factory() as session:
            return next(
                (row for row in session.scalars(select(MarketIdentityModel)) if _identity_key(row) == identity_key),
                None,
            )

    # The one name the forecast's own readings are filed under. They are not a publisher's prints;
    # they are the consolidated series, which is one value per day whoever contributed to it.
    # Read in app.forecasting.store_series since WP2, so the offline review reads the same rows the
    # page does; `store_series.CONSOLIDATED_SOURCE` is the name they are filed under.
    def _forecast_rows(identity_id: str) -> list[dict]:
        return store_series.forecast_rows(session_factory, identity_id)

    def _contributing_sources(session, identity_id: str) -> int:
        """How many publishers stand behind this identity's consolidated series.

        The gate used to count distinct source ids in the rows the forecast is built on. Those rows
        come from `_consolidated_rows`, which stamps every row with one literal id, because
        consolidation is precisely the step that collapses many publishers into one governed value.
        So the count was always 1, the threshold is 2, and every market read BLOCKED for ever - six
        publishers stood behind China TDI on 19 September 2026 while the screen said one.

        A reference point records the observations it was built from. That is where the publishers
        are, and that is what is counted here.
        """
        import json as _json

        observation_ids: set[str] = set()
        for point in session.scalars(
                select(MarketReferencePointModel)
                .where(MarketReferencePointModel.identity_id == identity_id)):
            raw = point.observation_ids
            if not raw:
                continue
            try:
                ids = _json.loads(raw) if isinstance(raw, str) else list(raw)
            except (TypeError, ValueError):
                continue
            observation_ids.update(str(x) for x in ids if x)
        if not observation_ids:
            # No consolidated series yet: the raw rows are the evidence, and they carry their own
            # source ids.
            return len({row.source_id for row in session.scalars(
                select(PriceObservationModel)
                .where(PriceObservationModel.identity_id == identity_id)) if row.source_id})
        sources = {row.source_id for row in session.scalars(
            select(PriceObservationModel)
            .where(PriceObservationModel.id.in_(observation_ids))) if row.source_id}
        return len(sources)

    @application.get("/readiness")
    def readiness(identity_key: str):
        identity = _identity_by_key(identity_key)
        if identity is None:
            raise HTTPException(status_code=404, detail="identity not found")
        report = assess_readiness(_forecast_rows(identity.id)).to_dict()
        with session_factory() as session:
            sources = _contributing_sources(session, identity.id)
        report["independent_sources"] = sources
        report["reasons"] = [r for r in report["reasons"] if not r.startswith("independent_sources")]
        if sources < 2:
            report["reasons"].append(f"independent_sources {sources} < 2")
        report["ready"] = not report["reasons"]
        return report

    @application.get("/backtest")
    def backtest(identity_key: str):
        identity = _identity_by_key(identity_key)
        if identity is None:
            raise HTTPException(status_code=404, detail="identity not found")
        # The same publisher count `/readiness` above uses. Without it the two gates disagreed on
        # the same market in the same render: READY beside 6 independent sources, and the controls
        # withheld because the consolidated rows count as 1.
        with session_factory() as session:
            sources = _contributing_sources(session, identity.id)
        return backtest_baselines(_forecast_rows(identity.id), independent_sources=sources)

    def _mark_what_may_be_published(payload: dict) -> None:
        """Say, per model, whether governance permits its number on a customer page.

        Masterplan H. The page runs five models and picks its own winner where a market has no
        chosen method, and `card_model` could pick a CONTROL - which is right as a benchmark and
        wrong as the published answer, because `naive_last` and `seasonal_naive` are **INACTIVE** in
        `forecast_method`. So a card could show a number from a method the review has already
        retired, and nothing on the screen said so.

        One catalogue: `forecast_method` is the authority on what may run, and this is where the page
        asks it. A control stays computed - a champion that has not been compared with "carry
        yesterday's price forward" has not been shown to be worth anything - it simply cannot be the
        number a reader is given.
        """
        from app.domain.enums import ForecastMethodStatus
        from app.forecasting.governance import ForecastMethodRegistry

        try:
            with session_factory() as session:
                permitted = {
                    str(method.method_key)
                    for method in ForecastMethodRegistry(session).list_all()
                    if getattr(method.status, "value", method.status)
                    != ForecastMethodStatus.INACTIVE.value
                }
        except Exception:               # pragma: no cover - governance must not take the page down
            return
        for model in payload.get("models") or ():
            model["may_be_published"] = str(model.get("model") or "") in permitted

    def _evidence_signature(identity, weeks):
        """The rows the forecast will read, and a fingerprint of them.

        `rows` already carries the Europe chain where there is one (masterplan F): the glue is in
        `store_series.forecast_rows` now, so this function does not build it and the signature does
        not need a separate term for it - a chain that grows by a month changes the row count and
        the newest date like any other reading would.
        """
        rows = _forecast_rows(identity.id)
        signature = (tuple(sorted(weeks or (4, 8, 12))), len(rows),
                     max((str(row.get("observation_date")) for row in rows), default=""))
        return rows, signature

    def forecast_if_kept(identity_key: str, horizons=(4, 8, 12)):
        """The forecast for this market if one is already held on today's evidence, else None.

        Reads the evidence (a few queries) and never computes: what the warm-up uses to count
        what is ready before it starts recomputing anything.
        """
        identity = _identity_by_key(identity_key)
        if identity is None:
            return None
        weeks = tuple(horizons)
        _rows, signature = _evidence_signature(identity, weeks)
        cached = _FORECAST_CACHE.get(identity_key)
        if cached is not None and cached[0] == signature:
            return cached[1]
        kept_payload = kept.load(kept_folder, identity_key, signature)
        if kept_payload is not None:
            _FORECAST_CACHE[identity_key] = (signature, kept_payload)
        return kept_payload

    def _with_governed(payload, identity_key: str, weeks):
        """The forecast payload plus what is governed for this market (WP3, decision D5).

        Added on every answer, never cached with the model list: the governed result changes when the
        production job runs or an administrator acts, not when this market's evidence changes. Per
        horizon it says NOT_GOVERNED (today's behaviour, nothing changes), PENDING, SUCCEEDED with the
        stored governed vintage and whether it is current, or FAILED with the reason and the automatic
        recommendation. A store problem here never takes the forecast off the page."""
        try:
            from app.forecasting.production import latest_governed

            governed = latest_governed(session_factory, identity_key, weeks or (4, 8, 12))
        except Exception as exc:          # pragma: no cover - the page must still answer
            logging.getLogger(__name__).warning("governed forecast not read", exc_info=True)
            governed = {"identity_key": identity_key, "error": str(exc)[:200], "horizons": {}}
        return {**payload, "governed": governed} if isinstance(payload, dict) else payload

    @application.get("/forecast")
    def forecast(identity_key: str, horizons: str = "4,8,12"):
        try:
            weeks = tuple(int(part) for part in horizons.split(",") if part.strip())
        except ValueError:
            weeks = ()
        return _with_governed(_forecast_payload(identity_key, horizons), identity_key, weeks)

    def _forecast_payload(identity_key: str, horizons: str = "4,8,12"):
        """Every model the licence gate allows, for one identity, at calendar-week horizons.

        No champion is chosen here. The endpoint reports what each model said and states plainly
        that nothing has been qualified for release yet - the three cards on the main screen have
        been empty since the application existed, and the first thing to put in them is an honest
        number rather than a confident one.
        """
        from app.forecasting.service import forecast_identity

        identity = _identity_by_key(identity_key)
        if identity is None:
            raise HTTPException(status_code=404, detail="identity not found")
        try:
            weeks = tuple(int(part) for part in horizons.split(",") if part.strip())
        except ValueError:
            raise HTTPException(status_code=400, detail="horizons must be whole weeks, comma separated")
        # Computed once per market until its evidence changes.
        #
        # A market with 151 months behind it - the published European series - is rolled through its
        # own backtest at three horizons by every model in the registry, which takes seconds rather
        # than milliseconds. The screen was waiting for that on every visit, and on 23 September 2026
        # it waited longer than its own timeout and showed the reader nothing.
        #
        # The key is the evidence itself: how many readings and the newest one. Collect a new price
        # and the fingerprint moves, so a stale forecast cannot outlive the data it was computed
        # from. The chained European months are among those readings since masterplan F, so they are
        # fingerprinted with everything else rather than counted separately.
        rows, signature = _evidence_signature(identity, weeks)
        cached = _FORECAST_CACHE.get(identity_key)
        if cached is not None and cached[0] == signature:
            return cached[1]
        # Kept from an earlier start on the same evidence (13 October 2026): a restart does not
        # recompute what has not changed.
        kept_payload = kept.load(kept_folder, identity_key, signature)
        if kept_payload is not None:
            _FORECAST_CACHE[identity_key] = (signature, kept_payload)
            return kept_payload
        payload = forecast_identity(
            rows,
            identity_key=identity_key,
            chemical=str(identity.product_family or "UNKNOWN"),
            currency=str(identity.currency or ""),
            horizons=weeks or (4, 8, 12),
        )
        _mark_what_may_be_published(payload)
        _FORECAST_CACHE[identity_key] = (signature, payload)
        kept.save(kept_folder, identity_key, signature, payload)

        # Record the claim, then settle whatever has since come due.
        #
        # Here rather than in a nightly job, because a forecast that is never served is not a claim
        # and one that is served has to be recorded at the moment it is served. Writing is idempotent
        # by as-of date, so a hundred page refreshes are one vintage. Neither step may take the
        # endpoint down: a store that cannot be written is a problem to fix, not a reason to leave
        # the screen blank.
        try:
            from app.forecasting.vintages import record, settle

            record(session_factory, payload)
            settle(session_factory, identity_key, _closed_periods(rows, payload))
        except Exception:       # pragma: no cover - recording must never break the forecast
            logging.getLogger(__name__).warning("forecast vintage not recorded", exc_info=True)
        return payload

    # `_europe_chain` was here: it built the published-series chain inside this handler and handed
    # it to `forecast_identity(chained=...)`, which gave it to one model. That is the defect
    # masterplan F removes - the glue is at the series layer now, in
    # `europe_chain.continued`, read through `store_series.forecast_rows` by the page, the governed
    # production run, the review job and the overrides alike. `_europe_markets` went with it; the
    # same query lives in `continued`, where the chain is built.

    def _closed_periods(rows: list[dict], payload: dict) -> dict:
        """The actual price of each period, bucketed exactly as the models bucketed it.

        The same bucketing function the forecast used, so a vintage is never scored against a
        differently-built version of the period it named. Scoring a monthly forecast against a single
        day's print that happened to fall in that month would be marking it on a different question.
        """
        from app.forecasting.service import MONTHLY, bucket

        # `chosen` is None when no frequency cleared its own density test, and the service then
        # falls back to monthly and forecasts anyway. Reading `chosen` alone therefore returned an
        # empty dict for exactly those markets, so `settle` was handed nothing and no vintage was
        # ever scored. TDI China is one of them: 108 daily prints over 4,650 days, 80 monthly
        # buckets at a density of 0.52 against a threshold of 0.6. The store had been recording
        # claims since March 2026 and had settled none of them (Stefan, 24 September 2026 - "how
        # are we going to measure how good the forecasting method is").
        #
        # The fallback here is the service's own fallback, so a vintage is scored against the
        # periods the model was built on and never against a differently-bucketed version of them.
        frequency = (payload.get("resolution") or {}).get("chosen") or MONTHLY
        return bucket(rows, frequency)

    def _warm_forecasts() -> None:
        """Compute every priced market's forecast, in this process, one after another.

        In-process on purpose. Asking for them over HTTP from a helper meant a second process, a
        socket, a client timeout and a proxy setting between the work and the gauge, and it hung
        twice in one morning. Here the only thing between them is a thread.
        """
        # Only the markets the Forecast page can offer: a market price with a reading in the
        # forecast years, the page's own rule (FORECAST_FROM_YEAR in app.py, priced_since). On
        # 13 October 2026 the customs history raised the count from 105 to 285 identities, 123 of
        # them history only, and the warm-up ran for twenty minutes with every page request queued
        # behind it (/references took 70 s). A market not warmed is still forecast when it is asked.
        from sqlalchemy import func

        with session_factory() as session:
            newest = dict(session.execute(
                select(PriceObservationModel.identity_id, func.max(PriceObservationModel.observation_date))
                .group_by(PriceObservationModel.identity_id)).all())
            identities = [row for row in session.scalars(select(MarketIdentityModel))
                          # Every kind a customer page may show. It read `== "market_price"`,
                          # which after migration 0058 matched NOTHING - the store holds assessed,
                          # contract, deal and ask now. `is_published` is a superset of what the
                          # old literal matched (it adds the 8 contract and 28 recorded-deal
                          # identities) and never less, so the warm-up covers more, not fewer.
                          if price_kinds.is_published(row.observation_type)
                          and str(newest.get(row.id) or "")[:4] >= str(WARM_UP_FROM_YEAR)]
        # Freshest first, so the markets being priced now are ready soonest.
        identities.sort(key=lambda row: str(newest.get(row.id) or ""), reverse=True)
        keys = [_identity_key(row) for row in identities]
        with _WARMUP_LOCK:
            _WARMUP.update({"running": True, "prepared": 0, "failed": 0, "total": len(keys),
                            "started_at": datetime.utcnow().isoformat(), "finished_at": None,
                            "current": None, "current_since": None, "refreshing": 0})
        prepared = failed = 0

        def _working_on(identity_key):
            with _WARMUP_LOCK:
                _WARMUP.update({"current": identity_key,
                                "current_since": datetime.utcnow().isoformat() if identity_key else None})

        def _count(outcome_ok: bool):
            nonlocal prepared, failed
            if outcome_ok:
                prepared += 1
            else:
                failed += 1
            with _WARMUP_LOCK:
                _WARMUP.update({"prepared": prepared, "failed": failed})

        # Pass 1 (v0.5.51): a forecast kept from an earlier start, on the same evidence, is a
        # prepared forecast - counted at once. Until now the gauge counted nothing until each market
        # had been recomputed, and after a morning collection that was every market: ten minutes
        # on Stefan's screen, three days running, for forecasts the store already held.
        pending: list[str] = []
        for identity_key in keys:
            _working_on(identity_key)
            try:
                if forecast_if_kept(identity_key) is not None:
                    _count(True)
                else:
                    pending.append(identity_key)
            except Exception:       # a market that cannot even be looked up is computed in pass 2
                pending.append(identity_key)
        with _WARMUP_LOCK:
            _WARMUP["refreshing"] = len(pending)

        # Pass 2: the markets whose evidence moved since their kept forecast. Freshest first (the
        # order of `keys`). The market in hand and how long it has been there are reported by
        # /forecast-readiness, so a stall has a name; one that takes over two minutes is logged.
        for identity_key in pending:
            _working_on(identity_key)
            began = time.monotonic()
            try:
                # The model list only: the warm-up prepares what is cached, and the governed block is
                # read per request (v0.5.63) - reading it 300 times here would only slow the start.
                _forecast_payload(identity_key)
                _count(True)
            except Exception:       # one market must never hold up the rest
                logging.getLogger(__name__).warning("forecast not prepared for %s", identity_key,
                                                    exc_info=True)
                _count(False)
            took = time.monotonic() - began
            if took > SLOW_MARKET_SECONDS:
                logging.getLogger(__name__).warning("forecast for %s took %.0f s", identity_key, took)
            with _WARMUP_LOCK:
                _WARMUP["refreshing"] = max(0, _WARMUP.get("refreshing", 0) - 1)
            # A breath between markets, so a page request waiting on the interpreter gets its turn.
            time.sleep(WARM_UP_PAUSE_SECONDS)
        _working_on(None)
        with _WARMUP_LOCK:
            _WARMUP.update({"running": False, "finished_at": datetime.utcnow().isoformat()})

    def _start_warm_up() -> bool:
        with _WARMUP_LOCK:
            if _WARMUP.get("running"):
                return False
            _WARMUP["running"] = True
        threading.Thread(target=_warm_forecasts, name="forecast-warm-up", daemon=True).start()
        return True

    # How far back the live end is re-converted on each run. A reading taken in the morning is
    # converted at the previous day's fixing because the ECB had not published yet; two weeks later
    # it has, and this picks it up. Stefan, 27 September 2026, chose to live with the timing rather
    # than hold readings back for a rate.
    REDO_DAYS = 14

    def _convert_readings_now() -> dict:
        with session_factory() as session:
            counted = convert_readings(
                session, redo_from=date.today() - timedelta(days=REDO_DAYS))
            session.commit()
        return counted

    @application.post("/prices/convert")
    def prices_convert():
        """Fill in every reading's value in every currency. What the collection run calls when it
        finishes, so the morning's readings are on the page in whatever currency is selected."""
        return {"currencies": list(CURRENCIES), **_convert_readings_now()}

    @application.post("/import")
    def import_document(payload: dict = Body(...)):
        """Store a document and the rows a person confirmed off it.

        The engine owns the store, so the import comes here rather than writing from the page. What
        arrives is the file itself and the rows as they were approved on screen; nothing in the file
        is re-read, because a re-read could store a number different from the one approved.

        Stefan, 5 October 2026, on the rights class: no default, stated per document, and the engine
        refuses the import without it.
        """
        import base64

        from app.importing.service import ConfirmationMissing, ConfirmedBatch, store

        try:
            content = base64.b64decode(str(payload.get("content_base64") or ""), validate=True)
        except Exception:
            raise HTTPException(status_code=400, detail="the file did not arrive intact")
        if not content:
            raise HTTPException(status_code=400, detail="no file was sent")
        rows = payload.get("rows") or []
        if not rows:
            raise HTTPException(status_code=400, detail="no confirmed rows were sent")
        batch = ConfirmedBatch(
            file_name=str(payload.get("file_name") or "document"),
            content=content,
            publisher=str(payload.get("publisher") or ""),
            rights_class=str(payload.get("rights_class") or ""),
            rows=rows,
        )
        try:
            result = application.state.market_data_service_import(batch)
        except ConfirmationMissing as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return {"source_id": batch.source_id, "file_name": batch.file_name,
                "state": result.state.value, "stored": len(result.records),
                "error": result.error}

    @application.post("/intelligence/events")
    def receive_intelligence(payload: dict = Body(...)):
        """Developments a person confirmed off one of HTC Global's own reports.

        The page used to write these to a file beside the application, which the forecaster never
        reads. They come here now, into the same `event_record` table the scraping lane fills, so
        what a person read off a briefing counts for a forecast like anything else.
        """
        from app.intelligence.inbox import receive

        source_id = str(payload.get("source_id") or "")
        events = payload.get("events") or []
        if not events:
            raise HTTPException(status_code=400, detail="no events were sent")
        return receive(application.state.session_factory, source_id, events)

    @application.get("/intelligence/events")
    def intelligence_events(source_id: str = ""):
        """What this lane has stored, so the page can show that an import arrived."""
        from app.intelligence.inbox import held

        return held(application.state.session_factory, source_id)

    @application.get("/forecast-readiness")
    def forecast_readiness():
        """How far the forecasts have been prepared. What the gauge reads."""
        with _WARMUP_LOCK:
            return dict(_WARMUP)

    @application.post("/forecast-warm-up")
    def forecast_warm_up():
        """Prepare every market's forecast now. Returns at once; the work runs behind it."""
        return {"started": _start_warm_up(), **forecast_readiness()}

    # Prepared at startup, so the first reader of the day waits for nothing. Off unless asked for,
    # because a test that builds an application does not want a warm-up thread behind it.
    if os.getenv("ENGINE_HOUSEKEEPING", "1") == "0":
        # A one-source collector run or another short-lived tool (v0.5.51). `app.cli` imports this
        # module, and until now every `collect --source X` child ran the whole housekeeping -
        # history imports, reference packs, every consolidated series, the bootstrap readings -
        # against Supabase before it fetched one page, and was killed at its 90-second limit
        # inside that housekeeping. Forty-one sources, nothing collected, "one source is slow".
        # Housekeeping belongs to the one long-lived engine; a child does its one job.
        import_status["housekeeping"] = {"status": "skipped", "reason": "ENGINE_HOUSEKEEPING=0"}
    elif os.getenv("FORECAST_WARM_UP") == "1":
        # The launcher's mode: answer first, housekeep behind it (see _housekeeping).
        threading.Thread(target=_housekeeping, name="startup-housekeeping", daemon=True).start()
    else:
        # Tests and one-off tools build the application and expect the store settled when it returns.
        _housekeeping()

    @application.get("/transactions")
    def transactions(chemical: str = "", geography: str = "", origin: str = "",
                     internal: bool = False):
        """What HTC actually paid: its own purchase history, as its own series.

        Separate from /observations on purpose. These are transaction prices - one buyer's signed
        orders and ERP postings - and everything that builds a customer-facing market reference
        filters on market_price, so they can never leak into a governed price. What they are good for
        is sitting BESIDE a published assessment on a chart, which is how the difference between a
        negotiated price and an assessed one becomes visible instead of theoretical.

        `origin` travels with each series, because that is what makes these worth reading at all: on
        the KGI lane every tank is Chinese material, so these are Chinese-origin TDI priced into Asia
        - a different market from Chinese domestic ex-works, and one nothing else in the store
        measures.

        WITHHELD UNLESS ASKED FOR. Every row in this lane is commercially confidential - what HTC
        paid BASF, what it paid Wanhua for the same product in the same month, and the spread between
        them. Unlike /reference-series, where public packs sit beside the internal ones, there is no
        public subset here: the whole lane is internal, so a caller that does not say so gets nothing
        rather than a filtered list.

        The default is empty because the caller that forgets is the one that has to be safe. This
        series reached a customer's Forecast page on 14 September 2026 because a screen asked for it
        and the engine handed it over without a question.
        """
        from app.history.transactions import OBSERVATION_TYPE

        if not internal:
            return []

        with session_factory() as session:
            query = (select(MarketIdentityModel, PriceObservationModel)
                     .join(PriceObservationModel,
                           PriceObservationModel.identity_id == MarketIdentityModel.id)
                     .where(MarketIdentityModel.observation_type == OBSERVATION_TYPE)
                     .order_by(PriceObservationModel.observation_date))
            if chemical:
                query = query.where(MarketIdentityModel.product_family == chemical.upper())
            if geography:
                query = query.where(MarketIdentityModel.market_geography == geography)
            if origin:
                # Where the material was MADE, not where it was delivered. This is the filter that
                # makes a Thai purchase order relevant to a chart of the Chinese market.
                query = query.where(MarketIdentityModel.origin == origin)
            rows = session.execute(query).all()

        series: dict[str, dict] = {}
        for identity, observation in rows:
            key = _identity_key(identity)
            entry = series.setdefault(key, {
                "identity_key": key,
                "chemical": identity.product_family,
                "label": identity.product_grade_or_chemistry or identity.product_family,
                "origin": identity.origin or "",
                "destination": identity.destination or identity.market_geography or "",
                "basis": identity.commercial_basis or "",
                "currency": identity.currency,
                "source_id": observation.source_id,
                "points": [],
            })
            entry["points"].append({
                "date": observation.observation_date.date().isoformat(),
                "price": float(observation.price),
                # Which kind of record this is. A signed order and a month's bookkeeping are both
                # evidence and they are not the same evidence, so the screen can say which it drew.
                "evidence": ("purchase_order" if str(observation.evidence_id).startswith("po:")
                             else "erp_posting"),
                "reference": str(observation.evidence_id).split(":")[1]
                             if str(observation.evidence_id).startswith("po:") else "",
            })
        return sorted(series.values(), key=lambda item: (item["chemical"], item["label"]))

    @application.get("/forecast-vintages")
    def forecast_vintages(identity_key: str, model: str = "", horizon_weeks: int = 0,
                          limit: int = 200):
        """Every forecast this identity was given, as it was given, newest first.

        The audit trail. A claim that cannot be looked up afterwards is not a claim, and until this
        existed none of them could be.
        """
        from app.forecasting.vintages import history

        return history(session_factory, identity_key, model_key=model,
                       horizon_weeks=horizon_weeks, limit=limit)

    @application.get("/forecast-performance")
    def forecast_performance(identity_key: str = "", state: str = ""):
        """What the recorded claims turned out to be worth: counts, per model, per horizon.

        Different evidence from /backtest, which refits the models on today's history. This reports
        only forecasts that were actually made and have since come due - a record that cannot improve
        retrospectively because the models got better.
        """
        from app.forecasting.vintages import CANDIDATE, CONTROL, RELEASED, performance

        states = tuple(part.strip().upper() for part in state.split(",") if part.strip()) \
            or (RELEASED, CANDIDATE, CONTROL)
        return performance(session_factory, identity_key, states=states)

    @application.get("/reference/as-of")
    def reference_as_of(identity_key: str, cutoff: datetime):
        with session_factory() as session:
            identity = next(
                (row for row in session.scalars(select(MarketIdentityModel)) if _identity_key(row) == identity_key),
                None,
            )
        if identity is None:
            raise HTTPException(status_code=404, detail="identity not found")
        result = application.state.market_data_service.reference_as_of(identity.id, cutoff)
        if result is None:
            raise HTTPException(status_code=404, detail="reference not found at cutoff")
        result["identity_key"] = identity_key
        return result

    @application.get("/reference")
    def reference(identity_key: str):
        with session_factory() as session:
            identity = next(
                (row for row in session.scalars(select(MarketIdentityModel)) if _identity_key(row) == identity_key),
                None,
            )
            if identity is None:
                raise HTTPException(status_code=404, detail="identity not found")
            point = session.scalar(
                select(MarketReferencePointModel)
                .where(MarketReferencePointModel.identity_id == identity.id)
                .order_by(MarketReferencePointModel.reference_date.desc(), MarketReferencePointModel.version.desc())
                .limit(1)
            )
            if point is None:
                raise HTTPException(status_code=404, detail="reference not found")
            return {
                "identity_id": identity.id,
                "identity_key": identity_key,
                "reference_date": point.reference_date,
                "reference_value": point.reference_value,
                "version": point.version,
                "observation_ids": point.observation_ids,
                "calculated_at": point.calculated_at,
            }

    @application.get("/references")
    def references():
        """The newest reference point for every identity, in one answer.

        `/reference` answers for one identity, and the application needs all of them to draw a
        page. It was asking one at a time: 113 HTTP round trips on every render, each of which
        loaded the whole identity table into Python to find one row by its computed key. With 70
        identities that was slow and survivable. After the ICIS import it was 113, and the page
        stopped finishing.

        One query, one pass, one answer. Identities with no reference point are simply absent, the
        same as a 404 from the single-identity endpoint.
        """
        with session_factory() as session:
            keys = {row.id: _identity_key(row) for row in session.scalars(select(MarketIdentityModel))}
            newest: dict[str, dict] = {}
            for point in session.scalars(
                select(MarketReferencePointModel).order_by(
                    MarketReferencePointModel.reference_date.desc(),
                    MarketReferencePointModel.version.desc(),
                )
            ):
                if point.identity_id in newest or point.identity_id not in keys:
                    continue
                newest[point.identity_id] = {
                    "identity_id": point.identity_id,
                    "identity_key": keys[point.identity_id],
                    "reference_date": point.reference_date,
                    "reference_value": point.reference_value,
                    "version": point.version,
                    "observation_ids": point.observation_ids,
                    "calculated_at": point.calculated_at,
                }
            return list(newest.values())

    @application.get("/reference/governed-series")
    def governed_reference_series(identity_key: str):
        """The consolidated series for one identity, with the spread of what each source printed.

        `/reference` answers with the newest point only, which is all a headline needs. The customer
        page needs the history as well, and it needs it on the same basis as the forecast: net of
        China's 13% VAT, one value per source per date, dates never blended.

        Before this existed the page built its own line straight off the raw prints, which are stored
        exactly as the publisher wrote them - VAT included. So the same market read USD 2,698 on one
        page and USD 2,387 on the next, 13% apart, and both were labelled the market price. One
        series, served once, is the only way that stays true.

        `low` and `high` are the lowest and highest of the per-source values behind that date, net of
        tax like the value itself, so the range always brackets the number it is quoted against.
        """
        with session_factory() as session:
            identity = next(
                (row for row in session.scalars(select(MarketIdentityModel)) if _identity_key(row) == identity_key),
                None,
            )
            if identity is None:
                raise HTTPException(status_code=404, detail="identity not found")

            from app.reference.series import (
                _rights_by_source, contributors_by_date, net_price,
            )

            rights = _rights_by_source(session)
            rows = list(session.scalars(
                select(PriceObservationModel)
                .where(PriceObservationModel.identity_id == identity.id)))
            in_force = {
                point.reference_date.date() if hasattr(point.reference_date, "date") else point.reference_date: point
                for point in sorted(
                    session.scalars(
                        select(MarketReferencePointModel)
                        .where(MarketReferencePointModel.identity_id == identity.id)),
                    key=lambda row: (row.reference_date, row.version))
            }

            points = []
            for day, contributors in sorted(contributors_by_date(rows).items()):
                point = in_force.get(day)
                if point is None:
                    continue
                by_source: dict[str, list[float]] = {}
                for row in contributors:
                    by_source.setdefault(str(row.source_id or ""), []).append(
                        net_price(row, identity, rights))
                per_source = [sum(values) / len(values) for values in by_source.values()]
                points.append({
                    "reference_date": day.isoformat(),
                    "reference_value": point.reference_value,
                    "sources": len(per_source),
                    "low": round(min(per_source), 2),
                    "high": round(max(per_source), 2),
                    "version": point.version,
                })

            return {
                "identity_id": identity.id,
                "identity_key": identity_key,
                "currency": identity.currency,
                "unit": identity.unit,
                "points": points,
            }

    return application


LOCAL_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+pysqlite:///./data/forecasting.db")
app = create_app(database_url=LOCAL_DATABASE_URL)
