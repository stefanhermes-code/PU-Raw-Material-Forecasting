"""Customs trade figures are market prices, and a market holds one datapoint per period (v0.5.11)

Stefan, 13 October 2026: Eurostat and UN Comtrade figures come from the commercial invoices behind
customs declarations and are market prices - "nothing else, no new type". And: "the only thing we
should do here is make sure no double datapoints are being made."

v0.5.9 and v0.5.10 stored them under types of their own and stored some splits of one market beside
the market itself. A store that ran either version is brought to the v0.5.11 shape here:

  1. The EU partner and residual series (China into Europe, other origins into Europe) are the same
     market and month as the extra-EU total. Their price rows are removed; the full records stay in
     feature_observation, where v0.5.11 keeps them as variables.
  2. Comtrade bilateral flows (a reporter's exports to one EU member state) are a split of that
     reporter's exports to the World. Their price rows are removed for the same reason.
  3. Predecessor codes (CN 39072020, HS 390720) continue the current market: their grade is cleared.
  4. Every remaining trade identity becomes market_price.
  5. Where a year holds both a Comtrade annual figure and monthly figures for one market, the annual
     one is removed.

Identities that become identical are folded into one (the 0046 rule). No price is changed.
"""
from alembic import op
import sqlalchemy as sa

revision = "0048_trade_figures_are_market_prices"
down_revision = "0047_the_extra_eu_import_total_has_no_stated_origin"
branch_labels = None
depends_on = None

TRADE_TYPES = ("trade_unit_value", "trade_export_unit_value", "derived_trade_unit_value",
               "trade_unit_value_annual", "trade_export_unit_value_annual")
MATCH = ("product_family", "product_grade_or_chemistry", "origin", "destination", "market_geography",
         "commercial_basis", "packaging", "currency", "unit", "observation_type")


def _delete_readings(bind, tables, where: str, params: dict) -> int:
    ids = [r[0] for r in bind.execute(sa.text("SELECT id FROM price_observation WHERE " + where), params)]
    for oid in ids:
        if "price_in_currency" in tables:
            bind.execute(sa.text("DELETE FROM price_in_currency WHERE observation_id = :o"), {"o": oid})
        bind.execute(sa.text("DELETE FROM price_observation WHERE id = :o"), {"o": oid})
    return len(ids)


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if not {"market_identity", "price_observation"} <= tables:
        return
    types = ", ".join(":t%d" % i for i in range(len(TRADE_TYPES)))
    tparams = {"t%d" % i: t for i, t in enumerate(TRADE_TYPES)}
    trade_ids = "SELECT id FROM market_identity WHERE observation_type IN (%s)" % types
    trade_identity_ids = {r[0] for r in bind.execute(sa.text(trade_ids), tparams)}

    # 1. EU partner and residual splits.
    split = _delete_readings(bind, tables,
        "source_id LIKE 'comext_%' AND identity_id IN (SELECT id FROM market_identity WHERE "
        "observation_type IN (" + types + ") AND market_geography = 'Europe' AND origin IS NOT NULL "
        "AND origin <> 'Europe')", tparams)
    # 2. Comtrade bilateral splits.
    bilateral = _delete_readings(bind, tables,
        "source_id LIKE 'comtrade_%' AND identity_id IN (SELECT id FROM market_identity WHERE "
        "observation_type IN (" + types + ") AND commercial_basis = 'FOB' AND destination IS NOT NULL)",
        tparams)
    # 3 and 4. Clear predecessor grades and make every trade identity a market price.
    bind.execute(sa.text("UPDATE market_identity SET product_grade_or_chemistry = NULL WHERE id IN ("
                         + trade_ids + ") AND (product_grade_or_chemistry LIKE 'CN %' OR "
                         "product_grade_or_chemistry LIKE 'HS %')"), tparams)
    renamed = bind.execute(sa.text("UPDATE market_identity SET observation_type = 'market_price' "
                                   "WHERE observation_type IN (" + types + ")"), tparams).rowcount
    # Trade identities left without readings are removed. Nothing else is touched.
    emptied = 0
    with_readings = {r[0] for r in bind.execute(sa.text("SELECT DISTINCT identity_id FROM price_observation"))}
    for iid in sorted(trade_identity_ids - with_readings):
        if "market_reference_point" in tables:
            bind.execute(sa.text("DELETE FROM market_reference_point WHERE identity_id = :i"), {"i": iid})
        if "derived_value" in tables:
            if bind.execute(sa.text("SELECT count(*) FROM derived_value WHERE identity_id = :i"), {"i": iid}).scalar():
                continue
        bind.execute(sa.text("DELETE FROM market_identity WHERE id = :i"), {"i": iid})
        emptied += 1
    # Fold identities that are now identical.
    keys = ", ".join(MATCH)
    folded = 0
    for (joined,) in list(bind.execute(sa.text(
            "SELECT group_concat(id, '|') FROM market_identity GROUP BY %s HAVING count(*) > 1" % keys))):
        ids = joined.split("|")
        if not set(ids) & trade_identity_ids:
            continue   # only groups a trade identity joined; anything else is 0046's business
        counts = {i: bind.execute(sa.text("SELECT count(*) FROM price_observation WHERE identity_id = :i"),
                                  {"i": i}).scalar() for i in ids}
        keep = sorted(ids, key=lambda i: (-counts[i], i))[0]
        for loser in ids:
            if loser == keep:
                continue
            _delete_readings(bind, tables,
                "identity_id = :l AND EXISTS (SELECT 1 FROM price_observation p WHERE p.identity_id = :k "
                "AND p.source_id = price_observation.source_id AND p.observation_date = price_observation.observation_date)",
                {"k": keep, "l": loser})
            bind.execute(sa.text("UPDATE price_observation SET identity_id = :k WHERE identity_id = :l"),
                         {"k": keep, "l": loser})
            if "derived_value" in tables:
                bind.execute(sa.text("UPDATE derived_value SET identity_id = :k WHERE identity_id = :l"),
                             {"k": keep, "l": loser})
            if "market_reference_point" in tables:
                bind.execute(sa.text("DELETE FROM market_reference_point WHERE identity_id IN (:k, :l)"),
                             {"k": keep, "l": loser})
            bind.execute(sa.text("DELETE FROM market_identity WHERE id = :l"), {"l": loser})
            folded += 1
    # 5. Annual where the same market has monthly figures in that year.
    annual = 0
    for oid, iid, day in list(bind.execute(sa.text(
            "SELECT id, identity_id, observation_date FROM price_observation WHERE source_id LIKE 'comtrade_annual_%'"))):
        year = str(day)[:4]
        has_monthly = bind.execute(sa.text(
            "SELECT count(*) FROM price_observation WHERE identity_id = :i AND source_id LIKE 'comtrade_monthly_%' "
            "AND substr(CAST(observation_date AS TEXT), 1, 4) = :y"), {"i": iid, "y": year}).scalar()
        if has_monthly:
            annual += _delete_readings(bind, tables, "id = :o", {"o": oid})
    print("[0048] EU partner/residual readings removed: %d" % split)
    print("[0048] Comtrade bilateral readings removed: %d" % bilateral)
    print("[0048] trade identities renamed to market_price: %d" % renamed)
    print("[0048] identities emptied and removed: %d; folded: %d" % (emptied, folded))
    print("[0048] Comtrade annual readings covered by monthly removed: %d" % annual)


def downgrade():
    pass
