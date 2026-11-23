from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Iterable

from app.domain.schemas import ExtractedRecord
from raw_material_forecasting.units import MT
from raw_material_forecasting import price_kinds


@dataclass(frozen=True)
class InternalHistoryProfile:
    sheet: str
    product: str
    grade: str | None
    geography: str
    currency: str
    unit: str
    commercial_basis: str
    packaging: str
    date_column: str = "Posting Date"
    price_column: str = "Price after Disc."
    supplier_column: str = "G/L Acct/BP Name"
    observation_type: str = price_kinds.DEAL


def _dt(value) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    if isinstance(value, str):
        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(value.strip(), fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def load_internal_workbook(path: str | Path, profile: InternalHistoryProfile, *, source_url: str) -> list[ExtractedRecord]:
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True, read_only=True)
    ws = wb[profile.sheet]
    rows = ws.iter_rows(values_only=True)
    headers = [str(v).strip() if v is not None else "" for v in next(rows)]
    index = {name: i for i, name in enumerate(headers)}
    required = [profile.date_column, profile.price_column]
    missing = [name for name in required if name not in index]
    if missing:
        raise ValueError(f"missing required columns: {', '.join(missing)}")
    seen: set[tuple[str, float, str]] = set()
    out: list[ExtractedRecord] = []
    for row in rows:
        dt = _dt(row[index[profile.date_column]])
        raw_price = row[index[profile.price_column]]
        if dt is None or raw_price in (None, ""):
            continue
        try:
            price = float(raw_price)
        except (TypeError, ValueError):
            continue
        supplier = ""
        if profile.supplier_column in index and row[index[profile.supplier_column]] is not None:
            supplier = str(row[index[profile.supplier_column]]).strip()
        key = (dt.date().isoformat(), price, supplier)
        if key in seen:
            continue
        seen.add(key)
        data = {
            "type": "price",
            "product": profile.product,
            "grade": profile.grade,
            "market_geography": profile.geography,
            "commercial_basis": profile.commercial_basis,
            "packaging": profile.packaging,
            "currency": profile.currency,
            "unit": profile.unit,
            "price": price,
            "observation_type": profile.observation_type,
            "observation_date": dt,
            "supplier": supplier or None,
            "provenance_file": Path(path).name,
        }
        out.append(ExtractedRecord(kind="price", source_url=source_url, publication_date=dt, data=data))
    return sorted(out, key=lambda r: (r.data["observation_date"], r.data["price"], r.data.get("supplier") or ""))


def load_curated_polyol_workbook(path: str | Path, *, sheet: str = "Raw_Price_Points") -> list[ExtractedRecord]:
    """Load only the workbook's explicitly approved exact MW3000/main-series rows."""
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True, read_only=True)
    ws = wb[sheet]
    rows = ws.iter_rows(values_only=True)
    headers = [str(v).strip() if v is not None else "" for v in next(rows)]
    index = {name: i for i, name in enumerate(headers)}
    required = {
        "Observation_Date", "Country_Region", "Product_Grade", "Source_Price_CNY_per_MT",
        "Include_Main_Series", "Source_URL", "Source_Name", "Source_Basis",
    }
    missing = sorted(required - index.keys())
    if missing:
        raise ValueError(f"missing required columns: {', '.join(missing)}")

    out: list[ExtractedRecord] = []
    seen: set[tuple[str, float, str]] = set()
    for row in rows:
        include = str(row[index["Include_Main_Series"]] or "").strip().upper()
        if include != "Y":
            continue
        dt = _dt(row[index["Observation_Date"]])
        if dt is None:
            continue
        try:
            price = float(row[index["Source_Price_CNY_per_MT"]])
        except (TypeError, ValueError):
            continue
        source_url = str(row[index["Source_URL"]] or "").strip()
        source_name = str(row[index["Source_Name"]] or "").strip()
        key = (dt.date().isoformat(), price, source_url)
        if key in seen:
            continue
        seen.add(key)
        data = {
            "type": "price",
            "product": "Polyether polyol MW3000",
            "grade": str(row[index["Product_Grade"]] or "MW3000").strip(),
            "market_geography": str(row[index["Country_Region"]] or "China").strip(),
            "commercial_basis": str(row[index["Source_Basis"]] or "CHINA_DOMESTIC_QUOTE").strip(),
            "packaging": "UNSPECIFIED",
            "currency": "CNY",
            "unit": MT,
            "price": price,
            "observation_type": price_kinds.ASK,
            "observation_date": dt,
            "supplier": str(row[index.get("Supplier_Seller", -1)] or "").strip() or None if "Supplier_Seller" in index else None,
            "source_name": source_name or None,
            "provenance_file": Path(path).name,
            "grade_fit": str(row[index.get("Grade_Fit", -1)] or "").strip() or None if "Grade_Fit" in index else None,
        }
        out.append(ExtractedRecord(kind="price", source_url=source_url, publication_date=dt, data=data))
    return sorted(out, key=lambda r: (r.data["observation_date"], r.data["price"], r.source_url))
