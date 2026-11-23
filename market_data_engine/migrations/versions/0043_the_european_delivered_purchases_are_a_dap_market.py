"""KGI's European delivered purchases become a DAP market (v0.4.220)

Stefan, 23 September 2026: these are delivered prices, so they are DAP, and they belong under DAP in
the selector rather than beside every other market as a second line.

One market per chemical, in euro per tonne, each month the average of what was bought that month
across the grades of that chemical. November 2013 to January 2017, so it is history: no forecast is
calculated on it, because a market is only forecast while its price reaches into 2026.

These are KGI's purchase prices. HTC buys no chemicals. No source is named on screen.
"""
import hashlib
import uuid
from datetime import datetime

from alembic import op
import sqlalchemy as sa

revision = "0043_the_european_delivered_purchases_are_a_dap_market"
down_revision = "0042_the_published_european_price_is_a_ddp_market"
branch_labels = None
depends_on = None

SOURCE = "europe_delivered_purchases"
FAMILIES = ("TDI", "PMDI", "POLYETHER_POLYOL")


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if not {"price_observation", "market_identity", "raw_evidence", "source_registry"} <= tables:
        return

    if not bind.execute(sa.text("SELECT 1 FROM source_registry WHERE source_id = :s"),
                        {"s": SOURCE}).scalar():
        bind.execute(sa.text(
            "INSERT INTO source_registry (id, source_id, publisher, source_name, base_url, "
            "rights_class, rights_status, collection_method, refresh_frequency, active, "
            "reliability_level, last_successful_collection, notes) VALUES (:id, :s, :pub, :name, '', "
            "'INTERNAL_ONLY', 'APPROVED', 'PURCHASE_RECORD', 'NONE', 1, 5, NULL, :notes)"
        ), {"id": str(uuid.uuid4()), "s": SOURCE, "pub": "Not disclosed",
            "name": "European delivered purchase prices 2013-2017",
            "notes": "KGI's own purchase records, averaged by month and published as a DAP market "
                     "(Stefan, 23 September 2026). Never named on a customer screen."})

    added = 0
    for family in FAMILIES:
        months = list(bind.execute(sa.text(
            "SELECT substr(p.observation_date, 1, 7) AS month, avg(p.price) "
            "FROM price_observation p JOIN market_identity i ON i.id = p.identity_id "
            "WHERE i.product_family = :family AND i.market_geography = 'Europe' "
            "AND i.commercial_basis = 'DELIVERED' AND i.observation_type = 'transaction_price' "
            "AND i.currency = 'EUR' GROUP BY month ORDER BY month"), {"family": family}))
        if not months:
            print(f"[0043] no European purchases for {family}; skipped")
            continue

        group = f"{family}|UNKNOWN|Europe|DAP|UNKNOWN|EUR|t"
        identity = bind.execute(sa.text(
            "SELECT id FROM market_identity WHERE comparability_group = :g AND "
            "observation_type = 'market_price' LIMIT 1"), {"g": group}).scalar()
        if identity is None:
            identity = str(uuid.uuid4())
            bind.execute(sa.text(
                "INSERT INTO market_identity (id, product_family, product_grade_or_chemistry, origin, "
                "destination, market_geography, commercial_basis, packaging, currency, unit, "
                "observation_type, comparability_group) VALUES (:id, :family, NULL, 'UNKNOWN', "
                "'Europe', 'Europe', 'DAP', NULL, 'EUR', 't', 'market_price', :g)"
            ), {"id": identity, "family": family, "g": group})

        record = (f"European delivered purchases of {family}: {len(months)} months, "
                  f"{months[0][0]} to {months[-1][0]}, the monthly average across grades, EUR/t.")
        evidence = hashlib.sha256(f"{SOURCE}|{family}".encode("utf-8")).hexdigest()
        if not bind.execute(sa.text("SELECT 1 FROM raw_evidence WHERE content_hash = :h"),
                            {"h": evidence}).scalar():
            bind.execute(sa.text(
                "INSERT INTO raw_evidence (id, source_id, ingestion_run_id, source_url, content_hash, "
                "content_location, publication_date, collection_timestamp, rights_status) "
                "VALUES (:id, :s, NULL, '', :h, :rec, :day, :now, 'APPROVED')"
            ), {"id": str(uuid.uuid4()), "s": SOURCE, "h": evidence, "rec": record,
                "day": f"{months[-1][0]}-01 00:00:00.000000", "now": _now()})

        for month, price in months:
            day = f"{month}-01 00:00:00.000000"
            if bind.execute(sa.text(
                "SELECT 1 FROM price_observation WHERE identity_id = :i AND source_id = :s "
                "AND substr(observation_date, 1, 7) = :m"
            ), {"i": identity, "s": SOURCE, "m": month}).scalar():
                continue
            bind.execute(sa.text(
                "INSERT INTO price_observation (id, identity_id, source_id, evidence_id, price, "
                "observation_date, publication_date, available_from, weight, native_price, "
                "native_currency) VALUES (:id, :i, :s, :e, :price, :day, :day, :day, 1.0, NULL, NULL)"
            ), {"id": str(uuid.uuid4()), "i": identity, "s": SOURCE, "e": evidence,
                "price": round(float(price), 2), "day": day})
            added += 1
        if "market_reference_point" in tables:
            bind.execute(sa.text("DELETE FROM market_reference_point WHERE identity_id = :i"),
                         {"i": identity})
    print(f"[0043] European delivered DAP markets: {added} monthly readings added")


def downgrade():
    pass
