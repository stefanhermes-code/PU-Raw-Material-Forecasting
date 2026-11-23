"""One China polymeric MDI series, and no producer announcements in it (v0.4.159)

Two faults, cleaned together, because neither can be judged while the other stands.

THE READINGS. The Longzhong collector took any four-to-six digit number followed by 元/吨 from
anywhere on the Oilchem page. That page carries no price table - the only such number on it is a
headline:

    [聚合MDI]：北方某大厂9月中旬一口价执行18500元/吨

one northern producer's fixed price for mid-September. It was filed as Longzhong's market price, and
because the headline stayed at the top of the list it was filed again every day from 11 to 16
September 2026. Six of the twelve Longzhong readings are that one announcement, and on the 15th and
16th it outvoted the real market price of 18,000. The connector now reads a published price table or
the Mysteel polyurethane block and nothing else; this clears what it already wrote.

A producer's asking price is worth having. It is evidence of what a producer wants, and it belongs
in the producer-signals lane, which reads the same page's headline stream for exactly that.

THE IDENTITIES. Three where there is one market. ECHEMI labels the grade "PM200" and Longzhong says
"PMDI", and grade is part of the comparability group, so the store made two markets of them; a third
carries a single Longzhong reading from 7 September under attribution that has since changed. They
are the same thing: polymeric MDI, China, ex-works, yuan per tonne. Everything joins the identity
with no grade qualifier, the way TDI China did in 0021 and 0026.

Every consolidated point for those identities is dropped so the series is rebuilt from what is
actually left.
"""
from alembic import op
import sqlalchemy as sa

revision = "0028_one_china_mdi_series"
down_revision = "0027_every_price_carries_a_date"
branch_labels = None
depends_on = None

# The page whose only price-shaped number is a producer's announcement.
HEADLINE_PAGE = "list.oilchem.net/117/6600"


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if not {"price_observation", "market_identity"} <= tables:
        return

    identities = bind.execute(sa.text(
        "SELECT id, product_grade_or_chemistry FROM market_identity "
        "WHERE product_family = 'PMDI' AND market_geography = 'China' AND commercial_basis = 'EXW' "
        "AND currency = 'CNY'"
    )).mappings().all()
    if not identities:
        print("[0028] no China PMDI ex-works identities; nothing to do")
        return
    ids = [row["id"] for row in identities]
    print(f"[0028] China PMDI ex-works identities found: {len(ids)} "
          f"{sorted(str(r['product_grade_or_chemistry']) for r in identities)}")

    # --- the producer announcements -------------------------------------------------------------
    placeholders = ",".join(f":id{n}" for n in range(len(ids)))
    params = {f"id{n}": value for n, value in enumerate(ids)}
    removed = 0
    if "raw_evidence" in tables:
        result = bind.execute(sa.text(
            f"DELETE FROM price_observation WHERE identity_id IN ({placeholders}) "
            "AND evidence_id IN (SELECT content_hash FROM raw_evidence "
            "                    WHERE source_url LIKE :page)"
        ), {**params, "page": f"%{HEADLINE_PAGE}%"})
        removed = int(result.rowcount or 0)
    print(f"[0028] readings taken from the headline page, removed: {removed}")

    # --- one identity ---------------------------------------------------------------------------
    survivor = bind.execute(sa.text(
        "SELECT id FROM market_identity "
        "WHERE product_family = 'PMDI' AND market_geography = 'China' AND commercial_basis = 'EXW' "
        "AND currency = 'CNY' AND (product_grade_or_chemistry IS NULL "
        "                          OR product_grade_or_chemistry = 'PMDI') "
        "ORDER BY (SELECT COUNT(*) FROM price_observation "
        "          WHERE identity_id = market_identity.id) DESC LIMIT 1"
    )).scalar()
    if survivor is None:
        survivor = ids[0]

    # The grade qualifier goes with them. "PMDI" on a series whose product family is already PMDI
    # says nothing, and "PM200" is one publisher's name for the same material.
    bind.execute(sa.text(
        "UPDATE market_identity SET product_grade_or_chemistry = NULL, "
        "comparability_group = 'PMDI|UNKNOWN|China|EXW|UNKNOWN|CNY|t' WHERE id = :id"
    ), {"id": survivor})

    moved = 0
    for identity_id in ids:
        if identity_id == survivor:
            continue
        result = bind.execute(sa.text(
            "UPDATE price_observation SET identity_id = :survivor WHERE identity_id = :old"
        ), {"survivor": survivor, "old": identity_id})
        moved += int(result.rowcount or 0)
    print(f"[0028] readings moved onto the one identity: {moved}")

    # A publisher that printed the same price for the same day on both the old identity and the new
    # one is one fact with two rows after the move.
    doubled = bind.execute(sa.text(
        "DELETE FROM price_observation WHERE id IN ("
        "  SELECT id FROM price_observation p WHERE identity_id = :id AND EXISTS ("
        "    SELECT 1 FROM price_observation q WHERE q.identity_id = p.identity_id"
        "      AND q.source_id = p.source_id AND q.price = p.price"
        "      AND q.observation_date = p.observation_date AND q.id < p.id))"
    ), {"id": survivor})
    print(f"[0028] duplicate rows collapsed after the move: {int(doubled.rowcount or 0)}")

    emptied = 0
    for identity_id in ids:
        if identity_id == survivor:
            continue
        held = bind.execute(sa.text(
            "SELECT COUNT(*) FROM price_observation WHERE identity_id = :id"), {"id": identity_id}
        ).scalar()
        if held:
            continue
        bind.execute(sa.text("DELETE FROM market_identity WHERE id = :id"), {"id": identity_id})
        emptied += 1
    print(f"[0028] emptied identities removed: {emptied}")

    if "market_reference_point" in tables:
        cleared = bind.execute(sa.text(
            f"DELETE FROM market_reference_point WHERE identity_id IN ({placeholders})"), params)
        print(f"[0028] consolidated points cleared for rebuild: {int(cleared.rowcount or 0)}")

    left = bind.execute(sa.text(
        "SELECT COUNT(*) FROM price_observation WHERE identity_id = :id"), {"id": survivor}).scalar()
    print(f"[0028] readings now on the one China MDI ex-works series: {left}")


def downgrade():
    # A producer's announcement that was filed as a market price is not restored, and three
    # identities where there is one market are not put back.
    pass
