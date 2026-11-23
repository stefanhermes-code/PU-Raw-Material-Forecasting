"""One China polyol series, and the resolver stops splitting it (v0.4.173)

Three identities for one market, found on 17 September 2026:

    POLYOL, no grade                        70 readings   FPB 40, KGI 15, Longzhong 12, ECHEMI 3
    POLYETHER_POLYOL, no grade              55 readings   FPB 40, KGI 15          last Oct 2023
    POLYOL / FLEXIBLE_SLABSTOCK_POLYETHER    3 readings   Longzhong               16-17 Sep 2026

THE DUPLICATES
--------------
Every one of the 55 evidence ids on POLYETHER_POLYOL is already on POLYOL - 55 shared, 0 unique. The
polyol merge of 14 September filed FoamPartner's and KGI's purchases under the chemistry name while
the collectors write the name `resolve_identity` returns, so the history was copied beside the live
series rather than joined to it, and the startup loader put the copy back on every launch. The rows
are dropped, not moved: there is nothing in them the series does not already hold.

`MARKET_LANE_FAMILY` in app/history/transactions.py now files a crossing row under the family the
publishers use, so the copy cannot come back.

THE SPLIT THAT WAS STILL OPEN
-----------------------------
Longzhong began printing its polyol under a product name, the resolver turned that into a grade, and
from 16 September its readings left the series the customer page shows and started a fourth identity
- including the price for 17 September. The page was a day behind and had no way to say so.

Same line, same fault, third time: six polymeric MDI identities in migration 0028, the seventh in
0032, and this. The resolver now returns no grade for polyol, and both polyol connectors stop
sending one. Where the chains have to be told apart - polyether against polyester, HR against graft
- the FAMILY says so and the route graph gives each its own code.
"""
from alembic import op
import sqlalchemy as sa

revision = "0034_one_china_polyol_series"
down_revision = "0033_kgi_mdi_purchases_merge"
branch_labels = None
depends_on = None

KEEP = ("POLYOL", None, "China", "EXW", "CNY", "market_price")


def _identities(bind):
    return bind.execute(sa.text("""
        select id, product_family, product_grade_or_chemistry, origin, market_geography,
               commercial_basis, currency, observation_type
        from market_identity
        where product_family in ('POLYOL', 'POLYETHER_POLYOL')
          and observation_type <> 'seller_quote'
    """)).fetchall()


def upgrade() -> None:
    bind = op.get_bind()
    rows = _identities(bind)
    keeper = next((r for r in rows
                   if r.product_family == "POLYOL" and not r.product_grade_or_chemistry
                   and r.observation_type == "market_price"
                   and r.commercial_basis == "EXW" and r.market_geography == "China"), None)
    if keeper is None:
        print("[0034] no China polyol ex-works series; nothing to do")
        return

    held = {r[0] for r in bind.execute(
        sa.text("select evidence_id from price_observation where identity_id = :i"),
        {"i": keeper.id})}

    merged = dropped = 0
    for row in rows:
        if row.id == keeper.id:
            continue
        # Geography is not a filter here. KGI's Shell Caradol is filed against Thailand on the
        # purchase lane and its readings are on the China series already; a copy is a copy wherever
        # it is filed.
        observations = bind.execute(sa.text(
            "select id, evidence_id from price_observation where identity_id = :i"),
            {"i": row.id}).fetchall()
        label = (f"{row.product_family} / {row.product_grade_or_chemistry or 'no grade'} "
                 f"[{row.observation_type}]")
        duplicates = [o for o in observations if o.evidence_id in held]
        fresh = [o for o in observations if o.evidence_id not in held]
        print(f"[0034] {label}: {len(observations)} readings "
              f"({len(fresh)} new, {len(duplicates)} already on the series)")
        for observation in duplicates:
            bind.execute(sa.text("delete from price_observation where id = :i"),
                         {"i": observation.id})
            dropped += 1
        for observation in fresh:
            bind.execute(sa.text(
                "update price_observation set identity_id = :keep where id = :i"),
                {"keep": keeper.id, "i": observation.id})
            held.add(observation.evidence_id)
            merged += 1

    print(f"[0034] readings merged onto the China polyol ex-works series: {merged}")
    print(f"[0034] duplicated readings removed: {dropped}")

    emptied = bind.execute(sa.text("""
        delete from market_identity
        where product_family in ('POLYOL', 'POLYETHER_POLYOL')
          and observation_type <> 'seller_quote'
          and id <> :keep
          and id not in (select distinct identity_id from price_observation)
    """), {"keep": keeper.id}).rowcount
    print(f"[0034] emptied identities removed: {emptied}")

    cleared = bind.execute(sa.text(
        "delete from market_reference_point where identity_id = :i"), {"i": keeper.id}).rowcount
    print(f"[0034] consolidated points cleared for rebuild: {cleared}")

    total, sources, first, last = bind.execute(sa.text("""
        select count(*), count(distinct source_id), min(observation_date), max(observation_date)
        from price_observation where identity_id = :i
    """), {"i": keeper.id}).fetchone()
    print(f"[0034] the China polyol ex-works series holds {total} readings from {sources} sources, "
          f"{str(first)[:10]} to {str(last)[:10]}")


def downgrade() -> None:
    raise NotImplementedError("0034 merges evidence; there is no unambiguous way back")
