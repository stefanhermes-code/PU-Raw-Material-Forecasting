from __future__ import annotations

import os
import secrets
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import available_timezones

import pandas as pd
try:
    import streamlit as st
except ModuleNotFoundError:  # Allows non-UI verification in environments without Streamlit.
    st = None


from raw_material_forecasting.platform_admin.models import (
    CommercialPackage,
    Company,
    CompanyEntitlement,
    DeliveryProfile,
    EntitlementScope,
    Recipient,
    TemporaryEntitlement,
    UserAccess,
    scope_to_dict,
)
from raw_material_forecasting.platform_admin.records import (
    RecordKind,
    cell_list,
    pick_to_edit,
    read_date,
    read_flag,
    read_list,
    read_text,
    reason_fields,
    record_tabs,
    required,
    safe_action,
    scope_cells,
    scope_from_cells,
)
from raw_material_forecasting.platform_admin.repository import PlatformAdminRepository
from raw_material_forecasting.release import restart_message, restart_required, running_version
from raw_material_forecasting.reporting_delivery import (
    DataExportService,
    DeliveryAttemptRepository,
    DeliveryExecutionService,
    LocalSecureLocationAdapter,
    OutputGenerationRepository,
)
from raw_material_forecasting.scheduled_delivery import (
    DeliveryScheduleRepository,
    DeliverySchedulerService,
    NoReleasedRunProvider,
)
from raw_material_forecasting.platform_admin.auth import (
    MINIMUM_PASSWORD_LENGTH,
    PLATFORM_ADMIN_EMAIL,
    login_enabled,
)
from raw_material_forecasting.platform_store import SQLITE_FALLBACK, platform_store_engine
from raw_material_forecasting.platform_admin.service import (
    ConfigurationError,
    EntitlementViolation,
    PlatformAdminError,
    PlatformAdminService,
    RecipientDomainViolation,
)

DB_PATH = SQLITE_FALLBACK   # kept for the tests and tools that name it; the live store is DATABASE_URL
FEATURE_OPTIONS = [
    "current_market",
    "market_intelligence",
    "forecast",
    "reports",
    "scheduled_delivery",
]
# Polyol is kept as the group token so entitlements sold before the split keep working; the two
# chains are separately sellable because they have separate routes and separate feedstocks.
CHEMICAL_OPTIONS = [
    "TDI", "MDI", "PMDI", "Monomeric MDI",
    "Polyol", "Polyether Polyol", "HR Polyol", "Graft Polyol", "PHD Polyol",
    "PIPA Polyol", "Visco Polyol", "Polyester Polyol",
    "Fatty Acid Ester Polyol", "*",
]
GEOGRAPHY_OPTIONS = [
    "China", "Southeast Asia", "South Asia", "South Korea", "Europe",
    "North America", "Middle East", "Global", "*",
]
def market_selection_ui_incoterms():
    """The delivery terms the application offers, in the selectors' own order.

    Imported lazily and by name rather than at the top: `platform_admin.ui` is imported by `app.py`
    before `market_selection_ui` finishes loading, and a top-level import of it here is a cycle.
    """
    from raw_material_forecasting.market_basis import DELIVERY_TERMS, INCOTERMS

    ordered = ("EXW", "FCA", "FOB", "CFR", "CIF", "DAP", "DDP", "DPU", "CPT", "CIP", "FAS", "FD")
    return [code for code in ordered if code in DELIVERY_TERMS or code in INCOTERMS]


# What an entitlement may name. The third of the three lists that answered "which delivery terms
# does this application know" and disagreed (masterplan F): this one had no FD, so an entitlement
# could not be written for the ICIS Western Europe markets at all. Derived from the one list now,
# with `*` for "any", which is an entitlement's own word and not a delivery term.
INCOTERM_OPTIONS = [*(code for code in market_selection_ui_incoterms()), "*"]
PACKAGING_OPTIONS = ["Not specified", "Drum", "IBC", "ISO Tank", "Flexibag", "Bulk Road Tank", "*"]
REPORT_FORMAT_OPTIONS = ["CSV", "XLSX", "PDF", "*"]
FREQUENCY_OPTIONS = ["Daily", "Weekly", "Monthly", "*"]
CHANNEL_OPTIONS = ["Email", "Secure location", "*"]
ROLE_OPTIONS = ["VIEWER", "COMPANY_ADMIN"]
OVERVIEW = "Dashboard"

# The commercial side: who the customers are, what they bought, who receives what, and the record
# of every change. Customer Reports is here rather than with the market data on Stefan's decision,
# 30 October 2026 - a report is a thing a customer receives, whatever it was built from.
CUSTOMER_PAGES = [
    "Customers",
    "Subscriptions",
    "People",
    "Deliveries",
    "Customer Reports",
    "Change history",
]
# The product: what we hold, what we cannot yet explain, how the chemicals move together, and the
# forecasts made from all of it.
MARKET_PAGES = [
    "Prices",
    "Open questions",
    "Relationships",
    "Forecasts",
]
# Every destination, in the order the bar offers them. Kept under its old name because several
# tests and the entitlement rules read it.
NAVIGATION = [OVERVIEW, *CUSTOMER_PAGES, *MARKET_PAGES]
# No "Close" in any of these. Closing the application is the button at the right-hand end of the
# bar, beside "Return to previous page": the launcher starts the engine, the collection run and
# delivery worker detached, so closing the console window leaves all three running and holding the
# store (Stefan, 26 September 2026). It is not a destination, so it is not among the destinations.

ACCESS_NAVIGATION = ["Package Templates", "Permanent Entitlements", "Temporary Entitlements"]
PEOPLE_NAVIGATION = ["Users", "Recipients & Domains"]
# Delivery was six blocks stacked on one page: profiles, the schedule, the schedule monitor, what
# had been generated, the send control and what had been sent. Set it up, say when, send it, see
# what happened.
DELIVERY_NAVIGATION = ["Profiles", "Schedule", "Send", "History"]
CUSTOMER_REPORT_NAVIGATION = ["Period report", "Landed price calculator"]
PRICE_NAVIGATION = ["What we hold", "Bring data in"]
RELATIONSHIP_NAVIGATION = ["The study", "The working behind it"]
FORECAST_NAVIGATION = ["What is available", "How methods are chosen", "How accurate they have been"]
INTELLIGENCE_NAVIGATION = [
    # Purchase History and Producer Signals were removed on 19 September 2026.
    #
    # Purchase History was titled "HTC's own purchase prices" and drew "what HTC paid BASF against
    # what it paid Wanhua". HTC buys nothing. Every transaction price in the store belongs to FNAG
    # and FoamPartner - two foam producers whose procurement history was merged in - and it ends in
    # 2017, so the page drew nine-year-old lines from other companies against a 2026 market with a
    # forecast band on the end.
    #
    # Producer Signals was the intake view for an automated extractor that has produced nothing
    # since it was written: both data/producer_signals.json and its rejected file are empty lists,
    # so not one candidate was ever even considered. The register that works - Market Signals, on
    # Market Intelligence - is maintained by hand and never touched this feed.
    #
    # The extractor code and its tests stay in the repo. Removing a page does not remove a
    # capability, and if the intake is ever wired to live sources the view can come back.
    #
    # Relationship method was added on 19 September 2026. The customer page had grown five sections
    # counting four different populations, and the three that were method evidence rather than
    # results - the series behind each number, the step-by-step chain walk with its lag check, and
    # the weekly lane's fill - moved here, together with the upstream reach control that drove half
    # the inconsistency.
    # Import, added 6 October 2026. Stefan: "I am getting sometimes documents/images with pricing
    # that are interesting to have." It is first in this list because it is the one area here that
    # takes something in rather than showing what is already held.
    "Import",
    # Reports, added 8 October 2026. Darren Millar wants a monthly or quarterly report on the raw
    # material movements to show his own customers, and Neveon wants one too. It sits beside Import
    # because the two are the ways something crosses the boundary of the application: one takes a
    # document in, the other sends one out.
    "Reports",
    "Data & Sources",
    # Investigation, added 19 October 2026. An observation the engine cannot explain is a case
    # with a chain of questions against it rather than a line in a rejection file. Stefan:
    # "I dont know how you are going to build that in the application but that is VITAL."
    #
    # It sits after Data & Sources because that page says what we hold and this one says what we
    # do not yet understand about it.
    "Investigation",
    "Chemical Relationships",
    "Relationship method",
    "Forecast Performance",
    # Forecast Method Management, added 20 October 2026 (Adaptive Forecast Method Selection, WP5):
    # which method makes each forecast, why, overrides and Challenger approval.
    "Forecast Method Management",
    # Forecasts Available, added 30 October 2026. Stefan: "What I really would like to have is a
    # very simple table showing which forecasts are now available, downloadable as csv, not too
    # complicated." It sits beside Forecast Method Management because that page is where a method
    # is changed and this one is where you see what the changes add up to.
    "Forecasts Available",
    "Delivered Price",
]


# A visible name on the left, the key `app.py` registers its renderer under on the right. The keys
# are the application's own wiring and do not move when a label does; `INTELLIGENCE_NAVIGATION`
# above is still the list of them, which is what the tests that guard reachability read.
INTERNAL_PAGES = {
    "What we hold": "Data & Sources",
    "Bring data in": "Import",
    "Open questions": "Investigation",
    "The study": "Chemical Relationships",
    "The working behind it": "Relationship method",
    "What is available": "Forecasts Available",
    "How methods are chosen": "Forecast Method Management",
    "How accurate they have been": "Forecast Performance",
    "Period report": "Reports",
    "Landed price calculator": "Delivered Price",
}


def _chose(mine: str, others: tuple[str, ...]) -> None:
    """One page is open at a time, whichever group it was chosen from.

    Two controls each hold their own value, so without this both would show something selected and
    the bar would claim two open pages. The chosen one wins and the other is cleared. Clicking the
    page already open deselects it in Streamlit's own handling, which would leave the admin on no
    page at all; that reads as "stay here".
    """
    chosen = st.session_state.get(mine)
    if chosen is None:
        # A group the open page does not belong to holds nothing. Nothing was chosen here.
        st.session_state[mine] = st.session_state.get("platform_admin_page")
        return
    st.session_state["platform_admin_page"] = chosen
    for key in others:
        st.session_state[key] = None


def _within(label: str, pages: list[str], key: str):
    """The pages inside one destination. A radio, like every other bar in the application.

    Not `st.tabs`: Streamlit runs the body of every tab on every render, so Forecasts built from
    tabs would draw the availability table, the method management page and the accuracy study each
    time anybody opened it, and make three engine calls to show one of them.
    """
    return st.radio(label, pages, horizontal=True, label_visibility="collapsed", key=key)


def build_service() -> PlatformAdminService:
    # v0.5.42: one store. The engine comes from platform_store, which follows DATABASE_URL.
    return PlatformAdminService(PlatformAdminRepository(platform_store_engine()))


def _as_utc(d: date, t: time = time.min) -> datetime:
    return datetime.combine(d, t, tzinfo=timezone.utc)


def _scope_editor(prefix: str, default: EntitlementScope | None = None) -> EntitlementScope:
    default = default or EntitlementScope()
    app_access = st.checkbox("Application access", value=default.application_access, key=f"{prefix}_app")
    features = st.multiselect("Functions", FEATURE_OPTIONS, default=[item for item in default.features if item in FEATURE_OPTIONS], key=f"{prefix}_features")
    chemicals = st.multiselect("Chemicals", CHEMICAL_OPTIONS, default=list(default.chemicals), key=f"{prefix}_chemicals")
    geographies = st.multiselect("Geographies", GEOGRAPHY_OPTIONS, default=list(default.geographies), key=f"{prefix}_geographies")
    incoterms = st.multiselect("Incoterms", INCOTERM_OPTIONS, default=list(default.incoterms), key=f"{prefix}_incoterms")
    packaging = st.multiselect("Packaging", PACKAGING_OPTIONS, default=list(default.packaging), key=f"{prefix}_packaging")
    horizon = st.number_input("Forecast horizon (months)", min_value=0, max_value=36, value=int(default.forecast_horizon_months), key=f"{prefix}_horizon")
    report_formats = st.multiselect("Report/data formats", REPORT_FORMAT_OPTIONS, default=list(default.report_formats), key=f"{prefix}_formats")
    frequencies = st.multiselect("Delivery frequencies", FREQUENCY_OPTIONS, default=list(default.delivery_frequencies), key=f"{prefix}_freqs")
    channels = st.multiselect("Delivery channels", CHANNEL_OPTIONS, default=list(default.delivery_channels), key=f"{prefix}_channels")
    return EntitlementScope(
        application_access=app_access,
        features=frozenset(features),
        chemicals=frozenset(chemicals),
        geographies=frozenset(geographies),
        incoterms=frozenset(incoterms),
        packaging=frozenset(packaging),
        forecast_horizon_months=int(horizon),
        report_formats=frozenset(report_formats),
        delivery_frequencies=frozenset(frequencies),
        delivery_channels=frozenset(channels),
    )


# The three small parts the record pages share live in `records`, beside the tab structure they
# belong to. Named here as well so the seven pages below read as they always did.
_reason_fields = reason_fields
_safe_action = safe_action
_pick_to_edit = pick_to_edit


def _company_map(service: PlatformAdminService) -> dict[str, Company]:
    return {c.company_id: c for c in service.repo.list_companies()}


def _select_company(service: PlatformAdminService, key: str) -> str | None:
    companies = _company_map(service)
    if not companies:
        st.info("Create a company first.")
        return None
    ids = list(companies)
    return st.selectbox("Company", ids, format_func=lambda cid: f"{companies[cid].name} ({cid})", key=key)


# How long before a contract or a grant running out is worth putting on the front page. A contract
# is a conversation with notice; a temporary grant simply stops, and a customer who loses a page
# mid-month without warning is the thing this is here to prevent.
CONTRACT_NOTICE_DAYS = 30
GRANT_NOTICE_DAYS = 7


def _commercial_decisions(service: PlatformAdminService, now: datetime) -> list[dict[str, str]]:
    """What on the commercial side is waiting for somebody, with the date it is waiting for.

    Only things a person has to act on. A contract that runs for another year and a grant that
    expired last month are both quiet: one needs nothing and the other has already happened.
    """
    waiting: list[dict[str, str]] = []
    for company in service.repo.list_companies():
        ends = company.contract_effective_to
        if ends is not None:
            days = (ends - now.date()).days if isinstance(ends, date) else None
            if days is not None and 0 <= days <= CONTRACT_NOTICE_DAYS:
                waiting.append({
                    "What": "Contract ends",
                    "Who": company.name,
                    "When": ends.isoformat(),
                    "In": f"{days} day{'' if days == 1 else 's'}",
                })
        for grant in service.repo.list_temporary_entitlements(company.company_id):
            if not grant.is_active(now):
                continue
            days = (grant.effective_to - now).days
            if 0 <= days <= GRANT_NOTICE_DAYS:
                waiting.append({
                    "What": f"Temporary grant ends ({grant.reason_type})",
                    "Who": company.name,
                    "When": grant.effective_to.date().isoformat(),
                    "In": f"{days} day{'' if days == 1 else 's'}",
                })
    return waiting


def _failed_deliveries(service: PlatformAdminService) -> list[dict[str, str]]:
    """Sends that did not arrive. A customer is not told; this page is the only place it shows."""
    try:
        records = DeliveryAttemptRepository(service.repo.engine).list_records(None)
    except Exception:          # the table is created on first use; an empty store is not a fault
        return []
    return [{
        "What": "Delivery failed",
        "Who": record.company_id,
        "When": str(record.attempted_at)[:16],
        "In": record.error_message or record.status,
    } for record in records if str(record.status).upper() not in {"DELIVERED", "SUCCESS", "SENT"}]


def _render_dashboard(service: PlatformAdminService,
                      internal_renderers: dict[str, object] | None = None) -> None:
    st.subheader("Dashboard")
    renderers = internal_renderers or {}

    # Did the data arrive, and what state are the forecasts in. Both are about the engine's store,
    # which this module does not reach into, so `app.py` draws them.
    market = renderers.get("Dashboard: market data")
    if callable(market):
        market()
    else:
        st.info("The market-data engine is not connected in this runtime.")

    st.markdown("### Anything needing a decision")
    now = datetime.now(timezone.utc)
    waiting = _commercial_decisions(service, now) + _failed_deliveries(service)

    questions = renderers.get("Open questions waiting")
    open_questions = None
    if callable(questions):
        try:
            open_questions = int(questions())
        except Exception:      # a number nobody can fetch is not a reason to lose the rest
            open_questions = None
    if open_questions:
        waiting.insert(0, {
            "What": "Open questions on the data",
            "Who": "Market data",
            "When": "-",
            "In": f"{open_questions} waiting to be worked",
        })

    if not service.repo.list_companies():
        st.info("No customers are configured yet. Create the first one to start defining what "
                "they may see and what they are sent.")
        if st.button("Create the first customer", type="primary", key="admin_create_first_company"):
            st.session_state["platform_admin_page"] = "Customers"
            st.rerun()
        return
    if not waiting:
        st.success("Nothing is waiting. No contract or grant runs out in the next month, every "
                   "delivery arrived, and no question on the data is open.")
        return
    st.dataframe(pd.DataFrame(waiting), width="stretch", hide_index=True)


STATUS_OPTIONS = ["ACTIVE", "TRIAL", "SUSPENDED", "CLOSED"]


def _company_kind(service: PlatformAdminService) -> RecordKind:
    return RecordKind(
        noun="company",
        id_of=lambda c: c.company_id,
        name_of=lambda c: f"{c.name} ({c.company_id})",
        to_row=lambda c: {
            "company_id": c.company_id, "name": c.name, "status": c.status,
            "contract_effective_from": c.contract_effective_from.isoformat(),
            "contract_effective_to": (c.contract_effective_to.isoformat()
                                      if c.contract_effective_to else ""),
        },
        from_row=lambda row: Company(
            required(row.get("company_id"), "company_id"),
            required(row.get("name"), "name"),
            (read_text(row.get("status")) or STATUS_OPTIONS[0]).upper(),
            read_date(row.get("contract_effective_from")) or date.today(),
            read_date(row.get("contract_effective_to")),
        ),
        save=lambda record, actor, reason: service.create_company(
            record, actor=actor, reason=reason),
    )


def _render_companies(service: PlatformAdminService) -> None:
    st.subheader("Customers")
    companies = service.repo.list_companies()

    def form(editing: Company | None) -> None:
        # Rebuilt when the choice changes - the key carries the id - so the fields show what is
        # stored rather than what the last render left behind.
        mark = editing.company_id if editing else "new"
        with st.form(f"company_form_{mark}"):
            company_id = st.text_input(
                "Company ID / code", value=editing.company_id if editing else "", placeholder="e.g. sf",
                disabled=editing is not None, key=f"company_id_{mark}",
                help="Fixed once the company exists. Choose it above to change its details.")
            name = st.text_input("Company name", value=editing.name if editing else "",
                                 key=f"company_name_{mark}")
            status = st.selectbox(
                "Status", STATUS_OPTIONS, key=f"company_status_{mark}",
                index=STATUS_OPTIONS.index(editing.status) if editing and editing.status in STATUS_OPTIONS else 0)
            effective_from = st.date_input(
                "Contract effective from", key=f"company_from_{mark}",
                value=editing.contract_effective_from if editing else date.today())
            has_end = st.checkbox(
                "Contract has end date", key=f"company_has_end_{mark}",
                value=bool(editing and editing.contract_effective_to))
            effective_to = st.date_input(
                "Contract effective to", key=f"company_to_{mark}",
                value=(editing.contract_effective_to if editing and editing.contract_effective_to
                       else date.today() + timedelta(days=365)),
                disabled=not has_end)
            actor, reason = _reason_fields(f"company_{mark}")
            submitted = st.form_submit_button(
                "Save changes" if editing else "Create company", type="primary")
            if submitted:
                _safe_action(lambda: service.create_company(
                    Company((editing.company_id if editing else company_id).strip(), name.strip(),
                            status, effective_from, effective_to if has_end else None),
                    actor=actor, reason=reason,
                ))

    record_tabs(_company_kind(service), companies, form,
                deactivate=service.deactivate, key="company")
    if companies:
        st.dataframe(pd.DataFrame([c.__dict__ for c in companies]), width="stretch", hide_index=True)


def _package_kind(service: PlatformAdminService) -> RecordKind:
    return RecordKind(
        noun="package",
        id_of=lambda p: p.package_id,
        name_of=lambda p: f"{p.name} ({p.package_id})",
        to_row=lambda p: {"package_id": p.package_id, "name": p.name, "active": p.active,
                          **scope_cells(p.scope)},
        from_row=lambda row: CommercialPackage(
            required(row.get("package_id"), "package_id"),
            required(row.get("name"), "name"),
            scope_from_cells(row),
            read_flag(row.get("active")),
        ),
        save=lambda record, actor, reason: service.save_package(
            record, actor=actor, reason=reason),
    )


def _render_packages(service: PlatformAdminService) -> None:
    st.subheader("Package Templates")
    st.caption("Templates seed a company entitlement. They are never the contractual truth after creation.")
    packages = service.repo.list_packages()

    def form(editing: CommercialPackage | None) -> None:
        mark = editing.package_id if editing else "new"
        with st.form(f"package_form_{mark}"):
            package_id = st.text_input(
                "Package ID", value=editing.package_id if editing else "", disabled=editing is not None,
                key=f"package_id_{mark}",
                help="Fixed once the template exists. Choose it above to change its contents.")
            name = st.text_input("Package name", value=editing.name if editing else "",
                                 key=f"package_name_{mark}")
            active = st.checkbox("Active", value=editing.active if editing else True,
                                 key=f"package_active_{mark}")
            scope = _scope_editor(f"package_{mark}", editing.scope if editing else None)
            actor, reason = _reason_fields(f"package_{mark}")
            submitted = st.form_submit_button(
                "Save changes" if editing else "Create template", type="primary")
            if submitted:
                _safe_action(lambda: service.save_package(
                    CommercialPackage((editing.package_id if editing else package_id).strip(),
                                      name.strip(), scope, active), actor=actor, reason=reason
                ))

    record_tabs(_package_kind(service), packages, form,
                deactivate=service.deactivate, key="package")
    if packages:
        st.dataframe(pd.DataFrame([
            {"Package ID": p.package_id, "Name": p.name, "Active": p.active, **scope_to_dict(p.scope)} for p in packages
        ]), width="stretch", hide_index=True)


def _permanent_kind(service: PlatformAdminService, company_id: str) -> RecordKind:
    return RecordKind(
        noun="entitlement",
        id_of=lambda e: e.entitlement_id,
        name_of=lambda e: f"{e.entitlement_id} (from {e.effective_from.date()})",
        to_row=lambda e: {
            "entitlement_id": e.entitlement_id, "package_id": e.package_id or "",
            "effective_from": e.effective_from.date().isoformat(),
            "effective_to": e.effective_to.date().isoformat() if e.effective_to else "",
            **scope_cells(e.scope)},
        from_row=lambda row: CompanyEntitlement(
            required(row.get("entitlement_id"), "entitlement_id"), company_id,
            scope_from_cells(row),
            _as_utc(read_date(row.get("effective_from")) or date.today()),
            _as_utc(read_date(row.get("effective_to"))) if read_date(row.get("effective_to")) else None,
            read_text(row.get("package_id")) or None,
        ),
        save=lambda record, actor, reason: service.set_permanent_entitlement(
            record, actor=actor, reason=reason),
    )


def _render_permanent(service: PlatformAdminService) -> None:
    st.subheader("Permanent Entitlements")
    st.caption("A company has one contractual entitlement, not a list of them, so Create makes "
               "that one and Edit holds it.")
    company_id = _select_company(service, "perm_company")
    if not company_id:
        return
    current = service.repo.get_company_entitlement(company_id)
    packages = service.repo.list_packages()

    def form(editing: CompanyEntitlement | None) -> None:
        mark = editing.entitlement_id if editing else "new"
        if editing is None and packages:
            st.markdown("**Seed from package template**")
            with st.form("seed_package_form"):
                package_id = st.selectbox("Package template", [p.package_id for p in packages], format_func=lambda pid: next(p.name for p in packages if p.package_id == pid))
                entitlement_id = st.text_input("Entitlement ID", value=current.entitlement_id if current else f"perm-{company_id}")
                effective_from = st.date_input("Effective from", value=date.today(), key="seed_eff")
                actor, reason = _reason_fields("seed")
                if st.form_submit_button("Create/replace from template"):
                    _safe_action(lambda: service.create_entitlement_from_package(
                        company_id=company_id, package_id=package_id, entitlement_id=entitlement_id.strip(),
                        effective_from=_as_utc(effective_from), actor=actor, reason=reason,
                    ))
            st.markdown("**Or set the contractual entitlement directly**")
        with st.form(f"permanent_form_{mark}"):
            entitlement_id = st.text_input("Entitlement ID", key=f"perm_id_{mark}", value=editing.entitlement_id if editing else f"perm-{company_id}")
            package_ref = st.text_input("Originating package ID (optional)", key=f"perm_pkg_{mark}", value=(editing.package_id or "") if editing else "")
            effective_from = st.date_input("Effective from", key=f"perm_from_{mark}", value=editing.effective_from.date() if editing else date.today())
            has_end = st.checkbox("Entitlement has end date", key=f"perm_has_end_{mark}", value=editing.effective_to is not None if editing else False)
            effective_to = st.date_input("Effective to", key=f"perm_to_{mark}", value=editing.effective_to.date() if editing and editing.effective_to else date.today() + timedelta(days=365), disabled=not has_end)
            scope = _scope_editor(f"permanent_{mark}", editing.scope if editing else None)
            actor, reason = _reason_fields(f"permanent_{mark}")
            if st.form_submit_button("Save contractual entitlement", type="primary"):
                _safe_action(lambda: service.set_permanent_entitlement(
                    CompanyEntitlement(
                        entitlement_id.strip(), company_id, scope, _as_utc(effective_from),
                        _as_utc(effective_to) if has_end else None, package_ref.strip() or None,
                    ), actor=actor, reason=reason,
                ))

    record_tabs(_permanent_kind(service, company_id), [current] if current else [], form,
                deactivate=service.deactivate, key=f"permanent_{company_id}")
    current = service.repo.get_company_entitlement(company_id)
    if current:
        st.json(scope_to_dict(current.scope))


TEMPORARY_REASONS = ["Trial", "Evaluation", "Commercial Exception"]


def _temporary_kind(service: PlatformAdminService, company_id: str) -> RecordKind:
    return RecordKind(
        noun="grant",
        id_of=lambda g: g.entitlement_id,
        name_of=lambda g: f"{g.entitlement_id} ({g.reason_type}, to {g.effective_to.date()})",
        to_row=lambda g: {
            "entitlement_id": g.entitlement_id, "reason_type": g.reason_type, "reason": g.reason,
            "effective_from": g.effective_from.date().isoformat(),
            "effective_to": g.effective_to.date().isoformat(),
            "approved_by": g.approved_by, **scope_cells(g.scope)},
        from_row=lambda row: TemporaryEntitlement(
            required(row.get("entitlement_id"), "entitlement_id"), company_id,
            scope_from_cells(row),
            _as_utc(read_date(row.get("effective_from")) or date.today()),
            _as_utc(read_date(row.get("effective_to")) or (date.today() + timedelta(days=14))),
            read_text(row.get("reason_type")) or TEMPORARY_REASONS[0],
            required(row.get("reason"), "reason"),
            read_text(row.get("approved_by")) or "HTC Platform Admin",
            read_text(row.get("approved_by")) or "HTC Platform Admin",
        ),
        save=lambda record, actor, reason: service.grant_temporary_entitlement(
            record, actor=actor, reason=reason),
    )


def _render_temporary(service: PlatformAdminService) -> None:
    st.subheader("Temporary Entitlements")
    company_id = _select_company(service, "temp_company")
    if not company_id:
        return
    grants = service.repo.list_temporary_entitlements(company_id)

    def form(editing: TemporaryEntitlement | None) -> None:
        mark = editing.entitlement_id if editing else "new"
        with st.form(f"temporary_form_{mark}"):
            entitlement_id = st.text_input("Temporary entitlement ID", key=f"temp_id_{mark}", value=editing.entitlement_id if editing else f"temp-{company_id}-{uuid4().hex[:8]}")
            reason_type = st.selectbox("Reason type", TEMPORARY_REASONS, key=f"temp_type_{mark}",
                                       index=TEMPORARY_REASONS.index(editing.reason_type) if editing and editing.reason_type in TEMPORARY_REASONS else 0)
            business_reason = st.text_input("Business reason", key=f"temp_why_{mark}", value=editing.reason if editing else "")
            effective_from = st.date_input("Effective from", key=f"temp_from_{mark}", value=editing.effective_from.date() if editing else date.today())
            effective_to = st.date_input("Expires on", key=f"temp_to_{mark}", value=editing.effective_to.date() if editing else date.today() + timedelta(days=14))
            approved_by = st.text_input("Approved by", key=f"temp_by_{mark}", value=editing.approved_by if editing else "HTC Platform Admin")
            scope = _scope_editor(f"temporary_{mark}", editing.scope if editing else None)
            actor, audit_reason = _reason_fields(f"temporary_{mark}")
            if st.form_submit_button("Save changes" if editing else "Grant temporary entitlement", type="primary"):
                _safe_action(lambda: service.grant_temporary_entitlement(
                    TemporaryEntitlement(
                        entitlement_id.strip(), company_id, scope, _as_utc(effective_from), _as_utc(effective_to),
                        reason_type, business_reason.strip(), actor.strip(), approved_by.strip(),
                    ), actor=actor, reason=audit_reason,
                ))

    record_tabs(_temporary_kind(service, company_id), grants, form,
                deactivate=service.deactivate, key=f"temporary_{company_id}")
    if grants:
        now = datetime.now(timezone.utc)
        st.dataframe(pd.DataFrame([
            {
                "ID": g.entitlement_id, "Type": g.reason_type, "Reason": g.reason,
                "From": g.effective_from, "To": g.effective_to, "Active now": g.is_active(now),
                "Scope": scope_to_dict(g.scope),
            } for g in grants
        ]), width="stretch", hide_index=True)


def _user_kind(service: PlatformAdminService, company_id: str) -> RecordKind:
    return RecordKind(
        noun="user",
        id_of=lambda u: u.user_id,
        name_of=lambda u: f"{u.email} ({u.role})",
        to_row=lambda u: {"user_id": u.user_id, "email": u.email, "role": u.role,
                          "active": u.active, **scope_cells(u.scope)},
        from_row=lambda row: UserAccess(
            required(row.get("user_id"), "user_id"), company_id,
            required(row.get("email"), "email"),
            (read_text(row.get("role")) or ROLE_OPTIONS[0]).upper(),
            scope_from_cells(row), read_flag(row.get("active")),
        ),
        save=lambda record, actor, reason: service.create_user(
            record, actor=actor, reason=reason),
    )


def _render_users(service: PlatformAdminService) -> None:
    st.subheader("Users")
    company_id = _select_company(service, "user_company")
    if not company_id:
        return
    users = service.repo.list_users(company_id)

    def form(editing: UserAccess | None) -> None:
        mark = editing.user_id if editing else "new"
        with st.form(f"user_form_{mark}"):
            user_id = st.text_input(
                "User ID", value=editing.user_id if editing else f"user-{uuid4().hex[:8]}",
                disabled=editing is not None, key=f"user_id_{mark}")
            email = st.text_input("User email", value=editing.email if editing else "",
                                  key=f"user_email_{mark}")
            role = st.selectbox(
                "Role", ROLE_OPTIONS, key=f"user_role_{mark}",
                index=ROLE_OPTIONS.index(editing.role) if editing and editing.role in ROLE_OPTIONS else 0)
            active = st.checkbox("Active", value=editing.active if editing else True,
                                 key=f"user_active_{mark}")
            scope = _scope_editor(f"user_{mark}", editing.scope if editing else None)
            actor, reason = _reason_fields(f"user_{mark}")
            if st.form_submit_button("Save changes" if editing else "Create user", type="primary"):
                _safe_action(lambda: service.create_user(
                    UserAccess((editing.user_id if editing else user_id).strip(), company_id,
                               email.strip(), role, scope, active), actor=actor, reason=reason
                ))

    record_tabs(_user_kind(service, company_id), users, form,
                deactivate=service.deactivate, key=f"user_{company_id}")
    if users:
        st.dataframe(pd.DataFrame([
            {"User ID": u.user_id, "Email": u.email, "Role": u.role, "Active": u.active,
             "Password": _password_state(service, u.email), "Scope": scope_to_dict(u.scope)}
            for u in users
        ]), width="stretch", hide_index=True)
        _render_user_passwords(service, users)


def _password_state(service: PlatformAdminService, email: str) -> str:
    """What this account's sign-in looks like, in the user list."""
    credential = service.repo.get_user_credential(email)
    if credential is None:
        return "Not handed out"
    if credential.must_change:
        return "Handed out, not yet replaced"
    return "Chosen by the user"


def _suggested_password() -> str:
    """A password worth handing over: unguessable, and sayable down a telephone line.

    Three words and a number beat a random string here, because this one gets read out or typed
    into a message and then typed again at the other end. It stops guarding the account the moment
    the customer signs in - `set_user_password` marks it as still to be replaced - so its whole
    job is to survive one trip and be entered correctly.
    """
    words = ("Harbour", "Reactor", "Monsoon", "Lattice", "Foundry", "Quarry", "Pigment",
             "Granite", "Compass", "Kiln", "Meridian", "Tanker", "Bellows", "Cinder")
    picked = [secrets.choice(words) for _ in range(3)]
    return "-".join(picked) + "-" + str(secrets.randbelow(90) + 10) + "!"


def _render_user_passwords(service: PlatformAdminService, users) -> None:
    """Handing a customer their first password, and resetting one they have lost.

    Stefan sets it here and sends it on; the application makes them replace it before it opens
    (see `_render_password_change` in app.py). HTC never holds a customer's own password: what is
    stored is a PBKDF2 digest, and nothing on this screen can read one back.
    """
    openable = [u for u in users if u.active]
    if not openable:
        return
    with st.expander("Sign-in passwords"):
        by_label = {f"{u.email} ({u.role})": u for u in openable}
        label = st.selectbox("User", sorted(by_label), key="user_password_pick")
        user = by_label[label]
        st.caption(f"Sign-in for {user.email}: {_password_state(service, user.email)}")

        suggestion_key = f"user_password_suggestion_{user.user_id}"
        if st.button("Suggest a password", key=f"suggest_{user.user_id}"):
            st.session_state[suggestion_key] = _suggested_password()
        suggestion = st.session_state.get(suggestion_key, "")
        if suggestion:
            st.code(suggestion, language=None)
            st.caption("Copy this into the form below and send it to the user. They replace it "
                       "the first time they sign in.")

        with st.form(f"user_password_form_{user.user_id}"):
            password = st.text_input(
                "Password to hand over", type="password",
                help=f"At least {MINIMUM_PASSWORD_LENGTH} characters, and three of: small "
                     "letter, capital, digit, punctuation.",
                key=f"user_password_{user.user_id}")
            confirm = st.text_input("Confirm", type="password",
                                    key=f"user_password_confirm_{user.user_id}")
            actor, reason = _reason_fields(f"user_password_{user.user_id}")
            if st.form_submit_button("Set this password", type="primary"):
                if password != confirm:
                    st.error("The two passwords are not the same.")
                else:
                    _safe_action(lambda: service.set_user_password(
                        user.email, password, actor=actor, reason=reason))


def _recipient_kind(service: PlatformAdminService, company_id: str) -> RecordKind:
    return RecordKind(
        noun="recipient",
        id_of=lambda r: r.recipient_id,
        name_of=lambda r: f"{r.name} <{r.email}>",
        to_row=lambda r: {"recipient_id": r.recipient_id, "name": r.name, "email": r.email,
                          "active": r.active, "external_exception": r.external_exception,
                          "exception_reason": r.exception_reason or ""},
        from_row=lambda row: Recipient(
            required(row.get("recipient_id"), "recipient_id"), company_id,
            required(row.get("email"), "email"),
            required(row.get("name"), "name"),
            read_flag(row.get("external_exception"), default=False),
            read_text(row.get("exception_reason")) or None,
            read_flag(row.get("active")),
        ),
        # The domain rule is the service's and the import does not go round it: an address outside
        # the company's approved domains is refused unless the sheet carries the exception and the
        # reason for it, exactly as the form demands.
        save=lambda record, actor, reason: service.add_recipient(
            record, actor=actor, reason=reason, allow_external=record.external_exception,
            exception_reason=record.exception_reason),
    )


def _render_recipients(service: PlatformAdminService) -> None:
    st.subheader("Recipients & Domains")
    company_id = _select_company(service, "recipient_company")
    if not company_id:
        return
    current_domains = sorted(service.repo.list_approved_domains(company_id))
    with st.form("domain_form"):
        domains_text = st.text_area("Approved company email domains", value="\n".join(current_domains), help="One domain per line. Do not include @.")
        actor, reason = _reason_fields("domains")
        if st.form_submit_button("Save approved domains"):
            domains = {line.strip().lower().lstrip("@") for line in domains_text.splitlines() if line.strip()}
            _safe_action(lambda: service.set_approved_domains(company_id, domains, actor=actor, reason=reason))
    recipients = service.repo.list_recipients(company_id)

    def form(editing: Recipient | None) -> None:
        mark = editing.recipient_id if editing else "new"
        with st.form(f"recipient_form_{mark}"):
            recipient_id = st.text_input(
                "Recipient ID", value=editing.recipient_id if editing else f"rec-{uuid4().hex[:8]}",
                disabled=editing is not None, key=f"recipient_id_{mark}")
            name = st.text_input("Recipient name", value=editing.name if editing else "",
                                 key=f"recipient_name_{mark}")
            email = st.text_input("Recipient email", value=editing.email if editing else "",
                                  key=f"recipient_email_{mark}")
            allow_external = st.checkbox(
                "HTC-approved external-domain exception", key=f"recipient_external_{mark}",
                value=bool(editing and editing.external_exception))
            exception_reason = st.text_input(
                "External exception reason", key=f"recipient_why_{mark}",
                value=(editing.exception_reason or "") if editing else "", disabled=not allow_external)
            actor, reason = _reason_fields(f"recipient_{mark}")
            if st.form_submit_button("Save changes" if editing else "Create recipient", type="primary"):
                _safe_action(lambda: service.add_recipient(
                    Recipient((editing.recipient_id if editing else recipient_id).strip(), company_id,
                              email.strip(), name.strip(),
                              active=editing.active if editing else True),
                    actor=actor, reason=reason, allow_external=allow_external,
                    exception_reason=exception_reason if allow_external else None,
                ))

    record_tabs(_recipient_kind(service, company_id), recipients, form,
                deactivate=service.deactivate, key=f"recipient_{company_id}")
    if recipients:
        st.dataframe(pd.DataFrame([r.__dict__ for r in recipients]), width="stretch", hide_index=True)


def _profile_kind(service: PlatformAdminService, company_id: str) -> RecordKind:
    return RecordKind(
        noun="profile",
        id_of=lambda p: p.profile_id,
        name_of=lambda p: f"{p.name} ({p.frequency})",
        to_row=lambda p: {
            "profile_id": p.profile_id, "name": p.name, "frequency": p.frequency,
            "formats": cell_list(p.formats), "channels": cell_list(p.channels),
            "recipient_ids": cell_list(p.recipient_ids), "active": p.active,
            **scope_cells(p.scope)},
        from_row=lambda row: DeliveryProfile(
            required(row.get("profile_id"), "profile_id"), company_id,
            required(row.get("name"), "name"),
            scope_from_cells(row),
            read_text(row.get("frequency")) or FREQUENCY_OPTIONS[0],
            read_list(row.get("formats")), read_list(row.get("channels")),
            read_list(row.get("recipient_ids")), read_flag(row.get("active")),
        ),
        save=lambda record, actor, reason: service.save_delivery_profile(
            record, actor=actor, reason=reason),
    )


def _render_delivery_profiles(service: PlatformAdminService) -> None:
    st.subheader("Delivery Profiles")
    company_id = _select_company(service, "profile_company")
    if not company_id:
        return
    recipients = service.repo.list_recipients(company_id)
    profiles = service.repo.list_delivery_profiles(company_id)
    frequencies = FREQUENCY_OPTIONS[:-1]

    def form(editing: DeliveryProfile | None) -> None:
        mark = editing.profile_id if editing else "new"
        with st.form(f"delivery_form_{mark}"):
            profile_id = st.text_input(
                "Profile ID", value=editing.profile_id if editing else f"profile-{uuid4().hex[:8]}",
                disabled=editing is not None, key=f"profile_id_{mark}")
            name = st.text_input("Profile name", value=editing.name if editing else "",
                                 key=f"profile_name_{mark}")
            frequency = st.selectbox(
                "Frequency", frequencies, key=f"profile_freq_{mark}",
                index=frequencies.index(editing.frequency) if editing and editing.frequency in frequencies else 0)
            formats = st.multiselect("Formats", REPORT_FORMAT_OPTIONS[:-1], key=f"profile_formats_{mark}",
                                     default=sorted(editing.formats) if editing else [])
            channels = st.multiselect("Channels", CHANNEL_OPTIONS[:-1], key=f"profile_channels_{mark}",
                                      default=sorted(editing.channels) if editing else [])
            recipient_ids = st.multiselect(
                "Recipients", [r.recipient_id for r in recipients], key=f"profile_recipients_{mark}",
                default=[r.recipient_id for r in recipients
                         if editing and r.recipient_id in editing.recipient_ids],
                format_func=lambda rid: next(f"{r.name} <{r.email}>" for r in recipients if r.recipient_id == rid),
            )
            active = st.checkbox("Active", value=editing.active if editing else True,
                                 key=f"profile_active_{mark}")
            scope = _scope_editor(f"profile_{mark}", editing.scope if editing else None)
            actor, reason = _reason_fields(f"profile_{mark}")
            if st.form_submit_button("Save changes" if editing else "Create profile", type="primary"):
                _safe_action(lambda: service.save_delivery_profile(
                    DeliveryProfile(
                        (editing.profile_id if editing else profile_id).strip(), company_id,
                        name.strip(), scope, frequency,
                        frozenset(formats), frozenset(channels), frozenset(recipient_ids), active,
                    ), actor=actor, reason=reason,
                ))

    record_tabs(_profile_kind(service, company_id), profiles, form,
                deactivate=service.deactivate, key=f"profile_{company_id}")
    if profiles:
        st.dataframe(pd.DataFrame([
            {
                "Profile": p.name, "Frequency": p.frequency, "Formats": sorted(p.formats),
                "Channels": sorted(p.channels), "Recipients": sorted(p.recipient_ids),
                "Active": p.active, "Scope": scope_to_dict(p.scope),
            } for p in profiles
        ]), width="stretch", hide_index=True)



def _build_delivery_scheduler(service: PlatformAdminService) -> DeliverySchedulerService:
    schedule_repo = DeliveryScheduleRepository(service.repo.engine)
    generation_repo = OutputGenerationRepository(service.repo.engine)
    delivery_repo = DeliveryAttemptRepository(service.repo.engine)
    exports = DataExportService(service, generation_repo, Path("data/generated_outputs"))
    delivery = DeliveryExecutionService(
        service,
        generation_repo,
        delivery_repo,
        secure_location_adapter=LocalSecureLocationAdapter(_delivery_secure_root()),
        email_adapter=None,
    )
    return DeliverySchedulerService(
        service,
        schedule_repo,
        NoReleasedRunProvider(),
        exports,
        delivery,
    )


def _render_schedule_configuration(service: PlatformAdminService) -> None:
    st.markdown("### Schedule Configuration")
    company_id = _select_company(service, "schedule_company")
    if not company_id:
        return
    profiles = service.repo.list_delivery_profiles(company_id)
    if not profiles:
        st.info("Create a Delivery Profile before configuring a schedule.")
        return

    profile_ids = [profile.profile_id for profile in profiles]
    profile_id = st.selectbox(
        "Delivery Profile",
        profile_ids,
        key="schedule_profile",
        format_func=lambda pid: next(
            f"{profile.name} ({profile.frequency})" for profile in profiles if profile.profile_id == pid
        ),
    )
    profile = next(profile for profile in profiles if profile.profile_id == profile_id)
    scheduler = _build_delivery_scheduler(service)
    existing = scheduler.schedules.get_schedule(profile_id)

    timezone_options = sorted(available_timezones())
    default_timezone = existing.timezone_name if existing else "Asia/Bangkok"
    if default_timezone not in timezone_options:
        timezone_options = [default_timezone] + timezone_options
    timezone_index = timezone_options.index(default_timezone)

    with st.form("delivery_schedule_form"):
        st.caption(f"Frequency: {profile.frequency}")
        first_delivery_date = st.date_input(
            "First delivery date",
            value=existing.first_delivery_date if existing else date.today(),
            key="schedule_first_delivery_date",
        )
        delivery_time = st.time_input(
            "Delivery time",
            value=existing.delivery_time if existing else time(8, 0),
            key="schedule_delivery_time",
        )
        timezone_name = st.selectbox(
            "Timezone",
            timezone_options,
            index=timezone_index,
            key="schedule_timezone",
            help="IANA timezone; delivery time is interpreted in this timezone.",
        )
        enabled = st.checkbox(
            "Schedule enabled",
            value=existing.enabled if existing else True,
            key="schedule_enabled",
        )
        actor, reason = _reason_fields("schedule")
        if st.form_submit_button("Save schedule", type="primary"):
            _safe_action(lambda: scheduler.save_schedule(
                profile_id,
                timezone_name=timezone_name,
                delivery_time=delivery_time,
                first_delivery_date=first_delivery_date,
                enabled=enabled,
                actor=actor,
                reason=reason,
            ))


def _render_schedule_monitor(service: PlatformAdminService) -> None:
    st.markdown("### Schedule Monitor")
    scheduler = _build_delivery_scheduler(service)
    rows = scheduler.monitor_rows()
    if not rows:
        st.info("No delivery schedules have been configured yet.")
    else:
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    st.caption("Released forecast run provider: waiting for the production forecast-release interface.")
    if not login_enabled():
        if st.button("Run due schedules now", key="delivery_schedule_run_due"):
            try:
                results = scheduler.run_due_once(
                    actor="HTC Platform Admin",
                    force_retry_waiting=True,
                )
            except PlatformAdminError as exc:
                st.error(str(exc))
                return
            if not results:
                st.info("No schedules are due.")
            for result in results:
                message = f"{result.profile_id}: {result.status}"
                if result.status == "COMPLETED":
                    st.success(message)
                elif result.status in {"WAITING_FOR_RUN", "PARTIAL"}:
                    st.warning(message)
                else:
                    st.error(message)

def _render_generation_history(service: PlatformAdminService) -> None:
    st.markdown("### Generation History")
    companies = service.repo.list_companies()
    options = ["All"] + [company.company_id for company in companies]
    selected = st.selectbox("Generation company filter", options, key="generation_history_company")
    records = OutputGenerationRepository(service.repo.engine).list_records(
        None if selected == "All" else selected
    )
    if not records:
        st.info("No report or data outputs have been generated yet.")
        return
    st.dataframe(pd.DataFrame([
        {
            "Generated at": record.generated_at,
            "Company": record.company_id,
            "Delivery profile": record.delivery_profile_id or "On demand",
            "Run ID": record.run_id,
            "Model / version": record.model_version,
            "Type": record.output_type,
            "Format": record.output_format,
            "Status": record.status,
            "Rows": record.row_count,
            "Generated by": record.generated_by,
            "File": Path(record.file_path).name if record.file_path else "-",
            "Path": record.file_path or "-",
            "Error": record.error_message or "",
        } for record in records
    ]), width="stretch", hide_index=True)


def _delivery_secure_root() -> Path:
    return Path(os.environ.get("HTC_SECURE_DELIVERY_ROOT", "data/secure_delivery"))


def _render_delivery_execution(service: PlatformAdminService) -> None:
    st.markdown("### Deliver validated output")
    secure_root = _delivery_secure_root()
    st.caption(f"Secure location root: {secure_root}")
    st.caption("Email transport: not configured in local development")

    generation_repo = OutputGenerationRepository(service.repo.engine)
    eligible = [
        record for record in generation_repo.list_records()
        if record.status == "VALIDATED" and record.delivery_profile_id
    ]
    if not eligible:
        st.info("No profile-bound validated outputs are available for delivery.")
        return

    generation_ids = [record.generation_id for record in eligible]
    selected_id = st.selectbox(
        "Validated output",
        generation_ids,
        key="delivery_execution_generation",
        format_func=lambda gid: next(
            f"{r.company_id} | {r.output_format} | {r.run_id} | {r.generated_at}"
            for r in eligible if r.generation_id == gid
        ),
    )
    actor = st.text_input("Delivered by", value="HTC Platform Admin", key="delivery_execution_actor")
    if st.button("Deliver now", type="primary", key="delivery_execution_submit"):
        delivery_repo = DeliveryAttemptRepository(service.repo.engine)
        executor = DeliveryExecutionService(
            service,
            generation_repo,
            delivery_repo,
            secure_location_adapter=LocalSecureLocationAdapter(secure_root),
            email_adapter=None,
        )
        try:
            results = executor.deliver(selected_id, delivered_by=actor)
        except PlatformAdminError as exc:
            st.error(str(exc))
            return
        for result in results:
            message = f"{result.channel}: {result.status}"
            if result.status == "DELIVERED":
                st.success(message)
            elif result.status == "NOT_CONFIGURED":
                st.warning(message)
            else:
                st.error(message)


def _render_delivery_history(service: PlatformAdminService) -> None:
    st.markdown("### Delivery History")
    companies = service.repo.list_companies()
    options = ["All"] + [company.company_id for company in companies]
    selected = st.selectbox("Delivery company filter", options, key="delivery_history_company")
    records = DeliveryAttemptRepository(service.repo.engine).list_records(
        None if selected == "All" else selected
    )
    if not records:
        st.info("No delivery attempts have been recorded yet.")
        return
    st.dataframe(pd.DataFrame([
        {
            "Attempted at": record.attempted_at,
            "Company": record.company_id,
            "Delivery profile": record.delivery_profile_id,
            "Generation": record.generation_id,
            "Channel": record.channel,
            "Destinations": ", ".join(record.destinations) if record.destinations else "-",
            "Status": record.status,
            "Delivered by": record.delivered_by,
            "Artifact": Path(record.artifact_path).name,
            "Delivered location": record.delivered_location or "-",
            "Completed at": record.completed_at,
            "Error": record.error_message or "",
        } for record in records
    ]), width="stretch", hide_index=True)


def _render_audit(service: PlatformAdminService) -> None:
    st.subheader("Change history")
    companies = service.repo.list_companies()
    options = ["All"] + [c.company_id for c in companies]
    selected = st.selectbox("Company filter", options, key="audit_company")
    records = service.list_audit(None if selected == "All" else selected)
    if not records:
        st.info("No audit records yet.")
        return
    st.dataframe(pd.DataFrame([
        {
            "Changed at": r.changed_at,
            "Company": r.company_id,
            "Entity": r.entity_type,
            "Entity ID": r.entity_id,
            "Action": r.action,
            "Changed by": r.actor,
            "Reason": r.reason,
            "Effective date": r.effective_date,
            "Old value": r.old_value_json,
            "New value": r.new_value_json,
        } for r in records
    ]), width="stretch", hide_index=True)


def _authenticate(service: PlatformAdminService) -> None:
    if not login_enabled():
        return

    if st.session_state.get("platform_admin_auth_email") == PLATFORM_ADMIN_EMAIL:
        return

    st.title("HTC Platform Admin")
    if not service.platform_admin_password_is_set():
        st.caption("First login: set the Platform Admin password")
        with st.form("admin_first_login"):
            email = st.text_input("Admin email")
            password = st.text_input("Set Platform Admin password", type="password")
            confirm = st.text_input("Confirm password", type="password")
            submitted = st.form_submit_button("Set password and sign in", type="primary")
        if submitted:
            if password != confirm:
                st.error("Passwords do not match")
            else:
                try:
                    service.set_initial_platform_admin_password(email, password)
                except PlatformAdminError as exc:
                    st.error(str(exc))
                else:
                    st.session_state["platform_admin_auth_email"] = PLATFORM_ADMIN_EMAIL
                    st.rerun()
        st.stop()

    with st.form("admin_login"):
        email = st.text_input("Admin email")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary")
    if submitted:
        if service.verify_platform_admin_password(email, password):
            st.session_state["platform_admin_auth_email"] = PLATFORM_ADMIN_EMAIL
            st.rerun()
        st.error("Invalid Platform Admin credentials")
    st.stop()


def _sign_out_of_platform_admin() -> None:
    """Leave the admin door. One of these, however many buttons ask for it."""
    st.session_state.pop("platform_admin_auth_email", None)
    st.rerun()


def render_platform_admin(internal_renderers: dict[str, object] | None = None) -> None:
    service = build_service()
    _authenticate(service)
    internal_renderers = internal_renderers or {}

    # Closing is a thing you do to an application running on your own machine. On a published
    # deployment the same button sends SIGTERM to the one process serving everybody, so `app.py`
    # registers the renderer only where there is something to close (v1.2.104).
    closing_is_possible = callable(internal_renderers.get("Close the application"))
    # Stefan, 23 November 2026: *"instead of Close maybe you could have suggest to make this into a
    # Log Out button"*. Right - the slot at the end of the bar is where the hand already goes, and
    # on a deployment leaving is what you actually want to do there. So where there is nothing to
    # close, that slot signs you out instead of standing empty.
    signing_out_is_on_the_bar = login_enabled() and not closing_is_possible

    # Signing out stays above the title. Returning to the application does not: it sits beside the
    # section bar, further down this function. Stefan, 4 October 2026. Unless the bar is carrying
    # it, in which case one screen would be offering the same thing twice.
    if login_enabled() and not signing_out_is_on_the_bar:
        signout_col, _spacer = st.columns([1.6, 6])
        with signout_col:
            if st.button("Sign out", width="stretch", key="platform_admin_signout"):
                _sign_out_of_platform_admin()

    # The same banner and the same gauge as every other page. Both belong to `app.py`; this module
    # asks for them by name, as it does for the pages themselves.
    banner = internal_renderers.get("Brand header")
    if callable(banner):
        banner()
    else:
        st.title("Platform Admin")
    gauge = internal_renderers.get("Collection gauge")
    if callable(gauge):
        gauge()

    # "Platform Admin" leads the line now that the banner carries the product name, so the reader
    # can still see at a glance which part of the application they are in.
    running = running_version()
    if login_enabled():
        st.caption(f"Platform Admin · HTC-controlled workspace · Admin {PLATFORM_ADMIN_EMAIL} · v{running}")
    else:
        st.caption(f"Platform Admin · Development mode · Login disabled · "
                   f"Admin {PLATFORM_ADMIN_EMAIL} · v{running}")
    if restart_required():
        st.warning(restart_message())

    # The way out sits on the section bar, beside Dashboard through Close, as its own button.
    # Stefan, 4 October 2026. It was above the title before, where the first control on a page lies
    # under Streamlit's header band; here it is clear of it and it reads as what it is - one more
    # place to go, on the row that holds the places to go.
    # Where the page is, in one place. The two group controls each hold their own value and clear
    # the other's, so the bar can never show two open pages.
    if "platform_admin_page" not in st.session_state:
        st.session_state["platform_admin_page"] = OVERVIEW
        st.session_state["platform_admin_overview"] = OVERVIEW
        st.session_state["platform_admin_customers"] = None
        st.session_state["platform_admin_market"] = None

    # Three columns whenever the end of the bar has something to offer: the sections, the way out,
    # and either closing the application or signing out. Two only in development mode, where
    # login is off and there is nothing to sign out of.
    last_slot_is_used = closing_is_possible or signing_out_is_on_the_bar
    columns = [5, 2.2, 2.2] if last_slot_is_used else [5, 2.2]
    nav_col, exit_col, *rest = st.columns(columns, vertical_alignment="center")
    close_col = rest[0] if rest else None
    with nav_col:
        st.radio(
            "Overview", [OVERVIEW], horizontal=True, label_visibility="collapsed",
            key="platform_admin_overview", on_change=_chose,
            args=("platform_admin_overview", ("platform_admin_customers", "platform_admin_market")),
        )
    with exit_col:
        if st.button("← Return to previous page", width="stretch", key="platform_admin_exit"):
            st.session_state["application_navigation"] = "Current Market"
            # Remove only the admin route, so any other query parameter survives the trip back.
            try:
                del st.query_params["admin"]
            except (KeyError, AttributeError):
                st.query_params.clear()
            st.rerun()
    if close_col is not None:
        with close_col:
            if closing_is_possible:
                # The same two flags `main()` sets from the top-row Close, so there is one close
                # page, one question and one yes however you arrive. Straight to the question: a
                # button that exists to reveal the real button is a third click on a two-click
                # decision (Stefan, 16 October 2026: "I now need to push 3 times").
                if st.button("Close the application", width="stretch",
                             key="platform_admin_close"):
                    st.session_state["closing_the_application"] = True
                    st.session_state["shutdown_asked"] = True
                    st.rerun()
            elif st.button("Sign out", width="stretch", key="platform_admin_signout_bar"):
                _sign_out_of_platform_admin()

    # The two groups, each named. The name is the whole point: before this the bar was seven peers
    # in a wrapping row and nothing on it said which half of the work a page belonged to.
    st.radio(
        "Customers", CUSTOMER_PAGES, horizontal=True, key="platform_admin_customers",
        on_change=_chose,
        args=("platform_admin_customers", ("platform_admin_overview", "platform_admin_market")),
    )
    st.radio(
        "Market data and forecasts", MARKET_PAGES, horizontal=True, key="platform_admin_market",
        on_change=_chose,
        args=("platform_admin_market", ("platform_admin_overview", "platform_admin_customers")),
    )
    section = st.session_state["platform_admin_page"]
    st.divider()

    # Closing takes over the page, as it does in the application. "Keep it running" clears
    # `shutdown_asked`, and this puts the reader back on the section they were looking at.
    if st.session_state.get("closing_the_application"):
        if not st.session_state.get("shutdown_asked") and not st.session_state.get("shutdown_done"):
            st.session_state["closing_the_application"] = False
            st.rerun()
        renderer = internal_renderers.get("Close the application")
        if renderer is None:
            st.info("Closing from the browser is not connected in this runtime.")
            return
        renderer()
        return

    def _internal(pages: list[str], key: str) -> None:
        """One of the application's own pages, chosen by its name on the bar rather than its key."""
        chosen = _within("area", pages, key) if len(pages) > 1 else pages[0]
        renderer = internal_renderers.get(INTERNAL_PAGES[chosen])
        if renderer is None:
            st.info(f"{chosen} is not connected in this runtime.")
            return
        renderer()

    if section == OVERVIEW:
        _render_dashboard(service, internal_renderers)
        return

    # ---- Customers ------------------------------------------------------------------------------
    if section == "Customers":
        _render_companies(service)
        return
    if section == "Subscriptions":
        {
            "Package Templates": _render_packages,
            "Permanent Entitlements": _render_permanent,
            "Temporary Entitlements": _render_temporary,
        }[_within("Subscriptions area", ACCESS_NAVIGATION, "platform_admin_access_area")](service)
        return
    if section == "People":
        {
            "Users": _render_users,
            "Recipients & Domains": _render_recipients,
        }[_within("People area", PEOPLE_NAVIGATION, "platform_admin_people_area")](service)
        return
    if section == "Deliveries":
        # Six blocks on one page until 30 October 2026, with no way to get past the first without
        # scrolling through all of them: set it up, say when, send it, see what happened.
        chosen = _within("Deliveries area", DELIVERY_NAVIGATION, "platform_admin_delivery_area")
        if chosen == "Profiles":
            _render_delivery_profiles(service)
        elif chosen == "Schedule":
            _render_schedule_configuration(service)
            _render_schedule_monitor(service)
        elif chosen == "Send":
            _render_delivery_execution(service)
        else:
            _render_generation_history(service)
            _render_delivery_history(service)
        return
    if section == "Customer Reports":
        _internal(CUSTOMER_REPORT_NAVIGATION, "platform_admin_report_area")
        return
    if section == "Change history":
        _render_audit(service)
        return

    # ---- Market data and forecasts ----------------------------------------------------------------
    if section == "Prices":
        _internal(PRICE_NAVIGATION, "platform_admin_price_area")
        return
    if section == "Open questions":
        _internal(["Open questions"], "platform_admin_question_area")
        return
    if section == "Relationships":
        _internal(RELATIONSHIP_NAVIGATION, "platform_admin_relationship_area")
        return
    if section == "Forecasts":
        _internal(FORECAST_NAVIGATION, "platform_admin_forecast_area")
        return
    # A page on the bar with nothing behind it is a dead end, and saying so beats a blank screen.
    st.info(f"{section} is not connected in this runtime.")
