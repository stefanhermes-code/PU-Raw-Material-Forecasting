"""The legacy polymeric MDI research joins the one China MDI series (v0.4.159)

Stefan, 16 September 2026: join.

What joins. Twenty-five SunSirs China domestic benchmark readings, 1 October 2025 to 27 April 2026,
carried in the store in US dollars. They are the only MDI history there is - the four publishers only
started printing on 7 September 2026 - and without them the series is nine days long.

They are in the store TWICE, the same twenty-five dates at the same twenty-five values, filed once
under "REGIONAL_MARKET" and once under "China domestic benchmark/market avg". One of the two is kept.

Two things are done to them on the way in, both of which the consolidated series already does to
every other contributor:

FX. Each reading is converted at the ECB cross of the day the price was struck, not at today's rate.
A single rate applied to eighteen months of history manufactures movement that never happened - the
fault that made a restated Wanhua series rubbish in August. `native_price` and `native_currency` keep
the dollars they arrived in.

TAX. A SunSirs China domestic benchmark is a domestic assessment, and Chinese domestic chemical
assessments are quoted 含税 - inclusive of the 13% VAT. The dollars are a conversion of a
VAT-inclusive yuan price, so the tax is still inside them, and the consolidator strips it the same
way it strips it from Longzhong's and ECHEMI's prints.

And what is not hidden: the basis. These are a national benchmark average rather than a quoted
ex-works price, and they are filed on the ex-works series by decision, the way FoamPartner's
Changzhou prices and KGI's delivered purchases were filed onto TDI China in 0026. There is also a
gap - the research stops on 27 April 2026 and the publishers start on 7 September, so four months
in the middle have nothing in them and no forecast should pretend otherwise.
"""
from alembic import op
import sqlalchemy as sa

revision = "0029_legacy_mdi_research_joins_the_series"
down_revision = "0028_one_china_mdi_series"
branch_labels = None
depends_on = None

SOURCE = "htc_legacy_pmdi_research"
# A rate more than this far from the price's own date is a different market, not a late fixing.
LOOKBACK_DAYS = 10


def _cross(bind, day: str) -> float | None:
    """CNY per USD on `day`, from the last ECB fixing on or before it."""
    rates = {}
    for currency in ("USD", "CNY"):
        value = bind.execute(sa.text(
            "SELECT rate FROM fx_rate WHERE currency = :currency AND base = 'EUR' "
            "AND substr(rate_date, 1, 10) <= :day "
            "AND julianday(:day) - julianday(substr(rate_date, 1, 10)) <= :window "
            "ORDER BY rate_date DESC LIMIT 1"
        ), {"currency": currency, "day": day, "window": LOOKBACK_DAYS}).scalar()
        if not value:
            return None
        rates[currency] = float(value)
    return rates["CNY"] / rates["USD"]


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if not {"price_observation", "market_identity", "fx_rate"} <= tables:
        return

    target = bind.execute(sa.text(
        "SELECT id FROM market_identity WHERE product_family = 'PMDI' "
        "AND market_geography = 'China' AND commercial_basis = 'EXW' AND currency = 'CNY' LIMIT 1"
    )).scalar()
    if target is None:
        print("[0029] no China PMDI ex-works series to join to; nothing done")
        return

    held = bind.execute(sa.text(
        "SELECT id, identity_id, observation_date, price FROM price_observation "
        "WHERE source_id = :source ORDER BY observation_date, id"
    ), {"source": SOURCE}).mappings().all()
    print(f"[0029] legacy research readings in the store: {len(held)}")

    kept: dict[tuple[str, float], dict] = {}
    duplicates = []
    for row in held:
        key = (str(row["observation_date"])[:10], round(float(row["price"]), 4))
        if key in kept:
            duplicates.append(row["id"])
        else:
            kept[key] = row
    for row_id in duplicates:
        bind.execute(sa.text("DELETE FROM price_observation WHERE id = :id"), {"id": row_id})
    print(f"[0029] the same reading filed under a second basis, removed: {len(duplicates)}")

    converted = unconverted = 0
    for (day, _price), row in sorted(kept.items()):
        cross = _cross(bind, day)
        if cross is None:
            # A reading whose own day has no fixing is left where it is and reported. Converting it
            # at a rate from another month would be inventing the number.
            unconverted += 1
            continue
        bind.execute(sa.text(
            "UPDATE price_observation SET identity_id = :target, price = :price, "
            "native_price = COALESCE(native_price, :native), "
            "native_currency = COALESCE(native_currency, 'USD') WHERE id = :id"
        ), {"target": target, "price": round(float(row["price"]) * cross, 2),
            "native": float(row["price"]), "id": row["id"]})
        converted += 1
    print(f"[0029] readings converted at their own day's ECB cross and joined: {converted}")
    if unconverted:
        print(f"[0029] readings left unjoined for want of a fixing on their own day: {unconverted}")

    emptied = bind.execute(sa.text(
        "DELETE FROM market_identity WHERE product_family IN ('PMDI', 'MDI') AND id <> :target "
        "AND NOT EXISTS (SELECT 1 FROM price_observation WHERE identity_id = market_identity.id)"
    ), {"target": target})
    print(f"[0029] emptied identities removed: {int(emptied.rowcount or 0)}")

    if "market_reference_point" in tables:
        cleared = bind.execute(sa.text(
            "DELETE FROM market_reference_point WHERE identity_id = :target"), {"target": target})
        print(f"[0029] consolidated points cleared for rebuild: {int(cleared.rowcount or 0)}")

    total, first, last = bind.execute(sa.text(
        "SELECT COUNT(*), MIN(observation_date), MAX(observation_date) FROM price_observation "
        "WHERE identity_id = :target"), {"target": target}).fetchone()
    print(f"[0029] the China MDI ex-works series now holds {total} readings, "
          f"{str(first)[:10]} to {str(last)[:10]}")


def downgrade():
    # The dollars each reading arrived in are kept in native_price, so nothing is lost, but the
    # series is not taken apart again.
    pass
