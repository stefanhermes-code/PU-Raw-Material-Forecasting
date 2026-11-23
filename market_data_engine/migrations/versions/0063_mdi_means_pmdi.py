"""MDI means pMDI, by ruling (v1.2.12)

**Stefan, 28 October 2026:** *"for now I take the decision to keep them as pMDI. We can always come
back to this later and make necessary changes."*

The question was D's gate's. It refused `MDI` because the store prices `PMDI` (polymeric) and
`PURE_MDI` (monomeric) separately and the bare word does not say which, and refusing it stopped five
of HTC's own purchase orders loading - which is how the gate came to be asked about them at all.

What the two products actually are was looked up rather than guessed. BASF's own data sheet calls
Lupranat MX 118/1 a solvent-free product based on **4,4'-MDI** - monomeric - at about 32.5% NCO and
about 25 mPa.s at 25 C, far thinner than polymeric MDI (30-31% NCO, 150-250 mPa.s), and the
isocyanate side of a viscoelastic slabstock system. Wanhua's WANNATE 8000 series are modified MDI
prepolymers; no sheet for 8223 was found. So neither is cleanly either family, and the honest answer
is a third one. **This is therefore a decision to revisit, not a fact**, and it is written that way
in `chemicals.LEGACY_CODES` so whoever comes back to it has the evidence rather than the conclusion.

Four identities carried `MDI`, nine readings between them:

    PMDI-bound by this migration        4 identities, 9 readings
      MDI Thailand DDP (reference)      1 identity,  0 readings
      MDI from import_echemi,
          import_htc_global,
          import_sunsirs                3 identities, 9 readings

The fold itself is in `chemicals.canonical_code`, beside POLYOL, so every reader and writer asks one
place - migration 0035 is the precedent. This migration moves the stored rows so the store agrees
with the fold; without it, `ensure_identity` would write PMDI while four rows still said MDI, and the
readings on them would be unreachable from the market they belong to.

Where a PMDI identity already matches on all nine match fields, the MDI row's children move to it and
the row goes; otherwise the row is relabelled in place. The series key's first field is the family, so
`forecast_vintage.identity_key` is re-keyed.

Idempotent: a second run finds no `MDI` left.
"""
from alembic import op
import sqlalchemy as sa

revision = "0063_mdi_means_pmdi"
down_revision = "0062_nothing_mints_a_market_unseen"
branch_labels = None
depends_on = None

# Written out rather than imported, as every migration in this chain writes its vocabulary out: the
# ruling has to be legible here in five years whatever `LEGACY_CODES` says then.
_WAS, _IS = "MDI", "PMDI"

MATCH_FIELDS = ("product_family", "product_grade_or_chemistry", "origin", "destination",
                "market_geography", "commercial_basis", "packaging", "unit", "observation_type")
_CHILDREN = ("price_observation", "derived_value", "forecast_method_override",
             "forecast_method_selection", "governed_forecast_run", "model_fit_result")
_RECOMPUTED = ("market_reference_point",)
_SERIES_KEY_FIELDS = 7


def _key_with(family: str, parts: list[str]) -> str:
    return "|".join([family, *parts[1:]])


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "market_identity" not in tables:
        return
    columns = ", ".join(("id", *MATCH_FIELDS, "comparability_group"))
    rows = bind.execute(sa.text(
        f"SELECT {columns} FROM market_identity WHERE product_family = :was"),
        {"was": _WAS}).mappings().all()
    moved, merged, rekeyed = 0, 0, 0
    for row in rows:
        where = " AND ".join(
            f"COALESCE({name},'') = COALESCE(:{name},'')" for name in MATCH_FIELDS[1:])
        same = bind.execute(sa.text(
            f"SELECT id FROM market_identity WHERE product_family = :now AND {where}"),
            {"now": _IS, **{name: row[name] for name in MATCH_FIELDS[1:]}}).scalar()
        if same is None:
            group = str(row["comparability_group"] or "").split("|")
            if group and group[0]:
                group[0] = _IS
            bind.execute(sa.text(
                "UPDATE market_identity SET product_family = :now, comparability_group = :group "
                "WHERE id = :id"),
                {"now": _IS, "group": "|".join(group), "id": row["id"]})
            moved += 1
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
    if "forecast_vintage" in tables:
        for (key,) in bind.execute(
                sa.text("SELECT DISTINCT identity_key FROM forecast_vintage")).all():
            parts = str(key or "").split("|")
            if len(parts) != _SERIES_KEY_FIELDS or parts[0] != _WAS:
                continue
            bind.execute(sa.text(
                "UPDATE forecast_vintage SET identity_key = :new WHERE identity_key = :old"),
                {"new": _key_with(_IS, parts), "old": key})
            rekeyed += 1
    print(f"0063: {moved} identities relabelled {_WAS} -> {_IS}, {merged} merged into a {_IS} row "
          f"that already existed, {rekeyed} forecast series re-keyed")


def downgrade() -> None:
    """Not reversible, and said plainly. Which of the four rows was `MDI` is not recoverable once
    they sit beside the store's other pMDI rows, and the merges cannot be undone."""
    return
