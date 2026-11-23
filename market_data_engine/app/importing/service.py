"""Storing what a person has confirmed off a document they were sent.

Stefan, 5 October 2026, asked for an import for the documents and pictures that arrive by hand. The
reading happens in the application; this is the half that writes, and it writes nothing that has not
been confirmed by a person looking at the page the number came off.

It deliberately goes through the same door every collected reading goes through. A confirmed batch
is handed to the ordinary persistence service as a connector whose "fetch" returns the uploaded file
and whose "parse" returns the confirmed rows. That buys identity resolution, the one-price-per-
source-per-day rule, the evidence trail, the currency conversion and the consolidated series rebuild
without a second implementation of any of them - and it means an imported price behaves on every
screen exactly like a collected one, because it took the same path.

The source is the issuer, not the file. A price letter from one producer in March and another in
June are two documents from one source, which is what makes a series out of them. Where the issuer
is unknown the batch falls to a standing "Imported by hand" source. Both are marked MANUAL_IMPORT so
the collection page and the publisher scorecard keep telling the truth about what is fetched daily
and what arrives by hand.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from app.domain.enums import RightsStatus
from app.domain.schemas import ExtractedRecord, SourceItem
from app.sources.registry import SourceDefinition

# What tells an imported source apart from a collected one, everywhere in the system.
MANUAL_IMPORT = "MANUAL_IMPORT"
FALLBACK_SOURCE_ID = "imported_by_hand"
FALLBACK_PUBLISHER = "Imported by hand"

REQUIRED_FIELDS = ("product", "market_geography", "commercial_basis", "currency", "unit",
                   "observation_date", "price", "observation_type")


class ConfirmationMissing(ValueError):
    """A row reached the writer without everything a price needs. It is not stored."""


def source_id_for(publisher: str) -> str:
    """One id per issuer, stable across every document they ever send."""
    slug = re.sub(r"[^a-z0-9]+", "_", str(publisher or "").strip().lower()).strip("_")
    return f"import_{slug}" if slug else FALLBACK_SOURCE_ID


def definition_for(publisher: str, *, rights_class: str, notes: str = "") -> SourceDefinition:
    """The registry entry for an issuer whose documents arrive by hand.

    `rights_class` is stated per import and has no default: what a document may be used for depends
    on what is in it, and Stefan rules on it document by document (5 October 2026).
    """
    if not str(rights_class or "").strip():
        raise ConfirmationMissing("the rights class has to be stated for every import")
    publisher = str(publisher or "").strip() or FALLBACK_PUBLISHER
    return SourceDefinition(
        source_id_for(publisher),
        publisher,
        f"{publisher} - documents imported by hand",
        "",
        rights_class,
        RightsStatus.APPROVED,
        collection_method=MANUAL_IMPORT,
        refresh_frequency="NONE",
        expected_publish_frequency="IRREGULAR",
        acquisition_methods=("MANUAL",),
        reliability_level=3,
        notes=notes or "Registered by the import on first use. Arrives by hand, never fetched.",
        collector_status=MANUAL_IMPORT,
    )


@dataclass
class ConfirmedBatch:
    """One file, the issuer it came from, and the rows a person has confirmed off it."""

    file_name: str
    content: bytes
    publisher: str
    rights_class: str
    rows: Sequence[dict[str, Any]]
    imported_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def source_id(self) -> str:
        return source_id_for(self.publisher)

    @property
    def source_url(self) -> str:
        # Not a web address. It names the file inside the import so the evidence row, the raw bytes
        # and every observation written from them point at each other.
        return f"import://{self.source_id}/{self.file_name}"


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        raise ConfirmationMissing("a reading with no date is not a point on a series")
    return datetime.fromisoformat(text[:10]).replace(tzinfo=timezone.utc)


class ConfirmedDocumentConnector:
    """The uploaded file as a source item, and the confirmed rows as its records.

    `extract_html` ignores what it is handed. The payload is the file, which may be a workbook or a
    photograph and is kept as evidence in exactly the bytes that arrived; what is stored as readings
    is what the person confirmed on screen, and nothing is re-read here. Re-reading would mean the
    stored price could differ from the one that was approved.
    """

    allow_empty_parse = False

    def __init__(self, batch: ConfirmedBatch):
        self.batch = batch
        self.source_id = batch.source_id
        self.source_url = batch.source_url

    def discover(self) -> list[SourceItem]:
        return [SourceItem(source_url=self.source_url)]

    def fetch_item(self, item: SourceItem) -> bytes:
        return self.batch.content

    def extract_html(self, payload: str, source_url: str | None = None) -> list[ExtractedRecord]:
        del payload
        out: list[ExtractedRecord] = []
        for position, row in enumerate(self.batch.rows, start=1):
            data = {key: value for key, value in dict(row).items() if value not in (None, "")}
            missing = [name for name in REQUIRED_FIELDS if not data.get(name)]
            if missing:
                raise ConfirmationMissing(
                    f"row {position} of {self.batch.file_name} is missing {', '.join(missing)}")
            # The engine's classifier reads `type` to decide what a record is. Without it a
            # confirmed price was quarantined: the run reported success, the file was stored, and
            # not one reading reached the store. My own test caught it.
            data["type"] = "price"
            observed = _as_datetime(data["observation_date"])
            data["observation_date"] = observed
            data["price"] = float(data["price"])
            data.setdefault("grade", None)
            data.setdefault("packaging", None)
            data["source_label"] = (f"{self.batch.publisher}, {self.batch.file_name}, "
                                    f"row {position}")
            out.append(ExtractedRecord(
                kind="price",
                source_url=source_url or self.source_url,
                publication_date=self.batch.imported_at,
                data=data,
            ))
        return out


def check(batch: ConfirmedBatch) -> None:
    """Refuse an incomplete batch before anything is written or run.

    The connector refuses too, but by then the runner has it and turns the refusal into a failed
    run: the caller got a 200 saying the state was not SUCCESS, which reads as "something went
    wrong" rather than "row 2 has no Incoterm". The check belongs at the door. My own test for the
    endpoint caught that the hard way.
    """
    if not batch.rows:
        raise ConfirmationMissing("no confirmed rows were sent")
    for position, row in enumerate(batch.rows, start=1):
        values = {key: value for key, value in dict(row).items() if value not in (None, "")}
        missing = [name for name in REQUIRED_FIELDS if not values.get(name)]
        if missing:
            raise ConfirmationMissing(
                f"row {position} of {batch.file_name} is missing {', '.join(missing)}")
        _as_datetime(values["observation_date"])
        try:
            price = float(values["price"])
        except (TypeError, ValueError):
            raise ConfirmationMissing(f"row {position} has a price that is not a number")
        if price <= 0:
            raise ConfirmationMissing(f"row {position} has a price of zero or less")


def store(service, batch: ConfirmedBatch):
    """Register the issuer if this is their first document, then write the batch.

    `mark_schedule=False`: nothing here was fetched, so the date the scheduler reads to decide
    whether a source is due must not move. An import is not a collection.
    """
    check(batch)
    service.registry.add(definition_for(batch.publisher, rights_class=batch.rights_class))
    return service.collect(ConfirmedDocumentConnector(batch), mark_schedule=False)


def imported_definitions(rows: Iterable[Any]) -> list[SourceDefinition]:
    """Rebuild the issuers from what the store already holds.

    A source created while the engine was running lives in the database and not in the code, so on
    the next start the registry would not know it and its own readings would have no definition.
    This reads them back.
    """
    out: list[SourceDefinition] = []
    for row in rows:
        if getattr(row, "collection_method", "") != MANUAL_IMPORT:
            continue
        out.append(SourceDefinition(
            row.source_id,
            row.publisher or FALLBACK_PUBLISHER,
            row.source_name or "",
            row.base_url or "",
            row.rights_class or "",
            RightsStatus.APPROVED,
            collection_method=MANUAL_IMPORT,
            refresh_frequency="NONE",
            expected_publish_frequency="IRREGULAR",
            acquisition_methods=("MANUAL",),
            active=bool(row.active),
            reliability_level=row.reliability_level or 3,
            notes=row.notes or "",
            collector_status=MANUAL_IMPORT,
        ))
    return out
