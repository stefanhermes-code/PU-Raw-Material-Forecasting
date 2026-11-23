"""One China polyether polyol ex-works series (v0.4.161)

Stefan, 16 September 2026. Five identities become one.

    ECHEMI            MW3000                      2026-09
    Longzhong         soft-foam polyether         2026-09
    FoamPartner       "ether" and "standard"      2014-01 to 2017-05
    KGI Thailand      Caradol SC65-18S / Shell    2021-07 to 2023-10

The two publishers were treated as separate markets because each labels the grade in its own words -
ECHEMI says MW3000, Longzhong says 软泡聚醚. They are the same material and, once ECHEMI was read off
its own table rather than the summary sentence above it (0030), they agree: 11,300 against 11,400 on
10 September, 12,583 against 12,600 on the 11th.

FoamPartner's two grade labels are the same thing for the same reason. Stefan, on being shown that
the polyester polyol history is a separate series of its own: "since there is a separate series on
polyester then yes standard and ether are the same".

KGI is a Shell Caradol SC65-18S, delivered into Thailand. Shell's datasheet for the SC series gives
the hydroxyl value as the first number - SC56-18S is 56 mg KOH/g - so this is an OH-65 polyether for
flexible slabstock against the roughly OH-56 grade the publishers quote. Different grade, same
material and the same application, and it carries freight into Thailand and a distributor's margin.
It is filed here as China ex-works by decision, the way KGI's TDI was in 0026. The dollars and baht
each reading arrived in are already in native_price.

What does NOT join, and why: the thirty curated seller quotes from 100ppi and ChemicalBook. Thirty
readings, three prices - 10,560 held over seventeen dates, 11,960 over eleven, 13,890 over two - and
ChemicalBook is a supplier marketplace with a request-for-quote form rather than a price publisher.
That is a seller's list price re-dated on each visit, and it would set the market level for six
months. It stays out, and the gap between 2017 and 2026 stays a gap.

The aliphatic polyester polyol history stays out too. Different material.
"""
from alembic import op
import sqlalchemy as sa

revision = "0031_one_china_polyether_polyol_series"
down_revision = "0030_echemi_readings_come_off_the_table"
branch_labels = None
depends_on = None

# The family the application's selector matches on: "Polyol" upper-cased.
FAMILY = "POLYOL"
GROUP = "POLYOL|UNKNOWN|China|EXW|UNKNOWN|CNY|t"

# The identities that join, named by what they are rather than by an id that differs per store.
JOINING = (
    ("POLYOL", "China", "EXW"),                    # the two publishers
    ("POLYETHER_POLYOL", "China", "EXW"),          # FoamPartner Changzhou
    ("POLYETHER_POLYOL", "Thailand", "DELIVERED"), # KGI, Shell Caradol
)
# Curated seller quotes. Left where they are, not deleted: they are evidence of what a seller asked.
STAYS_OUT = ("CHINA_DOMESTIC_SELLER_QUOTE",)


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if not {"price_observation", "market_identity"} <= tables:
        return

    found = []
    for family, geography, basis in JOINING:
        rows = bind.execute(sa.text(
            "SELECT id, product_grade_or_chemistry, currency FROM market_identity "
            "WHERE product_family = :family AND market_geography = :geography "
            "AND commercial_basis = :basis"
        ), {"family": family, "geography": geography, "basis": basis}).mappings().all()
        for row in rows:
            if row["currency"] != "CNY":
                # Everything joining is already in yuan (0025 converted KGI). A series in another
                # currency is a conversion job, not a merge, and is left alone rather than guessed.
                print(f"[0031] left out, not in yuan: {family}/{geography}/{basis} "
                      f"({row['currency']})")
                continue
            found.append((row["id"], f"{family}/{geography}/{basis}/{row['product_grade_or_chemistry']}"))
    if not found:
        print("[0031] nothing to join")
        return
    for _id, label in found:
        print(f"[0031] joining: {label}")

    # The survivor is the one with the most readings behind it, so the fewest rows move.
    counts = {}
    for identity_id, _label in found:
        counts[identity_id] = bind.execute(sa.text(
            "SELECT COUNT(*) FROM price_observation WHERE identity_id = :id"), {"id": identity_id}
        ).scalar() or 0
    survivor = max(counts, key=lambda key: (counts[key], key))

    bind.execute(sa.text(
        "UPDATE market_identity SET product_family = :family, product_grade_or_chemistry = NULL, "
        "market_geography = 'China', commercial_basis = 'EXW', origin = 'China', "
        "destination = NULL, currency = 'CNY', unit = 't', observation_type = 'market_price', "
        "comparability_group = :group WHERE id = :id"
    ), {"family": FAMILY, "group": GROUP, "id": survivor})

    moved = 0
    for identity_id, _label in found:
        if identity_id == survivor:
            continue
        result = bind.execute(sa.text(
            "UPDATE price_observation SET identity_id = :survivor WHERE identity_id = :old"
        ), {"survivor": survivor, "old": identity_id})
        moved += int(result.rowcount or 0)
    print(f"[0031] readings moved onto the one series: {moved}")

    doubled = bind.execute(sa.text(
        "DELETE FROM price_observation WHERE id IN ("
        "  SELECT id FROM price_observation p WHERE identity_id = :id AND EXISTS ("
        "    SELECT 1 FROM price_observation q WHERE q.identity_id = p.identity_id"
        "      AND q.source_id = p.source_id AND q.price = p.price"
        "      AND q.observation_date = p.observation_date AND q.id < p.id))"
    ), {"id": survivor})
    print(f"[0031] duplicate rows collapsed after the move: {int(doubled.rowcount or 0)}")

    emptied = bind.execute(sa.text(
        "DELETE FROM market_identity WHERE product_family IN ('POLYOL', 'POLYETHER_POLYOL') "
        "AND id <> :id AND commercial_basis NOT IN :out "
        "AND NOT EXISTS (SELECT 1 FROM price_observation WHERE identity_id = market_identity.id)"
        .replace(":out", "('" + "','".join(STAYS_OUT) + "')")
    ), {"id": survivor})
    print(f"[0031] emptied identities removed: {int(emptied.rowcount or 0)}")

    if "market_reference_point" in tables:
        cleared = bind.execute(sa.text(
            "DELETE FROM market_reference_point WHERE identity_id = :id"), {"id": survivor})
        print(f"[0031] consolidated points cleared for rebuild: {int(cleared.rowcount or 0)}")

    total, first, last = bind.execute(sa.text(
        "SELECT COUNT(*), MIN(observation_date), MAX(observation_date) FROM price_observation "
        "WHERE identity_id = :id"), {"id": survivor}).fetchone()
    print(f"[0031] the China polyether polyol ex-works series holds {total} readings, "
          f"{str(first)[:10]} to {str(last)[:10]}")


def downgrade():
    # Five labels for one material are not restored.
    pass
