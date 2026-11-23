from pathlib import Path
import pandas as pd

from .contracts import SourceRights
from .series_audit import IDENTITY_COLUMNS


MARKET_REQUIRED_COLUMNS = [
    "observation_date", "publication_date", "price_per_tonne",
    *IDENTITY_COLUMNS, "source_id", "rights_classification",
]

PURCHASE_REQUIRED_COLUMNS = [
    "transaction_date", "price_per_tonne", "quantity_tonnes",
    *IDENTITY_COLUMNS, "source_id",
]


def _read_csv(source) -> pd.DataFrame:
    return pd.read_csv(source)


def load_market_observations(source) -> pd.DataFrame:
    df = _read_csv(source)
    missing = [c for c in MARKET_REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError("missing required columns: " + ", ".join(missing))

    for value in df["rights_classification"].astype(str):
        if not SourceRights(value).eligible_for_model:
            raise ValueError(f"ineligible source rights: {value}")

    identities = df[IDENTITY_COLUMNS].astype(str).drop_duplicates()
    if len(identities) != 1:
        raise ValueError("MVP accepts exactly one canonical identity per file")

    df = df.copy()
    df["observation_date"] = pd.to_datetime(df["observation_date"], errors="raise")
    df["publication_date"] = pd.to_datetime(df["publication_date"], errors="raise")
    df["price_per_tonne"] = pd.to_numeric(df["price_per_tonne"], errors="raise")
    if (df["price_per_tonne"] <= 0).any():
        raise ValueError("price_per_tonne must be positive")
    if (df["publication_date"] < df["observation_date"]).any():
        raise ValueError("publication_date cannot precede observation_date")
    return df.sort_values("observation_date").reset_index(drop=True)


def load_purchase_transactions(source) -> pd.DataFrame:
    df = _read_csv(source)
    missing = [c for c in PURCHASE_REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError("missing required columns: " + ", ".join(missing))
    df = df.copy()
    df["transaction_date"] = pd.to_datetime(df["transaction_date"], errors="raise")
    df["price_per_tonne"] = pd.to_numeric(df["price_per_tonne"], errors="raise")
    df["quantity_tonnes"] = pd.to_numeric(df["quantity_tonnes"], errors="raise")
    if (df["price_per_tonne"] <= 0).any() or (df["quantity_tonnes"] <= 0).any():
        raise ValueError("purchase price and quantity must be positive")
    return df.sort_values("transaction_date").reset_index(drop=True)
