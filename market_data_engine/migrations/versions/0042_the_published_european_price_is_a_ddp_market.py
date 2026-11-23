"""The published Western Europe series becomes a DDP market (v0.4.220)

Stefan, 23 September 2026: the Incoterm selector is a selector. A DDP price has no business on a
chart whose selector says CFR or EXW, and the way to stop that is not to hide the line - it is to
give the series the market it belongs to.

So the published Western Europe series for TDI, polymeric MDI and flexible slabstock polyol is a
market of its own on DDP, in the euro it is published in, with its own price card and its own
forecast. It carries 151 monthly readings from January 2014, which is the longest European history
the store holds and the history the Europe method was measured on.

No source is named on screen, here or anywhere else. The series is licensed for internal use and the
rights row says so; what a reader sees is a price and a delivery term.
"""
import hashlib
import uuid
from datetime import datetime

from alembic import op
import sqlalchemy as sa

revision = "0042_the_published_european_price_is_a_ddp_market"
down_revision = "0041_imarc_mdi_is_polymeric_mdi"
branch_labels = None
depends_on = None

SOURCE = "europe_published_ddp"
# Store code -> the reference series' label.
SERIES = {
    "TDI": "TDI WE DDP",
    "PMDI": "MDI WE DDP",
    "POLYETHER_POLYOL": "Flexible slabstock polyol WE DDP",
}


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    needed = {"price_observation", "market_identity", "raw_evidence", "reference_series",
              "reference_observation", "source_registry"}
    if not needed <= tables:
        return

    if not bind.execute(sa.text("SELECT 1 FROM source_registry WHERE source_id = :s"),
                        {"s": SOURCE}).scalar():
        bind.execute(sa.text(
            "INSERT INTO source_registry (id, source_id, publisher, source_name, base_url, "
            "rights_class, rights_status, collection_method, refresh_frequency, active, "
            "reliability_level, last_successful_collection, notes) VALUES (:id, :s, :pub, :name, '', "
            "'INTERNAL_ONLY', 'APPROVED', 'REFERENCE_SERIES', 'MONTHLY', 1, 4, NULL, :notes)"
        ), {"id": str(uuid.uuid4()), "s": SOURCE, "pub": "Not disclosed",
            "name": "Published Western Europe monthly price series",
            "notes": "The reference series already in the store, published as its own DDP market. "
                     "Licensed for internal use: never named on a customer screen."})

    added = 0
    for family, label in SERIES.items():
        series_id = bind.execute(sa.text(
            "SELECT series_id FROM reference_series WHERE label = :label AND is_index = 0 LIMIT 1"
        ), {"label": label}).scalar()
        if series_id is None:
            print(f"[0042] no published series for {family}; skipped")
            continue
        rows = list(bind.execute(sa.text(
            "SELECT observation_date, value FROM reference_observation WHERE series_id = :s "
            "ORDER BY observation_date"), {"s": series_id}))
        if not rows:
            continue

        group = f"{family}|UNKNOWN|Europe|DDP|UNKNOWN|EUR|t"
        identity = bind.execute(sa.text(
            "SELECT id FROM market_identity WHERE comparability_group = :g AND "
            "observation_type = 'market_price' LIMIT 1"), {"g": group}).scalar()
        if identity is None:
            identity = str(uuid.uuid4())
            bind.execute(sa.text(
                "INSERT INTO market_identity (id, product_family, product_grade_or_chemistry, origin, "
                "destination, market_geography, commercial_basis, packaging, currency, unit, "
                "observation_type, comparability_group) VALUES (:id, :family, NULL, 'UNKNOWN', "
                "'Europe', 'Europe', 'DDP', NULL, 'EUR', 't', 'market_price', :g)"
            ), {"id": identity, "family": family, "g": group})

        record = (f"{label}: {len(rows)} monthly readings, "
                  f"{str(rows[0][0])[:10]} to {str(rows[-1][0])[:10]}, EUR per tonne.")
        evidence = hashlib.sha256(f"{SOURCE}|{label}".encode("utf-8")).hexdigest()
        if not bind.execute(sa.text("SELECT 1 FROM raw_evidence WHERE content_hash = :h"),
                            {"h": evidence}).scalar():
            bind.execute(sa.text(
                "INSERT INTO raw_evidence (id, source_id, ingestion_run_id, source_url, content_hash, "
                "content_location, publication_date, collection_timestamp, rights_status) "
                "VALUES (:id, :s, NULL, '', :h, :rec, :day, :now, 'APPROVED')"
            ), {"id": str(uuid.uuid4()), "s": SOURCE, "h": evidence, "rec": record,
                "day": str(rows[-1][0]), "now": _now()})

        for day, value in rows:
            stamp = str(day)
            if bind.execute(sa.text(
                "SELECT 1 FROM price_observation WHERE identity_id = :i AND source_id = :s "
                "AND substr(observation_date, 1, 10) = :d"
            ), {"i": identity, "s": SOURCE, "d": stamp[:10]}).scalar():
                continue
            bind.execute(sa.text(
                "INSERT INTO price_observation (id, identity_id, source_id, evidence_id, price, "
                "observation_date, publication_date, available_from, weight, native_price, "
                "native_currency) VALUES (:id, :i, :s, :e, :price, :day, :day, :day, 1.0, NULL, NULL)"
            ), {"id": str(uuid.uuid4()), "i": identity, "s": SOURCE, "e": evidence,
                "price": float(value), "day": stamp})
            added += 1
        if "market_reference_point" in tables:
            bind.execute(sa.text("DELETE FROM market_reference_point WHERE identity_id = :i"),
                         {"i": identity})
    print(f"[0042] published European DDP markets: {added} monthly readings added")


def downgrade():
    pass
