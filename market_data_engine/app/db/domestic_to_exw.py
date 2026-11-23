"""Fold 'DOMESTIC' China CNY market identities into their EXW twins (idempotent).

Used by migrations 0003 and 0004. Governance decision 2026-09-08: every China domestic
CNY market price is EXW; 'DOMESTIC' is not an Incoterm and must not exist as a basis.
"""
from __future__ import annotations

import sqlalchemy as sa

MATCH_COLUMNS = (
    "product_family", "product_grade_or_chemistry", "origin", "destination",
    "market_geography", "packaging", "currency", "unit", "observation_type",
)


def fold_domestic_into_exw(bind) -> dict[str, int]:
    tables = set(sa.inspect(bind).get_table_names())
    stats = {"renamed": 0, "merged": 0, "duplicates_dropped": 0, "orphans_dropped": 0}
    if "market_identity" not in tables:
        return stats

    domestic = bind.execute(sa.text(
        "SELECT * FROM market_identity WHERE commercial_basis = 'DOMESTIC' AND upper(currency) IN ('CNY', 'RMB')"
    )).mappings().all()

    for d in domestic:
        where = " AND ".join(f"{c} IS :{c}" for c in MATCH_COLUMNS)
        params = {c: d[c] for c in MATCH_COLUMNS}
        target = bind.execute(sa.text(
            f"SELECT id FROM market_identity WHERE commercial_basis = 'EXW' AND {where}"
        ), params).scalar()

        if target is None:
            bind.execute(sa.text(
                "UPDATE market_identity SET commercial_basis = 'EXW', "
                "comparability_group = replace(comparability_group, '|DOMESTIC|', '|EXW|') WHERE id = :id"
            ), {"id": d["id"]})
            stats["renamed"] += 1
            continue

        if "price_observation" in tables:
            # Rows already present under the EXW twin (same source + evidence) are duplicates.
            res = bind.execute(sa.text(
                "DELETE FROM price_observation WHERE identity_id = :d AND EXISTS ("
                "SELECT 1 FROM price_observation e WHERE e.identity_id = :e "
                "AND e.source_id = price_observation.source_id AND e.evidence_id = price_observation.evidence_id)"
            ), {"d": d["id"], "e": target})
            stats["duplicates_dropped"] += res.rowcount or 0
            bind.execute(sa.text("UPDATE price_observation SET identity_id = :e WHERE identity_id = :d"), {"e": target, "d": d["id"]})
        if "market_reference_point" in tables:
            rows = bind.execute(sa.text(
                "SELECT id, reference_date FROM market_reference_point WHERE identity_id = :d ORDER BY reference_date, version"
            ), {"d": d["id"]}).mappings().all()
            for row in rows:
                max_version = bind.execute(sa.text(
                    "SELECT COALESCE(MAX(version), 0) FROM market_reference_point WHERE identity_id = :e AND reference_date = :dt"
                ), {"e": target, "dt": row["reference_date"]}).scalar()
                bind.execute(sa.text(
                    "UPDATE market_reference_point SET identity_id = :e, version = :v WHERE id = :id"
                ), {"e": target, "v": int(max_version) + 1, "id": row["id"]})
        bind.execute(sa.text("DELETE FROM market_identity WHERE id = :id"), {"id": d["id"]})
        stats["merged"] += 1

    if "market_reference_point" in tables:
        bind.execute(sa.text(
            "DELETE FROM market_reference_point WHERE identity_id IN ("
            "SELECT id FROM market_identity WHERE commercial_basis = 'DOMESTIC' "
            "AND id NOT IN (SELECT identity_id FROM price_observation))"
        ))
    res = bind.execute(sa.text(
        "DELETE FROM market_identity WHERE commercial_basis = 'DOMESTIC' "
        "AND id NOT IN (SELECT identity_id FROM price_observation) "
        "AND id NOT IN (SELECT identity_id FROM market_reference_point)"
    ))
    stats["orphans_dropped"] += res.rowcount or 0
    return stats
