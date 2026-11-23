"""IMARC's Europe TDI price for July 2026, from Charlie's record (v0.4.213)

Stefan, 21 September 2026: put the July figure in.

IMARC's page showed Europe TDI at USD 2.53/kg for July 2026 when Charlie checked it on 16 September
2026. By 21 September the page had moved on to August, so the figure can no longer be read from
IMARC itself; it comes in on Charlie's record of the page and says so in its evidence row.

It is filed as IMARC's reading, in the same market the collector writes to (TDI, Europe, regional
assessment, USD/t), so it merges with IMARC's other months and Business Analytiq's. Should IMARC
ever print July again, the collector's reading replaces this one under the revision rule.
"""
import hashlib
import uuid
from datetime import datetime

from alembic import op
import sqlalchemy as sa

revision = "0040_imarc_tdi_europe_july_from_charlies_record"
down_revision = "0039_a_producer_price_carries_a_delivery_term"
branch_labels = None
depends_on = None

SOURCE = "imarc_tdi"
URL = "https://www.imarcgroup.com/toluene-diisocyanate-pricing-report"
DAY = "2026-07-01 00:00:00.000000"
PRICE = 2530.0          # USD 2.53/kg, as recorded
RECORD = ("IMARC Europe TDI July 2026: USD 2.53/kg. Recorded from the public page by Charlie "
          "(ChatGPT) on 16 September 2026; no longer shown on the page on 21 September 2026.")


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if not {"price_observation", "market_identity", "raw_evidence"} <= tables:
        return

    identity = bind.execute(sa.text(
        "SELECT i.id FROM market_identity i JOIN price_observation p ON p.identity_id = i.id "
        "WHERE p.source_id = :source AND i.product_family = 'TDI' AND i.market_geography = 'Europe' "
        "AND i.commercial_basis = 'REGIONAL_MARKET' AND i.currency = 'USD' LIMIT 1"
    ), {"source": SOURCE}).scalar()
    if identity is None:
        print("[0040] IMARC has not collected Europe TDI yet; nothing to join July to")
        return
    if bind.execute(sa.text(
        "SELECT 1 FROM price_observation WHERE identity_id = :identity AND source_id = :source "
        "AND substr(observation_date, 1, 10) = '2026-07-01'"
    ), {"identity": identity, "source": SOURCE}).scalar():
        print("[0040] IMARC July 2026 already in the store; nothing done")
        return
    weight = bind.execute(sa.text(
        "SELECT weight FROM price_observation WHERE identity_id = :identity AND source_id = :source LIMIT 1"
    ), {"identity": identity, "source": SOURCE}).scalar()

    content_hash = hashlib.sha256(RECORD.encode("utf-8")).hexdigest()
    bind.execute(sa.text(
        "INSERT INTO raw_evidence (id, source_id, ingestion_run_id, source_url, content_hash, "
        "content_location, publication_date, collection_timestamp, rights_status) "
        "VALUES (:id, :source, NULL, :url, :hash, :location, :day, :now, 'APPROVED')"
    ), {"id": str(uuid.uuid4()), "source": SOURCE, "url": URL, "hash": content_hash,
        "location": RECORD, "day": DAY, "now": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")})
    bind.execute(sa.text(
        "INSERT INTO price_observation (id, identity_id, source_id, evidence_id, price, observation_date, "
        "publication_date, available_from, weight, native_price, native_currency) "
        "VALUES (:id, :identity, :source, :hash, :price, :day, :day, :day, :weight, NULL, NULL)"
    ), {"id": str(uuid.uuid4()), "identity": identity, "source": SOURCE, "hash": content_hash,
        "price": PRICE, "day": DAY, "weight": float(weight)})
    if "market_reference_point" in tables:
        bind.execute(sa.text("DELETE FROM market_reference_point WHERE identity_id = :identity"),
                     {"identity": identity})
    print("[0040] IMARC Europe TDI July 2026 added at USD 2,530/t; series cleared for rebuild")


def downgrade():
    pass
