"""A case points at its evidence by hash, which is not a row id (v1.2.74)

Stefan pressed "Open cases from the store" on the Open questions page and got `Internal Server
Error`. Not a timeout and not an empty store: the engine refused the very first insert.

    IntegrityError: insert or update on table "investigation_case" violates foreign key
    constraint "investigation_case_evidence_id_fkey"
    DETAIL: Key (evidence_id)=(0062e538a0...ecfb95a1) is not present in table "raw_evidence".

The key is a sha256 content hash. `raw_evidence.id` is a uuid. They are two different kinds of
identifier and no value of the first will ever be a value of the second.

`evidence_id` throughout this engine means **the hash of the document the reading came from**.
`price_observation.evidence_id` holds one, `event_record.evidence_id` holds one, and neither
declares a foreign key, because a hash is deliberately many-to-one: one document read by five
importers is five `raw_evidence` rows sharing a hash (v1.2.73 measured exactly that). 0051 created
`investigation_case` with `ForeignKey("raw_evidence.id")`, reading the column name as a row id,
and the case copies `event.evidence_id` straight into it.

Measured against the live store before this was written: of 505 events, **0** carry an
`evidence_id` that matches a `raw_evidence.id` and 148 match a `content_hash`. So the constraint
could never be satisfied by any case opened from any event, ever. It did not bite for three weeks
because the engine was on SQLite, which does not enforce the foreign keys the models declare -
0051's own docstring says so about a different table. On Postgres it is enforced, and the feature
has been dead since the move.

**The constraint is dropped, not repointed.** Repointing it at `content_hash` is not available:
Postgres requires the referenced column to be unique, and this one deliberately is not. And it
should not be wanted - 357 of those 505 events carry a hash with no `raw_evidence` row behind it
at all, because `evidence_id` also holds namespaced keys for loaded data (`history:`,
`bootstrap:`) rather than hashes of collected documents. A case is opened for an observation we
cannot explain. Refusing to open it because its document was loaded rather than fetched is the
same mistake as throwing the observation away, which is the thing this whole table exists to stop.

Nothing is deleted. `investigation_case` holds no rows - this has never been able to write one.
"""
from alembic import op
import sqlalchemy as sa

revision = "0069_a_case_points_at_its_evidence_by_hash"
down_revision = "0068_two_chemicals_leave_the_store"
branch_labels = None
depends_on = None

TABLE = "investigation_case"
COLUMN = "evidence_id"

# SQLite names no constraint it was not given a name for, and a batch rebuild cannot drop what it
# cannot name. This is the convention alembic applies while rebuilding, so the inline foreign key
# from 0051 becomes `fk_investigation_case_evidence_id` for exactly long enough to drop it.
NAMING = {"fk": "fk_%(table_name)s_%(column_0_name)s"}


def _pointing_at_evidence(bind) -> list[dict]:
    """The foreign keys on investigation_case that point at raw_evidence, however named."""
    inspector = sa.inspect(bind)
    if TABLE not in set(inspector.get_table_names()):
        return []
    return [fk for fk in inspector.get_foreign_keys(TABLE)
            if fk.get("referred_table") == "raw_evidence"]


def upgrade() -> None:
    bind = op.get_bind()
    found = _pointing_at_evidence(bind)
    if not found:
        print(f"[0069] no foreign key from {TABLE}.{COLUMN} to raw_evidence; nothing to loosen")
        return

    for fk in found:
        name = fk.get("name") or f"fk_{TABLE}_{COLUMN}"
        if bind.dialect.name == "postgresql":
            op.drop_constraint(name, TABLE, type_="foreignkey")
        else:
            # SQLite has no DROP CONSTRAINT, so alembic rebuilds the table: new table, copy, drop
            # the old one, rename. Where foreign keys are enforced, the drop is refused the moment
            # one enquiry points at one case - which is every store that has ever been worked. This
            # is the rebuild procedure SQLite documents: enforcement off, rebuild, enforcement back
            # exactly as it was found.
            was_on = bind.exec_driver_sql("PRAGMA foreign_keys").scalar()
            bind.exec_driver_sql("PRAGMA foreign_keys = OFF")
            try:
                with op.batch_alter_table(TABLE, naming_convention=NAMING) as batch:
                    batch.drop_constraint(name, type_="foreignkey")
            finally:
                bind.exec_driver_sql(f"PRAGMA foreign_keys = {'ON' if was_on else 'OFF'}")
        print(f"[0069] dropped {name}: a content hash is not a row id")

    left = _pointing_at_evidence(bind)
    if left:                                                  # pragma: no cover - cannot happen
        raise RuntimeError(f"0069 left {len(left)} foreign key(s) from {TABLE} to raw_evidence")


def downgrade() -> None:
    """Deliberately nothing.

    Putting the constraint back would restore a rule the data has never been able to satisfy, and
    the next person to press the button would get the same 500. If this needs reversing, the thing
    to reverse is the premise - that `evidence_id` is a row id - not this migration.
    """
