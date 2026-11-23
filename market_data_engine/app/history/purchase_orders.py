"""Signed purchase orders, loaded as governed observations.

The ERP extract in `transactions.py` stops in November 2023. The purchase orders behind those
postings did not: they sit as signed PDFs, and each one is a price HTC actually agreed, on a dated
document, with the arithmetic on its face. The first four cover April, May and July 2024 - three
months the live China collection has no print for at all.

This is a separate lane from the ERP extract because a PO is a different kind of record. An ERP
posting is a month's activity summarised after the fact; a PO is one negotiation on one day, with its
number, its vendor, its tonnage and its Incoterm. Keeping them apart means the provenance of every
number stays readable: `evidence_id` carries the PO number, so any observation on any screen can be
traced back to a document somebody signed.

THREE DECISIONS
---------------
**Origin comes from the buyer, not from the Incoterm.** Every tank here is Chinese material, and
CIF Bangkok cannot say so - a destination-side term states where a tank landed. The origin rule
exists to stop an origin being invented from silence, not to overwrite one somebody states, so these
are filed as Chinese-origin TDI delivered into Thailand. That is what they are, and it is why they
are worth more than a Thai purchase record.

**A PO is filed under the month it was RAISED, not the month it was delivered.** POM124050022 was
raised on 31 May for delivery on 15 June. The price was agreed in May; the delivery date says when
the tank arrived. Confirmed by Stefan, 14 September 2026, and applied to every PO rather than
case by case - a convention that is decided per document is not a convention.

**A PO supersedes the ERP posting that records the same purchase.** The two describe one trade, so
loading both would count it twice - and the order is the better record. The workbook rows are
inventory-ledger receipts: dated when the tank was booked in, not when the price was agreed, and
carrying no document number, no tonnage and no Incoterm. POM123100008 was signed on 3 October 2023
and posts on 6 November; read from the ledger that price lands a month late. Read from the order it
lands in the month it was struck. Reversed on Stefan's instruction, 14 September 2026.

"The same purchase" is not "the same month" - a receipt falls after its order, not on it. It is the
same product, grade, supplier and market, at the same price, posting no earlier than the order month
and no later than RECEIPT_WINDOW_MONTHS after it. Anything that does not match on price is left
alone: a double-counted price is visible on a chart and can be found, a quietly deleted one cannot.

Where no posting matches, every PO in the month is loaded and the forecasting layer takes the median
of a period's prints - so two POs in one month are two witnesses rather than a collision. The one
exception is two orders raised on the SAME DAY at the SAME PRICE: that is one negotiation written on
two documents, and it is stored as one point.

**The price is the unit price, net of tax.** Every one of these documents shows a subtotal, 7% Thai
VAT, and a gross total. Dividing the gross by the tonnage inflates the price by exactly that 7%, and
it is the kind of error that looks plausible on a chart forever. The loader recomputes the subtotal
from quantity and unit price and refuses the row if it does not tie out.

They are `transaction_price`, like everything else HTC bought, so they inform analysis and can never
reach a customer-facing market reference.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.db.models import MarketIdentityModel, PriceInCurrencyModel, PriceObservationModel
from app.domain.schemas import MarketIdentity
from app.fx.rates import cross_rate, rate_table
from app.history.transactions import (
    MARKET_OBSERVATION_TYPE, OBSERVATION_TYPE, _ensure_sources, combines_suppliers,
    market_lane_for, store_currency,
)
from app.paths import repo_path
from app.persistence.identity import Refused, ensure_identity
from app.persistence import quarantine

DEFAULT_FILE = repo_path("data", "research", "purchase_orders.csv")

# How far the stated subtotal may sit from quantity x unit price before the row is refused. A whole
# unit of currency covers the rounding a PO prints; anything larger is a transcription error.
TOLERANCE = 1.0

# How far a receipt may post after the order it belongs to before it is treated as a different
# trade. A tank ordered in July and booked in in August is one purchase; one booked in five months
# later is another order nobody kept the paper for.
RECEIPT_WINDOW_MONTHS = 3

# How close an ERP posting's price must be to the order's before the two are called one purchase.
# Tight on purpose: these are the same negotiated number read off two documents, so they agree
# exactly or they are not the same trade.
#
# Compared on what was actually paid, not on the stored value. The KGI series is stored in yuan,
# converted at the rate of each row's own date - and an order raised in July and posted in August
# converts at two different rates, so 74,000 baht stops equalling 74,000 baht and the same trade ends
# up in the store twice. `native_price` is the baht figure both rows came from.
SAME_PRICE_TOLERANCE = 1.0


def evidence_id(po_number: str, item_code: str) -> str:
    """The PO number, because that is what makes it this purchase and nothing else.

    Not the month: two POs in one month are two trades. Not the price, and not the revision either:
    a revised order is the same order, and an id that changed with the revision would leave the
    cancelled price in the store beside the one that replaced it. The revision decides which price
    wins on that single row - see `import_purchase_orders`.
    """
    return f"po:{po_number}:{item_code}" if item_code else f"po:{po_number}"


def _identity_for(row: dict) -> MarketIdentity:
    """The identity a PO belongs to, taking origin and Incoterm from the document.

    Both were wrong on the first pass and both matter.

    **Origin.** Every one of these tanks is Chinese material - BASF ships Lupranate T80 out of China,
    Nagase ships Wanhua. The loader filed them with no origin, because `app/domain/origin.py` marks
    the origin UNKNOWN behind a destination-side term, and correctly so: CIF Bangkok says where the
    tank landed and nothing whatever about who made it. That rule fills a silence. It is not meant to
    survive somebody stating the origin, and it does not - a stated origin passes through untouched.
    So the origin is stated here, from the buyer, and these become what they actually are: Chinese
    TDI, priced into Thailand.

    **Incoterm.** Both suppliers sell delivered, and the orders say so. BASF (Thai) Limited and
    Nagase (Thailand) Co., Ltd are Thai entities invoicing in baht with 7% Thai VAT on the face of
    every order - a domestic sale with duty and import VAT already settled. A real CIF import would
    put those with Customs, not on the supplier's invoice. The earlier split, BASF on CIF and Nagase
    on DDP, was read off no document: none of the five BASF orders prints a term, and the only term
    printed anywhere in the sixteen is DAP, on three Nagase forms. Both are DAP from 14 September
    2026, and the two suppliers share one series.

    `incoterm_on_document` still keeps whatever the paper says beside the contract term, so a form
    that disagrees stays visible instead of being quietly overwritten.
    """
    # The supplier is part of the identity unless the series combines them - see
    # COMBINED_SUPPLIER_SERIES in transactions.py, which both loaders read so an order and the
    # postings it extends cannot land on different rows.
    if combines_suppliers(row.get("source", ""), row.get("chemical", ""), row.get("grade", "")):
        grade = row.get("grade", "")
    else:
        grade = " / ".join(part for part in (row.get("grade", ""), row.get("supplier", "")) if part)
    # An order for a product whose series has joined the published market lane joins it too, or the
    # order and the postings it extends end up on two different rows.
    joins_market, market_grade, market_origin, market_destination, market_basis = market_lane_for(row)
    # The series is kept in whatever currency it is kept in - see STORE_CURRENCY in transactions.py.
    # KGI paid in baht and the series is stored in yuan, so the identity has to say yuan or the order
    # lands on a second row beside the postings it extends.
    currency = store_currency(row.get("source", ""), str(row["currency"]))
    destination = row.get("destination") or row.get("market_geography") or ""
    basis = str(row.get("incoterm") or "DELIVERED").strip().upper()
    origin = str(row.get("origin") or "").strip()
    # The price is quoted in the market it lands in, which is what geography means here.
    geography = destination
    if joins_market:
        grade = market_grade or ""
        origin, destination, basis = market_origin or "", market_destination, market_basis
        geography = market_origin
    return MarketIdentity(
        product_family=row["chemical"],
        product_grade_or_chemistry=grade or None,
        origin=origin or None,
        destination=destination,
        market_geography=geography,
        incoterm_or_commercial_basis=basis,
        currency=currency,
        unit="t",
        observation_type=MARKET_OBSERVATION_TYPE if joins_market else OBSERVATION_TYPE,
        comparability_group="|".join(
            part for part in (row["chemical"], grade or "UNKNOWN", origin or "UNKNOWN",
                              destination, basis, currency, "t") if part),
    )


def _self_consistent(row: dict, quantity: float, unit_price: float) -> str:
    """"" when the PO's own numbers tie out, otherwise what disagrees with what."""
    def number(field: str) -> float | None:
        raw = str(row.get(field) or "").replace(",", "").replace(" ", "")
        try:
            return float(raw)
        except ValueError:
            return None

    subtotal, tax, total = number("subtotal"), number("tax"), number("total")
    computed = quantity * unit_price
    if subtotal is not None and abs(subtotal - computed) > TOLERANCE:
        return (f"{quantity:,.0f} kg x {unit_price} = {computed:,.2f}, "
                f"but the PO says {subtotal:,.2f}")
    if subtotal is not None and tax is not None and total is not None:
        if abs((subtotal + tax) - total) > TOLERANCE:
            return f"subtotal {subtotal:,.0f} + tax {tax:,.0f} does not make the printed {total:,.0f}"
    return ""


def _month_of(raised: date) -> date:
    return date(raised.year, raised.month, 1)


def read_purchase_orders(path: Path | str = DEFAULT_FILE) -> list[dict]:
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        return [row for row in csv.DictReader(handle) if row.get("po_number")]


def _as_paid(row, fallback: float | None = None) -> float:
    """What this row was actually paid in, before any conversion the store applied."""
    native = getattr(row, "native_price", None)
    if native is not None:
        return float(native)
    return float(row.price) if fallback is None else float(fallback)


def _supersede_erp_postings(session, identity, month, observed, price_per_tonne,
                            paid: float | None = None, source: str | None = None) -> list[str]:
    """Remove the ledger postings that record this same purchase, and say which.

    Matched across identities, not on this one. A PO states its origin and Incoterm and the older
    postings may not, so they can sit on different identity rows. What makes it the same purchase is
    the product, the supplier, the market, the price and the timing; which row it landed on is
    exactly what this must see past.

    Runs on every import, not only when the order is new. The engine reloads the ERP extract at every
    start, which puts the superseded postings back; if this only ran for orders it had not seen
    before, the second start would leave each of those trades in the store twice.
    """
    window_end = datetime(
        month.year + (month.month - 1 + RECEIPT_WINDOW_MONTHS) // 12,
        (month.month - 1 + RECEIPT_WINDOW_MONTHS) % 12 + 1,
        1, tzinfo=timezone.utc)
    # Scoped by source and product family, not by grade or market geography. Those used to be the
    # discriminators, and they stopped working the moment a series moved lane: an order that is now
    # China ex-works with no grade will never match a posting still filed as Thailand DELIVERED
    # "TDI 80/20", and the trade lands in the store twice. The source is the real discriminator -
    # one buyer's own ledger - and the price has to agree as well.
    query = (select(PriceObservationModel)
             .join(MarketIdentityModel, MarketIdentityModel.id == PriceObservationModel.identity_id)
             .where(
                 MarketIdentityModel.product_family == identity.product_family,
                 PriceObservationModel.observation_date >= observed,
                 PriceObservationModel.observation_date <= window_end,
                 PriceObservationModel.evidence_id.like("history:%"),
             ))
    if source:
        query = query.where(PriceObservationModel.source_id == source)
    postings = session.scalars(query).all()
    target = price_per_tonne if paid is None else paid
    removed = []
    for posting in postings:
        if abs(_as_paid(posting) - target) > SAME_PRICE_TOLERANCE:
            continue
        removed.append(posting.observation_date.date().replace(day=1).isoformat())
        # Its conversions first: price_in_currency points at the posting, Postgres enforces that
        # key, and SQLite never did - the import failed on every start since the Supabase move.
        for conversion in session.scalars(select(PriceInCurrencyModel).where(
                PriceInCurrencyModel.observation_id == posting.id)):
            session.delete(conversion)
        session.delete(posting)
    # Flush the deletes before the next order looks, or two orders raised in the same month both
    # find the posting still sitting there and both report replacing it.
    session.flush()
    return removed


def import_purchase_orders(session_factory, path: Path | str = DEFAULT_FILE) -> dict:
    """Load signed POs. Idempotent by PO number; never replaces an ERP posting."""
    path = Path(path)
    if not path.exists():
        return {"status": "absent", "path": str(path), "inserted": 0}

    rows = read_purchase_orders(path)
    inserted = already = superseded = rejected = unconvertible = collapsed = 0
    notes: list[str] = []

    with session_factory() as session:
        _ensure_sources(session)
        rates = rate_table(session)
        for row in rows:
            try:
                raised = date.fromisoformat(row["po_date"])
                quantity = float(row["quantity_kg"])
                unit_price = float(row["unit_price_per_kg"])
            except (KeyError, TypeError, ValueError) as exc:
                rejected += 1
                notes.append(f"{row.get('po_number')}: unreadable ({exc})")
                continue
            if quantity <= 0 or unit_price <= 0:
                rejected += 1
                notes.append(f"{row['po_number']}: quantity or unit price is not positive")
                continue

            # The document has to agree with itself. Every PO prints quantity, unit price, subtotal,
            # tax and total, and those five numbers over-determine each other - which makes a typed
            # transcription checkable rather than trusted. The failure this catches is taking the
            # price from the gross total, which on a Thai PO is 7% high and looks entirely plausible
            # on a chart for as long as nobody opens the PDF again.
            problem = _self_consistent(row, quantity, unit_price)
            if problem:
                rejected += 1
                notes.append(f"{row['po_number']}: {problem}")
                continue

            month = _month_of(raised)
            observed = datetime(month.year, month.month, month.day, tzinfo=timezone.utc)
            raised_at = datetime(raised.year, raised.month, raised.day, tzinfo=timezone.utc)
            # A row whose chemical, unit or price kind the application cannot read is rejected
            # with the reason rather than filed under a guess - and it is written to the
            # quarantine, so an unknown value in HTC's OWN purchase book is as visible as one from
            # a publisher. These are our records: a refusal here is a question about them, and the
            # worst answer would be to drop the row quietly. Masterplan D, v1.2.12.
            try:
                identity = ensure_identity(session, _identity_for(row))
            except Refused as refused:
                quarantine.record(
                    session, source_id=str(row.get("source") or ""),
                    refusals=refused.refusals, reading=dict(row),
                    document=str(path), page=str(row.get("po_number") or ""),
                    seen_on=str(raised))
                rejected += 1
                notes.append(f"{row['po_number']}: {refused}")
                continue
            key = evidence_id(row["po_number"], row.get("item_code", ""))

            # A PO that states its origin and Incoterm is a different identity from an ERP posting
            # that states neither, and it should be - a China-origin CIF price and an unattributed
            # delivered price are not one series. But the store then holds two series for what a
            # reader will call "the BASF contract", and a split nobody was told about is how a chart
            # quietly loses half its history. So it is named here every time it happens, and someone
            # decides whether the older postings carry the same terms.
            sibling = session.scalar(
                select(MarketIdentityModel).where(
                    MarketIdentityModel.product_family == identity.product_family,
                    MarketIdentityModel.product_grade_or_chemistry == identity.product_grade_or_chemistry,
                    MarketIdentityModel.market_geography == identity.market_geography,
                    MarketIdentityModel.id != identity.id,
                ))
            if sibling is not None:
                note = (f"{row['po_number']}: {identity.product_grade_or_chemistry} now has two "
                        f"series - this PO on {identity.origin}/{identity.commercial_basis}, and "
                        f"older rows on {sibling.origin}/{sibling.commercial_basis}. Confirm whether "
                        "the earlier purchases carry the same origin and terms.")
                if note not in notes:
                    notes.append(note)

            # The ledger postings that record this same purchase are superseded by the order, and
            # this runs whether or not the order is already in the store: the ERP extract reloads on
            # every start, which puts them back.
            native = round(unit_price * 1000.0, 2)
            native_currency = str(row["currency"]).upper()
            kept_in = store_currency(row.get("source", ""), native_currency)
            price_per_tonne = native
            if kept_in != native_currency:
                found = cross_rate(rates, raised, native_currency, kept_in)
                if found is None:
                    unconvertible += 1
                    notes.append(f"{row['po_number']}: no {native_currency}/{kept_in} rate for "
                                 f"{raised.isoformat()}")
                    continue
                price_per_tonne = round(native * found[0], 2)

            for when in _supersede_erp_postings(session, identity, month, observed,
                                                price_per_tonne, paid=native,
                                                source=row["source"]):
                superseded += 1
                notes.append(f"{row['po_number']}: replaced the {when} ERP posting at the same price")

            stored = session.scalar(select(PriceObservationModel).where(
                PriceObservationModel.evidence_id == key))
            if stored is not None:
                # A revision supersedes; it does not sit beside what it replaced. The evidence id is
                # the PO number, so both versions land on the same row by construction - what has to
                # be decided is which price wins, and the answer is the newer document. Without this
                # a revised order imported second was skipped as "already present" and the store kept
                # the price that had been cancelled.
                revised = str(row.get("revision") or "").strip()
                new_price = price_per_tonne
                if revised and stored.price != new_price:
                    notes.append(f"{row['po_number']}: revision {revised} replaces "
                                 f"{stored.price:,.0f} with {new_price:,.0f} {row['currency']}/t")
                    stored.price = new_price
                    stored.native_price = native if kept_in != native_currency else None
                    stored.native_currency = (native_currency if kept_in != native_currency
                                              else None)
                    stored.publication_date = raised_at
                    stored.available_from = raised_at
                    stored.observation_date = observed
                    inserted += 0
                else:
                    already += 1
                continue

            # Two orders signed the same day at the same price are one price event.
            #
            # POM121070006 and POM121070007 were both raised on 13 July 2021 at 64.200 baht - 5,000
            # kg in drums and 50,000 kg in an ISO tank. Two documents, one negotiation. Left as two
            # rows they read as a price printed twice, which is what an average of the day's prints
            # would then count it as. Collapsed on Stefan's instruction, 14 September 2026.
            #
            # Same day AND same price: two orders on one day at different prices are two prices, and
            # the same price on two days is the market holding still. Both are kept.
            twin = session.scalar(
                select(PriceObservationModel).where(
                    PriceObservationModel.identity_id == identity.id,
                    PriceObservationModel.observation_date == observed,
                    PriceObservationModel.price == price_per_tonne,
                    PriceObservationModel.evidence_id.like("po:%"),
                ))
            if twin is not None:
                collapsed += 1
                notes.append(f"{row['po_number']}: same day and same price as {twin.evidence_id} - "
                             "one price event, not two")
                continue

            session.add(PriceObservationModel(
                identity_id=identity.id, source_id=row["source"], evidence_id=key,
                price=price_per_tonne, currency=kept_in,
                native_price=native if kept_in != native_currency else None,
                native_currency=native_currency if kept_in != native_currency else None,
                observation_date=observed,
                # The document's own date, on both. A PO is published the day it is signed - there is
                # no lag between agreeing a price and the buyer knowing it.
                publication_date=raised_at, available_from=raised_at, weight=1.0,
            ))
            # Flush so the next row can find it. The engine builds its sessions with autoflush off,
            # so without this a revision listed after the order it cancels looks up its own evidence
            # id, finds nothing pending, and lands beside the price it was meant to replace - the
            # store keeping 116.000 and 114.500 for one order. The tests used a default session and
            # flushed by accident, which is why this only showed up against the real store.
            session.flush()
            inserted += 1
        session.commit()

    return {"status": "ok", "path": str(path), "orders": len(rows), "inserted": inserted,
            "already_present": already, "superseded_erp_postings": superseded,
            "unconvertible": unconvertible, "collapsed_same_day_prints": collapsed,
            # Kept so a caller written against the old contract reads a number rather than a
            # KeyError. Nothing is deferred any more, so it is always zero.
            "deferred_to_erp": 0, "rejected": rejected,
            "notes": notes[:20]}


def coverage(session_factory) -> list[dict]:
    """One line per PO in the store, so a price on a screen can be traced to a signed document."""
    with session_factory() as session:
        rows = session.execute(
            select(MarketIdentityModel, PriceObservationModel)
            .join(PriceObservationModel, PriceObservationModel.identity_id == MarketIdentityModel.id)
            .where(PriceObservationModel.evidence_id.like("po:%"))
            .order_by(PriceObservationModel.observation_date)
        ).all()
    return [
        {"po_number": observation.evidence_id.split(":")[1],
         "month": observation.observation_date.date().isoformat(),
         "raised": observation.publication_date.date().isoformat() if observation.publication_date else None,
         "product": identity.product_grade_or_chemistry,
         "geography": identity.market_geography,
         "price_per_tonne": observation.price, "currency": identity.currency}
        for identity, observation in rows
    ]
