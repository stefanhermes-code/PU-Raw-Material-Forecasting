"""The store enforces what the models declare (v1.2.78)

Found by rebuilding the live store from a backup onto a local Postgres and comparing it, table by
table and column by column, against a store built by replaying this chain onto an empty server.
The two should describe the same database. In thirteen places they did not, and every one of them
has the same cause: 0001 is `Base.metadata.create_all`, which creates a table it does not find and
NEVER ALTERS ONE IT DOES. So anything added to the models after a table existed reached a store
built from scratch and never reached the store that was already running.

Two indexes, declared in `models.py` with `index=True` and absent from the live store:

    forecast_vintage (chemical)          - how the performance screen narrows 7,720 claims
    quarantined_reading (source_id)      - how the quarantine page groups refusals by publisher

Eleven columns the models declare as not optional, which the live store still accepts a NULL in:

    forecast_vintage   quantiles, calibration, metadata_json, created_at
    fx_rate            currency, fetched_at, rate_date
    investigation_case data
    investigation_enquiry data
    quarantined_reading example
    reference_series   imported_at

None of the eleven holds a NULL today - all 343,961 rows were counted on 9 November 2026 and every
one of these columns came back zero - so this tightens the store to what the application already
writes rather than changing what it means. A column that does hold a NULL when this runs is
reported and left alone: a migration that cannot keep its promise should say so, not force it.

Nothing here is created or altered twice. An index already present is left alone and a column
already NOT NULL is left alone, so this is a no-op against any store built from the models and the
repair against the one that was not.

SQLite is left alone throughout. Every SQLite store in this project is built by 0001's
`create_all`, so it has all of this from the models already; and `ALTER COLUMN ... SET NOT NULL`
does not exist there - it would mean rebuilding each table through batch mode to achieve nothing.
"""
from alembic import op
import sqlalchemy as sa

revision = "0070_the_store_enforces_what_the_models_declare"
down_revision = "0069_a_case_points_at_its_evidence_by_hash"
branch_labels = None
depends_on = None

# (index name, table, columns) - the names the models' own `index=True` produces.
WANTED_INDEXES = (
    ("ix_forecast_vintage_chemical", "forecast_vintage", ["chemical"]),
    ("ix_quarantined_reading_source_id", "quarantined_reading", ["source_id"]),
)

# (table, column) for every column the models declare as not optional.
WANTED_NOT_NULL = (
    ("forecast_vintage", "quantiles"),
    ("forecast_vintage", "calibration"),
    ("forecast_vintage", "metadata_json"),
    ("forecast_vintage", "created_at"),
    ("fx_rate", "currency"),
    ("fx_rate", "fetched_at"),
    ("fx_rate", "rate_date"),
    ("investigation_case", "data"),
    ("investigation_enquiry", "data"),
    ("quarantined_reading", "example"),
    ("reference_series", "imported_at"),
)


def _add_the_missing_indexes(bind, inspector, tables) -> int:
    made = 0
    for name, table, columns in WANTED_INDEXES:
        if table not in tables:
            print("[0070] no %s in this store; skipped" % table)
            continue
        present = inspector.get_indexes(table)
        # By name AND by shape: a store built from the models already has the index, under
        # whichever name SQLAlchemy chose rather than the one alembic would choose.
        if name in {index["name"] for index in present}:
            continue
        if tuple(columns) in {tuple(index["column_names"]) for index in present}:
            continue
        op.create_index(name, table, columns)
        print("[0070] created %s" % name)
        made += 1
    return made


def _close_the_nullable_columns(bind, inspector, tables) -> int:
    if bind.dialect.name == "sqlite":
        print("[0070] SQLite: the models already built these NOT NULL; nothing to alter")
        return 0
    closed = 0
    for table, column in WANTED_NOT_NULL:
        if table not in tables:
            print("[0070] no %s in this store; skipped" % table)
            continue
        found = next((c for c in inspector.get_columns(table) if c["name"] == column), None)
        if found is None:
            print("[0070] %s.%s is not in this store; skipped" % (table, column))
            continue
        if not found["nullable"]:
            continue
        holes = bind.execute(sa.text(
            'SELECT count(*) FROM "%s" WHERE "%s" IS NULL' % (table, column))).scalar()
        if holes:
            # Left exactly as it is. The models say this column is not optional and the store
            # disagrees in `holes` rows; which of the two is wrong is not a question a migration
            # gets to answer, and filling them in would be inventing data.
            print("[0070] %s.%s holds %d NULL(s); LEFT NULLABLE for a reader to settle"
                  % (table, column, holes))
            continue
        op.alter_column(table, column, nullable=False)
        print("[0070] %s.%s is now NOT NULL" % (table, column))
        closed += 1
    return closed


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    made = _add_the_missing_indexes(bind, inspector, tables)
    closed = _close_the_nullable_columns(bind, inspector, tables)
    print("[0070] indexes created: %d; columns closed to NULL: %d" % (made, closed))


def downgrade() -> None:
    """Reversible, and the store is the poorer for it.

    The indexes go and the columns open again. Nothing is lost that a reader needs, because every
    value these columns hold is still there - only the rule that they must hold one is dropped.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if bind.dialect.name != "sqlite":
        for table, column in WANTED_NOT_NULL:
            if table not in tables:
                continue
            found = next((c for c in inspector.get_columns(table) if c["name"] == column), None)
            if found is not None and not found["nullable"]:
                op.alter_column(table, column, nullable=True)
    for name, table, _columns in WANTED_INDEXES:
        if table in tables and name in {i["name"] for i in inspector.get_indexes(table)}:
            op.drop_index(name, table_name=table)
