"""The China polyol series is filed as POLYETHER_POLYOL, not POLYOL (v0.4.177)

POLYOL is a group of materials, not a material. It was the family the resolver returned for every
polyol print, and on 18 September 2026 it collided: the China series sat under POLYOL and FNAG's
European polyether purchases under POLYETHER_POLYOL, and the selector rendered both as "Polyether
Polyol". Two series, one name on screen, and nothing to tell a reader which was which.

The material is a soft-foam polyether polyol and the graph has a code for exactly that. The resolver
now returns it, and names the other chains it can recognise - graft, PHD, PIPA, visco, polyester -
rather than putting them all in one bucket.

Geography still separates the two series: China ex-works against Europe delivered. Whether they ever
join is a separate question and this migration does not answer it.
"""
from alembic import op
import sqlalchemy as sa

revision = "0035_polyol_is_named_by_its_chemistry"
down_revision = "0034_one_china_polyol_series"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    rows = bind.execute(sa.text("""
        select id, product_grade_or_chemistry, market_geography, commercial_basis,
               currency, unit, observation_type, comparability_group
        from market_identity where product_family = 'POLYOL'
    """)).fetchall()

    for row in rows:
        clash = bind.execute(sa.text("""
            select id from market_identity
            where product_family = 'POLYETHER_POLYOL'
              and coalesce(product_grade_or_chemistry, '') = coalesce(:grade, '')
              and coalesce(market_geography, '') = coalesce(:geo, '')
              and coalesce(commercial_basis, '') = coalesce(:basis, '')
              and coalesce(currency, '') = coalesce(:ccy, '')
              and coalesce(observation_type, '') = coalesce(:obs, '')
              and id <> :self
        """), {"grade": row.product_grade_or_chemistry, "geo": row.market_geography,
               "basis": row.commercial_basis, "ccy": row.currency,
               "obs": row.observation_type, "self": row.id}).fetchone()

        label = f"{row.product_grade_or_chemistry or 'no grade'} / {row.market_geography} / {row.observation_type}"
        if clash is not None:
            # A POLYETHER_POLYOL twin already exists for this exact market. Move the readings onto
            # it rather than leaving two identities that would now be identical in every field.
            moved = bind.execute(sa.text(
                "update price_observation set identity_id = :keep where identity_id = :old"),
                {"keep": clash.id, "old": row.id}).rowcount
            bind.execute(sa.text("delete from market_reference_point where identity_id = :old"),
                         {"old": row.id})
            bind.execute(sa.text("delete from market_identity where id = :old"), {"old": row.id})
            print(f"[0035] {label}: {moved} readings merged onto the existing polyether identity")
            continue

        group = (row.comparability_group or "").replace("POLYOL|", "POLYETHER_POLYOL|", 1)
        bind.execute(sa.text("""
            update market_identity
            set product_family = 'POLYETHER_POLYOL', comparability_group = :group
            where id = :id
        """), {"group": group, "id": row.id})
        print(f"[0035] {label}: renamed to POLYETHER_POLYOL")

    # Left behind by 0033: the KGI MDI identities were emptied but the rows survived, so the store
    # still reported an MDI family with nothing in it.
    empty = bind.execute(sa.text("""
        delete from market_identity
        where id not in (select distinct identity_id from price_observation)
          and id not in (select distinct identity_id from market_reference_point)
    """)).rowcount
    print(f"[0035] empty identities removed: {empty}")

    families = bind.execute(sa.text(
        "select distinct product_family from market_identity order by 1")).fetchall()
    print(f"[0035] families now in the store: {', '.join(f[0] for f in families)}")


def downgrade() -> None:
    raise NotImplementedError("0035 renames a family and may merge identities; there is no way back")
