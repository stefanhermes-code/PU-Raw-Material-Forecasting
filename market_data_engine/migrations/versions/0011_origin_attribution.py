"""record where each price series' material was actually made (v0.4.70)

`market_identity.origin` and `.destination` have existed since 0001 and were never populated. That
silence let the application present all seven TDI series as if they were equally attributable, when
only two of them are.

The Incoterm is what separates them. EXW and FOB are quoted at the seller's end, so their geography
IS the origin. CFR and CIF are quoted at the buyer's end: the geography is the DESTINATION, and the
origin is unknown unless the publisher says otherwise - which ECHEMI does not. Its column is headed
"Area", there is no origin field, and where ECHEMI does know an origin it writes it into the product
name ("China Base oil - CFR"). The TDI export rows carry no such prefix, so the assumption that they
were Chinese exports was ours, not the publisher's.

Backfills both columns from the rule in `app.domain.origin`. Anything a connector actually stated is
left alone.
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_origin_attribution"
down_revision = "0010_sunsirs_event_lane"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "market_identity" not in inspector.get_table_names():
        return

    from app.domain.origin import derive

    rows = bind.execute(sa.text(
        "SELECT id, market_geography, commercial_basis, origin, destination FROM market_identity"
    )).mappings().all()

    for row in rows:
        origin, destination = derive(
            row["market_geography"], row["commercial_basis"],
            origin=row["origin"], destination=row["destination"],
        )
        if origin == row["origin"] and destination == row["destination"]:
            continue
        bind.execute(
            sa.text("UPDATE market_identity SET origin = :origin, destination = :destination WHERE id = :id"),
            {"origin": origin, "destination": destination, "id": row["id"]},
        )


def downgrade():
    pass
