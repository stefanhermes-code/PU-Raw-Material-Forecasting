"""The startup bootstrap stops recreating the POLYOL family (v0.4.178)

Migration 0035 renamed the two China polyol identities to POLYETHER_POLYOL. The very next launcher
start put POLYOL back: `data/bootstrap_observations.json` is re-imported on every start and carried
`"product_family": "POLYOL"` written out literally on 30 seller quotes, which never pass through the
resolver. A fresh POLYOL identity was created and those 30 readings were inserted a second time,
beside the 30 the renamed identity already held.

The JSON now says POLYETHER_POLYOL, so no further start can recreate it. This migration clears up
what the one start left behind: duplicated readings are deleted, anything the twin does not already
hold is moved across, and the recreated identity is removed.

Migration 0032 documented this same re-import trap for pMDI. It was not enough to stop it happening
again, so `tests/test_bootstrap_agrees_with_the_store.py` now fails the build if the bootstrap file
names a family the resolver would not produce.
"""
from alembic import op
import sqlalchemy as sa

revision = "0036_bootstrap_stops_recreating_polyol"
down_revision = "0035_polyol_is_named_by_its_chemistry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    rows = bind.execute(sa.text("""
        select id, product_grade_or_chemistry, market_geography, commercial_basis,
               currency, unit, observation_type
        from market_identity where product_family = 'POLYOL'
    """)).fetchall()

    if not rows:
        print("[0036] no POLYOL identity in this store; nothing to clean up")
        return

    for row in rows:
        label = (f"{row.product_grade_or_chemistry or 'no grade'} / {row.market_geography}"
                 f" / {row.observation_type}")

        twin = bind.execute(sa.text("""
            select id from market_identity
            where product_family = 'POLYETHER_POLYOL'
              and coalesce(product_grade_or_chemistry, '') = coalesce(:grade, '')
              and coalesce(market_geography, '') = coalesce(:geo, '')
              and coalesce(commercial_basis, '') = coalesce(:basis, '')
              and coalesce(currency, '') = coalesce(:ccy, '')
              and coalesce(observation_type, '') = coalesce(:obs, '')
        """), {"grade": row.product_grade_or_chemistry, "geo": row.market_geography,
               "basis": row.commercial_basis, "ccy": row.currency,
               "obs": row.observation_type}).fetchone()

        if twin is None:
            # No polyether twin: this is the only identity for that market, so rename it in place
            # rather than dropping readings on the floor.
            bind.execute(sa.text("""
                update market_identity
                set product_family = 'POLYETHER_POLYOL',
                    comparability_group = replace(comparability_group, 'POLYOL|', 'POLYETHER_POLYOL|')
                where id = :id
            """), {"id": row.id})
            print(f"[0036] {label}: renamed in place, no duplicate to clear")
            continue

        # A reading is the same reading when the same source printed the same price for the same
        # day. Those are the re-import's duplicates and they go.
        dropped = bind.execute(sa.text("""
            delete from price_observation
            where identity_id = :old
              and exists (
                select 1 from price_observation keep
                where keep.identity_id = :keep
                  and keep.source_id = price_observation.source_id
                  and keep.observation_date = price_observation.observation_date
                  and keep.price = price_observation.price
              )
        """), {"old": row.id, "keep": twin.id}).rowcount

        moved = bind.execute(sa.text(
            "update price_observation set identity_id = :keep where identity_id = :old"),
            {"keep": twin.id, "old": row.id}).rowcount

        bind.execute(sa.text("delete from market_reference_point where identity_id = :old"),
                     {"old": row.id})
        bind.execute(sa.text("delete from market_identity where id = :old"), {"old": row.id})
        print(f"[0036] {label}: {dropped} duplicated readings deleted, "
              f"{moved} moved onto the polyether identity, recreated identity removed")


def downgrade() -> None:
    raise NotImplementedError("Deleted duplicate readings are not restored.")
