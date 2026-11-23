from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Iterable, NamedTuple

from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.classification.classifier import classify_record
from app.persistence.identity import Refused, ensure_identity
from app.persistence import quarantine
from app.db.models import (
    PriceInCurrencyModel,
    EventRecordModel,
    FeatureObservationModel,
    IngestionRunModel,
    MarketIdentityModel,
    MarketReferencePointModel,
    PriceObservationModel,
    QualityIssueModel,
    RawEvidenceModel,
    SourceRegistryModel,
)
from app.domain.enums import EvidenceClass, EventState
from app.identity.resolver import resolve_identity
from app.ingestion.runner import IngestionRunResult, IngestionRunner
from app.quality.scoring import evidence_weight
from app.reference.daily import arithmetic_mean_reference, observation_timestamp, select_latest_daily_contributors
from app.reference.series import rebuild_reference_series
from app.scheduling.scheduler import due_source_ids as scheduled_due_source_ids
from app.sources.registry import SourceRegistry


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _same_moment(held: datetime | None, offered: datetime | None) -> bool:
    """Whether two stored moments are the same instant, with a naive one read as UTC.

    Every datetime this store writes is UTC. Postgres hands them back with that said and SQLite
    hands them back without it, so comparing them raw made a tz-aware publication date differ from
    the identical naive one the test store held - and an unchanged month then loaded its record and
    wrote a value it already had, once per month, every run.
    """
    if held is None or offered is None:
        return held is offered
    if held.tzinfo is None:
        held = held.replace(tzinfo=timezone.utc)
    if offered.tzinfo is None:
        offered = offered.replace(tzinfo=timezone.utc)
    return held == offered


def _moment_key(moment):
    """A stored moment as a dictionary key, whatever shape the record spelled it in.

    A naive moment is read as UTC, for `_same_moment`'s reason: Postgres states the time zone and
    SQLite does not, so one instant arrives in two shapes and would key two entries. A date and an
    ISO string are normalised for a second reason - the query this replaces compared them against a
    timestamp column and the driver cast them, so a connector handing a day as `date(2000, 1, 1)`
    or `"2000-01-01"` still matched the row it had written last run. An index keyed on the raw
    value would not, and would insert a second row beside the first.
    """
    if moment is None:
        return None
    if isinstance(moment, str):
        try:
            moment = datetime.fromisoformat(moment)
        except ValueError:
            return moment
    if isinstance(moment, datetime):
        return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)
    if isinstance(moment, date):
        return datetime(moment.year, moment.month, moment.day, tzinfo=timezone.utc)
    return moment


class _HeldPrice(NamedTuple):
    """One market-day this source already holds, without loading its record.

    Enough to decide whether anything needs writing - the price, and the day it was published -
    and the id to load the row by when something does.
    """
    id: str
    price: float | None
    publication_date: datetime | None


class _HeldMonth(NamedTuple):
    """One variable-month the store already holds, without its record.

    Enough to decide whether anything needs writing - the value, the signature of the bytes it was
    read from, the day it became available - and the id to load the record by when something does.
    The `data` column is not read: these are 5,883 rows for one UN Comtrade window, and the whole
    point is to pay for them once.
    """
    id: str
    value: float | None
    raw_signature: str | None
    available_from: datetime | None


class PersistentMarketDataService:
    """Persist one complete source collection run and its derived evidence."""

    def __init__(self, session_factory: sessionmaker, raw_root, registry: SourceRegistry | None = None):
        self.session_factory = session_factory
        self.registry = registry or SourceRegistry.default()
        self.runner = IngestionRunner(self.registry, raw_root)

    def collect(self, connector, *, mark_schedule: bool = True) -> IngestionRunResult:
        """Run a connector and store what it produced.

        `mark_schedule=False` is for a backfill. `last_successful_collection` is what the scheduler
        reads to decide whether a source is due today, so a backfill that stamped it would satisfy
        today's check with yesterday's pages - turning the gap-filler into a gap-maker.
        """
        started_at = _now()
        self._trade_counts = {}
        # One run, one reading of what the store already holds. Emptied here and not at the end, so
        # the index belongs to this run and a service used for a second source cannot answer with
        # the first one's rows.
        self._monthly_features = {}
        # The same, for prices: what this source holds by market and day, what each market holds by
        # calendar year, and the identity row behind each set of identity fields. All three were a
        # query per record.
        self._prices = {}
        self._price_years = {}
        self._identity_rows = {}
        self._years_swept = set()
        result = self.runner.run(connector)
        finished_at = _now()

        with self.session_factory() as session:
            self._sync_sources(session)
            run = IngestionRunModel(
                source_id=connector.source_id,
                state=result.state.value,
                started_at=started_at,
                finished_at=finished_at,
                error=result.error,
            )
            session.add(run)
            session.flush()

            source = self.registry.get(connector.source_id)
            source_row = session.scalar(select(SourceRegistryModel).where(SourceRegistryModel.source_id == connector.source_id))
            if result.state.value == "SUCCESS" and source_row is not None and mark_schedule:
                source_row.last_successful_collection = finished_at
            elif result.state.value != "SUCCESS":
                session.add(
                    QualityIssueModel(
                        source_id=connector.source_id,
                        issue_type=result.state.value,
                        severity="WARNING" if result.state.value in {"PARTIAL", "SOURCE_CHANGED"} else "ERROR",
                        details={"error": result.error, "run_id": run.id},
                    )
                )
                session.flush()

            evidence_by_url = self._persist_raw(session, run.id, source.rights_status.value, result)

            impacted_identity_ids: set[str] = set()
            if result.records:
                for record in result.records:
                    evidence = evidence_by_url.get(record.source_url)
                    if evidence is None:
                        continue
                    klass = classify_record(record.data)
                    if klass == EvidenceClass.DIRECT_MARKET_PRICE:
                        identity_id = self._persist_price(session, connector.source_id, evidence, record, source.reliability_level)
                        if identity_id:
                            impacted_identity_ids.add(identity_id)
                    elif klass in {
                        EvidenceClass.LEADING_EXPLANATORY_VARIABLE,
                        EvidenceClass.HISTORICAL_LAGGING_MARKET_INDICATOR,
                    }:
                        self._persist_feature(session, connector.source_id, evidence, record)
                    elif klass in {
                        EvidenceClass.KNOWN_FUTURE_VARIABLE,
                        EvidenceClass.EVENT_REGIME_INFORMATION,
                    }:
                        self._persist_event(session, connector.source_id, evidence, record)
                    elif klass == EvidenceClass.REJECT_QUARANTINE:
                        self._persist_quality_issue(session, connector.source_id, evidence, record)

            for identity_id in impacted_identity_ids:
                self._update_reference(session, identity_id)
                self._flag_frozen_feed(session, connector.source_id, identity_id)

            if connector.source_id.startswith(("comext_", "comtrade_")):
                self._trade_refresh_summary(session, connector.source_id, result, run.id)
            self._trade_counts = None
            self._monthly_features = {}
            self._prices = {}
            self._price_years = {}
            self._identity_rows = {}
            self._years_swept = set()

            session.commit()
        return result

    def _flag_frozen_feed(self, session, source_id: str, identity_id: str) -> None:
        """Raise an issue when this source's readings for this market have stopped moving.

        Not a substitute and not a correction: the readings stay exactly as collected. What changes
        is that somebody is told. A listing frozen on one number for a month reads as a live market
        on every screen, which is how a trader's standing offer sat 70% above the market for months
        without anybody noticing (19 September 2026).
        """
        from app.quality.staleness import frozen_run

        rows = [
            {"source_id": row.source_id, "observation_date": row.observation_date, "price": row.price}
            for row in session.scalars(
                select(PriceObservationModel)
                .where(
                    PriceObservationModel.identity_id == identity_id,
                    PriceObservationModel.source_id == source_id,
                )
                .order_by(PriceObservationModel.observation_date, PriceObservationModel.id)
            )
        ]
        run = frozen_run(rows)
        if run is None:
            return
        details = run.as_details() | {"identity_id": identity_id, "summary": run.as_sentence()}
        existing = session.scalar(
            select(QualityIssueModel).where(
                QualityIssueModel.source_id == source_id,
                QualityIssueModel.issue_type == "STALE_VALUE",
            ).order_by(QualityIssueModel.created_at.desc())
        )
        # One open issue per source and market, updated rather than repeated - a frozen feed would
        # otherwise raise a fresh row on every collection and bury everything else.
        if existing is not None and (existing.details or {}).get("identity_id") == identity_id:
            existing.details = details
            return
        session.add(QualityIssueModel(
            source_id=source_id,
            issue_type="STALE_VALUE",
            severity="WARNING",
            details=details,
        ))
        session.flush()

    def due_source_ids(self, now: datetime | None = None) -> list[str]:
        # v0.5.51: a read. It used to sync the registry here - an UPDATE of every source row on
        # every /sources call - and on Postgres those writes queued behind the collector's own open
        # transactions until Supabase cancelled them: /sources answered 500, the collector's first
        # source sat for an hour, and the engine's log filled with "statement timeout". Reads do
        # not write. The registry is synced at engine start and after each collection.
        with self.session_factory() as session:
            rows = list(session.scalars(select(SourceRegistryModel)))
            last_successful: dict[str, datetime | None] = {}
            for row in rows:
                value = row.last_successful_collection
                if value is not None and value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
                last_successful[row.source_id] = value
        return scheduled_due_source_ids(self.registry, last_successful=last_successful, now=now)

    def reference_as_of(self, identity_id: str, cutoff: datetime) -> dict | None:
        with self.session_factory() as session:
            rows = list(
                session.scalars(
                    select(PriceObservationModel)
                    .where(
                        PriceObservationModel.identity_id == identity_id,
                        PriceObservationModel.available_from <= cutoff,
                    )
                    .order_by(PriceObservationModel.available_from, PriceObservationModel.id)
                )
            )
        if not rows:
            return None
        contributors = select_latest_daily_contributors(rows, cutoff=cutoff)
        if not contributors:
            return None
        value = arithmetic_mean_reference(contributors)
        reference_date = max(observation_timestamp(row) for row in contributors if observation_timestamp(row) is not None)
        return {
            "identity_id": identity_id,
            "cutoff": cutoff,
            "reference_date": reference_date,
            "reference_value": value,
            "observation_ids": [row.id for row in contributors],
        }

    def sync_sources(self) -> int:
        """Bring the stored registry up to the code's. Returns how many rows changed."""
        with self.session_factory() as session:
            changed = self._sync_sources(session)
            session.commit()
        return changed

    def _sync_sources(self, session: Session) -> int:
        """Write only the rows whose values differ. On a settled store that is none, and no lock is taken."""
        changed = 0
        existing = {row.source_id: row for row in session.scalars(select(SourceRegistryModel))}
        for source in self.registry.list():
            row = existing.get(source.source_id)
            values = {
                "publisher": source.publisher,
                "source_name": source.source_name,
                "base_url": source.base_url,
                "rights_class": source.rights_class,
                "rights_status": source.rights_status.value,
                "collection_method": source.collection_method,
                "refresh_frequency": source.refresh_frequency,
                "active": source.active,
                "reliability_level": source.reliability_level,
                "notes": source.notes,
            }
            if row is None:
                session.add(SourceRegistryModel(source_id=source.source_id, **values))
                changed += 1
                continue
            differing = {key: value for key, value in values.items()
                         if (getattr(row, key) or None) != (value or None)}
            for key, value in differing.items():
                setattr(row, key, value)
            changed += 1 if differing else 0
        session.flush()
        return changed

    def _persist_raw(self, session: Session, run_id: str, rights_status: str, result: IngestionRunResult) -> dict[str, RawEvidenceModel]:
        persisted: dict[str, RawEvidenceModel] = {}
        for source_url, raw_ref in result.raw_by_url.items():
            row = session.scalar(
                select(RawEvidenceModel).where(
                    RawEvidenceModel.source_id == result.source_id,
                    RawEvidenceModel.content_hash == raw_ref.content_hash,
                    RawEvidenceModel.source_url == source_url,
                )
            )
            publication_date = next(
                (r.publication_date for r in result.records if r.source_url == source_url and r.publication_date is not None),
                None,
            )
            if row is None:
                row = RawEvidenceModel(
                    source_id=result.source_id,
                    ingestion_run_id=run_id,
                    source_url=source_url,
                    content_hash=raw_ref.content_hash,
                    content_location=raw_ref.path,
                    publication_date=publication_date,
                    collection_timestamp=_now(),
                    rights_status=rights_status,
                )
                session.add(row)
                session.flush()
            persisted[source_url] = row
        return persisted

    def _ensure_identity(self, session: Session, record,
                         source_id: str = "") -> MarketIdentityModel:
        """Resolve the identity from the record, then hand it to the one shared upsert.

        `source_id` travels with it for one value only: a customs `market_price` is a deal rather
        than an assessment, so the gate needs to know who sent it (masterplan A item 6, D).
        """
        held = getattr(self, "_identity_rows", None)
        if held is None:
            held = self._identity_rows = {}
        return ensure_identity(session, resolve_identity(record.data), source_id, cache=held)

    def _persist_price(self, session: Session, source_id: str, evidence: RawEvidenceModel, record, reliability_level: int) -> str | None:
        # The door (masterplan D, v1.2.11). A value a gated field cannot hold does not mint a
        # market - and the reading is not dropped either: it goes to `quarantined_reading` with the
        # publisher's own word, the reason, and the document to go back to, and Data & Sources shows
        # it. Before this, the extractor's string went straight into an identity: 23 markets were
        # minted holding a PRICE in the unit field, one print as two markets with one reading each.
        try:
            identity = self._ensure_identity(session, record, source_id)
        except Refused as refused:
            quarantine.record(
                session, source_id=source_id, refusals=refused.refusals,
                reading=jsonable_encoder(record.data),
                document=str(getattr(evidence, "content_location", "") or ""),
                page=str(record.data.get("page") or record.data.get("line") or ""),
                seen_on=record.data.get("observation_date") or record.publication_date
                or record.collected_at)
            return None
        available_from = record.publication_date or record.collected_at
        # A price with no date is not a point on a time series, and one was stored: a SUNSIRS
        # reading for 1 September 2026, undated, beside the dated reading of the same price. It
        # survived because the duplicate check below compares observation_date to observation_date,
        # and in SQL NULL is never equal to NULL - so an undated row can never match an existing one
        # and re-inserts on every collection run. Downstream, the consolidator falls back to the
        # publication date and counted it; the repair pass reads observation_date directly and did
        # not, so the two rewrote each other and September's price oscillated.
        #
        # The date is settled here, once, at the door. The publisher's own date stands where the
        # connector gave one; where it gave none, the date it published on is the honest answer and
        # is what every reader downstream was inferring anyway. A record with neither is not stored.
        observation_date = record.data.get("observation_date") or available_from
        if observation_date is None:
            return None
        complete = all(
            [
                identity.product_grade_or_chemistry,
                identity.commercial_basis,
                identity.packaging,
            ]
        )
        price = float(record.data["price"])
        # One market, one datapoint per period, across frequencies (Stefan, 13 October 2026). An
        # annual figure stands only for a year the same market's monthly figures do not cover; a
        # monthly figure arriving later takes the year over and the annual one is removed.
        if record.data.get("annual_fallback") and self._has_monthly_in_year(session, identity.id, observation_date):
            return identity.id
        if record.data.get("supersedes_annual"):
            self._drop_annual_in_year(session, identity.id, observation_date)
        # One publisher, one market, one day: one price. Whatever it says last is what it says.
        #
        # This matched on the price as well, so a publisher restating a day at a DIFFERENT figure
        # inserted a second row beside the first. Consolidation takes one value per source as the
        # mean of that source's rows, so a revision was averaged with the number it replaced and the
        # governed value became a figure nobody published: SunSirs revised propylene oxide for 20
        # September 2026 from 12,033.33 to 11,933.33 and the two would have produced 11,983.33.
        # Across the store it had already happened 26 times, worst case five prints of one day from
        # one page, spanning 11,400 to 12,700.
        #
        # Matching without the price keeps what the price was there for - an identical restatement
        # is still the same fact and still a no-op, which is what stopped Longzhong's rolling 7-day
        # table re-inserting itself on every run. A changed one now supersedes in place. Nothing is
        # lost: every fetch keeps its own row in `raw_evidence`, which is where the audit trail
        # belongs. A price series is not the place to keep a number the publisher has withdrawn.
        #
        # Live connectors only. Curated history is written by `app/history/*`, which has its own
        # writer, so a month holding two grades of one family is not touched by this.
        index = self._price_index(session, source_id)
        key = (identity.id, _moment_key(observation_date))
        known = index.get(key)

        # Held, at the same price: decided on the index alone, without reading the row back.
        if isinstance(known, _HeldPrice) and known.price == price:
            if record.data.get("record_revisions"):
                self._count("unchanged", record)
                # The figure stands; only when it became available is corrected. The single-series
                # Comext connector dated each month by its own first day, about six weeks before
                # Eurostat published it, which let a backtest see the future.
                if (record.publication_date is not None
                        and not _same_moment(known.publication_date, record.publication_date)):
                    row = session.get(PriceObservationModel, known.id)
                    if row is not None:
                        row.publication_date = record.publication_date
                        row.available_from = available_from
                    index[key] = known._replace(publication_date=record.publication_date)
            return identity.id

        # Anything else needs the record itself: a figure the publisher has changed, or a row this
        # run wrote a moment ago and is now being handed a second print of.
        existing = known if isinstance(known, PriceObservationModel) else None
        if existing is None and known is not None:
            existing = session.get(PriceObservationModel, known.id)
        if existing is not None:
            if existing.price == price:
                if record.data.get("record_revisions"):
                    self._count("unchanged", record)
                    if record.publication_date is not None and not _same_moment(
                            existing.publication_date, record.publication_date):
                        existing.publication_date = record.publication_date
                        existing.available_from = available_from
                return identity.id
            if record.data.get("record_revisions"):
                self._count("revised", record)
                # A statistical office revises a month it has already published. The series keeps the
                # current figure, as it does for every source, and the figure it replaced is written
                # down here rather than lost: Stefan, 13 October 2026, "Record revisions rather than
                # silently overwriting historical data."
                self._record_revision(session, source_id, kind="price", identity_id=identity.id,
                                      series=identity.observation_type,
                                      month=record.data.get("observation_month"),
                                      old=existing.price, new=price, record=record)
            existing.price = price
            existing.evidence_id = evidence.content_hash
            existing.available_from = available_from
            existing.publication_date = record.publication_date
            existing.weight = evidence_weight(reliability_level, complete)
            index[key] = existing
            return identity.id
        if record.data.get("record_revisions"):
            self._count("new", record)
        fresh = PriceObservationModel(
            identity_id=identity.id,
            source_id=source_id,
            evidence_id=evidence.content_hash,
            price=price,
            observation_date=observation_date,
            publication_date=record.publication_date,
            available_from=available_from,
            weight=evidence_weight(reliability_level, complete),
            currency=str(identity.currency or "").strip().upper(),
        )
        session.add(fresh)
        # Not flushed. The insert goes with the next flush the run makes, in one statement with
        # every other new reading, instead of a round trip of its own. What the per-record flush
        # used to guarantee - that a second print of one market-day meets the row the first print
        # wrote - the index guarantees instead, by holding the pending object itself.
        index[key] = fresh
        self._note_year(identity.id, observation_date, source_id)
        return identity.id

    def _price_index(self, session: Session, source_id: str) -> dict:
        """What this source already holds, one market-day per entry, read in one query.

        The sibling of `_monthly_feature_index`, and the same defect. Measured against the live
        store on 19 November 2026, a second run of `comtrade_annual_2000_2008` with nothing at all
        to change spent 43.7 seconds on 1,201 of these lookups, one per record.

        A row with no observation date is left out on purpose. The query this replaces compared
        `observation_date = observation_date`, and in SQL NULL is never equal to NULL, so an
        undated row could never match and was re-inserted on every run - a fault settled at the
        door above, where a record with no date of any kind is not stored at all. Leaving such a row
        out of the index keeps this path's answer exactly what it was.

        Where two rows share a market and a day, the one that became available last wins, which is
        what the `ORDER BY available_from DESC` on the query this replaces said.
        """
        held = getattr(self, "_prices", None)
        if held is None:
            held = self._prices = {}
        index = held.get(source_id)
        if index is not None:
            return index
        index = held[source_id] = {}
        newest: dict = {}
        for row_id, identity_id, moment, value, published, available in session.execute(
            select(PriceObservationModel.id, PriceObservationModel.identity_id,
                   PriceObservationModel.observation_date, PriceObservationModel.price,
                   PriceObservationModel.publication_date, PriceObservationModel.available_from)
            .where(PriceObservationModel.source_id == source_id)
        ):
            if moment is None:
                continue
            key = (identity_id, _moment_key(moment))
            stamp = _moment_key(available)
            if key in index:
                held_stamp = newest.get(key)
                if stamp is None or (held_stamp is not None and stamp <= held_stamp):
                    continue
            newest[key] = stamp
            index[key] = _HeldPrice(row_id, value, published)
        return index

    def _year_sources(self, session: Session, identity_id: str) -> dict:
        """Which sources this market holds a reading from, per calendar year, read once per run.

        `_year_rows` selected every reading of the market IN FULL and filtered the year in Python,
        once per record: measured 19 November 2026, 1,059 calls and 39.9 seconds for one UN
        Comtrade window, each of them dragging back a few hundred whole rows to answer a yes or no.
        Two columns, once per market, is the same answer - and far less of the egress budget.

        The year is still decided here rather than in SQL, for the reason the old comment gave:
        SQLite and Postgres disagree about time zones.
        """
        held = getattr(self, "_price_years", None)
        if held is None:
            held = self._price_years = {}
        years = held.get(identity_id)
        if years is not None:
            return years
        years = held[identity_id] = {}
        for source_id, moment in session.execute(
            select(PriceObservationModel.source_id, PriceObservationModel.observation_date)
            .where(PriceObservationModel.identity_id == identity_id)
        ):
            if moment is not None:
                years.setdefault(moment.year, set()).add(source_id)
        return years

    def _note_year(self, identity_id: str, moment, source_id: str) -> None:
        """A reading this run has just written, so the year index stays the store's own answer."""
        held = getattr(self, "_price_years", None)
        if not held or identity_id not in held or moment is None:
            return
        held[identity_id].setdefault(moment.year, set()).add(source_id)

    @staticmethod
    def _year_rows(session: Session, identity_id: str, moment):
        """Every reading of one market in one calendar year, in full, to delete them by.

        Only `_drop_annual_in_year` needs the rows themselves, and only for a market and year that
        `_year_sources` has already said holds an annual stand-in.
        """
        rows = session.scalars(select(PriceObservationModel).where(
            PriceObservationModel.identity_id == identity_id)).all()
        return [r for r in rows if r.observation_date is not None and r.observation_date.year == moment.year]

    def _has_monthly_in_year(self, session: Session, identity_id: str, moment) -> bool:
        return any(str(one).startswith("comtrade_monthly_")
                   for one in self._year_sources(session, identity_id).get(moment.year, ()))

    def _drop_annual_in_year(self, session: Session, identity_id: str, moment) -> None:
        """Remove the annual stand-in for a year this market now has monthly figures for.

        Once per market and year per run, not once per record. A monthly window carries
        `supersedes_annual` on every one of its readings - twelve months across 142 markets - and
        after the first sweep of a year there is nothing left to find. Nothing can put an annual row
        back inside this run, because a monthly window produces monthly readings only, so every
        sweep after the first was a full read of the market for an empty answer.
        """
        swept = getattr(self, "_years_swept", None)
        if swept is None:
            swept = self._years_swept = set()
        mark = (identity_id, moment.year)
        if mark in swept:
            return
        swept.add(mark)
        years = self._year_sources(session, identity_id).get(moment.year) or set()
        if not any(str(one).startswith("comtrade_annual_") for one in years):
            return
        for row in self._year_rows(session, identity_id, moment):
            if row.source_id.startswith("comtrade_annual_"):
                for converted in session.scalars(select(PriceInCurrencyModel).where(
                        PriceInCurrencyModel.observation_id == row.id)).all():
                    session.delete(converted)
                session.delete(row)
        for one in [one for one in years if str(one).startswith("comtrade_annual_")]:
            years.discard(one)
        session.flush()

    def _count(self, what: str, record) -> None:
        counts = getattr(self, "_trade_counts", None)
        if counts is None:
            return
        counts[what] = counts.get(what, 0) + 1
        period = record.data.get("observation_period") or record.data.get("observation_month")
        if period and str(period) > str(counts.get("latest_period") or ""):
            counts["latest_period"] = str(period)
        for status in record.data.get("refresh_statuses") or []:
            counts.setdefault("statuses", set()).add(status)

    def _trade_refresh_summary(self, session: Session, source_id: str, result, run_id: str) -> None:
        """One line per refresh of a trade-layer source: what arrived, what changed, what is missing.

        Stefan, 13 October 2026, section 10: every refresh records its retrieval result, in his
        status words. Written as a quality-issue row of type TRADE_REFRESH so it sits beside the
        REVISION rows it summarises; the application's screens read only STALE_VALUE from that list.
        """
        from app.trade import materials as m
        counts = getattr(self, "_trade_counts", None) or {}
        statuses: list[str] = []
        if result.state.value not in ("SUCCESS", "PARTIAL"):
            statuses.append(m.API_ERROR if "HTTP" in str(result.error or "") or "error" in str(result.error or "").lower()
                            else m.SOURCE_UNAVAILABLE)
        else:
            if counts.get("new"):
                statuses.append(m.NEW_OBSERVATION)
            if counts.get("revised"):
                statuses.append(m.REVISED_OBSERVATION)
            if not counts.get("new") and not counts.get("revised"):
                statuses.append(m.NO_NEW_OBSERVATION)
            if counts.get("no_unit_value"):
                statuses.append(m.MISSING_QUANTITY)
            for status in sorted(counts.get("statuses") or ()):
                if status not in statuses:
                    statuses.append(status)
        session.add(QualityIssueModel(
            source_id=source_id, issue_type="TRADE_REFRESH", severity="INFO",
            details={"run_id": run_id, "statuses": statuses,
                     "new_observations": counts.get("new", 0),
                     "revised_observations": counts.get("revised", 0),
                     "unchanged_observations": counts.get("unchanged", 0),
                     "months_without_unit_value": counts.get("no_unit_value", 0),
                     "latest_period": counts.get("latest_period"),
                     "run_state": result.state.value, "error": result.error,
                     "checked_at": datetime.now(timezone.utc).isoformat()}))
        session.flush()

    def _record_revision(self, session: Session, source_id: str, *, kind: str, identity_id: str | None,
                         series: str | None, month: str | None, old: float | None, new: float | None,
                         record, previous_raw: str | None = None) -> None:
        session.add(QualityIssueModel(
            source_id=source_id,
            issue_type="REVISION",
            severity="WARNING",
            details={
                "kind": kind,
                "identity_id": identity_id,
                "series": series,
                "origin": record.data.get("origin"),
                "observation_month": month,
                "previous_value": old,
                "revised_value": new,
                "previous_raw": previous_raw,
                "revised_raw": record.data.get("raw_signature"),
                "revision_detected_at": datetime.now(timezone.utc).isoformat(),
                "source": record.data.get("primary_source"),
                "source_updated": record.data.get("source_updated") or record.data.get("eurostat_updated"),
                "retrieved_at": record.data.get("retrieved_at"),
            },
        ))
        session.flush()

    def _monthly_feature_index(self, session: Session, source_id: str) -> dict:
        """What this source already holds, one variable-month per entry, read in one query.

        A statistical series re-reads every month it has on every run, so the number of lookups
        this path makes is the whole history, every day. Each lookup filtered on a JSON path that
        no index covers, and each insert was flushed on its own: measured against the live store on
        19 November 2026, 35 ms a round trip.

        That is what stopped `comext_benzene_eu`. Its ten Comext siblings held about 4,277 feature
        rows each and spent 150 seconds of a 300-second budget proving them unchanged; benzene held
        none, so it needed 4,277 lookups AND 4,277 flushed inserts, was killed at 300 seconds on
        every run from 13 October 2026, and - the run being one transaction - rolled back to zero
        each time and started again the next day. Nothing in the record said so: the kill leaves no
        ingestion run, so the admin page still showed its last SUCCESS, of 12 October. The eleven
        UN Comtrade monthly windows carry up to 5,883 rows and were at the same wall.

        So the history is read once per run, without the `data` column, and what comes back is only
        what deciding needs: the row's id, its value, its raw signature and the day it became
        available. An unchanged month now costs nothing at all, and the full record is loaded by
        primary key only for a month that is genuinely being written.
        """
        held = getattr(self, "_monthly_features", None)
        if held is None:
            held = self._monthly_features = {}
        index = held.get(source_id)
        if index is not None:
            return index
        index = held[source_id] = {}
        month_of = FeatureObservationModel.data["observation_month"].as_string()
        signature_of = FeatureObservationModel.data["raw_signature"].as_string()
        for row_id, kind, month, value, signature, available_from in session.execute(
            select(FeatureObservationModel.id, FeatureObservationModel.feature_type, month_of,
                   FeatureObservationModel.value, signature_of,
                   FeatureObservationModel.available_from)
            .where(FeatureObservationModel.source_id == source_id)
        ):
            if month is None:
                continue
            index[(kind, month)] = _HeldMonth(row_id, value, signature, available_from)
        return index

    def _persist_monthly_feature(self, session: Session, source_id: str, evidence: RawEvidenceModel,
                                 record, feature_type: str, month: str) -> None:
        """One value per variable per month, whatever fetch it came from.

        The ordinary feature path de-duplicates on the evidence hash, which suits a page read once.
        A statistical series re-reads every month on every run, so the next new month would re-insert
        all the old ones under the new hash. Here the month is the key: an unchanged month is left
        alone, a changed one is updated and its previous value recorded as a revision.

        What the store already holds is read once per run by `_monthly_feature_index`, not once per
        record, and a month written earlier in this same run is in that index as the pending object
        itself - so two fetches that carry the same month still meet one row, as they did when every
        insert was flushed and found again by a query.
        """
        value = record.data.get("value")
        value = float(value) if value is not None else None
        if record.data.get("record_revisions") and record.data.get("observation_status"):
            self._count("no_unit_value", record)
        index = self._monthly_feature_index(session, source_id)
        key = (feature_type, month)
        known = index.get(key)
        signature = record.data.get("raw_signature")

        # Held, and nothing about it has changed: decided on the index alone, without reading the
        # row. A row written before signatures existed is judged on its value alone.
        if isinstance(known, _HeldMonth):
            same_raw = signature is None or known.raw_signature is None or known.raw_signature == signature
            if known.value == value and same_raw:
                if (record.publication_date is not None
                        and not _same_moment(known.available_from, record.publication_date)):
                    row = session.get(FeatureObservationModel, known.id)
                    if row is not None:
                        row.available_from = record.publication_date
                        index[key] = known._replace(available_from=record.publication_date)
                return

        existing = known if isinstance(known, FeatureObservationModel) else None
        if existing is None and known is not None:
            existing = session.get(FeatureObservationModel, known.id)
        if existing is not None:
            stored = (existing.data or {}).get("raw_signature")
            same_raw = signature is None or stored is None or stored == signature
            if existing.value == value and same_raw:
                if (record.publication_date is not None
                        and not _same_moment(existing.available_from, record.publication_date)):
                    existing.available_from = record.publication_date
                return
            if record.data.get("record_revisions"):
                self._record_revision(session, source_id, kind="feature", identity_id=None,
                                      series=feature_type, month=month, old=existing.value,
                                      new=value, record=record,
                                      previous_raw=(existing.data or {}).get("raw_signature"))
            existing.value = value
            existing.evidence_id = evidence.content_hash
            existing.data = jsonable_encoder(record.data)
            existing.available_from = record.publication_date or record.collected_at
            index[key] = existing
            session.flush()
            return
        fresh = FeatureObservationModel(
            source_id=source_id,
            evidence_id=evidence.content_hash,
            feature_type=feature_type,
            value=value,
            data=jsonable_encoder(record.data),
            available_from=record.publication_date or record.collected_at,
        )
        session.add(fresh)
        # Not flushed. The insert goes with the next flush the run makes, in one statement with
        # every other new month, instead of a round trip of its own.
        index[key] = fresh

    def _persist_feature(self, session: Session, source_id: str, evidence: RawEvidenceModel, record) -> None:
        feature_type = str(record.data.get("feature_type") or record.data.get("type") or "UNKNOWN")
        month = record.data.get("observation_period") or record.data.get("observation_month")
        if month:
            self._persist_monthly_feature(session, source_id, evidence, record, feature_type, str(month))
            return
        # Dedup on what the record says it IS, where it says so, and on the evidence hash only as
        # a fallback. A hash identifies a document, and a document fetched twice is not always the
        # same bytes: the Longzhong article pages carry a rolling price sidebar, so the same weekly
        # operating rate hashed differently on two runs an hour apart and was stored twice
        # (measured, 17 November 2026). A daily collector would have added a copy a day, and a
        # feature that counts seven times in a week is a feature that lies to the forecast.
        #
        # `_persist_event` has the same problem and solved it on the headline; features carry no
        # headline, so a record that can name itself says so in `observation_key`.
        key = record.data.get("observation_key")
        headline = str(record.data.get("headline") or "")
        existing = None
        if key:
            existing = session.scalar(
                select(FeatureObservationModel).where(
                    FeatureObservationModel.source_id == source_id,
                    FeatureObservationModel.feature_type == feature_type,
                    FeatureObservationModel.data["observation_key"].as_string() == str(key),
                )
            )
        # Both, not one or the other. A record that has just been GIVEN a key will not match the
        # row it wrote before the key existed, and a lookup that stopped at the key would file a
        # second copy of the same fact on the first run after any such change - which is exactly
        # what happened on 17 November, four rows of it, an hour after the key was added.
        if existing is None and headline:
            # The same fallback `_persist_event` uses, for the same reason. A headline-derived
            # feature - every CAPACITY and MARGIN posting the intelligence stream reads - has no
            # key of its own, and its page is the rolling listing, whose hash moves. Measured on
            # 17 November 2026: 70 rows for 63 distinct headlines in this source alone.
            existing = session.scalar(
                select(FeatureObservationModel).where(
                    FeatureObservationModel.source_id == source_id,
                    FeatureObservationModel.feature_type == feature_type,
                    FeatureObservationModel.data["headline"].as_string() == headline,
                )
            )
        if existing is None and not key and not headline:
            # Nothing in the record names it, so the document has to. Unchanged behaviour, and the
            # reason the duplicates happened: it is the weakest of the three and it was the only one.
            existing = session.scalar(
                select(FeatureObservationModel).where(
                    FeatureObservationModel.source_id == source_id,
                    FeatureObservationModel.evidence_id == evidence.content_hash,
                    FeatureObservationModel.feature_type == feature_type,
                )
            )
        if existing is not None:
            self._improve_feature_reading(session, existing, evidence, record)
            return
        value = record.data.get("value")
        session.add(
            FeatureObservationModel(
                source_id=source_id,
                evidence_id=evidence.content_hash,
                feature_type=feature_type,
                value=float(value) if value is not None else None,
                data=jsonable_encoder(record.data),
                available_from=record.publication_date or record.collected_at,
            )
        )
        session.flush()

    def _persist_event(self, session: Session, source_id: str, evidence: RawEvidenceModel, record) -> None:
        event_type = str(record.data.get("event_type") or record.data.get("type") or "UNKNOWN").upper()
        # Dedup on the event itself, not on the evidence blob: one page carries many events of the
        # same type, and the same headline reappears on later fetches with a new content hash.
        headline = str(record.data.get("headline") or record.data.get("title") or "")
        existing = session.scalar(
            select(EventRecordModel).where(
                EventRecordModel.source_id == source_id,
                EventRecordModel.event_type == event_type,
                EventRecordModel.data["headline"].as_string() == headline,
            )
        ) if headline else session.scalar(
            select(EventRecordModel).where(
                EventRecordModel.source_id == source_id,
                EventRecordModel.evidence_id == evidence.content_hash,
                EventRecordModel.event_type == event_type,
            )
        )
        if existing is not None:
            self._improve_event_date(session, existing, record)
            return
        state = str(record.data.get("state") or EventState.ANNOUNCED.value)
        session.add(
            EventRecordModel(
                source_id=source_id,
                evidence_id=evidence.content_hash,
                event_type=event_type,
                material=record.data.get("material") or record.data.get("product"),
                state=state,
                data=jsonable_encoder(record.data),
                available_from=record.publication_date or record.collected_at,
            )
        )
        session.flush()

    @staticmethod
    def _improve_feature_reading(
        session: Session, existing: FeatureObservationModel, evidence: RawEvidenceModel, record
    ) -> None:
        """Put a number on a feature that was stored without one.

        A feature read off a listing page is a headline and nothing else. The publisher prints the
        week's operating rate in the article body, which this collector did not open until v1.2.80,
        so 59 of the 63 capacity-utilisation rows in the live store carried a null value (measured,
        18 November 2026). Dedup matched each of them on its headline and returned, which made the
        emptiness permanent: the number could arrive on every later run and never be stored.

        Only ever fills what is missing. A reading already taken is never moved by a later fetch -
        that is what dedup is for - and a key or a period already recorded stands. When the number
        does arrive, `evidence_id` moves with it, because a reading has to cite the document it was
        read from and not the listing page that merely named it.
        """
        changed = False
        value = record.data.get("value")
        if existing.value is None and value is not None:
            existing.value = float(value)
            existing.evidence_id = evidence.content_hash
            changed = True
        data = dict(existing.data or {})
        missing = {
            field: content
            for field, content in (record.data or {}).items()
            if content is not None and data.get(field) is None
        }
        if missing:
            data.update(jsonable_encoder(missing))
            existing.data = data
            changed = True
        if changed:
            session.flush()

    @staticmethod
    def _improve_event_date(session: Session, existing: EventRecordModel, record) -> None:
        """Put the publisher's own date on an event that was stored without one.

        An event whose page carried no readable date is stored at collection time, because that is
        the only moment we can honestly vouch for. When a later run CAN read the date - the parser
        learned the publisher's format, or the page started printing one - the stored row is still
        carrying the day we happened to fetch it. Dedup would keep it that way forever.

        Only ever applied where no publisher date was recorded before: a date we already read is not
        overwritten by a later fetch, and a row that still has no date is left at collection time.
        """
        published = getattr(record, "publication_date", None)
        if published is None:
            return
        data = dict(existing.data or {})
        if data.get("observation_date"):
            return                              # a real date is already on the record
        data["observation_date"] = jsonable_encoder(published)
        existing.data = data
        existing.available_from = published
        session.flush()

    def _persist_quality_issue(self, session: Session, source_id: str, evidence: RawEvidenceModel, record) -> None:
        existing = session.scalar(
            select(QualityIssueModel).where(
                QualityIssueModel.source_id == source_id,
                QualityIssueModel.issue_type == "REJECT_QUARANTINE",
            )
        )
        if existing is None:
            session.add(
                QualityIssueModel(
                    source_id=source_id,
                    issue_type="REJECT_QUARANTINE",
                    severity="WARNING",
                    details={"evidence_id": evidence.content_hash, "record": jsonable_encoder(record.data)},
                )
            )
            session.flush()

    def _update_reference(self, session: Session, identity_id: str) -> None:
        """Rebuild this identity's consolidated series after a collection.

        It used to write one point, for the newest date only. So the engine had a consolidated price
        and no consolidated history, and anything reading back through time read four publishers'
        raw prints instead of the one governed value. Rebuilding the whole series costs nothing when
        nothing changed - a point whose value and contributors are unchanged is left alone - and it
        means the number on the screen today and the number a backtest scores against 2024 come out
        of the same rule.
        """
        rebuild_reference_series(session, identity_id)
