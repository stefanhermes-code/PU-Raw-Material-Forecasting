"""ECHEMI readings are restated from the page's own table (v0.4.160)

An ECHEMI product page states its price twice and the two do not agree.

Above the table sits a summary sentence - "Ethylene oxide-propylene oxide copolymer prices across 4
categories as of Sep 11, 2026: China Domestic at 10516.667 Yuan/mt, International from 1375 ..." -
and below it the page's own labelled figure: "China Domestic Price Sep 11, 2026 12583.333Yuan/mt".

The sentence lags. Its 11 September figure, 10516.667, is the table's 8 September figure to three
decimal places. The collector took the first date-and-price anywhere on the flattened page, which is
always that sentence, so three series were stored below the market:

    polyether polyol   11 Sep   stored 10,516.67   the table says 12,583.33   20% low
    polymeric MDI      11 Sep   stored 17,266.67   the table says 18,066.67    4% low
    toluene            11 Sep   stored  7,783.33   the table says  8,833.33   12% low

Read against the table, ECHEMI and Longzhong agree to within 0.9% on every day both printed - 12,583
against 12,600 on polyol, 0.13% apart. Read against the sentence they were 20% apart, which is what
sent somebody looking.

The date was wrong too. ECHEMI leaves yesterday's table up until it publishes today's, so the
9 September polyol page still carried the 8th's figure. That reading is dropped rather than restated:
it is the 8th's price, and the 8th is already in the store.

Every figure below was re-read from the raw evidence the collection itself stored, and each row is
named by the id of the reading it corrects.
"""
from alembic import op
import sqlalchemy as sa

revision = "0030_echemi_readings_come_off_the_table"
down_revision = "0029_legacy_mdi_research_joins_the_series"
branch_labels = None
depends_on = None

# (observation id, source, stored date, stored price, the table's date, the table's price)
CORRECTIONS = (
    ("63e11610-ffd4-40df-908d-6f9d550e61d3", "echemi_pmdi", "2026-09-08", 17066.67, "2026-09-08", 17266.67),
    ("79c208b4-aa20-4c0c-8bd8-957e23bb23a6", "echemi_polyol", "2026-09-08", 10216.67, "2026-09-08", 10516.67),
    ("c81cb377-b4fd-487e-9ede-398a950f95c3", "echemi_pmdi", "2026-09-09", 17266.67, "2026-09-09", 17266.67),
    ("32ad8363-f368-4a2c-bcad-bb5ee8f484aa", "echemi_polyol", "2026-09-09", 10216.67, "2026-09-08", 10516.67),
    ("0adaaa4d-d6f3-46b1-8a66-965e657da717", "echemi_toluene", "2026-09-10", 7306.67, "2026-09-09", 8100.0),
    ("051c69e9-52a4-496f-b6a9-215dbb6a2df2", "echemi_pmdi", "2026-09-10", 17266.67, "2026-09-10", 17466.67),
    ("74d6a7d9-9d48-43ec-bda7-807ff2ca7844", "echemi_polyol", "2026-09-10", 10371.43, "2026-09-10", 11300.0),
    ("735ad3cf-5c55-499d-b1fa-a1ff6cafa697", "echemi_toluene", "2026-09-10", 7440.0, "2026-09-10", 8483.33),
    ("de2dcc3e-7b5c-4c71-9131-ad3b4ab0854d", "echemi_pmdi", "2026-09-11", 17266.67, "2026-09-11", 18066.67),
    ("b89152c8-77df-490b-9f52-1b15ea59fb61", "echemi_polyol", "2026-09-11", 10516.67, "2026-09-11", 12583.33),
    ("c4b9a21c-6779-43c7-a26b-74c919b0f0a5", "echemi_toluene", "2026-09-11", 7783.33, "2026-09-11", 8833.33),
)


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "price_observation" not in tables:
        return

    restated = dropped = absent = unchanged = 0
    touched: set[str] = set()
    for row_id, source, stored_day, stored_price, table_day, table_price in CORRECTIONS:
        held = bind.execute(sa.text(
            "SELECT identity_id, observation_date, price FROM price_observation WHERE id = :id"
        ), {"id": row_id}).mappings().first()
        if held is None:
            # Already cleaned, or a store this migration has not seen. Nothing is forced.
            absent += 1
            continue
        if str(held["observation_date"])[:10] != stored_day or round(float(held["price"]), 2) != stored_price:
            # The reading is not the one measured, so it is left exactly as it is.
            unchanged += 1
            continue
        touched.add(held["identity_id"])
        if table_day != stored_day:
            # The page was stale: this is the earlier day's price, and the earlier day is stored.
            bind.execute(sa.text("DELETE FROM price_observation WHERE id = :id"), {"id": row_id})
            dropped += 1
            continue
        bind.execute(sa.text(
            "UPDATE price_observation SET price = :price WHERE id = :id"
        ), {"price": table_price, "id": row_id})
        restated += 1

    print(f"[0030] readings restated from the page's table: {restated}")
    print(f"[0030] readings dropped as a stale repeat of an earlier day: {dropped}")
    if unchanged:
        print(f"[0030] readings left alone because they no longer match what was measured: {unchanged}")
    if absent:
        print(f"[0030] readings not present in this store: {absent}")

    if touched and "market_reference_point" in tables:
        cleared = 0
        for identity_id in touched:
            result = bind.execute(sa.text(
                "DELETE FROM market_reference_point WHERE identity_id = :id"), {"id": identity_id})
            cleared += int(result.rowcount or 0)
        print(f"[0030] consolidated points cleared for rebuild: {cleared}")


def downgrade():
    # A price that was read off the wrong line is not put back.
    pass
