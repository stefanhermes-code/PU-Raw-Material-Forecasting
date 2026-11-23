"""A consolidated series belongs to an assessed market, whichever door built it.

`rebuild_reference_series` is called from two places. `rebuild_all_market_series` selects the
assessed markets and calls it for each; `persistence.service._update_reference` calls it directly
for whatever identity just collected, with no filter at all. The rule - only an assessed market is
consolidated, because averaging two sellers' quotes or two buyers' orders produces a price nobody
asked and nobody paid - lived in the first caller only.

Measured against the live store on 29 October 2026:

    kind     identities   consolidated points   last written
    deal            176                18,907     2026-10-22
    ask               4                   149     2026-10-28

Nothing is wrong in those 19,056 numbers today, and the reason is luck rather than design: no deal
or ask market has ever had two sources printing on one date, so every consolidated value equals the
single reading it was computed from. The first market that did would have been averaged silently.

The guard now sits inside `rebuild_reference_series`, so both doors carry it, and a market that may
not be consolidated has its points retired rather than left standing. This migration retires the
ones already written. The forecast falls back to the raw readings for such a market, net of tax
(`store_series.forecast_rows`), which for one source on a date is that reading - so no forecast
input changes.

Nothing is deleted that the evidence does not still hold: these rows are derived, every observation
behind them is untouched, and the rebuild reproduces whatever the evidence supports.
"""
from alembic import op
import sqlalchemy as sa

revision = "0065_one_guard_on_one_door"
down_revision = "0064_a_region_is_a_region_where_the_publisher_says_so"
branch_labels = None
depends_on = None

MAY_BE_CONSOLIDATED = "assessed"


def upgrade() -> None:
    bind = op.get_bind()
    before = bind.execute(sa.text("""
        select lower(i.observation_type), count(p.id)
        from market_reference_point p join market_identity i on i.id = p.identity_id
        where lower(i.observation_type) <> :kind group by 1 order by 2 desc
    """), {"kind": MAY_BE_CONSOLIDATED}).fetchall()
    for kind, points in before:
        print(f"  retiring {points} consolidated points from {kind} markets")

    bind.execute(sa.text("""
        delete from market_reference_point p
        using market_identity i
        where i.id = p.identity_id and lower(i.observation_type) <> :kind
    """), {"kind": MAY_BE_CONSOLIDATED})

    left = bind.execute(sa.text("""
        select count(*) from market_reference_point p join market_identity i on i.id = p.identity_id
        where lower(i.observation_type) <> :kind
    """), {"kind": MAY_BE_CONSOLIDATED}).scalar()
    assert left == 0, f"{left} points still on markets that may not be consolidated"
    print(f"  {bind.execute(sa.text('select count(*) from market_reference_point')).scalar()} "
          f"consolidated points remain, all on assessed markets")


def downgrade() -> None:
    """Not reversible, and does not need to be: these rows are derived.

    Running `rebuild_all_market_series` rebuilds every point the evidence supports. What this
    removed is what the evidence does not support under the rule in force.
    """
