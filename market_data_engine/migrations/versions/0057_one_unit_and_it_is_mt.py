"""One unit, and it is MT (v1.2.5)

Masterplan A, item 4: "Unit: one token, MT, applied in the store and read from the store wherever
it is printed." Stefan ruled MT. The store wrote `t`, the manual import posted `t`, and every
screen printed MT as a literal that read nothing - so no screen could ever have shown him that
his ruling had not been applied.

The old `units.py` said the store could not be moved because "rewriting t to MT would split every
series in two". Measured before writing this: no two identities differ only by their unit, and
`market_identity` has no unique index over the field set. The 0021 duplication that claim was
afraid of came from rewriting some rows and not others. This rewrites all of them, in one
transaction.

Four things move.

**1. Every identity's unit becomes MT** - 354 holding a tonne spelling, and 23 holding a PRICE:
`18,166.67/t`, `4,280.00/MT`, `12,300-12,600/t`, `18,200/t and above`. Three import connectors
wrote the price-and-unit string into `unit` instead of splitting it. The price itself is stored
correctly beside each one, so nothing is lost - the unit is the tonne token in the string, and
every one of the 23 carries one. Why the extractors did it is section D's, not this migration's.

**2. The comparability group's unit field.** It is the LAST field, and the group has three shapes
in the live store - 307 rows of seven fields, 46 of eight (these carry origin and destination),
21 of six (no packaging). A fixed field index would have moved 307 and left 70 behind, so this
folds the last field when it names a tonne, which is true of all three shapes. That the group has
three shapes at all is a finding for A, recorded there; this migration only moves the unit.

**3. Two identities merge.** Folding the 23 price strings makes two TDI China rows identical on
every one of the ten fields the store matches on: they were `18,500/t` and `18,200/t and above`,
one market, split only by the extractor's mistake. Both readings come from one document - the same
`evidence_id` - on 1 October 2026. Left as two rows they would be the duplication migration 0046
exists to repair, with `ensure_identity` always matching the first and the second holding an
orphan reading. So the readings move onto the surviving row (the lower id, chosen that way to be
deterministic) and the dead row goes. Its derived reference point is deleted rather than moved: a
reference point is computed from readings and the survivor's will be recomputed from both.

**4. `forecast_vintage.identity_key`** - field 7 of 8 - so a governed forecast is still reachable
from the identity that produced it. The two merging identities' keys become one, which is right:
they were never two series. Their vintages (three each, all 1 October 2026, one model) sit under
one key afterwards and are distinct by `vintage_key`, which is the unique one.

Idempotent: a second run finds nothing to fold and no duplicates to merge. The downgrade puts the
token back to `t`; it cannot restore the 23 price strings or un-merge the two rows, and should
not.
"""
from alembic import op
import re
import sqlalchemy as sa

revision = "0057_one_unit_and_it_is_mt"
down_revision = "0056_one_active_selection_per_pair"
branch_labels = None
depends_on = None

MT = "MT"

# The same spellings `raw_material_forecasting.units` folds. Written out here rather than imported
# because a migration has to mean the same thing in five years, whatever the application says then.
_TONNE = r"(?:metric\s*)?(?:tonnes?|tons?|mts?|t)"
_ALONE = re.compile(rf"^\s*/?\s*{_TONNE}\s*$", re.I)
# The unit inside a price string: a "/token" where the token names a tonne.
_TRAILING = re.compile(rf"/\s*{_TONNE}\b", re.I)

# Field 7 of the eight-field series key (`app.domain.series_key.FIELDS`), zero-based.
_SERIES_KEY_FIELDS = 8
_SERIES_KEY_UNIT = 6

# What the store decides two readings are the same series by (`app.persistence.identity`).
MATCH_FIELDS = ("product_family", "product_grade_or_chemistry", "origin", "destination",
                "market_geography", "commercial_basis", "packaging", "currency", "unit",
                "observation_type")

# Everything that points at an identity, from the FK list on `market_identity`.
_CHILDREN = ("price_observation", "derived_value", "forecast_method_override",
             "forecast_method_selection", "governed_forecast_run", "model_fit_result")
# Derived from readings, so recomputed rather than carried over.
_RECOMPUTED = ("market_reference_point",)


def _folded(unit) -> str | None:
    """MT where this names a tonne or carries one, None where it should be left alone."""
    text = str(unit or "").strip()
    if not text or text == MT:
        return None
    return MT if (_ALONE.match(text) or _TRAILING.search(text)) else None


def _group_folded(group) -> str | None:
    """The comparability group with its LAST field set to MT, whatever the group's shape."""
    parts = str(group or "").split("|")
    if len(parts) < 2 or _folded(parts[-1]) is None:
        return None
    parts[-1] = MT
    return "|".join(parts)


def _key_folded(key) -> str | None:
    """The eight-field series key with its unit field set to MT."""
    parts = str(key or "").split("|")
    if len(parts) != _SERIES_KEY_FIELDS or parts[_SERIES_KEY_UNIT] == MT:
        return None
    parts[_SERIES_KEY_UNIT] = MT
    return "|".join(parts)


def _fold_identities(bind) -> int:
    rows = bind.execute(sa.text(
        "SELECT id, unit, comparability_group FROM market_identity")).mappings().all()
    moved = 0
    for row in rows:
        unit, group = _folded(row["unit"]), _group_folded(row["comparability_group"])
        if unit is None and group is None:
            continue
        sets, params = [], {"id": row["id"]}
        if unit is not None:
            sets.append("unit = :unit")
            params["unit"] = unit
        if group is not None:
            sets.append("comparability_group = :grp")
            params["grp"] = group
        bind.execute(sa.text(f"UPDATE market_identity SET {', '.join(sets)} WHERE id = :id"), params)
        moved += 1
    return moved


def _merge_duplicates(bind, tables: set[str]) -> list[tuple[str, str]]:
    """Identities now identical on MATCH_FIELDS: keep the lowest id, move its siblings' rows."""
    columns = ", ".join(MATCH_FIELDS)
    rows = bind.execute(sa.text(f"SELECT id, {columns} FROM market_identity")).mappings().all()
    groups: dict[tuple, list[str]] = {}
    for row in rows:
        groups.setdefault(tuple(row[name] for name in MATCH_FIELDS), []).append(str(row["id"]))
    merged = []
    for ids in groups.values():
        if len(ids) < 2:
            continue
        keep, *dead = sorted(ids)
        for one in dead:
            for table in _CHILDREN:
                if table in tables:
                    bind.execute(
                        sa.text(f"UPDATE {table} SET identity_id = :keep WHERE identity_id = :dead"),
                        {"keep": keep, "dead": one})
            for table in _RECOMPUTED:
                if table in tables:
                    bind.execute(sa.text(f"DELETE FROM {table} WHERE identity_id = :dead"),
                                 {"dead": one})
            bind.execute(sa.text("DELETE FROM market_identity WHERE id = :dead"), {"dead": one})
            merged.append((one, keep))
    return merged


def _rekey_vintages(bind) -> int:
    keys = [r[0] for r in bind.execute(sa.text(
        "SELECT DISTINCT identity_key FROM forecast_vintage")).all()]
    moved = 0
    for key in keys:
        folded = _key_folded(key)
        if folded is None:
            continue
        bind.execute(
            sa.text("UPDATE forecast_vintage SET identity_key = :new WHERE identity_key = :old"),
            {"new": folded, "old": key})
        moved += 1
    return moved


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "market_identity" not in tables:
        return
    moved = _fold_identities(bind)
    merged = _merge_duplicates(bind, tables)
    vintages = _rekey_vintages(bind) if "forecast_vintage" in tables else 0
    print(f"0057: {moved} identities folded to {MT}; "
          f"{len(merged)} merged away {merged or ''}; {vintages} forecast series re-keyed")


def downgrade() -> None:
    """Back to `t`. The 23 price strings and the merge are not restored; neither should be."""
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "market_identity" not in tables:
        return
    bind.execute(sa.text("UPDATE market_identity SET unit = 't' WHERE unit = :mt"), {"mt": MT})
    for table, column, last in (("market_identity", "comparability_group", True),
                                ("forecast_vintage", "identity_key", False)):
        if table not in tables:
            continue
        for key in [r[0] for r in bind.execute(
                sa.text(f"SELECT DISTINCT {column} FROM {table}")).all()]:
            parts = str(key or "").split("|")
            index = len(parts) - 1 if last else _SERIES_KEY_UNIT
            if (not last and len(parts) != _SERIES_KEY_FIELDS) or parts[index] != MT:
                continue
            parts[index] = "t"
            bind.execute(sa.text(f"UPDATE {table} SET {column} = :new WHERE {column} = :old"),
                         {"new": "|".join(parts), "old": key})
