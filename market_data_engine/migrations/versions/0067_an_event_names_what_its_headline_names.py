"""Attribute the stored events whose headline already says which chemical they are about.

Longzhong's PU channel tags every headline with its subject - `[聚醚]`, `[液氯]`, `[MDI]`, `[THF]` -
and `mysteel_pu._material_of` reads that tag. Measured 29 October 2026: of the 105 events collected
from it, 15 carry no material. **The parser is not what is wrong with them.** Asked today it
attributes 8 of the 15 correctly, and it agrees with all 40 of the attributed rows checked. Those 8
are simply older than the labels that now resolve them:

    2025-11-08  POLYOL           [行业快讯]：联泓格润24万吨/年聚醚多元醇(PPG)装置一次开车成功
    2025-11-20  POLYOL           [行业快讯]：隆华新材33万吨/年聚醚多元醇扩建项目一次开车成功
    2025-12-25  PO               [行业快讯]：联泓格润一体化项目30万吨/年PO装置一次开车成功
    2026-05-15  POLYOL           [行业快讯]：广西氯碱HPPO项目聚醚装置一次开车成功
    2026-08-26  POLYOL           [聚醚]：8月26日东南亚地区聚醚多元醇价格快讯
    2026-09-02  POLYOL           [聚醚]：9月2日中国出口聚醚多元醇价格快讯
    2026-09-02  POLYOL           [聚醚]：9月2日东南亚地区聚醚多元醇价格快讯
    2026-09-09  LIQUID_CHLORINE  [液氯]：山东信发液氯价格快讯

Two more resolve because this release taught the parser two tags it never had: bare `MDI`, which is
pMDI by Stefan's ruling of 29 October (every MDI label in the table was a specific grade, so a
roundup headed "国内MDI装置动态汇总" named nothing), and THF under both its spellings.

What stays blank, correctly: four more THF plant events, which name a chemical the store does not
price and the route graph does not hold - they will now say THF on the Market Intelligence page and
still reach no forecast - and one headline about the Hua Jin Aramco refinery having 32 units
mechanically complete, which names no chemical at all.

This asks `_material_of` for the answer rather than restating the eight. The parser is the one
definition of what a Longzhong headline is about, and a list copied into a migration is a second one
that would be wrong the moment the first changed.

Nothing is invented: every material written here is read out of the headline already stored on the
row. `material` is the only column touched.
"""
from alembic import op
import sqlalchemy as sa

revision = "0067_an_event_names_what_its_headline_names"
down_revision = "0066_a_sale_inside_china_carries_the_tax"
branch_labels = None
depends_on = None

# The connectors whose headlines this parser reads. Longzhong and Mysteel are the same channel.
TAGGED_HEADLINES = ("longzhong_tdi", "mysteel_pu", "mysteel_polyester")


def upgrade() -> None:
    from app.sources.connectors.mysteel_pu import _material_of

    bind = op.get_bind()
    rows = bind.execute(sa.text("""
        select id, source_id, coalesce(data->>'headline','') as headline
        from event_record
        where coalesce(material,'') = '' and source_id = any(:sources)
    """), {"sources": list(TAGGED_HEADLINES)}).fetchall()

    attributed = 0
    left = []
    for row_id, source_id, headline in rows:
        material = _material_of(headline or "")
        if not material:
            left.append(headline)
            continue
        bind.execute(sa.text("update event_record set material = :m where id = :i"),
                     {"m": material, "i": row_id})
        attributed += 1
        print(f"  {source_id}: {material} <- {(headline or '')[:56]}")

    print(f"  {attributed} of {len(rows)} attributed from their own headline")
    for headline in left:
        print(f"  still blank, and correctly: {(headline or '')[:60]}")

    blank = bind.execute(sa.text("""
        select count(*) from event_record
        where coalesce(material,'') = '' and source_id = any(:sources)
    """), {"sources": list(TAGGED_HEADLINES)}).scalar()
    print(f"  {blank} left with no material on these sources")


def downgrade() -> None:
    """Not reversible, and the record loses nothing by that.

    The headline each material was read from is still on the row, so what this did can be checked
    at any time by asking `_material_of` the same question.
    """
