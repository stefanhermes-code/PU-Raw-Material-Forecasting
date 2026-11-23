"""A price kind is a line, not a market: seven values become four (v1.2.6)

Masterplan A, item 6, on Stefan's ruling of 27 October 2026 - *"Contract and spot shown
separately"* - and his question about the old field, *"I am not sure what that means."*

`observation_type` held seven values that were not mutually exclusive, because it asked three
questions at once: what the number is, how it was formed, and what period it covers. A formula
price *is* a contract price; a quarterly average is an average *of* something. Three of the seven
came from one publisher, so the extractor was choosing between them for prints of the same kind.

So the kind becomes four - assessed, contract, deal, ask - and the other two questions become
properties of the **reading**: `price_formed` and `period_covered`, added here.

    was                   identities  readings   becomes
    market_price, customs lanes   176    18,234   deal      (comtrade_*, comext_*)
    market_price, the rest        141    12,599   assessed
    transaction_price              28     1,766   deal
    contract_price                  5     1,807   contract  (formed: negotiated)
    formula_based                   3       879   contract  (formed: formula)
    producer_price                  4       136   ask
    seller_quote                    1        30   ask
    quarterly_average               3        11   assessed  (period: quarter)

`market_price` is the only value that needs the reading's publisher as well, because it held two
different things: a publisher's assessment, and a customs unit value - which is a trade that
happened, so a deal. Checked before writing this: **no identity mixes the two** (0 of the 317
`market_price` identities hold both customs and assessed readings), so the kind can stay on the
identity, where it is the line. And the collapse **merges nothing** - 376 identities stay 376
distinct on the ten fields the store matches on - because the identities that differ in kind differ
elsewhere too.

`period_covered` is read off the publisher, not guessed: every reading from the monthly, quarterly
and annual lanes falls on the first of its period, and the gap between consecutive stamps says
which period it is. That rule was run over all 35,462 readings and agreed with
`price_kinds.period_of` on every source with enough stamps to judge - zero disagreements.

`forecast_vintage.identity_key` carries the kind as its eighth field, so it is re-keyed here; a
governed forecast has to stay reachable from the identity that produced it. The comparability group
does not carry the kind, so it is untouched.

One thing left open rather than invented: **an ask has no `price_formed`.** A posting is neither
assessed nor negotiated nor set by a formula - the seller simply posted it - and rather than force
one of Stefan's three values or add a fourth without asking, the 166 ask readings keep it empty.

Idempotent: a second run finds the four values already in place and nothing to backfill.
"""
from alembic import op
import re
import sqlalchemy as sa

revision = "0058_a_price_kind_is_a_line_not_a_market"
down_revision = "0057_one_unit_and_it_is_mt"
branch_labels = None
depends_on = None

# The vocabulary, written out rather than imported: a migration has to mean the same thing in five
# years, whatever `raw_material_forecasting.price_kinds` says then. It is checked against that
# module by `tests/test_a_price_kind_is_a_line.py`, so the two cannot drift unnoticed.
ASSESSED, CONTRACT, DEAL, ASK = "assessed", "contract", "deal", "ask"
KINDS = (ASSESSED, CONTRACT, DEAL, ASK)
WAS = {"market_price": ASSESSED, "contract_price": CONTRACT, "formula_based": CONTRACT,
       "transaction_price": DEAL, "producer_price": ASK, "seller_quote": ASK,
       "quarterly_average": ASSESSED}
WAS_FORMED = {"market_price": "assessed", "quarterly_average": "assessed",
              "contract_price": "negotiated", "transaction_price": "negotiated",
              "formula_based": "formula"}
CUSTOMS_LANES = re.compile(r"^(comtrade_|comext_)", re.I)
PERIOD_BY_SOURCE = (
    (re.compile(r"^comtrade_annual_", re.I), "year"),
    (re.compile(r"^(comtrade_monthly_|comext_)", re.I), "month"),
    (re.compile(r"^(europe_published_ddp|europe_delivered_purchases|fnag_switzerland"
                r"|foampartner_china|kgi_thailand|imarc_|procurementresource_"
                r"|import_procurement_resource|import_sunsirs_priceseek)", re.I), "month"),
    (re.compile(r"^chemanalyst_", re.I), "quarter"),
    (re.compile(r"^(icis_history|import_longzhong_mysteel)$", re.I), "week"),
)

# Field 8 of the eight-field series key is the price kind (`app.domain.series_key.FIELDS`).
_SERIES_KEY_FIELDS = 8
_SERIES_KEY_KIND = 7

MATCH_FIELDS = ("product_family", "product_grade_or_chemistry", "origin", "destination",
                "market_geography", "commercial_basis", "packaging", "currency", "unit",
                "observation_type")
_CHILDREN = ("price_observation", "derived_value", "forecast_method_override",
             "forecast_method_selection", "governed_forecast_run", "model_fit_result")
_RECOMPUTED = ("market_reference_point",)


def _period_of(source_id) -> str:
    source = str(source_id or "").strip()
    for pattern, period in PERIOD_BY_SOURCE:
        if pattern.match(source):
            return period
    return "day"


def _kind_of(stored, sources) -> str:
    """The kind for one identity. `sources` is every publisher that has written to it."""
    was = str(stored or "").strip()
    if was in KINDS:
        return was
    named = [s for s in sources if s]
    if was == "market_price" and named and all(CUSTOMS_LANES.match(s) for s in named):
        return DEAL
    return WAS.get(was, ASSESSED)


def _add_reading_columns(bind, tables: set[str]) -> None:
    if "price_observation" not in tables:
        return
    have = {c["name"] for c in sa.inspect(bind).get_columns("price_observation")}
    with op.batch_alter_table("price_observation") as batch:
        if "price_formed" not in have:
            batch.add_column(sa.Column("price_formed", sa.String, nullable=False,
                                       server_default=""))
        if "period_covered" not in have:
            batch.add_column(sa.Column("period_covered", sa.String, nullable=False,
                                       server_default="day"))


def _backfill_readings(bind) -> int:
    """`price_formed` from what the identity's old value said; `period_covered` from the source."""
    # Only identities still holding one of the OLD seven. This runs before `_collapse_kinds`, and
    # the guard is what makes a second run a no-op: once a kind is `assessed` the reading's
    # `price_formed` is already set, and re-deriving it from the new value would wipe `negotiated`
    # off every deal, because "assessed" is both a kind and a way of being formed.
    placeholders = ", ".join(f":k{i}" for i in range(len(KINDS)))
    pairs = bind.execute(sa.text(
        "SELECT DISTINCT i.observation_type, o.source_id "
        "FROM price_observation o JOIN market_identity i ON i.id = o.identity_id "
        f"WHERE i.observation_type NOT IN ({placeholders})"),
        {f"k{i}": kind for i, kind in enumerate(KINDS)}).all()
    touched = 0
    for stored, source in pairs:
        was = str(stored or "").strip()
        if was in ("assessed", "negotiated", "formula"):
            formed = was
        elif was == "market_price" and CUSTOMS_LANES.match(str(source or "").strip()):
            # A customs unit value is a trade that happened, so its price was negotiated between a
            # buyer and a seller - not assessed by a publisher, whatever the declaration was filed
            # as. Calling it a deal while recording "assessed" would be the old field's fault again.
            formed = "negotiated"
        else:
            formed = WAS_FORMED.get(was, "")
        result = bind.execute(sa.text(
            "UPDATE price_observation SET price_formed = :formed, period_covered = :period "
            "WHERE source_id = :source AND identity_id IN "
            "(SELECT id FROM market_identity WHERE observation_type = :stored)"),
            {"formed": formed, "period": _period_of(source), "source": source, "stored": stored})
        touched += result.rowcount or 0
    return touched


def _series_key(row, kind: str) -> str:
    """The eight-field key `app.domain.series_key` builds, with `kind` as its eighth field."""
    def field(name):
        value = row[name]
        return str(value) if value not in (None, "") else "UNKNOWN"
    return "|".join([field("product_family"), field("product_grade_or_chemistry"),
                     field("market_geography"), field("commercial_basis"), field("packaging"),
                     field("currency"), field("unit"), kind])


def _collapse_kinds(bind) -> tuple[int, dict, dict]:
    """The identity's kind becomes one of the four.

    Returns the count, a tally of what moved where, and the EXACT old-key -> new-key map for
    `forecast_vintage`. The map is built here, before anything is written, because after the
    collapse the old value is gone and a stored key cannot be matched back to its identity: two
    identities can share the seven fields before the kind - one assessed, one a producer's posting
    - and nothing in the key would say which one a vintage belonged to.
    """
    columns = ("id", "observation_type", "product_family", "product_grade_or_chemistry",
               "market_geography", "commercial_basis", "packaging", "currency", "unit")
    rows = bind.execute(sa.text(
        f"SELECT {', '.join(columns)} FROM market_identity")).mappings().all()
    by_identity: dict[str, set] = {}
    for row in bind.execute(sa.text(
            "SELECT identity_id, source_id FROM price_observation "
            "GROUP BY identity_id, source_id")).all():
        by_identity.setdefault(str(row[0]), set()).add(row[1])

    moved, tally, rekey = 0, {}, {}
    for row in rows:
        was = str(row["observation_type"] or "").strip()
        now = _kind_of(was, by_identity.get(str(row["id"]), set()))
        if now == was:
            continue
        rekey[_series_key(row, was)] = _series_key(row, now)
        bind.execute(sa.text("UPDATE market_identity SET observation_type = :now WHERE id = :id"),
                     {"now": now, "id": row["id"]})
        tally[f"{was} -> {now}"] = tally.get(f"{was} -> {now}", 0) + 1
        moved += 1
    return moved, tally, rekey


def _merge_duplicates(bind, tables: set[str]) -> list[tuple[str, str]]:
    """Any identities the collapse made identical. Measured as none, kept so it cannot surprise."""
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


def _rekey_vintages(bind, rekey: dict[str, str]) -> tuple[int, int]:
    """Field 8 of every stored series key, from the map built before the collapse.

    A key the map does not hold is left exactly as it is. Fourteen of them are stale: they name a
    `commercial_basis` no identity carries any more - "delivery: Shandong; Incoterm n/s" and the
    like, from before that field was normalised - so there is no identity for them to stay
    reachable from and inventing a kind for 42 orphan rows would be worse than leaving them.
    """
    moved, left = 0, 0
    for (key,) in bind.execute(sa.text("SELECT DISTINCT identity_key FROM forecast_vintage")).all():
        now = rekey.get(str(key))
        if now is None:
            parts = str(key or "").split("|")
            if len(parts) == _SERIES_KEY_FIELDS and parts[_SERIES_KEY_KIND] not in KINDS:
                left += 1
            continue
        bind.execute(
            sa.text("UPDATE forecast_vintage SET identity_key = :new WHERE identity_key = :old"),
            {"new": now, "old": key})
        moved += 1
    return moved, left


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "market_identity" not in tables:
        return
    _add_reading_columns(bind, tables)
    readings = _backfill_readings(bind) if "price_observation" in tables else 0
    moved, tally, rekey = _collapse_kinds(bind)
    merged = _merge_duplicates(bind, tables)
    vintages, orphans = (_rekey_vintages(bind, rekey) if "forecast_vintage" in tables else (0, 0))
    print(f"0058: {readings} readings given a formed/period; {moved} identities collapsed {tally}; "
          f"{len(merged)} merged away {merged or ''}; {vintages} forecast series re-keyed, "
          f"{orphans} left (no identity carries their fields any more)")


def downgrade() -> None:
    """The four values cannot be split back into seven - `deal` was two of them and `contract`
    two more - so this drops the two reading columns and leaves the kinds as they are. Said plainly
    rather than pretending: the seven were not mutually exclusive, which is why they went."""
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "price_observation" not in tables:
        return
    have = {c["name"] for c in sa.inspect(bind).get_columns("price_observation")}
    with op.batch_alter_table("price_observation") as batch:
        for column in ("price_formed", "period_covered"):
            if column in have:
                batch.drop_column(column)
