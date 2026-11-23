"""HTC's own historical purchase prices, loaded as governed observations.

The correlation work needs years of price history and the live collectors have days of it. HTC
already owns years: FoamPartner's China procurement records (2014-2017) and KGI's Thai ERP postings
(2021-2023). `scripts/extract_historical_prices.py` pulls them out of the workbooks into
`data/research/historical_rm_native.csv`; this module loads that file into the engine.

Three decisions worth knowing.

**They are stored as `transaction_price`, not `market_price`.** These are prices one buyer actually
paid, which is evidence of a different kind from a published assessment - nobody's judgement sits
between the trade and the number, but they carry contract terms, volume tiers and negotiating
position, and they lag spot. Everything that builds the customer-facing reference filters on
`market_price`, so this history informs analysis without ever leaking into a governed price.

**They are stored in the currency they were paid in.** CNY per tonne for China, THB per tonne for
Thailand. The application already converts to a display currency using the ECB rate of each
observation's own date - the same path the live ECHEMI CNY series takes - so converting here would
duplicate that logic and throw away the original. What this module does need is for the FX history to
reach back far enough, which is why it extends the ECB backfill to 2014.

**Supplier is part of the identity.** BASF and Wanhua quoted KGI 93 and 64 baht in the same month.
That is two contracts, not a number to average.

**One of these series is not a purchase record any more.** FoamPartner's Changzhou T-80 joins the
published China TDI ex-works series - Stefan's decision on 14 September 2026. The publishers start in
January 2024 and FoamPartner ends in April 2017, so the two occupy different years of one line and
never have to be reconciled against each other. `MARKET_LANE` says which rows do this and under what
grade; everything else stays a transaction price and stays out of the customer-facing reference.

**Origin and terms are stated here, not patched afterwards.** This module used to file every purchase
as UNKNOWN origin on a DELIVERED basis, on the reasoning that a delivered price is quoted at the
buyer's end and states no origin. Migrations 0017 and 0018 then moved the rows onto China-origin
identities where they belong. The loader did not know that, so on the next start it looked for its
rows on the identity it still built, found none, and inserted a second copy - and another on every
start after that. 352 rows for 176 pieces of evidence.

A migration cannot hold a fact that the code which writes the rows does not share. So the facts live
in ATTRIBUTION: FoamPartner's Changzhou plant is Chinese material delivered in China, KGI bought
Chinese material into Thailand on CIF from BASF and DDP from Nagase - all stated by Stefan on 14
September 2026. A supplier not listed keeps the old treatment, because a silence is still a silence.
Where the product itself has been spoken for, MARKET_LANE sets the terms and this table is not
consulted.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.db.models import MarketIdentityModel, PriceObservationModel, SourceRegistryModel
from app.fx.rates import cross_rate, rate_table
from app.domain.schemas import MarketIdentity
from app.paths import repo_path
from app.persistence.identity import Refused, ensure_identity
from app.persistence import quarantine
from raw_material_forecasting import price_kinds

OBSERVATION_TYPE = price_kinds.DEAL

# Series stored in a currency other than the one they were paid in.
#
# KGI bought Chinese material and paid in baht, so the baht number already contains whatever rate the
# seller applied on the day. Left in baht the series moves when the baht moves: Nagase held 64,200 THB
# from July 2021 to July 2022 while the same contract, read in yuan, fell 6.8%. Converting at the ECB
# cross of the day the price was struck undoes the seller's conversion at the moment it was made.
#
# This is only safe on prices that are real prints. Run it over a figure carried forward and it
# manufactures movement out of a number that never moved, which is why the ledger clean-up came first.
#
# Stefan, 14 September 2026. The baht figure is kept on every row in `native_price`, so the
# conversion can be rechecked or redone.
STORE_CURRENCY: dict[str, str] = {
    "kgi_thailand": "CNY",
}


def store_currency(source: str, native: str) -> str:
    """The currency this series is kept in, which is not always the one it was paid in."""
    return STORE_CURRENCY.get(source or "", (native or "").upper())

# (source, supplier) -> (origin, destination, commercial basis). Supplier "" matches every row of
# that source. An entry that is absent leaves the row as a delivered price of unstated origin.
ATTRIBUTION: dict[tuple[str, str], tuple[str | None, str | None, str]] = {
    # FoamPartner's Changzhou prices are ex-works. Stated by Stefan, 14 September 2026, replacing the
    # DAP reading 0017 and 0018 carried: nothing in the workbooks says either way, and the decision
    # is his. EXW is quoted at the seller's end, so no destination is claimed.
    ("foampartner_china", ""): ("China", None, "EXW"),
    # Both KGI suppliers sell the same way. BASF (Thai) Limited and Nagase (Thailand) Co., Ltd are
    # Thai entities invoicing in baht with 7% Thai VAT on the face of every order - a domestic sale,
    # duty and import VAT already settled, delivered. A real CIF import would put those with Customs,
    # not on the supplier's invoice. CIF and DDP were read off nothing: no BASF document prints a
    # term at all, and the only term printed anywhere in the sixteen orders is DAP, on three Nagase
    # forms. Set to DAP by Stefan, 14 September 2026.
    ("kgi_thailand", "BASF"): ("China", "Thailand", "DAP"),
    ("kgi_thailand", "Wanhua (via Nagase)"): ("China", "Thailand", "DAP"),
}

# Series where the supplier is not part of the identity.
#
# It normally is, and for a reason: two suppliers quoting one month is two contracts, not a number to
# average. KGI's TDI is the exception, on the same evidence that settled the Incoterm. Both suppliers
# are Thai entities selling Chinese T-80 into Rat Burana on identical terms, so what separated them
# was a guess about Incoterms that the invoices contradict. One series, and the supplier stays in
# every observation's evidence id. Stefan, 14 September 2026.
COMBINED_SUPPLIER_SERIES: set[tuple[str, str, str]] = {
    ("kgi_thailand", "TDI", "TDI 80/20"),
    # KGI's polymeric MDI, on the same evidence and for the same reason. Wanhua's W8223 through
    # Nagase and BASF's Lupranate MX118/1 are both Thai entities invoicing in baht on identical
    # terms; separating them would separate two contracts for one material.
    ("kgi_thailand", "PMDI", "W8223"),
    ("kgi_thailand", "PMDI", "Lupranate MX118/1"),
}


def combines_suppliers(source: str, chemical: str, grade: str) -> bool:
    """True when every supplier of this product shares one series."""
    return (source or "", chemical or "", grade or "") in COMBINED_SUPPLIER_SERIES

UNSTATED = (None, None, "DAP")
# A purchase price with no term printed on the paperwork is a delivered price, and delivered is
# DAP - Stefan, 23 September 2026. This used to file it as "DELIVERED", which is not an Incoterm
# and was a second spelling of the same market: the DAP identities were written beside it and
# every European purchase series ended up held twice, 732 readings with a copy each. Migration
# 0046 removed the copies; this is the line that wrote them.

# Purchase rows that join the published market series rather than standing as their own.
#
# China TDI ex-works is one line. The published assessments cover January 2024 onward; FoamPartner's
# Changzhou T-80 covers 2014 to April 2017; KGI's purchases cover July 2021 to July 2024. Decided by
# Stefan on 14 September 2026, in that order, and this is the last of them - after it there is one
# TDI China EXW series and nothing else to call anything.
#
# What this costs, stated rather than hidden: KGI bought DAP into Thailand, so freight out of China,
# Thai duty and the distributor's margin are inside those prices, and they are filed here as ex-works
# China. On the two months where both exist they sit within 1.3% of the published assessment, so the
# join shows no step - but the terms are not the same terms.
#
# (source, chemical, grade) -> (grade it joins under, origin, destination, basis). The published
# series carries no grade, because a China TDI spot assessment is T-80 without saying so; a row has
# to drop its grade to land on that identity rather than beside it.
MARKET_LANE: dict[tuple[str, str, str], tuple[str | None, str | None, str | None, str]] = {
    ("foampartner_china", "TDI", "TDI 80/20"): (None, "China", None, "EXW"),
    ("kgi_thailand", "TDI", "TDI 80/20"): (None, "China", None, "EXW"),
    # China polymeric MDI, merged on Stefan's instruction, 16 September 2026. Four orders across
    # three years - Wanhua's W8223 in November 2021, November 2023 and February 2024, and BASF's
    # Lupranate MX118/1 in December 2023 - against a published series that starts in October 2025.
    # They are the only MDI evidence before then.
    #
    # The same cost as TDI's, stated rather than hidden: these were delivered into Thailand, so
    # freight out of China, Thai duty and the distributor's margin sit inside them, and they are
    # filed here as ex-works China. Unlike TDI there is no overlapping month to measure the step,
    # because the published series does not reach back to 2024.
    ("kgi_thailand", "PMDI", "W8223"): (None, "China", None, "EXW"),
    ("kgi_thailand", "PMDI", "Lupranate MX118/1"): (None, "China", None, "EXW"),
    # China polyether polyol, merged the same day. FoamPartner's Changzhou history under its two
    # grade labels, and KGI's Shell Caradol SC65-18S.
    ("foampartner_china", "POLYETHER_POLYOL", "ether"): (None, "China", None, "EXW"),
    ("foampartner_china", "POLYETHER_POLYOL", "standard"): (None, "China", None, "EXW"),
    ("kgi_thailand", "POLYETHER_POLYOL", "Caradol SC65-18S"): (None, "China", None, "EXW"),
}

MARKET_OBSERVATION_TYPE = price_kinds.ASSESSED


def attribution_for(source: str, supplier: str) -> tuple[str | None, str | None, str]:
    """Origin, destination and basis for a purchase row, or the unstated default."""
    return ATTRIBUTION.get((source, supplier), ATTRIBUTION.get((source, ""), UNSTATED))


# The family a row takes when it crosses onto a published series.
#
# Empty, and kept for the next time the two disagree. It held POLYETHER_POLYOL -> POLYOL, because
# the collectors wrote POLYOL while the purchase files said POLYETHER_POLYOL, and the merge of
# 14 September put the purchases beside the live series instead of on it. The resolver now returns
# POLYETHER_POLYOL, so both sides say the same word and there is nothing to translate.
MARKET_LANE_FAMILY: dict[str, str] = {}


def market_family_for(chemical: str) -> str:
    """The family a crossing row is filed under, which is the one the publishers use."""
    return MARKET_LANE_FAMILY.get(str(chemical or "").strip().upper(), str(chemical or ""))


def market_lane_for(row: dict):
    """(joins a published series, grade, origin, destination, basis) - or (False, ...) of Nones.

    The supplier is not consulted. It used to be, to stop a supplier-specific contract crossing into
    a market series, but KGI's TDI is one series across both its suppliers already - see
    COMBINED_SUPPLIER_SERIES - so the grade is what identifies it.
    """
    key = (row.get("source", ""), row.get("chemical", ""), row.get("grade", ""))
    spec = MARKET_LANE.get(key)
    if spec is None:
        return False, None, None, None, None
    return (True,) + spec

# Resolved from the package, not the working directory. The engine starts in `market_data_engine/`,
# where a relative "data/research/..." points at a folder that does not exist.
DEFAULT_FILE = repo_path("data", "research", "historical_rm_native.csv")

# Fritz Nauer AG's European procurement, kept in its own file rather than appended to the China one.
# Different plant, different continent, different currency, and a separate extraction that can be
# re-run without touching the other.
FNAG_FILE = repo_path("data", "research", "fnag_europe_native.csv")

HISTORY_FILES: tuple = (DEFAULT_FILE, FNAG_FILE)

# The internal sources. Purchase records, not publications - so they are registered as internal and
# never presented as a market publisher.
SOURCES: dict[str, dict] = {
    "foampartner_china": {
        "publisher": "FoamPartner China (FPB)",
        "name": "FoamPartner China raw-material procurement 2014-2017",
        "url": "internal://foampartner/procurement",
    },
    "kgi_thailand": {
        "publisher": "King Global Industries",
        "name": "KGI Thailand raw-material purchase postings 2021-2023",
        "url": "internal://kgi/erp",
    },
    # Fritz Nauer AG, Wolfhausen, Switzerland. FPB's European sister plant, and the book that
    # carries FPB's own China prices in a block beside its European ones - which is where the FPB
    # initials came from. Prices are net averages quoted per 100 kg; the extraction divides by 100
    # and the 2016 consumption book, which records kilos and euros side by side, agrees with the
    # result to within 1.6% on every material.
    "fnag_switzerland": {
        "publisher": "Fritz Nauer AG (FNAG)",
        "name": "Fritz Nauer AG European raw-material procurement 2013-2017",
        "url": "internal://fnag/procurement",
    },
}


def _evidence_id(row: dict) -> str:
    """Stable per observation, so re-importing the same file changes nothing."""
    return "history:" + "|".join([
        row["source"], row["chemical"], row.get("grade", ""), row.get("supplier", ""),
        row["observation_month"],
    ])


def _identity_for(row: dict) -> MarketIdentity:
    source = row.get("source", "")
    if combines_suppliers(source, row.get("chemical", ""), row.get("grade", "")):
        grade = row.get("grade", "")
    else:
        grade = " / ".join(part for part in (row.get("grade", ""), row.get("supplier", "")) if part)
    currency = store_currency(source, row["native_currency"])
    origin, destination, basis = attribution_for(source, row.get("supplier", ""))
    joins_market, market_grade, market_origin, market_destination, market_basis = market_lane_for(row)
    geography = row["market_geography"]
    if joins_market:
        grade = market_grade or ""
        origin, destination, basis = market_origin, market_destination, market_basis
        # The market it belongs to, not the market it was bought in. KGI bought in Thailand; the
        # series is China ex-works, and a row keeping "Thailand" here lands beside it, not on it.
        geography = market_origin
    return MarketIdentity(
        product_family=market_family_for(row["chemical"]) if joins_market else row["chemical"],
        product_grade_or_chemistry=grade or None,
        origin=origin,
        destination=destination,
        market_geography=geography,
        incoterm_or_commercial_basis=basis,
        currency=currency,
        unit="t",
        observation_type=MARKET_OBSERVATION_TYPE if joins_market else OBSERVATION_TYPE,
        comparability_group="|".join(
            part for part in ((market_family_for(row["chemical"]) if joins_market else row["chemical"]),
                              grade or "UNKNOWN", origin, destination,
                              geography, basis, currency, "t") if part),
    )


def _ensure_sources(session) -> None:
    for source_id, meta in SOURCES.items():
        # By source_id, not by primary key: `id` is a generated uuid, so session.get() never matches
        # and the insert is attempted again on every start. Look up on the column that identifies it.
        existing = session.scalar(
            select(SourceRegistryModel).where(SourceRegistryModel.source_id == source_id))
        if existing is not None:
            continue
        session.add(SourceRegistryModel(
            source_id=source_id, publisher=meta["publisher"], source_name=meta["name"],
            base_url=meta["url"], rights_class="INTERNAL", rights_status="OWNED",
            collection_method="MANUAL", refresh_frequency="MANUAL", active=True,
            reliability_level=4,
            notes="HTC's own purchase records. Transaction prices, not market assessments: stored as "
                  "transaction_price so they never reach a customer-facing reference.",
        ))


def import_transactions(session_factory, path: Path | str = DEFAULT_FILE) -> dict:
    """Load the extracted history. Idempotent: an observation already present is left alone."""
    path = Path(path)
    if not path.exists():
        return {"status": "absent", "path": str(path), "inserted": 0}

    with path.open(encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle) if row.get("native_price_per_kg")]

    inserted = skipped = unconvertible = 0
    notes: list[str] = []
    with session_factory() as session:
        _ensure_sources(session)
        rates = rate_table(session)
        for row in rows:
            month = date.fromisoformat(row["observation_month"])
            observed = datetime(month.year, month.month, month.day, tzinfo=timezone.utc)
            # Same rule as the purchase orders: a value the application cannot read is skipped
            # with the reason and written to the quarantine, never filed under a guess and never
            # dropped quietly (masterplan D, v1.2.12).
            try:
                identity = ensure_identity(session, _identity_for(row))
            except Refused as refused:
                quarantine.record(
                    session, source_id=str(row.get("source") or ""),
                    refusals=refused.refusals, reading={
                        key: str(value) for key, value in row.items()},
                    seen_on=row.get("observation_month"))
                skipped += 1
                notes.append(f"{_evidence_id(row)}: {refused}")
                continue
            evidence_id = _evidence_id(row)
            already = session.scalar(
                select(PriceObservationModel).where(
                    PriceObservationModel.identity_id == identity.id,
                    PriceObservationModel.source_id == row["source"],
                    PriceObservationModel.evidence_id == evidence_id,
                )
            )
            if already is not None:
                skipped += 1
                continue
            # The workbooks quote per kilo; the application governs per tonne.
            native = round(float(row["native_price_per_kg"]) * 1000.0, 2)
            native_currency = row["native_currency"].upper()
            kept_in = store_currency(row["source"], native_currency)
            price = native
            if kept_in != native_currency:
                found = cross_rate(rates, month, native_currency, kept_in)
                if found is None:
                    # A price that cannot be converted must not be stored looking converted.
                    unconvertible += 1
                    notes.append(f"{evidence_id}: no {native_currency}/{kept_in} rate for "
                                 f"{month.isoformat()}")
                    continue
                price = round(native * found[0], 2)
            session.add(PriceObservationModel(
                identity_id=identity.id, source_id=row["source"], evidence_id=evidence_id,
                price=price, observation_date=observed,
                publication_date=observed, available_from=observed, weight=1.0,
                currency=kept_in,
                native_price=native if kept_in != native_currency else None,
                native_currency=native_currency if kept_in != native_currency else None,
            ))
            inserted += 1
        session.commit()

    return {"status": "ok", "path": str(path), "rows": len(rows),
            "inserted": inserted, "already_present": skipped,
            "unconvertible": unconvertible, "notes": notes[:20]}


def coverage(session_factory) -> list[dict]:
    """One line per historical series: what it is, how many months, and the span."""
    with session_factory() as session:
        rows = session.execute(
            select(MarketIdentityModel, PriceObservationModel)
            .join(PriceObservationModel, PriceObservationModel.identity_id == MarketIdentityModel.id)
            .where(MarketIdentityModel.observation_type == OBSERVATION_TYPE)
        ).all()

    grouped: dict[tuple, list] = {}
    for identity, observation in rows:
        key = (observation.source_id, identity.product_family,
               identity.product_grade_or_chemistry or "", identity.currency)
        grouped.setdefault(key, []).append(observation.observation_date)

    out = []
    for (source_id, family, grade, currency), dates in sorted(grouped.items()):
        clean = sorted(d for d in dates if d is not None)
        out.append({
            "source_id": source_id, "product_family": family, "grade": grade,
            "currency": currency, "months": len(clean),
            "first": clean[0].date().isoformat() if clean else None,
            "last": clean[-1].date().isoformat() if clean else None,
        })
    return out
