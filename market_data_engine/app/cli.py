from __future__ import annotations

import argparse
import faulthandler
import os
import sys

# The background collector kills a source that runs past its time. Asked by it, the run writes every
# thread's stack to stderr a few seconds before that, so the log says where it was rather than only
# that it was late (13 October 2026).
if os.environ.get("STEP6_STACK_DUMP_SECONDS", "").strip().isdigit():
    faulthandler.dump_traceback_later(int(os.environ["STEP6_STACK_DUMP_SECONDS"]), exit=False,
                                      file=sys.stderr)

try:  # Use the Windows/OS certificate store for TLS (publishers whose servers omit an
    import truststore  # intermediate certificate fail with certifi's bundle but load fine in Edge).

    truststore.inject_into_ssl()
except Exception:  # pragma: no cover - optional hardening
    pass
import json

# A CLI run is one job - a collection, a coverage report, an export - not an engine. Importing app.main
# builds the application; this keeps that build from also running the engine's housekeeping
# (v0.5.51; see create_app). The engine itself is started by uvicorn, never through here.
os.environ.setdefault("ENGINE_HOUSEKEEPING", "0")
import re
from typing import Iterable

from app.ingestion.runner import IngestionRunResult
from app.sources.connectors.echemi_tdi import EchemiTDIConnector
from app.sources.connectors.echemi_product import EchemiProductConnector
from app.sources.connectors.sci99_tdi import Sci99TDIConnector
from app.sources.connectors.sci99_pmdi import Sci99PMDIConnector
from app.sources.connectors.baiinfo_tdi import BaiinfoTDIConnector
from app.sources.connectors.businessanalytiq_tdi import BusinessAnalytiqTDIConnector
from app.sources.connectors.pudaily_tdi import PUDailyTDIConnector
from app.trade.connectors import trade_connector_for
from app.sources.connectors.imarc_price import ImarcPriceConnector
from app.sources.connectors.procurement_resource_price import ProcurementResourcePriceConnector
from app.sources.connectors.chemanalyst_tdi import ChemAnalystTDIConnector
from app.sources.connectors.longzhong_tdi import LongzhongTDIConnector
from app.sources.connectors.oilchem_product import OilchemProductConnector
from app.sources.connectors.jlc_channel import JlcChannelConnector
from app.sources.connectors.sci99_product import Sci99ProductConnector
from app.sources.connectors.sunsirs_product import SunSirsProductConnector
from app.sources.connectors.source_monitor import SourceMonitorConnector
from app.sources.connectors.sunsirs_tdi import SunSirsTDIConnector
from app.sources.connectors.echemi_news import EchemiNewsConnector
from app.sources.connectors.mysteel_polyester import MysteelPolyesterConnector
from app.sources.connectors.producer_press import covestro_press, wanhua_news
from app.sources.connectors.exchange_filings import ExchangeFilingsConnector
from app.sources.registry import SourceRegistry


_CONNECTORS = {
    "sunsirs_tdi": SunSirsTDIConnector,
    "echemi_tdi": EchemiTDIConnector,
    "longzhong_tdi": LongzhongTDIConnector,
    "sci99_tdi": Sci99TDIConnector,
    "baiinfo_tdi": BaiinfoTDIConnector,
    "businessanalytiq_tdi": BusinessAnalytiqTDIConnector,
    "pudaily_tdi": PUDailyTDIConnector,
    "chemanalyst_tdi": ChemAnalystTDIConnector,
    "sci99_pmdi": Sci99PMDIConnector,
    # The first live Chinese polyester polyol, 29 September 2026.
    "longzhong_polyester": MysteelPolyesterConnector,
    # The market-intelligence sourcing lane: no prices, only what is happening in the market.
    "echemi_news": EchemiNewsConnector,
    "covestro_press": covestro_press,
    "wanhua_news": wanhua_news,
}

# Shanghai filings, one connector per issuer, built from the issuer table so adding a producer is
# a one-line change in exchange_filings.ISSUERS rather than three edits here.
_FILING_RE = re.compile(r"^sse_(?P<code>\d{6})_filings$")


def connector_for(source_id: str):
    if source_id in _CONNECTORS:
        return _CONNECTORS[source_id]()
    filing = _FILING_RE.match(source_id)
    if filing:
        return ExchangeFilingsConnector(filing.group("code"))
    # Free European monthly prices (21 September 2026).
    if source_id == "imarc_tdi":
        return ImarcPriceConnector(source_id, "https://www.imarcgroup.com/toluene-diisocyanate-pricing-report",
                                   product="TDI", label="TDI")
    if source_id == "imarc_mdi":
        # Polymeric MDI: in flexible foam MDI means pMDI (Stefan, 22 September 2026).
        return ImarcPriceConnector(source_id, "https://www.imarcgroup.com/methylene-diphenyl-diisocyanate-pricing-report",
                                   product="Polymeric MDI", label="MDI")
    # The European feedstocks, registered 1 October 2026. Both publishers print every region on
    # one page, so each of these also gives the Chinese feedstock a second publisher.
    if source_id == "imarc_benzene":
        return ImarcPriceConnector(source_id, "https://www.imarcgroup.com/benzene-pricing-report",
                                   product="Benzene", label="benzene")
    if source_id == "imarc_toluene":
        return ImarcPriceConnector(source_id, "https://www.imarcgroup.com/toluene-pricing-report",
                                   product="Toluene", label="toluene")
    if source_id == "imarc_propylene_oxide":
        return ImarcPriceConnector(source_id, "https://www.imarcgroup.com/propylene-oxide-pricing-report",
                                   product="Propylene oxide", label="propylene oxide")
    if source_id == "imarc_adipic_acid":
        return ImarcPriceConnector(source_id, "https://www.imarcgroup.com/adipic-acid-pricing-report",
                                   product="Adipic acid", label="adipic acid")
    if source_id == "businessanalytiq_adipic_acid":
        return BusinessAnalytiqTDIConnector(
            source_id, "https://businessanalytiq.com/procurementanalytics/index/adipic-acid-price-index/",
            product="Adipic acid")
    if source_id == "businessanalytiq_pure_mdi":
        return BusinessAnalytiqTDIConnector(
            source_id,
            "https://businessanalytiq.com/procurementanalytics/index/methylene-diphenyl-diisocyanate-pure-price-index/",
            product="Pure MDI")
    if source_id == "businessanalytiq_benzene":
        return BusinessAnalytiqTDIConnector(
            source_id, "https://businessanalytiq.com/procurementanalytics/index/benzene-price-index/",
            product="Benzene")
    if source_id == "businessanalytiq_toluene":
        return BusinessAnalytiqTDIConnector(
            source_id, "https://businessanalytiq.com/procurementanalytics/index/toluene-price-index/",
            product="Toluene")
    if source_id == "businessanalytiq_propylene_oxide":
        return BusinessAnalytiqTDIConnector(
            source_id, "https://businessanalytiq.com/procurementanalytics/index/propylene-oxide-price-index/",
            product="Propylene oxide")
    # The public trade-data layer (Stefan, 13 October 2026): one Eurostat source per material, the
    # five Comext series collected since 1 October keep their source ids, and UN Comtrade by window.
    trade = trade_connector_for(source_id)
    if trade is not None:
        return trade
    if source_id == "procurementresource_tdi":
        return ProcurementResourcePriceConnector(
            source_id, "https://www.procurementresource.com/resource-center/toluene-diisocyanate-price-trends",
            # Germany stays Germany. Stefan ruled on 2 October 2026 that the German figure is its
            # own market rather than a line inside TDI Europe, and this replaces the ChemAnalyst
            # page that ruling was about.
            product="Toluene Diisocyanate", countries={"Germany": "Germany"})
    if source_id == "procurementresource_polyether":
        return ProcurementResourcePriceConnector(
            source_id, "https://www.procurementresource.com/resource-center/polyether-polyol-price-trends",
            # Germany is the European market; the USA row is North America (Stefan, 23 September
            # 2026). The page prints both, and both carry their own month.
            product="Polyether Polyol", countries={"Germany": "Europe", "USA": "North America"})
    if source_id == "echemi_pmdi":
        return EchemiProductConnector(
            source_id=source_id, source_url="https://www.echemi.com/productsInformation/pd20170601172413283-polymericmdi.html",
            # No grade: PM200 is ECHEMI's name for polymeric MDI, not a distinct material.
            product="Polymeric MDI", grade=None, geography="China", basis="EXW",
        )
    if source_id == "echemi_polyol":
        return EchemiProductConnector(
            source_id=source_id, source_url="https://www.echemi.com/productsInformation/pd20170105140233784-polyether-polyol.html",
            # No grade, for the same reason as polymeric MDI above: MW3000 is ECHEMI's product
            # name, not a material the market prices apart from what Longzhong calls soft-foam
            # polyether. A grade here is a second identity for one series.
            product="Polyether polyol MW3000", grade=None, geography="China", basis="EXW",
        )
    # Feedstocks: collected for the correlation work, not sold as a series.
    if source_id == "echemi_toluene":
        return EchemiProductConnector(
            source_id=source_id,
            source_url="https://www.echemi.com/productsInformation/temppid160704000607-toluene.html",
            product="Toluene", grade=None, geography="China", basis="EXW",
        )
    if source_id == "sunsirs_toluene":
        return SunSirsProductConnector(
            source_id=source_id, source_url="https://www.sunsirs.com/uk/prodetail-177.html",
            product="Toluene", geography="China", basis="EXW",
        )
    if source_id == "sunsirs_benzene":
        return SunSirsProductConnector(
            source_id=source_id, source_url="https://www.sunsirs.com/uk/prodetail-120.html",
            product="Benzene", geography="China", basis="EXW",
        )
    if source_id == "longzhong_pmdi":
        return OilchemProductConnector(
            source_id=source_id, source_url="https://list.oilchem.net/117/6600/",
            product="Polymeric MDI", grade=None, geography="China", basis="EXW", title_token="聚合MDI",
            mysteel_family="PMDI",
        )
    if source_id == "sci99_po":
        return Sci99ProductConnector(
            source_id=source_id, old_id="399",
            product="Propylene oxide", grade=None, geography="China", basis="EXW",
        )
    if source_id == "sunsirs_po":
        return SunSirsProductConnector(
            source_id=source_id, source_url="https://www.sunsirs.com/uk/prodetail-438.html",
            product="Propylene oxide", geography="China", basis="EXW",
        )
    # The four feedstocks SunSirs carried alone. Monitor ids from the discovery run of
    # 2 October 2026, which returned 375 for TDI - the id already in use - as its own check.
    if source_id == "sci99_benzene":
        return Sci99ProductConnector(
            source_id=source_id, old_id="377",
            product="Benzene", grade=None, geography="China", basis="EXW",
        )
    if source_id == "sci99_toluene":
        return Sci99ProductConnector(
            source_id=source_id, old_id="374",
            product="Toluene", grade=None, geography="China", basis="EXW",
        )
    if source_id == "sci99_diethylene_glycol":
        return Sci99ProductConnector(
            source_id=source_id, old_id="507",
            product="Diethylene glycol", grade=None, geography="China", basis="EXW",
        )
    if source_id == "sci99_adipic_acid":
        return Sci99ProductConnector(
            source_id=source_id, old_id="378",
            product="Adipic acid", grade=None, geography="China", basis="EXW",
        )
    if source_id == "sci99_eo":
        return Sci99ProductConnector(
            source_id=source_id, old_id="937",
            product="Ethylene oxide", grade=None, geography="China", basis="EXW",
        )
    if source_id == "sunsirs_eo":
        return SunSirsProductConnector(
            source_id=source_id, source_url="https://www.sunsirs.com/uk/prodetail-856.html",
            product="Ethylene oxide", geography="China", basis="EXW",
        )
    if source_id == "sunsirs_adipic_acid":
        return SunSirsProductConnector(
            source_id=source_id, source_url="https://www.sunsirs.com/uk/prodetail-837.html",
            product="Adipic acid", geography="China", basis="EXW",
        )
    if source_id == "sunsirs_deg":
        return SunSirsProductConnector(
            source_id=source_id, source_url="https://www.sunsirs.com/uk/prodetail-1332.html",
            product="Diethylene glycol", geography="China", basis="EXW",
        )
    # The polyester feedstocks that had no Chinese price (v1.2.117). Ethylene glycol and maleic
    # anhydride are read from SCI99 alone: SunSirs sat 6% and 19% away from it on the same days,
    # and a consolidated value of two publishers that far apart is a price neither printed. DMT
    # is not here because neither publisher prices it.
    if source_id == "sci99_ethylene_glycol":
        return Sci99ProductConnector(
            source_id=source_id, old_id="367",
            product="Ethylene glycol", grade=None, geography="China", basis="EXW",
        )
    if source_id == "sci99_maleic_anhydride":
        return Sci99ProductConnector(
            source_id=source_id, old_id="114763214",
            product="Maleic anhydride", grade=None, geography="China", basis="EXW",
        )
    if source_id == "sci99_phthalic_anhydride":
        return Sci99ProductConnector(
            source_id=source_id, old_id="94703214",
            product="Phthalic anhydride", grade=None, geography="China", basis="EXW",
        )
    if source_id == "sunsirs_phthalic_anhydride":
        return SunSirsProductConnector(
            source_id=source_id, source_url="https://www.sunsirs.com/uk/prodetail-541.html",
            product="Phthalic anhydride", product_token="PA", geography="China", basis="EXW",
        )
    if source_id == "sci99_pta":
        return Sci99ProductConnector(
            source_id=source_id, old_id="368",
            product="Terephthalic acid", grade=None, geography="China", basis="EXW",
        )
    if source_id == "sunsirs_pta":
        return SunSirsProductConnector(
            source_id=source_id, source_url="https://www.sunsirs.com/uk/prodetail-356.html",
            product="Terephthalic acid", product_token="PTA", geography="China", basis="EXW",
            sector="Textile",
        )
    if source_id == "jlc_pu_channel":
        return JlcChannelConnector()
    if source_id == "sci99_polyol":
        # 软泡聚醚 - flexible-foam polyether. No grade, for the same reason as Longzhong's: the
        # grade is the publisher's product name, not a market the trade prices apart, and a grade
        # here would open a second identity for one series.
        return Sci99ProductConnector(
            source_id=source_id, old_id="759",
            product="Polyether polyol flexible foam", grade=None, geography="China", basis="EXW",
        )
    if source_id == "longzhong_polyol":
        return OilchemProductConnector(
            source_id=source_id, source_url="https://list.oilchem.net/154/6596/",
            product="Polyether polyol flexible foam", grade=None, geography="China", basis="EXW", title_token="软泡聚醚",
            mysteel_family="POLYOL",
        )
    registry = SourceRegistry.default()
    try:
        source = registry.get(source_id)
    except KeyError as exc:
        raise KeyError(f"unknown source: {source_id}") from exc
    return SourceMonitorConnector(source)


def collect_selected(service, source_ids: Iterable[str]) -> list[IngestionRunResult]:
    return [service.collect(connector_for(source_id)) for source_id in source_ids]


def collect_due(service) -> list[IngestionRunResult]:
    return collect_selected(service, service.due_source_ids())




def resolve_source_ids(service, *, source: str, due_only: bool = False) -> list[str]:
    registry_ids = [item.source_id for item in SourceRegistry.expanded().list()]
    if source not in {"all", "due"}:
        return [source]
    if source == "due" or due_only:
        return list(service.due_source_ids())
    return registry_ids


def _keep_the_pc_awake() -> None:
    """While a long review runs, ask Windows not to sleep (v0.5.67). The screen may still turn off; the
    request ends with this process, so the PC's own sleep setting is untouched and applies again the
    moment the review finishes. Nothing happens on other systems."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    except Exception:            # noqa: BLE001 - staying awake is a convenience, never a reason to fail
        pass


def _progress_to_window_and_file(line: str) -> None:
    """A review's progress line: to the window (stderr) and into the report file (stdout), so it
    can be followed from the file while it runs (v0.5.58)."""
    print(line, file=sys.stderr, flush=True)
    if not sys.stdout.isatty():
        print(line, flush=True)


def main(argv: list[str] | None = None) -> int:
    registry_ids = [item.source_id for item in SourceRegistry.expanded().list()]
    parser = argparse.ArgumentParser(prog="pu-forecasting")
    sub = parser.add_subparsers(dest="command", required=True)
    collect = sub.add_parser("collect", help="Collect one or all approved live sources")
    collect.add_argument("--source", choices=[*registry_ids, "all", "due"], default="all")
    collect.add_argument(
        "--due-only",
        action="store_true",
        help="When --source all, run only sources not yet checked today (UTC).",
    )
    bootstrap = sub.add_parser("bootstrap", help="Load governed historical observations into the local database")
    bootstrap.add_argument("--file", required=True)
    sub.add_parser("fx-refresh", help="Fetch missing ECB daily reference rates (EUR base) into the local database")
    sub.add_parser("checkpoint", help="Fold the SQLite write-ahead log back into the database file")
    backup = sub.add_parser("backup", help="Copy the database to a dated file, once a day, keeping the newest few")
    backup.add_argument("--to", required=True)
    backup.add_argument("--keep", type=int, default=7)
    signals = sub.add_parser("producer-signals-export", help="Write Step 6 producer-signal candidates into the application feed")
    signals.add_argument("--out", default=None)
    cover = sub.add_parser("coverage", help="Report the trading days each daily series is missing")
    cover.add_argument("--days", type=int, default=30)
    check = sub.add_parser("check-reference",
                           help="Report what is wrong with a reference pack, before importing it")
    check.add_argument("--file", required=True)
    review = sub.add_parser("review",
                            help="Model Fit Review, proposal only: rank every governed method per series "
                                 "and horizon on matched dates (WP2). Writes a report, changes nothing.")
    review.add_argument("--cutoff", default="", help="YYYY-MM-DD; default today (UTC)")
    review.add_argument("--identity", action="append", default=[], help="identity key; repeatable; default all")
    review.add_argument("--method", action="append", default=[], help="method key; repeatable; default all selectable + challengers")
    review.add_argument("--max-origins", type=int, default=None, help="newest N origins only (a quick look)")
    review.add_argument("--no-related", action="store_true", help="skip the related-series search")
    review.add_argument("--out", default="", help="write the full report as JSON here")
    review.add_argument("--record", action="store_true",
                        help="WP4: record a governed Model Fit Review in the store (results; and selections only "
                             "when automatic selection is switched on). Without it the review only reports.")
    review.add_argument("--actor", default="SYSTEM", help="who started the recorded review")
    validate = sub.add_parser("validate", help="Release checks of the forecast-method governance against the store (WP5)")
    validate.add_argument("--for-activation", action="store_true",
                          help="also require a successful full Model Fit Review (the activation guard)")
    setting = sub.add_parser("governance-setting",
                             help="Show or change a forecast-method governance setting (WP4/WP5). Changing it needs "
                                  "--actor and --reason and is recorded.")
    setting.add_argument("--key", default="", help="automatic_selection_enabled")
    setting.add_argument("--value", default="", help="on/off for the switch; a whole number of months for the cadence")
    setting.add_argument("--actor", default="")
    setting.add_argument("--reason", default="")
    produce = sub.add_parser("produce",
                             help="Governed production forecasts (WP3): run the method in force for every series and "
                                  "horizon that has one, and record vintages / failures. Off the page path.")
    produce.add_argument("--issue-date", default="", help="YYYY-MM-DD; default today (UTC)")
    args = parser.parse_args(argv)

    if args.command == "validate":
        from app.forecasting.validation import run_checks, summary_lines
        from app.main import app

        checks = run_checks(app.state.session_factory, for_activation=args.for_activation)
        print("\n".join(summary_lines(checks)))
        return 0 if all(c.passed for c in checks) else 5

    if args.command == "governance-setting":
        from app.forecasting.domain import GovernanceError
        from app.forecasting.review import review_state
        from app.forecasting.settings import DEFAULTS, get_setting, set_setting
        from app.main import app

        factory = app.state.session_factory
        if args.key:
            raw = args.value.strip().lower()
            try:
                if args.key == "automatic_selection_enabled":
                    if raw not in ("on", "off"):
                        raise ValueError("type ON or OFF")
                    value = raw == "on"
                    if value:
                        # The activation guard (WP5 Task 12 step 8, decision D9): every release check passes
                        # and a successful full Model Fit Review exists - or nothing is switched on.
                        from app.forecasting.validation import run_checks, summary_lines
                        checks = run_checks(factory, for_activation=True)
                        if not all(c.passed for c in checks):
                            print("\n".join(summary_lines(checks)))
                            raise ValueError("the release checks above do not all pass; automatic selection stays OFF")
                else:
                    value = int(raw or 0)
                with factory() as session:
                    changed = set_setting(session, args.key, value, actor=args.actor, reason=args.reason)
                    session.commit()
            except (GovernanceError, ValueError) as refused:
                print(f"REFUSED: {refused}")
                return 4
            print(f"CHANGED: {changed['key']} = {changed['value']} by {changed['actor']} ({changed['reason']})")
        with factory() as session:
            for key in DEFAULTS:
                print(f"{key} = {get_setting(session, key)}")
        state = review_state(factory)
        print(f"method selection = {state['state']}; last review = {state['last_at']} "
              f"({state['last_scope']})")
        return 0

    if args.command == "produce":
        from datetime import date as _date, datetime as _datetime, timezone as _timezone

        from app.forecasting.production import produce_all
        from app.main import app

        issue = _date.fromisoformat(args.issue_date) if args.issue_date else _datetime.now(_timezone.utc).date()
        summary = produce_all(app.state.session_factory, issue_date=issue, progress=_progress_to_window_and_file)
        print(f"governed production forecasts at {summary['issue_date']}: {summary['pairs']} pairs, "
              f"{summary['succeeded']} succeeded, {summary['failed']} failed")
        return 0 if summary["failed"] == 0 else 2

    if args.command == "review":
        _keep_the_pc_awake()
        from datetime import date, datetime, timezone

        from app.forecasting.review_job import run_review, summary_lines
        from app.main import app

        cutoff = date.fromisoformat(args.cutoff) if args.cutoff else datetime.now(timezone.utc).date()
        if args.record:
            from app.forecasting.review import ModelFitReviewService

            if args.identity or args.max_origins is not None:
                print("Note: a review of some markets or of the newest origins only is recorded as PARTIAL - "
                      "it commits no selection and does not count as the quarterly review.", flush=True)
            recorded = ModelFitReviewService(app.state.session_factory).run(
                cutoff, args.actor, identity_keys=args.identity or None, max_origins=args.max_origins,
                progress=_progress_to_window_and_file)
            print(json.dumps(recorded, indent=2, default=str))
            return 0 if recorded.get("status") == "SUCCEEDED" else 3
        report = run_review(app.state.session_factory, cutoff=cutoff,
                            identity_keys=args.identity or None, method_keys=args.method or None,
                            max_origins=args.max_origins, related=not args.no_related,
                            progress=_progress_to_window_and_file)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as handle:
                json.dump(report, handle, indent=2, default=str)
        print("\n".join(summary_lines(report)))
        if args.out:
            print(f"full report: {args.out}")
        return 0

    if args.command == "check-reference":
        from app.reference.validate import validate_pack

        report = validate_pack(args.file)
        print(json.dumps(report, indent=2))
        # A pack with errors is not imported: every one of them is a row the importer would drop in
        # silence, or a series that could never be matched to a chain.
        return 1 if report["errors"] else 0


    if args.command == "producer-signals-export":
        from app.main import app
        from app.producer_signals.export import export_producer_signals

        print(json.dumps(export_producer_signals(app.state.session_factory, args.out), indent=2))
        return 0

    if args.command == "backup":
        from app.backup import backup_database

        print(json.dumps(backup_database(os.environ.get("DATABASE_URL", ""), args.to, keep=args.keep)))
        return 0

    if args.command == "checkpoint":
        from sqlalchemy import text

        from app.main import app

        with app.state.session_factory() as session:
            # v0.5.39: a WAL checkpoint is a SQLite matter; on the Supabase store there is nothing to do.
            if session.get_bind().dialect.name != "sqlite":
                print(json.dumps({"checkpoint": "skipped: store is not SQLite"}))
                return 0
            row = session.execute(text("PRAGMA wal_checkpoint(TRUNCATE)")).fetchone()
        print(json.dumps({"checkpoint": list(row) if row is not None else None}))
        return 0

    if args.command == "fx-refresh":
        from app.fx.ecb import refresh_ecb_rates
        from app.main import app

        result = refresh_ecb_rates(app.state.session_factory)
        print(json.dumps(result, indent=2))
        return 0 if result.get("status") == "ok" else 1

    if args.command == "coverage":
        from app.history.backfill import coverage_summary, daily_coverage
        from app.main import app

        rows = daily_coverage(app.state.session_factory, window_days=args.days)
        print(json.dumps({"summary": coverage_summary(rows), "series": rows}, indent=2))
        return 0

    if args.command == "collect":
        from app.persistence.factory import create_collection_service

        service = create_collection_service()
        source_ids = resolve_source_ids(service, source=args.source, due_only=args.due_only)
        results = collect_selected(service, source_ids)
        print(
            json.dumps(
                [
                    {
                        "source_id": result.source_id,
                        "state": result.state.value,
                        "records": len(result.records),
                        "error": result.error,
                    }
                    for result in results
                ],
                indent=2,
            )
        )
        try:
            from app.producer_signals.export import export_producer_signals

            export_producer_signals(service.session_factory)
        except Exception as exc:  # never fail a collection because the feed could not be written
            print(json.dumps({"producer_signals_export_error": repr(exc)}))
        return 0 if all(result.state.value in {"SUCCESS", "PARTIAL"} for result in results) else 1
    if args.command == "bootstrap":
        from app.bootstrap import bootstrap_file
        from app.main import app

        result = bootstrap_file(app.state.session_factory, app.state.registry, args.file)
        print(json.dumps(result, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
