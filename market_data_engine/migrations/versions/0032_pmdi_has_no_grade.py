"""Polymeric MDI has no grade, and the research is not imported twice (v0.4.164)

Migration 0028 merged three China PMDI identities into one. The next collection made a fourth, and
the next startup made a fifth. Both had the same cause and neither was the merge.

THE GRADE. `resolve_identity` returned the publisher's own name for the product as the grade
whenever the connector did not name one. So SCI99's "Polymeric MDI", Longzhong's "PMDI" and ECHEMI's
"PM200" each became a grade, and grade is part of the comparability group, so each became a market.
The family already says polymeric MDI; there is no grade to carry. The resolver and both connectors
are fixed, and this folds in what they wrote before they were.

THE RE-IMPORT. `data/bootstrap_observations.json` is loaded on every launcher start, and it carried
the twenty-five SunSirs research readings that 0029 converted to yuan and joined. The loader's
duplicate check keys on identity, so readings that had moved to another identity did not match and
were inserted again - the joined copies on the consolidated series, and a second set on a freshly
minted "China domestic benchmark" identity in dollars. They are removed from the bootstrap file,
kept beside it as `bootstrap_observations_legacy_pmdi_joined.json`, and the re-imported set is
dropped here.

After this there is one China polymeric MDI ex-works series and four publishers printing onto it:
Longzhong, ECHEMI, SCI99 - whose monitor id 384 the first live collection confirmed - and the
research history behind them.
"""
from alembic import op
import sqlalchemy as sa

revision = "0032_pmdi_has_no_grade"
down_revision = "0031_one_china_polyether_polyol_series"
branch_labels = None
depends_on = None

GROUP = "PMDI|UNKNOWN|China|EXW|UNKNOWN|CNY|t"


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if not {"price_observation", "market_identity"} <= tables:
        return

    # --- the re-imported research ---------------------------------------------------------------
    # Only the copies on a NON ex-works identity: those are the ones the bootstrap minted. The
    # joined copies stay where 0029 put them.
    reimported = bind.execute(sa.text(
        "DELETE FROM price_observation WHERE source_id = 'htc_legacy_pmdi_research' "
        "AND identity_id IN (SELECT id FROM market_identity WHERE product_family = 'PMDI' "
        "                    AND commercial_basis <> 'EXW')"
    ))
    print(f"[0032] research readings re-imported by the bootstrap, removed: {int(reimported.rowcount or 0)}")

    # --- one identity ----------------------------------------------------------------------------
    rows = bind.execute(sa.text(
        "SELECT id, product_grade_or_chemistry, "
        "  (SELECT COUNT(*) FROM price_observation WHERE identity_id = market_identity.id) AS held "
        "FROM market_identity WHERE product_family = 'PMDI' AND market_geography = 'China' "
        "AND commercial_basis = 'EXW' AND currency = 'CNY' ORDER BY held DESC, id"
    )).mappings().all()
    if not rows:
        print("[0032] no China PMDI ex-works identity; nothing to do")
        return
    for row in rows:
        print(f"[0032] found: grade={row['product_grade_or_chemistry']!r} readings={row['held']}")
    survivor = rows[0]["id"]

    bind.execute(sa.text(
        "UPDATE market_identity SET product_grade_or_chemistry = NULL, comparability_group = :group "
        "WHERE id = :id"), {"group": GROUP, "id": survivor})

    moved = 0
    for row in rows[1:]:
        result = bind.execute(sa.text(
            "UPDATE price_observation SET identity_id = :survivor WHERE identity_id = :old"
        ), {"survivor": survivor, "old": row["id"]})
        moved += int(result.rowcount or 0)
    print(f"[0032] readings moved onto the one series: {moved}")

    doubled = bind.execute(sa.text(
        "DELETE FROM price_observation WHERE id IN ("
        "  SELECT id FROM price_observation p WHERE identity_id = :id AND EXISTS ("
        "    SELECT 1 FROM price_observation q WHERE q.identity_id = p.identity_id"
        "      AND q.source_id = p.source_id AND q.price = p.price"
        "      AND q.observation_date = p.observation_date AND q.id < p.id))"
    ), {"id": survivor})
    print(f"[0032] duplicate rows collapsed: {int(doubled.rowcount or 0)}")

    emptied = bind.execute(sa.text(
        "DELETE FROM market_identity WHERE product_family = 'PMDI' AND id <> :id "
        "AND NOT EXISTS (SELECT 1 FROM price_observation WHERE identity_id = market_identity.id)"
    ), {"id": survivor})
    print(f"[0032] emptied identities removed: {int(emptied.rowcount or 0)}")

    if "market_reference_point" in tables:
        cleared = bind.execute(sa.text(
            "DELETE FROM market_reference_point WHERE identity_id = :id"), {"id": survivor})
        print(f"[0032] consolidated points cleared for rebuild: {int(cleared.rowcount or 0)}")

    total, sources, first, last = bind.execute(sa.text(
        "SELECT COUNT(*), COUNT(DISTINCT source_id), MIN(observation_date), MAX(observation_date) "
        "FROM price_observation WHERE identity_id = :id"), {"id": survivor}).fetchone()
    print(f"[0032] the China polymeric MDI ex-works series holds {total} readings from {sources} "
          f"sources, {str(first)[:10]} to {str(last)[:10]}")


def downgrade():
    # A grade minted from a publisher's spelling is not restored.
    pass
