"""Load a pack of reference series into the engine.

One shape, for every pack that will ever arrive. The upstream data HTC can get is scattered across
statistical agencies, subscription reports and internal workbooks, and it will keep arriving in
whatever shape its publisher chose. Rather than a script per pack, everything is converted once into
one long format - a row per chemical, per date, per value - and this loads that.

    chemical, date, value, unit, geography, series_key, series, source_type, quality, source_url

`chemical` must be a chemical route graph node code, because that is what the correlation engine
matches on.

`series_key` is WHICH SERIES this is - the market it describes. `series` is WHERE THE VALUE CAME
FROM. They are separate columns because a pack built from more than one source document has one
series and several provenances, and keying identity off provenance splits it. EUROPUR carries the
same six European price lines in two report vintages: without `series_key` a continuous 151-month
run of European TDI entered the store as a 54-month series and a 97-month series, and the regression
that needs the run would have seen 97 of the 151 months it was built for. `series_key` is optional;
a pack drawn from one document can leave it out and `series` identifies the series as before.

`quality` is carried through to the screen unchanged: a grouped chlor-alkali PPI standing in for
chlorine is a weak proxy for the whole of its life, and the reader should always be told so. Where
one series is assembled from rows of differing quality, it takes the WEAKEST tier present and the
note records the mix - a series is only as sound as its weakest month, and a caption that claimed
otherwise would be claiming it about the months a reader is most likely to act on, the recent ones.

Idempotent by construction: every observation's evidence id is derived from series and date, so
re-importing a pack that has been extended adds only the new months.
"""
from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

from sqlalchemy import select

from app.db.models import ReferenceObservationModel, ReferenceSeriesModel
from app.paths import repo_path

# Resolved from the package, not the working directory - see app/paths.py.
DEFAULT_DIR = repo_path("data", "reference")

# Rights classes. A reference series is not always ours to show.
#
# The column has existed on reference_series since migration 0014 and nothing has ever read it, so
# every series has been public by default. That was harmless while every pack was a statistical
# agency's PPI. It stops being harmless with EUROPUR: the report is confidential, its member data is
# aggregated under CEFIC rules, and its series are labelled with the report and the chart they were
# drawn from. Imported without a gate they appear on Chemical Relationships, which is a customer
# screen.
PUBLIC = "PUBLIC_ATTRIBUTION"
INTERNAL_ONLY = "INTERNAL_ONLY"


@dataclass(frozen=True)
class Pack:
    """One long-format CSV and the terms it arrives under."""
    path: Path
    rights_class: str = PUBLIC


# Packs named one by one rather than found by a folder glob.
#
# The EUROPUR folder holds three vintages of the same pack - v0.1 with two series, v0.2 with six,
# v0.3 with six extended back to 2014 - beside the raw per-chart extractions they were built from. A
# glob takes all of them, and because a series is identified by its label, v0.2's TDI line drawn from
# Chart 20 (digitised) would enter the store as a second European TDI series alongside v0.3's Chart
# 25 line (exact). One market, two series, and a correlation study that finds them 0.99 correlated
# with each other. Naming the pack is the difference between loading data and loading a folder.
#
# The CSV also stays where its README and its charts are. 902 rows, including 151 unbroken monthly
# points of TDI from January 2014, sat in that folder unread while the forecast ran on 28.
PACKS: tuple[Pack, ...] = (
    Pack(repo_path("data", "europur", "europur_prices_v0.4.csv"), INTERNAL_ONLY),
)

REQUIRED_COLUMNS = ("chemical", "date", "value")

QUALITY_TIERS = ("STRONG", "USABLE_PROXY", "WEAK_PROXY")
DEFAULT_QUALITY = "USABLE_PROXY"

# What a pack calls its own quality, in the engine's three tiers.
#
# A pack describes its quality in the vocabulary of how it was made. EUROPUR says VECTOR_EXACT for a
# value read out of the PDF's own line coordinates and DIGITISED for one measured off a bitmap at
# about +/- 20 EUR/t. Both fell through to USABLE_PROXY, which erased the distinction the pack was
# careful to record - 878 exact rows and 24 measured ones, filed as though they were the same thing.
_QUALITY_ALIASES = {
    "VECTOR_EXACT": "STRONG", "EXACT": "STRONG", "REPORTED": "STRONG", "PRINTED": "STRONG",
    "DIGITISED": "WEAK_PROXY", "DIGITIZED": "WEAK_PROXY", "ESTIMATED": "WEAK_PROXY",
    "INTERPOLATED": "WEAK_PROXY",
}

# Units that are an actual amount of money per amount of material. Everything else is an index, and
# the distinction decides whether a series may ever be shown as a price.
#
# Volume units belong here as much as mass ones. Brent is quoted per barrel and naphtha per tonne;
# both are money for material. Accepting only mass filed a real USD/bbl crude price in the index
# lane, where it could never be shown as the price it is.
_PRICE_UNIT = re.compile(
    r"^(usd|eur|cny|thb|jpy|gbp)\s*/\s*"
    r"(mt|metric\s*tonne|tonne|ton|t|kg|kilo|kilogram|bbl|barrel|gal|gallon|l|litre|liter|m3|cbm)$",
    re.I,
)


def is_price_unit(unit: str) -> bool:
    return bool(_PRICE_UNIT.match(str(unit or "").strip()))


def normalise_quality(value: str) -> str:
    token = str(value or "").strip().upper().replace(" ", "_").replace("-", "_")
    if token in QUALITY_TIERS:
        return token
    return _QUALITY_ALIASES.get(token, DEFAULT_QUALITY)


def weakest_quality(tiers: Iterable[str]) -> str:
    """The weakest tier present, or the default when nothing usable was given."""
    present = [tier for tier in tiers if tier in QUALITY_TIERS]
    if not present:
        return DEFAULT_QUALITY
    return max(present, key=QUALITY_TIERS.index)


def series_id(chemical: str, geography: str, series: str) -> str:
    """Stable id for a series, so a re-import updates rather than duplicates.

    Built from what identifies the series rather than from a row number: the same PPI series for the
    same chemical in the same geography is the same series whichever pack it arrives in.
    """
    raw = "|".join([str(chemical).strip().upper(), str(geography).strip().upper(),
                    str(series).strip().upper()])
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"ref:{str(chemical).strip().upper()}:{digest}"


def _as_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for pattern in ("%Y-%m-%d", "%Y-%m", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text[:10] if pattern == "%Y-%m-%d" else text, pattern).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def read_long_csv(path: Path | str) -> list[dict]:
    """Read the long format, tolerating the column-name variations packs arrive with."""
    aliases = {
        "chemical": "chemical", "date": "date", "month": "date", "period": "date",
        "value": "value", "price": "value", "index": "value",
        "unit": "unit", "units": "unit",
        "geography": "geography", "region": "geography", "geographic basis": "geography",
        "series": "series", "series used": "series",
        "series_key": "series_key", "series key": "series_key",
        "identity": "series_key", "series id": "series_key",
        "source type": "source_type", "source_type": "source_type",
        "quality": "quality",
        "source url": "source_url", "source_url": "source_url", "url": "source_url",
        "publisher": "publisher", "note": "note", "caveat": "note",
    }
    rows: list[dict] = []
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        for raw in csv.DictReader(handle):
            row = {}
            for key, value in raw.items():
                canonical = aliases.get(str(key or "").strip().lower())
                if canonical:
                    row[canonical] = value
            if all(row.get(column) not in (None, "") for column in REQUIRED_COLUMNS):
                rows.append(row)
    return rows


def _evidence_id(series: str, day: date) -> str:
    return f"ref:{series}:{day.isoformat()}"


def import_reference_rows(session_factory, rows: Iterable[dict], *, pack: str = "",
                          rights_class: str = PUBLIC) -> dict:
    """Load long-format rows. Returns counts; never partially commits a series."""
    rows = list(rows)
    series_seen: dict[str, dict] = {}
    observations: list[tuple[str, date, float]] = []

    # A row that will not parse is skipped so one bad line cannot cost a whole pack - but it is
    # counted and named, because a pack half of whose rows were dropped used to import as "ok".
    skipped: list[str] = []

    # Provenance strings and quality tiers seen per series, so a series assembled from several
    # source documents can say what it was assembled from and how sound its weakest month is.
    provenance: dict[str, list[str]] = {}
    tiers: dict[str, dict[str, int]] = {}

    for line, row in enumerate(rows, start=2):     # header is line 1
        day = _as_date(row.get("date"))
        if day is None:
            skipped.append(f"line {line}: unreadable date {row.get('date')!r}")
            continue
        try:
            value = float(str(row.get("value")).replace(",", ""))
        except (TypeError, ValueError):
            skipped.append(f"line {line}: unreadable value {row.get('value')!r}")
            continue
        chemical = str(row.get("chemical") or "").strip().upper().replace(" ", "_")
        geography = str(row.get("geography") or "UNKNOWN").strip()
        source = str(row.get("series") or "").strip()
        # The series is what the pack says it is; where the value came from does not change which
        # series it belongs to. A pack that names only one falls back to it, as before.
        label = str(row.get("series_key") or "").strip() or source or chemical
        key = series_id(chemical, geography, label)
        unit = str(row.get("unit") or "").strip()
        tier = normalise_quality(row.get("quality"))
        counts = tiers.setdefault(key, {})
        counts[tier] = counts.get(tier, 0) + 1
        if source and source != label and source not in provenance.setdefault(key, []):
            provenance[key].append(source)
        series_seen.setdefault(key, {
            "series_id": key,
            "chemical": chemical,
            "label": label,
            "geography": geography,
            "unit": unit,
            "is_index": not is_price_unit(unit),
            "quality": DEFAULT_QUALITY,          # replaced below, once every row has been seen
            "source_type": str(row.get("source_type") or "").strip(),
            "publisher": str(row.get("publisher") or "").strip(),
            "source_url": str(row.get("source_url") or "").strip(),
            "rights_class": rights_class,
            "note": (str(row.get("note") or "").strip() or (f"Imported from {pack}" if pack else "")),
        })
        observations.append((key, day, value))

    for key, payload in series_seen.items():
        counts = tiers.get(key, {})
        payload["quality"] = weakest_quality(counts)
        parts = [payload["note"]]
        sources = provenance.get(key) or []
        if len(sources) > 1:
            # Named, not counted: "assembled from 2 sources" tells a reader nothing they can check.
            parts.append("Assembled from " + "; ".join(sources))
        if len(counts) > 1:
            # The series took the weakest tier, which on a mixed series understates most of it. The
            # mix is written down so a caption can say where the weak months are instead of letting
            # 24 digitised months quietly condemn 127 exact ones.
            parts.append("Quality mix: " + ", ".join(
                f"{count} {tier}" for tier, count in
                sorted(counts.items(), key=lambda item: QUALITY_TIERS.index(item[0]))))
        payload["note"] = "; ".join(part for part in parts if part)

    inserted = 0
    already = 0
    with session_factory() as session:
        for key, payload in series_seen.items():
            existing = session.scalar(
                select(ReferenceSeriesModel).where(ReferenceSeriesModel.series_id == key)
            )
            if existing is None:
                session.add(ReferenceSeriesModel(**payload))
            else:
                # A re-import may carry a corrected label, quality or url; the series is the same.
                for field, value in payload.items():
                    if field != "series_id" and value:
                        setattr(existing, field, value)
        session.flush()

        held = {
            row for row in session.scalars(
                select(ReferenceObservationModel.evidence_id)
            )
        }
        for key, day, value in observations:
            evidence = _evidence_id(key, day)
            if evidence in held:
                already += 1
                continue
            held.add(evidence)
            session.add(ReferenceObservationModel(
                series_id=key,
                observation_date=datetime(day.year, day.month, day.day, tzinfo=timezone.utc),
                value=value, evidence_id=evidence,
            ))
            inserted += 1
        session.commit()

    return {"status": "ok", "pack": pack, "series": len(series_seen),
            "rows": len(observations), "inserted": inserted, "already_present": already,
            "skipped": len(skipped), "skipped_rows": skipped[:20]}


def import_reference_packs(session_factory, directories: Iterable[Path | str] | Path | str = DEFAULT_DIR,
                           packs: Iterable[Pack] = PACKS) -> list[dict]:
    """Load the public pack directory, then every named pack. A missing file is not an error.

    Two ways in, because they answer different questions. Anything dropped into data/reference is
    public upstream history and is loaded on sight. A pack that arrives under terms - EUROPUR - is
    named here with those terms attached, so its rights class is a property of the declaration and
    cannot be lost by a file being moved or copied.
    """
    if isinstance(directories, (str, Path)):
        directories = [directories]
    results: list[dict] = []
    seen: set[Path] = set()

    def load(path: Path, rights_class: str) -> None:
        if path in seen or not path.is_file():
            return
        seen.add(path)
        try:
            rows = read_long_csv(path)
        except Exception as exc:
            results.append({"status": "skipped", "pack": path.name, "reason": str(exc)[:160]})
            return
        if not rows:
            results.append({"status": "skipped", "pack": path.name,
                            "reason": "no rows with chemical, date and value"})
            return
        try:
            result = import_reference_rows(session_factory, rows, pack=path.name,
                                           rights_class=rights_class)
            result["rights_class"] = rights_class
            results.append(result)
        except Exception as exc:  # one bad pack must not stop the others or the startup
            results.append({"status": "failed", "pack": path.name, "error": str(exc)})

    for directory in directories:
        folder = Path(directory)
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.csv")):
            load(path, PUBLIC)
    for pack in packs:
        load(Path(pack.path), pack.rights_class)
    return results


def list_reference_series(session_factory, *, include_internal: bool = False) -> list[dict]:
    """Every active reference series. Internal-only series are withheld unless asked for.

    Withheld by default rather than on request, because the caller that forgets to ask is the one
    that must be safe. A screen that wants EUROPUR has to say so; a screen that says nothing gets
    what HTC is free to show.
    """
    with session_factory() as session:
        query = (select(ReferenceSeriesModel).where(ReferenceSeriesModel.active.is_(True))
                 .order_by(ReferenceSeriesModel.chemical, ReferenceSeriesModel.label))
        if not include_internal:
            query = query.where(ReferenceSeriesModel.rights_class != INTERNAL_ONLY)
        series = list(session.scalars(query))
        out = []
        for row in series:
            points = list(session.scalars(
                select(ReferenceObservationModel)
                .where(ReferenceObservationModel.series_id == row.series_id)
                .order_by(ReferenceObservationModel.observation_date)
            ))
            out.append({
                "series_id": row.series_id, "chemical": row.chemical, "label": row.label,
                "rights_class": row.rights_class,
                "geography": row.geography, "unit": row.unit, "is_index": bool(row.is_index),
                "quality": row.quality, "source_type": row.source_type,
                "publisher": row.publisher, "source_url": row.source_url, "note": row.note,
                "observations": [
                    {"date": p.observation_date.date().isoformat(), "value": float(p.value)}
                    for p in points
                ],
            })
        return out
