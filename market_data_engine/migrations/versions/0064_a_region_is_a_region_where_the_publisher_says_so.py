"""A region is a region where the publisher assesses one (v1.2.15)

Masterplan F. `app/domain/origin.national` folds a sub-national label to its country, so a reading
of "East China" is stored as China. The fold was written for SunSirs, which prints its China TDI
ex-works assessment as "China" on most days and "East China" on a few: one market under two names,
which consolidates against itself.

**ICIS is not that case.** It assesses East, North and South China separately, with different values
and different date ranges, and folding them averages three assessments into one series - the thing
`app/reference/series.py` exists to prevent. Measured 28 October 2026 over every sub-national label
the store holds:

    icis_history          3 regions   East, North and South China      787 readings
    longzhong_polyester   1 region    East China                         3 readings

So the test is the publisher's own behaviour, and it is measurable: **a publisher that prices more
than one region of a country means the regions.** `ASSESSES_REGIONS_SEPARATELY` names the one that
does, and `national` now takes the source id so it can ask.

The application's own pages had already ruled the same way from the other side -
`market_selection_ui` keeps East, North and South China out of `ORIGINS` and draws them as lines
inside China - so the page expected the store to keep the region while the store was folding it
away. The eight ICIS identities survived only because they predate the fold; nothing new could ever
have joined them.

**What this migration moves.** The three `longzhong_polyester` identities on East China, one reading
each, which the fold should have folded and did not. Each has an exact twin on China - same grade,
same basis, same unit, same kind, same origin - holding six readings, so each reading joins the
series it belongs to and the empty identity goes.

    ALIPHATIC_POLYESTER_POLYOL  East China  POLYESTER_AA_BG     -> China, joining 6 readings
    ALIPHATIC_POLYESTER_POLYOL  East China  POLYESTER_AA_EG     -> China, joining 6 readings
    ALIPHATIC_POLYESTER_POLYOL  East China  POLYESTER_AA_BG_EG  -> China, joining 6 readings

The eight ICIS identities are left exactly as they are: they are three real regional assessments and
the store is now able to keep them.

Idempotent: a second run finds no unfolded region left from a publisher that assesses only one.
"""
from alembic import op
import sqlalchemy as sa

revision = "0064_a_region_is_a_region_where_the_publisher_says_so"
down_revision = "0063_mdi_means_pmdi"
branch_labels = None
depends_on = None

# Written out rather than imported, as every migration in this chain writes its vocabulary out.
NATIONAL_FOR = {
    "east china": "China", "north china": "China", "south china": "China",
    "central china": "China", "northwest china": "China", "northeast china": "China",
    "southwest china": "China",
}
ASSESSES_REGIONS_SEPARATELY = ("icis_history",)

MATCH_FIELDS = ("product_family", "product_grade_or_chemistry", "origin", "destination",
                "market_geography", "commercial_basis", "packaging", "unit", "observation_type")
_CHILDREN = ("price_observation", "derived_value", "forecast_method_override",
             "forecast_method_selection", "governed_forecast_run", "model_fit_result")
_RECOMPUTED = ("market_reference_point",)


def _publishers_of(bind, identity_id: str) -> set[str]:
    return {str(row[0]) for row in bind.execute(sa.text(
        "SELECT DISTINCT source_id FROM price_observation WHERE identity_id = :id"),
        {"id": identity_id}).all() if row[0]}


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "market_identity" not in tables:
        return
    columns = ", ".join(("id", *MATCH_FIELDS))
    rows = bind.execute(sa.text(f"SELECT {columns} FROM market_identity")).mappings().all()
    folded, merged, kept = 0, 0, []
    for row in rows:
        country = NATIONAL_FOR.get(str(row["market_geography"] or "").strip().lower())
        if not country:
            continue
        publishers = _publishers_of(bind, row["id"])
        if publishers & set(ASSESSES_REGIONS_SEPARATELY):
            kept.append(f'{row["market_geography"]} ({", ".join(sorted(publishers))})')
            continue
        where = " AND ".join(
            f"COALESCE({name},'') = COALESCE(:{name},'')"
            for name in MATCH_FIELDS if name != "market_geography")
        same = bind.execute(sa.text(
            f"SELECT id FROM market_identity WHERE market_geography = :country AND {where}"),
            {"country": country,
             **{name: row[name] for name in MATCH_FIELDS if name != "market_geography"}}).scalar()
        if same is None:
            bind.execute(sa.text(
                "UPDATE market_identity SET market_geography = :country WHERE id = :id"),
                {"country": country, "id": row["id"]})
            folded += 1
            continue
        for table in _CHILDREN:
            if table in tables:
                bind.execute(sa.text(
                    f"UPDATE {table} SET identity_id = :keep WHERE identity_id = :dead"),
                    {"keep": same, "dead": row["id"]})
        for table in _RECOMPUTED:
            if table in tables:
                bind.execute(sa.text(f"DELETE FROM {table} WHERE identity_id = :dead"),
                             {"dead": row["id"]})
        bind.execute(sa.text("DELETE FROM market_identity WHERE id = :dead"), {"dead": row["id"]})
        merged += 1
    print(f"0064: {folded} regional identities relabelled to their country, {merged} merged into "
          f"the country identity that already held the market; {len(kept)} left as regions because "
          f"the publisher assesses them separately {sorted(set(kept)) or ''}")


def downgrade() -> None:
    """Not reversible: which region each merged reading was filed under is not recoverable once it
    sits in the country's series."""
    return
