"""A grade is a grade, and a product is an example of one (v1.2.8)

Masterplan A, items 1, 2 and 3. Stefan, 27 October 2026: *"the product name is not the chemical
name... more like an example of the chemical."*

`product_grade_or_chemistry` held **64 values** and was three levels at once plus commentary - a
family restated, a published grade tier, a supplier's product name, and notes the extractor made
about the reading. What each one is was not a judgement call: **the publisher who wrote it says
which level it is**, and that is what `raw_material_forecasting.chemical_vocabulary` is built on.

    fnag_switzerland        HTC's own purchase lane  -> supplier PRODUCTS, 20 of them
    icis_history            a publisher's tiers      -> published GRADES
    longzhong_polyester     which glycol the polyester is built on -> chemistry GRADES
    foampartner_china       a fogging limit, an isomer ratio -> GRADES
    100ppi / chemicalbook   one product, MN-3050D
    import_* (by hand)      "grade unspecified", "MW/f n/s", "source notation" -> COMMENTARY

Resolved through the vocabulary, the 64 values are: **45 identities a grade** (9,247 readings),
**23 a supplier product** (577), **34 commentary** (206) and **2 the family restated** (76). Nothing
is unrecognised - checked before this was written, and a value the vocabulary does not know would
go to quarantine rather than become a grade, because a grade minted from whatever a publisher typed
is a market minted from a typo.

Three things move.

**1. The grade field holds a grade code, or nothing.** Twelve codes, each a family plus the
fingerprint the trade distinguishes it by - the isomer ratio for TDI, the solids content for graft
polyol, OH value and functionality for the polyether tiers, the glycol feed for the aliphatic
polyesters. A product resolves UPWARD to the grade it is an example of where the register names one
(`M-200` is crude polymeric MDI, `PO 56` and `CP 1050` are the slabstock triol); commentary and the
family restated become nothing.

**2. The product moves to the reading.** `price_observation.supplier_product`, from the identity
its readings hang off. This is the same rule items 5 and 6 followed: what describes the reading goes
on the reading. Without it, collapsing the grade would *lose* which product HTC bought - six of its
purchase products sit in one market (polyether polyol Europe DAP) and would have become six
anonymous readings. Now they are six named readings of one series.

**3. Nineteen groups of identities merge, absorbing 33** - 376 rows become 343. This is the point
rather than a side effect: the plan calls them the fake splits. The largest is polyether polyol
Europe DAP, where `OHZ 48`, `CP 1421`, `CP 4610`, `Aktiv 35`, `Soft 38` and `C 5710` were six
markets and are one, each reading still naming its product. The rest are pairs and triples of
`grade unspecified` against `grade unspecified (80/20 unconfirmed)`, or one publisher's `AA/BG`
against its own `AA/BG (source notation)` - the same thing written twice.

The series key's second field is the grade, so `forecast_vintage.identity_key` is re-keyed, and so
is the comparability group.

Idempotent: a second run finds the grades already codes, the products already on the readings, and
nothing left to merge.
"""
from alembic import op
import sqlalchemy as sa

revision = "0060_a_grade_is_a_grade_and_a_product_is_an_example"
down_revision = "0059_currency_is_a_property_of_the_reading"
branch_labels = None
depends_on = None

# The seven-field series key after migration 0059; the grade is its second field.
_SERIES_KEY_FIELDS = 7
_SERIES_KEY_GRADE = 1

MATCH_FIELDS = ("product_family", "product_grade_or_chemistry", "origin", "destination",
                "market_geography", "commercial_basis", "packaging", "unit", "observation_type")
_CHILDREN = ("price_observation", "derived_value", "forecast_method_override",
             "forecast_method_selection", "governed_forecast_run", "model_fit_result")
_RECOMPUTED = ("market_reference_point",)


def _vocabulary():
    """The application's vocabulary, imported rather than copied.

    The other migrations in this chain write their vocabulary out, because a token like `MT` or
    `assessed` has to mean the same thing in five years whatever the application says then. This one
    cannot: the mapping is 64 spellings onto twelve grades and twenty-one products, read off the
    publishers who wrote them, and a copy of that would be the second definition the whole of
    section A is about. So it imports, and `tests/test_a_grade_is_a_grade.py` pins the resolution of
    every one of the 64 values this migration was measured against.
    """
    from raw_material_forecasting import chemical_vocabulary
    return chemical_vocabulary


def _add_reading_product(bind, tables: set[str]) -> int:
    if "price_observation" not in tables:
        return 0
    have = {c["name"] for c in sa.inspect(bind).get_columns("price_observation")}
    if "supplier_product" not in have:
        with op.batch_alter_table("price_observation") as batch:
            batch.add_column(sa.Column("supplier_product", sa.String, nullable=False,
                                       server_default=""))
    vocabulary = _vocabulary()
    filled = 0
    rows = bind.execute(sa.text(
        "SELECT id, product_family, product_grade_or_chemistry FROM market_identity "
        "WHERE product_grade_or_chemistry IS NOT NULL")).mappings().all()
    for row in rows:
        level, what = vocabulary.resolve(row["product_grade_or_chemistry"], row["product_family"])
        if level != vocabulary.PRODUCT:
            continue
        result = bind.execute(sa.text(
            "UPDATE price_observation SET supplier_product = :name "
            "WHERE identity_id = :id AND COALESCE(supplier_product, '') = ''"),
            {"name": what.product_name, "id": row["id"]})
        filled += result.rowcount or 0
    return filled


def _resolve_grades(bind) -> tuple[int, dict[str, str]]:
    """The grade field becomes a code or NULL. Returns the count and the old->new key map."""
    vocabulary = _vocabulary()
    columns = ("id", "product_grade_or_chemistry", "comparability_group", "product_family",
               "market_geography", "commercial_basis", "packaging", "unit", "observation_type")
    rows = bind.execute(sa.text(
        f"SELECT {', '.join(columns)} FROM market_identity")).mappings().all()
    moved, rekey = 0, {}
    for row in rows:
        was = row["product_grade_or_chemistry"]
        code = vocabulary.grade_code_for_the_store(was, row["product_family"]) or None
        if code == was:
            continue
        rekey[_series_key(row, was)] = _series_key(row, code)
        group = str(row["comparability_group"] or "").split("|")
        if len(group) >= 2:
            group[1] = code or "UNKNOWN"
        bind.execute(sa.text(
            "UPDATE market_identity SET product_grade_or_chemistry = :g, "
            "comparability_group = :c WHERE id = :id"),
            {"g": code, "c": "|".join(group), "id": row["id"]})
        moved += 1
    return moved, rekey


def _series_key(row, grade) -> str:
    """The seven-field key `app.domain.series_key` builds, with `grade` as its second field."""
    def field(name):
        value = row[name]
        return str(value) if value not in (None, "") else "UNKNOWN"
    return "|".join([field("product_family"), str(grade) if grade else "UNKNOWN",
                     field("market_geography"), field("commercial_basis"), field("packaging"),
                     field("unit"), field("observation_type")])


def _rekey_vintages(bind, rekey: dict[str, str]) -> tuple[int, int]:
    moved, left = 0, 0
    for (key,) in bind.execute(sa.text("SELECT DISTINCT identity_key FROM forecast_vintage")).all():
        now = rekey.get(str(key))
        if now is None or now == key:
            left += 1
            continue
        bind.execute(
            sa.text("UPDATE forecast_vintage SET identity_key = :new WHERE identity_key = :old"),
            {"new": now, "old": key})
        moved += 1
    return moved, left


def _merge_duplicates(bind, tables: set[str]) -> list[tuple[str, str]]:
    """The fake splits, dissolving. The survivor is the one with the most readings, then the
    lowest id - the longest history keeps its identity and its forecasts."""
    columns = ", ".join(MATCH_FIELDS)
    rows = bind.execute(sa.text(f"SELECT id, {columns} FROM market_identity")).mappings().all()
    counts = {str(a): b for a, b in bind.execute(sa.text(
        "SELECT identity_id, COUNT(*) FROM price_observation GROUP BY identity_id")).all()}
    groups: dict[tuple, list[str]] = {}
    for row in rows:
        groups.setdefault(tuple(row[name] for name in MATCH_FIELDS), []).append(str(row["id"]))
    merged = []
    for ids in groups.values():
        if len(ids) < 2:
            continue
        keep, *dead = sorted(ids, key=lambda one: (-counts.get(one, 0), one))
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


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "market_identity" not in tables:
        return
    products = _add_reading_product(bind, tables)
    moved, rekey = _resolve_grades(bind)
    vintages, left = (_rekey_vintages(bind, rekey) if "forecast_vintage" in tables else (0, 0))
    merged = _merge_duplicates(bind, tables)
    print(f"0060: {products} readings given their supplier product; {moved} grades resolved to a "
          f"code or nothing; {vintages} forecast series re-keyed, {left} unchanged; "
          f"{len(merged)} identities merged away")


def downgrade() -> None:
    """The 64 spellings cannot be recovered from twelve codes, and the merges cannot be undone. The
    reading's product column goes; the grade codes stay, said plainly rather than pretended."""
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "price_observation" not in tables:
        return
    have = {c["name"] for c in sa.inspect(bind).get_columns("price_observation")}
    if "supplier_product" in have:
        with op.batch_alter_table("price_observation") as batch:
            batch.drop_column("supplier_product")
