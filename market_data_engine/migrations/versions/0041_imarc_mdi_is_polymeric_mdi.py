"""IMARC's MDI is polymeric MDI (v0.4.214)

Stefan, 22 September 2026: in flexible foam MDI means pMDI. IMARC's readings, collected as MDI on
21 September, move to the PMDI family, which makes Europe's the live pMDI Europe price. Nothing
else is filed as MDI; the move is limited to IMARC's own rows.
"""
from alembic import op
import sqlalchemy as sa

revision = "0041_imarc_mdi_is_polymeric_mdi"
down_revision = "0040_imarc_tdi_europe_july_from_charlies_record"
branch_labels = None
depends_on = None

SOURCE = "imarc_mdi"


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if not {"price_observation", "market_identity"} <= tables:
        return
    held = bind.execute(sa.text(
        "SELECT DISTINCT i.id, i.market_geography, i.commercial_basis, i.currency, i.unit, "
        "i.observation_type, i.product_grade_or_chemistry, i.packaging FROM market_identity i "
        "JOIN price_observation p ON p.identity_id = i.id "
        "WHERE p.source_id = :source AND i.product_family = 'MDI'"), {"source": SOURCE}).mappings().all()
    moved = 0
    for row in held:
        target = bind.execute(sa.text(
            "SELECT id FROM market_identity WHERE product_family = 'PMDI' "
            "AND market_geography = :geo AND commercial_basis = :basis AND currency = :currency "
            "AND unit = :unit AND observation_type = :otype "
            "AND COALESCE(product_grade_or_chemistry, '') = COALESCE(:grade, '') "
            "AND COALESCE(packaging, '') = COALESCE(:packaging, '') LIMIT 1"),
            {"geo": row["market_geography"], "basis": row["commercial_basis"], "currency": row["currency"],
             "unit": row["unit"], "otype": row["observation_type"],
             "grade": row["product_grade_or_chemistry"], "packaging": row["packaging"]}).scalar()
        if target is None:
            group = "|".join(["PMDI", row["product_grade_or_chemistry"] or "UNKNOWN", row["market_geography"] or "UNKNOWN",
                              row["commercial_basis"] or "UNKNOWN", row["packaging"] or "UNKNOWN",
                              row["currency"], row["unit"]])
            bind.execute(sa.text(
                "UPDATE market_identity SET product_family = 'PMDI', comparability_group = :group WHERE id = :id"),
                {"group": group, "id": row["id"]})
        else:
            bind.execute(sa.text(
                "UPDATE price_observation SET identity_id = :target WHERE identity_id = :id AND source_id = :source"),
                {"target": target, "id": row["id"], "source": SOURCE})
            bind.execute(sa.text(
                "DELETE FROM market_identity WHERE id = :id AND NOT EXISTS "
                "(SELECT 1 FROM price_observation WHERE identity_id = :id)"), {"id": row["id"]})
            row = dict(row); row["id"] = target
        if "market_reference_point" in tables:
            bind.execute(sa.text("DELETE FROM market_reference_point WHERE identity_id = :id"), {"id": row["id"]})
        moved += 1
    print(f"[0041] IMARC MDI series moved to polymeric MDI: {moved}")


def downgrade():
    pass
