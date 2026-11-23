"""The two aliphatic isocyanates come out of the store (v1.2.73)

Stefan, 6 November 2026, having been told the rows were doing no harm where they sat and asked for
them to go anyway: **"Purge them."**

v1.2.72 took HDI and IPDI out of `identity_vocabulary.RAW_MATERIALS` - they are coatings materials
and HTC's book is foam, and that file carries the whole argument and the one thing that would
reverse it. What it deliberately did not do was touch the store, so the readings stayed where they
were and Data & Sources said **"Chemical not named in the application"** beside them. That is an
honest answer, and it is not the one Stefan wants to keep reading.

### What is in the store, measured today against the live database

    market_reference_point     6   three governed versions of each series, the last one net of VAT
    price_in_currency         12   each reading held in CNY, USD and EUR
    price_observation          4   two readings each, from one document, read by two importers
    forecast_vintage          18   control-arm baselines, every one of them
    market_identity            2   HDI China CNY/MT and IPDI China CNY/MT, both assessed
    ----------------------------
                              42

The four readings are 36,000 CNY/MT for HDI and 47,000 for IPDI, both dated 18 October 2026, both
arriving twice over - once as `import_htc_global` and once as `import_sunsirs` - from one document.

### What is NOT touched, and this is the point of the migration rather than of a SQL statement

**The raw evidence stays.** All four readings carry the same `evidence_id` - which is a content
hash, not a row id - and so do forty-one others: thirteen polyether polyol, twelve pMDI, ten TDI and
six aliphatic polyester polyol. That hash resolves to five `raw_evidence` rows, one per importer
that read the same October document. Deleting any of them to tidy up after two chemicals would take
four live markets' provenance with it, and this store's rule is that a reading whose evidence cannot
be produced is an assertion. Checked after this ran: the five rows are there, all APPROVED, and the
forty-one readings still resolve.

**The quarantine is empty of them**, checked: nothing aliphatic is waiting there. Nothing was forced
onto the customer pages by hand either - `customer_page_controls.json` holds no HDI or IPDI entry -
so the `forced_onto_pages` hole left open in v1.2.72 was never a hole in fact.

### Why the vintages go too, which is the only arguable part

`forecast_vintage` is append-only by design: it is the record of what this application said, kept so
that a claim can be scored against what happened. Deleting from it is not something to do lightly.

These eighteen can go, and the reason is in the rows themselves. Every one is `state = CONTROL` -
the baseline arm, never released, never shown to anybody - every one is `naive_last`, every one is
`as_of` 18 October, and **not one has an outcome recorded**. Nor can one ever be: scoring needs a
later reading of the same series, no reading will arrive for a chemical the door now refuses, and
the series they point at is deleted by this migration. A forecast that can never be scored, of a
market that does not exist, held in the table whose whole purpose is scoring, records nothing.

### The guard

If either family is back in `RAW_MATERIALS` when this runs, that family is left completely alone and
the migration says so. The one line that would bring them back is written in `identity_vocabulary`:
a system-house customer asking about yellowing in light-stable integral skin. If that has happened
by the time somebody replays this store from zero, the readings are theirs to keep.
"""
from alembic import op
import sqlalchemy as sa

revision = "0068_two_chemicals_leave_the_store"
down_revision = "0067_an_event_names_what_its_headline_names"
branch_labels = None
depends_on = None

# Written down rather than derived, because they are gone from the vocabulary and there is nothing
# left to derive them from. The guard below asks the vocabulary the only question it can still
# answer: are these two still untracked?
FAMILIES = ("HDI", "IPDI")

# Everything that points at a reading, and everything that points at a series. Child-first.
# `logistics_normalization` cascades from `price_observation` in the live schema and is still named
# here, because a migration that relies on a cascade it did not write breaks quietly when the schema
# changes. Several of these hold nothing for these two families today; they are named so that the
# deletion cannot fail on a pointer nobody measured.
BY_OBSERVATION = ("price_in_currency", "quality_issue", "logistics_normalization")
BY_IDENTITY = ("market_reference_point", "derived_value", "market_reference",
               "forecast_method_override", "forecast_method_selection",
               "governed_forecast_run", "model_fit_result")


def _usable(bind, table: str, column: str) -> bool:
    """Whether this store has that column. Replays and the test fixtures hold a subset."""
    inspector = sa.inspect(bind)
    if table not in set(inspector.get_table_names()):
        return False
    return column in {c["name"] for c in inspector.get_columns(table)}


def _in_list(sql: str, name: str, values: list[str]):
    """`IN (...)` over a Python list, which both psycopg and sqlite take."""
    return sa.text(sql).bindparams(sa.bindparam(name, expanding=True))


def upgrade() -> None:
    bind = op.get_bind()

    families = list(FAMILIES)
    try:
        from raw_material_forecasting.identity_vocabulary import RAW_MATERIALS
    except Exception as exc:                                  # pragma: no cover - import guard
        print(f"[0068] the vocabulary could not be read ({exc}); going by the written list")
    else:
        kept = [f for f in families if f in RAW_MATERIALS]
        for family in kept:
            print(f"[0068] {family} is named in RAW_MATERIALS again - its readings are left alone")
        families = [f for f in families if f not in kept]
    if not families:
        print("[0068] both families are tracked again; nothing is removed")
        return

    if not _usable(bind, "market_identity", "product_family"):
        print("[0068] this store holds no market_identity table; nothing to do")
        return

    identities = [row[0] for row in bind.execute(
        _in_list("SELECT id FROM market_identity WHERE product_family IN :f", "f", families),
        {"f": families}).fetchall()]
    print(f"[0068] series found for {', '.join(families)}: {len(identities)}")

    observations: list[str] = []
    if identities and _usable(bind, "price_observation", "identity_id"):
        observations = [row[0] for row in bind.execute(
            _in_list("SELECT id FROM price_observation WHERE identity_id IN :i", "i", identities),
            {"i": identities}).fetchall()]
    print(f"[0068] readings found: {len(observations)}")

    removed: dict[str, int] = {}

    def _delete(table: str, column: str, values: list[str]) -> None:
        if not values or not _usable(bind, table, column):
            return
        result = bind.execute(
            _in_list(f"DELETE FROM {table} WHERE {column} IN :v", "v", values), {"v": values})
        count = int(result.rowcount or 0)
        if count:
            removed[table] = removed.get(table, 0) + count

    for table in BY_OBSERVATION:
        _delete(table, "observation_id", observations)
    for table in BY_IDENTITY:
        _delete(table, "identity_id", identities)
    _delete("price_observation", "identity_id", identities)
    # By chemical, not by series: two of the eighteen vintages are keyed on a grade-level identity
    # string that never became a `market_identity` row at all.
    _delete("forecast_vintage", "chemical", families)
    _delete("market_identity", "id", identities)

    for table in sorted(removed):
        print(f"[0068]   {table}: {removed[table]}")
    print(f"[0068] rows removed: {sum(removed.values())}")

    left = bind.execute(
        _in_list("SELECT count(*) FROM market_identity WHERE product_family IN :f", "f", families),
        {"f": families}).scalar()
    if left:                                                  # pragma: no cover - cannot happen
        raise RuntimeError(f"0068 left {left} aliphatic series in the store")


def downgrade() -> None:
    """Not reversible, and it was asked for in those terms.

    The document the four readings were read from is still in `raw_evidence`, untouched and shared
    with four live markets, so if these two are ever named again the import reads them back from the
    publisher's own words rather than from anything this migration could have kept.
    """
