"""Currency is a property of the reading, not of the series (v1.2.7)

Masterplan A, item 5: *"leave the conversion as it is built (see E) and take currency out of the
identity."*

A TDI China price quoted in yuan and one quoted in dollars are the same series. The conversion is
already built and already per reading - `price_in_currency` holds 106,052 rows, every reading in
EUR, USD and CNY, keyed on `observation_id` - so nothing on a screen ever needed the *series* to
say which currency it was in. What the currency describes is the reading: what the publisher
printed.

Measured before anything moved: **no two identities in the store differ only by currency** (0
groups of the 376), and no two series keys do either, so taking currency out of the key merges
nothing and splits nothing. Section E had already found the same thing from the other side - no
market in the store is split by currency alone.

Three things move.

**1. `price_observation.currency`** - the currency the reading is priced in, backfilled from the
identity that holds it. This is the truth from here on. `native_currency` beside it keeps its own
meaning, which is narrower: the currency a reading arrived in when the store converted it on
import, set on 64 of the 35,462 rows.

**2. The series key loses its sixth field**, from eight to seven, and so does the comparability
group; `MATCH_FIELDS` goes from ten to nine. `forecast_vintage.identity_key` is re-keyed
accordingly, because a governed forecast has to stay reachable from the identity that produced it.

**3. `market_identity.currency` stops identifying anything.** The column stays and is set from the
readings it holds - the currency of record for the series, which the forecast labels and the VAT
rule read - and the migration recomputes it so it cannot start out disagreeing. Where a series ever
comes to hold two currencies it is set empty, which is the honest answer: ask the reading.
`tests/test_currency_is_a_property_of_the_reading.py` pins that agreement.

Idempotent: a second run finds the column present, the keys already seven fields, and nothing to
recompute.
"""
from alembic import op
import sqlalchemy as sa

revision = "0059_currency_is_a_property_of_the_reading"
down_revision = "0058_a_price_kind_is_a_line_not_a_market"
branch_labels = None
depends_on = None

# The key as it was BEFORE this migration: eight fields, currency sixth (zero-based 5). Written out
# because a migration has to mean the same thing in five years, whatever `FIELDS` says then.
_WAS_FIELDS = 8
_WAS_CURRENCY = 5

MATCH_FIELDS = ("product_family", "product_grade_or_chemistry", "origin", "destination",
                "market_geography", "commercial_basis", "packaging", "unit", "observation_type")
_CHILDREN = ("price_observation", "derived_value", "forecast_method_override",
             "forecast_method_selection", "governed_forecast_run", "model_fit_result")
_RECOMPUTED = ("market_reference_point",)


def _add_reading_currency(bind, tables: set[str]) -> int:
    if "price_observation" not in tables:
        return 0
    have = {c["name"] for c in sa.inspect(bind).get_columns("price_observation")}
    if "currency" not in have:
        with op.batch_alter_table("price_observation") as batch:
            batch.add_column(sa.Column("currency", sa.String, nullable=False, server_default=""))
    result = bind.execute(sa.text(
        "UPDATE price_observation SET currency = COALESCE(("
        "  SELECT i.currency FROM market_identity i WHERE i.id = price_observation.identity_id"
        "), '') WHERE COALESCE(price_observation.currency, '') = ''"))
    return result.rowcount or 0


def _shorten(key, fields: int, drop: int) -> str | None:
    """The key with its `drop`th field removed, or None if it is not `fields` fields long."""
    parts = str(key or "").split("|")
    if len(parts) != fields:
        return None
    del parts[drop]
    return "|".join(parts)


def _shorten_groups(bind) -> int:
    """The comparability group's currency is the field before the unit, and the unit is last."""
    moved = 0
    rows = bind.execute(sa.text(
        "SELECT id, comparability_group FROM market_identity")).mappings().all()
    for row in rows:
        parts = str(row["comparability_group"] or "").split("|")
        if len(parts) < 3:
            continue
        del parts[-2]
        bind.execute(sa.text("UPDATE market_identity SET comparability_group = :g WHERE id = :id"),
                     {"g": "|".join(parts), "id": row["id"]})
        moved += 1
    return moved


def _rekey_vintages(bind) -> tuple[int, int]:
    moved, left = 0, 0
    for (key,) in bind.execute(sa.text("SELECT DISTINCT identity_key FROM forecast_vintage")).all():
        shorter = _shorten(key, _WAS_FIELDS, _WAS_CURRENCY)
        if shorter is None:
            left += 1
            continue
        bind.execute(
            sa.text("UPDATE forecast_vintage SET identity_key = :new WHERE identity_key = :old"),
            {"new": shorter, "old": key})
        moved += 1
    return moved, left


def _merge_duplicates(bind, tables: set[str]) -> list[tuple[str, str]]:
    """Identities now identical on the nine remaining match fields. Measured as none."""
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
                    bind.execute(sa.text(
                        f"UPDATE {table} SET identity_id = :keep WHERE identity_id = :dead"),
                        {"keep": keep, "dead": one})
            for table in _RECOMPUTED:
                if table in tables:
                    bind.execute(sa.text(f"DELETE FROM {table} WHERE identity_id = :dead"),
                                 {"dead": one})
            bind.execute(sa.text("DELETE FROM market_identity WHERE id = :dead"), {"dead": one})
            merged.append((one, keep))
    return merged


def _restate_identity_currency(bind) -> tuple[int, int]:
    """Set each identity's currency from the readings it holds; empty where they disagree."""
    one, mixed = 0, 0
    rows = bind.execute(sa.text(
        "SELECT identity_id, COUNT(DISTINCT currency) AS kinds, MIN(currency) AS only "
        "FROM price_observation WHERE COALESCE(currency, '') <> '' "
        "GROUP BY identity_id")).mappings().all()
    for row in rows:
        value = str(row["only"]) if row["kinds"] == 1 else ""
        if row["kinds"] != 1:
            mixed += 1
        else:
            one += 1
        bind.execute(sa.text("UPDATE market_identity SET currency = :c WHERE id = :id"),
                     {"c": value, "id": row["identity_id"]})
    return one, mixed


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "market_identity" not in tables:
        return
    readings = _add_reading_currency(bind, tables)
    groups = _shorten_groups(bind)
    vintages, left = (_rekey_vintages(bind) if "forecast_vintage" in tables else (0, 0))
    merged = _merge_duplicates(bind, tables)
    one, mixed = _restate_identity_currency(bind)
    print(f"0059: {readings} readings given their currency; {groups} comparability groups "
          f"shortened; {vintages} forecast series re-keyed, {left} left; {len(merged)} merged away "
          f"{merged or ''}; {one} identities in one currency, {mixed} holding more than one")


def downgrade() -> None:
    """Put the currency back into the keys from the readings, and drop the reading's column."""
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "market_identity" not in tables:
        return
    for row in bind.execute(sa.text(
            "SELECT id, comparability_group, currency FROM market_identity")).mappings().all():
        parts = str(row["comparability_group"] or "").split("|")
        if len(parts) < 2:
            continue
        parts.insert(len(parts) - 1, str(row["currency"] or "UNKNOWN"))
        bind.execute(sa.text("UPDATE market_identity SET comparability_group = :g WHERE id = :id"),
                     {"g": "|".join(parts), "id": row["id"]})
    if "forecast_vintage" in tables:
        for (key,) in bind.execute(
                sa.text("SELECT DISTINCT identity_key FROM forecast_vintage")).all():
            parts = str(key or "").split("|")
            if len(parts) != _WAS_FIELDS - 1:
                continue
            parts.insert(_WAS_CURRENCY, "UNKNOWN")
            bind.execute(sa.text(
                "UPDATE forecast_vintage SET identity_key = :new WHERE identity_key = :old"),
                {"new": "|".join(parts), "old": key})
    if "price_observation" in tables:
        have = {c["name"] for c in sa.inspect(bind).get_columns("price_observation")}
        if "currency" in have:
            with op.batch_alter_table("price_observation") as batch:
                batch.drop_column("currency")
