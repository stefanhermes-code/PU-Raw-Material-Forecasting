"""A sale inside China carries the 13%, whatever the delivery term is written as.

`tax.py` has always said the VAT is "the convention of that market, not a property of any one
publisher", and then tested for an ex-works basis. The test was a proxy for "a sale between two
Chinese parties" and a poor one: it missed every domestic yuan price not written EXW.

Measured 29 October 2026, 104 readings were kept gross that should not have been:

    35  PO, DAP, jlc_pu_channel          - beside three EXW lines from the SAME publisher,
                                           all three of which were being netted
    30  polyether polyol, on a basis spelled CHINA_DOMESTIC_SELLER_QUOTE
    39  six `import_*` connectors, which carry no delivery term at all

The clearest of them: on 16 October SunSirs printed TDI China at 17,900 yuan. The store held that
one number netted to 15,841 under `sunsirs_tdi`, and kept at 17,900 under `import_sunsirs` and
`import_htc_global` - because those two rows have no basis, not because anybody decided they were
different prices.

So the test is now whether the delivery term names a border. An import is taxed at customs
separately and an export is zero-rated, so neither carries the VAT in its printed figure; a sale
inside China does. ICIS is exempt by Stefan's ruling of 29 October 2026 and stays exempt whatever
its term, which is why that ruling had to be written down before this test could be widened -
widening it first would have taken 13% off 573 readings that never had it.

This rebuilds the consolidated series, which is where the net value is stored. The raw prints are
untouched, as always: 17,900 is what SunSirs said.
"""
from alembic import op
import sqlalchemy as sa

revision = "0066_a_sale_inside_china_carries_the_tax"
down_revision = "0065_one_guard_on_one_door"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from sqlalchemy.orm import sessionmaker

    from app.reference.series import rebuild_all_market_series

    bind = op.get_bind()
    affected = bind.execute(sa.text("""
        select count(distinct i.id) from market_identity i
        join price_observation o on o.identity_id = i.id
        where upper(coalesce(i.currency,'')) in ('CNY','RMB')
          and upper(coalesce(i.commercial_basis,'')) not in
              ('EXW','EX-WORKS','EXWORKS','CFR','CIF','CIP','CPT','C&F','FAS','FCA','FOB','DDU')
    """)).scalar()
    print(f"  {affected} yuan markets were netted on a term the old test did not recognise")

    # The engine's own function, so the stored value is produced by the rule in force rather than
    # by arithmetic written here that could disagree with it.
    moved = rebuild_all_market_series(sessionmaker(bind=bind))
    revised = sum(row.get("revised", 0) for row in moved)
    retired = sum(row.get("retired", 0) for row in moved)
    print(f"  rebuilt {len(moved)} markets: {revised} values revised, {retired} dates retired")

    left = bind.execute(sa.text("""
        select count(*) from market_reference_point p
        join market_identity i on i.id = p.identity_id
        where lower(i.observation_type) <> 'assessed'
    """)).scalar()
    assert left == 0, f"{left} points on markets that may not be consolidated"


def downgrade() -> None:
    """Not reversible, and does not need to be: consolidated points are derived.

    `rebuild_all_market_series` reproduces whatever the rule in force supports.
    """
