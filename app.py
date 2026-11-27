from __future__ import annotations

import base64
import json
import logging
import math
import os
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys

# The two source roots, on the path before anything imports from them.
#
# On this machine the launcher sets PYTHONPATH and this block finds nothing to do. Streamlit
# Community Cloud runs `streamlit run app.py` from the repository root and offers no way to set
# PYTHONPATH at all, so without this the first `from raw_material_forecasting import ...` below
# ends the session with ModuleNotFoundError before a single pixel is drawn.
#
# market_data_engine goes FIRST, ahead of the repository root that Python itself puts at
# sys.path[0]. The engine's package is named `app` and this file is app.py: whichever the import
# machinery reaches first wins, and the engine's own `from app.config import Settings` needs the
# package rather than this script. See raw_material_forecasting.embedded_engine.
_REPO_ROOT = Path(__file__).resolve().parent
for _source_root in (_REPO_ROOT / "src", _REPO_ROOT / "market_data_engine"):
    if _source_root.is_dir() and str(_source_root) not in sys.path:
        sys.path.insert(0, str(_source_root))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# Streamlit 1.63 runs on Uvicorn instead of Tornado, and Uvicorn's Windows accept loop prints a
# full traceback every time a connection is dropped before the handshake completes - "OSError:
# [WinError 64] The specified network name is no longer available". A browser opening and
# reopening a tab does that. Tornado swallowed it; this fills the launcher window with tracebacks
# that read like a failure and are not one.
#
# The listening socket, the bind and every real request are unaffected: this quiets one logger in
# the server process the script runs in, and only the level that carries those accept traces.
logging.getLogger("asyncio").setLevel(logging.CRITICAL)

# What a hosted deployment is configured with.
#
# This machine keeps its configuration in the environment: database.env for the store, secrets.env
# for the keys, both under the Windows application-data folder and never in the project folder.
# The container is given environment variables too. Streamlit Community Cloud has neither - what a
# deployment there is given lives in st.secrets, typed into that app's Secrets box - so the names
# are copied into the environment here, before anything reads them. The engine takes DATABASE_URL
# from os.environ and so does every tool in the repository: one shape of configuration, three
# places it can come from.
#
# An existing environment variable wins, so a shell that set one for this run is not overridden.
# Only strings are copied; a secrets file may hold nested tables, and those belong to whoever
# asked for them. Nothing here prints a value.
# Two shapes arrive here, and v1.2.98 read only one of them. A flat file:
#
#     DATABASE_URL = "postgresql+psycopg://..."
#
# and a file with the names under a heading, which is the habit DEPLOYMENT.md for the Observatory
# product teaches:
#
#     [secrets]
#     DATABASE_URL = "postgresql+psycopg://..."
#
# A name under a heading is a nested table, not a string, so it was skipped - silently, because
# skipping is what the isinstance check is for. The deployment then had no DATABASE_URL, the
# platform store fell back to an empty SQLite file, and every sign-in was refused in the same
# words a wrong password gets. One level of nesting is now unwrapped as well.
try:
    _secret_items = list(dict(st.secrets).items())
    for _, _secret_value in list(_secret_items):
        if not isinstance(_secret_value, str):
            try:
                _secret_items.extend(dict(_secret_value).items())
            except Exception:           # not a table either; nothing to unwrap
                pass
    for _secret_name, _secret_value in _secret_items:
        if isinstance(_secret_value, str) and _secret_name not in os.environ:
            os.environ[_secret_name] = _secret_value
except Exception:                       # no secrets file at all: this machine, which is normal
    pass

from raw_material_forecasting.delivered_price import calculate_delivered_price
from raw_material_forecasting.forecasting import ets_forecast
from raw_material_forecasting.series_audit import audit_series, evaluate_horizon_gate
from raw_material_forecasting.timebase import has_continuous_weekly_observations, normalize_weekly_price_series
from raw_material_forecasting.ui_helpers import build_user_forecast_frame, status_message
from raw_material_forecasting.ui_state import intelligence_queue_frame, conceptual_pmdi_chain_frame
from raw_material_forecasting.performance import performance_summary
from raw_material_forecasting.intelligence.watchlists import load_watchlist
from raw_material_forecasting.intelligence.discovery import run_watchlist_scan
from raw_material_forecasting import collection_watch
from raw_material_forecasting import deployment_profile
from raw_material_forecasting.platform_store import platform_store_url
from raw_material_forecasting import embedded_engine

# A published customer deployment carries its own switches. The marker file is written by the
# publish script and exists in no other checkout, so this does nothing on Stefan's machine and
# everything on Streamlit Community Cloud, which has no environment variables to set. Applied
# HERE, after the secrets are read and before anything below reads a switch out of os.environ.
HOSTED_DEPLOYMENT = deployment_profile.apply(_REPO_ROOT)
from raw_material_forecasting import coverage, customer_catalogue, customer_controls, trade_feeds
from raw_material_forecasting import populations
from raw_material_forecasting import source_status
from raw_material_forecasting import price_kinds
from raw_material_forecasting import secrets_file
from raw_material_forecasting import (forecast_cards, forecast_controls, forecast_explainer,
                                       forecast_record, market_intelligence)
from raw_material_forecasting.market_data_engine_client import MarketDataEngineClient, MarketDataEngineUnavailable
from raw_material_forecasting.current_market import EUROPE_MIN_MONTHS, best_option as best_market_option, build_current_market_state, cascade_options, enough_history, equivalent_identity_keys, evidence_spread, find_identity, governed_history, identity_dimensions, preferred_identity, reference_history, same_kind_of_price, state_from_governed_series, with_readings
from raw_material_forecasting.tdi_market_view import build_tdi_china_domestic_state
from raw_material_forecasting.release import (
    restart_message, restart_required, running_version, version_on_disk)
from raw_material_forecasting.chemicals import display_chemical
from raw_material_forecasting import market_lines as market_lines_module
from raw_material_forecasting import units
from raw_material_forecasting.market_lines import (
    CURRENT_WITHIN_DAYS, FORECAST_WITHIN_DAYS, display_packaging,
    drawable_selections,
    geographies_with_a_market,
    history_span, lines_on, lines_packaged, market_lines, packaging_choices,
    priced_since, terms_with_a_market,
)
from raw_material_forecasting import shutdown as runtime_shutdown
from raw_material_forecasting import page_chart
from raw_material_forecasting import route_diagram
from raw_material_forecasting import page_reports
from raw_material_forecasting import publisher_scorecard
from raw_material_forecasting.market_basis import (
    BASIS_LABEL, INCOTERM_LABEL, display_basis, geography_phrase)
from raw_material_forecasting.series_curation import basis_for, is_drawn, market_key, terms_named
from raw_material_forecasting import mi_report
from raw_material_forecasting.chemical_routes import load_graph as load_chemical_route_graph
from raw_material_forecasting.correlation_view import (
    FULL_CHAIN,
    targets_with_a_study as relationship_offerable,
    build_view as build_relationship_view,
    display_name as relationship_display_name,
    market_labels as relationship_market_labels,
    render_method_evidence as render_relationship_method_evidence,
    render_study as render_relationship_study,
    target_markets as relationship_markets,
)
from raw_material_forecasting.fx import DEFAULT_DISPLAY_CURRENCY, DISPLAY_CURRENCIES, FxTable, convert_snapshot
from raw_material_forecasting.source_status import classify_source, next_check_text, price_matrix, price_matrix_table, summarize as summarize_sources, window_by_source
from raw_material_forecasting.platform_admin.access import allowed_application_pages, entitled_chemicals as chemicals_within_entitlement, filter_entitled_values, identity_allowed
from raw_material_forecasting.platform_admin.models import RuntimeAccess
from raw_material_forecasting.platform_admin.service import PlatformAdminError
from raw_material_forecasting.platform_admin.ui import build_service as build_platform_admin_service, render_platform_admin
from raw_material_forecasting.platform_admin.auth import PLATFORM_ADMIN_EMAIL, login_enabled, normalise_email
from raw_material_forecasting.platform_admin.navigation import PLATFORM_ADMIN_PAGE, application_pages
from raw_material_forecasting.producer_signals import (
    customer_visible_signals,
    internal_display_rows,
    load_producer_signals,
)
from market_selection_ui import ACTIVE_CHEMICALS, ASSESSMENT_BASES, CHEMICALS, ORIGINS, INCOTERMS,  market_title, lane_status, is_active_chemical, is_published_market, chemical_kind, feedstocks_of, why_not_published, FEEDSTOCK_CHEMICALS


# Streamlit re-runs this script top to bottom on every interaction, so reading the file here would
# report whatever is on disk while the imported modules stay as they were loaded. `running_version`
# is captured once at import and is therefore the code that is actually answering.
VERSION = running_version()
TEMPLATE_PATH = Path("data/templates/market_observations_template.csv")
# v0.5.42: data/runtime/forecast.db is gone. It held the v0.4 China pMDI seed, rebuilt from the
# public research CSV at every start, and the only reader (get_persistent_context) was never
# called. One store: the market data engine on Supabase.
WATCHLIST_PATH = Path("data/config/china_pmdi_watchlist.json")
# Where the market-data engine answers. Loopback on Stefan's machine, where Run-MVP.bat starts it
# beside the application; another address in the hosted deployment, where the two run as separate
# processes in one container. Read from the environment so the page does not have to know which.
MARKET_DATA_ENGINE_URL = os.environ.get("MARKET_DATA_ENGINE_URL", "http://127.0.0.1:7861").strip()


def _as_on_disk(module):
    """The module as its file has it now: imported again where the file changed since it was.

    Compared by the file's modification time, kept on the module itself, so it costs one `stat`
    a run and imports nothing while nothing has changed. A file that will not import - a publish
    caught half-written - leaves the module exactly as it was; the next run looks again.

    Safe for `embedded_engine` because what it has started is kept on `sys`, not in the module.
    It is not a general cure for a stale module and must not be pointed at one that keeps state.
    """
    import importlib

    try:
        on_disk = os.path.getmtime(module.__file__)
    except (OSError, TypeError, AttributeError):
        return module
    if getattr(module, "_file_stamp", None) == on_disk:
        return module
    try:
        module = importlib.reload(module)
    except Exception:
        logging.getLogger(__name__).warning("could not import %s again", module.__name__,
                                            exc_info=True)
        return module
    module._file_stamp = on_disk
    return module


def _embedded_market_data_engine() -> str:
    """The engine inside this process, of the release this page is, and where it answers.

    Streamlit re-runs this script top to bottom on every click, so this is asked on every click
    and has to cost nothing when nothing has changed: `embedded_engine` keeps the one engine it
    started and hands back its address.

    It was `st.cache_resource` that made it happen once, and that is what kept a v1.2.112 engine
    answering v1.2.114 pages on 24 November 2026: the cache remembered an address, a publish
    replaced the page, and nothing ever asked again. Now the page says which release it is, and
    where the engine answering was started for another one `embedded_engine` builds a fresh one
    from the files on disk and retires the old - see its module docstring for what keeps that
    safe. The spinner is shown only when something is about to start.

    THE RELEASE IS READ FROM DISK, AND THE HELPER IS NOT ASSUMED TO BE THE NEW ONE
    ------------------------------------------------------------------------------
    The first rehearsal of this, on a copy of the deployment, failed in a way worth keeping. The
    new files were dropped over a running v1.2.114 and the next visit raised: the new page was
    calling `embedded_engine.answering`, and the `embedded_engine` Python was holding was still
    v1.2.114's, which has no such function. Streamlit always runs the new script; whether it has
    imported the script's helper modules again by then is its business and a matter of timing.
    At the top of this file that is every visitor on an error page.

    So: the one helper this depends on is brought up to its file here (`_as_on_disk`), an older
    one that could not be is still used the way it always was, and the release asked for is the
    one on disk - not `VERSION`, which is a fact about a module import and can lag the files.
    """
    starter = _as_on_disk(embedded_engine)
    release = version_on_disk()
    if not hasattr(starter, "answering") or release in ("", "unknown"):
        return starter.start()
    if starter.answering(release):
        return starter.start(release=release)
    with st.spinner("Starting the market-data engine ..."):
        url = starter.start(release=release)
    # What the pages kept was the previous engine's answers. A release that changes what the
    # engine says - v1.2.113 took China's VAT out of the readings - must not be read through
    # fifteen minutes of the old one's.
    st.cache_data.clear()
    return url


# Streamlit Community Cloud runs one command and gives no way to start a second process, so there
# the engine runs on a thread in here instead. Off everywhere else: on this machine the real
# engine is already listening on 7861, and a second one would be two engines on one store.
if embedded_engine.wanted():
    MARKET_DATA_ENGINE_URL = _embedded_market_data_engine()

TDI_CHINA_DOMESTIC_IDENTITY_KEY = "TDI|UNKNOWN|China|EXW|UNKNOWN|CNY|t|market_price"
STEP6_COLLECTION_STATUS_PATH = Path(os.environ.get("STEP6_COLLECTION_STATUS", "Step6_Collection_Status.json"))
PRODUCER_SIGNALS_PATH = Path(os.environ.get("PRODUCER_SIGNALS_FEED", "data/producer_signals.json"))
HTC_LOGO = Path("assets/htc_global.png")
PU_OBSERVATORY_LOGO = Path("assets/pu_observatory.png")
HORIZON_TITLES = {4: "4 Week Forecast", 8: "8 Week Forecast", 12: "12 Week Forecast"}
FORECAST_SCREEN_NAME = "PU Market Forecast"
APPLICATION_PAGES = ["Current Market", "Historic Pricing", "Market Intelligence", "Forecast"]
HTC_LOGIN_MODE = os.environ.get("HTC_LOGIN_MODE", "development").strip().lower()
PLATFORM_ACCESS_MODE = os.environ.get("HTC_PLATFORM_ACCESS_MODE", "open").strip().lower()
# Where a signed-in customer's address is kept between reruns. Streamlit runs this script again on
# every click, so the session is the only place a sign-in can live.
SIGNED_IN_EMAIL = "application_user_email"
# A machine-set identity standing in for a sign-in. It is a development convenience and nothing
# else: with it on, whoever opens the port IS that user, which on a deployment reachable from the
# internet would be every visitor at once, all of them holding one customer's entitlement. Off
# unless the machine explicitly says otherwise, so a hosted deployment that simply does not set it
# gets the sign-in screen - which is the behaviour you want it to fall back to.
TRUSTED_RUNTIME_IDENTITY = os.environ.get("HTC_TRUSTED_RUNTIME_USER", "").strip().lower() in {
    "1", "true", "yes", "on",
}


def _platform_admin_route_requested() -> bool:
    value = st.query_params.get("admin")
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value or "").strip().lower() in {"1", "true", "yes", "admin"}


def _platform_admin_control_visible() -> bool:
    if not login_enabled():
        return True
    return st.session_state.get("platform_admin_auth_email") == PLATFORM_ADMIN_EMAIL


def _signed_in_email() -> str:
    """The address the person at this browser signed in with, or empty if nobody has."""
    return str(st.session_state.get(SIGNED_IN_EMAIL) or "").strip()


def _configured_runtime_user_email() -> str:
    # See TRUSTED_RUNTIME_IDENTITY: a machine-set identity, for a developer's own session only.
    if not TRUSTED_RUNTIME_IDENTITY:
        return ""
    return os.environ.get("HTC_RUNTIME_USER_EMAIL", "").strip()


def said_to_the_customer(error: Exception) -> str | None:
    """The sentence a refusal carries for the customer, where it carries one.

    Read off the error HERE, and not imported from the service by name. v1.2.139 imported it by
    name at the top of this file and took the live site down on 27 November 2026: the hosted
    process re-reads this file on every visit and keeps the package it imported when it started,
    so the day a release adds a name, this file asks an older package for it and every visitor
    gets an ImportError instead of a sign-in page. With an older package behind it this returns
    None and the door says what it said before, until the host is restarted.
    """
    return getattr(error, "for_the_customer", None)


def _render_sign_in(service) -> None:
    """The customer's way in: an email, a password, and nothing else on the page.

    Every refusal says the same thing, because the screen that distinguishes "no such address"
    from "wrong password" tells a stranger which of our customers' addresses are real. The words
    come from the service, which refuses the whole pair as one.
    """
    left, _right = st.columns([1.1, 1.9])
    with left:
        st.markdown("### Sign in")
        with st.form("application_sign_in"):
            email = st.text_input("Email", key="sign_in_email")
            password = st.text_input("Password", type="password", key="sign_in_password")
            submitted = st.form_submit_button("Sign in", type="primary", width="stretch")
        if submitted:
            try:
                service.sign_in(email, password)
            except PlatformAdminError as exc:
                # The customer's sentence where the refusal carries one (v1.2.139): "Your access
                # starts on 1 December 2026", not "company has no entitlement and holds no product".
                st.error(said_to_the_customer(exc) or str(exc))
            else:
                st.session_state[SIGNED_IN_EMAIL] = normalise_email(email)
                st.rerun()
        st.caption(f"Access is arranged by HTC Global. Write to {PLATFORM_ADMIN_EMAIL}.")


def _render_password_change(service, email: str) -> None:
    """Replacing the password that was handed over, before the application opens.

    The first password is spoken on a call or written in a mail, so it is not a secret for long.
    This screen stands between it and the data until the customer has chosen their own.
    """
    left, _right = st.columns([1.1, 1.9])
    with left:
        st.markdown("### Choose your own password")
        st.caption("The password you were given has travelled in a message. Replace it to go on.")
        with st.form("application_password_change"):
            current = st.text_input("The password you were given", type="password")
            chosen = st.text_input("Your new password", type="password")
            confirm = st.text_input("Your new password again", type="password")
            submitted = st.form_submit_button("Save and continue", type="primary", width="stretch")
        if submitted:
            if chosen != confirm:
                st.error("The two new passwords are not the same.")
            else:
                try:
                    service.change_own_password(email, current, chosen)
                except PlatformAdminError as exc:
                    st.error(str(exc))
                else:
                    st.rerun()
        if st.button("Sign in as somebody else", key="password_change_signout"):
            _sign_out()


def _sign_out() -> None:
    st.session_state.pop(SIGNED_IN_EMAIL, None)
    for key in ("sign_in_email", "sign_in_password"):
        st.session_state.pop(key, None)
    st.rerun()


def _adopt_platform_admin_as_the_signed_in_user() -> None:
    """Somebody who proved the Platform Admin password is signed in to the application too.

    Stefan, 23 November 2026: *"It says return to previous page but that is same as logout."*

    It was. The two doors are separate sessions. `?admin=1` authenticates against the Platform
    Admin password and sets `platform_admin_auth_email`; the application's own sign-in sets
    `application_user_email`. Arrive by URL, prove the admin password, press "Return to previous
    page", and the application finds nobody signed in and draws the sign-in screen - having just
    been shown the strongest credential the system has.

    On Stefan's machine this never showed, because there login is disabled and the gate returns
    early. It only appears on a deployment, which is where he now works.

    The admin password is a higher bar than a user password, not a lower one, so proving it is
    proof enough for the application. The entitlement still decides what is drawn, and a forced
    password change still applies: this says who is reading, not what they may see.
    """
    if _signed_in_email():
        return
    if st.session_state.get("platform_admin_auth_email") != PLATFORM_ADMIN_EMAIL:
        return
    st.session_state[SIGNED_IN_EMAIL] = PLATFORM_ADMIN_EMAIL


def resolve_runtime_access() -> RuntimeAccess | None:
    """Who is reading, and what they are entitled to see. None when access is not being enforced.

    Three gates, in this order: a sign-in, then the password change that a handed-out password
    carries with it, then the entitlement. The first two draw a page and stop the script, so a
    caller only ever gets a resolved entitlement or nothing at all.
    """
    if not login_enabled():
        return None
    if PLATFORM_ACCESS_MODE != "enforced":
        return None
    service = build_platform_admin_service()
    _adopt_platform_admin_as_the_signed_in_user()
    email = _signed_in_email()
    if not email:
        email = _configured_runtime_user_email()
        if not email:
            render_brand_header()
            _render_sign_in(service)
            st.stop()
    elif service.password_change_required(email):
        render_brand_header()
        _render_password_change(service, email)
        st.stop()
    try:
        return service.resolve_user_access(email)
    except PlatformAdminError as exc:
        render_brand_header()
        st.error(said_to_the_customer(exc) or f"Application access denied: {exc}")
        if _signed_in_email() and st.button("Sign out", key="denied_signout"):
            _sign_out()
        st.stop()


def _scope_allows(value: str, allowed: frozenset[str]) -> bool:
    return "*" in allowed or value.strip().lower() in {str(item).strip().lower() for item in allowed}


def _entitled_values(values, allowed: frozenset[str], runtime_access: RuntimeAccess | None):
    return list(values) if runtime_access is None else filter_entitled_values(values, allowed)


def _entitled_chemicals(codes, runtime_access: RuntimeAccess | None):
    """The chemicals a reader may choose from. Chemicals have their own rule and this is it.

    An entitlement is written in the names it is SOLD under - "Polyether Polyol", "MDI" - while
    the selectors are built from the store's codes - POLYETHER_POLYOL, PMDI. `_entitled_values`
    compares two strings, so against a code it quietly matched nothing: measured 22 November 2026,
    an entitlement naming TDI, PMDI and Polyether Polyol was offered TDI and PMDI on all three
    pages, and the third chemical the customer had paid for was in no dropdown anywhere.

    `chemicals_within_entitlement` is the rule that was written for this in `platform_admin.access`
    and that nothing here was calling: it knows a sold name grants the codes beneath it, and that
    the generosity runs one way only - "Polyether Polyol" grants the four polyether-based polymer
    grades, "Polyester Polyol" grants none of them.
    """
    if runtime_access is None:
        return list(codes)
    return chemicals_within_entitlement(codes, runtime_access.scope.chemicals)


def image_data_uri(path: Path) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def apply_dashboard_css() -> None:
    st.markdown(
        """
        <style>
        :root { --navy:#061a2f; --navy2:#0a3157; --teal:#008da2; --blue:#0a6ed1; --ink:#10253c; --muted:#667b90; }
        .stApp { background: linear-gradient(180deg, #f8fafc 0%, #eef4f8 100%); color: var(--ink); }
        .block-container { width: 96%; max-width: 2200px; padding-top: 0.55rem; padding-bottom: 2rem; }
        header[data-testid="stHeader"] { background: transparent; }
        /* The transparent header still catches the mouse.
         *
         * Stefan, 3 October 2026: "Return to application" answered the mouse only along its bottom
         * edge. The page sets the top padding to 0.55rem against Streamlit's own 6rem, so the first
         * control on any page sits underneath the header band - invisible, because the line above
         * makes it transparent, and still in front, because a transparent element is still an
         * element. Only the few pixels of the button below the band were reachable.
         *
         * The band itself now ignores the mouse and its children take it back, so Streamlit's Rerun
         * and menu controls keep working and the empty space stops swallowing clicks. */
        header[data-testid="stHeader"] { pointer-events: none; }
        header[data-testid="stHeader"] * { pointer-events: auto; }
        [data-testid="stToolbar"] { right: 1rem; }
        h1, h2, h3 { color: var(--ink); }

        .brand-header {
            display:grid; grid-template-columns:minmax(320px,1fr) 1.65fr minmax(330px,1fr);
            align-items:center; gap:1.3rem; padding:0.95rem 1.35rem;
            background: radial-gradient(circle at 50% 120%, rgba(16,125,210,.26), transparent 42%),
                        linear-gradient(108deg,#020b16 0%,#082a4d 55%,#061828 100%);
            border-bottom:4px solid #00a2b7; border-radius:14px 14px 5px 5px;
            box-shadow:0 14px 34px rgba(5,25,48,.18); margin-bottom:.45rem; min-height:125px;
        }
        .brand-left { display:flex; align-items:center; gap:.9rem; min-width:0; }
        .brand-left img { width:94px; height:94px; object-fit:contain; flex:0 0 auto; filter:brightness(1.45) saturate(1.2) contrast(1.08); }
        .brand-left .brand-copy { min-width:0; }
        .brand-left .brand-name { color:#fff; font-size:1.43rem; font-weight:760; letter-spacing:.01em; }
        .brand-left .brand-tagline { color:#c8d8e8; font-size:.82rem; line-height:1.25; margin-top:.18rem; }
        .brand-center { color:white; text-align:center; padding:0 .8rem; border-left:1px solid rgba(255,255,255,.22); border-right:1px solid rgba(255,255,255,.22); }
        .brand-center .title { font-size:2.28rem; line-height:1.02; font-weight:780; letter-spacing:-.025em; }
        .brand-center .strap { margin-top:.42rem; color:#9fd8e5; font-size:.88rem; letter-spacing:.025em; }
        .brand-center .brand-version { margin-top:.35rem; color:#fff; font-size:.78rem; font-weight:700; letter-spacing:.04em; }
        .brand-right { display:grid; grid-template-columns:1fr 92px; align-items:center; gap:.85rem; }
        .brand-right .copy { text-align:right; }
        .brand-right .name { color:#fff; font-size:1.22rem; font-weight:780; letter-spacing:.025em; }
        .brand-right .engine { color:#a7d9e2; font-size:.8rem; margin-top:.18rem; }
        .brand-right img { width:92px; height:92px; object-fit:cover; border-radius:10px; border:1px solid rgba(255,255,255,.32); box-shadow:0 5px 16px rgba(0,0,0,.25); }

        div[role="radiogroup"] { display:flex; gap:.35rem; background:#071d34; border-radius:9px; padding:.24rem .35rem; margin-bottom:.5rem; }
        div[role="radiogroup"] label { border-radius:7px; padding:.48rem 1.1rem; color:#fff !important; }
        div[role="radiogroup"] label * { color:#fff !important; }
        div[role="radiogroup"] label:has(input:checked) { background:linear-gradient(90deg,#007f93,#0a6ed1); color:white; box-shadow:0 4px 12px rgba(0,95,170,.25); }
        .st-key-platform_admin_entry button { background:linear-gradient(90deg,#465b6d,#16758a) !important; color:#fff !important; border:1px solid #355566 !important; border-radius:9px !important; min-height:44px; font-weight:600; box-shadow:0 3px 10px rgba(22,61,77,.18); }
        .st-key-platform_admin_entry button:hover { background:linear-gradient(90deg,#3d5060,#12677a) !important; border-color:#244a5b !important; }

        .filter-strip { background:white; border:1px solid #d9e4ee; border-radius:13px; box-shadow:0 5px 16px rgba(28,62,96,.05); padding:.25rem .45rem .05rem; margin:.2rem 0 .75rem; }
        .forecast-card { position:relative; overflow:hidden; background:linear-gradient(180deg,#fff 0%,#fbfdff 100%); border:1px solid #d7e3ef; border-radius:15px; padding:1rem 1.05rem; box-shadow:0 7px 22px rgba(28,62,96,.08); min-height:188px; }
        .forecast-card:before { content:""; position:absolute; left:0; top:0; right:0; height:4px; background:linear-gradient(90deg,#008fa3,#0875d1); }
        .forecast-card .card-top { display:flex; justify-content:space-between; align-items:center; gap:.8rem; }
        .forecast-card .card-title { font-size:.95rem; font-weight:760; color:#173a5e; }
        .forecast-card .status { font-size:.67rem; font-weight:800; color:#855d00; background:#ffdf8a; border-radius:999px; padding:.2rem .5rem; }
        .forecast-card .value { margin-top:.72rem; font-size:1.95rem; font-weight:800; color:#071c33; letter-spacing:-.02em; }
        .forecast-card .value.range { font-size:1.34rem; line-height:1.2; }
        .forecast-card .delta { margin-top:.1rem; font-size:.94rem; font-weight:700; }
        .forecast-card .delta.up { color:#058948; } .forecast-card .delta.down { color:#c43c3c; } .forecast-card .delta.flat { color:#6b7d8f; }
        .forecast-card .meta { margin-top:.88rem; padding-top:.65rem; border-top:1px solid #edf2f6; color:#71869a; font-size:.76rem; }

        /* The banner replaced three forecast cards and the evidence panel. They carried the same
           forecast number three times over, because this market has no direction the model can
           follow and the centre is identical at every horizon - so the only part that changed, the
           range, was the part the reader had to compare across boxes to find. One row per month
           puts it in a column that reads down. */
        .forecast-banner { position:relative; overflow:hidden; background:linear-gradient(180deg,#fff 0%,#fbfdff 100%); border:1px solid #d7e3ef; border-radius:15px; padding:1.15rem 1.35rem 1.25rem; box-shadow:0 7px 22px rgba(28,62,96,.08); }
        .forecast-banner:before { content:""; position:absolute; left:0; top:0; right:0; height:4px; background:linear-gradient(90deg,#008fa3,#0875d1); }
        .forecast-banner .banner-head { display:flex; justify-content:space-between; align-items:baseline; gap:1rem; }
        .forecast-banner .banner-title { font-size:1.12rem; font-weight:780; color:#173a5e; }
        .forecast-banner .banner-date { font-size:.86rem; font-weight:700; color:#71869a; }
        .forecast-banner .banner-figures { display:flex; gap:2.6rem; margin:.95rem 0 1.05rem; align-items:flex-end; }
        /* The move takes the band's colours, not the card's: red is the upside here, because on a
           raw material the high side is the one that costs money. */
        .forecast-banner .banner-move { font-size:.94rem; font-weight:750; padding-bottom:.18rem; font-variant-numeric:tabular-nums; }
        .forecast-banner .banner-move.up { color:#c43c3c; } .forecast-banner .banner-move.down { color:#1b6a8c; }
        .forecast-banner .figure .label { font-size:.72rem; color:#67809a; text-transform:uppercase; letter-spacing:.04em; }
        .forecast-banner .figure .value { font-size:1.72rem; font-weight:800; color:#071c33; letter-spacing:-.02em; line-height:1.2; }
        .forecast-banner .banner-bands { display:flex; flex-direction:column; gap:.95rem; }
        .forecast-banner .band-label { font-size:.74rem; font-weight:700; color:#67809a; text-transform:uppercase; letter-spacing:.04em; margin-bottom:.3rem; }
        .forecast-banner .band-top { display:flex; font-size:.86rem; font-weight:700; color:#1b3b5b; font-variant-numeric:tabular-nums; margin-bottom:.22rem; }
        .forecast-banner .band-top-left { display:flex; justify-content:space-between; gap:.5rem; min-width:0; }
        .forecast-banner .band-top-right { text-align:right; min-width:0; }
        .forecast-banner .band-at { font-weight:700; color:#67809a; text-transform:uppercase; font-size:.72rem; letter-spacing:.04em; }
        .forecast-banner .band-bar { display:flex; height:2.1rem; border-radius:7px; overflow:hidden; }
        .forecast-banner .band-down, .forecast-banner .band-up { display:flex; align-items:center; justify-content:center; color:#fff; font-weight:750; font-size:.9rem; font-variant-numeric:tabular-nums; white-space:nowrap; }
        .forecast-banner .band-down { background:#1b6a8c; }
        .forecast-banner .band-up { background:#d92b2b; border-left:2px solid #fff; }
        .forecast-banner .band-none { font-size:.9rem; color:#67809a; }
        .forecast-banner .banner-table { width:100%; border-collapse:collapse; font-size:.95rem; }
        .forecast-banner .banner-table th { font-size:.74rem; font-weight:700; color:#67809a; text-transform:uppercase; letter-spacing:.04em; padding:.4rem .55rem; border-bottom:1px solid #e3ebf2; }
        .forecast-banner .banner-table td { padding:.52rem .55rem; border-bottom:1px solid #f1f5f9; color:#1b3b5b; }
        .forecast-banner .banner-table tr:last-child td { border-bottom:none; }
        .forecast-banner .banner-table .month { font-weight:720; color:#173a5e; }
        .forecast-banner .banner-table .num { text-align:right; font-variant-numeric:tabular-nums; }
        .forecast-banner .banner-trials { margin-top:.55rem; font-size:.78rem; color:#67809a; }
        .market-summary { background:white; border:1px solid #d7e3ef; border-radius:15px; padding:1rem 1.05rem; box-shadow:0 7px 22px rgba(28,62,96,.08); }
        .summary-title { font-weight:800; font-size:1.08rem; margin-bottom:.8rem; color:#0b1f33; }
        .summary-kpi { background:linear-gradient(135deg,#eaf4fb,#f6fbfe); border-left:4px solid #008da2; border-radius:10px; padding:.78rem; margin-bottom:.72rem; }
        .summary-kpi .label { font-size:.72rem; color:#67809a; text-transform:uppercase; letter-spacing:.04em; }
        .summary-kpi .big { font-size:1.52rem; font-weight:800; color:#0b1f33; }
        .summary-row { display:flex; justify-content:space-between; gap:1rem; padding:.44rem 0; border-bottom:1px solid #edf1f5; font-size:.82rem; }
        .summary-row .label { color:#71869a; } .summary-row .value { color:#1b3b5b; font-weight:680; text-align:right; }
        .direction-panel { margin:.75rem 0 .2rem; padding:.72rem .8rem; background:#f7fbfd; border-radius:10px; border:1px solid #e2edf3; }
        .direction-label { font-size:.7rem; color:#6c8195; text-transform:uppercase; letter-spacing:.04em; }
        .direction-value { margin-top:.12rem; font-size:1rem; font-weight:780; color:#0d4667; }
        .driver-panel { margin-top:.72rem; padding-top:.68rem; border-top:1px solid #e7eef4; }
        .driver-title { font-size:.82rem; font-weight:760; color:#183c5f; }
        .driver-empty { margin-top:.3rem; color:#75889a; font-size:.76rem; line-height:1.35; }

        .insight-card { background:#fff; border:1px solid #d7e3ef; border-radius:15px; padding:.95rem 1rem; box-shadow:0 5px 16px rgba(28,62,96,.05); }
        .insight-card .title { font-weight:780; color:#173a5e; margin-bottom:.55rem; }
        .insight { margin:.34rem 0; color:#334e68; font-size:.86rem; } .muted { color:#728599; font-size:.76rem; }
        .app-footer { margin-top:1.2rem; padding:.62rem 1rem; background:#071d34; color:#b8cbdc; border-radius:8px; font-size:.72rem; display:flex; justify-content:space-between; gap:1rem; }

        .current-market-hero {
            position:relative; overflow:hidden; margin:.35rem 0 .8rem; padding:1.35rem 1.55rem;
            background:radial-gradient(circle at 88% 18%, rgba(0,184,206,.13), transparent 28%),
                       linear-gradient(120deg,#061a2f 0%,#0a3157 66%,#0b3b62 100%);
            border:1px solid #174c73; border-radius:16px; box-shadow:0 10px 28px rgba(8,42,74,.15);
            color:white;
        }
        .current-market-hero:before { content:""; position:absolute; left:0; top:0; bottom:0; width:5px; background:linear-gradient(180deg,#00bfd6,#0a6ed1); }
        .current-market-kicker { color:#8fdce7; font-size:.72rem; font-weight:800; letter-spacing:.075em; text-transform:uppercase; }
        .current-market-price { margin-top:.24rem; color:#fff; font-size:2.6rem; line-height:1.02; font-weight:820; letter-spacing:-.035em; }
        .current-market-identity { margin-top:.48rem; color:#c6d9e9; font-size:.84rem; }
        .current-market-status { display:inline-block; margin-top:.68rem; padding:.22rem .58rem; border-radius:999px; background:rgba(0,190,214,.16); border:1px solid rgba(91,230,243,.34); color:#dffbff; font-size:.7rem; font-weight:800; letter-spacing:.035em; }
        .current-market-unavailable { background:linear-gradient(180deg,#fff,#f9fbfd); color:#10253c; border-color:#d7e3ef; }
        .current-market-unavailable .current-market-kicker { color:#6f8498; }
        .current-market-unavailable .current-market-price { color:#173a5e; font-size:1.65rem; }
        .current-market-unavailable .current-market-identity { color:#6f8498; }
        .current-market-support { background:#fff; border:1px solid #d7e3ef; border-radius:14px; padding:.95rem 1rem; box-shadow:0 5px 16px rgba(28,62,96,.05); min-height:126px; }
        .current-market-support .title { color:#6b8195; font-size:.7rem; font-weight:800; letter-spacing:.055em; text-transform:uppercase; }
        .current-market-support .value { margin-top:.28rem; color:#173a5e; font-size:1.18rem; font-weight:800; }
        .current-market-support .copy { margin-top:.34rem; color:#71869a; font-size:.78rem; line-height:1.35; }

        .collection-progress-panel {
            display:flex; align-items:center; justify-content:center; gap:1.25rem;
            min-height:142px; margin:.1rem 0 .48rem; padding:.48rem 1.1rem;
            background:radial-gradient(circle at 50% -30%, rgba(0,183,207,.10), transparent 52%),
                       linear-gradient(180deg,rgba(255,255,255,.96),rgba(246,250,253,.98));
            border:1px solid #d7e3ed; border-radius:13px;
            box-shadow:inset 0 1px 0 rgba(255,255,255,.9), 0 8px 22px rgba(27,59,91,.06);
        }
        .collection-instrument { --pct:0; position:relative; width:124px; height:124px; flex:0 0 124px; }
        .collection-gauge {
            position:absolute; inset:0; border-radius:50%;
            background:
                radial-gradient(circle at 37% 30%, rgba(255,255,255,.78) 0 3%, transparent 18%),
                radial-gradient(circle at 50% 50%, #142b41 0 43%, #071b2e 44% 52%, transparent 53%),
                conic-gradient(from -135deg,
                    #00b8ce 0deg calc(var(--pct) * 2.7deg),
                    #27445c calc(var(--pct) * 2.7deg) 270deg,
                    #d8e2e9 270deg 360deg);
            border:7px solid #c8d3dc;
            box-shadow:inset 0 0 0 2px #f8fbfd, inset 0 0 19px rgba(0,0,0,.48),
                       0 5px 14px rgba(13,39,62,.22), 0 0 0 1px #8799a8;
        }
        .collection-gauge:before {
            content:""; position:absolute; inset:9px; border-radius:50%;
            background:repeating-conic-gradient(from -135deg, rgba(224,239,247,.88) 0deg 1deg, transparent 1deg 13.5deg);
            -webkit-mask:radial-gradient(circle, transparent 0 68%, #000 69% 75%, transparent 76%);
            mask:radial-gradient(circle, transparent 0 68%, #000 69% 75%, transparent 76%);
            opacity:.8;
        }
        .collection-needle {
            position:absolute; left:50%; top:50%; width:46px; height:3px; border-radius:3px;
            background:linear-gradient(90deg,#f5fbff 0%,#00c3db 72%,#69f0ff 100%);
            transform-origin:0 50%; transform:rotate(calc(135deg + (var(--pct) * 2.7deg)));
            transition:transform .65s cubic-bezier(.2,.75,.25,1);
            box-shadow:0 0 7px rgba(0,205,230,.95); z-index:4;
        }
        .collection-needle:before {
            content:""; position:absolute; width:12px; height:12px; border-radius:50%; left:-6px; top:-4.5px;
            background:radial-gradient(circle at 35% 30%,#e9f5fb,#657f94 48%,#153047 72%);
            border:1px solid #dbe8ef; box-shadow:0 2px 5px rgba(0,0,0,.38);
        }
        .collection-readout {
            position:absolute; left:50%; bottom:21px; transform:translateX(-50%); z-index:5;
            min-width:50px; padding:.08rem .28rem; text-align:center; border-radius:4px;
            background:rgba(2,13,24,.90); border:1px solid rgba(91,220,235,.35);
            color:#dffbff; font:700 .78rem/1.35 ui-monospace,SFMono-Regular,Consolas,monospace;
            letter-spacing:.02em; box-shadow:inset 0 0 10px rgba(0,188,210,.12);
        }
        .collection-progress-copy { min-width:210px; max-width:430px; }
        .collection-progress-title { color:#173a5e; font-size:.94rem; font-weight:780; letter-spacing:.01em; }
        .collection-progress-sub { color:#70869a; font-size:.76rem; margin-top:.2rem; }
        .collection-progress-line { height:6px; margin-top:.54rem; border-radius:999px; overflow:hidden; background:#dce7ee; }
        .collection-progress-line > span {
            display:block; height:100%; width:calc(var(--pct) * 1%); border-radius:inherit;
            background:linear-gradient(90deg,#008da2,#00bfd6,#0a6ed1); transition:width .65s ease;
            box-shadow:0 0 8px rgba(0,168,195,.42);
        }
        .collection-progress-panel.running .collection-gauge { animation:collection-glow 2.1s ease-in-out infinite; }
        @keyframes collection-glow {
            0%,100% { filter:drop-shadow(0 0 0 rgba(0,190,214,0)); }
            50% { filter:drop-shadow(0 0 5px rgba(0,190,214,.22)); }
        }

        @media (max-width:1050px) {
            .brand-header { grid-template-columns:1fr; text-align:center; }
            .brand-left { justify-content:center; } .brand-center { border:0; padding:.3rem 0; }
            .brand-right { grid-template-columns:1fr 82px; max-width:380px; margin:0 auto; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

def render_brand_header() -> None:
    htc = image_data_uri(HTC_LOGO)
    obs = image_data_uri(PU_OBSERVATORY_LOGO)
    st.markdown(
        f"""
        <div class="brand-header">
          <div class="brand-left">
            <img src="{htc}" alt="HTC Global">
            <div class="brand-copy">
              <div class="brand-name">HTC Global</div>
              <div class="brand-tagline">Polyurethane business guidance and intelligence</div>
            </div>
          </div>
          <div class="brand-center">
            <div class="title">PU Raw Material Forecasting</div>
            <div class="strap">Market intelligence for polyurethane raw-material decisions</div>
            <div class="brand-version">v{VERSION}{_an_older_engine()}</div>
          </div>
          <div class="brand-right">
            <div class="copy">
              <div class="name">PU Observatory</div>
              <div class="engine">Market Intelligence Engine</div>
            </div>
            <img src="{obs}" alt="PU Observatory">
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def load_collection_progress() -> tuple[int, str]:
    percent, state, _payload = load_collection_progress_payload()
    return percent, state


def load_collection_progress_payload() -> tuple[int, str, dict]:
    try:
        payload = json.loads(STEP6_COLLECTION_STATUS_PATH.read_text(encoding="utf-8"))
        percent = max(0, min(100, int(payload.get("percent", 0))))
        state = str(payload.get("state") or "running")
        return percent, state, payload
    except (OSError, ValueError, TypeError):
        return 0, "running", {}


SLOW_SOURCE_MINUTES = 3


def _stalled_for_minutes(payload: dict) -> float:
    """How long the source now being collected has been running. Zero when nothing is running."""
    if not payload.get("in_progress"):
        return 0.0
    since = payload.get("in_progress_since")
    if not since:
        return 0.0
    try:
        started = datetime.fromisoformat(str(since))
    except (TypeError, ValueError):
        return 0.0
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - started).total_seconds() / 60)


@st.cache_data(ttl=2, show_spinner=False)
def _forecast_readiness() -> dict:
    """How far the engine has got preparing the forecasts. Asked of the engine, not of a helper.

    Stefan, 23 September 2026: the gauge is what tells a reader the application is ready to use, so
    it reads the work from whoever is doing it. The helper that used to prepare the forecasts over
    HTTP stopped answering twice in one morning, and the needle stood still over an application that
    worked perfectly.
    """
    try:
        return MarketDataEngineClient(MARKET_DATA_ENGINE_URL, timeout=2.0).forecast_readiness()
    except MarketDataEngineUnavailable:
        return {}


def _draw_gauge_panel(percent: int, title: str, subtitle: str, *, complete: bool) -> None:
    """The instrument itself, so one panel can be read by two different things.

    A machine that collects reads a run in progress. A published deployment collects nothing and
    reads what the store already holds. Same needle, same words under it, one piece of markup.
    """
    panel_state = "complete" if complete else "running"
    st.markdown(
        f"""
        <div class="collection-progress-panel {panel_state}" style="--pct:{percent}">
          <div class="collection-instrument" style="--pct:{percent}">
            <div class="collection-gauge"></div>
            <div class="collection-needle"></div>
            <div class="collection-readout">{percent}%</div>
          </div>
          <div class="collection-progress-copy" style="--pct:{percent}">
            <div class="collection-progress-title">{title}</div>
            <div class="collection-progress-sub">{subtitle}</div>
            <div class="collection-progress-line"><span></span></div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _newest_published_price_day() -> tuple[bool, str | None]:
    """Whether the engine answered, and the newest priced reading day in the store.

    Read off the index snapshot the market page loads anyway - four fields per reading and no
    prices, just whether each one carries a price - so this costs no egress of its own.
    """
    snapshot = _load_step6_snapshot(readings="index")
    if snapshot is None:
        return False, None
    days = [str(row.get("observation_date") or row.get("publication_date") or "")
            for row in getattr(snapshot, "observations", None) or []
            if row.get("price") is not None]
    days = [day for day in days if day]
    return True, max(days) if days else None


def _render_published_store_gauge() -> None:
    """The gauge on a deployment that collects nothing itself.

    The gauge was built for a machine that collects: the daily run writes its progress beside the
    store and the needle follows it. A published deployment has no such run - `ENGINE_HOUSEKEEPING`
    and `FORECAST_WARM_UP` are off there by `deployment_profile`, and nothing writes the progress
    file - so the needle read a file that was not there as "nought per cent, still running" and
    announced "Refreshing current market evidence in the background" above a store that was already
    complete.

    Stefan, 23 November 2026, signed in as the owner on the first live deployment: *"the gauge is at
    0%, how does this match with your test of the Duroflex account?"* It did not match it at all.
    The customer page drew a real price out of Supabase while the needle above it said the data had
    not arrived yet.

    So on a published deployment the needle answers the question a reader there actually has: how
    current is the data I am looking at. HTC Global collects into the store; this deployment reads it.
    """
    answered, newest = _newest_published_price_day()
    if not answered:
        _draw_gauge_panel(0, "Market data loading",
                          "The market data is still being read. Reload the page in a moment.",
                          complete=False)
        return
    if not newest:
        _draw_gauge_panel(0, "No market prices published yet",
                          "The store this deployment reads holds no priced market yet.",
                          complete=False)
        return
    _draw_gauge_panel(100, "Market data up to date",
                      f"Newest price in the store: {_pretty_date(newest)}. "
                      "New prices appear here within fifteen minutes of being published.",
                      complete=True)


@st.fragment(run_every="1s")
def render_collection_progress_gauge() -> None:
    if HOSTED_DEPLOYMENT:
        # Nothing collects on a published deployment, so there is no run for a needle to follow.
        _render_published_store_gauge()
        return
    percent, state, payload = load_collection_progress_payload()
    complete = state == "complete" and percent >= 100
    title = "Market data updated" if complete else "Updating market data"
    subtitle = "Market data refresh completed." if complete else "Refreshing current market evidence in the background."
    if not complete and _stalled_for_minutes(payload) >= SLOW_SOURCE_MINUTES:
        # A needle that moves says the run is alive. It does not say a source has stopped answering,
        # and a reader watching a live gauge sit still deserves to be told which of the two it is.
        subtitle = "Still refreshing. One source is slow to answer."
    if complete:
        attempted = int(payload.get("attempted") or 0)
        retrieved = int(payload.get("data_retrieved") or 0)
        records = int(payload.get("records_retrieved") or 0)
        if attempted == 0:
            title = "Market data up to date"
            subtitle = "Already checked today; the next check runs at the first start tomorrow."
        else:
            subtitle = f"{attempted} checked today, {retrieved} delivered new data ({records} records)."

    # The forecasts are the last thing between the reader and a usable page, so the gauge covers
    # them: the collection carries the needle to 90, preparing the forecasts carries it the rest of
    # the way (Stefan, 23 September 2026).
    readiness = _forecast_readiness()
    if not complete:
        # Collection owns the first stretch and stops at 90, so the needle never has to come back
        # down when the forecasts take over (Stefan, 23 September 2026: it read 98, then 28).
        percent = min(percent, 90)
    elif readiness.get("running"):
        prepared = int(readiness.get("prepared") or 0)
        total = int(readiness.get("total") or 0)
        percent = min(99, 90 + round(9 * prepared / total)) if total else 90
        complete = False
        title = "Preparing forecasts"
        subtitle = "Almost ready."

    _draw_gauge_panel(percent, title, subtitle, complete=complete)
    # When the run finishes, draw the page again - once.
    #
    # Stefan, 7 October 2026: "After the data has loaded, I need to refresh every time to get the
    # screen to show the current market price." This gauge is a fragment, which means it is the only
    # thing on the page that reruns on its own. Everything around it - the price, the chart, the
    # banner - was drawn once, before the collection had finished, and Streamlit had no reason to
    # draw it again. So the gauge announced "Market data updated" over the price from before the
    # update, and only F5 told the truth.
    #
    # One rerun of the whole page, keyed to the run that just finished, so a second collection later
    # in the day does it again while a gauge sitting at complete does not rerun the page every
    # second it ticks.
    if complete:
        signature = "|".join(str(payload.get(key)) for key in
                             ("updated_at", "attempted", "data_retrieved", "records_retrieved"))
        if st.session_state.get("_collection_drawn_for") != signature:
            st.session_state["_collection_drawn_for"] = signature
            st.rerun(scope="app")



def canonical_identity(df: pd.DataFrame) -> str:
    cols = ["product_family", "grade", "origin", "destination", "price_basis", "packaging", "shipment_size", "currency"]
    return " | ".join(str(df.iloc[0][c]) for c in cols)


def horizon_matrix(df: pd.DataFrame, audit) -> pd.DataFrame:
    publication_lag_verified = "publication dates unverified" not in audit.reasons
    weekly_continuity = has_continuous_weekly_observations(df)
    regime_count = int(df["regime"].nunique()) if "regime" in df.columns else 0
    rows = []
    for weeks in (4, 8, 12):
        gate = evaluate_horizon_gate(
            observation_count=audit.observation_count,
            context_observations=24,
            horizon_observations=weeks,
            regime_count=regime_count,
            publication_lag_verified=publication_lag_verified,
            significance_passed=None,
            operational_rights_clear=audit.operational_rights_clear,
            target_frequency_continuous=weekly_continuity,
        )
        rows.append({
            "Horizon": f"{weeks} weeks",
            "Status": gate.status,
            "Independent episodes": gate.independent_episodes,
            "Reason": "; ".join(gate.reasons) if gate.reasons else "Gate passed",
        })
    return pd.DataFrame(rows)


def direction_markup(delta_pct: float) -> tuple[str, str]:
    if delta_pct > 0.05:
        return "up", f"↑ +{delta_pct:.1f}% vs. latest"
    if delta_pct < -0.05:
        return "down", f"↓ {delta_pct:.1f}% vs. latest"
    return "flat", f"→ {delta_pct:+.1f}% vs. latest"


def render_forecast_card(weeks: int, value: float, latest: float, currency: str, status: str) -> None:
    delta_pct = ((value / latest) - 1) * 100 if latest else 0.0
    direction_class, direction_text = direction_markup(delta_pct)
    st.markdown(
        f"""
        <div class="forecast-card">
          <div class="card-top">
            <div class="card-title">{HORIZON_TITLES[weeks]}</div>
            <div class="status">{status}</div>
          </div>
          <div class="value">{currency} {value:,.0f} / {units.MT}</div>
          <div class="delta {direction_class}">{direction_text}</div>
          <div class="meta">Research projection from the current validated series.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def research_uncertainty_band(df: pd.DataFrame, forecast_series: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Create a transparent research-only uncertainty heuristic.

    It uses observed weekly volatility and widens with sqrt(horizon). It is not a
    calibrated probabilistic interval and is labelled accordingly in the UI.
    """
    weekly = normalize_weekly_price_series(df)
    volatility = float(weekly.pct_change().dropna().std()) if len(weekly) > 2 else 0.0
    volatility = max(volatility, 0.01)
    lower, upper = [], []
    for idx, value in enumerate(forecast_series.astype(float), start=1):
        width = min(0.45, 1.64 * volatility * math.sqrt(idx))
        lower.append(max(0.0, value * (1 - width)))
        upper.append(value * (1 + width))
    return pd.Series(lower, dtype=float), pd.Series(upper, dtype=float)


def build_forecast_figure(df: pd.DataFrame, forecast_series: pd.Series, currency: str) -> go.Figure:
    history = df[["observation_date", "price_per_tonne"]].copy().sort_values("observation_date")
    history["observation_date"] = pd.to_datetime(history["observation_date"])
    forecast = build_user_forecast_frame(history["observation_date"].iloc[-1], forecast_series)
    lower, upper = research_uncertainty_band(df, forecast_series)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=history["observation_date"], y=history["price_per_tonne"],
        mode="lines+markers", name="Observed price",
        line=dict(color="#0a6ed1", width=3), marker=dict(size=6),
        hovertemplate="%{x|%d %b %Y}<br>Observed: %{y:,.0f} " + currency + "/" + units.MT + "<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=forecast["date"], y=upper, mode="lines", line=dict(width=0),
        showlegend=False, hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=forecast["date"], y=lower, mode="lines", line=dict(width=0),
        fill="tonexty", fillcolor="rgba(0,141,162,0.14)",
        name="Confidence range (research heuristic)", hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=forecast["date"], y=forecast["Forecast"], mode="lines+markers", name="Forecast",
        line=dict(color="#008da2", width=3, dash="dash"), marker=dict(size=6),
        hovertemplate="%{x|%d %b %Y}<br>Forecast: %{y:,.0f} " + currency + "/" + units.MT + "<extra></extra>",
    ))
    cutoff = history["observation_date"].iloc[-1]
    fig.add_vline(x=cutoff, line_width=1, line_dash="dot", line_color="#7890a5")
    fig.add_annotation(x=cutoff, y=1.03, yref="paper", text="Forecast starts", showarrow=False, font=dict(size=11, color="#657b90"))
    fig.update_layout(
        height=455, margin=dict(l=18, r=18, t=28, b=18),
        paper_bgcolor="white", plot_bgcolor="white",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.03, xanchor="left", x=0),
        # One tick per month here too, so the two charts on the two pages read the same way.
        xaxis=dict(showgrid=True, gridcolor="rgba(148,163,184,0.18)", linecolor="#d9e4ed",
                   dtick="M1", tickformat="%b %y", ticks="outside", ticklabelmode="period",
                   tickangle=-60, tickfont=dict(size=10)),
        yaxis=dict(title=units.per_unit(currency), gridcolor="#edf2f6", zeroline=False),
        font=dict(color="#223b53"),
    )
    return fig


def render_market_summary(df: pd.DataFrame, audit, source_label: str, latest: float, horizon_status: dict[str, str], forecast_series: pd.Series) -> None:
    latest_date = str(audit.end_date or "-")
    freshness = f"{audit.freshness_days or 0} days"
    currency = str(df.iloc[0]["currency"])
    worst_status = "RESEARCH" if "RESEARCH" in horizon_status.values() else audit.release_status
    four_week = float(forecast_series.iloc[3])
    delta_pct = ((four_week / latest) - 1) * 100 if latest else 0.0
    if delta_pct > 0.5:
        direction = f"Upward  ↑  {delta_pct:+.1f}% at 4 weeks"
    elif delta_pct < -0.5:
        direction = f"Downward  ↓  {delta_pct:+.1f}% at 4 weeks"
    else:
        direction = f"Broadly stable  →  {delta_pct:+.1f}% at 4 weeks"
    st.markdown(
        f"""
        <div class="market-summary">
          <div class="summary-title">Market Intelligence</div>
          <div class="summary-kpi">
            <div class="label">Last observed market price</div>
            <div class="big">{currency} {latest:,.0f} / {units.MT}</div>
          </div>
          <div class="summary-row"><span class="label">Latest observation</span><span class="value">{latest_date}</span></div>
          <div class="summary-row"><span class="label">Freshness</span><span class="value">{freshness}</span></div>
          <div class="summary-row"><span class="label">Source</span><span class="value">{source_label}</span></div>
          <div class="summary-row"><span class="label">Market status</span><span class="value">{worst_status}</span></div>
          <div class="summary-row"><span class="label">Observations</span><span class="value">{audit.observation_count}</span></div>
          <div class="direction-panel">
            <div class="direction-label">Modelled market direction</div>
            <div class="direction-value">{direction}</div>
          </div>
          <div class="driver-panel">
            <div class="driver-title">Verified market drivers</div>
            <div class="driver-empty">No forecast-active verified market drivers yet. Discovered information remains separate until source, timing and review controls are satisfied.</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

def render_key_insights(df: pd.DataFrame, forecast_series: pd.Series) -> None:
    ordered = df.sort_values("observation_date")
    latest = float(ordered.iloc[-1]["price_per_tonne"])
    first = float(ordered.iloc[0]["price_per_tonne"])
    observed_change = ((latest / first) - 1) * 100 if first else 0.0
    four_week = float(forecast_series.iloc[3])
    twelve_week = float(forecast_series.iloc[11])
    four_change = ((four_week / latest) - 1) * 100 if latest else 0.0
    twelve_change = ((twelve_week / latest) - 1) * 100 if latest else 0.0
    st.markdown(
        f"""
        <div class="insight-card">
          <div class="title">Market Insights</div>
          <div class="insight">• Observed series change from first to latest point: <b>{observed_change:+.1f}%</b>.</div>
          <div class="insight">• Current 4-week research projection versus latest observed price: <b>{four_change:+.1f}%</b>.</div>
          <div class="insight">• Current 12-week research projection versus latest observed price: <b>{twelve_change:+.1f}%</b>.</div>
          <div class="muted">These are calculated from the loaded series and current research projection. Market-driver intelligence is added separately as verified evidence becomes available.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# What conversion actually does, said in one place because it is said on two screens and because
# the old wording was not quite true (masterplan E, measured; said on the screen by B, v1.2.9).
#
# The rate of the reading's own day is used where the ECB published one: 89% of readings in USD,
# 82% in EUR, 73% in CNY, 72% in THB. The rest fall back, and 59% of the CNY fallbacks are
# Saturdays and Sundays - there is no rate on a Saturday because there is no market on a Saturday,
# which is correct behaviour and not a gap. Measured over all 35,426 priced readings: the furthest
# any reading reaches back for a rate is FOUR DAYS, in every one of the four currencies. So the
# claim on the screen is the bound, not the ideal.
FX_RULE = ("Converted at the ECB rate of each reading's own date, or - where the market was shut "
           "that day - the last rate published before it, never more than four days earlier.")


def display_currency() -> str:
    return str(st.session_state.get("display_currency") or DEFAULT_DISPLAY_CURRENCY)


def render_display_currency_selector() -> str:
    if st.session_state.get("display_currency") not in DISPLAY_CURRENCIES:
        st.session_state["display_currency"] = DEFAULT_DISPLAY_CURRENCY
    return str(
        st.selectbox(
            "Display currency",
            _alphabetical(DISPLAY_CURRENCIES),
            key="display_currency",
            help=FX_RULE + " Evidence is stored in the publisher's currency.",
        )
    )


def _load_fx_table() -> FxTable:
    """ECB daily rates from the engine (cached per session run; empty table when the engine has none)."""
    # Only a table with rates in it is kept. An empty one used to be cached the same way, so a
    # single slow answer during warm-up left the whole session with no rates and no way back -
    # which is how a yuan price reached the Current Market card with USD in front of it on
    # 27 September 2026. Readings no longer come through here at all; references still do.
    cached = st.session_state.get("_fx_table")
    if isinstance(cached, FxTable) and cached.available:
        return cached
    try:
        # The rate table is a body of data, not an availability check: 16,295 daily fixings, asked
        # for while the engine is still warming its forecasts. It gets the data timeout.
        payload = MarketDataEngineClient(MARKET_DATA_ENGINE_URL, timeout=6.0).fx_rates(
            tuple(c for c in DISPLAY_CURRENCIES if c != "EUR"))
        table = FxTable.from_rows(payload.get("rates") or [], base=str(payload.get("base") or "EUR"))
    except MarketDataEngineUnavailable:
        return FxTable()
    if table.available:
        st.session_state["_fx_table"] = table
    return table


# Fifteen minutes, raised from one on 13 October 2026. The stamp below already re-reads the moment
# a collection lands readings; the expiry only catches imports, which write no progress file. With
# the customs history the dataset is 40,308 readings and 31.8 MB, and a one-minute expiry fetched it
# again for every reader who clicked after a minute.
SNAPSHOT_SECONDS = 900


def _snapshot_stamp() -> str:
    """What makes the engine's answer different from the one before it, asked of no one.

    Streamlit runs the whole script again on every click, so a page with four selectors on it used
    to fetch and convert the entire market dataset four times over - 18,306 readings, 15.9 MB,
    measured on 12 October 2026 - to work out what the next selector could offer. The answer is
    kept between runs instead, and this line is how the application knows the kept one is still the
    current one: the collection run writes its progress beside the store, so a run that lands new
    readings changes the stamp and the next run reads the engine again.

    Readings can also arrive from an import, which writes no progress file, so the kept answer also
    expires on its own after SNAPSHOT_SECONDS.
    """
    # Only what changes when readings land. `updated_at` and `attempted` moved on every source the
    # collector so much as started, so for the forty minutes of a collection run every click fetched
    # the whole dataset again, including after the 26 trade sources that saved nothing
    # (13 October 2026).
    _percent, state, payload = load_collection_progress_payload()
    return "|".join([state] + [str(payload.get(key)) for key in
                               ("data_retrieved", "records_retrieved")])


# The last snapshot that actually carried readings, per display currency, and when it was read.
# See `_load_step6_snapshot`. One entry per currency; the engine's answer is about 28 MB.
_LAST_GOOD_SNAPSHOT: dict[tuple[str, str], tuple[object, datetime]] = {}
_SERVING_A_KEPT_SNAPSHOT: dict[str, object] = {"since": None}

# How old a kept snapshot may be before the page says so out loud.
KEPT_SNAPSHOT_NOTE_MINUTES = 20


def snapshot_age_to_report() -> float | None:
    """Minutes since the snapshot on screen was read, when it is a kept one worth mentioning."""
    since = _SERVING_A_KEPT_SNAPSHOT.get("since")
    if not isinstance(since, datetime):
        return None
    minutes = (datetime.now(timezone.utc) - since).total_seconds() / 60
    return minutes if minutes >= KEPT_SNAPSHOT_NOTE_MINUTES else None


def _load_step6_snapshot(readings: str = "full"):
    """Engine snapshot converted to the selected display currency (evidence itself stays in source currency).

    `readings="index"` asks for the four fields the dropdowns are decided by rather than every
    reading with its price - 1.5 MB against 15.2, measured 30 October 2026 - and skips the currency
    conversion entirely. A caller that draws NUMBERS asks for "full" and gets what it always got.
    The index's prices come back as 1, never a value, so a page that asks for the wrong one fails
    where it stands instead of charting a flat line at 1.

    A read that comes back without readings returns the last one that had them.

    Measured 30 October 2026: `/observations` is 35,515 readings and 27.9 MB, about seven seconds
    of real work, and 25 to 41 seconds while the forecast warm-up is running. Over the client's
    data timeout it came back empty, this function returned None **and cleared the cache**, and so
    the next click paid the whole forty seconds again - for as long as the warm-up lasted. The
    fifteen-minute cache never got the chance to help. Stefan, 30 October 2026: "just switching
    between chemicals in the current market takes too much time."

    Clearing the cache is still right - a None must not be kept for fifteen minutes - but throwing
    away the last GOOD answer with it was not. A snapshot a few minutes old is a far better answer
    than "Market data is being collected" on a page whose data arrived hours ago, and the page says
    so once it is old enough to matter.
    """
    # The rate table is part of what the kept answer was built from, so it is part of the stamp:
    # rates that arrive after a snapshot was converted without them make the kept one wrong, and
    # it is read again rather than shown for the rest of the minute.
    table = _load_fx_table()
    stamp = f"{_snapshot_stamp()}|{table.available}|{table.latest_date}"
    currency = display_currency()
    if readings == "index":
        # The index carries no prices, so there is nothing to convert and nothing the display
        # currency changes. One kept answer for every currency instead of one each.
        currency = "-"
    snapshot = _step6_snapshot(currency, stamp, readings, _table=table)
    if snapshot is None:
        # Silence is not worth keeping. An engine still warming up answers properly a second
        # later, and a page holding on to the silence would go on saying the data is coming after
        # it had arrived.
        _step6_snapshot.clear()
        kept = _LAST_GOOD_SNAPSHOT.get((currency, readings))
        if kept is not None:
            _SERVING_A_KEPT_SNAPSHOT["since"] = kept[1]
            return kept[0]
        return None
    _LAST_GOOD_SNAPSHOT[(currency, readings)] = (snapshot, datetime.now(timezone.utc))
    _SERVING_A_KEPT_SNAPSHOT["since"] = None
    return snapshot


@st.cache_data(ttl=SNAPSHOT_SECONDS, max_entries=6, show_spinner=False)
def _step6_snapshot(currency: str, stamp: str, readings: str = "full", *, _table):
    """The snapshot itself, read once per currency and per stamp rather than once per click.

    This took a `reference_cutoff` that nothing passed. It was last used by the v0.4.31 Current
    Market screen, and the only thing still naming it was a check describing that screen. The
    engine client keeps the capability - it is the engine's to offer - and the application no
    longer carries an argument it never fills.
    """
    try:
        snapshot = MarketDataEngineClient(MARKET_DATA_ENGINE_URL, timeout=1.5).snapshot(
            currency=None if readings == "index" else currency, readings=readings)
    except MarketDataEngineUnavailable:
        return None
    # Markets, but none of their readings: the engine is answering and has not handed the prices
    # over yet. `/observations` is the heaviest call in the snapshot - 17,297 rows and 13.8 MB on
    # 28 September 2026 - and when it overruns its data timeout the client returns an empty list and
    # records why in `snapshot.errors`, which no page reads. Every page then finds nothing drawable
    # and says so in its own words: Forecast told Stefan "No market is held for TDI yet" while TDI
    # held sixteen markets and a current price (0.4.266, 28 September 2026). A page handed nothing
    # says the data is still coming, which is the one thing that is true at that moment.
    if snapshot.identities and not snapshot.observations:
        return None
    return convert_snapshot(snapshot, _table, currency)


def render_display_currency_note(snapshot=None) -> None:
    currency = display_currency()
    table = _load_fx_table()
    if not table.available:
        st.caption(f"Display currency {currency}: exchange rates are not available yet, prices are shown in their source currency.")
        return
    st.caption(f"Prices shown in {currency}. {FX_RULE} Latest fixing {table.latest_date}.")


def _format_reference_value(value: float | None, currency: str | None) -> str:
    if value is None:
        return "Building"
    return units.with_unit(f"{currency or display_currency()} {value:,.0f}")


# Which model the page publishes.
#
# ETS sat here as the benchmark control while nothing had been qualified. The rolling-origin
# backtest settled it on 14 September 2026: scored on the origins every model answered, Last Month's
# Move carries the one-month horizon, and Stefan chose it. So the page shows what was chosen.
#
# ETS has not gone anywhere - it is still in the payload, still scored beside this one every time the
# backtest runs, and it is still what this has to beat to stay here.
CARD_MODEL = "momentum"
# The three horizons a market is forecast at, in calendar weeks: one, two and three months. The
# governed selection names a method PER horizon, so these are also the keys the stored answer
# comes back under.
_FORECAST_HORIZONS = (4, 8, 12)
# A market is forecast while it is still being priced (Stefan, 23 September 2026).
FORECAST_FROM_YEAR = 2026
# Europe TDI, pMDI and polyol: a weighted average of the last three months plus a share of last
# month's error, on the published series continued by the card's changes (Stefan, 22 September
# 2026). The engine answers it for those three markets only and declines everywhere else.
EUROPE_MODEL = "weighted_error"


def _card_model(payload: dict) -> str:
    """The model the page shows for this market, decided on this market's own record.

    The Europe chain method answers Europe and nothing else, so it keeps its place. Everywhere
    else the choice is made in `forecast_cards.card_model`: a candidate has to be ahead of the
    control on the control's own exam, or the control is what gets published.
    """
    return forecast_cards.card_model(payload, pinned=CARD_MODEL, chained=EUROPE_MODEL)


def _points_the_page_draws(payload: dict, model: dict | None, horizon: int) -> list[dict]:
    """The forecast points a reader is shown, out to `horizon` weeks. One list for every surface.

    THE CARDS READ THE GOVERNED ANSWER, SO THE LINE HAS TO READ IT TOO - and so does the download.
    When the cards started reading the stored governed forecast on 22 October 2026 and the chart
    did not, they drifted immediately: Adipic Acid Europe drew a flat line at the last price,
    because `card_model` had fallen back to the control, while the card beside it read the
    governed forecast. The chart was given the substitution the cards make.

    The download was not. `forecast_export_frame` went on listing the pinned model's own points,
    and the Word report is written from that frame. So for a governed market the page and the file
    were two different forecasts. Stefan, 23 November 2026, holding the file: "it shows only one
    month graph, and the numbers are sometimes double." Polyether Polyol China's report read
    January, January, February; the page it was downloaded from drew January, February, March. The
    doubling was real, and it was in the lane the page never showed him.

    Same substitution at the same chokepoint, in one function now: the governed point where there
    is one, this model's own point where there is not. And where the pinned model declined a
    market the governed job answered, the governed points stand alone - but only where the history
    is enough for a forecast at all.
    """
    points = [p for p in (model or {}).get("points", ())
              if int(p["horizon_weeks"]) <= horizon] if model else []
    points = [_card_forecast(payload, int(p["horizon_weeks"])) or p for p in points]
    if not points and _forecast_history_enough(payload):
        points = [found for weeks in _FORECAST_HORIZONS if weeks <= horizon
                  if (found := _card_forecast(payload, weeks)) is not None]
    return points


def _shown_day(payload: dict, point: dict | None):
    """The day this point is drawn on and dated with: see `forecast_cards.shown_day`.

    One function for the card, the chart and the download, so the three cannot disagree about when
    a forecast is for. Returns None when there is no point or it carries no period.
    """
    if not point or not point.get("period"):
        return None
    latest = (payload or {}).get("latest") or {}
    return forecast_cards.shown_day(
        point.get("period"), latest.get("date") or (payload or {}).get("as_of"),
        monthly=forecast_cards.answers_by_month(payload, point))


def _governed_point(payload: dict, weeks: int) -> dict | None:
    """The stored governed forecast, read by `forecast_cards.governed_point`."""
    return forecast_cards.governed_point(payload, weeks)


def _card_forecast(payload: dict, weeks: int, model_key: str | None = None) -> dict | None:
    # A caller that NAMES a model wants that model's own answer - the performance screens compare
    # one method against another, and handing them the governed number instead would compare a
    # method with itself. The governed answer is only what the page shows when nobody asked for a
    # particular method.
    asked_for_a_model = model_key is not None
    model_key = model_key or _card_model(payload)
    computed: dict | None = None
    for model in payload.get("models") or ():
        if model.get("model") != model_key or model.get("status") != "ok":
            continue
        for point in model.get("points") or ():
            if int(point.get("horizon_weeks") or 0) == weeks:
                computed = {**point, "model": model_key, "metadata": model.get("metadata") or {}}
                break
        break
    if asked_for_a_model:
        return computed
    governed = _governed_point(payload, weeks)
    if governed is None:
        return computed
    # Built on the computed point where there is one, so `steps`, `resolution` and the rest of the
    # shape the cards read keep their meaning, and only the numbers change. Where the pinned model
    # declined this market there is nothing to build on, and the governed point stands alone -
    # `forecast_cards` reads every structural key with a default.
    #
    # `calibration` is DROPPED rather than carried over. It is the measured error profile of the
    # model that was computed here, and applying it to a governed centre would put one method's
    # number inside another method's band. Without it `band_for` falls back to the governed
    # quantiles, which is the band the method in force actually produced.
    if computed is None:
        return governed
    merged = {**computed, **governed}
    merged.pop("calibration", None)
    return merged


def render_unreleased_forecast_card(weeks: int, payload: dict | None = None,
                                    currency: str = "", as_of: str | None = None) -> None:
    """One horizon card.

    The card leads with the RANGE, because that is what differs across the three horizons and what a
    buyer can act on. It led with a single figure, and on a level-only model that figure is identical
    at four, eight and twelve weeks - correct, and indistinguishable from a broken screen.

    What the card says is decided in `forecast_cards`, which is free of Streamlit so the wording can
    be rendered and read without starting the application.
    """
    point = _card_forecast(payload or {}, weeks)
    table = _load_fx_table()
    target = display_currency()
    stamp = as_of_date(as_of)

    def _show(value: float) -> str:
        converted = table.convert(float(value), currency, target, stamp) if currency else None
        amount, shown = (converted[0], target) if converted else (float(value), currency or target)
        return f"{shown} {amount:,.0f}"

    content = forecast_cards.card_content(
        point, weeks=weeks,
        anchor=(payload or {}).get("anchor_value"),
        show=_show,
        landing=_pretty_date(_shown_day(payload or {}, point)),
        # The last price anybody printed, so the card is not 3% behind a market that moved this week
        # with nothing on it saying so.
        latest=(payload or {}).get("latest"),
        # What the store says this series is in, rather than a literal on the card.
        unit=(payload or {}).get("unit"),
    )
    st.markdown(forecast_cards.card_html(content), unsafe_allow_html=True)


def _evidence_range(state) -> str:
    low, high = state.price_min, state.price_max
    if low is None or high is None:
        return "-"
    unit = state.currency or display_currency()
    return f"{low:,.0f}–{high:,.0f} {unit}"


def _pretty_date(value) -> str:
    """A date a buyer reads, rather than an ISO stamp."""
    from datetime import date as _date
    try:
        return _date.fromisoformat(str(value)[:10]).strftime("%-d %B %Y")
    except Exception:
        try:
            return _date.fromisoformat(str(value)[:10]).strftime("%d %B %Y").lstrip("0")
        except Exception:
            return str(value or "")


def as_of_date(value):
    """The as-of stamp as a date, for converting a forecast at the rate of the day it was made."""
    from datetime import date as _date
    try:
        return _date.fromisoformat(str(value)[:10])
    except Exception:
        return _date.today()


@st.cache_data(ttl=300, show_spinner=False)
def _load_transactions(chemical: str, origin: str = "") -> list[dict]:
    """KGI's own purchase history, for drawing beside the published price. PLATFORM ADMIN ONLY.

    `internal=True` is passed here and nowhere else. The engine withholds this lane from anyone who
    does not ask for it by name, so a screen that calls the client without thinking gets nothing -
    which is the behaviour that was missing when these prices reached a customer's Forecast page.

    Never allowed to break the chart: a stale engine has no such endpoint and simply draws one line.
    """
    try:
        return MarketDataEngineClient(MARKET_DATA_ENGINE_URL, timeout=6.0).transactions(
            chemical=chemical, origin=origin, internal=True)
    except MarketDataEngineUnavailable:
        return []


@st.cache_data(ttl=300, show_spinner=False)
def _load_market_history(chemical: str, geography: str, target: str) -> list[dict]:
    """The months the published European series holds, in the reader's currency.

    Not drawn any more: the series is the DDP market now. What still reads it is the history rule,
    which counts the months a chained Europe forecast reads (Stefan, 23 September 2026).
    """
    from raw_material_forecasting.market_history import history_lines

    client = MarketDataEngineClient(MARKET_DATA_ENGINE_URL, timeout=6.0)
    try:
        series = client.reference_series(internal=True)
    except MarketDataEngineUnavailable:
        series = []
    # The purchase lane is not read here any more. Those prices are a market of their own on DAP
    # (Stefan, 23 September 2026), so a customer page asks the engine for markets and never for the
    # internal lane.
    table = _load_fx_table()
    out = []
    for line in history_lines(series, chemical, geography):
        points = []
        for day, value in line["points"]:
            converted = (table.convert(value, line["currency"], target, date.fromisoformat(day))
                         if line["currency"] and table.available else None)
            if line["currency"] and line["currency"] != target and converted is None:
                continue            # no rate for that day: the point is left out, never guessed
            points.append((pd.to_datetime(day), converted[0] if converted else value))
        if len(points) >= 2:
            out.append({"name": line["name"], "points": points, "style": line.get("style", "line")})
    return out


def _add_history_lines(figure: go.Figure, lines: list[dict] | None, currency: str) -> None:
    for line in lines or []:
        # Purchase history is sparse - months with a delivery and months without - so it is drawn as
        # points, never joined across months when nothing was bought.
        points = line.get("style") == "points"
        figure.add_trace(go.Scatter(
            x=[day for day, _ in line["points"]], y=[value for _, value in line["points"]],
            mode="markers" if points else "lines", name=line["name"],
            marker=dict(color="#b45309", size=5),
            line=dict(color="#64748b", width=1.8, dash="dot"),
            hovertemplate="%{x|%b %Y}<br>%{y:,.0f} " + currency + "/" + units.MT + "<extra>" + line["name"] + "</extra>",
        ))


@st.cache_data(ttl=120, show_spinner=False)
def _load_forecast(identity_key: str, horizons: tuple[int, ...]) -> dict:
    try:
        # Thirty seconds, not eight. A market with 151 months behind it - the published European
        # series - is rolled through its own backtest at three horizons by every model in the
        # registry, and that took longer than eight seconds: the page got nothing back and told the
        # reader the market was history (Stefan, 23 September 2026). The answer is cached for two
        # minutes, so the wait falls on the first view of a market and on no other.
        client = MarketDataEngineClient(MARKET_DATA_ENGINE_URL, timeout=30.0)
        payload = client.forecast(identity_key, horizons)
    except MarketDataEngineUnavailable:
        return {}
    # The GOVERNED answer, carried in the same cached payload so the page makes one pair of calls
    # per market rather than one per horizon per render.
    #
    # `forecast` above asks every eligible model to compute now; this asks what the method IN FORCE
    # said when the production job ran. They are different questions and the second is the one a
    # customer is shown. Keeping them in one payload means `_card_forecast` can prefer the governed
    # number at a single place instead of at each of its nine call sites.
    #
    # A failure here is not a failure of the page: an engine without the route, or an error
    # fetching it, leaves `governed` empty and every horizon falls back to the computed answer,
    # which is what the page showed before this existed.
    try:
        governed = client.governed_forecast(identity_key, horizons)
    except MarketDataEngineUnavailable:
        governed = {}
    return {**payload, "governed": governed} if isinstance(payload, dict) else payload


def render_forecast_banner(payload: dict, horizons: list[int], *, title: str,
                           today: float | None, as_at,
                           market_price: tuple[str, float] | None = None) -> None:
    """One banner for the market: today's price, the forecast, and a row per month.

    This replaced three cards and the evidence panel. The three cards repeated one number, because
    the centre is identical at every horizon on a series with no direction to follow; what changed
    between them was the range, and the reader had to compare across boxes to see it.

    Nothing on it is ours. Publisher counts, how many origins the backtest ran, what the last single
    print was, how the model reached its answer - all of that was on the page and none of it is the
    reader's business. What is left is today's price, the forecast, the range, and how often a
    forecast at that horizon has landed close.
    """
    currency = str(payload.get("currency") or "")
    table = _load_fx_table()
    target = display_currency()
    stamp = as_of_date(payload.get("as_of"))

    def _amount(value: float) -> tuple[float, str]:
        """The figure in the reader's currency, and the code it is in."""
        converted = table.convert(float(value), currency, target, stamp) if currency else None
        return (converted[0], target) if converted else (float(value), currency or target)

    def _show(value: float) -> str:
        amount, shown = _amount(value)
        return units.with_unit(f"{shown} {amount:,.0f}")

    # One horizon on the banner, whichever is nearest. The page still asks the engine for all of
    # them and the chart still draws all of them; a buyer covering next month is deciding about
    # next month, and three rows of the same number under three widening bands read as noise.
    points = [point for weeks in sorted(horizons)
              if (point := _card_forecast(payload, weeks)) is not None][:1]
    if not points or not _forecast_history_enough(payload):
        # Say what is missing and how much of it, rather than "being prepared" forever.
        #
        # China MDI sat on that sentence for a week with twelve months of readings behind it. A
        # reader cannot tell a market that needs another fortnight from one that will never answer,
        # and the page knows the difference.
        st.info(_forecast_shortfall(payload))
        return

    def _show_converted(value: float) -> str:
        """Already in the reader's currency: format it, convert nothing.

        The snapshot behind today's price is converted by the page before it reaches this banner.
        Sending it through `_show` converted it a second time, and the banner reported USD 392 on a
        market trading near USD 2,630.
        """
        return units.with_unit(f"{target} {float(value):,.0f}")

    shown_currency = _amount(1.0)[1]
    content = forecast_cards.banner_content(
        payload, points, show=_show, show_today=_show_converted,
        today=today, as_at=as_at, title=title,
        amount=lambda value: _amount(value)[0], currency=shown_currency,
        # The figure Current Market prints, already in the reader's currency. Only where the
        # banner is in that currency too: a market the table cannot convert is shown in the
        # currency it is assessed in, and a reference in another one is no reference.
        market_price=market_price if shown_currency == target else None)
    st.markdown(forecast_cards.banner_html(content), unsafe_allow_html=True)
    # The banner carries four kinds of number and every one of them has been asked about: what the
    # range is, what a percentage next to a month means, whether the forecast is a promise. The
    # answers do not fit on the banner without turning it into prose, so they go in a document the
    # reader takes away. It is written from this same `content`, so it always says what the screen
    # says on the day it was downloaded.
    st.download_button(
        "How to read this forecast (Word)",
        data=forecast_explainer.explainer_docx(content),
        file_name=forecast_explainer.file_name(content),
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        key="forecast_banner_explainer",
    )


def render_flat_forecast_note(payload: dict) -> None:
    """One line under the cards when all three show the same figure, saying why.

    Raised three times now, and each time the answer was that it is arithmetically correct - which
    is true and is not an answer a reader can do anything with.
    """
    model = next((m for m in (payload.get("models") or ())
                  if m.get("model") == _card_model(payload) and m.get("status") == "ok"), None)
    if model is None:
        return
    note = forecast_cards.flat_note(model.get("points") or [], model.get("metadata") or {})
    if note:
        st.caption(note)


def render_forecast_basis(payload: dict) -> None:
    """One line under the cards, for a buyer.

    What the engine tried, what it rejected and why, the model family and the release machinery are
    all real and all belong in Platform Admin. Here the reader needs the age of the evidence and the
    weight to put on the number.
    """
    if not payload:
        return
    observations = payload.get("observations") or 0
    latest = payload.get("latest") or {}
    # The date of the newest PRINT, not the start of the month it fell in. "to 1 September 2026" on a
    # screen refreshed on the 14th reads as a fortnight-old application.
    through = _pretty_date(latest.get("date") or payload.get("as_of"))
    if observations and through:
        st.caption(
            f"Based on {observations} months of consolidated price history, priced to {through}. "
            "Indicative while accuracy is measured against outcomes."
        )


from raw_material_forecasting.transaction_view import (
    span_for as _transaction_span, transaction_traces as _transaction_traces,
)


def _priced_into_the_forecast_years(payload: dict) -> bool:
    """Whether this market is still being priced. Stefan, 23 September 2026: forecast 2026 onward.

    The European delivered purchases end in January 2017. Carrying three months forward from a price
    that stopped nine years ago is arithmetic, not a forecast.
    """
    stamp = str(payload.get("as_of") or (payload.get("latest") or {}).get("date") or "")[:4]
    return stamp.isdigit() and int(stamp) >= FORECAST_FROM_YEAR


def _forecast_history_enough(payload: dict) -> bool:
    """The same rule as the price card: below it the market is an indication, with no forecast.

    The Europe method counts the months it reads - the published series and the card's changes -
    against its own minimum of four (Stefan, 22 September 2026).
    """
    if not _priced_into_the_forecast_years(payload):
        return False
    if _card_model(payload) == EUROPE_MODEL:
        # The months the method read. On a chained market that is the published series plus the
        # card's changes; on DDP and DAP, which are chained to nothing, it is the market's own
        # months. Reading only the chained count called 151 months of published European polyol a
        # market indication (Stefan, 23 September 2026).
        months = int(payload.get("chained_months") or payload.get("observations") or 0)
        return months >= EUROPE_MIN_MONTHS
    readings = payload.get("readings") or payload.get("history") or []
    # Across a restart: this history is what the engine fitted on, and `store_series.forecast_rows`
    # has already put the market's earlier record in front of it where it has one. Cutting that back
    # to "since the market last started being priced" would throw away the months the forecast was
    # built from and then report there were not enough of them.
    return enough_history([day for day, _value in readings], across_a_restart=True)


def _forecast_shortfall(payload: dict) -> str:
    """Why this market has no forecast. Short: this is a customer screen."""
    if not payload:
        # No answer is not an answer about the market. This said "this market is history" over a
        # market priced in July, because the forecast had not come back (Stefan, 23 September 2026).
        return "The forecast for this market is being prepared. The price history is below."
    if not _priced_into_the_forecast_years(payload):
        return "This market is history. It is not being priced now, so it carries no forecast."
    if not _forecast_history_enough(payload):
        return "Market indication. There is not yet enough price history for a forecast."
    return ("This market has enough history for a forecast and the method has not been able to "
            "read a direction in it. The price and its range are shown below.")


# How long a hole in the record has to be before the forecast chart stops drawing through it.
#
# The same number Current Market uses. A monthly series steps 28 to 35 days and stays joined; two
# months or more and the line between two readings is the chart's invention rather than a price
# path.
FORECAST_GAP_DAYS = 45


def _unbroken_runs(history):
    """The history split wherever the record stops, so nothing is drawn across a hole."""
    if len(history) < 2:
        return [list(history)] if history else []
    runs, current = [], [history[0]]
    for previous, point in zip(history, history[1:]):
        if (point[0] - previous[0]).days > FORECAST_GAP_DAYS:
            runs.append(current)
            current = []
        current.append(point)
    runs.append(current)
    return [run for run in runs if run]


def _ending_on_the_market_price(history: list, market_price: tuple[str, float] | None) -> list:
    """The drawn history, ending on the price Current Market shows where there is one.

    The history is the consolidated series, and its newest day can sit one publisher behind the
    prints: on 23 November 2026 Polyether Polyol China was consolidated at 11:18 UTC from the one
    print that had landed, the second landed after it, and until the next evening's run the chart
    ended on a figure the Current Market card - which reads the prints - did not show. Same day,
    two numbers, 0.6% apart with the tax out and 13.7% with it in.

    So the last point is the card's. On the card's own day it replaces what was there; a day past
    the end of the history it is added. A market price OLDER than the history's newest day changes
    nothing: the history is ahead of it, and moving its end backwards would delete readings.
    """
    if not history or market_price is None or not market_price[1]:
        return history
    day, value = pd.to_datetime(market_price[0]), float(market_price[1])
    if day < history[-1][0]:
        return history
    kept = history[:-1] if day == history[-1][0] else list(history)
    return kept + [(day, value)]


def _build_forecast_figure(payload: dict, horizon: int, currency: str, target: str,
                           table, transactions: list[dict] | None = None,
                           history_lines: list[dict] | None = None,
                           market_price: tuple[str, float] | None = None) -> go.Figure:
    """Price history, the forecast path, and the range around it.

    The band is what this chart is for. A single dashed line invites a reader to treat one number as
    the answer; a band that opens with distance says the same thing the cards say - the further out,
    the less is known - and says it in one glance.

    History and forecast come from the same payload, so the line and the cards cannot drift apart.
    """
    stamp = as_of_date(payload.get("as_of"))

    def _convert(value):
        if not currency:
            return float(value)
        converted = table.convert(float(value), currency, target, stamp)
        return converted[0] if converted else float(value)

    # `readings` is every governed reading, one per date. `history` is the monthly buckets the model
    # fits on, each the median of its month, so the newest point sits in the middle of a month that
    # is not over: on 17 September 2026 it drew China TDI at 14,926 when the market was at 15,664.
    # The chart draws what we hold; the cards still come from the buckets.
    drawn = payload.get("readings") or payload.get("history") or []
    history = [(pd.to_datetime(day), _convert(value)) for day, value in drawn]
    history = _ending_on_the_market_price(history, market_price)
    model = next((m for m in (payload.get("models") or ())
                  if m.get("model") == _card_model(payload) and m.get("status") == "ok"), None)
    # No forecast line on a market the banner calls an indication (Stefan, 22 September 2026): the
    # banner said "not enough price history for a forecast" over a chart that drew one to December.
    if not _forecast_history_enough(payload):
        model = None
    figure = go.Figure()
    # History is drawn whether or not a model answered.
    #
    # It used to return an empty figure when the model declined, and the page then said "Price
    # history is being collected for this market" over a series with thirty-one points in it. China
    # MDI on 16 September 2026: two years of history on screen as nothing at all, because Last
    # Month's Move cannot carry a move across the four-month gap before the publishers started.
    #
    # A market with a price and no forecast shows its price.
    if not history:
        return figure

    # The history is broken wherever the record is, for the same reason as on Current Market: a
    # straight run between two readings months apart reads as a steady move and nobody measured
    # anything in it. TDI China drew one diagonal from about 4,300 down to 1,950 across the years
    # with no readings (Stefan, 26 September 2026).
    runs = _unbroken_runs(history)
    for index, piece in enumerate(runs):
        figure.add_trace(go.Scatter(
            x=[day for day, _ in piece], y=[value for _, value in piece],
            mode="lines+markers" if len(piece) == 1 else "lines",
            name="Market price", legendgroup="Market price", showlegend=index == 0,
            line=dict(color="#1d4ed8", width=2.4), marker=dict(size=5, color="#1d4ed8"),
            hovertemplate="%{x|%d %b %Y}<br>%{y:,.0f} " + target + "/" + units.MT + "<extra></extra>",
        ))
    # The holes are bridged rather than left blank, the way Current Market has always drawn them.
    # Chinese pMDI has 608 days with nothing in them and the line simply stopped, which a reader
    # takes for a market that stopped rather than a record with a hole in it. Stefan, 1 October
    # 2026: "bridge it in the graph, that is what we do with gaps." Dotted, and it says so in the
    # legend, because nobody measured anything in there.
    for index, (before, after) in enumerate(zip(runs, runs[1:])):
        figure.add_trace(go.Scatter(
            x=[before[-1][0], after[0][0]], y=[before[-1][1], after[0][1]], mode="lines",
            name="No readings", legendgroup="No readings", showlegend=index == 0,
            line=dict(color="#1d4ed8", width=1.6, dash="dot"),
            hovertemplate="%{x|%d %b %Y}<br>nothing measured between these two<extra></extra>",
        ))
    # The axis covers the published price AND the purchases beside it, so two and a half years of
    # KGI history are not thrown away because the published series happens to start in 2024.
    published = (min(day for day, _ in history).strftime("%Y-%m-%d"),
                 max(day for day, _ in history).strftime("%Y-%m-%d"))
    span = _transaction_span(transactions or [], published)
    for trace in _transaction_traces(transactions or [], currency, target, table, stamp, span):
        figure.add_trace(trace)

    # The forecast line starts where the reader is standing: the date of the last price anybody
    # printed, at that price. It used to start at the last monthly BUCKET, which is keyed to the
    # first of the month - so a chart looked at on the 14th announced "Forecast starts, 1 Sep 2026"
    # and drew the line from a fortnight ago at a number nobody quoted that day.
    #
    # The model still runs from the bucket. What moves is only where the picture begins.
    latest = (payload or {}).get("latest") or {}
    anchor_day, anchor_value = history[-1]
    printed_on = pd.to_datetime(latest["date"]) if latest.get("date") else None
    if printed_on is not None and printed_on >= anchor_day and latest.get("value") is not None:
        anchor_day, anchor_value = printed_on, _convert(float(latest["value"]))
    # And where Current Market shows a price for this market, that is where the reader is standing:
    # the history above already ends on it, and the forecast is drawn from it.
    if market_price is not None and market_price[1] and (
            pd.to_datetime(market_price[0]) >= anchor_day):
        anchor_day, anchor_value = history[-1]
    # The points the page draws, from the one place that decides them: the chart, the CSV and the
    # report under it all ask `_points_the_page_draws`.
    points = _points_the_page_draws(payload, model, horizon)
    # On the price's own day of each month, not the 1st: see `forecast_cards.shown_day`.
    days = [anchor_day] + [pd.to_datetime(_shown_day(payload, p) or p["period"]) for p in points]
    middle = [anchor_value] + [_convert(p["value"]) for p in points]

    # Both certainties, read from the same place the banner reads them: the errors this model made
    # at this horizon, cut at the width that would have covered 80% and 90% of them. The chart used
    # to draw one band off the raw 0.1 and 0.9 quantiles while the banner printed two calibrated
    # ones, so the picture and the table under it were answering with different numbers.
    # Stefan, 30 September 2026: put both on the graph, in two colours.
    def _edges(coverage: str) -> tuple[list[float], list[float]] | None:
        low_side: list[float] = [anchor_value]
        high_side: list[float] = [anchor_value]
        for point in points:
            band = forecast_cards.band_for(point, coverage)
            if band is None:
                return None
            low_share, high_share = band
            low_side.append(_convert(float(point["value"]) * (1.0 + low_share)))
            high_side.append(_convert(float(point["value"]) * (1.0 + high_share)))
        return low_side, high_side

    bands = {coverage: _edges(coverage) for coverage in ("90", "80")}
    # Nothing calibrated yet: fall back to the model's own quantiles, one band, as before.
    if points and not any(bands.values()):
        bands = {"80": ([anchor_value] + [_convert(p["quantiles"].get("0.1", p["value"]))
                                          for p in points],
                        [anchor_value] + [_convert(p["quantiles"].get("0.9", p["value"]))
                                          for p in points])}

    if points:
        # The wider band is drawn first so the narrower one sits on top of it and reads as the
        # firmer claim. mode="lines" explicitly: without it the boundary of the fill draws its own
        # markers, which look like data points sitting on the edge of the band and are nothing of
        # the kind. The edges carry their own markers instead, so every number is on the picture.
        shading = {"90": ("rgba(148,163,184,0.26)", "#94a3b8"),
                   "80": ("rgba(13,148,136,0.24)", "#0d9488")}
        for coverage in ("90", "80"):
            edges = bands.get(coverage)
            if not edges:
                continue
            low_side, high_side = edges
            fill, stroke = shading[coverage]
            figure.add_trace(go.Scatter(
                x=days + days[::-1], y=high_side + low_side[::-1], fill="toself",
                fillcolor=fill, mode="lines", line=dict(width=0, color="rgba(0,0,0,0)"),
                name=f"{coverage}% certain", legendgroup=coverage, hoverinfo="skip"))
            for side, values in (("High", high_side), ("Low", low_side)):
                figure.add_trace(go.Scatter(
                    x=days[1:], y=values[1:], mode="markers+text",
                    marker=dict(size=6, color=stroke, symbol="line-ew-open",
                                line=dict(width=2, color=stroke)),
                    text=[""] * (len(values) - 2) + [f"{values[-1]:,.0f}"],
                    textposition="top right" if side == "High" else "bottom right",
                    textfont=dict(size=11, color=stroke),
                    name=f"{coverage}% certain", legendgroup=coverage, showlegend=False,
                    hovertemplate=("%{x|%b %Y}<br>" + coverage + "% certain " + side.lower()
                                   + ": %{y:,.0f} " + target + "/" + units.MT + "<extra></extra>")))
        figure.add_trace(go.Scatter(
            x=days, y=middle, mode="lines+markers", name="Forecast",
            line=dict(color="#0d9488", width=2.6, dash="dash"), marker=dict(size=7),
            hovertemplate="%{x|%d %b %Y}<br>%{y:,.0f} " + target + "/" + units.MT + "<extra></extra>",
        ))
        # Left of the line, not centred on it. The line sits near the right edge by construction -
        # the forecast starts there - so a label centred on it runs off the plot and arrives as
        # "Last price · forecast from h".
        #
        # The label is a separate annotation, not one of add_vline's own annotation_* arguments,
        # and these numbers are exactly what those arguments produced: plotly places a "top left"
        # vline label at the top of the line and anchors it right, so the text ends where the line
        # is. Passing ANY annotation_* argument routes add_vline through plotly/shapeannotation.py,
        # whose _mean() does float(sum(x)) / len(x) over the shape's span - and sum() starts at
        # integer 0, so "0 + <a date>" raises TypeError before the label is ever placed. Measured
        # 5 November 2026: on plotly 6.5.0 a datetime.date, a pandas Timestamp, a datetime and an
        # ISO string all fail, so no value of anchor_day would have worked. plotly 7.0.0 added a
        # fallback for anything carrying .timestamp(), which is why this survived on the venv's
        # 7.0.0 and raised the moment the chart was drawn on 6.5.0 - a version plotly>=5.20
        # happily installs. add_annotation does not go near that code at all.
        figure.add_vline(x=anchor_day, line_width=1, line_dash="dot", line_color="#94a3b8")
        figure.add_annotation(x=anchor_day, y=1, yref="y domain", xanchor="right", yanchor="top",
                              text="Last price · forecast from here", showarrow=False,
                              font=dict(size=11, color="#64748b"))
    # The range buttons sit at the top left, where the eye lands first, and nothing is drawn above
    # the plot to collide with them. The legend moves to the foot of the figure, anchored to the
    # container rather than the plot area, so it stays put however many series are on the chart and
    # however many rows it wraps to.
    figure.update_layout(
        margin=dict(l=10, r=10, t=58, b=54), height=580,
        yaxis_title=units.per_unit(target), xaxis_title="",
        legend=dict(orientation="h", xref="container", yref="container",
                    xanchor="left", x=0.01, yanchor="bottom", y=0.01,
                    font=dict(size=11), itemwidth=30),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        hovermode="x unified", dragmode="pan",
    )
    # The chart opens on the last year and the forecast, which is the window a buyer reads first.
    # Twelve years of history compressed to fit is a picture of nothing. The slider and the buttons
    # put the rest one drag away.
    # `days` is just the anchor when no model answered, so this is the last price either way.
    last_day = max(days[-1], history[-1][0])
    # The same months of history as Current Market, measured from the same newest reading.
    #
    # This opened on everything since 2014 whenever the figure held more than one trace. The rule was
    # written for purchase records, which predated the published price and had to be in view; they
    # left the customer charts, and the rule stayed behind and fired on the forecast line and its
    # band instead. So a forecast always opened on twelve years while Current Market opened on
    # fourteen months, and the same governed series looked like two histories.
    #
    # Measured from the last reading, not from the end of the forecast: the history window is about
    # the past, and the forecast extends the view to the right of it.
    _add_history_lines(figure, history_lines, target)
    first_drawn = min([history[0][0]] + [line["points"][0][0] for line in history_lines or []])
    window_start = _history_window_start(first_drawn, history[-1][0])
    # Every month gets a tick. Plotly's default thins them to six-month steps on a range this long,
    # which leaves a reader counting gridlines to work out which month a turn happened in.
    figure.update_xaxes(showgrid=True, gridcolor="rgba(148,163,184,0.18)",
                        dtick="M1", tickformat="%b %y", ticks="outside",
                        ticklabelmode="period", tickangle=-60, tickfont=dict(size=10),
                        range=[window_start, last_day + pd.Timedelta(days=20)],
                        rangeslider=dict(visible=True, thickness=0.09,
                                         bgcolor="rgba(148,163,184,0.10)",
                                         bordercolor="rgba(148,163,184,0.45)", borderwidth=1),
                        rangeselector=dict(
                            x=0, y=1.02, xanchor="left", yanchor="bottom",
                            bgcolor="rgba(226,232,240,0.75)", activecolor="#0d9488",
                            font=dict(size=11),
                            buttons=[
                                dict(count=6, label="6m", step="month", stepmode="backward"),
                                dict(count=1, label="1y", step="year", stepmode="backward"),
                                dict(count=2, label="2y", step="year", stepmode="backward"),
                                dict(count=5, label="5y", step="year", stepmode="backward"),
                                dict(step="all", label="All"),
                            ]))
    # The y axis is left free so a zoom into a flat stretch can open the scale with it; pinned, the
    # chart zooms in time and stays unreadable in price.
    figure.update_yaxes(gridcolor="rgba(148,163,184,0.25)", fixedrange=False)
    return figure


def _report_series(frame, currency: str):
    """The report's two series, read back from the frame the page hands the customer.

    One source for the screen, the CSV and the document: whatever the page drew is what the report
    carries, and none of the three can drift from the other two.
    """
    price = f"Price {units.pair(currency)}"
    history, ahead = [], []
    for row in frame.to_dict("records"):
        if str(row.get("Series") or "") == "Observed":
            history.append((row["Date"], row[price]))
        else:
            low, high = row.get("Low"), row.get("High")
            ahead.append((row["Date"], row[price], low if low != "" else None,
                          high if high != "" else None))
    return history, ahead


def forecast_export_frame(payload: dict, currency: str, target: str, table,
                          market_price: tuple[str, float] | None = None) -> pd.DataFrame:
    """The chart's own numbers as a table, in the currency on screen.

    One function, because the delivery section hands the customer the same figures. Two versions of
    this would drift, and a download that disagrees with the chart above it is worse than none.
    """
    stamp = as_of_date(payload.get("as_of"))

    def _convert(value):
        if not currency:
            return float(value)
        converted = table.convert(float(value), currency, target, stamp)
        return converted[0] if converted else float(value)

    price = f"Price {units.pair(target)}"
    market = market_columns_from_key(payload.get("identity_key") or "")
    rows: list[dict] = []
    # Ending on the market price, as the chart does - `_ending_on_the_market_price`.
    observed = _ending_on_the_market_price(
        [(pd.to_datetime(day), _convert(value))
         for day, value in (payload.get("readings") or payload.get("history") or [])],
        market_price)
    for day, value in observed:
        rows.append({"Date": day.strftime("%Y-%m-%d"), "Series": "Observed",
                     price: round(value, 2), "Low": "", "High": ""})
    model = next((m for m in (payload.get("models") or ())
                  if m.get("model") == _card_model(payload) and m.get("status") == "ok"), None)
    if not _forecast_history_enough(payload):
        model = None        # the download says what the chart says: no forecast on an indication
    # The points the chart draws, not the pinned model's own: for a governed market those are two
    # different forecasts, and the file must say what the page says. See `_points_the_page_draws`.
    for point in _points_the_page_draws(payload, model, max(_FORECAST_HORIZONS)):
        quantiles = point.get("quantiles") or {}
        low, high = quantiles.get("0.1"), quantiles.get("0.9")
        rows.append({
            # The day the chart draws it on, so the file and the picture give one date.
            "Date": str(_shown_day(payload, point) or point["period"]),
            "Series": f"Forecast {int(point['horizon_weeks'])} weeks",
            price: round(_convert(point["value"]), 2),
            "Low": round(_convert(low), 2) if low is not None else "",
            "High": round(_convert(high), 2) if high is not None else "",
        })
    return _market_frame(market, rows, ["Date", "Series", price, "Low", "High"])


def _clean_dimension(value) -> str:
    text = str(value or "").strip()
    return "Not specified" if text.upper() in {"", "UNKNOWN", "UNSPECIFIED", "NOT SPECIFIED", "NOT_SPECIFIED"} else text


def market_columns_from_key(key: str) -> dict:
    """The market a file describes, taken from the series it was built from.

    A file of dates and prices is unusable the moment it leaves the screen it came from: nothing in
    it says which chemical, which market, or on what terms. These are the customer's own selectors
    and nothing else. Currency and unit are in the price column's heading already, and grade is not
    something the customer chooses: on the market path it is empty in every row we hold, and where a
    grade is recorded at all it is on KGI's own purchase records, which never reach a market view.
    """
    parts = str(key or "").split("|")
    positions = {"Chemical": 0, "Geography": 2, INCOTERM_LABEL: 3}
    return {name: _clean_dimension(parts[index] if index < len(parts) else "")
            for name, index in positions.items()}


MARKET_COLUMNS = ("Chemical", "Geography", INCOTERM_LABEL)


def _market_frame(market: dict, rows: list[dict], columns: list[str]) -> pd.DataFrame:
    """Rows with the market repeated on each, minus any dimension nothing is recorded for."""
    named = {name: _clean_dimension(market.get(name)) for name in MARKET_COLUMNS}
    known = [name for name, value in named.items() if value != "Not specified"]
    named = {name: named[name] for name in known}
    return pd.DataFrame([{**named, **row} for row in rows], columns=[*known, *columns])


# How much history both price charts open on. One constant, because two pages drawing the same
# governed series have to open on the same months: Current Market opened on the last fourteen months
# and Forecast opened on everything since 2014, so the same market looked like two different
# histories side by side.
HISTORY_WINDOW_DAYS = 430


def _history_window_start(first_reading, last_reading):
    """Where both charts open: fourteen months back from the newest reading, or the first one."""
    start = last_reading - pd.Timedelta(days=HISTORY_WINDOW_DAYS)
    return first_reading if start < first_reading else start


def _build_market_reference_figure(points, currency: str | None = None,
                                   history_lines: list[dict] | None = None) -> go.Figure:
    """One line: the market price for the selected market, one point a day.

    The chart drew four publishers' own series side by side on a page that promises one price, then
    drew the individual prices behind the line. Both say the same thing twice. The price is the
    product; what it was built from belongs in the evidence table, not on the chart.
    """
    fig = go.Figure()
    if not points:
        return fig
    currency = currency or display_currency()

    days = [pd.to_datetime(point.day) for point in points]
    values = [point.value for point in points]
    fig.add_trace(go.Scatter(
        x=days, y=values, mode="lines+markers", name="Market price",
        line=dict(color="#1d4ed8", width=2.4), marker=dict(size=5),
        hovertemplate="%{x|%d %b %Y}<br>%{y:,.0f} " + currency + "/" + units.MT + "<extra></extra>",
    ))

    _add_history_lines(fig, history_lines, currency)
    last = max(days)
    first_drawn = min([min(days)] + [line["points"][0][0] for line in history_lines or []])
    window_start = _history_window_start(first_drawn, last)
    fig.update_layout(
        height=520,
        margin=dict(l=18, r=18, t=40, b=20),
        paper_bgcolor="white",
        plot_bgcolor="white",
        hovermode="x unified",
        dragmode="pan",
        showlegend=bool(history_lines),   # one line needs no key to itself
        legend=dict(orientation="h", x=1, xanchor="right", y=1.04, yanchor="bottom"),
        font=dict(color="#223b53"),
    )
    # One tick per month, and the same slider and buttons as the forecast chart, so the two pages
    # are read the same way rather than each needing its own explanation.
    fig.update_xaxes(showgrid=True, gridcolor="rgba(148,163,184,0.18)", linecolor="#d9e4ed",
                     dtick="M1", tickformat="%b %y", ticks="outside", ticklabelmode="period",
                     tickangle=-60, tickfont=dict(size=10),
                     range=[window_start, last + pd.Timedelta(days=20)],
                     rangeslider=dict(visible=True, thickness=0.09,
                                      bgcolor="rgba(148,163,184,0.10)",
                                      bordercolor="rgba(148,163,184,0.45)", borderwidth=1),
                     rangeselector=dict(
                         x=0, y=1.04, xanchor="left", yanchor="bottom",
                         bgcolor="rgba(226,232,240,0.75)", activecolor="#0d9488",
                         font=dict(size=11),
                         buttons=[
                             dict(count=6, label="6m", step="month", stepmode="backward"),
                             dict(count=1, label="1y", step="year", stepmode="backward"),
                             dict(count=2, label="2y", step="year", stepmode="backward"),
                             dict(count=5, label="5y", step="year", stepmode="backward"),
                             dict(step="all", label="All"),
                         ]))
    fig.update_yaxes(title=units.per_unit(currency), gridcolor="#edf2f6", zeroline=False, fixedrange=False)
    return fig


def market_reference_export_frame(points, currency: str, market: dict) -> pd.DataFrame:
    """The reference line as a table. Delivery hands the customer these same figures."""
    column = f"Market price {units.pair(currency)}"
    rows = [{"Date": point.day, column: round(point.value, 2)} for point in points]
    return _market_frame(market, rows, ["Date", column])


def _display_market_dimension(value: str) -> str:
    normalized = str(value or "").strip()
    if normalized.upper() in {"", "UNKNOWN", "UNSPECIFIED", "NOT SPECIFIED", "NOT_SPECIFIED"}:
        return "Not specified"
    # The store keeps PMDI; the trade writes pMDI. See raw_material_forecasting/chemicals.py.
    return display_chemical(normalized)


def _display_basis(value: str) -> str:
    """A commercial basis, named. `CHINA_PRODUCER_LIST` is a key, and it reached the screen raw."""
    return display_basis(value)


def _basis_namer(chemical: str, geography: str):
    """Names the terms of one market, for that market's own selector.

    A series can be on terms the publisher did not write: FD is free delivered and what it
    describes is DAP, and a regional assessment printed with no terms is read as delivered. Each is
    a ruling with its evidence in `series_curation`, not a coat of paint. What none of them does is
    decide which readings share a line - that is the merging command, and only that. Stefan,
    28 September 2026: "A label is not pure cosmetic, nor is it a command to merge."
    """
    def name(value: str) -> str:
        return display_basis(terms_named(chemical, geography, value))

    return name


_SPAN_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
                "September", "October", "November", "December")


def _not_collected_note(identities, observations, *, chemical: str, geography: str = "") -> str:
    """What a page says when it holds no current price, with the history checked rather than hinted.

    It used to read "We are not pricing X at the moment. Historic Pricing has what we hold", which
    says the same thing whether twelve years sit behind it or nothing does, and sends half the
    readers to an empty page. Stefan, 1 October 2026, gave the words and then the better version:
    "Even better if we actually check if we have historic data and then say that we have historic
    data or not."
    """
    where = f" in {_display_market_dimension(geography)}" if geography else ""
    span = history_span(identities, observations, chemical=chemical, geography=geography)
    opening = f"Currently we are not collecting the pricing for this chemical{where}."
    if span is None:
        return f"{opening} We hold no historic data for it either."
    first, last = span

    def _month(day: str) -> str:
        return f"{_SPAN_MONTHS[int(day[5:7]) - 1]} {day[:4]}"

    period = _month(first) if _month(first) == _month(last) else f"{_month(first)} to {_month(last)}"
    return f"{opening} Historic Pricing holds it from {period}."


def _alphabetical(options, label=None):
    """Options in the order a reader looks for them: by the name on the screen, A to Z.

    Stefan, 30 September 2026: "In general dropdown lists should be sorted by alphabet." Sorted on
    the label the selector shows rather than on the code behind it, so pMDI lands between Graft
    Polyol and Polyether Polyol where a reader expects it instead of wherever PMDI falls. Case is
    ignored, because a list that puts every lower-case name after every upper-case one is not
    alphabetical to anybody but a computer.

    Which option a page OPENS on is a separate question and is pinned separately at each selector.
    Sorting must never change the market a page lands on.
    """
    show = label or (lambda value: value)
    return sorted(options, key=lambda value: str(show(value)).casefold())


def _carry_market_choice(to_page: str, from_page: str) -> None:
    """Carry the market from one page's selectors to the other's, once per arrival.

    Stefan, 23 October 2026: open TDI China EXW on Current Market, move to Forecast, "I want the
    choice to remain the same, not jump to the ABC". The two pages keep their own selector keys -
    `current_market_chemical` and `forecast_chemical` and so on - so neither knew what the other was
    showing, and each opened on its own default.

    ONCE PER ARRIVAL, not on every rerun. Carrying it every time would undo a market chosen ON the
    page you are standing on: the next rerun would find the other page's value still in the session
    and put it back, and the selector would spring shut every time it was touched.

    A value the other page offers and this one does not is not a problem to solve here. Every
    selector is followed by `_keep_choice`, which points it at something the page actually offers,
    so a market carried from a page with a wider list lands on the nearest thing this one has.
    """
    if st.session_state.get("_market_page") == to_page:
        return
    st.session_state["_market_page"] = to_page
    for dimension in ("chemical", "geography", "incoterm", "packaging"):
        carried = st.session_state.get(f"{from_page}_{dimension}")
        if carried is not None:
            st.session_state[f"{to_page}_{dimension}"] = carried


def _keep_choice(key: str, options, preferred) -> None:
    """Point a selector at a value it offers, and tell the browser.

    Removing a stale value from the session left the choice to `index`, which the server used and
    the browser never heard about: the card priced CIF polyether in Europe while the box above it
    still read EXW, left over from China. Writing the value into the session is what sends it to
    the browser, so the box and the card show the same market.
    """
    if st.session_state.get(key) not in options:
        st.session_state[key] = preferred if preferred in options else options[0]


def render_current_market(runtime_access: RuntimeAccess | None = None) -> None:
    """The markets being priced now: anybody priced them inside `CURRENT_WITHIN_DAYS`.

    One year of each one is drawn (`drawn_on_current`). Everything older is on Historic Pricing.
    """
    _render_market_page(runtime_access, priced_recently_only=True)


def render_historic_pricing(runtime_access: RuntimeAccess | None = None) -> None:
    """Every other market we hold a record for, drawn over its whole record.

    Stefan, 27 September 2026, after counting what was on Current Market: 74 graphs, of which 51
    had not been priced by anybody since August 2023. A page headed Current Market that is two
    thirds history is not a current market page. So the two were separated.

    **The line between them was drawn in the wrong place, twice, and 3 November 2026 moved it.**

    It ran through the MARKETS and not through the DATES, so Current Market carried the right
    markets and then drew each one's entire record on them - aliphatic polyester polyol China,
    five weeks of our own readings, opened on a chart that was 73% an empty hole with a deal
    record from 2017 as its only visible line. Stefan, looking at it: *"if it split by market then
    it is wrong, isnt it?? It should split by date"*. It now splits by both: this page gets the
    markets nobody is pricing, and the dates older than a year on the ones they are.

    And it asked whether WE took the reading, which hid nine markets somebody else had priced
    inside two months. Who took a reading is a fact the chart still draws - blue is ours - and no
    longer one that decides which page a market is on.
    """
    _render_market_page(runtime_access, priced_recently_only=False)


@st.cache_data(ttl=SNAPSHOT_SECONDS, max_entries=24, show_spinner=False)
def _market_readings(identity_keys: tuple[str, ...], currency: str, stamp: str) -> list[dict]:
    """The full readings of ONE market, kept between runs the way the snapshot is.

    The page's snapshot carries the index - four fields per reading and no prices - which is all
    five selector functions ever read. A chart draws numbers, so it needs the readings themselves,
    and it needs them only for the market on screen: 322 markets at 35,515 readings against one
    market at a few hundred.

    `stamp` is the same stamp the snapshot is kept on, so new readings landing refresh both
    together rather than leaving a chart a quarter of an hour behind its own selectors.
    """
    if not identity_keys:
        return []
    try:
        return MarketDataEngineClient(MARKET_DATA_ENGINE_URL, timeout=1.5).readings_for(
            identity_keys, currency=currency)
    except MarketDataEngineUnavailable:
        return []


def _render_market_page(runtime_access: RuntimeAccess | None = None, *,
                        priced_recently_only: bool) -> None:
    live = priced_recently_only
    st.markdown("## Current Market Pricing" if live else "## Historic Pricing")
    _say_when_the_engine_is_older()

    # The index, not every reading. Everything above the chart - which chemicals are offered, which
    # geographies, which terms, which market to open on, whether anything was collected recently -
    # is decided by five functions that read four fields and never a price as a value. The chart
    # asks for its own market's readings further down.
    snapshot = _load_step6_snapshot(readings="index")
    if snapshot is None:
        st.info("Market data is being collected.")
        return
    stale_minutes = snapshot_age_to_report()
    if stale_minutes is not None:
        st.caption(f"The market data engine is busy; these readings were last read "
                   f"{stale_minutes:.0f} minutes ago.")

    # The markets a customer is offered, and nothing else: the three chemicals, China and Europe, an
    # Incoterm, a market price. The Incoterm list used to carry CHINA_PRODUCER_LIST and
    # CHINA_DOMESTIC_SELLER_QUOTE as if they were delivery terms, because they were stored in the
    # same field; they are evidence behind the EXW market, not markets of their own.
    identities = [dict(row) for row in snapshot.identities if is_published_market(row)]
    # Two different nothings, and until v1.2.101 they shared one sentence.
    #
    # Stefan, 23 November 2026, signed in with a scope of `*` on the first deployment and shown
    # "Contact HTC Global to add markets to your subscription." With `*` that sentence cannot be
    # true: there is no market an entitlement of everything excludes. The markets were not being
    # withheld from him, they had not arrived - and the page blamed his subscription, which is the
    # one explanation he could not act on.
    #
    # A customer seeing an empty page should be told to call us. The owner seeing an empty page
    # needs to know the engine has nothing, because he is the one who can go and look at why.
    published = len(identities)
    if runtime_access is not None:
        identities = [row for row in identities if identity_allowed(row, runtime_access.scope)]
    if not identities and published == 0:
        st.warning(
            "No market prices have loaded yet.\n\n"
            "Nothing is wrong with your subscription. The market data is still being read, or "
            "it cannot be reached from here. Reload in a minute; if it keeps saying this, "
            "contact HTC Global."
        )
        # What exactly could not be read is the owner's business and not the reader's: he is
        # the one who can go and look at why (Stefan, 23 November 2026, the same page in both
        # doors).
        if snapshot.errors and _signed_in_email() == PLATFORM_ADMIN_EMAIL:
            st.caption("Owner only: "
                       + "; ".join(f"{where}: {what}" for where, what in snapshot.errors.items()))
        return
    if not identities:
        st.info("Contact HTC Global to add markets to your subscription.")
        return

    as_of = date.today()
    # The landing market is chosen by evidence - freshest reading, most publishers, longest history -
    # rather than by whichever identity happened to sort first on its UUID.
    chosen = preferred_identity(identities, snapshot.observations, snapshot.references)
    if chosen is None:
        st.info("No quotable market identity is available yet.")
        return
    preferred_chemical, preferred_geography, preferred_incoterm, _packaging = identity_dimensions(chosen)

    # Five selectors, always the same five. Packaging used to appear only where a market had more
    # than one, which meant it vanished on East China and the reader could not see which packaging
    # they were looking at (Stefan, 27 September 2026).
    selectors = st.columns([1.45, 1.45, 1.35, 1.2, 0.85])
    s1, s2, s3, s_packaging, s_currency = selectors
    with s1:
        # Only chemicals with a series this page can draw (Stefan, 13 October 2026: "when there is
        # nothing to show, there should be no dropdown choice"). The list is read from the same
        # sets the Data & Sources eligibility table marks Yes.
        drawable = customer_catalogue.offered(
            identities, snapshot.observations,
            customer_catalogue.CURRENT if live else customer_catalogue.HISTORIC,
            sources=snapshot.sources)
        chemical_options = _entitled_chemicals(
            customer_catalogue.chemicals_offered(drawable), runtime_access)
        if not chemical_options:
            st.info("No market is available on this page within the active subscription.")
            return
        chemical_options = _alphabetical(chemical_options, _display_market_dimension)
        _carry_market_choice("current_market", "forecast")
        _keep_choice("current_market_chemical", chemical_options, preferred_chemical)
        chemical = st.selectbox(
            "Chemical",
            chemical_options,
            key="current_market_chemical",
            format_func=_display_market_dimension,
        )

    # Below the chemical, a selector offers what draws and nothing else.
    #
    # Each list used to fall back to a standing roster wherever the store knew nothing, so a
    # chemical with no readings offered fifteen geographies and eight Incoterms and every one of
    # those 120 paths ended on an empty chart. Counted across the page: 73 selections drew
    # something and 672 drew nothing. Stefan, 27 September 2026: "obviously the 672 selections a
    # user should not be able to make."
    #
    # The chemical list is the exception and stays whole. He asked for every PU raw material to be
    # on it - "the more chemicals we can add that are PU Raw Materials the better" - and a chemical
    # we hold no market for says so in one line, which is a better answer than being absent.
    # Which markets this page carries. Current Market takes the ones anybody priced inside six
    # months; Historic Pricing takes everything else we hold a record for.
    geography_options = _entitled_values(
        geographies_with_a_market(drawable, chemical),
        runtime_access.scope.geographies if runtime_access else frozenset(), runtime_access)
    if not geography_options:
        st.info(
            _not_collected_note(identities, snapshot.observations, chemical=chemical)
            if live else
            f"No record is held for {_display_market_dimension(chemical)}.")
        return
    with s2:
        # Never `geography_options[0]`. The moment the reader picks a different chemical from the
        # landing one, the fallback decides the market, and alphabetical order knows nothing about
        # evidence: it put a producer-posting basis collected that morning - one reading, one
        # publisher - ahead of five years of EXW history, and drew a chart with a single dot on it.
        preferred_geo = preferred_geography if chemical == preferred_chemical else (
            best_market_option(identities, "geography", observations=snapshot.observations,
                               references=snapshot.references, chemical=chemical)
            or geography_options[0])
        geography_options = _alphabetical(geography_options, _display_market_dimension)
        _keep_choice("current_market_geography", geography_options, preferred_geo)
        geography = st.selectbox(
            "Geography",
            geography_options,
            key="current_market_geography",
            format_func=_display_market_dimension,
        )

    # Every line this market carries: the assessed history, the record that bridges it, and what we
    # collect ourselves. A market is a chemical in a geography (Stefan, 26 September 2026); the
    # Incoterm is a choice inside it rather than a market of its own, so the chart draws them all
    # and this selector decides only which of them the headline price is read from.
    # `identities_of` is `market_lines`' own rule for which identities make up this market - East
    # China sits inside China, a merged term is one market - so the readings asked for and the
    # readings drawn can never be decided differently.
    lines = _lines_of_the_market(identities, snapshot.sources, chemical, geography, live=live)
    # A line is currently priced if it carries a reading inside the window that decides this page.
    # Whose reading it is decides the colour of the stroke and nothing else (3 November 2026).
    priced_now = [line for line in lines if market_lines_module.current_price(line)]

    # Every set of terms this market holds a record on, whether or not anybody is assessing it
    # today. It used to offer only the terms our own collection covers, which was harmless while
    # the chart drew all of them anyway. From the moment the Incoterm decides what is drawn it
    # stopped being harmless: TDI Europe collects a spot CFR and a regional index, so those two
    # were the whole list, and 440 weeks of contract FD and twelve years of DDP could not be
    # reached at all (Stefan, 27 September 2026). Where a term has no current reading the card
    # says so, which is the honest answer and one the page already gives.
    incoterm_options = _entitled_values(
        terms_with_a_market(drawable, chemical, geography),
        runtime_access.scope.incoterms if runtime_access else frozenset(), runtime_access)
    if not incoterm_options:
        st.info(
            _not_collected_note(identities, snapshot.observations,
                                chemical=chemical, geography=geography)
            if live else
            f"No record is held for {_display_market_dimension(chemical)} in "
            f"{_display_market_dimension(geography)}.")
        return
    with s3:
        preferred_basis = preferred_incoterm if (
            chemical == preferred_chemical and geography == preferred_geography) else (
            best_market_option(identities, "incoterm", observations=snapshot.observations,
                               references=snapshot.references, chemical=chemical,
                               geography=geography)
            or incoterm_options[0])
        incoterm_options = _alphabetical(incoterm_options, _basis_namer(chemical, geography))
        _keep_choice("current_market_incoterm", incoterm_options, preferred_basis)
        incoterm = st.selectbox(
            INCOTERM_LABEL,
            incoterm_options,
            key="current_market_incoterm",
            format_func=_basis_namer(chemical, geography),
        )

    # Packaging back in the selector (Stefan, 26 September 2026). It was taken out on 23 September
    # because every market then carried one packaging and the selector had nothing to choose
    # between. Opening the roster changed that: TDI China now draws the market's own assessment,
    # which names no packaging, on top of three drummed regional assessments, and the chart put all
    # four up at once. Packaging is a choice the reader makes, so the chart draws one at a time.
    #
    # The choice is made across the whole market rather than under the Incoterm above it. The
    # drummed assessments are delivered and the market's own is ex-works, so a packaging list that
    # narrowed to the chosen Incoterm would offer one option on every market and change nothing.
    # The Incoterm chooses what is drawn, not only what the headline is read from (Stefan,
    # 27 September 2026). Five sets of terms on one pair of axes is the same complaint as five
    # packagings, one dimension over.
    lines = lines_on(lines, incoterm)

    # Packaging follows the Incoterm, because the chart now does. It is always on screen, even when
    # a market has one packaging: it vanished on East China, which has only drums, and a reader who
    # cannot see which packaging they are looking at - or get back to the one they came from - is
    # worse served than one looking at a selector with a single entry.
    # A market with nothing on these terms still gets a selector rather than an empty column, so
    # the row does not change shape as the reader moves through it.
    # Only packagings this page has a series for, so no choice ends on an empty chart.
    packaging_options, preferred_packaging = _packaging_the_page_opens_on(
        lines, drawable, chemical, geography, incoterm)
    _keep_choice("current_market_packaging", packaging_options, preferred_packaging)
    with s_packaging:
        packaging = st.selectbox(
            "Packaging",
            packaging_options,
            key="current_market_packaging",
            format_func=display_packaging,
        )

    lines = lines_packaged(lines, packaging)
    priced_now = [line for line in lines if market_lines_module.current_price(line)]

    with s_currency:
        render_display_currency_selector()

    # With the readings, so "which of these series is this market" is decided rather than left to
    # whichever identity the index happened to list first. See `find_identity`: on Aliphatic
    # Polyester Polyol China EXW that accident took the card's kicker from a series last priced on
    # 1 May 2017 while the price beside it was from 30 October 2026.
    selected_identity = find_identity(identities, chemical, geography, incoterm,
                                      observations=snapshot.observations)
    identity_keys = equivalent_identity_keys(identities, selected_identity) if selected_identity else ()
    # The governed series, not the raw prints: same basis as the forecast, net of tax. The prints
    # are only read when the engine has no governed series for this market yet.
    governed_points = _governed_points(selected_identity) if selected_identity else []

    # The headline is today's market, so it is read through `current_price` - the same rule that
    # decided the selectors above. A market whose only record is assessed history still has a chart
    # and no current price: eight years of ICIS is history, and history is not a price today
    # (Stefan, 26 September 2026). What has changed, on his instruction of 20 November 2026, is
    # that the age answers that now and the source does not. A licensed assessment published four
    # weeks ago is this market's price today, and it was already what put the market on this page.
    # Asked of `market_lines.headline_price`, which the Forecast page asks too (24 November 2026):
    # one rule for the figure, so the two pages cannot print different ones for one market.
    headline = market_lines_module.headline_price(lines, incoterm, packaging)
    currency = display_currency()

    # No Incoterm on the card: it is in the selector directly above (Stefan, 22 September 2026).
    identity_label = " · ".join([
        _display_market_dimension(chemical), _display_market_dimension(geography)])

    # "Market indication" while the market has less price history than a forecast needs, by the
    # engine's rule; "Market price" from then on. Nothing about origin (Stefan, 21 September 2026).
    if selected_identity:
        history_days = _market_history_days(governed_points, snapshot, identity_keys)
        kicker = _price_kind(incoterm, _market_has_history(history_days, chemical, geography))
    else:
        kicker = _price_kind(incoterm, True)

    if headline is not None:
        priced_day, priced_value = headline
        st.markdown(
            f"""
            <div class="current-market-hero">
              <div class="current-market-kicker">{kicker}</div>
              <div class="current-market-price">{_format_reference_value(priced_value, currency)}</div>
              <div class="current-market-identity">{identity_label}</div>
              <div class="current-market-status">PRICED {priced_day}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f"""
            <div class="current-market-hero current-market-unavailable">
              <div class="current-market-kicker">{kicker}</div>
              <div class="current-market-price">No current price available</div>
              <div class="current-market-identity">{identity_label}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown("This market is not currently assessed. The chart below is its record.")

    # No verification panel and no evidence-range card under the banner (Stefan, 22 September 2026):
    # a customer page does not grade its own price, and the range repeated the banner. The publisher
    # count still decides "Market indication" above and the forecast rules.
    st.write("")
    _render_market_lines_chart(
        lines, currency,
        {"Chemical": chemical, "Geography": geography, INCOTERM_LABEL: incoterm},
        card={"kicker": kicker,
              "price": _format_reference_value(headline[1], currency) if headline else "",
              "priced": headline[0] if headline else ""})
    # The feedstock boxes stood here from 1 October 2026, when the feedstocks came off
    # the chemical list. Removed 13 October 2026: feedstocks are markets of their own again, and
    # Stefan: "it is confusing and does not add any value."


def _price_kind(incoterm: str, has_history: bool) -> str:
    """What kind of price the card carries, in the reader's words.

    Stefan, 23 September 2026. The difference between the two European series is not freight: inside
    the EU, DDP and DAP part company only over import duty and clearance, and neither applies. What
    differs is the kind of price - one is published before any discount, the other is what was
    negotiated off it. So the card says which it is rather than leaving two delivery codes to imply
    the gap is transport.
    """
    basis = str(incoterm or "").strip().upper()
    if basis == "DDP":
        return "Published price"
    if basis == "DAP":
        return "Negotiated price"
    return "Market price" if has_history else "Market indication"


def _market_has_history(days, chemical: str, geography: str) -> bool:
    """The forecast's history rule, on the months the forecast reads.

    In Europe that is the published series and the card's months, against the Europe minimum of four
    (Stefan, 22 September 2026). Everywhere else it is the card's own months.
    """
    if enough_history(days):
        return True
    if not days or str(geography or "").strip() != "Europe":
        return False
    from raw_material_forecasting.market_history import HISTORY_NAME

    published = [day for line in _load_market_history(chemical, geography, display_currency())
                 if line["name"] == HISTORY_NAME for day, _value in line["points"]]
    return bool(published) and enough_history(list(days) + published,
                                              minimum_months=EUROPE_MIN_MONTHS)


def _market_history_days(governed_points, snapshot, identity_keys) -> list:
    """The dates this market was priced on: the governed series, or the prints when there is none.

    Kept out of the customer view so the engine's field names stay out of it.
    """
    if governed_points:
        return [point.get("reference_date") for point in governed_points]
    # Of the kind of price this market is quoted in, so "how long have we priced this market" is
    # not answered by months that belong to the other record. No Current Market card holds two
    # kinds today (13 of 13, measured 3 November 2026), so this changes nothing now and cannot
    # drift later - the same rule the Forecast page's banner uses, not a second copy of it.
    keys = set(same_kind_of_price(getattr(snapshot, "identities", []), identity_keys))
    return [row.get("observation_date") for row in getattr(snapshot, "observations", [])
            if row.get("identity_key") in keys]


def _governed_points(identity: dict) -> list[dict]:
    """The engine's governed series for one market, in the display currency.

    Converted here rather than in the engine: the store keeps each series in the currency it is
    governed in, and the screen converts at the rate of each point's own date, which is the same rule
    the rest of the page uses. The value and the two ends of its spread are converted at one rate per
    point, so the range cannot drift off the number it brackets.
    """
    # Looked up here rather than at the call site: the customer views are held to the customer's
    # vocabulary, and the engine's field names are not part of it.
    identity_key = str((identity or {}).get("identity_key") or "")
    if not identity_key:
        return []
    try:
        payload = MarketDataEngineClient(MARKET_DATA_ENGINE_URL, timeout=1.5).governed_reference_series(identity_key)
    except MarketDataEngineUnavailable:
        return []
    points = payload.get("points") or []
    if not points:
        return []

    table = _load_fx_table()
    target = display_currency()
    source = str(payload.get("currency") or "")

    out: list[dict] = []
    for point in points:
        row = dict(point)
        row["source_currency"] = source
        row["currency"] = source
        day = _as_fx_date(row.get("reference_date"))
        converted = table.convert(float(row["reference_value"]), source, target, day) if table.available else None
        if converted is None:
            out.append(row)
            continue
        value, rate, rate_date = converted
        row["reference_value"] = value
        for key in ("low", "high"):
            if row.get(key) is not None:
                row[key] = round(float(row[key]) * rate, 2)
        row["currency"] = target
        row["fx_rate"] = rate
        row["fx_rate_date"] = rate_date.isoformat() if rate_date else None
        out.append(row)
    return out


def _as_fx_date(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _basis_note(incoterm: str) -> str:
    """What a reader needs to know about the price this selection shows, in one sentence.

    On DDP it is the published, non-negotiated Western Europe price, which is why it sits above what
    a contract buyer pays. On DAP it is delivered prices, and they stop in January 2017.
    """
    basis = incoterm.strip().upper()
    if basis == "DDP":
        return ("This is the published, non-negotiated Western Europe DDP price, monthly, before "
                "any discounts. ")
    if basis == "DAP":
        return "These are delivered prices, monthly, November 2013 to January 2017. "
    return ""


# How a market's lines are drawn.
#
# Stefan ruled the grammar on 26 September 2026, when a market drew ONE line:
#
#   green solid   what somebody else assessed or recorded
#   green dotted  the chart joining two readings that are far apart - nobody measured in there
#   blue solid    what we collect ourselves, from the day our own collection began
#
# On 27 October 2026 he ruled "Contract and spot shown separately", and a market began drawing up
# to three lines - assessed, contract, deal. Nothing was added to tell them apart. Colour said
# PROVENANCE and nothing at all said KIND, so every line's history stroke came out the same green:
# TDI China EXW drew "EXW - History", "EXW Contract - History" and "EXW Deal - History" in one
# colour, and its two dotted bridges in another one colour (Stefan, 30 October 2026, looking at it:
# "showing multiple series in one graph. And then also with the same color").
#
# They are not near-duplicates that could be merged. Measured on the live store the same day, TDI
# China: the assessed CFR series and the contract CFR series are both 422 readings from
# `icis_history` over the same dates, and only TEN of the 422 carry the same price - the contract
# price runs about fifty dollars above the spot assessment. Three real series, three real answers.
#
# So the two rulings get a channel each, and neither is overturned:
#
#   COLOUR says which price it is   assessed green, contract amber, deal violet, ask slate
#   STYLE says where it came from   solid = somebody else's record, dotted = a bridge
#   BLUE still means we read it ourselves, on whichever line it happens
#
# The bridge now takes its own line's colour instead of always the assessed green, which is what
# made "EXW Deal - Bridge" indistinguishable from "EXW - Bridge".
_ASSESSED = "#0f766e"
_COLLECTED = "#1d4ed8"
_KIND_COLOUR = {
    price_kinds.ASSESSED: _ASSESSED,
    price_kinds.CONTRACT: "#b45309",
    price_kinds.DEAL: "#7c3aed",
    price_kinds.ASK: "#64748b",
}


def _stroke_style(line_kind: str, stroke_kind: str) -> dict:
    """How one piece of one line is drawn: its colour from the kind, its style from the provenance."""
    colour = _KIND_COLOUR.get(price_kinds.kind_of(line_kind), _ASSESSED)
    if stroke_kind == market_lines_module.COLLECTED:
        return dict(color=_COLLECTED, dash="solid", width=2.6)
    if stroke_kind == market_lines_module.BRIDGE:
        return dict(color=colour, dash="dot", width=1.6)
    return dict(color=colour, dash="solid", width=2.0)


_STROKE_NAME = {
    market_lines_module.HISTORY: "History",
    market_lines_module.BRIDGE: "Bridge",
    market_lines_module.COLLECTED: "Collected",
}

def _build_market_lines_figure(lines, currency: str) -> go.Figure:
    """The market over its whole horizon, with every reading marked."""
    fig = go.Figure()
    if not lines:
        return fig
    named: set[str] = set()
    for line in lines:
        assessed = ", ".join(line.assessed_in)
        for stroke in line.strokes():
            name = f"{line.label} — {_STROKE_NAME.get(stroke.kind, stroke.kind)}"
            style = _stroke_style(line.kind, stroke.kind)
            fig.add_trace(go.Scatter(
                x=[pd.to_datetime(day) for day, _v in stroke.points],
                y=[value for _d, value in stroke.points],
                mode="lines", name=name, legendgroup=name,
                showlegend=name not in named,
                line=dict(color=style["color"], width=style["width"], dash=style["dash"]),
                hovertemplate=("%{x|%d %b %Y}<br>%{y:,.0f} " + currency + "/" + units.MT + "<br>"
                               + line.label + (f" · assessed {assessed}" if assessed else "")
                               + "<extra></extra>"),
            ))
            named.add(name)
    days = [pd.to_datetime(day) for line in lines for day, _v in line.points]
    first, last = min(days), max(days)
    fig.update_layout(
        height=520, margin=dict(l=18, r=18, t=40, b=20),
        paper_bgcolor="white", plot_bgcolor="white",
        hovermode="closest", dragmode="pan", showlegend=True,
        legend=dict(orientation="h", x=0, xanchor="left", y=1.04, yanchor="bottom",
                    font=dict(size=11)),
        font=dict(color="#223b53"),
    )
    fig.update_xaxes(showgrid=True, gridcolor="rgba(148,163,184,0.18)", linecolor="#d9e4ed",
                     ticks="outside", tickfont=dict(size=10),
                     range=[first, last + pd.Timedelta(days=20)],
                     rangeslider=dict(visible=True, thickness=0.09,
                                      bgcolor="rgba(148,163,184,0.10)",
                                      bordercolor="rgba(148,163,184,0.45)", borderwidth=1))
    fig.update_yaxes(showgrid=True, gridcolor="rgba(148,163,184,0.18)", linecolor="#d9e4ed",
                     title=units.per_unit(currency), tickformat=",.0f")
    return fig


def _render_market_lines_chart(lines, currency: str, market: dict, card: dict | None = None) -> None:
    """The market's whole record: what was assessed, what bridged it, what we read this morning."""
    if not lines:
        st.info("Price history for this market is being collected.")
        return
    held = [(day, value) for line in lines for day, value in line.points]
    # The same rule `_render_price_evidence_chart` has followed since 26 September 2026, on the
    # chart that was written after it and never got it. A trace of one reading is `mode="lines"`
    # with one point: Plotly has nothing to join and draws nothing, so what reaches the screen is
    # an empty grid with a range slider under it and a caption about dragging through time. Stefan
    # saw it on Polyether Polyol Canada CIF on 20 November 2026 - eight selections were in that
    # state, seven of them lanes two days old - and the first time he saw it, in September, what
    # he said was "the graph has disappeared".
    #
    # One reading is a fact about the market, so the page states it. Nothing is hidden by this: the
    # card above prints the price with its date, the sentence names the day, and the CSV below is
    # built from the same readings either way.
    if len(held) < 2:
        only_day = held[0][0] if held else ""
        st.info(f"This market has one reading so far, for {only_day}. A price line starts at the "
                "second reading.")
    else:
        st.plotly_chart(_build_market_lines_figure(lines, currency), width="stretch",
                        config={"displaylogo": False, "scrollZoom": True,
                                "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"]})
        drawn = {line.kind for line in lines}
        others = [price_kinds.display_kind(kind).lower() for kind in
                  sorted(drawn - {price_kinds.ASSESSED}, key=price_kinds.line_order)]
        if len(others) == 1:
            says_kinds = f" The {others[0]} price of the same market is drawn in its own colour."
        elif others:
            named = ", ".join(others[:-1]) + " and " + others[-1]
            says_kinds = f" The {named} prices of the same market are drawn in their own colours."
        else:
            says_kinds = ""
        st.caption("Green is the assessed market price, blue is collected by us, and a dotted "
                   "stretch is the chart joining two readings with nothing measured between them."
                   + says_kinds +
                   f" All values in {currency} per {units.MT}. Drag to move through time, "
                   "scroll to zoom.")
    rows = []
    for line in lines:
        for day, value in line.points:
            rows.append({"Date": day, "Terms": line.label,
                         # The line knows which of its own days we collected; it recorded that when
                         # it was built, from the source (masterplan G). This column used to ask a
                         # function that only had the date.
                         "Record": _STROKE_NAME.get(line.provenance(day), ""),
                         f"Price ({units.pair(currency)})": round(value, 2)})
    if rows:
        frame = pd.DataFrame(rows).sort_values(["Date", "Terms"])
        st.download_button(
            "Download this data (CSV)",
            data=frame.to_csv(index=False).encode("utf-8"),
            file_name=f"htc_market_{str(market.get('Chemical') or '').lower()}_"
                      f"{str(market.get('Geography') or '').lower().replace(' ', '_')}.csv",
            mime="text/csv", key="market_lines_export")


def _render_price_evidence_chart(snapshot, identity_keys, market: dict, currency: str,
                                 *, governed_points: list[dict] | None = None,
                                 card: dict | None = None) -> None:
    """The reference for the selected market over time, under the selection that produced it."""
    points = governed_history(governed_points) if governed_points else reference_history(snapshot, identity_keys)
    if not points:
        st.info("Price history for this market selection is being collected.")
        return
    # No second line on the chart. The published Western Europe series and the European delivered
    # purchases are markets of their own now, on DDP and on DAP, so a chart draws the market its
    # selector names and nothing else (Stefan, 23 September 2026).
    lines: list[dict] = []
    # A market with one reading drew an empty grid with a single dot on it and a caption inviting the
    # reader to drag through time. It reads as a broken chart, and the first thing it was said about
    # was "the graph has disappeared". One reading is a fact about the market, so the page states it.
    if len(points) < 2 and not lines:
        only = points[0]
        st.info(
            f"This market has one reading so far, for {only.day}. A price line starts at the second "
            "reading."
        )
    else:
        st.plotly_chart(_build_market_reference_figure(points, currency, lines),
                        width="stretch",
                        config={"displaylogo": False, "scrollZoom": True,
                                "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"]})
        st.caption("One price for each day a reading was held, the average of what the independent "
                   "publishers printed that day. "
                   + _basis_note(str(market.get(INCOTERM_LABEL) or ""))
                   + "Drag the chart to move through time, scroll to zoom, or use the slider below it.")
    export = market_reference_export_frame(points, currency, market)
    left, right = st.columns([1, 1])
    with left:
        st.download_button(
            "Download this data (CSV)",
            data=export.to_csv(index=False).encode("utf-8"),
            file_name=f"htc_market_price_{points[-1].day.replace('-', '')}.csv",
            mime="text/csv", key="market_reference_export",
        )
    with right:
        # The selectors decide what is in it (Stefan, 23 September 2026): this report is written
        # from the very points drawn above, for the market named in the selectors.
        card = card or {}
        st.download_button(
            "Download report (Word)",
            data=page_reports.current_market_docx(
                [(point.day, point.value) for point in points], market=market, currency=currency,
                kicker=str(card.get("kicker") or "Market price"),
                price=str(card.get("price") or ""), priced=str(card.get("priced") or ""),
                note=_basis_note(str(market.get(INCOTERM_LABEL) or "")).strip()),
            file_name=page_reports.file_name("Current_Market", market),
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            key="market_reference_report",
        )


@dataclass(frozen=True)
class SelectedMarketEvidence:
    """What the Forecast page shows beside the forecast, for the market that was selected."""
    reference_value: float | None
    latest_observation_date: str | None
    evidence_source_count: int
    price_min: float | None
    price_max: float | None
    currency: str | None
    evidence: list


def forecast_identity_for(identities: list[dict], observations: list[dict],
                          chemical: str, geography: str, incoterm: str) -> dict | None:
    """The priced series behind a lane the customer picked.

    Packaging is deliberately not matched. A customer chooses drums or an ISO tank as a commercial
    term; the published price series does not distinguish them, and requiring a match would leave
    every selection without a forecast.

    Where several series fit, a series that is **still being priced** beats one that is not, and
    only then does the one with the most readings behind it win.

    THE READING COUNT ALONE PICKED A DEAD SERIES (Stefan, 2 November 2026)
    ----------------------------------------------------------------------
    "Check Aliphatic Polyester, there is current market price but no forecast?" He was right, and
    the reason was here. `ALIPHATIC_POLYESTER_POLYOL | China | EXW` holds five stored series:

        POLYESTER_LOW_FOGGING  deal        41 readings   last 2017-05-01
        UNKNOWN                deal        41 readings   last 2017-05-01
        POLYESTER_AA_BG        assessed     9 readings   last 2026-10-30
        POLYESTER_AA_BG_EG     assessed     9 readings   last 2026-10-30
        POLYESTER_AA_EG        assessed     9 readings   last 2026-10-30

    41 beats 9, so the page forecast a series that stopped in May 2017 and then told the reader
    "This market is history. It is not being priced now, so it carries no forecast." - over a
    market priced on 30 October 2026 at CNY 12,168.14/MT, which Current Market was showing at the
    same moment. A count of readings measures how long a series RAN, not whether it is alive, and
    for a market whose collection has just started it points at exactly the wrong one.

    The test is the page's own, `_priced_into_the_forecast_years`: the newest reading falls in
    `FORECAST_FROM_YEAR` or later. The same rule already decides whether the LANE is offered at
    all (`customer_catalogue.offered`, FORECAST) - it was simply not applied when choosing the
    series inside the lane, which is the defect this application keeps finding: one rule, two
    places, and the copy that runs is the one nobody ruled on.

    WHAT IT COSTS, MEASURED AGAINST THE LIVE STORE (2 November 2026)
    ----------------------------------------------------------------
    Of the 61 lanes the Forecast page offers, the rule changes **two**, and both were wrong:

        ALIPHATIC_POLYESTER_POLYOL | China | EXW   2017-05-01 (41) -> 2026-10-30 (9)
        POLYETHER_POLYOL           | China | FOB   2025-01-01 (104) -> 2026-08-01 (2)

    Both series it moves away from answer the engine with `status: not_priced`, so no forecast is
    lost - both lanes were, and remain, NOT_GOVERNED. What changes is what the page SAYS: it stops
    calling a live market history, and says the true thing instead, that the series is young.

    Not the tighter `FORECAST_WITHIN_DAYS` window that `priced_since` uses for the lane. Measured
    on the same day, 91 days would also have moved `POLYETHER_POLYOL | United States | CIF` and
    `| FOB` off an 81- and a 100-reading series that carry a SUCCEEDED governed forecast, onto a
    one-reading series that carries none - because a monthly series printing on the 1st is 93 days
    old by 2 November. The year test is what this page already states, and it is enough.
    """
    wanted = (str(chemical).strip().upper(), str(geography).strip().upper(), str(incoterm).strip().upper())
    matches = [
        row for row in identities
        if (str(row.get("product_family") or "").strip().upper(),
            str(row.get("market_geography") or "").strip().upper(),
            str(row.get("commercial_basis") or "").strip().upper()) == wanted
    ]
    if not matches:
        return None
    held = Counter(str(row.get("identity_key")) for row in observations if row.get("price") is not None)
    newest: dict[str, str] = {}
    for row in observations:
        if row.get("price") is None:
            continue
        key = str(row.get("identity_key"))
        day = str(row.get("observation_date") or "")[:10]
        if day > newest.get(key, ""):
            newest[key] = day
    return max(matches, key=lambda row: (_still_priced(newest.get(str(row.get("identity_key")), "")),
                                         held.get(str(row.get("identity_key")), 0)))


def _still_priced(newest_day: str) -> bool:
    """Whether a series is one this page can forecast at all, by its own year rule.

    The same question `_priced_into_the_forecast_years` asks of a forecast payload, asked here of a
    stored series, so the page cannot choose a series it is about to call history while a live one
    sits beside it in the same lane.
    """
    year = str(newest_day or "")[:4]
    return year.isdigit() and int(year) >= FORECAST_FROM_YEAR


@st.cache_data(ttl=60, max_entries=4, show_spinner=False)
def _engine_release(page_release: str) -> str:
    """The release the engine answering this page says it is. Empty when it cannot be asked.

    Kept for a minute, and keyed on the page's own release so a page that has just been replaced
    asks again at once rather than trusting what the page before it was told.
    """
    del page_release
    try:
        return MarketDataEngineClient(MARKET_DATA_ENGINE_URL, timeout=1.5).engine_version()
    except MarketDataEngineUnavailable:
        return ""


def _engine_is_this_release() -> bool:
    """Whether the page and the engine answering it are the same release.

    A FEATURE THAT SPANS BOTH NEEDS BOTH
    ------------------------------------
    On the customer deployment the engine runs on a thread inside the page's own process. A
    publish replaces the page's code in place; whether the engine thread takes up its own new code
    without a reboot has never been demonstrated either way (masterplan, v1.2.112).

    v1.2.113 is the first release where that matters to a number on the screen. The engine takes
    China's VAT out of the readings and the Forecast page reads its reference price from those
    readings. A v1.2.113 page on a v1.2.112 engine would ask for net, be answered with the print,
    and set a price with the tax in it beside a forecast with the tax out: Polyether Polyol China
    at 1,597 against 1,527, a fall of 4.4% that nothing forecast.

    So the page asks. Where the two are not the same release it does not use what only the new
    engine can give it, and the owner is told in one line what to do. Nothing is restarted from
    here - a forced restart is what took the deployment down on 23 November.
    """
    return bool(VERSION) and _engine_release(VERSION) == VERSION


def _an_older_engine() -> str:
    """" · engine v1.2.112" beside the release in the header, and only while the two differ.

    The one place it can be seen without signing in, which is where a publish is checked from.
    Nothing is added when the two agree, or when the engine does not say what it is.
    """
    answering = "" if _engine_is_this_release() else _engine_release(VERSION)
    return f" · engine v{answering}" if answering[:1].isdigit() else ""


def _say_when_the_engine_is_older() -> None:
    """One line for the owner, on the pages it affects, when the engine has not caught up."""
    if _signed_in_email() != PLATFORM_ADMIN_EMAIL or _engine_is_this_release():
        return
    answering = _engine_release(VERSION) or "not answering with a release"
    # Since v1.2.115 the page starts a fresh engine itself, so this line means that did not work,
    # and it says why where the engine said.
    asked_of = getattr(embedded_engine, "status", None)
    refused = "; ".join(((asked_of() if asked_of else {}).get("refused") or {}).values())
    st.caption(f"Owner only: these pages are v{VERSION} and the market-data engine inside the app "
               f"is {('v' + answering) if answering[:1].isdigit() else answering}. "
               + (f"A fresh engine would not start ({refused}); it is tried again every three "
                  "minutes. " if refused else "")
               + "Until the app is rebooted (Manage app, then Reboot app) the prices here are as "
               "that engine reads them, and the Forecast card keeps the month as its reference.")


def _lines_of_the_market(identities: list[dict], sources, chemical: str, geography: str,
                         *, live: bool) -> list:
    """Every line a market draws, from its own readings, with Stefan's Yes and No applied.

    One function for the page that draws them and the page that only needs the price at the end of
    one of them. `live` is Current Market's one-year window; Historic Pricing draws the record.
    """
    drawn = market_lines_module.identities_of(identities, chemical=chemical, geography=geography)
    readings = _market_readings(tuple(sorted(drawn)), display_currency(), _snapshot_stamp())
    # Current Market draws one year; Historic Pricing draws the whole record (Stefan, 3 November
    # 2026: "everything older then 1 year is history"). The two pages share this function, so the
    # cut is made once, here, on the readings the lines are built from - not on the axis range,
    # which would leave twelve years in the figure for the slider to find and would not be the
    # split he asked for.
    if live:
        readings = market_lines_module.drawn_on_current(readings)
    lines = market_lines(identities, readings, sources, chemical=chemical, geography=geography)
    # A series Stefan set to No is not drawn either. The selectors already leave it out, but a
    # region inside a country (East China under China) is drawn as a line of its own on the
    # country's chart, and would otherwise come back that way (13 October 2026).
    taken_off = customer_controls.hidden()
    if taken_off:
        lines = [line for line in lines
                 if (str(chemical), line.region or str(geography), line.basis.upper(),
                     line.packaging) not in taken_off]
    return lines


def _packaging_the_page_opens_on(lines, catalogue, chemical: str, geography: str,
                                 incoterm: str) -> tuple[list[str], str]:
    """The packagings offered for these terms, and the one the selector lands on unprompted.

    `lines` are the lines on these terms. Only packagings the page has a series for, so no choice
    ends on an empty chart; and the one it opens on is the first that is priced now.
    """
    offered = tuple(customer_catalogue.packagings_offered(
        catalogue, chemical, geography, incoterm)) or packaging_choices(lines) or ("",)
    preferred = market_lines_module.opening_packaging(lines, incoterm, offered)
    options = _alphabetical(list(offered), display_packaging)
    return options, (preferred if preferred in options else options[0])


def _current_market_price(identities: list[dict], sources, catalogue, chemical: str,
                          geography: str, incoterm: str, *,
                          kind: str = "") -> tuple[str, float] | None:
    """The day and the figure Current Market prints for this market as it opens, or nothing.

    Not a second rule: the same lines, the same packaging and `market_lines.headline_price`, which
    is what the card itself calls. The readings are the ones Current Market already holds for this
    market, kept between runs, so asking costs nothing a reader of both pages has not paid.

    `kind` narrows it to one kind of price - the Forecast page states the kind its forecast is
    built on, so the price beside the forecast is that kind too (Stefan, 26 November 2026; see
    `market_lines.lines_of_kind`). On a market that holds one kind, which is nearly all of them,
    it changes nothing.
    """
    lines = lines_on(_lines_of_the_market(identities, sources, chemical, geography, live=True),
                     incoterm)
    if kind:
        lines = market_lines_module.lines_of_kind(lines, kind)
    _options, packaging = _packaging_the_page_opens_on(lines, catalogue, chemical, geography,
                                                       incoterm)
    return market_lines_module.headline_price(lines, incoterm, packaging)


def _kind_that_is_forecast(identities: list[dict], identity_key: str) -> str:
    """The kind of price - assessed, contract or deal - of the series a forecast is built on."""
    row = next((row for row in identities
                if str(row.get("identity_key")) == str(identity_key)), None)
    return price_kinds.kind_of(row.get("observation_type")) if row else ""


def forecast_identity_keys(identities: list[dict], observations: list[dict],
                           chemical: str, geography: str, incoterm: str) -> tuple[str, ...]:
    """Every stored series that is the market the customer selected, the chosen one first.

    The order is the contract, not an accident. The whole set is what the chart draws and what
    today's price is pooled from; the first element is the one series the forecast is built on,
    and it is the series `forecast_identity_for` chose. `equivalent_identity_keys` is what keeps
    that promise - see its own docstring for the four lanes where it used to be broken.
    """
    chosen = forecast_identity_for(identities, observations, chemical, geography, incoterm)
    if chosen is None:
        return ()
    return equivalent_identity_keys(identities, chosen) or (str(chosen.get("identity_key")),)


def selected_market_evidence(snapshot, identity_keys, *,
                             readings: list[dict] | None = None,
                             governed: list[dict] | None = None) -> SelectedMarketEvidence:
    """Today's price for the Forecast page, on the basis the forecast itself is built on.

    The banner puts today's price next to next month's forecast, so if they come from different
    arithmetic the reader is handed a move that did not happen. The forecast is built on the
    governed series, net of China's 13% VAT; this read the raw prints, which carry it. On
    17 September 2026 that put today at USD 2,623 against a forecast of USD 2,224, and most of
    that 18% gap was tax rather than a market anybody could trade on.

    `readings` is this market's own readings, prices and all. Without it the snapshot's are used,
    which is right only where the snapshot carries the full readings: the page's snapshot carries
    the index, whose prices are 1 rather than values, and a banner built from that would read
    USD 1. The caller fetches the market it is about and passes it in.

    `governed` is this market's governed series, and the page leaves it out: the page wants the
    engine asked. Every other caller states it, because `_governed_points` is an HTTP call to
    whatever engine is running on this machine, and a caller that leaves it out is answered from
    the live store rather than from the readings it passed in. On 3 November 2026 a test of this
    function asserted a date of 2026-08-01 that was in no fixture of its own: it was the newest
    governed point the running application held that afternoon. A reading for 1 October arrived on
    the 17th and the test failed, fifteen days after the code it tests had last been touched. An
    empty sequence is "the engine holds no governed series for this market", which is the other
    half of the rule and the branch that reads the prints.
    """
    # The series the forecast itself runs on - `identity_keys[0]`, which `equivalent_identity_keys`
    # puts first - and not whichever member of the set the snapshot happened to list first. The
    # governed series read here is what the banner prints as "today", directly above a forecast
    # built from `identity_keys[0]`, so taking the two from different series puts a move on the
    # screen that did not happen. Measured 3 November 2026: on `POLYETHER_POLYOL | China | FOB`,
    # `| United States | CIF` and `| FOB` the snapshot's order handed this the other series of the
    # pair - a deal record against an assessed forecast on China FOB, and the reverse on the two
    # United States lanes.
    primary = str(identity_keys[0]) if identity_keys else ""
    identity = next((dict(row) for row in getattr(snapshot, "identities", [])
                     if str(row.get("identity_key")) == primary), {})
    governed = _governed_points(identity) if governed is None else list(governed)
    priced = snapshot if readings is None else with_readings(snapshot, readings)
    # One kind of price for the one figure. The whole set is what the chart draws, and it is right
    # that it draws both an assessment and a deal record in their own colours. But the banner prints
    # a single "today" directly above a forecast built from `identity_keys[0]`, and an average of
    # the two kinds belongs to neither - see `same_kind_of_price` for the four banners on which that
    # average read a move of up to 58% that no market made.
    pooled = same_kind_of_price(getattr(snapshot, "identities", []), identity_keys)
    state = (state_from_governed_series(governed, identity) if governed
             else build_current_market_state(priced, pooled, as_of=None))
    key_set = set(pooled)
    rows = [dict(row) for row in priced.observations
            if row.get("identity_key") in key_set and row.get("price") is not None]
    rows.sort(key=lambda row: str(row.get("observation_date") or ""))
    return SelectedMarketEvidence(
        reference_value=state.current_value,
        latest_observation_date=state.current_date,
        evidence_source_count=state.source_count,
        price_min=state.evidence_low,
        price_max=state.evidence_high,
        currency=state.currency or display_currency(),
        evidence=rows,
    )


def render_market_forecast(runtime_access: RuntimeAccess | None = None) -> None:
    st.markdown("## Market Selection")

    # Loaded first, because the selectors are built from it. This page used to offer a fixed roster -
    # eleven chemicals, five geographies, seven Incoterms - and decide afterwards whether any of it
    # had a price, while Current Market offered only what the store held. The same market could be
    # chosen on one page and not the other, and the two opened on different defaults.
    #
    # The index, not every reading. The twelve things read off it here - which chemicals carry a
    # forecast, which of those have a current price, which geographies and which terms, what to
    # open each selector on, whether there is any history behind a market with no price, and which
    # stored series the selection names - go through `customer_catalogue.offered`, `best_row`,
    # `history_span` and a count of priced readings per key. Read line by line, every one of them
    # touches `identity_key`, `source_id`, `observation_date`, `publication_date` and a price only
    # ever as `is None`. The banner and the chart need numbers and ask for this market's own
    # readings further down.
    snapshot = _load_step6_snapshot(readings="index")
    if snapshot is None:
        st.info("Market evidence is being collected.")
        return
    identities = [dict(row) for row in snapshot.identities if is_published_market(row)]
    if runtime_access is not None:
        identities = [row for row in identities if identity_allowed(row, runtime_access.scope)]

    # Only chemicals with a series priced in the forecast years: the Forecast column of the
    # Data & Sources eligibility table (Stefan, 13 October 2026).
    drawable = customer_catalogue.offered(identities, snapshot.observations,
                                          customer_catalogue.FORECAST,
                                          forecast_from_year=FORECAST_FROM_YEAR)
    # And which of them the Current Market page would also offer, so this page can say when it is
    # forecasting a market nobody has a current price for (masterplan G). Measured 28 October 2026:
    # 46 of the 120 markets the rules offer here. The arrears are the reason - Comext and Comtrade
    # publish two to three months late, so their newest reading is always inside the forecast years
    # and always outside the current-price window. Nothing on the card used to say so, and the page
    # that would say so does not offer the market.
    priced_now = customer_catalogue.offered(identities, snapshot.observations,
                                            customer_catalogue.CURRENT,
                                            sources=snapshot.sources)
    chemical_options = _entitled_chemicals(
        customer_catalogue.chemicals_offered(drawable), runtime_access)
    if not chemical_options:
        st.info("No forecast market is available within the active subscription.")
        return
    _carry_market_choice("forecast", "current_market")
    s1, s2, s3, s4 = st.columns([1.45, 1.45, 1.35, 0.85])
    with s1:
        # Sorted, with the page still opening on the chemical it opened on before.
        #
        # `index` is gone and `_keep_choice` does the work, as it already did for the two selectors
        # below. The index only ever applied on the FIRST render - once the key is in the session
        # Streamlit uses that and ignores it - and it could not have carried a market from Current
        # Market anyway, because a value in the session that the page does not offer raises rather
        # than falling back.
        opens_on = chemical_options[0]
        chemical_options = _alphabetical(chemical_options, display_chemical)
        _keep_choice("forecast_chemical", chemical_options, opens_on)
        chemical = st.selectbox("Chemical", chemical_options,
                                format_func=display_chemical, key="forecast_chemical")
    # Cascaded and defaulted exactly as Current Market does it, so one choice lands on one market on
    # both pages - including the part added on 27 September 2026, that a selector offers only what
    # there is something to show. A forecast page that offers a market it holds nothing for is the
    # same dead end as a chart page that does.
    # Only what this page can forecast. It used to offer every market the store draws, including
    # markets whose last reading is from 2017, and then answer the reader with "This market is
    # history. It is not being priced now, so it carries no forecast." Stefan, 28 September 2026:
    # "if there is no Forecast that can be made from the time series AND it is only history, then
    # no reason to show this timeseries on the page." The test is the page's own - priced in the
    # forecast years - and not Current Market's 35-day collection rule, which would also have taken
    # away the six monthly European and Middle East markets that do carry a forecast.
    geography_options = _entitled_values(
        geographies_with_a_market(drawable, chemical),
        runtime_access.scope.geographies if runtime_access else frozenset(), runtime_access)
    if not geography_options:
        st.info(_not_collected_note(identities, snapshot.observations, chemical=chemical))
        return
    preferred_geography = (best_market_option(identities, "geography",
                                              observations=snapshot.observations,
                                              references=snapshot.references, chemical=chemical)
                           or geography_options[0])
    with s2:
        geography_options = _alphabetical(geography_options, _display_market_dimension)
        _keep_choice("forecast_geography", geography_options, preferred_geography)
        origin = st.selectbox("Geography", geography_options, key="forecast_geography")
    incoterm_options = _entitled_values(
        terms_with_a_market(drawable, chemical, origin),
        runtime_access.scope.incoterms if runtime_access else frozenset(), runtime_access)
    if not incoterm_options:
        st.info(_not_collected_note(identities, snapshot.observations,
                                    chemical=chemical, geography=origin))
        return
    preferred_basis = (best_market_option(identities, "incoterm",
                                          observations=snapshot.observations,
                                          references=snapshot.references, chemical=chemical,
                                          geography=origin)
                       or incoterm_options[0])
    with s3:
        incoterm_options = _alphabetical(incoterm_options, _basis_namer(chemical, origin))
        _keep_choice("forecast_incoterm", incoterm_options, preferred_basis)
        incoterm = st.selectbox(INCOTERM_LABEL, incoterm_options, key="forecast_incoterm",
                                format_func=_basis_namer(chemical, origin))
    with s4:
        render_display_currency_selector()

    st.markdown(f"## {market_title(chemical, origin)}")
    # The line under this heading read "Commercial identity: EXW | ISO Tank. Incoterm and packaging
    # are controlled as separate dimensions." The selectors two rows above already say EXW and ISO
    # Tank, and how the store keeps its dimensions is ours. Removed on Stefan's instruction,
    # 16 September 2026 - asked for once before, argued against, and he was right.

    # The same published set the selectors were built from. It matched against every stored series,
    # transaction records included, and relied on a readings count to keep them out.
    #
    # `identity_keys[0]` is the series the pick chose, and `_load_forecast` below is handed it. It
    # used to be whichever key sorted first, which on four Polyether Polyol lanes was a one-reading
    # August 2026 stub standing in front of a hundred-month record - three of them with a governed
    # forecast already stored that no customer could see.
    identity_keys = forecast_identity_keys(identities, list(snapshot.observations), chemical,
                                           origin, incoterm)
    if not identity_keys:
        st.info(f"No current market price is held for {display_chemical(chemical)} in {origin}.")
        return
    # The one place on this page that needs prices as values: the banner's "today", the dates
    # beside it, and the evidence spread. One market's readings instead of 322 markets'.
    state = selected_market_evidence(
        snapshot, identity_keys,
        readings=_market_readings(tuple(sorted(identity_keys)), display_currency(),
                                  _snapshot_stamp()))

    # The reference used to lead this page in four large metric tiles, above the forecast the page
    # exists for. It is context, so it sits in the Market Evidence panel beside the chart and the
    # forecast leads.
    if state.reference_value is None:
        st.info("The market price is building. It needs two independent comparable prices "
                "within the current window.")
    # The price Current Market shows for this market, on the day it shows it. The card, the
    # percentage on it, the point the chart starts from, the CSV and the report all read this one
    # figure (Stefan, 24 November 2026). None where the market has no current price.
    #
    # Of the kind the forecast is built on (Stefan, 26 November 2026). Polyether Polyol Japan FOB
    # holds one assessed reading at 1,867 and a customs series near 6,000; the forecast is built
    # on the customs series, and with the newest reading of any kind beside it the card read
    # "up 231.1%".
    forecast_kind = _kind_that_is_forecast(identities, identity_keys[0])
    market_price = _current_market_price(identities, snapshot.sources, priced_now,
                                         chemical, origin, incoterm, kind=forecast_kind)
    # Only from an engine of this release: an older one answers with the tax still in the
    # readings, and that figure does not belong beside a forecast with the tax out.
    if not _engine_is_this_release():
        market_price = None
    # What Current Market leads with, where that is another figure: the two pages then print two
    # prices for one market, and the reader is told why rather than left to find it.
    leads_current_market = (_current_market_price(identities, snapshot.sources, priced_now,
                                                  chemical, origin, incoterm)
                            if market_price is not None else None)
    _say_when_the_engine_is_older()

    forecast_payload: dict = {}
    forecast_horizons = [4, 8, 12]
    if runtime_access is not None:
        max_weeks = runtime_access.scope.forecast_horizon_months * 4
        forecast_horizons = [weeks for weeks in forecast_horizons if weeks <= max_weeks]
    if forecast_horizons:
        forecast_payload = _load_forecast(identity_keys[0], tuple(forecast_horizons))
        # Today's price is the consolidated value, not the last single print. Two numbers for today
        # on one screen - 2,634 in the evidence panel and 2,648 on the cards - is one number too
        # many, and the consolidated one is the one the whole engine is built to produce.
        render_forecast_banner(
            forecast_payload, forecast_horizons,
            # "China Market" is two readings - the market in China, or the market for Chinese
            # material. The banner says which, and the basis is what decides it: FOB China is
            # material leaving China, CIF China is material landing in it. This said "China
            # origin" under both, because the Geography selector lands in a variable called
            # `origin` and the word was taken from the variable rather than from the price.
            title=" — ".join(
                part for part in (_display_market_dimension(chemical),
                                  geography_phrase(origin, incoterm)) if part),
            today=state.reference_value,
            as_at=state.latest_observation_date,
            market_price=market_price,
        )
        # Said under the banner, where the reader has just seen the figure it is about. The card
        # carries a dated "Last print" line, so nothing here is contradicted - what was missing is
        # that a market can be forecast while having no current price at all (masterplan G).
        # Any packaging of it: this page chooses a chemical, a geography and the terms, and leaves
        # packaging to the lines on the chart, so "is this market priced now" is asked at the three
        # fields the reader actually picked.
        if market_price is not None and leads_current_market not in (None, market_price):
            st.caption(
                "This market holds more than one kind of price. The forecast is built on the "
                f"{price_kinds.display_kind(forecast_kind).lower()} price, so today's price here "
                "is that one. Current Market leads with the newest price of any kind, which for "
                "this market is a different figure.")
        if not any(one[:3] == (chemical, origin, incoterm) for one in priced_now):
            st.caption(
                "No current price: nobody has priced this market inside the last "
                f"{market_lines_module.CURRENT_WITHIN_DAYS} days, so it is not on Current "
                "Market. The forecast below is made from the history above, and the date beside "
                "each figure says how old it is.")
        # Which of the two regimes this card is (masterplan H). For 40 markets the figure is the
        # method chosen on measured evidence; for 49 the page re-forecasts with the challengers and
        # picks its own winner, and until now the page did not say which a reader was looking at.
        # No method is named - Stefan, 23 October 2026: no sourcing behind a number reaches a
        # customer - so this says how the choice was made and nothing about what was chosen.
        chosen = forecast_cards.how_this_was_chosen(forecast_payload, forecast_horizons[0])
        if chosen:
            st.caption(chosen)
        # "How the forecast method is chosen" is NOT shown here. Stefan, 23 October 2026: no
        # sourcing behind a number reaches a customer, for a forecast or a current price. That
        # expander named the method in force, its matched MAE and how often its direction was
        # right - the method's whole provenance, on the page a buyer reads. It belongs to Platform
        # Admin, where `admin_rows` already serves it, and `render_method_fit_compact` is left in
        # place for that side rather than deleted.
    else:
        st.info("Contact HTC Global to add forecast horizons to your subscription.")

    st.write("")
    # The banner carries one month; the chart is where the work looks further. The heading says so
    # rather than leaving the reader to notice.
    has_forecast = _forecast_history_enough(forecast_payload) and any(
        model.get("model") == _card_model(forecast_payload) and model.get("status") == "ok"
        and model.get("points")
        for model in (forecast_payload.get("models") or ()))
    st.markdown("### Price history and forecast" if has_forecast else "### Price history")
    if True:
        # The selector says what the banner says. A page offering "12 weeks" above a banner headed
        # "October 2026" asks the reader to hold two units in their head for one horizon.
        _card_points = [point for model in (forecast_payload.get("models") or ())
                        if model.get("model") == _card_model(forecast_payload)
                        and model.get("status") == "ok"
                        for point in (model.get("points") or ())]
        # No forecast, no horizon to choose. The control used to render regardless, and with no
        # points behind it `horizon_label` has no resolution to read, so it fell back to the weeks
        # the engine was ASKED in - "4 weeks | 8 weeks | 12 weeks" - on a market whose banner one
        # line above said the forecast was being prepared. Two units and nothing to apply them to.
        # The chart opens on the nearest horizon, which is the one the banner above it is about.
        #
        # It opened on the furthest. Stefan, 2 October 2026, read the banner's November figure
        # against the three-month band edges on the chart and took them for the same number said
        # twice. They are two horizons on one screen, and the control sitting between them said
        # "3 months". Opening on one month puts the picture and the banner on the same month, and
        # the reader still steps out to two and three whenever they want to.
        chart_horizon = min(forecast_horizons or [12])
        if _card_points and has_forecast:
            chart_horizon = st.radio(
                "Horizon", forecast_horizons or [12],
                index=0,
                horizontal=True, key="forecast_chart_horizon",
                format_func=lambda weeks: forecast_cards.horizon_label(weeks, _card_points),
                label_visibility="collapsed",
            )
        # NO purchase history here. This is a customer's Forecast page, and the purchase series is
        # HTC's own negotiated prices and KGI's supplier contracts - what HTC pays BASF, what it pays
        # Wanhua, and the gap between them. It went on this chart and should never have: the store
        # marks the whole lane INTERNAL/OWNED and the module that loads it says in its first
        # paragraph that it must never reach a customer-facing view.
        #
        # It lives in Platform Admin instead. Not "this page, hidden when a customer is logged in" -
        # that is one entitlement bug away from a leak, and the leak would be HTC's buying prices.
        figure = _build_forecast_figure(forecast_payload, chart_horizon,
                                        str(forecast_payload.get("currency") or ""),
                                        display_currency(), _load_fx_table(),
                                        market_price=market_price)
        if figure.data:
            # Toolbar off. It appears on hover in the top right corner, exactly where the range
            # buttons sit, and covers them - and it is developer furniture on a customer page: zoom,
            # pan, autoscale, and a "download plot as a PNG" nobody asked for. The range buttons and
            # the slider along the bottom already do the navigating.
            st.plotly_chart(figure, width="stretch",
                            config={"displayModeBar": False, "displaylogo": False,
                                    "scrollZoom": True})
            st.caption("Drag the chart to move through time, scroll to zoom, or use the slider "
                       "below it. The shaded band opens with distance because the further ahead, "
                       "the less is known.")
            export = forecast_export_frame(
                forecast_payload, str(forecast_payload.get("currency") or ""),
                display_currency(), _load_fx_table(), market_price=market_price)
            # The report's "Today" is the market price where there is one, like the card above.
            today_value, today_day = (
                (market_price[1], market_price[0]) if market_price is not None
                else (state.reference_value, state.latest_observation_date))
            left, right = st.columns([1, 1])
            with left:
                st.download_button(
                    "Download this data (CSV)",
                    data=export.to_csv(index=False).encode("utf-8"),
                    file_name=f"htc_price_history_and_forecast_{as_of_date(forecast_payload.get('as_of')):%Y%m%d}.csv",
                    mime="text/csv", key="forecast_chart_export",
                )
            with right:
                market = {"Chemical": _display_market_dimension(chemical), "Geography": origin,
                          INCOTERM_LABEL: _display_basis(incoterm)}
                history, ahead = _report_series(export, display_currency())
                st.download_button(
                    "Download report (Word)",
                    data=page_reports.forecast_docx(
                        history, ahead, market=market, currency=display_currency(),
                        today_price=(units.with_unit(f"{display_currency()} {today_value:,.0f}")
                                     if today_value is not None else ""),
                        priced=str(today_day or "")),
                    file_name=page_reports.file_name("Forecast", market),
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    key="forecast_chart_report",
                )
        elif not forecast_payload:
            # The engine did not answer. That is a fact about this minute, not about the market,
            # and the page says so rather than reporting an empty market (23 September 2026).
            st.info("The price history for this market is on its way. Refresh in a moment.")
        else:
            st.info("Price history is being collected for this market.")

    if forecast_payload and not _card_points:
        # Said once, under the chart, where the reader has just seen the history that exists.
        st.caption("The price history is here; the forecast is not yet. It needs two months "
                   "running at the end of the series to measure a move from, and a track record "
                   "behind it before any range is worth printing.")

def _per_usd(currency: str) -> float:
    """Units of `currency` per 1 USD, from the ECB table, or 1.0 where it cannot be had.

    The table is EUR-based, so this is two lookups divided. A default, not a lock: every other
    number on this page is entered by hand for one route, and the rate is no different - a reader
    pricing a forward shipment uses the rate they have been quoted, not yesterday's fixing.
    """
    code = str(currency or "").strip().upper()
    if code == "USD":
        return 1.0
    table = _load_fx_table()
    if not table.available:
        return 1.0
    here = table.rate_on(code, table.latest_date)
    dollars = table.rate_on("USD", table.latest_date)
    if not here or not dollars or not dollars[0]:
        return 1.0
    return round(here[0] / dollars[0], 4)


def render_delivered_price() -> None:
    st.markdown("## Landed price calculator")
    st.caption("Translate a market price into one route-specific landed price.")
    st.info("Duty and anti-dumping cost are entered manually for this route.")

    col1, col2 = st.columns(2, gap="large")
    with col1:
        st.markdown("#### Market and logistics")
        # Every label reads the unit the store keeps rather than spelling it out: six literals
        # here were the last of the hardcoded tonnes (masterplan E, v1.2.13).
        market_price = st.number_input(f"Market price per {units.MT}", min_value=0.0, value=1800.0,
                                       step=10.0)
        freight = st.number_input(f"Freight per {units.MT}", min_value=0.0, value=120.0, step=5.0)
        packaging = st.number_input(f"Packaging per {units.MT}", min_value=0.0, value=35.0, step=5.0)
        insurance = st.number_input(f"Insurance per {units.MT}", min_value=0.0, value=8.0, step=1.0)
    with col2:
        st.markdown("#### Route and currency")
        duty = st.number_input(f"Duty / ADD per {units.MT}", min_value=0.0, value=0.0, step=5.0)
        route_cost = st.number_input("Other route / port cost", min_value=0.0, value=12.0, step=1.0)
        # The two currencies are NAMED now, which is the whole of E's remainder. The page asked for
        # "Source currency per USD" and "Target currency per USD" - two rates for two currencies it
        # never asked the name of - and then printed the answer as a bare number per MT with no
        # currency on it, directly beside a figure labelled USD. A reader could not tell what the
        # second number was in. Three currencies, the store's own (`DISPLAY_CURRENCIES`), since
        # those are the three every reading is converted into.
        source_currency = st.selectbox("Prices above are entered in",
                                       _alphabetical(list(DISPLAY_CURRENCIES)),
                                       key="delivered_source_currency")
        target_currency = st.selectbox("Show the delivered price in",
                                       _alphabetical(list(DISPLAY_CURRENCIES)),
                                       key="delivered_target_currency")
        source_fx = st.number_input(f"{source_currency} per USD", min_value=0.0001,
                                    value=_per_usd(source_currency), step=0.1,
                                    key="delivered_source_rate")
        target_fx = st.number_input(f"{target_currency} per USD", min_value=0.0001,
                                    value=_per_usd(target_currency), step=0.1,
                                    key="delivered_target_rate")
        st.caption(f"Both rates start at the latest ECB fixing and are yours to change. {FX_RULE}")

    if st.button("Calculate delivered price", type="primary", width="stretch"):
        result = calculate_delivered_price(
            market_price_per_tonne=market_price,
            freight_per_tonne=freight,
            packaging_per_tonne=packaging,
            insurance_per_tonne=insurance,
            duty_per_tonne=duty,
            other_route_cost_per_tonne=route_cost,
            source_currency_per_usd=source_fx,
            target_currency_per_usd=target_fx,
        )
        a, b = st.columns(2)
        a.metric(f"Delivered price, {units.pair('USD')}", f"{result.usd_per_tonne:,.2f}")
        b.metric(f"Delivered price, {units.pair(target_currency)}",
                 f"{result.target_currency_per_tonne:,.2f}")
        breakdown = pd.DataFrame({"Component": list(result.components.keys()),
                                  units.pair("USD"): list(result.components.values())})
        st.dataframe(breakdown, hide_index=True, width="stretch")
        if target_currency == "USD" and source_currency == "USD":
            st.caption("Both figures are the same because the prices were entered in USD and the "
                       "delivered price is asked for in USD.")


SIGNAL_DATE_WINDOWS: tuple[tuple[str, int | None], ...] = (
    ("Last 30 days", 30),
    ("Last 90 days", 90),
    ("Last 12 months", 365),
    ("All time", None),
)


def _signal_filters(signals, key_prefix: str):
    """Filter row for a signal table. Returns the narrowed list.

    Signals are never aged out of the feed - a capacity addition from last year is still a fact about
    the market. But a count over everything ever collected keeps climbing while saying nothing about
    what is recent, so the timeframe is a lens the reader chooses. "All time" is the default, because
    a default window would be aging out through the back door.
    """
    from raw_material_forecasting.producer_signals import filter_options, filter_signals

    options = filter_options(signals)
    labels = [label for label, _ in SIGNAL_DATE_WINDOWS]
    columns = st.columns([1.2, 1.4, 1, 1, 1.2])
    window_label = columns[0].selectbox("Period", labels, index=len(labels) - 1, key=f"{key_prefix}_window")
    producers = columns[1].multiselect("Producer", options["producers"], key=f"{key_prefix}_producer")
    chemicals = columns[2].multiselect("Chemical", options["chemicals"], key=f"{key_prefix}_chemical")
    geographies = columns[3].multiselect("Geography", options["geographies"], key=f"{key_prefix}_geography")
    kinds = columns[4].multiselect("Type", options["kinds"], key=f"{key_prefix}_kind")

    days = dict(SIGNAL_DATE_WINDOWS)[window_label]
    since = (date.today() - timedelta(days=days)) if days else None
    return filter_signals(
        signals, since=since, producers=producers, chemicals=chemicals,
        geographies=geographies, kinds=kinds,
    )


def render_market_intelligence(runtime_access: RuntimeAccess | None = None) -> None:
    """What is going on in the TDI market in China: the situation, what happened, what it means.

    The three sections answer three different questions and are not interchangeable. A reader who
    wants to know whether to move a contract reads Impact; one who wants to check a claim reads
    Developments; one deciding who to talk to reads the roster.

    Every piece of markup is built in `market_intelligence`, so the page can be rendered and looked
    at without starting Streamlit - which is the only way a layout gets checked before it ships.
    """
    # The same selection as the other two pages. Without it the page read "TDI · China" whatever
    # the reader had chosen everywhere else, and showed TDI's plants under that heading.
    origin_options = _entitled_values(ORIGINS, runtime_access.scope.geographies, runtime_access) \
        if runtime_access else list(ORIGINS)
    every_chemical = [*ACTIVE_CHEMICALS, *FEEDSTOCK_CHEMICALS]
    entitled_chemicals = _entitled_chemicals(every_chemical, runtime_access)
    # Only chemicals with intelligence in at least one entitled geography: the Market Intelligence
    # column of the Data & Sources eligibility table (Stefan, 13 October 2026).
    told = customer_catalogue.intelligence_markets(entitled_chemicals, origin_options,
                                                   market_intelligence.has_intelligence)
    chemical_options = [c for c in entitled_chemicals if any(c == one[0] for one in told)]
    if not chemical_options or not origin_options:
        st.info("No market is available within the active entitlement.")
        return
    s1, s2, _rest = st.columns([1.35, 1.35, 3.1])
    with s1:
        opens_on = chemical_options[0]
        chemical_options = _alphabetical(chemical_options, display_chemical)
        chemical = st.selectbox("Chemical", chemical_options,
                                index=chemical_options.index(opens_on),
                                key="intelligence_chemical",
                                format_func=display_chemical)
    # Cascaded, the way Current Market cascades its geographies off the store.
    #
    # The roster used to stand wherever a chemical had nothing anywhere, so the page could still
    # be asked about it. Counted against the store on 11 October 2026 that was 165 dead options
    # against 9 live ones: eleven chemicals each offering fifteen geographies with nothing behind
    # any of them. Stefan: "no choice if not possible to choose." A chemical we hold no
    # intelligence for says so in one line and offers nothing to pick.
    served = market_intelligence.markets_with_intelligence(chemical, origin_options)
    if not served:
        st.info(f"No market intelligence is held for {display_chemical(chemical)} yet.")
        return
    with s2:
        geography_options = _alphabetical(served, _display_market_dimension)
        _keep_choice("intelligence_origin", geography_options, geography_options[0])
        origin = st.selectbox("Geography", geography_options, key="intelligence_origin")

    plants = market_intelligence.load_plants(chemical=chemical, geography=origin)
    pipeline = market_intelligence.load_pipeline(chemical=chemical, geography=origin)
    signals = load_producer_signals(PRODUCER_SIGNALS_PATH)
    # The SELECTED chemical, not the entitlement. The entitlement says what a reader may see; the
    # selector says what they asked to see, and a polyol page carrying only TDI signals is the same
    # fault as a polyol page headed TDI.
    #
    # Geography is deliberately not passed: a joint venture abandoned in Saudi Arabia bears on a
    # company buying in China, and `customer_visible_signals` ignores it for that reason.
    entitled = runtime_access.scope.chemicals if runtime_access is not None else None
    scope = [chemical] if entitled is None else [c for c in [chemical] if _scope_allows(c, entitled)]
    visible = customer_visible_signals(signals, chemicals=scope or entitled)

    # The route graph decides what a signal has to do with the chemical the reader picked. Without
    # it the page falls back to the stored sentence, which is what put "Feedstock for TDI" under a
    # polyether polyol heading.
    try:
        routes = load_chemical_route_graph()
    except (OSError, ValueError):
        routes = None
    # The page as a branded Word report (Stefan, 22 September 2026), built from the same roster,
    # signals and Impact lines as the screen, so the two cannot say different things.
    with _rest:
        st.markdown('<div style="height:28px"></div>', unsafe_allow_html=True)
        st.download_button(
            "Download report (Word)",
            data=mi_report.market_intelligence_docx(plants, pipeline, visible, chemical=chemical,
                                                    geography=origin, graph=routes),
            file_name=mi_report.report_file_name(chemical, origin),
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            key="intelligence_report_docx",
        )
    # The two long tables fold. Everything else is written straight out.
    #
    # A heading listed in `market_intelligence.FOLDED` takes the block after it inside an expander
    # named for the section, so the plant roster and the signal table open on a click instead of
    # pushing the rest of the page below the fold (Stefan, 25 September 2026). Which sections fold
    # is decided next to the headings themselves, so the page and the report stay one description.
    blocks = list(market_intelligence.page_blocks(plants, pipeline, visible,
                                                  chemical=chemical, geography=origin,
                                                  graph=routes))
    folding: str | None = None
    for block in blocks:
        label = market_intelligence.FOLDED.get(block)
        if label:
            folding = label
            continue
        if folding:
            with st.expander(folding, expanded=False):
                st.markdown(block, unsafe_allow_html=True)
            folding = None
            continue
        st.markdown(block, unsafe_allow_html=True)


def render_chemical_relationships(runtime_access: RuntimeAccess | None = None) -> None:
    # The study reads the engine's own observations rather than the display-converted snapshot: it
    # converts each price at the rate of that price's own date, which a single display currency
    # conversion would have already flattened.
    try:
        client = MarketDataEngineClient(MARKET_DATA_ENGINE_URL, timeout=6.0)
        snapshot = client.snapshot()
        observations = snapshot.observations
        # Producer price announcements. Correlation answers the cost question; an announcement
        # answers whether the producer priced ahead of the cost, and it is dated evidence rather
        # than a statistic.
        events = snapshot.events
        # Upstream history that is not a price: indices, proxies, benchmarks from other markets.
        # Empty on an engine older than v0.4.81, which degrades to "no reference data" rather than
        # breaking the page.
        #
        # The internal view asks for the series HTC holds under terms that do not allow showing them
        # on; a customer session does not, and the engine withholds them. EUROPUR is the reason:
        # the report is confidential, its member data is aggregated under CEFIC rules, and its
        # series carry the report and chart they were drawn from in their own labels.
        reference = client.reference_series(internal=runtime_access is None)
        # What the engine managed to load at startup, so a missing file is reported as a missing
        # file rather than looking like a chemical nobody has sourced yet.
        imports = client.imports()
    except MarketDataEngineUnavailable:
        st.markdown("## Chemical Relationships")
        st.warning("The Step 6 market-data engine is not reachable, so no price history can be read.")
        return
    try:
        graph = load_chemical_route_graph()
    except (OSError, ValueError) as exc:
        st.markdown("## Chemical Relationships")
        st.warning(f"The governed chemical routes could not be loaded: {exc}")
        return

    # None means the internal view, which sees every raw material; a customer sees only the chains
    # their entitlement covers, and the selector itself is filtered so the rest are not even named.
    allowed = None if runtime_access is None else runtime_access.scope.chemicals
    render_relationship_study(observations, _load_fx_table(), graph, allowed=allowed,
                              reference=reference, imports=imports, events=events)


def render_relationship_method(runtime_access: RuntimeAccess | None = None) -> None:
    """The working behind the relationship numbers. PLATFORM ADMIN ONLY.

    The series each number was built from, whether the chain's steps add up to the direct
    measurement, whether a raw material moves ahead of its own feedstock, and how the weekly lane is
    filling. All of it used to sit on the customer page between the results, where it read as the
    page arguing with itself, and the reach control that came with it was one of two settings that
    made the counts on that page move around.
    """
    st.markdown("## The working behind the relationships")
    st.caption("Internal. The evidence behind the Chemical Relationships numbers, not a result.")
    try:
        client = MarketDataEngineClient(MARKET_DATA_ENGINE_URL, timeout=6.0)
        snapshot = client.snapshot()
        # The same expression as the customer loader, deliberately: the internal lane is unlocked
        # from one place in this application, and a second literal here would make it two.
        reference = client.reference_series(internal=runtime_access is None)
    except MarketDataEngineUnavailable:
        st.warning("The Step 6 market-data engine is not reachable, so no price history can be read.")
        return
    try:
        graph = load_chemical_route_graph()
    except (OSError, ValueError) as exc:
        st.warning(f"The governed chemical routes could not be loaded: {exc}")
        return

    fx = _load_fx_table()
    targets = relationship_offerable(snapshot.observations, fx, graph, allowed=None)
    if not targets:
        st.info("No chemical route is available.")
        return
    labels = {relationship_display_name(graph, code): code for code in targets}
    chosen = st.selectbox("Raw material", _alphabetical(list(labels)),
                          key="relationship_method_target")
    reach = st.radio(
        "How far upstream to measure",
        options=(1, 2, 3, FULL_CHAIN),
        index=3,
        horizontal=True,
        key="relationship_method_reach",
        format_func=lambda n: ("the whole chain" if n == FULL_CHAIN
                               else f"{n} priced step" + ("" if n == 1 else "s")),
        help="A step counts only when it lands on a chemical somebody quotes. The customer page is "
             "fixed at the whole chain; this is here to check what a narrower reach would change.",
    )
    # The market belongs with the measurement. Whether propylene moves polyether polyol has a
    # different answer in each one - 0.53 over 150 months in Western Europe, 0.16 over 39 in China -
    # and a study that pools them answers neither question.
    unfiltered = build_relationship_view(snapshot.observations, fx, graph, target=labels[chosen],
                                         max_priced_steps=reach, reference=reference)
    markets = relationship_markets(unfiltered)
    market_names = relationship_market_labels(unfiltered)
    geography = None
    if markets:
        geography = st.selectbox("Market",
                                 _alphabetical(markets, lambda m: market_names.get(m, m)),
                                 key="relationship_method_market",
                                 format_func=lambda m: market_names.get(m, m))
    view = (unfiltered if geography is None else
            build_relationship_view(snapshot.observations, fx, graph, target=labels[chosen],
                                    max_priced_steps=reach, reference=reference,
                                    geography=geography))
    render_relationship_method_evidence(view, events=snapshot.events,
                                        observations=snapshot.observations, fx=fx)


def render_accuracy_in_force(client) -> None:
    """How accurate the methods actually serving forecasts are, by chemical and by horizon.

    Stefan, 23 October 2026: "that is also what the application should present." This page showed
    the baselines - carrying the last price, the average of the last three - and never the methods
    in force, so the one question it exists to answer was the one it did not.

    Two columns, not one. A method can sit within 8% of the price and call the direction right
    barely more than half the time, because most months move very little: the level is easy and the
    sign is close to a coin toss. Either number alone misleads, in opposite directions.
    """
    try:
        found = client.model_fit_accuracy()
    except MarketDataEngineUnavailable:
        return
    overall = (found or {}).get("overall") or {}
    if not overall:
        return

    st.markdown("### Accuracy of the methods in force")
    a, b, c = st.columns(3)
    a.metric("Price accuracy", f"{overall['accuracy']:.0f}%")
    b.metric("Direction right",
             "-" if overall.get("direction") is None else f"{overall['direction']:.0f}%")
    c.metric("Selections measured", overall.get("selections", 0))
    st.caption("Price accuracy is 100 less the method's average error as a share of that market's "
               "own price level, so markets in different currencies can be averaged. Measured on "
               "the methods in force, in the last full review.")

    horizons = (found or {}).get("by_horizon") or {}
    if horizons:
        st.dataframe(
            [{"Horizon": f"{weeks} weeks", "Price accuracy": f"{row['accuracy']:.0f}%",
              "Direction right": "-" if row.get("direction") is None else f"{row['direction']:.0f}%",
              "Selections": row.get("selections", 0)}
             for weeks, row in sorted(horizons.items(), key=lambda kv: int(kv[0]))],
            hide_index=True, width="stretch")

    chemicals = (found or {}).get("by_chemical") or {}
    if chemicals:
        # Worst first: the page is read to find what needs attention, not to be reassured.
        rows = sorted(chemicals.items(), key=lambda kv: kv[1].get("accuracy", 0))
        st.dataframe(
            [{"Chemical": _display_market_dimension(name),
              "Price accuracy": f"{row['accuracy']:.0f}%",
              "Direction right": "-" if row.get("direction") is None else f"{row['direction']:.0f}%",
              "Selections": row.get("selections", 0)}
             for name, row in rows],
            hide_index=True, width="stretch")


def render_forecast_performance(runtime_access: RuntimeAccess | None = None) -> None:
    st.markdown("## How accurate the methods have been")
    st.caption("What the methods in force are worth, then one market at a time: what this market "
               "was actually told and what arrived, and then the two controls it is measured "
               "against.")

    # First, and above the market selector, because it is about EVERY selection in force rather
    # than the one market chosen below (Stefan, 23 October 2026). The page opens on what the
    # forecasting is worth, and the per-market gate and baselines follow.
    render_accuracy_in_force(MarketDataEngineClient(MARKET_DATA_ENGINE_URL, timeout=1.5))

    # Every market with a governed series, not China TDI alone.
    #
    # This page was hardcoded to one identity key, so the only market whose forecast method could be
    # judged was the first one built. Judging the method is the entire purpose of the page, and the
    # markets that most need judging are the new ones.
    snapshot = _load_step6_snapshot()
    identities = [dict(row) for row in getattr(snapshot, "identities", [])] if snapshot else []
    priced = {str(ref.get("identity_key")) for ref in getattr(snapshot, "references", [])
              if ref.get("identity_key")}
    governed = [row for row in identities if str(row.get("identity_key")) in priced] or identities
    if runtime_access is not None:
        governed = [row for row in governed if identity_allowed(row, runtime_access.scope)]
    if not governed:
        st.info("No governed market is available within the active entitlement.")
        return

    def _market_label(row: dict) -> str:
        return " · ".join(part for part in (
            display_chemical(row.get("product_family") or ""),
            str(row.get("market_geography") or ""),
            _display_market_dimension(row.get("commercial_basis") or ""),
        ) if part)

    governed.sort(key=_market_label)
    governed = _alphabetical(governed, _market_label)
    default = next((i for i, row in enumerate(governed)
                    if str(row.get("identity_key")) == TDI_CHINA_DOMESTIC_IDENTITY_KEY), 0)
    chosen = st.selectbox("Market", governed, index=default, format_func=_market_label,
                          key="forecast_performance_market")
    identity_key = str(chosen.get("identity_key"))

    client = MarketDataEngineClient(MARKET_DATA_ENGINE_URL, timeout=1.5)
    try:
        readiness = client.readiness(identity_key)
        backtest = client.backtest(identity_key)
    except MarketDataEngineUnavailable:
        st.warning("The Step 6 market-data engine is not reachable. Start the application with "
                   "Run-MVP.bat.")
        return

    # The record comes first, and it comes out whatever the controls say.
    #
    # It was written after the baseline table, behind the early return below, so on 24 September
    # 2026 TDI China passed the release gate, the baseline returned no models, and the screen
    # stopped before ever reaching it. The record is the evidence this page exists for; a backtest
    # that has nothing to say is no reason to hide it.
    render_forecast_record(identity_key, _market_label(chosen), client)

    st.markdown("### Controls")

    # The gate sits here, beside the only section it governs, and its label says what it governs.
    #
    # It was the first and largest thing on the page: four metrics headed "Data sufficiency gate -
    # BLOCKED" above the forecast record, which it does not gate. `assess_readiness` is consulted
    # in exactly two places, the controls backtest below and the `/readiness` route that draws
    # these four numbers; nothing in `production.py`, `selection.py` or `review_job.py` asks it. 80
    # markets carry a forecast in force and 35 of them read BLOCKED, so the page opened by telling
    # the reader that most of the product was not ready - about a section further down that the
    # reader had not reached yet (Stefan, 31 October 2026, on Adipic Acid China EXW: "If no
    # forecast method is being chosen, then explain the bottom banner text in blue").
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Scored", "Yes" if readiness.get("ready") else "No")
    c2.metric("Governed observations", f"{int(readiness.get('points') or 0):,}")
    c3.metric("Independent publishers", int(readiness.get("independent_sources") or 0))
    c4.metric("History span", f"{int(readiness.get('span_days') or 0):,} days")

    models = backtest.get("models") or []
    if not readiness.get("ready"):
        # Why, in a sentence a buyer can weigh, rather than the threshold that failed.
        #
        # 28 of the 35 blocked markets fail on one publisher and 10 on span; the screen printed
        # `independent_sources 1 < 2` at a reader who would have to know the rule to get anything
        # from it. The wording lives in `forecast_controls` so it can be read by a test without
        # Streamlit, and it is built from the engine's own reason codes - a second opinion computed
        # here could disagree with the four metrics beside it.
        st.info(forecast_controls.blocked_banner(_market_label(chosen), readiness))
        st.caption(forecast_controls.caption())
        return
    if not models:
        st.info("This market has the history the controls need, but no horizon could be scored on "
                "it: too few of its readings have another reading 1, 2 or 3 months later. The "
                "forecast record above is unaffected.")
        st.caption(forecast_controls.caption())
        return

    st.success(forecast_controls.ready_banner(_market_label(chosen), readiness))

    # One row per control per horizon, on the months scale the record above prints.
    #
    # There is no single "closest method" to headline any more, and there never honestly was: the
    # two controls were scored one row ahead - one day on a daily series - and printed directly
    # beneath a record of 1, 2 and 3-month calls, with nothing saying the numbers were not
    # comparable. Carrying the last price forward is near unbeatable over a day and loses badly
    # over a quarter, so at one day ahead the yardstick flattered every method it was there to
    # test.
    basis = f"{chosen.get('currency') or ''}/{chosen.get('unit') or 'MT'}".strip("/")
    frame = pd.DataFrame(forecast_controls.control_rows(models),
                         columns=list(forecast_controls.COLUMNS))
    if basis:
        frame = frame.rename(columns={"Average miss": f"Average miss ({basis})"})
    st.dataframe(frame, hide_index=True, width="stretch")
    st.caption(forecast_controls.caption(basis))
    # The exception after the rule, not before it: the caption says the controls were run at 1, 2
    # and 3 months, and this says which of those this market's own readings could not answer.
    note = forecast_controls.horizons_note(models, backtest.get("horizon_weeks") or ())
    if note:
        st.caption(note)


def render_forecast_record(identity_key: str, market: str, client) -> None:
    """Every forecast this market was given, and what happened to the ones that have come due.

    Stefan, 24 September 2026: "how are we going to measure how good the forecasting method is/was".
    The engine has been writing each claim down since March, and until today none of them had been
    scored, because the settling step asked the payload which frequency it had chosen and this
    market chooses none - it falls back to monthly and forecasts anyway. So the record existed and
    was empty of outcomes.

    The controls below and this table answer two different questions. The controls refit today's
    two reference methods on today's history and can therefore improve retrospectively. This is what
    was actually said, on the day it was said, scored against the price that arrived. Only the
    second kind of evidence survives a customer asking how the method has done - which is why this
    table is now the first thing on the page and the controls follow it, rather than the other way
    round.
    """
    st.markdown("### The forecast record")
    try:
        record = client.forecast_vintages(identity_key, limit=500)
    except MarketDataEngineUnavailable:
        st.warning("The Step 6 market-data engine is not reachable.")
        return
    if not record:
        st.info("No forecast has been recorded for this market yet. One is written every time the "
                "market's forecast is served.")
        return

    counts = forecast_record.record_counts(record)
    c1, c2, c3 = st.columns(3)
    c1.metric("Forecasts recorded", f"{counts['recorded']:,}")
    c2.metric("Scored so far", f"{counts['settled']:,}")
    # The same mark as the table below, not a hyphen: no claim here carried a band yet.
    c3.metric("Inside the band",
              f"{counts['inside']} of {counts['banded']}" if counts["banded"]
              else forecast_record.UNKNOWN)

    # Two frames, because the screen and the file want opposite things from an unsettled claim.
    #
    # A claim that has not come due carries None in its outcome columns, which is correct: nought is
    # a measurement and "not yet" is not. This table used to coerce those five columns to numeric,
    # on the belief that the cell would then be blank. Measured on 31 October 2026 in an isolated
    # page, Streamlit 1.63.0 with pandas 3.0.5: it is not - a `float64` holding `NaN` renders as the
    # word **None**, as does a nullable `Int64` holding `pd.NA`, with or without a `column_config`.
    # So most of this table read "None" five times per row, which looks like a fault in the page.
    #
    # `display_frame` is all text with the dash. `csv_frame` keeps the numbers and leaves the cell
    # empty, which is what a spreadsheet wants. Why both, and what the screen gives up by being
    # text, is written out in `forecast_record.display_rows`.
    st.dataframe(forecast_record.display_frame(record), hide_index=True, width="stretch")
    st.download_button(
        "Download the forecast record (CSV)",
        data=forecast_record.csv_frame(record).to_csv(index=False).encode("utf-8"),
        file_name=forecast_record.file_name(market),
        mime="text/csv",
        key="forecast_record_csv",
    )
    if not counts["settled"]:
        st.caption("A forecast is scored when the month it named has closed and a price for that "
                   "month exists. Nothing here has come due yet.")


_CADENCE_BANDS = ((3.5, "Daily"), (10.0, "Weekly"), (45.0, "Monthly"), (120.0, "Quarterly"))


def _cadence_of(days: list[str]) -> tuple[str, float]:
    """How often this series publishes, read from the series itself.

    Stefan, 1 October 2026: "Not assume all is daily to make something declare it is missing, that is
    a basic mistake you make all the time." A monthly assessment cannot miss a Tuesday. So the gap a
    series normally leaves is measured from its own readings, and what it has missed is counted
    against that and nothing else.
    """
    if len(days) < 3:
        return "Too few to tell", 0.0
    ordered = sorted(days)
    gaps = sorted((date.fromisoformat(b) - date.fromisoformat(a)).days
                  for a, b in zip(ordered, ordered[1:]))
    typical = float(gaps[len(gaps) // 2])
    for limit, name in _CADENCE_BANDS:
        if typical <= limit:
            return name, typical
    return "Occasional", typical


def _missed_since(days: list[str], typical: float, *, today: date, window: int = 90) -> int:
    """Readings this series should have had in the last 90 days, and did not.

    Counted at the series' own pace, from its own first reading inside the window, and never for a
    series whose pace we cannot read. Today is not counted: the day is not over.
    """
    if typical <= 0:
        return 0
    inside = sorted(d for d in days if (today - date.fromisoformat(d)).days <= window)
    if len(inside) < 3:
        return 0
    first, last = date.fromisoformat(inside[0]), date.fromisoformat(inside[-1])
    expected = int((today - first).days / typical)
    return max(0, expected - (len(inside) - 1))


def _is_fetched(source: dict) -> bool:
    """Whether we go and get this one. Everything else is held for the record.

    One judge, and it lives in `source_status.we_fetch` (masterplan G). It was here, in `app.py`,
    which meant the only code that could ask "did we collect this reading?" was the application -
    so `market_lines` answered it with a date instead, and called 34,476 of 35,462 readings
    somebody else's history. Moving the predicate into `src/` is what let the provenance rule be
    about the source at all.
    """
    return source_status.we_fetch(source)


def _feed_rows(snapshot, publisher_of, today: date, fetched: set[str]) -> list[dict]:
    """One row per feed: one market read by one publisher (`populations.FEEDS`).

    `_market` carries the four-field market the feed reads, so the panel can say how many markets
    its feeds cover without collapsing the display columns back into a key.
    """
    wanted = {str(row.get("identity_key")): row for row in snapshot.identities
              if str(row.get("identity_key"))}
    feeds: dict[tuple, list[str]] = {}
    arrivals: dict[tuple, list[str]] = {}
    members: dict[tuple, str] = {}
    for obs in snapshot.observations:
        identity = wanted.get(str(obs.get("identity_key") or ""))
        if identity is None or obs.get("price") is None:
            continue
        day = str(obs.get("observation_date") or "")[:10]
        source_id = str(obs.get("source_id") or "")
        if source_id not in fetched:
            continue
        # The same rules every other page runs on: a reading we set aside is not a reading, and the
        # terms are the ones that were ruled rather than the ones the publisher printed.
        if not day or not is_drawn(identity, source_id, day):
            continue
        # One row per feed: UN Comtrade's year-by-year sources count as one monthly and one annual
        # feed, and a predecessor customs code counts with the code it continues.
        key = market_key(identity, source_id) + (trade_feeds.feed_of(source_id),)
        feeds.setdefault(key, []).append(day)
        # Pace and misses are read on the day a reading could first be seen. For Eurostat that is
        # its release, about ten weeks after the month, so a July figure in October is on time.
        arrivals.setdefault(key, []).append(trade_feeds.arrival_day(obs) or day)
        members[key] = max(members.get(key, ""), source_id)
    rows = []
    for (chemical, geography, basis, packaging, _feed), days in feeds.items():
        source_id = members[(chemical, geography, basis, packaging, _feed)]
        seen = arrivals[(chemical, geography, basis, packaging, _feed)]
        cadence, typical = _cadence_of(sorted(set(seen)))
        rows.append({
            "Chemical": display_chemical(chemical),
            "Type": chemical_kind(chemical),
            "Geography": geography or "Not specified",
            BASIS_LABEL: _display_basis(basis),
            "Packaging": _display_market_dimension(packaging) if packaging else "Any",
            "Publisher": publisher_of.get(source_id, source_id),
            "Publishes": cadence,
            "Readings": len(days),
            "First": min(days),
            "Last collection": max(days),
            "Missed (90 days)": _missed_since(sorted(set(seen)), typical, today=today),
            "_chemical": chemical, "_source": source_id, "_pace": typical,
            "_market": (chemical, geography, basis, packaging),
        })
    return sorted(rows, key=lambda r: (r["Type"], r["Chemical"], r["Geography"], r["Publisher"]))


def _agreement_gaps(snapshot, publisher_of) -> dict[tuple[str, str, str, str], float]:
    """Where two publishers price the same market on the same terms in the same month, how far apart.

    Keyed by (publisher, chemical, geography, month) and given as a percentage of the midpoint. A
    market only one publisher prices has no entry, and the scorecard scores no agreement for it
    rather than inventing one.

    Two things here are the point rather than detail, both of them Stefan's rule that a publisher is
    measured on its own period and never on the calendar day.

    A publisher's value for the month is the mean of what it printed that month. Taking its last
    print instead compared a daily publisher's single Tuesday against a monthly assessor's whole
    month, which is the same mistake as asking the monthly assessor why it did not print on Tuesday.

    And the terms are part of the market. SunSirs prints EXW China; IMARC prints a regional
    assessment with no Incoterm on it. Those are different numbers about the same chemical, so
    putting them in one bucket manufactures a disagreement that neither publisher is responsible for.
    """
    wanted = {str(row.get("identity_key")): row for row in snapshot.identities
              if str(row.get("identity_key"))}
    priced: dict[tuple[str, str, str, str, str], dict[str, list[float]]] = {}
    for obs in snapshot.observations:
        identity = wanted.get(str(obs.get("identity_key") or ""))
        if identity is None or obs.get("price") is None:
            continue
        day = str(obs.get("observation_date") or "")[:10]
        source_id = str(obs.get("source_id") or "")
        if not day or not is_drawn(identity, source_id, day):
            continue
        key = market_key(identity, source_id) + (day[:7],)
        publisher = publisher_of.get(source_id, source_id)
        priced.setdefault(key, {}).setdefault(publisher, []).append(float(obs["price"]))
    out: dict[tuple[str, str, str, str], float] = {}
    for (chemical, geography, _basis, _packaging, month), by_publisher in priced.items():
        if len(by_publisher) < 2:
            continue
        monthly = {publisher: sum(values) / len(values)
                   for publisher, values in by_publisher.items()}
        ordered = sorted(monthly.values())
        half = len(ordered) // 2
        # A true median. Taking ordered[half] made the higher of two prints the reference, so the
        # lower publisher carried the whole gap and the higher one scored a perfect nothing. With
        # two publishers on a market - which is nearly every overlap we have - that scored a
        # publisher for its position in the pair rather than for its number.
        middle = (ordered[half] if len(ordered) % 2
                  else (ordered[half - 1] + ordered[half]) / 2.0)
        if middle <= 0:
            continue
        for publisher, value in monthly.items():
            gap = abs(value - middle) / middle * 100.0
            previous = out.get((publisher, chemical, geography, month))
            out[(publisher, chemical, geography, month)] = (gap if previous is None
                                                            else max(previous, gap))
    return out


def _publisher_rows(rows: list[dict], snapshot, publisher_of, today: date) -> list[dict]:
    """The scorecard. Every column is a ratio against what that publisher itself commits to.

    Stefan, 2 October 2026: a supplier scorecard compares suppliers who sell different materials in
    different volumes at different frequencies, because each KPI is measured against that supplier's
    own commitment. The same applies here, so the hand-set reliability level is gone and nothing on
    this table is a judgement.
    """
    by_publisher: dict[str, list[dict]] = {}
    for row in rows:
        by_publisher.setdefault(row["Publisher"], []).append(row)
    state_of = {str(s.get("source_id")): s for s in snapshot.sources}
    frozen_sources = {str(row.get("source_id")) for row in getattr(snapshot, "quality", [])
                      if str(row.get("issue_type") or "") == "STALE_VALUE"}
    gaps = _agreement_gaps(snapshot, publisher_of)

    cards = []
    extra: dict[str, dict] = {}
    for publisher, feeds in by_publisher.items():
        series = []
        for row in feeds:
            pace = float(row.get("_pace") or 0.0)
            held = int(row["Readings"])
            missed = int(row["Missed (90 days)"])
            mine = [value for (who, chemical, geography, _month), value in gaps.items()
                    if who == publisher and chemical == row["Chemical"]
                    and geography == row["Geography"]]
            series.append({
                "held": held, "owed": held + missed, "pace_days": pace,
                "last": row["Last collection"], "first": row["First"],
                "frozen": row["_source"] in frozen_sources, "readings": held,
                "gap_pct": (sorted(mine)[len(mine) // 2] if mine else None),
            })
        sources = {row["_source"] for row in feeds}
        attempts = sum(int(state_of.get(s, {}).get("attempts_90d") or 0) for s in sources)
        reached = sum(int(state_of.get(s, {}).get("reached_90d") or 0) for s in sources)
        cards.append(publisher_scorecard.score_publisher(
            publisher, series, today=today, attempts=attempts, reached=reached))
        checks = [state_of.get(s, {}).get("last_attempt_at") for s in sources]
        extra[publisher] = {
            "Chemicals": len({row["Chemical"] for row in feeds}),
            "Last reading": max(row["Last collection"] for row in feeds),
            "Last checked": max([str(c)[:10] for c in checks if c], default="-"),
        }

    def _show(value):
        # Text on purpose: the column also carries "-" and "Too new to score", and a column that
        # mixes numbers with words made Streamlit warn and convert it on every render.
        return "-" if value is None else str(round(value))

    out = []
    for card in publisher_scorecard.rank(cards):
        row = {
            "Publisher": card.publisher,
            "Score": card.note or _show(card.score),
            "Feeds": card.feeds,
            "Readings": card.readings,
            "Chemicals": extra[card.publisher]["Chemicals"],
        }
        for name in publisher_scorecard.WEIGHTS:
            row[name] = _show(card.scores.get(name))
        row["Last reading"] = extra[card.publisher]["Last reading"]
        row["Last checked"] = extra[card.publisher]["Last checked"]
        out.append(row)
    return out


def _render_trade_refresh_log(snapshot, publisher_of, into) -> None:
    """What each Eurostat and UN Comtrade check found, and every figure they revised.

    Stefan's instruction of 13 October 2026, sections 10 and 11: every refresh records its result,
    and a revised figure is kept with the value it replaced. The engine writes both; this shows them.

    Drawn into `into` since 5 November 2026 rather than a section of its own: it is one of four
    tables about the same 102 sources, and four titles on the page said that four times.
    """
    quality = list(getattr(snapshot, "quality", []) or [])
    latest: dict[str, dict] = {}
    for row in quality:
        if str(row.get("issue_type") or "") != "TRADE_REFRESH":
            continue
        source_id = str(row.get("source_id") or "")
        if str(row.get("created_at") or "") >= str((latest.get(source_id) or {}).get("created_at") or ""):
            latest[source_id] = row
    revisions = [row for row in quality if str(row.get("issue_type") or "") == "REVISION"]
    if not latest and not revisions:
        return
    into.markdown(f"**Customs trade data** - last check per source ({len(latest)}), "
                  f"revisions ({len(revisions)})")
    if latest:
        into.dataframe(pd.DataFrame([{
            "Publisher": publisher_of.get(source_id, source_id),
            "Source": source_id,
            "Checked": str((row.get("details") or {}).get("checked_at") or row.get("created_at") or "")[:16].replace("T", " "),
            "Result": ", ".join((row.get("details") or {}).get("statuses") or []),
            "New": int((row.get("details") or {}).get("new_observations") or 0),
            "Revised": int((row.get("details") or {}).get("revised_observations") or 0),
            "Latest period": str((row.get("details") or {}).get("latest_period") or "-"),
        } for source_id, row in sorted(latest.items())]), hide_index=True, width="stretch",
            height=min(38 * (len(latest) + 1), 300))
    if revisions:
        into.caption("Revised figures: the market carries the revised value; the value it replaced "
                     "is kept here.")
        into.dataframe(pd.DataFrame([{
            "Source": str(row.get("source_id") or ""),
            "Series": str((row.get("details") or {}).get("series") or ""),
            "Origin": str((row.get("details") or {}).get("origin") or ""),
            "Period": str((row.get("details") or {}).get("observation_month") or ""),
            "Was": (row.get("details") or {}).get("previous_value"),
            "Now": (row.get("details") or {}).get("revised_value"),
            "Detected": str((row.get("details") or {}).get("revision_detected_at") or row.get("created_at") or "")[:10],
        } for row in sorted(revisions, key=lambda r: str(r.get("created_at") or ""), reverse=True)[:200]]),
            hide_index=True, width="stretch", height=min(38 * (len(revisions) + 1), 300))


def render_market_data_collection(runtime_access: RuntimeAccess | None = None) -> None:
    """What we collect, how reliably, and what we keep for the record.

    Rebuilt on 1 October 2026. It had carried four counters that counted different populations, a
    price matrix, a duplicate of the Current Market reference, a ledger of every observation and a
    CSV template. Stefan: "the whole page is crap... I think we need to split the whole page;
    A) Daily/weekly/Monthly collected series B) Imported & research series."

    It also read the store raw, so it showed terms we had aliased and series we had cut, and
    disagreed with every other page in the application. It runs on the same curation rules now.

    Four of its sections became one on 5 November 2026, and the tables are unchanged inside it.
    """
    snapshot = _load_step6_snapshot()
    if snapshot is None:
        st.info("Market data engine is not available.")
        return
    today = datetime.now(timezone.utc).date()
    publisher_of = {str(src.get("source_id")): str(src.get("publisher") or src.get("source_id"))
                    for src in snapshot.sources}

    fetched = {str(s.get("source_id")) for s in snapshot.sources if _is_fetched(s)}
    rows = _feed_rows(snapshot, publisher_of, today, fetched)
    if not rows:
        st.info("No feeds are collected yet.")
        return

    # A feed whose dates advance while its value never moves reads as a live market on every screen
    # and is not one. That is how a trader's standing offer sat 70% above the market for months. The
    # readings are left exactly as collected; what changes is that it is said out loud. Kept through
    # the rebuild of 1 October 2026 - it is the one thing on the old page that earned its place.
    frozen = [row for row in getattr(snapshot, "quality", [])
              if str(row.get("issue_type") or "") == "STALE_VALUE"]
    if frozen:
        # The one warning on the page: a title that says how many, so it is seen without opening.
        moving = st.expander(f"Feeds not moving - {len(frozen)}: a published price that has "
                             "stopped changing is not being assessed", icon=":material/warning:",
                             key="sources_frozen")
        moving.dataframe(pd.DataFrame([{
            "Publisher": publisher_of.get(str(row.get("source_id")), str(row.get("source_id"))),
            "What was seen": str((row.get("details") or {}).get("summary") or ""),
        } for row in frozen]), hide_index=True, width="stretch", height=38 * (len(frozen) + 1))

    kept = [s for s in snapshot.sources if not _is_fetched(s)]
    # Lateness belongs to the quiet-collection section, judged on each feed's own rhythm; a flat
    # 45-day count in this title would call every on-time customs month late.
    #
    # One section for the sources, not four. It was Collected feeds, Publisher scorecard, Customs
    # trade data and Imported sources: four titles on the page, four counts of the same 102
    # sources, and nothing on the page said they were the same 102. Stefan, 5 November 2026: "Is
    # all of this table necessary??" The tables are all still here, one click down, in the order a
    # reader asks for them: what arrives, what we hold without fetching, who is any good, and what
    # the customs checks found.
    collected = st.expander(
        f"Sources and feeds - {populations.SOURCES.count(len(snapshot.sources))}, "
        f"{populations.FEEDS.count(len(rows))} covering "
        f"{populations.MARKETS.count(len({r['_market'] for r in rows}))}, "
        f"{populations.READINGS.count(sum(r['Readings'] for r in rows))}",
        key="sources_collected")
    collected.markdown(f"**Collected feeds** - {populations.FEEDS.count(len(rows))}")
    collected.caption(f"A feed is {populations.FEEDS.fields}; a market is "
                      f"{populations.MARKETS.fields}. Only the publishers we fetch are counted "
                      f"here - the {len(kept)} held for the record are the next table down, so "
                      "these readings are not all the readings the store holds. Each feed is "
                      "judged by the pace it publishes at, read from its own readings - a monthly "
                      "assessment cannot miss a Tuesday.")
    c1, c2, c3, c4 = collected.columns(4)
    def _pick(column, label, field, key):
        values = sorted({row[field] for row in rows})
        with column:
            return st.selectbox(label, ["All"] + _alphabetical(values), key=key)
    chemical = _pick(c1, "Chemical", "Chemical", "collection_chemical")
    geography = _pick(c2, "Geography", "Geography", "collection_geography")
    terms = _pick(c3, BASIS_LABEL, BASIS_LABEL, "collection_terms")
    packaging = _pick(c4, "Packaging", "Packaging", "collection_packaging")
    shown = [row for row in rows
             if (chemical == "All" or row["Chemical"] == chemical)
             and (geography == "All" or row["Geography"] == geography)
             and (terms == "All" or row[BASIS_LABEL] == terms)
             and (packaging == "All" or row["Packaging"] == packaging)]
    if not shown:
        collected.caption("Nothing is collected on that combination.")
    else:
        frame = pd.DataFrame([{k: v for k, v in row.items() if not k.startswith("_")}
                              for row in shown])
        # A fixed height that scrolls, rather than every row unfolded down the page.
        collected.dataframe(frame, hide_index=True, width="stretch",
                            height=min(38 * (len(frame) + 1), 420))
        # No flat 45-day count here. The note on this section's title says a flat count would
        # call every on-time customs month late, and then this line did it anyway: "357 have not
        # been read in 45 days", 357 of 381, on 5 November 2026. That is the same defect Stefan
        # was looking at when he asked whether all of this table was necessary - a number that
        # flags almost everything tells a reader nothing. Lateness is judged one section up, on
        # each feed's own rhythm, and nothing else on the page should pretend to judge it.
        collected.caption(f"{populations.FEEDS.count(len(shown))}, "
                          f"{populations.READINGS.count(sum(r['Readings'] for r in shown))}. "
                          "Whether any of them is late is judged on its own rhythm, in Collection "
                          "gone quiet.")

    collected.markdown("**Held for the record** - "
                       f"{populations.SOURCES.count(len(kept))} we do not fetch")
    collected.caption("Subscription publishers we are not entitled to collect, publishers that "
                      "closed their page, our own research imports and the licensed history. None "
                      "of these is checked daily, so none of them can be late.")
    if kept:
        collected.dataframe(pd.DataFrame([{
            "Publisher": str(s.get("publisher") or "-"),
            "Source": str(s.get("source_name") or ""),
            "Why it is here": ("Not fetched" if str(s.get("collector_status") or "") == "MONITOR_ONLY"
                               else "Imported once"),
            "Readings": int(s.get("observation_count") or 0),
            "Latest reading": str(s.get("latest_observation_date") or "")[:10] or "-",
        } for s in sorted(kept, key=lambda x: str(x.get("publisher") or ""))]),
            hide_index=True, width="stretch", height=min(38 * (len(kept) + 1), 300))

    publishers = pd.DataFrame(_publisher_rows(rows, snapshot, publisher_of, today))
    collected.markdown(f"**Publisher scorecard** - {len(publishers)} publishers")
    collected.caption("Every column is a ratio against what that publisher itself publishes, so a "
                      "monthly assessor and a daily collector are compared on the same terms. 100 "
                      "is best. Weighted "
                      + ", ".join(f"{name} {weight}"
                                  for name, weight in publisher_scorecard.WEIGHTS.items())
                      + ". Agreement is scored only where another publisher prices the same "
                      "market; where nothing else does, its weight goes to the rest.")
    collected.dataframe(publishers, hide_index=True, width="stretch",
                        height=min(38 * (len(publishers) + 1), 420))

    _render_trade_refresh_log(snapshot, publisher_of, collected)


def render_customer_catalogue() -> list[dict] | None:
    """Which markets the customer pages may offer. Stefan, 13 October 2026.

    The first table on Data & Sources and the control for every customer selector: the selectors
    read `customer_catalogue.offered`, the same sets this table marks Yes, so a choice that is not
    Yes here cannot be made on a customer page.

    Returns the catalogue rows so the quiet panel below can check itself against them - its
    population is these markets collapsed to chemical and geography (`populations.widths_agree`).
    """
    snapshot = _load_step6_snapshot()
    if snapshot is None:
        st.info("Market data is being collected.")
        return None
    publishers = {str(s.get("source_id")): str(s.get("publisher") or s.get("source_name") or s.get("source_id"))
                  for s in snapshot.sources}
    rows = customer_catalogue.catalogue(
        [dict(row) for row in snapshot.identities], snapshot.observations,
        sources=snapshot.sources,
        is_published=is_published_market, why_not_published=why_not_published,
        has_intelligence=market_intelligence.has_intelligence,
        chemicals=[*ACTIVE_CHEMICALS, *FEEDSTOCK_CHEMICALS], geographies=list(ORIGINS),
        publisher_of=lambda source_id: publishers.get(source_id, source_id),
        forecast_from_year=FORECAST_FROM_YEAR)
    if not rows:
        st.info("The store holds no markets yet.")
        return None
    shown = sum(1 for row in rows if (row[customer_catalogue.SHOW] if row.get("by_hand") is None
                                      else row["by_hand"]))
    by_hand = sum(1 for row in rows if row.get("by_hand") is not None)
    pieces = customer_catalogue.pieces_for_display(
        rows, chemical_name=display_chemical, geography_name=_display_market_dimension)
    with st.expander(f"Customer pages - {populations.MARKETS.of(shown, len(rows))} shown, "
                     f"{by_hand} set by hand", key="sources_catalogue"):
        _render_catalogue_table(rows, shown, by_hand)
        # Markets glued together by a ruling are judged as one; each piece is listed with what it
        # would earn alone, so a piece that only qualifies inside the glued market stays in view.
        #
        # Inside this section since 5 November 2026. It had one of its own, which put a 122-row
        # reference list on the page at the same rank as the control it is a footnote to. The
        # reason it was ever given one - Streamlit does not fold a section inside another - is
        # answered by making it a second table here rather than a second section.
        if pieces:
            st.markdown(f"**Glued markets, piece by piece** - {len(pieces)} pieces")
            st.caption("Each row is one publisher's feed inside a glued market. The market is "
                       "judged as a whole; 'alone' says what the piece would earn by itself.")
            st.dataframe(pd.DataFrame(pieces), hide_index=True, width="stretch",
                         height=min(38 * (len(pieces) + 1), 420))
    return rows


def _render_catalogue_table(rows, shown, by_hand) -> None:
    """The eligibility table with Stefan's one editable column, inside its section."""
    # The full rules, once, in short. The counts are already in the section title.
    st.caption(
        f"A market is {populations.MARKETS.fields}. Current = priced by anybody in the last "
        f"{CURRENT_WITHIN_DAYS} days; Historic = everything else we hold; Forecast = newest "
        f"reading in "
        f"{FORECAST_FROM_YEAR} or later and no more than {FORECAST_WITHIN_DAYS} days old, so a "
        "horizon still lands ahead. Intelligence = this chemical and geography have a Market "
        "Intelligence page behind them - a producer register, developments, or both; it is a fact "
        "about the chemical and geography, so every market in the same two says the same. Markets "
        "glued by a ruling are judged as one. Change any of the first three columns to decide "
        "yourself; your choice wins until you set it back.")
    only = st.radio("Show", ["All", "Yes only", "No only", "Needs review"], horizontal=True,
                    key="catalogue_filter", label_visibility="collapsed")
    shown_value = (lambda row: row[customer_catalogue.SHOW] if row.get("by_hand") is None
                   else row["by_hand"])
    if only == "Yes only":
        rows = [row for row in rows if shown_value(row)]
    elif only == "No only":
        rows = [row for row in rows if not shown_value(row)]
    elif only == "Needs review":
        # The rows where a hand-set answer and the evaluation have drifted apart, so the list can be
        # worked through rather than hunted for.
        rows = [row for row in rows if any((row.get("needs_review") or {}).values())]
    # Editable in all three page columns (Stefan, 16 October 2026: "All 3 columns to be editable").
    # One column used to decide every page together, which could not express the case that prompted
    # it: a market that belongs on Historic Pricing and not on Forecast. A choice that differs from
    # the evaluation is kept in customer_controls and wins on that page; setting it back to the
    # evaluation removes it.
    frame = pd.DataFrame(customer_catalogue.rows_for_display(
        rows, chemical_name=display_chemical, geography_name=_display_market_dimension,
        terms_name=_basis_namer, packaging_name=display_packaging))
    keys = [customer_catalogue.series_key(row) for row in rows]
    columns = {customer_catalogue.SHORT_PAGE[page]: page
               for page in customer_catalogue.PRICE_PAGES}
    edits = st.session_state.get("catalogue_editor_round", 0)
    edited = st.data_editor(
        frame, hide_index=True, width="stretch", key=f"catalogue_editor_{edits}",
        disabled=[column for column in frame.columns if column not in columns],
        column_config={column: st.column_config.SelectboxColumn(
            column, options=customer_catalogue.CELL_OPTIONS, required=True,
            help=f"Whether this market is offered on {page}. Your choice wins over the evaluation; "
                 "set it back to the evaluation to let the evaluation decide again. "
                 f"{customer_catalogue.REVIEW_MARK} means the evaluation would no longer give the "
                 "answer you set - the market has moved since you chose.")
            for column, page in columns.items()})
    refused: list[str] = []
    changed = False
    for i in range(len(frame)):
        row = rows[i]
        for column, page in columns.items():
            # On the answer underneath, not the text: picking the unmarked option on a marked cell
            # is not a change of mind, and the mark is computed rather than set by hand.
            wanted = customer_catalogue.answer_of(edited.iloc[i][column])
            if wanted == customer_catalogue.answer_of(frame.iloc[i][column]):
                continue
            # A Yes on Forecast cannot outrank the arithmetic: a reading older than the longest
            # horizon leaves no month ahead to forecast. Refused rather than stored, so the table
            # never shows a choice that does nothing.
            if wanted and page == customer_catalogue.FORECAST and row.get("forecast_too_old"):
                refused.append(f"{display_chemical(row['chemical'])} - "
                               f"{_display_market_dimension(row['geography'])}")
                continue
            # The evaluation's answer travels with the choice, so a later run can tell a choice
            # the market has moved under from one that simply differs.
            customer_controls.set_choice(
                keys[i], None if wanted == row["evaluated_pages"][page] else wanted, page=page,
                evaluated=row["evaluated_pages"][page])
            changed = True
    if refused:
        st.warning(f"{customer_catalogue.TOO_OLD_TO_FORECAST}: "
                   + ", ".join(sorted(set(refused))) + ".")
    if changed or refused:
        st.session_state["catalogue_editor_round"] = edits + 1
        st.rerun()


def render_collection_watch(catalogue_rows=None) -> None:
    """Which chemicals in a geography have gone quiet, against their own rhythm.

    Stefan, 11 October 2026, asked why four of five chemicals in a quarterly report began in
    September. Nothing earlier existed, and nothing in the application was watching for collection
    that stops. A monthly publisher is not late on day eight, so the rhythm is read off each one's
    own readings rather than assumed.

    `catalogue_rows` are the catalogue's markets, so this panel can check that its own population
    is those markets collapsed to chemical and geography (masterplan B).
    """
    snapshot = _load_step6_snapshot()
    if snapshot is None:
        st.info("Market data is being collected.")
        return
    held = collection_watch.watch(snapshot.observations)
    if not held:
        st.info("Nothing is collected yet.")
        return
    # Masterplan B asks the page to carry the check itself rather than the plan asserting it once:
    # this panel's population must be the catalogue's markets collapsed to two fields. Silent while
    # they agree, which is the only state worth no words.
    if catalogue_rows:
        disagreement = populations.widths_agree(catalogue_rows, held)
        if disagreement:
            st.warning(disagreement)
    # Only a market somebody is still publishing can go quiet. A record that stopped in 2017 is
    # history, and the application has said so since v1.2.61; listing it here as collection gone
    # quiet reported that same fact a second time, as a fault. Measured 5 November 2026: 83 rows of
    # 161, 69 of them markets with no price inside six months. Stefan: "Is all of this table
    # necessary??"
    #
    # The line is a year rather than the six months that decide Current Market, on purpose. A
    # market that has just fallen OFF Current Market is the one you most want to be told about,
    # and 25 of the rows are exactly that: ten South Korean markets that stopped on the same day,
    # nine Saudi ones on another.
    #
    # This comment said "a dead connector, not a quiet market" until 5 November 2026, when Stefan
    # said "find out why" and it turned out to be wrong. Nothing is dead. They are UN Comtrade
    # customs figures; the connector ran weekly and succeeded every time, with South Korea and
    # Türkiye named in every request. Comtrade's own API, asked directly, has nothing for South
    # Korea, Türkiye or China for any month of 2026 and stops at April for Saudi Arabia. The
    # countries have not filed. Three batches stopping on three days is what a filing calendar
    # looks like, not what a broken fetch looks like - and the page now says which it is, in the
    # Collection column, rather than leaving a reader to guess as this comment did.
    identities = [dict(row) for row in snapshot.identities]
    live = {(chemical, geography) for chemical, geography, _, _ in market_lines_module.priced_recently(
        identities, snapshot.observations,
        within_days=market_lines_module.DRAWN_ON_CURRENT_DAYS)}
    on_current = {(chemical, geography) for chemical, geography, _, _ in market_lines_module.priced_recently(
        identities, snapshot.observations,
        within_days=market_lines_module.CURRENT_WITHIN_DAYS)}
    watched = [one for one in held if (one.chemical, one.geography) in live]
    waiting = [one for one in watched if one.state != collection_watch.CURRENT]

    # The longest hole in each late series, as a column rather than the section it used to have.
    # That section listed 106 rows of which 80 were holes that closed over a year ago - the join
    # between a licensed history block and today's collection, not work anybody can do. A hole in
    # a series that is still arriving on time is not news either; a hole in one that has gone
    # quiet is part of the same story, so it is told beside it.
    longest: dict[tuple[str, str], str] = {}
    for one in waiting:
        for before, after, span in collection_watch.gaps_in(
                snapshot.observations, chemical=one.chemical, geography=one.geography)[:1]:
            longest[(one.chemical, one.geography)] = f"{span} days, to {after.isoformat()}"

    # Whether OUR collection is the reason. Decided in `source_status`, off the feeds that
    # actually produced each market's readings, so the page states a fact rather than implying a
    # fault it has not checked. Before 5 November 2026 every row read "behind their own rhythm"
    # and a reader had no way to tell a broken fetch from a publisher who published nothing -
    # which is the question Stefan had to ask by hand.
    feeds = source_status.feeds_by_pair(snapshot.observations)
    today = date.today()
    fault = {(one.chemical, one.geography): source_status.collection_fault(
        feeds.get((one.chemical, one.geography)), snapshot.sources,
        since=one.last_day, today=today) for one in waiting}
    ours = [key for key, answer in fault.items() if answer != source_status.COLLECTING]

    title = (f"Collection gone quiet - {populations.PAIRS.of(len(waiting), len(watched))} behind "
             "their own rhythm" if waiting else
             f"Collection gone quiet - none, all {populations.PAIRS.count(len(watched))} on rhythm")
    quiet = st.expander(title, key="sources_quiet")
    quiet.caption(
        f"Only the {populations.PAIRS.count(len(watched))} somebody has priced inside "
        f"{market_lines_module.DRAWN_ON_CURRENT_DAYS} days can be late here; the "
        f"{len(held) - len(watched)} older than that are history, and the price pages say so. "
        "Each one is judged on the rhythm read from its own readings, so a monthly assessment is "
        "not late on day eight.")
    if not waiting:
        quiet.caption("Nothing is overdue.")
    else:
        quiet.caption(
            f"Of these, {populations.PAIRS.count(len(ours))} are ours - a feed that is failing, "
            "or one nothing fetches. For the rest we asked after the market went quiet and "
            "nothing came back, which is the publisher's silence and not ours."
            if ours else
            "None of these is a collection failure: every feed behind them was collected "
            "successfully after the market went quiet, and nothing came back. The publishers "
            "have published nothing.")
        # Two dates, because for a customs feed they are months apart and both are true: the month
        # the figure is about, and the day it could first be seen. Lateness is counted from the
        # second - Comtrade's December is not late in January - and a reader is shown the first.
        # One column showed the second under the heading "Last reading" until 5 November 2026, so
        # South Korea read 2026-03-01 when its newest customs month is December 2025.
        shown = any(one.lagged for one in waiting)
        quiet.dataframe(pd.DataFrame([
            {"Chemical": display_chemical(one.chemical), "Geography": one.geography,
             "Last figure": one.figure_day.isoformat(),
             **({"Could be seen": one.last_day.isoformat()} if shown else {}),
             "Expected": one.expected,
             "Quiet for": f"{one.days_since} days", "Readings": one.readings,
             "State": one.state,
             "On Current Market": "Yes" if (one.chemical, one.geography) in on_current else "No",
             "Longest gap": longest.get((one.chemical, one.geography), "-"),
             "Collection": fault.get((one.chemical, one.geography), source_status.COLLECTING)}
            for one in waiting]), hide_index=True, width="stretch",
            height=min(38 * (len(waiting) + 1), 420))
        if shown:
            quiet.caption(
                "\"Could be seen\" is months after \"Last figure\" for the customs feeds: "
                "UN Comtrade and Eurostat publish a month well after it ends, so they are judged "
                "on arrival and never on the month they describe.")


def render_quarantine() -> None:
    """Readings that did not become markets, with the publisher's own words.

    Masterplan D, v1.2.11. `ensure_identity` was get-or-create on nine fields and nothing stood
    between a publisher's printed string and a new market: 23 markets were minted holding a PRICE in
    the unit field, one print as two markets with one reading each. Three fields are gated now, and
    a value they cannot hold does not mint a market - and is not dropped either, which is what this
    section is for. Nothing is silently lost and nothing silently becomes a market.

    It is a queue, not a bin. Naming the value, or fixing the connector, lets the reading in on the
    next collection.
    """
    snapshot = _load_step6_snapshot()
    if snapshot is None:
        return
    rows = list(getattr(snapshot, "quarantine", []) or [])
    if not rows:
        # Nothing refused is the normal state and does not need a section of its own; an empty
        # table with a title would read as a problem. Said in one line so a reader knows the door
        # is there and is shut.
        st.caption("Nothing is held back: every reading collected has become a market, or joined "
                   "one.")
        return
    stopped = sum(int(row.get("readings") or 0) for row in rows)
    with st.expander(
            f"Held back, nothing lost - {len(rows)} values the application cannot read, "
            f"{populations.READINGS.count(stopped)} stopped", key="sources_quarantine",
            icon=":material/pan_tool:"):
        st.caption("A reading is held back only where a field has no honest fold and filing it "
                   "anyway would publish something untrue: a chemical the application does not "
                   f"price, a price written into the unit field, a kind of price outside the "
                   f"{len(price_kinds.KINDS)} it keeps. One row per value, however many readings "
                   "it has stopped. Name the value and the readings arrive on the next collection.")
        st.dataframe(pd.DataFrame([{
            "Publisher": _publisher_name(snapshot, str(row.get("source_id") or "")),
            "Field": _quarantine_field(str(row.get("field") or "")),
            "What was read": str(row.get("value") or "(nothing)"),
            "Why it is held back": str(row.get("reason") or ""),
            "Readings": int(row.get("readings") or 0),
            "First": str(row.get("first_seen") or ""),
            "Last": str(row.get("last_seen") or ""),
            "Document": str(row.get("document") or "")[-60:],
        } for row in rows]), hide_index=True, width="stretch",
            height=min(38 * (len(rows) + 1), 420))


# The field names the store uses, in a reader's words. The store's own name is the key a developer
# needs; a publisher's contact does not know what `observation_type` is.
_QUARANTINE_FIELDS = {"product_family": "Chemical", "unit": "Unit",
                      "observation_type": "Kind of price"}


def _quarantine_field(name: str) -> str:
    return _QUARANTINE_FIELDS.get(name, name)


def _publisher_name(snapshot, source_id: str) -> str:
    for source in snapshot.sources:
        if str(source.get("source_id")) == source_id:
            return str(source.get("publisher") or source.get("source_name") or source_id)
    return source_id or "-"


def render_data_sources(runtime_access: RuntimeAccess | None = None) -> None:
    st.markdown("## Prices")
    # One line per section, each opened only when wanted. Stefan, 15 October 2026: "condense it
    # with pull down sections rather than putting complete folded out tables, it just gets too
    # much". Every title carries the count that says whether it needs opening.
    #
    # Nine sections became four on 5 November 2026. Stefan: "Is all of this table necessary??" It
    # was not: three of the nine flagged 138 of 161 markets between them, most of them records the
    # application already calls history, and a list that flags 86% of everything is not a worklist.
    # Four earn a title of their own - the control, the one real warning, what has gone quiet, and
    # what we hold. Nothing was deleted; the rest are tables inside those four.
    st.caption("Open a section to see its table. Each title says what is inside.")
    catalogue_rows = render_customer_catalogue()
    render_collection_watch(catalogue_rows)
    render_market_data_collection(runtime_access)
    # Last, because it is the only section that is normally empty, and because it answers the
    # question the three above raise: what did NOT become one of these markets (masterplan D).
    render_quarantine()


def render_producer_signals_internal() -> None:
    st.markdown("## Producer Signals")
    st.caption("Internal producer-signal intake view. Acquisition remains owned by the Step 6 source-expansion work.")
    signals = load_producer_signals(PRODUCER_SIGNALS_PATH)
    if not signals:
        st.info("Producer signal intake contract is ready. No producer signals have been received yet.")
        with st.expander("Producer signal contract"):
            st.code(
                "signal_id | producer | chemical | geography | category | what_changed | announced_at | "
                "effective_from | effective_to | status | market_relevance | verification_status | source_url | source_date"
            )
        return
    selected = _signal_filters(signals, "producer_signals_internal")
    if not selected:
        st.info("No producer signals match the selected filters.")
        return
    st.caption(f"{len(selected)} of {len(signals)} signals")
    st.dataframe(pd.DataFrame(internal_display_rows(selected)), hide_index=True, width="stretch")


def render_purchase_history() -> None:
    """KGI's own purchase prices against the published market. Platform Admin only.

    Every number on this page is commercially confidential: what KGI paid BASF and what it paid
    Wanhua for the same product in the same month, and the spread between the two. In 2022 that
    spread was 45%. None of it belongs anywhere a customer can reach.
    """
    st.markdown("## Purchase History")
    st.caption(
        "HTC's own purchase prices, drawn against the published market assessment. "
        "Confidential - negotiated prices and supplier contracts. Internal use only."
    )
    transactions = _load_transactions("TDI", "China")
    if not transactions:
        st.info("No purchase history is loaded. The engine imports it at startup from the ERP "
                "extract and the signed purchase orders.")
        return

    payload = _load_forecast(TDI_CHINA_DOMESTIC_IDENTITY_KEY, (4, 8, 12))
    if not payload:
        st.warning("The Step 6 market-data engine is not reachable, so the published price cannot "
                   "be drawn beside the purchases.")
        return

    figure = _build_forecast_figure(payload, 12, str(payload.get("currency") or ""),
                                    display_currency(), _load_fx_table(),
                                    transactions=transactions)
    if figure.data:
        st.plotly_chart(figure, width="stretch",
                        config={"displaylogo": False, "scrollZoom": True,
                                "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"]})

    rows = []
    for entry in transactions:
        for point in entry.get("points") or ():
            rows.append({
                "Month": str(point.get("date"))[:7],
                "Supplier": str(entry.get("label") or "").split(" / ", 1)[-1],
                "Terms": entry.get("basis") or "",
                "Price": round(float(point.get("price") or 0)),
                "Currency": entry.get("currency") or "",
                "Record": "Signed order" if point.get("evidence") == "purchase_order" else "ERP posting",
                "Document": point.get("reference") or "",
            })
    if rows:
        st.dataframe(pd.DataFrame(sorted(rows, key=lambda r: (r["Month"], r["Supplier"]), reverse=True)),
                     hide_index=True, width="stretch")


def _close_the_application() -> None:
    """Close everything from the page, so the console window finishes by itself.

    Stefan, 26 September 2026: "that is not an elegant solution. Come back with something more
    elegant in the browser."

    The launcher starts the engine, the collection run and the delivery worker detached, so closing
    the console with X, or answering Y to "Terminate batch job", leaves all three running and
    holding the store. The only path that ever stopped them was Ctrl+C followed by N, which reaches
    the batch file's own cleanup lines.

    This does what those lines do. Streamlit then exits on its own, the batch file stops waiting for
    it, runs its cleanup over an already-clean slate, and the window closes. No prompt, no orphans.
    """
    st.markdown("## Close the application")
    st.caption("Stops the market-data engine, the collection run and the delivery worker, then "
               "closes this application. The console window closes by itself afterwards.")

    if st.session_state.get("shutdown_done"):
        for line in st.session_state.get("shutdown_report", []):
            st.write(line)
        st.success("Closed. You can close this tab.")
        return

    # No "are you sure you want to be asked" step. Arriving here is already the asking: from the
    # top bar the Close button comes straight here, and from Platform Admin the section is only
    # reached by scrolling to it. A button that exists to reveal the real button was a third click
    # on a two-click decision (Stefan, 16 October 2026: "I now need to push 3 times").
    st.warning("This closes the application and stops the market-data engine. "
               "Anything being collected right now is stopped with it.")
    # "Keep it running" only where there is something to go back to. From the top bar it leaves the
    # closing page; inside Platform Admin the section is just a section, and a cancel button that
    # redraws the same section is a button that does nothing.
    leaving = bool(st.session_state.get("closing_the_application"))
    confirm, cancel = st.columns([1, 1]) if leaving else (st.container(), None)
    if cancel is not None:
        with cancel:
            if st.button("Keep it running", key="shutdown_cancel"):
                st.session_state["shutdown_asked"] = False
                st.rerun()
    with confirm:
        if not st.button("Yes, close it", type="primary", key="shutdown_confirm"):
            return

    results = runtime_shutdown.stop_runtime()
    report = [f"{result.name}: {result.outcome}"
              + (f" - {result.detail}" if result.detail else "")
              for result in results]
    st.session_state["shutdown_report"] = report
    st.session_state["shutdown_done"] = True
    for line in report:
        st.write(line)
    if not runtime_shutdown.everything_stopped(results):
        st.error("Something did not stop. The application is still running so you can look at the "
                 "list above.")
        st.session_state["shutdown_done"] = False
        st.session_state["shutdown_asked"] = False
        return
    st.success("Closed. You can close this tab.")
    # Say the stop was asked for, before asking for it. Windows reports a terminated process as a
    # failure, so without this the launcher read a deliberate close as a crash: it printed "The MVP
    # stopped with an error" and sat waiting for a keypress, which is the console window Stefan was
    # left closing by hand on 27 September 2026. A crash still stops and waits, as it should.
    try:
        runtime_shutdown.say_the_close_was_asked_for()
    except (OSError, AttributeError) as exc:
        # AttributeError as well as OSError: Streamlit re-runs this file on every interaction and
        # keeps the modules it imported at start-up, so a browser tab left open across an update is
        # running today's page against yesterday's module. Stefan got a traceback on the close
        # button for exactly that reason on 27 September 2026. The close still happens; only the
        # marker the launcher reads is missing, so the console window is what needs a hand.
        st.caption(f"The console window may need closing by hand: {exc}")
    # The page is rendered before this process goes, so the reader sees the outcome rather than a
    # browser error. Streamlit exiting is what releases the batch file from its wait.
    _stop_this_process_shortly()


def _stop_this_process_shortly(delay: float = 1.5) -> None:
    import signal
    import threading

    def stop() -> None:
        os.kill(os.getpid(), getattr(signal, "SIGTERM", signal.SIGINT))

    threading.Timer(delay, stop).start()


# What a document is for. Three answers, because three different things arrive and they are stored
# in different places: a price list becomes readings, the weekly briefing becomes developments, and
# a special report becomes the graded state of the chain.
#
# Stefan, 8 October 2026: the briefings and the special reports are HTC Global's own work, written
# out of the PU Observatory and the PI3 engines, so there is no rights question on them and no
# publisher to name. What they add is the trend and the direction, never a price: the prices quoted
# inside a briefing are other people's assessments of series this application already collects, and
# reading them in here would count the same market twice.
INTAKE_KINDS = ("Prices", "Weekly briefing", "Supply chain report", "Pricing report")

# The document chosen from a watched folder, kept across reruns. See _pick_from_the_watched_folders.
_PICKED = "import_picked_document"


def _detected_intake(document) -> str:
    from raw_material_forecasting import briefing_reader, document_reader, monitor_reader

    text = getattr(document, "text", "") or ""
    if briefing_reader.looks_like_a_briefing(text):
        return "Weekly briefing"
    # The whole document as the reader sees it - tables first, then the running text. A Word file
    # of the Hormuz special report carries its title in a table, so its running text begins
    # elsewhere and the first 3,000 characters never said "special report" (18 October 2026): the
    # .docx was offered as Prices while the same report as a PDF was recognised.
    head = document_reader._document_text(document)[:6000].lower()
    if "special report" in head and "supply chain" in head:
        return "Supply chain report"
    if monitor_reader.looks_like_a_monitor(text) or monitor_reader.looks_like_a_monitor(head):
        return "Pricing report"
    return "Prices"


def _import_a_monitor(document, file_name: str) -> None:
    """A report written as prose, read into the same register the briefing feeds.

    Stefan, 9 October 2026, on a waiting list that offered a document and then handed it back
    unread: "This is stupid." It was. The weekly briefing is labelled and reads by rule; this one
    says the same kind of thing in paragraphs, so it goes to the model that already reads documents
    full of prices, under the same guard - a row whose quote is not in the document never reaches
    the screen.

    This half stores no price. The prices the report quotes are read on the same screen by
    `_import_prices` (18 October 2026) and go to the series they belong to, each under the
    publisher that assessed it - the same block, and the same proposed rows, that a plain price
    list goes through. Only the widget keys differ.
    """
    from raw_material_forecasting import intelligence_register as recorded
    from raw_material_forecasting import monitor_reader
    from raw_material_forecasting.document_reader import ReaderUnavailable
    from raw_material_forecasting.market_intelligence import DATA_DIR

    with st.spinner("Reading the report"):
        try:
            found = monitor_reader.read_developments(document)
        except ReaderUnavailable as problem:
            st.error(str(problem))
            return
    if not found:
        st.warning("No development could be read out of this report.")
        return

    frame = pd.DataFrame([
        {"Take": True,          # every item is offered; the person unticks what they do not want
         "No": number,
         "Happened": item.day or "",
         "Confidence": item.confidence or "unknown",
         "Chemicals": ";".join(item.chemicals),
         "Markets": ";".join(item.geographies),
         "Kind": item.kind,
         "What happened": item.headline,
         "Why it matters": item.context,
         "What it means for a buyer": item.implication,
         "Names a source": ", ".join(recorded.names_a_source(
             item.headline, item.context, item.implication)),
         "Read from": item.quote}
        for number, item in enumerate(found, start=1)
    ])
    edited = st.data_editor(frame, width="stretch", hide_index=True, key="monitor_rows",
                            disabled=["No", "Confidence", "Read from", "Names a source"])
    st.caption("Every row carries the text it was read from. A row with nothing under Chemicals "
               "is stored too - it has no market to sit under yet, and it is still intelligence. "
               "A row naming an assessor is stored with that name on it and withheld from "
               "customer-facing output until the name is out of the wording.")

    if not st.button("Confirm and store", type="primary", key="monitor_confirm"):
        return
    taken = edited[edited["Take"] == True]          # noqa: E712 - a pandas mask, not a comparison
    rows, refused = [], []
    today = date.today()
    for _index, line in taken.iterrows():
        item = found[int(line["No"]) - 1]
        chemicals = [part.strip().upper() for part in str(line["Chemicals"]).split(";") if part.strip()]
        markets = [part.strip() for part in str(line["Markets"]).split(";") if part.strip()]
        # A ticked row is stored. It used to be dropped here for naming no chemical or no market -
        # silently, after the person had ticked it - so an item about the Strait of Hormuz could be
        # confirmed on screen and simply not appear. What this application cannot yet attach to a
        # price series is still intelligence. Stefan, 21 October 2026: "get rid of the fucking
        # gate, put it in in the market intelligence."
        named = recorded.names_a_source(line["What happened"], line["Why it matters"],
                                        line["What it means for a buyer"])
        # Stored with the name on it rather than refused. `attribution` is kept off every
        # customer page and `report_gates` withholds a line that names a source, so the rights rule
        # is enforced where it belongs - where something is published, not by losing HTC Global's
        # own intelligence at the door. Stefan, 21 October 2026: "Store it and flag it as naming a
        # source - withhold it only from customer-facing output."
        rows.append(recorded.Entry(
            reported=str(line["Happened"] or today.isoformat()),
            event_date=str(line["Happened"] or ""), precision="DAY" if line["Happened"] else "PERIOD",
            headline=str(line["What happened"]).strip(), kind=str(line["Kind"]).strip(),
            chemicals=";".join(chemicals), geographies=";".join(markets),
            confidence=item.confidence, context=str(line["Why it matters"]).strip(),
            implication=str(line["What it means for a buyer"]).strip(), quote=item.quote,
            attribution=", ".join(named), document=file_name,
            stored_on=today.isoformat()))
    for number, named in flagged_names(taken):
        st.info(f"Item {number} names {named}. It is stored and shown here, and withheld from "
                f"customer-facing output until the name is out of the wording.")
    if not rows:
        st.error("Nothing is ticked, or the ticked rows have no market to sit under.")
        return
    added, total = recorded.append(rows, DATA_DIR)
    told = _tell_the_engine(rows, document=file_name)
    st.success(f"{added} stored, {len(rows) - added} already held. The register now holds {total}.")
    if told:
        st.caption(told)
    # No rerun here. The prices this report quotes are read below, and clearing the screen now
    # would take that half with it - which is exactly what happened on 22 October 2026.


def _tell_the_engine(rows, *, document: str) -> str:
    """Put the stored developments where the forecaster looks. Never stops an import.

    The register beside the application is what the Market Intelligence page reads and it has
    already been written by the time this runs. This is the other half: `event_record` in the
    engine's store, which is what `feature_rows` counts. Before 21 October 2026 only the first was
    written, so every imported document was visible on a page and invisible to every forecast.

    A failure here is reported and swallowed. The engine being down is not a reason to lose what a
    person just confirmed, and the backfill on this screen can carry it over later.
    """
    from raw_material_forecasting import intelligence_bridge as bridge
    from raw_material_forecasting.market_data_engine_client import (
        ImportRefused, MarketDataEngineUnavailable)

    events = bridge.to_events(rows, document=document)
    if not events:
        return ""
    try:
        client = MarketDataEngineClient(MARKET_DATA_ENGINE_URL, timeout=60.0)
        result = client.send_intelligence(bridge.BRIEFING, events)
    except (MarketDataEngineUnavailable, ImportRefused) as problem:
        return f"stored here, but the forecaster was not told: {problem}"
    stored, already = result.get("stored", 0), result.get("already_held", 0)
    return (f"{stored} went to the forecaster"
            + (f", {already} were already there" if already else ""))


_OUTCOME = "import_outcome"
_BATCH_OUTCOME = "import_batch_outcome"


def _finished_with(file_name: str, headline: str, note: str = "") -> None:
    """Let go of the document, and carry the outcome over the rerun that clears the screen.

    The review screen is drawn from the document held in session state, and the waiting list above
    it is counted from what the register already names. Both are stale the moment a store succeeds:
    the screen still offers rows that are now kept, and the list still counts the document as
    waiting. Dropping the document and rerunning fixes both, and the outcome is stashed first
    because a rerun would otherwise wipe the only confirmation that anything happened.

    Stefan, 22 October 2026: "after confirm and store, the screen should refresh so I can see that
    this document has been processed."
    """
    st.session_state[_OUTCOME] = (file_name, headline, note)
    st.session_state.pop(_PICKED, None)
    st.rerun()


def _show_the_last_outcome() -> None:
    """What the run before this one stored, shown once."""
    outcome = st.session_state.pop(_OUTCOME, None)
    if not outcome:
        return
    file_name, headline, note = outcome
    st.success(f"**{file_name}** - {headline}")
    if note:
        st.caption(note)


def flagged_names(taken) -> list[tuple[int, str]]:
    """The ticked rows that name an assessor, so the screen can say where they will not appear.

    They are stored either way. This is a note, not a refusal.
    """
    out = []
    for _index, line in taken.iterrows():
        named = str(line.get("Names a source", "") or "").strip()
        if named:
            out.append((int(line["No"]), named))
    return out


def _import_a_briefing(document, file_name: str) -> None:
    """The weekly briefing, read into the register of what happened."""
    from datetime import date

    from raw_material_forecasting import briefing_reader
    from raw_material_forecasting import intelligence_register as recorded
    from raw_material_forecasting.market_intelligence import DATA_DIR

    briefing = briefing_reader.read(document.text, name=file_name)
    for note in briefing.notes:
        st.info(note)
    if not briefing.items:
        return
    st.write(f"{len(briefing.items)} items, for the week ending "
             f"{briefing.covers_to or briefing.prepared}.")

    frame = pd.DataFrame([
        {"Take": True,          # every item is offered; the person unticks what they do not want
         "No": item.number,
         "Reported": str(briefing.covers_to or briefing.prepared or ""),
         "Happened": item.day.isoformat() if item.day else "",
         "Confidence": item.confidence,
         "Chemicals": ";".join(item.chemicals),
         "Markets": ";".join(item.geographies),
         "Kind": item.kind,
         "What happened": item.headline,
         "Why it matters": item.context,
         "What it means for a buyer": item.implication,
         "Names a source": ", ".join(recorded.names_a_source(
             item.headline, item.context, item.implication)),
         "Read from": item.quote}
        for item in briefing.items
    ])
    edited = st.data_editor(frame, width="stretch", hide_index=True, key="briefing_rows",
                            disabled=["No", "Confidence", "What happened", "Read from",
                                      "Names a source"])
    st.caption("An item with nothing in Chemicals has no market to sit under and will not be "
               "stored. The wording under What it means is what a customer reads, so put it in "
               "the context of the report rather than leaving the briefing's own shorthand. "
               "A row that names an assessor or a news desk cannot be stored until that name is "
               "out of the wording: a customer report names no source anywhere.")

    if not st.button("Confirm and store", type="primary", key="briefing_confirm"):
        return
    taken = edited[edited["Take"] == True]          # noqa: E712 - a pandas mask, not a comparison
    rows, refused = [], []
    for _index, line in taken.iterrows():
        item = next(one for one in briefing.items if one.number == int(line["No"]))
        chemicals = [part.strip().upper() for part in str(line["Chemicals"]).split(";") if part.strip()]
        markets = [part.strip() for part in str(line["Markets"]).split(";") if part.strip()]
        # A ticked row is stored. It used to be dropped here for naming no chemical or no market -
        # silently, after the person had ticked it - so an item about the Strait of Hormuz could be
        # confirmed on screen and simply not appear. What this application cannot yet attach to a
        # price series is still intelligence. Stefan, 21 October 2026: "get rid of the fucking
        # gate, put it in in the market intelligence."
        named = recorded.names_a_source(line["What happened"], line["Why it matters"],
                                        line["What it means for a buyer"])
        # Stored with the name on it rather than refused. `attribution` is kept off every
        # customer page and `report_gates` withholds a line that names a source, so the rights rule
        # is enforced where it belongs - where something is published, not by losing HTC Global's
        # own intelligence at the door. Stefan, 21 October 2026: "Store it and flag it as naming a
        # source - withhold it only from customer-facing output."
        entry = recorded.from_briefing(
            item, document=file_name, reported=briefing.covers_to or briefing.prepared,
            chemicals=chemicals, geographies=markets,
            implication=str(line["What it means for a buyer"]).strip(),
            attribution=", ".join(named))
        entry.kind = str(line["Kind"]).strip()
        entry.context = str(line["Why it matters"]).strip()
        rows.append(entry)
    for number, named in flagged_names(taken):
        st.info(f"Item {number} names {named}. It is stored and shown here, and withheld from "
                f"customer-facing output until the name is out of the wording.")
    if not rows:
        st.error("Nothing is ticked, or the ticked rows have no market to sit under.")
        return
    added, total = recorded.append(rows, DATA_DIR)
    told = _tell_the_engine(rows, document=file_name)
    _finished_with(file_name,
                   f"{added} stored, {len(rows) - added} already held. "
                   f"The register now holds {total}.", told)


def _import_a_condition(document, file_name: str) -> None:
    """A special report, read into the graded state of the chain.

    Stefan, 9 October 2026, looking at a form asking him for the state of the chain: "All that
    info is in the report, it is not good that I need to add it manually." It is in the report, in
    its own tables, and it is read from there now. What is shown is what was read; the form is
    behind it for the one report in eighteen whose author wrote something the reader cannot follow.
    """
    from raw_material_forecasting import supply_conditions
    from raw_material_forecasting.market_intelligence import DATA_DIR

    found = supply_conditions.read_report(document, name=file_name)
    complete = bool(found.reported and found.state and found.exposures)
    exposures_frame = pd.DataFrame([
        {"Input": row.group, "Level": row.level, "Chemicals": row.chemicals, "Why": row.mechanism}
        for row in found.exposures
    ] or [{"Input": "", "Level": "", "Chemicals": "", "Why": ""}])

    if complete:
        st.success(f"Read from the document: {found.event or 'this event'}, "
                   f"{found.reported}, chain graded {found.state.lower()}, "
                   f"{len(found.exposures)} input groups.")
        if found.note:
            st.write(found.note)
        st.dataframe(exposures_frame, hide_index=True, width="stretch")
        with st.expander("Change something before it is stored"):
            event, reported, state, note, edited = _condition_form(found, supply_conditions,
                                                                   exposures_frame)
    else:
        st.warning("This report does not give everything in the shape the reader follows. "
                   "What is missing is below; a level guessed wrong is worse than one set by hand.")
        event, reported, state, note, edited = _condition_form(found, supply_conditions,
                                                               exposures_frame)

    if complete:
        event = st.session_state.get("condition_event") or found.event
        reported = st.session_state.get("condition_date") or found.reported
        state = st.session_state.get("condition_state") or found.state
        note = st.session_state.get("condition_note") or found.note
        edited = st.session_state.get("condition_rows_frame", exposures_frame)

    if not st.button("Confirm and store", type="primary", key="condition_confirm"):
        return
    if state in ("", "- choose -") or not str(reported).strip():
        st.error("The state of the chain and the report date are both needed.")
        return
    exposures = []
    for _index, line in pd.DataFrame(edited).iterrows():
        if not str(line["Input"]).strip() or not str(line["Level"]).strip():
            continue
        exposures.append(supply_conditions.Exposure(
            group=str(line["Input"]).strip(), level=str(line["Level"]).strip(),
            chemicals=str(line["Chemicals"]).strip().upper(),
            mechanism=str(line["Why"]).strip()))
    added, total = supply_conditions.append([supply_conditions.Condition(
        event=str(event).strip(), reported=str(reported).strip(), state=str(state), 
        note=str(note).strip(), document=file_name, exposures=exposures)], DATA_DIR)
    st.success(f"{added} stored. The application holds {total} readings of the chain.")


def _condition_form(found, supply_conditions, exposures_frame):
    """What was read, offered for correction. Every field opens on what the document said."""
    col1, col2 = st.columns([2, 1])
    with col1:
        event = st.text_input("What is happening", value=found.event, key="condition_event")
    with col2:
        reported = st.text_input("Report date", value=found.reported, key="condition_date",
                                 help="The day the report was written, as 2026-07-06.")
    states = ["- choose -", *supply_conditions.STATES]
    state = st.selectbox("State of the chain", states, key="condition_state",
                         index=states.index(found.state) if found.state in states else 0)
    note = st.text_area("In one sentence", value=found.note, key="condition_note",
                        help="What a customer is told about the chain as a whole.")
    edited = st.data_editor(exposures_frame, width="stretch", hide_index=True,
                            num_rows="dynamic", key="condition_rows")
    st.session_state["condition_rows_frame"] = edited
    return event, reported, state, note, edited


def _watched_arrivals():
    """What has landed in HTC Global's own intelligence folders and has not been read yet."""
    from raw_material_forecasting import intelligence_register as recorded
    from raw_material_forecasting import intelligence_watch as watch
    from raw_material_forecasting import supply_conditions
    from raw_material_forecasting.market_intelligence import DATA_DIR

    folders = watch.folders()
    held = watch.held_documents(recorded.load(DATA_DIR), supply_conditions.load(DATA_DIR))
    # By content, not by file name. The same report arrives as a Word file and as a PDF, and one
    # week of the newsletter arrived twice with a "(1)" on the end; comparing names showed five
    # reports already in the store as though they had never been read. Each document is opened
    # once and its hash and stated day remembered, so the list costs nothing to draw again.
    return folders, watch.unread(folders, data_dir=DATA_DIR, held=held), watch


def _pick_from_the_watched_folders():
    """Choose what to read: one document, or every one that is waiting.

    Stefan, 8 October 2026: the briefing and the special report come out of a scheduled workflow
    every week and the intention is that they get loaded every week. Remembering to do that is not
    a job for a person. On 9 October, looking at thirty-eight waiting documents: "if I have these
    reports and they would add datapoints and or signals I would want to import all. Even if it is
    from the past." So the list is ticked rather than stepped through, and a batch reads every
    ticked document in one pass.

    Nothing here stores anything on its own judgement. A batch stores what each document says
    about itself and reports what it could not read; a single document still goes to the review
    screen.
    """
    folders, waiting, watch = _watched_arrivals()
    for folder in folders:
        if folder.missing:
            st.warning(f"{folder.name} is not where it is expected: {folder.path}")
    if not waiting:
        st.caption(watch.describe(folders, waiting))
        return None

    st.write(f"**{watch.describe(folders, waiting)}**")
    frame = pd.DataFrame([
        {"Read": False, "Date": f"{row.written:%d %b %Y}", "Kind": row.kind,
         "Document": row.label, "Folder": row.folder}
        for row in waiting
    ])
    edited = st.data_editor(frame, width="stretch", hide_index=True, key="watched_rows",
                            disabled=["Date", "Kind", "Document", "Folder"])
    ticked = [row for row, take in zip(waiting, edited["Read"].tolist()) if take]
    one_col, all_col = st.columns([1, 1])
    with one_col:
        one = st.button("Read this one", key="watched_read", disabled=len(ticked) != 1,
                        help="Tick exactly one document to look at its rows before storing.")
    with all_col:
        many = st.button(f"Read and store {len(ticked)} documents", type="primary",
                         key="watched_read_all", disabled=len(ticked) < 2)
    if one and len(ticked) == 1:
        try:
            picked = ticked[0].name, ticked[0].file.read_bytes()
        except OSError as problem:
            st.error(f"{ticked[0].name} could not be opened: {problem}")
            return None
        # Kept, because a button is True only on the rerun its own click caused. Handing the
        # document back without keeping it drew the review screen once; the data_editor on that
        # screen caused the next rerun, which found no click and no document and drew an empty
        # page. Stefan, 21 October 2026, reading the briefing of the 19th: "the spinner works but
        # then nothing".
        st.session_state[_PICKED] = picked
        return picked
    if many and ticked:
        _read_them_all(ticked)
    return None


def _read_them_all(arrivals) -> None:
    """Read every ticked document and store what it says about itself.

    A document is stored only where it was read whole. A special report that does not give its own
    date and state is listed as needing a look rather than stored half-read, and a briefing item
    that names an assessor is left out exactly as it is on the one-at-a-time screen.
    """
    from raw_material_forecasting import briefing_reader
    from raw_material_forecasting import document_intake
    from raw_material_forecasting import intelligence_register as recorded
    from raw_material_forecasting import supply_conditions
    from raw_material_forecasting.market_intelligence import DATA_DIR

    stored, skipped, refused = [], [], []
    progress = st.progress(0.0, text="Reading")
    for index, arrival in enumerate(arrivals, start=1):
        progress.progress(index / len(arrivals), text=f"Reading {arrival.name}")
        try:
            document = document_intake.read(arrival.name, arrival.file.read_bytes())
        except OSError as problem:
            skipped.append((arrival.name, str(problem)))
            continue
        kind = arrival.kind if arrival.kind != "any" else _detected_intake(document)
        if kind == "condition":
            found = supply_conditions.read_report(document, name=arrival.name,
                                                  event=_event_of(arrival, document))
            if not found.reported:
                # Not the day the file was saved. "Geopolitical Update EU Flexible Foam
                # 21-04-2026.docx" was saved on the 22nd, and a chain reading filed on the wrong
                # day is a chain reading of a day nobody graded. Stefan, 26 October 2026: "the
                # file title is not decisive, neither is the saving date."
                skipped.append((arrival.name, "it states no date of its own - open it on its own "
                                              "screen, where you can read what it says"))
                continue
            if not found.state:
                # No grid is fine; no state is not. The state IS the reading, and guessing one
                # from prose would write a chain history the reports never stated - "remains below
                # a full critical stop" is not a reading of Critical.
                skipped.append((arrival.name, "it states no classification - grade it on its own "
                                              "screen, where you can read what it does say"))
                continue
            added, _total = supply_conditions.append([found], DATA_DIR)
            stored.append((arrival.name, f"{added} reading of the chain"))
            continue
        if kind == "briefing":
            briefing = briefing_reader.read(document.text, name=arrival.name)
            rows = []
            for item in briefing.items:
                # Every item is kept. It used to need a chemical AND a geography AND to be marked
                # for a customer, and anything else fell through a bare `continue` - unstored,
                # unreported, with nothing on screen to say it had been dropped. Three of the six
                # items in the briefing of 19 October went that way: the Strait of Hormuz, the
                # BASF-Evonik opposition, the Indian recycling plant. Stefan, 21 October 2026:
                # "get rid of the fucking gate, put it in in the market intelligence".
                named = recorded.names_a_source(item.headline, item.context, item.implication)
                if named:
                    refused.append((arrival.name, item.number, ", ".join(named)))
                rows.append(recorded.from_briefing(
                    item, document=arrival.name,
                    reported=briefing.covers_to or briefing.prepared,
                    attribution=", ".join(named)))
            if not rows:
                skipped.append((arrival.name, "no item in it can be stored"))
                continue
            added, _total = recorded.append(rows, DATA_DIR)
            told = _tell_the_engine(rows, document=arrival.name)
            stored.append((arrival.name, f"{added} developments"
                                         + (f"; {told}" if told else "")))
            continue
        if kind == "monitor":
            skipped.append((arrival.name, "a pricing report is read on its own, because its rows "
                                          "are proposed by a model and have to be looked at"))
            continue
        skipped.append((arrival.name, "this kind is read one at a time"))
    progress.empty()
    # Stashed and shown on the next pass, so the rerun that clears the screen and recounts the
    # waiting list does not take the results with it.
    st.session_state[_BATCH_OUTCOME] = (stored, refused, skipped)
    st.session_state.pop(_PICKED, None)
    st.rerun()


def _show_the_last_batch() -> None:
    """What the batch read before this pass did, shown once."""
    outcome = st.session_state.pop(_BATCH_OUTCOME, None)
    if not outcome:
        return
    stored, refused, skipped = outcome
    if stored:
        st.success(f"{len(stored)} documents read and stored.")
        st.dataframe(pd.DataFrame([{"Document": name, "Stored": what} for name, what in stored]),
                     hide_index=True, width="stretch")
    for name, number, named in refused:
        st.info(f"{name}, item {number} names {named}. It is stored and shown in Market "
                f"Intelligence, and withheld from customer-facing output until the name is out "
                f"of the wording.")
    if skipped:
        st.info("These were not stored and are still waiting:")
        st.dataframe(pd.DataFrame([{"Document": name, "Why": why} for name, why in skipped]),
                     hide_index=True, width="stretch")


# Enough mentions to be what the report is about rather than an aside. The updates that ARE about
# the strait name it three to seven times, in the summary; a report that merely refers to it once
# in passing is not a reading of it.
_HORMUZ_MENTIONS = 2


def _event_of(arrival, document=None) -> str:
    """What a special report is about, read from the report.

    The file name was the whole of this rule until 26 October 2026, so
    "PU_Supply_Chain_Geopolitical_Update_2026-05-29.docx" - seven mentions of the Strait of Hormuz,
    starting in its first paragraph - was filed as a different event from the report issued five
    weeks later under a name that happened to say "Hormuz". They are one disruption, and
    `(event, reported)` is the dedup key.

    Stefan, 18 October 2026: "You are judging by report title....not by content.....that is
    dangerous."
    """
    text = str(getattr(document, "text", "") or "")
    if text.lower().count("hormuz") >= _HORMUZ_MENTIONS:
        return "Strait of Hormuz disruption"
    # Falling back to the name only where there is no text to read.
    return "Strait of Hormuz disruption" if "hormuz" in arrival.name.lower() else ""


def render_document_import() -> None:
    """Drop in a document. It is read for you. You check the rows and store the ones that are right.

    Stefan, 7 October 2026, with his own monitor document on the first version of this screen: six
    dropdowns to fill, every suggestion empty, fourteen rows read and none of them complete. He was
    right - a form that asks a reader to describe their own document is not an import. So the
    document is read by the model now, and the mapping survives only as a fallback for a machine
    with no key.

    What keeps an invented price out is the quote. Every proposed row carries the exact text it was
    read from, a row whose quote is not in the document is dropped before it reaches the screen, and
    the quote sits in the table beside the number so a glance checks it against the source.
    """
    import base64

    from raw_material_forecasting import document_intake, document_reader, price_import
    from raw_material_forecasting.market_data_engine_client import ImportRefused

    st.subheader("Import a document")
    st.caption("Any file: a spreadsheet, a PDF, a Word letter, a deck, a web page, an email or a "
               "photograph. The file is kept whole whether or not anything can be read out of it.")

    _show_the_last_outcome()
    _show_the_last_batch()
    waiting = _pick_from_the_watched_folders()
    uploaded = st.file_uploader(
        "Or drop a document in", type=None, key="import_document",
        help="Nothing is stored until you confirm the rows below.")
    if uploaded is not None:                      # a dropped file replaces a chosen one
        st.session_state.pop(_PICKED, None)
        file_name, data = uploaded.name, uploaded.getvalue()
    elif waiting is not None:
        file_name, data = waiting
    elif st.session_state.get(_PICKED):           # chosen on an earlier rerun, still being read
        file_name, data = st.session_state[_PICKED]
    else:
        return
    if st.session_state.get(_PICKED) and st.button("Choose a different document",
                                                   key="import_let_go"):
        st.session_state.pop(_PICKED, None)
        st.rerun()
    document = document_intake.read(file_name, data)
    st.write(f"**{file_name}** - {document_intake.describe(document)}")
    for note in document.notes:
        st.info(note)

    # Which of the three this is. It is detected and then shown, because a briefing that arrives as
    # a price list would be read for prices it only quotes.
    detected = _detected_intake(document)
    intake = st.radio("What is in this document", INTAKE_KINDS,
                      index=INTAKE_KINDS.index(detected), horizontal=True, key="import_intake")
    if intake == "Weekly briefing":
        st.caption("HTC Global's own briefing. No rights question and no publisher to name, and "
                   "no price in it is stored: those are other people's assessments of markets "
                   "this application already collects.")
        _import_a_briefing(document, file_name)
        return
    if intake == "Pricing report":
        st.caption("HTC Global's own pricing report. It is written as prose rather than to a "
                   "template, so it is read by the model that reads documents full of prices, "
                   "under the same rule: every row carries the text it was read from. What it "
                   "reports goes to Market Intelligence; the prices it quotes go to the markets "
                   "they belong to, each under the publisher that assessed it.")
        _import_a_monitor(document, file_name)
        # Stefan, 18 October 2026: a pricing report has to add its prices to the series as well as
        # its developments to the intelligence. Until now the prices were read and thrown away.
        st.markdown("---")
        st.subheader("Prices quoted in this report")
        _import_prices(document, file_name, data, document_reader, price_import, ImportRefused,
                       key="monitor_prices")
        # Both halves are above. The screen clears when the person says the document is done, not
        # when the first half is stored.
        st.markdown("---")
        if st.button("Done with this document", key="monitor_done"):
            _finished_with(file_name, "read for both its developments and its prices.")
        return
    if intake == "Supply chain report":
        st.caption("HTC Global's own special report, held as the graded state of the chain rather "
                   "than as a document.")
        _import_a_condition(document, file_name)
        return

    _import_prices(document, file_name, data, document_reader, price_import, ImportRefused)


def _import_prices(document, file_name: str, data: bytes, document_reader, price_import,
                   ImportRefused, *, key: str = "import") -> None:
    """Propose every price in the document, let the person check the rows, store the ticked ones.

    One block for two intakes: a document that is a price list, and HTC's own pricing report,
    which quotes prices beside what it says about the market (18 October 2026). `key` keeps the
    widgets of the two apart.
    """
    import base64

    rows = _read_document_rows(document, file_name, document_reader, price_import)

    if rows is None:

        return

    if not rows:

        st.warning("No prices were found in this document.")

        return

    # What the document itself said, offered rather than asked for. The reader has always read a
    # publisher per row; the page used to ignore it and put one typed name on every row.
    named = [str(row.values.get("publisher") or "").strip()
             for row in rows if str(row.values.get("publisher") or "").strip()]
    suggested = Counter(named).most_common(1)[0][0] if named else ""

    publisher = st.text_input(

        "Who issued it", value=suggested, key=f"{key}_publisher",

        help="Read from the document where it says so, and used only for rows that name nobody. "
             "A row's own Publisher column wins.")

    if named:

        st.caption(f"{len(named)} of {len(rows)} rows name their issuer in the document.")

    # No default and no blank. What a document may be used for depends on what is in it, and Stefan

    # rules on it document by document (5 October 2026).

    rights = st.selectbox(

        "Rights", ["- choose -", *_alphabetical(

            ["PUBLIC_ATTRIBUTION", "LICENSED_OR_PUBLIC_EXCERPT", "LICENSED_INTERNAL_USE",

             "INTERNAL_ONLY", "INTERNAL"])],

        key=f"{key}_rights",

        help="LICENSED and INTERNAL are never named on a customer screen.")



    tally = price_import.summarise(rows)

    st.write(f"{tally['rows']} prices read, {tally['ready']} complete, {tally['held']} held back.")

    frame = pd.DataFrame([

        {"Take": row.ready, "No": row.source_row,

         "Chemical": row.values.get("chemical", ""),

         "Geography": row.values.get("geography", ""),

         "Terms": row.values.get("basis", ""),

         "Publisher": row.values.get("publisher", ""),

         "Date": row.values.get("observation_date", ""),

         "Currency": row.values.get("currency", ""),

         "Price": row.values.get("price"),

         "Kind": row.values.get("observation_type", ""),

         "Read from": row.values.get("quote", ""),

         "Why it is held": ", ".join((*row.missing, *row.problems))}

        for row in rows

    ])

    edited = st.data_editor(frame, width="stretch", hide_index=True, key=f"{key}_rows",

                            disabled=["No", "Read from", "Why it is held"])

    st.caption("Tick a row to include it. The text under Read from is what the price "

               "came off in the document; a row with something in the last column "

               "cannot be stored until that is filled in.")



    if not st.button("Confirm and store", type="primary", key=f"{key}_confirm"):

        return

    if rights == "- choose -":

        st.error("Choose the rights before storing. There is no default.")

        return

    taken = edited[edited["Take"] == True]          # noqa: E712 - a pandas mask, not a comparison

    held = [str(row["No"]) for _index, row in taken.iterrows()

            if str(row["Why it is held"]).strip()]

    if held:

        st.error("These rows are still incomplete: " + ", ".join(held))

        return

    # Grouped by who issued each price. One document can quote several assessors - the Price
    # Monitor of 20 October 2026 quotes SunSirs among others - and the engine takes one publisher
    # per import, so one import is sent per issuer rather than one name being put on them all.
    by_issuer: dict[str, list[dict]] = {}

    for _index, row in taken.iterrows():

        original = next(item for item in rows if item.source_row == int(row["No"]))

        values = dict(original.values)

        values.update({"product": row["Chemical"], "market_geography": row["Geography"],

                       "commercial_basis": row["Terms"], "currency": row["Currency"],

                       "observation_type": row["Kind"],

                       "observation_date": str(row["Date"]), "price": float(row["Price"])})

        values.setdefault("unit", units.MT)

        values.setdefault("packaging", "Not specified")

        issuer = str(row.get("Publisher", "") or "").strip() or publisher

        by_issuer.setdefault(issuer, []).append(values)

    if not by_issuer:

        st.error("Nothing is ticked.")

        return

    without = [str(row["No"]) for _index, row in taken.iterrows()
               if not (str(row.get("Publisher", "") or "").strip() or publisher)]

    if without:

        st.error("These rows name no issuer, in the document or in the box above: "
                 + ", ".join(without))

        return

    content = base64.b64encode(data).decode("ascii")

    answers = []

    try:

        for issuer, issuer_rows in sorted(by_issuer.items()):

            answers.append((issuer, MarketDataEngineClient().send_import({

                "file_name": file_name,

                "content_base64": content,

                "publisher": issuer,

                "rights_class": rights,

                "rows": issuer_rows,

            })))

        answer = answers[-1][1]

    except ImportRefused as exc:

        st.error(str(exc))

        return

    except Exception as exc:

        st.error(f"The engine could not be reached: {exc}")

        return

    # Every issuer, not just the last one answered: a document quoting three assessors is three
    # imports, and reporting one of them would understate what was stored.
    total = sum(int(reply.get("stored", 0) or 0) for _issuer, reply in answers)

    st.success(f"{total} readings stored, from "
               f"{len(answers)} issuer{'' if len(answers) == 1 else 's'}.")

    if len(answers) > 1:

        st.dataframe(pd.DataFrame([{"Issuer": issuer, "Stored": reply.get("stored", 0),
                                    "Series key": reply.get("source_id", "")}
                                   for issuer, reply in answers]),
                     hide_index=True, width="stretch")


def _read_document_rows(document, name: str, document_reader, price_import):
    """The proposed rows: read by the model, or mapped by hand when there is no key.

    Returns None when the reader is unavailable and the fallback has not been filled in yet, so the
    page stops without claiming the document holds nothing.
    """
    if document_reader.configured():
        cached = st.session_state.get("_import_rows_for")
        if cached and cached[0] == name:
            return cached[1]
        with st.spinner("Reading the document."):
            try:
                rows = document_reader.read_rows(document)
            except document_reader.ReaderUnavailable as exc:
                st.error(str(exc))
                return _mapped_by_hand(document, price_import)
        st.session_state["_import_rows_for"] = (name, rows)
        return rows
    st.warning("No reading key is set on this machine, so the columns have to be mapped by hand.")
    return _mapped_by_hand(document, price_import)


def _mapped_by_hand(document, price_import):
    """The fallback. Tables in the order the document holds them, never sorted.

    Sorted by name, "table 10" came before "table 2" and the page opened on the source register at
    the back of Stefan's monitor instead of the price table at the front (7 October 2026). The order
    of tables in a document is the document's own and carries meaning; the alphabet does not.
    """
    if not document.tables:
        st.warning("No table was found in this document, and there is no key to read it with. "
                   "The file itself is kept.")
        return []
    with st.expander("Map the columns by hand", expanded=True):
        labels = [sheet.name for sheet in document.tables]
        chosen = st.selectbox("Table", labels, key="import_table")
        sheet = next(item for item in document.tables if item.name == chosen)
        suggested = price_import.suggest_mapping(sheet.columns)
        mapping: dict[str, str] = {}
        columns = st.columns(3)
        for index, field_name in enumerate(("chemical", "geography", "basis", "observation_date",
                                            "price", "packaging")):
            with columns[index % 3]:
                options = ["- none -", *sheet.columns]
                current = suggested.get(field_name)
                picked = st.selectbox(
                    field_name.replace("_", " ").title(), options,
                    index=options.index(current) if current in options else 0,
                    key=f"import_map_{field_name}")
                if picked != "- none -":
                    mapping[field_name] = picked
        settings = {
            "currency": st.selectbox(
                "Currency", _alphabetical(list(DISPLAY_CURRENCIES)),
                key="import_currency"),
            # The manual import used to post "t" while every screen printed MT. One token now
            # (masterplan A, item 4), and it is the store's.
            "unit": units.MT,
            "packaging": "Not specified",
            "observation_type": st.selectbox(
                "What kind of price", _alphabetical(price_import.OBSERVATION_TYPES),
                key="import_observation_type"),
        }
    return price_import.propose(sheet, mapping, settings)


# The customer report.
#
# Darren Millar asked for a monthly or quarterly report on raw material price movements that he can
# put in front of his own customers: independent of his company, and not built on ICIS numbers,
# which he does not trust enough to show. Neveon wants the same. So this is not one document for one
# reader; it is a report with four selectors, produced on demand from the markets held here.
#
# Stefan, 8 October 2026: HTC Global's own research, carrying the HTC Global and PI3 marks, his name
# and address as the contact, and no source named anywhere in it. The four selectors are his -
# chemicals, geography, period, and the feedstocks under whichever chemicals were chosen.
#
# Nothing on this page composes a sentence. The figures come from period_report, the prose from
# period_narrative, the document from period_report_docx, and no model is anywhere in that chain.
REPORT_PERIODS_OFFERED = 12
# How far back the chart under each market reaches: a year of months, two years of quarters. A
# chart that starts where the period starts shows the period and hides the run-up to it.
REPORT_HISTORY_PERIODS = {"month": 12, "quarter": 8}


def _report_periods(kind: str, today: date) -> list:
    """The periods a report can be run for: this one and the eleven before it, newest first."""
    from raw_material_forecasting.period_report import period_for

    period = period_for(today, kind)
    out = [period]
    for _step in range(REPORT_PERIODS_OFFERED - 1):
        period = period.previous
        out.append(period)
    return out


def _carried_text(behind: dict, condition) -> list[str]:
    """Wording that came in from a document HTC Global already published.

    The writing rules are ours and apply to what we write. A line lifted out of a briefing is
    evidence, and rewriting it to suit a house style would be editing evidence.
    """
    out: list[str] = []
    for items in (behind or {}).values():
        for entry in items:
            for field in ("headline", "development", "context", "implication"):
                value = str(getattr(entry, field, "") or "").strip()
                if value:
                    out.append(value)
    for field in ("note", "event"):
        value = str(getattr(condition, field, "") or "").strip()
        if value:
            out.append(value)
    for exposure in list(getattr(condition, "exposures", ()) or ()):
        value = str(getattr(exposure, "mechanism", "") or "").strip()
        if value:
            out.append(value)
    return out


def _report_boxes(markets, feedstocks, behind, outlooks, period, prose, condition=None,
                  wording=None, narrative=None) -> dict:
    """The three statements on the first page, composed from what was computed.

    Stefan, 9 October 2026, holding Charlie's version beside ours: a headline, where it is
    heading, and what it means for a buyer, before any prose. None of it is new information - the
    headline is the lead market's own figures, the heading is its outlook, and the buyer line is
    the wording confirmed when the developments were imported.
    """
    boxes: dict[str, str] = {}
    lead = next((row for row in markets if row.change_percent is not None), None)
    if lead is not None:
        direction = "higher" if (lead.change_percent or 0) > 0 else "lower"
        boxes["headline"] = (
            f"{display_chemical(lead.chemical)} in {lead.geography} closed at "
            f"{prose.money(lead.closed, lead.currency)}, "
            f"{abs(lead.change_percent):.1f} per cent {direction} {lead.scope_label}.")
        ahead = prose.outlook_paragraph(outlooks.get(f"{lead.chemical}|{lead.geography}"), period)
        if ahead:
            boxes["direction"] = ahead
    # The opening paragraph is composed from the quarter's own intelligence, so the condition and
    # the developments travel with it. Stefan, 10 October 2026.
    boxes["condition"] = condition
    boxes["behind"] = behind
    boxes["wording"] = dict(wording or {})
    boxes["narrative"] = narrative
    return boxes


def _forecast_line(history, outlook):
    """The forecast as a line off the last reading, and the range around it as a wedge.

    The forecast is one point at one horizon, so the line runs from where the market actually
    closed to where the method puts it, and the band opens from that same close. Drawing it from
    anywhere else would invent a path nobody measured.
    """
    if outlook is None or not getattr(outlook, "stands_up", False) or not history:
        return [], []
    last_day, last_value = history[-1]
    lands = last_day + timedelta(weeks=int(getattr(outlook, "horizon_weeks", 4) or 4))
    line = [(last_day, last_value), (lands, float(outlook.value))]
    if not getattr(outlook, "has_band", False):
        return line, []
    return (line,
            [(last_day, last_value, last_value), (lands, float(outlook.low), float(outlook.high))])


def _series_identities(identities, readings, *, chemical, geography, terms, packaging):
    """The identities whose drawn readings make up one series (chemical, geography, terms, packaging)."""
    wanted = {str(row.get("identity_key") or ""): row for row in identities
              if str(row.get("product_family") or "").strip() == chemical
              and str(row.get("market_geography") or "").strip() == geography}
    stated = market_lines_module._stated(packaging)
    out: dict[str, dict] = {}
    for reading in readings:
        key = str(reading.get("identity_key") or "")
        identity = wanted.get(key)
        if identity is None or key in out:
            continue
        source_id = str(reading.get("source_id") or "")
        if basis_for(identity, source_id).upper() != str(terms).upper():
            continue
        if market_lines_module._stated(identity.get("packaging")) != stated:
            continue
        out[key] = identity
    return list(out.values())


def _report_outlook(identities, readings, references, *, chemical, geography, currency, facts,
                    horizon_weeks=4, terms=None, packaging=None, label=None):
    """Where this market is expected next, from the same forecast the Forecast page shows.

    The report and the screen must not be able to disagree, so this reads the engine's forecast for
    the identity the application would land on for this market, takes the model the page takes, and
    the band the page takes - the width measured off the errors that model actually made at this
    horizon. A market whose method has not been tested carries no outlook; the document says "where
    an outlook is given" and means it.
    """
    if terms is None:
        wanted = [row for row in identities
                  if str(row.get("product_family") or "").strip() == chemical
                  and str(row.get("market_geography") or "").strip() == geography]
    else:
        # The forecast of this series and no other: an export price is not forecast from an import
        # price (Stefan's adipic acid report, 14 October 2026).
        wanted = _series_identities(identities, readings, chemical=chemical, geography=geography,
                                    terms=terms, packaging=packaging)
    named = label or geography
    chosen = preferred_identity(wanted, readings, references) if wanted else None
    if chosen is None:
        return None
    payload = _load_forecast(str(chosen.get("identity_key") or ""), (horizon_weeks,))
    if not payload:
        return None
    model = next((one for one in (payload.get("models") or ())
                  if one.get("model") == _card_model(payload) and one.get("status") == "ok"), None)
    point = next((one for one in ((model or {}).get("points") or ())
                  if int(one.get("horizon_weeks") or 0) == horizon_weeks), None)
    if point is None:
        return None
    band = forecast_cards.band_for(point, "80")
    trials = forecast_cards.trials_for(point)

    # The forecast is served in the currency the market is assessed in. The report states one
    # currency, so it is converted here at the rate for the day the forecast was issued.
    table = _load_fx_table()
    source = str(payload.get("currency") or "")
    stamp = as_of_date(payload.get("as_of"))

    def to_report(value: float) -> float:
        if not source or source == currency:
            return float(value)
        converted = table.convert(float(value), source, currency, stamp)
        return converted[0] if converted else float(value)

    middle = float(point.get("value") or 0.0)
    if band is None or not trials:
        # The method has not been tested at this horizon for this market, so there is no measured
        # width. The forecast is still the forecast; what is missing is said rather than invented.
        return facts.Outlook(chemical=chemical, geography=named, currency=currency,
                             horizon_weeks=horizon_weeks, value=to_report(middle),
                             coverage="80", trials=None)
    low_share, high_share = band
    return facts.Outlook(
        chemical=chemical, geography=named, currency=currency, horizon_weeks=horizon_weeks,
        value=to_report(middle), low=to_report(middle * (1.0 + low_share)),
        high=to_report(middle * (1.0 + high_share)), coverage="80", trials=trials)


def _periods_that_hold_a_reading(identities, readings, *, chemicals, geographies, kind, today):
    """The periods a report can actually be written for, out of the twelve a calendar offers.

    Stefan, 8 October 2026: "If there is nothing to show, then why can I select it?? ... You only
    offer when there is something to report on." Q3 2026 was offered because the calendar has a Q3,
    not because anything was in it.
    """
    wanted = {str(row.get("identity_key") or "") for row in identities
              if str(row.get("product_family") or "").strip() in set(chemicals)
              and str(row.get("market_geography") or "").strip() in set(geographies)}
    offered = _report_periods(kind, today)
    if not wanted:
        return []
    held = set()
    for row in readings:
        if str(row.get("identity_key") or "") not in wanted:
            continue
        day = str(row.get("observation_date") or "")[:10]
        for one in offered:
            if one.holds(day):
                held.add(one.name)
                break
        if len(held) == len(offered):
            break
    return [one for one in offered if one.name in held]


def _report_series_offered(identities, readings, sources=None):
    """Every series a customer page offers: the report carries nothing the pages would not show.

    Stefan, 14 October 2026, on the adipic acid Q3 report: it printed the regional assessments he
    had set to No in the Data & Sources table. The report reads the same three sets the pages read,
    with his Yes and No already applied.
    """
    return (customer_catalogue.offered(identities, readings, customer_catalogue.CURRENT,
                                      sources=sources)
            | customer_catalogue.offered(identities, readings, customer_catalogue.HISTORIC,
                                         sources=sources)
            | customer_catalogue.offered(identities, readings, customer_catalogue.FORECAST,
                                         forecast_from_year=FORECAST_FROM_YEAR))


def _report_periods_for_series(identities, readings, *, series, kind, today, facts):
    """The periods in which at least one of these series has enough readings to be reported.

    Enough is `facts.READINGS_FOR_A_PERIOD` (two) in that one series. Newest first, as offered.
    """
    offered = _report_periods(kind, today)
    held = set()
    for chemical, geography, terms, packaging in series:
        counted: dict[str, int] = {}
        for day, _value in facts._drawn_points(identities, readings, chemical=chemical,
                                               geography=geography, terms=terms,
                                               packaging=packaging):
            for one in offered:
                if one.holds(day):
                    counted[one.name] = counted.get(one.name, 0) + 1
                    break
        held |= {name for name, count in counted.items()
                 if count >= facts.READINGS_FOR_A_PERIOD}
    return [one for one in offered if one.name in held]


def _report_feedstock_series(series, *, chemicals, feedstocks, geographies):
    """The offered series of each feedstock: in the chosen places, or wherever it is offered."""
    out = []
    for feedstock in feedstocks:
        if feedstock in set(chemicals):
            continue
        mine = sorted(one for one in series if one[0] == feedstock)
        here = [one for one in mine if one[1] in set(geographies)]
        out.append((feedstock, here, [one for one in mine if one not in here]))
    return out


def _feedstocks_with_a_price(all_identities, readings, *, chemicals, geographies, period, facts,
                             series=None):
    """The feedstocks under those chemicals that carry a price in this period, and no others.

    A feedstock that is itself one of the chosen chemicals is already a market in this report, and
    a report that prices propylene oxide in its own section and again underneath the polyols is
    saying the same thing twice.
    """
    out = []
    if series is not None:
        names = sorted({name for chemical in chemicals for name, _note in feedstocks_of(chemical)})
        for feedstock, here, elsewhere in _report_feedstock_series(
                series, chemicals=chemicals, feedstocks=names, geographies=geographies):
            if any(facts.market_period(all_identities, readings, chemical=c, geography=g,
                                       period=period, terms=t, packaging=p).readings
                   >= facts.READINGS_FOR_A_PERIOD
                   for c, g, t, p in here + elsewhere):
                out.append(feedstock)
        return out
    for feedstock in sorted({name for chemical in chemicals
                             for name, _note in feedstocks_of(chemical)} - set(chemicals)):
        for geography in geographies:
            row = facts.market_period(all_identities, readings, chemical=feedstock,
                                      geography=geography, period=period)
            if row.readings:
                out.append(feedstock)
                break
    return out


def _report_feedstock_rows(all_identities, observations, *, chemicals, geographies, period, facts,
                           currency="USD", series=None):
    """One row per selected feedstock, in the region it is held where the selected ones hold none.

    A European report whose toluene is Chinese has to say so rather than let a reader take it for
    European toluene; market_paragraph names the region on every line, so the fallback is visible on
    the page itself.
    """
    rows = []
    if series is not None:
        # One row per offered series of the feedstock that priced in the period, in the chosen
        # places where it has any, and otherwise in the place it is offered.
        for feedstock, here, elsewhere in _report_feedstock_series(
                series, chemicals=(), feedstocks=chemicals, geographies=geographies):
            found = facts.report_series(all_identities, observations, series=here,
                                        period=period, currency=currency)
            if not found:
                found = facts.report_series(all_identities, observations, series=elsewhere,
                                            period=period, currency=currency)
            rows += found
        return rows
    for feedstock in chemicals:
        where = list(geographies)
        found = []
        for geography in where:
            row = facts.market_period(all_identities, observations, chemical=feedstock,
                                      geography=geography, period=period, currency=currency)
            if not (row.quiet and not row.last_day):
                found.append(row)
        if not found:
            elsewhere = sorted({str(row.get("market_geography") or "").strip()
                                for row in all_identities
                                if str(row.get("product_family") or "").strip().upper() == feedstock})
            for geography in elsewhere:
                row = facts.market_period(all_identities, observations, chemical=feedstock,
                                          geography=geography, period=period, currency=currency)
                if not (row.quiet and not row.last_day):
                    found = [row]
                    break
        rows += found
    return rows


def _report_upstream_paragraphs(rows, notes, period, prose) -> list:
    """Each feedstock: why it is in the report, then what it did. Said once per material."""
    out, said = [], set()
    for row in rows:
        if row.chemical not in said:
            said.add(row.chemical)
            sentence = prose.upstream_sentence(row.chemical, notes.get(row.chemical, ()))
            if sentence:
                out.append(sentence)
        out.append(prose.market_paragraph(row, period))
    return out


def render_period_report() -> None:
    """Choose the markets, the period and the feedstocks; read the report; then take the document."""
    from raw_material_forecasting import intelligence_register as recorded
    from raw_material_forecasting import market_background
    from raw_material_forecasting import period_narrative as prose
    from raw_material_forecasting import period_report as facts
    from raw_material_forecasting import period_report_docx as document
    from raw_material_forecasting import report_gates as gates
    from raw_material_forecasting import report_standard
    from raw_material_forecasting import report_archive
    from raw_material_forecasting import supply_conditions
    from raw_material_forecasting.market_intelligence import DATA_DIR

    st.subheader("Customer report")
    st.caption("What the selected markets did over one month or one quarter, written as a report "
               "for a customer to read.")

    snapshot = _load_step6_snapshot()
    if snapshot is None:
        st.info("Market data is being collected.")
        return
    # One currency through the whole document, the one the selector names. The snapshot arrives
    # converted into it, each reading at the rate for its own day. Before this the report printed
    # whatever the conversion produced and wrote USD in front of it, so a report run with the
    # selector on EUR carried euro figures labelled as dollars.
    currency = display_currency()
    # A reading the FX table could not convert is still in its publisher's currency. One of those
    # in a document that states a single currency is a wrong number, so it is left out and the
    # person is told how many.
    readings = [row for row in snapshot.observations if not row.get("fx_unconverted")]
    all_identities = [dict(row) for row in snapshot.identities]
    identities = [row for row in all_identities if is_published_market(row)]
    drawable = _report_series_offered(identities, readings, snapshot.sources)
    if not drawable:
        st.info("No market draws a price yet, so there is nothing to report on.")
        return

    chemical_options = _alphabetical(sorted({chem for chem, _g, _b, _p in drawable}),
                                     display_chemical)
    chosen_chemicals = st.multiselect(
        "Chemicals", chemical_options, default=[], format_func=display_chemical,
        key="report_chemicals")
    if not chosen_chemicals:
        return

    geography_options = _alphabetical(sorted({geo for chem, geo, _b, _p in drawable
                                              if chem in chosen_chemicals}))
    chosen_geographies = st.multiselect(
        "Geography", geography_options, default=[], key="report_geographies")
    if not chosen_geographies:
        return

    kind_label = st.radio("Period", ["Month", "Quarter"], horizontal=True, key="report_period_kind")
    kind = "month" if kind_label == "Month" else "quarter"
    # Only the periods that hold a reading for what is selected above. A calendar has a Q3 whether
    # or not anything was priced in it; this list does not.
    chosen_series = sorted(one for one in drawable
                           if one[0] in chosen_chemicals and one[1] in chosen_geographies)
    periods = _report_periods_for_series(identities, readings, series=chosen_series, kind=kind,
                                         today=date.today(), facts=facts)
    if not periods:
        st.info("No price was recorded for this selection in the last "
                f"{REPORT_PERIODS_OFFERED} {'months' if kind == 'month' else 'quarters'}.")
        return
    # A calendar is a scale, like the forecast horizon: September 2026 belongs above August 2026 and
    # nowhere near A. Newest first, because that is the report being asked for.
    period = st.selectbox("Which period", periods, format_func=lambda item: item.name,
                          key="report_period")

    feedstock_options = _alphabetical(
        _feedstocks_with_a_price(identities, readings, chemicals=chosen_chemicals,
                                 geographies=chosen_geographies, period=period, facts=facts,
                                 series=drawable),
        display_chemical)
    # Stefan, 8 October 2026: "Feedstock (pre-selected based on A)." A selector keeps its own value
    # once it has been drawn, so an opening value is applied exactly once: add TDI afterwards and
    # toluene stayed unticked, take pMDI out and benzene fell away leaving nothing ticked at all.
    # The ticks follow the chemicals instead - whenever that selection changes, the feedstocks of
    # the new set are ticked before this selector is drawn.
    follows = (tuple(chosen_chemicals), tuple(chosen_geographies), period.name)
    if st.session_state.get("report_feedstocks_follow") != follows:
        st.session_state["report_feedstocks_follow"] = follows
        st.session_state["report_feedstocks"] = list(feedstock_options)
    chosen_feedstocks = st.multiselect(
        "Feedstocks", feedstock_options, format_func=display_chemical,
        key="report_feedstocks",
        help="The materials upstream of the chemicals chosen above. Nobody reading the report buys "
             "them; their direction is the pressure under the prices that do matter.")
    # Why each one is in the report, and how far under the market it sits. The store knows that
    # benzene reaches pMDI via aniline; the report says so rather than calling it one step up.
    feedstock_notes: dict[str, list[tuple[str, str]]] = {}
    for chemical in chosen_chemicals:
        for feedstock, note in feedstocks_of(chemical):
            if feedstock in chosen_feedstocks:
                feedstock_notes.setdefault(feedstock, []).append((chemical, note))

    # What this report would be. A preview left on the screen after a selector moved is a report of
    # something else, and the document behind it is the one that would be sent.
    signature = (tuple(chosen_chemicals), tuple(chosen_geographies), period.name,
                 tuple(chosen_feedstocks), currency)

    # What the report will contain, said before it is asked for rather than after.
    # One section per series with at least two readings in the period: a chemical in a place on
    # one set of terms and one packaging. Anything thinner is not written up, so it is not counted.
    sections = [one for one in chosen_series
                if sum(1 for day, _value in facts._drawn_points(
                    identities, readings, chemical=one[0], geography=one[1], terms=one[2],
                    packaging=one[3]) if period.holds(day)) >= facts.READINGS_FOR_A_PERIOD]
    st.caption(f"{populations.MARKETS.count(len(sections))} and "
               f"{len(chosen_feedstocks)} feedstock"
               f"{'' if len(chosen_feedstocks) == 1 else 's'} will be written up for "
               f"{period.name}.")

    if st.button("Prepare the report", type="primary", key="report_prepare"):
        markets = facts.report_series(identities, readings, series=chosen_series, period=period,
                                      currency=currency)
        if not markets:
            st.warning("None of the selected markets has two readings in this period.")
            return
        # Where each market is expected next. The monitor is issued every quarter, and the
        # edition that closes one quarter is read by someone buying in the next, so every
        # edition carries the forecast. Stefan, 10 October 2026.
        outlooks, not_forecast = {}, {}
        for row in markets:
            found = _report_outlook(identities, readings, snapshot.references,
                                    chemical=row.chemical, geography=row.where,
                                    currency=currency, facts=facts, terms=row.terms,
                                    packaging=row.packaging, label=row.geography)
            if found is None:
                continue
            # A forecast that lands before the report is written is a month that has already
            # passed. Customs prices arrive two or three months late, so their four-week forecast
            # from July landed in August. The report then says where the series last priced instead.
            # Stefan's adipic acid report, 14 October 2026.
            last_seen = str(row.closed_on or "")[:10]
            lands = (date.fromisoformat(last_seen) + timedelta(weeks=int(found.horizon_weeks or 4))
                     if last_seen else None)
            if lands is not None and lands < date.today():
                not_forecast[f"{row.chemical}|{row.geography}"] = {
                    "latest_observation": last_seen,
                    "latest_month": f"{date.fromisoformat(last_seen):%B %Y}",
                    "why": ("the latest reading of this market is older than the forecast "
                            "horizon, so a forecast from it would land before this report"),
                }
                continue
            outlooks[f"{row.chemical}|{row.geography}"] = found
        feedstock_rows = _report_feedstock_rows(
            identities, readings, chemicals=chosen_feedstocks,
            geographies=chosen_geographies, period=period, facts=facts, currency=currency,
            series=drawable)
        charts = {}
        # The chart is drawn from the same markets the figures were read from. Stefan,
        # 11 October 2026: the polyether polyol chart closed at USD 1,725 and peaked at USD 1,947
        # while the text said 1,717 and 1,909. The figures came from the published identities and
        # the chart came from every identity in the store, and polyether polyol in China has a
        # second EXW identity, not published, carrying twelve readings in the quarter. Two
        # pictures of one market is one too many.
        #
        # A market is published and a feedstock need not be - toluene in China has no published
        # identity at all - so each chart takes the list its own row was built from.
        series = {}
        for row, from_these in ([(one, identities) for one in markets]
                                + [(one, identities) for one in feedstock_rows]):
            inside = facts.market_history(from_these, readings, chemical=row.chemical,
                                          geography=row.where, period=period, periods_back=1,
                                          terms=row.terms or None,
                                          packaging=row.packaging if row.terms else None)
            # One reading draws a dot, not a line: a full-width chart of a single point told the
            # reader nothing the sentence below it does not (adipic acid Q3, 14 October 2026).
            if len(inside) < 2:
                continue
            key = f"{row.chemical}|{row.geography}"
            series[key] = inside
            ahead, wedge = _forecast_line(inside, outlooks.get(key))
            series[f"forecast|{key}"], series[f"band|{key}"] = ahead, wedge
            # One chart to a row, the full width of the page. Stefan, 12 October 2026: the
            # reference layout set them side by side and "in reality this is not very good
            # idea". No title inside the picture: the panel above it names the market.
            charts[key] = document.chart_for(
                inside, row.currency, period=period,
                market_name=display_chemical(row.chemical), forecast=ahead, band=wedge,
                across=1)
        # What was behind the prices: the developments HTC Global recorded in the period, and the
        # state of the chain itself where it was graded inside the period. Said once per chemical -
        # most of these touch every region a material is priced in.
        held = recorded.load(DATA_DIR)
        behind: dict[str, list] = {}
        for chemical in dict.fromkeys(row.chemical for row in markets):
            seen, rows = set(), []
            for geography in chosen_geographies:
                for entry in recorded.for_market(held, chemical=chemical, geography=geography,
                                                 first_day=period.first_day,
                                                 last_day=period.last_day):
                    if entry.key not in seen:
                        seen.add(entry.key)
                        rows.append(entry)
            if rows:
                behind[chemical] = sorted(rows, key=lambda item: item.reported, reverse=True)
        condition = supply_conditions.in_period(supply_conditions.load(DATA_DIR),
                                                period.first_day, period.last_day)
        # How each material is made, from the route graph the engine's technical model was seeded
        # from. One chain per chemical; a chemical the graph does not hold gets no chain rather
        # than a guess at one.
        graph = load_chemical_route_graph()
        routes = {}
        for chemical in dict.fromkeys(row.chemical for row in markets):
            steps = facts.route_chain(graph, chemical, feedstocks_of(chemical))
            if len(steps) > 1:
                routes[chemical] = steps
                here = ({row.chemical for row in markets}
                        | {row.chemical for row in feedstock_rows})
                # No title inside the picture. The section band above it already says "How
                # Polyether Polyol is made", and printing it again in the image gave every chain
                # two headings, one styled and one not.
                drawn = route_diagram.report_picture(graph, chemical, priced_here=here)
                charts[f"route|{chemical}"] = drawn or page_chart.route_png(
                    [(name, code in here) for code, name, _tracked in steps])
        # The market behind the price: the plant roster, the pipeline and the dated plant events
        # HTC Global researched market by market. Held for weeks, never read by this report.
        grounds = {}
        for row in markets:
            # The market behind the price belongs to the place, however many series it holds.
            if f"{row.chemical}|{row.where}" in grounds:
                continue
            ground = market_background.load(row.chemical, row.where, period=period)
            if ground.has_anything:
                grounds[f"{row.chemical}|{row.where}"] = ground

        from raw_material_forecasting import development_writer as writer

        entries = [entry for items in behind.values() for entry in items]
        wording, left_alone, trouble = {}, [], ""
        try:
            for key, written in writer.write_all(entries).items():
                if written.sentence:
                    wording[key] = written.sentence
                else:
                    left_alone.append((written.headline, "; ".join(written.why)))
        except writer.WriterUnavailable as problem:
            trouble = str(problem)
        if trouble:
            st.warning(f"The Observatory could not be reached, so every development shows the "
                       f"line the register holds. {trouble}")
        elif left_alone:
            st.warning(f"{len(left_alone)} of {len(entries)} developments kept the register's own "
                       "line, because the sentence that came back could not be printed.")
            for headline, why in left_alone:
                st.write(f"{headline} - {why or 'no reason recorded'}")

        from raw_material_forecasting import report_writer

        pack = facts.fact_pack(markets, feedstock_rows, period=period, currency=currency)
        # What the prices themselves say about the chain. The route graph asserts the chemistry;
        # this measures whether the feedstock's price actually carries into the material's, by how
        # much, and whether it arrives early enough to be worth watching.
        #
        # Stefan, 11 October 2026: "I am also not prepared to let the chemical relationship go."
        # The measurement has existed for a year and nothing in the report ever asked for it.
        #
        # Read off the engine's own observations rather than the display-converted snapshot,
        # because correlation converts each price at the rate of its own date and a single-rate
        # conversion has already flattened that. A failure here costs the paragraph, never the
        # report.
        try:
            from raw_material_forecasting import correlation as study
            from raw_material_forecasting import measured_relationships as chain

            graph_for_study = load_chemical_route_graph()
            own = MarketDataEngineClient(MARKET_DATA_ENGINE_URL, timeout=6.0).snapshot()
            lines = study.build_series(own.observations, _load_fx_table(), graph_for_study,
                                       frequency=study.MONTHLY)
            wanted = [(row.chemical, row.where, under.chemical)
                      for row in markets for under in feedstock_rows
                      if under.where == row.where
                      and row.chemical in [name for name, _note
                                           in feedstock_notes.get(under.chemical, ())]]
            measured = chain.for_the_edition(lines, wanted, correlation=study)
            if measured:
                pack["relationships"] = measured
        except Exception as problem:            # noqa: BLE001 - the report comes first
            st.caption(f"The measured chain relationships could not be read: "
                       f"{str(problem)[:120]}")
        def _shown(value, money):
            return "-" if value is None else units.with_unit(f"{money} {round(float(value)):,.0f}")

        def _forecast_block(one):
            held = {"value": round(float(one.value)),
                    "value_printed": _shown(one.value, one.currency),
                    "horizon_weeks": one.horizon_weeks,
                    "range_measured": bool(one.has_band)}
            if one.has_band:
                held.update({"low": round(float(one.low)), "high": round(float(one.high)),
                             "low_printed": _shown(one.low, one.currency),
                             "high_printed": _shown(one.high, one.currency),
                             "coverage": one.coverage})
            return held

        pack["forecast"] = {key: _forecast_block(one)
                            for key, one in outlooks.items() if one.stands_up}
        if not_forecast:
            pack["forecast_not_given"] = not_forecast
        pack["capacity"] = {key: {"plants": len(ground.plants),
                                  "capacity_low_kt": round(ground.capacity_low_kt),
                                  "capacity_kt": round(ground.capacity_kt),
                                  "capacity_printed": (
                                      f"{ground.capacity_low_kt:,.0f} to "
                                      f"{ground.capacity_kt:,.0f} kt a year"
                                      if ground.capacity_is_a_range
                                      else f"{ground.capacity_kt:,.0f} kt a year"),
                                  "owners": [(name, round(kt))
                                             for name, kt in ground.owners[:3]],
                                  "top_share": round(ground.top_share(3)),
                                  "coming": [(one.site, one.owner, one.kt, one.due)
                                             for one in ground.coming]}
                            for key, ground in grounds.items()}
        told = {
            "period": {"name": period.name, "from": period.first_day.isoformat(),
                       "to": period.last_day.isoformat()},
            "chain_condition": ({"event": getattr(condition, "event", ""),
                                 "state": getattr(condition, "state", ""),
                                 "exposures": [{"chemicals": one.chemicals,
                                                "mechanism": one.mechanism}
                                               for one in (getattr(condition, "exposures", ())
                                                           or ())]}
                                if condition is not None else {}),
            "recorded": [{"date": entry.event_date or entry.reported,
                          "headline": entry.headline,
                          "context": entry.context,
                          "written": wording.get(writer.key_for(entry), "")}
                         for items in behind.values() for entry in items],
        }
        narrative, narrative_trouble = None, ""
        try:
            narrative = report_writer.write(
                facts=pack, intelligence=told,
                labels=gates.labels_of(markets, feedstock_rows), period_name=period.name)
        except writer.WriterUnavailable as problem:
            narrative_trouble = str(problem)
        if narrative_trouble:
            st.warning("The narrative was written by the application, because the Observatory "
                       f"could not be reached. {narrative_trouble}")
        elif narrative is not None and not narrative.written:
            st.warning("The Observatory returned no narrative, so the application wrote one. "
                       "Nothing it did return was discarded.")

        # The back matter. What the prices say about the chain is measured, not asserted, so it
        # carries the overlap it was measured on; coverage and currency are the two things a
        # reader needs before weighing any figure in front of them.
        # Stefan took the back matter out of the template on 12 October 2026. The coverage and
        # the currency are said where the figures are, in every paragraph and on every axis. The
        # measured chain relationships are held here and no longer printed; the report does not
        # explain itself to a customer.
        # The photographs, from the approved register. An edition with no approved picture for
        # a slot simply has no picture there.
        from raw_material_forecasting import report_images

        photos = report_images.photographs(period.name)
        made = document.build(markets, period=period, charts=charts, wording=wording,
                              feedstocks=feedstock_rows, feedstock_notes=feedstock_notes,
                              behind=behind, condition=condition, currency=currency,
                              outlooks=outlooks, routes=routes, backgrounds=grounds,
                              photos=photos, not_forecast=not_forecast,
                              boxes=_report_boxes(markets, feedstock_rows, behind, outlooks,
                                                  period, prose, condition, wording, narrative))
        # Every edition goes through the gates before it is offered for download. Stefan,
        # 10 October 2026: fix the way the report is made. The figure count is not run here yet,
        # and the record says so rather than claiming a check that nobody performed.
        headline = str(getattr(narrative, "headline", "") or "")
        checks = gates.run(made, labels=gates.labels_of(markets, feedstock_rows),
                           exempt=tuple(document.STANDING_NOTICES)
                                  + ((headline,) if headline else ()),
                           carried=_carried_text(behind, condition), period_name=period.name,
                           facts=pack, check_figures=False)
        # The chart against the text, market by market, before either is shown. A disagreement
        # names the market, the field and both values, and holds the edition out of the reference
        # base until it is settled.
        disagreements = [str(one) for one in gates.check_chart_agreement(
            series, pack, list(markets) + list(feedstock_rows))]
        checks = list(checks) + disagreements
        # The release check, read off the finished document rather than off what went into it.
        # Stefan, 12 October 2026: one report, in Word, which he approves and saves as PDF for
        # the customer. So every figure printed in the file a customer will receive is traced
        # back to the fact pack here, which is the control V2.1 section 10 asks for.
        release_checks = []
        try:
            from raw_material_forecasting.publication import qa as release

            release_checks = release.check_lines(
                gates.document_text(made), facts=pack,
                supplied=sorted(report_writer.supplied_figures(pack, told)),
                labels=gates.labels_of(markets, feedstock_rows), period_name=period.name,
                exempt=(document.COPYRIGHT, headline))
        except Exception as problem:            # noqa: BLE001 - the report still stands
            st.warning(f"The release check could not be completed: {str(problem)[:200]}")

        st.session_state["report_document"] = {
            "name": document.file_name(period),
            "bytes": made,
            "release_checks": [str(one) for one in release_checks],
            "checks": [str(one) for one in checks],
            "on_the_figures": (disagreements
                               + list(getattr(narrative, "factual", []) or [])),
            "narrative_by": ("the Observatory" if getattr(narrative, "written", False)
                             else "the application"),
            "standard": report_standard.VERSION,
            # What goes to the reference base: the words as a customer reads them, and the facts
            # they were built from, so a later edition can check a continuity claim against
            # figures instead of against prose.
            "edition_text": "\n".join(gates.document_text(made)),
            "facts_pack": facts.fact_pack(markets, feedstock_rows, period=period,
                                          currency=currency),
            "markets_light": [{"chemical": row.chemical, "geography": row.geography,
                               "place": row.where, "terms": row.terms,
                               "packaging": row.packaging}
                              for row in markets],
            "period_to": period.last_day.isoformat(),
            "currency": currency,
            "summary": prose.executive_summary(
                markets, facts.headline(markets), period, condition,
                sum(len(rows) for rows in behind.values())),
            "paragraphs": [prose.market_paragraph(row, period) for row in markets],
            "spreads": [prose.spread_paragraph(gap, period) for gap in facts.spreads(markets)],
            "outlook": [prose.outlook_paragraph(one, period) for one in outlooks.values()],
            "behind": {
                display_chemical(chemical): (
                    [prose.condition_paragraph(condition, chemical, period)]
                    + [prose.behind_paragraph(entry, period) for entry in rows]
                    + [prose.implication_paragraph(rows, period)])
                for chemical, rows in behind.items()},
            "underneath": _report_upstream_paragraphs(feedstock_rows, feedstock_notes, period,
                                                      prose),
            "under_name": ", ".join(dict.fromkeys(
                f"{display_chemical(row.chemical)} in {row.geography}"
                for row in feedstock_rows)),
            "glance": [
                {"Material": display_chemical(row.chemical), "Market": row.geography,
                 "Opened": prose.money(row.opened, row.currency) if row.opened else "-",
                 "Closed": prose.money(row.closed, row.currency) if row.closed else "-",
                 "Change": (f"{row.change_percent:+.1f}%" if row.change_percent is not None
                            else "no new developments")}
                for row in markets
            ],
            "period": period.name,
            "signature": signature,
        }

    built = st.session_state.get("report_document")
    if not built:
        return
    if built.get("signature") != signature:
        st.session_state.pop("report_document", None)
        return

    st.divider()
    st.markdown(f"### {built['period']}")
    st.write(built["summary"])
    st.dataframe(pd.DataFrame(built["glance"]), hide_index=True, width="stretch")
    for paragraph in built["paragraphs"]:
        st.write(paragraph)
    for paragraph in built["spreads"]:
        st.write(paragraph)
    for chemical, paragraphs in (built.get("behind") or {}).items():
        st.markdown(f"**{chemical} in the period**")
        for paragraph in paragraphs:
            if paragraph:
                st.write(paragraph)
    if built["underneath"]:
        st.markdown(f"**{built.get('under_name') or 'The material underneath'}**")
        for paragraph in built["underneath"]:
            st.write(paragraph)
    for paragraph in (built.get("outlook") or []):
        st.write(paragraph)

    st.caption(f"Narrative written by {built.get('narrative_by', 'the application')}.")
    figures = built.get("on_the_figures") or []
    if figures:
        st.error("To settle before this goes out.")
        for one in figures:
            st.write(one)

    def _gist(one: str) -> str:
        return " ".join(str(one).split())[:70].lower()

    seen = {_gist(one) for one in figures}
    other = [one for one in (built.get("checks") or []) if _gist(one) not in seen]
    if other:
        with st.expander(f"On the document - {len(other)} to consider", expanded=False):
            for one in other:
                st.write(one)
    if not figures and not other:
        st.success("Nothing outstanding.")

    # One document. Stefan, 12 October 2026: he reads the Word, edits what he wants and saves
    # it as PDF for the customer, so what leaves this page is the file he edits.
    release_checks = built.get("release_checks") or []
    if release_checks:
        with st.expander(f"On the publication - {len(release_checks)} to settle", expanded=True):
            st.caption("Every figure in the finished document is traced back to the fact pack. "
                       "These did not trace, or did not read as they should.")
            for one in release_checks:
                st.write(one)
    st.download_button("Approve and download the report (Word)", data=built["bytes"],
                       file_name=built["name"], type="primary",
                       mime="application/vnd.openxmlformats-officedocument"
                            ".wordprocessingml.document",
                       key="report_download")

    _report_to_the_reference_base(built)


def _report_to_the_reference_base(built: dict) -> None:
    """Put an issued edition into the base of our own past editions.

    Stefan, 9 October 2026, wants every report we make kept so a later one can refer to what was
    said before. Two things hold here. An edition with an outstanding check never goes up, because
    an error uploaded once becomes retrievable history. And what goes up is the edition beside its
    fact pack, so a figure is always read from the facts.
    """
    from raw_material_forecasting import report_archive

    if not report_archive.configured():
        return
    st.markdown("**Reference base**")
    if built.get("checks"):
        st.caption("Settle the points above and this edition can go in.")
        return
    if st.button("Add this edition to the reference base", key="report_to_base"):
        try:
            receipt = report_archive.put(
                edition_text=built.get("edition_text", ""),
                facts=built.get("facts_pack", {}),
                markets=built.get("markets_light", []),
                period_name=built.get("period", ""),
                period_to=built.get("period_to", ""),
                currency=built.get("currency", "USD"),
                checks=built.get("checks") or [],
                standard=built.get("standard", ""))
        except (report_archive.ArchiveUnavailable, report_archive.Refused) as problem:
            st.error(str(problem))
            return
        went = f"{len(receipt.uploaded)} file(s) in"
        replaced = (f", {len(receipt.superseded)} earlier version(s) marked"
                    if receipt.superseded else "")
        st.success(f"{went}{replaced}.")


# The five questions, in the order the engine asks them, for the heading on each row.
_WHY_WORDS = {
    "W1": "What exactly is unknown?",
    "W2": "Who would know, and who is obliged to say?",
    "W3": "What trace would it leave in what we already hold?",
    "W4": "What does it imply, and does the implication narrow it?",
    "W5": "Why might the answer not exist at all?",
}
# What each outcome looks like at a glance. A pending question has to read as pending: the whole
# point of the engine is that an unanswered question is visible rather than silent.
_OUTCOME_MARK = {"ANSWERED": "OK", "NARROWED": "~", "EXHAUSTED": "-", "UNANSWERABLE": "!",
                 "PENDING": "o"}
_STATE_WORDS = {
    "OPEN": "being worked",
    "NARROWED": "down to a stated few, and publishable as that",
    "RESOLVED": "named, with the trail of what said so",
    "EXPLAINED": "why the answer does not exist - a collection requirement, not a failure",
}
# What a reader picks, against the state it means. The stored codes are not offered: RESOLVED
# would sort under R and tell nobody what it is.
_SHOWING = {"Being worked": "OPEN", "Explained": "EXPLAINED", "Named": "RESOLVED",
            "Narrowed": "NARROWED"}
# The capabilities that need nothing but what we already hold, so the page can offer to run them.
_FREE = ("ROSTER", "OUR_PRICES", "OUR_EVENTS")


def render_investigation() -> None:
    """The cases, and the controls to work them.

    An observation the engine cannot explain becomes a case with a chain of questions against it
    rather than a line in a rejection file nobody reads (v0.5.42). This is where a person sees them.

    Stefan, 17 October 2026: "If it is unattributed you do not throw away the observation. You dig
    deeper... I dont know how you are going to build that in the application but that is VITAL."

    Three things here are deliberate. The observation is shown VERBATIM, in its own language,
    because reasoning over a generated English summary is how three days went on the wrong
    question. Every question is listed including the ones nobody has asked yet, so pending is
    visible rather than absent. And a case cannot be ended without saying what the answer or the
    reason is - the engine refuses it and so does this form.
    """
    st.subheader("Open questions")
    st.caption(
        "An observation we cannot explain is a case, not a reject. Each one names what is missing "
        "- the actor, the amount, the date, the place, the cause, the scope, or whether the market "
        "exists at all - and carries five kinds of question generated from that. A case ends "
        "named, narrowed to a stated few, or explained. There is no ending that says nothing."
    )
    client = MarketDataEngineClient(MARKET_DATA_ENGINE_URL, timeout=4.0)
    try:
        cases = client.investigation_cases()
    except MarketDataEngineUnavailable as problem:
        st.info(f"The engine is not answering, so the cases cannot be read: {problem}")
        return

    top, _spacer = st.columns([2.4, 5])
    with top:
        if st.button("Open cases from the store", width="stretch", key="investigation_open"):
            with st.spinner("Reading every event and every market"):
                try:
                    found = client.investigation_open()
                except MarketDataEngineUnavailable as problem:
                    st.error(str(problem))
                else:
                    st.success(
                        f"{found['from_events']} from events, {found['from_prices']} from price "
                        f"levels, {found['enquiries_run']} enquiries run. "
                        f"{found['assessments_misfiled']} events are a publisher's own price "
                        "bulletin or statistical roundup misfiled as a producer action - those are "
                        "a classification defect, not cases.")
                    st.rerun()

    if not cases:
        st.info("No cases yet. Open them from the store above.")
        return

    counted = Counter(case["state"] for case in cases)
    gaps = Counter(case["gap"] for case in cases)
    st.markdown(
        " / ".join(f"**{counted[state]}** {state.lower()}" for state in
                   ("OPEN", "NARROWED", "RESOLVED", "EXPLAINED") if counted[state])
        + "  -  " + ", ".join(f"{n} {gap.lower()}" for gap, n in gaps.most_common()))

    # Both dropdowns read A to Z through the application's own helper, with the everything option
    # first - the pattern every collection selector already uses. The state is offered under the
    # word for what it means rather than the stored code, so "Named" reads where RESOLVED would
    # have sorted under R and told a reader nothing.
    left, right = st.columns([2, 3])
    with left:
        shown = st.selectbox("Showing", ["Every case"] + _alphabetical(list(_SHOWING)),
                             key="investigation_state")
    with right:
        gap = st.selectbox("Gap", ["All"] + _alphabetical(list(gaps)), key="investigation_gap")
    state = _SHOWING.get(shown)
    wanted = [case for case in cases
              if (state is None or case["state"] == state)
              and (gap == "All" or case["gap"] == gap)]
    if not wanted:
        st.info("Nothing matches that.")
        return
    st.caption(f"{len(wanted)} of {len(cases)}")

    # A cap, because 44 cases each with eight questions is a very long page. The filter above is
    # how a longer list is reached, rather than scrolling.
    for case in wanted[:40]:
        _render_one_case(client, case)
    if len(wanted) > 40:
        st.caption(f"{len(wanted) - 40} more not shown; narrow the filter above.")


def _render_one_case(client, case: dict) -> None:
    where = " / ".join(part for part in (case.get("chemical"), case.get("geography")) if part)
    day = str(case.get("observed_at") or "")[:10]
    with st.expander(f"[{case['gap']}] {case['observed'][:70]}", expanded=False):
        st.caption(" - ".join(part for part in (
            where, day, case.get("source_id") or "",
            _STATE_WORDS.get(case["state"], case["state"]),
        ) if part))
        # Verbatim, in the publisher's own language. Not a translation and not a summary.
        st.markdown("**The observation, as the publisher wrote it**")
        st.code(case["observed"], language=None)
        if case.get("answer"):
            st.success(case["answer"])

        st.markdown("**The questions**")
        for enquiry in case.get("enquiries", []):
            st.markdown(
                f"`{_OUTCOME_MARK.get(enquiry['outcome'], ' ')}` **{enquiry['question']}** - "
                f"`{enquiry['capability']}` - {enquiry['asked']}")
            if enquiry.get("found"):
                st.caption(enquiry["found"])
            elif enquiry["outcome"] == "PENDING":
                st.caption("Not asked yet. Nothing has run against this one.")

        suspects = case.get("candidates") or []
        if suspects:
            st.markdown("**The suspects, from our own map**")
            for one in suspects:
                size = f"{one['capacity_kt']:,.0f} kt" if one.get("capacity_kt") else ""
                line = " - ".join(part for part in (one["name"], one.get("province") or "",
                                                    size, one["state"].lower()) if part)
                st.markdown(f"- {line}")
                if one.get("why"):
                    st.caption(one["why"])
                if one["state"] == "CANDIDATE":
                    with st.form(f"rule_out_{one['id']}", clear_on_submit=True):
                        why = st.text_input("Rule it out, saying what ruled it out",
                                            key=f"why_{one['id']}")
                        if st.form_submit_button("Rule out"):
                            if not why.strip():
                                st.warning("Say what ruled it out; a suspect is not dropped "
                                           "silently.")
                            else:
                                client.investigation_rule_out(one["id"], why=why.strip())
                                st.rerun()

        _render_case_controls(client, case)


def _render_case_controls(client, case: dict) -> None:
    """Work it, end it, or put it back to work. Ending needs an answer or a reason."""
    if case["state"] != "OPEN":
        if st.button("Put it back to work", key=f"reopen_{case['id']}"):
            client.investigation_reopen(case["id"])
            st.rerun()
        return

    pending = [e for e in case.get("enquiries", []) if e["outcome"] == "PENDING"]
    free = [e for e in pending if e["capability"] in _FREE]
    if free and st.button(f"Ask the {len(free)} question(s) we can answer ourselves",
                          key=f"work_{case['id']}"):
        client.investigation_work(case["id"])
        st.rerun()
    waiting = [e for e in pending if e["capability"] not in _FREE]
    if waiting:
        st.caption("Waiting on something outside the application: "
                   + ", ".join(sorted({e["capability"] for e in waiting}))
                   + ". Those are not wired yet.")

    with st.form(f"close_{case['id']}"):
        st.markdown("**End the case**")
        how = st.radio("How it ends", ["RESOLVED", "NARROWED", "EXPLAINED"], horizontal=True,
                       key=f"how_{case['id']}",
                       help="Named, down to a stated few, or why the answer does not exist.")
        answer = st.text_area("The answer, or the reason there is none",
                              key=f"answer_{case['id']}", height=90)
        if st.form_submit_button("End it"):
            if not answer.strip():
                st.warning("A case cannot be ended without saying what the answer or the reason "
                           "is.")
            else:
                client.investigation_close(case["id"], state=how, answer=answer.strip())
                st.rerun()


# ----------------------------------------------------------------------------------------------
# Forecast method governance (Adaptive Forecast Method Selection CR, WP5 Task 11, v0.5.66).
#
# The compact Method Fit under the forecast says which method made each horizon and why; the
# administrator page puts the automatic winner beside the active method, the override with a
# recommendation when the two differ, the alternatives, the history and the Challengers - so why a
# method is active can be traced without reading a log or a table.

def render_method_fit_compact(identity_key: str, horizons: list[int]) -> None:
    from raw_material_forecasting.method_fit_view import any_governed, user_rows, why_line

    client = MarketDataEngineClient(MARKET_DATA_ENGINE_URL, timeout=1.5)
    try:
        fit = client.model_fit(identity_key)
    except MarketDataEngineUnavailable:
        return
    if not fit:
        return
    with st.expander("How the forecast method is chosen", expanded=False):
        if not any_governed(fit):
            st.caption(why_line(fit, min(horizons or [4])))
            return
        rows = [row for row in user_rows(fit) if int(row["Horizon"].split()[0]) in set(horizons)]
        st.dataframe(rows, hide_index=True, width="stretch")
        for weeks in horizons:
            st.caption(f"{weeks} weeks: {why_line(fit, weeks)}")


def render_forecast_method_management() -> None:
    from raw_material_forecasting.market_data_engine_client import GovernanceRefused
    from raw_material_forecasting.method_fit_view import METHOD_NAMES, admin_rows, method_name, why_line

    st.markdown("## How forecast methods are chosen")
    st.caption("Which method makes each market's forecast at each horizon, why, and what an administrator can "
               "change. Every change needs your name and a reason, and is kept.")
    client = MarketDataEngineClient(MARKET_DATA_ENGINE_URL, timeout=1.5)
    try:
        status = client.review_status()
        settings = client.governance_settings()
        methods = client.forecast_methods()
    except MarketDataEngineUnavailable:
        st.warning("The Step 6 market-data engine is not reachable. Start the application with Run-MVP.bat.")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Automatic selection", "ON" if settings.get("automatic_selection_enabled") else "OFF")
    c2.metric("Model Fit Review", str(status.get("state") or "—").replace("_", " ").title())
    # Read from the fields the engine actually answers with. These read `last_successful_at`,
    # `due_at` and `overdue_days`, none of which `review_state` has returned since the quarterly
    # cadence was abolished on 29 October 2026 - so "Last full review" showed a dash while the
    # engine knew the review had finished that morning, and "Next review due" showed a dash for a
    # thing that no longer exists. `test_the_admin_tiles_read_what_the_engine_answers` holds the
    # two sides together now.
    c3.metric("Last full review", str(status.get("last_at") or "—")[:10])
    # What the last review covered, in the engine's own words - "one market" or "every market".
    # A quarterly due date has no meaning now that a method is re-checked when a reading arrives
    # that its choice has not seen, and the scope is the thing an administrator asks next.
    c4.metric("Last review covered", str(status.get("last_scope") or "—").title())
    if not settings.get("automatic_selection_enabled"):
        st.info("Automatic selection is OFF: reviews record their rankings and proposals, but no market's "
                "forecast method is changed by them. It is switched on after the application-native validation.")

    snapshot = _load_step6_snapshot()
    identities = [dict(row) for row in getattr(snapshot, "identities", [])] if snapshot else []
    if not identities:
        st.info("No market is held yet.")
        return

    def _base(row: dict) -> str:
        return " · ".join(part for part in (display_chemical(row.get("product_family") or ""),
                                             str(row.get("market_geography") or ""),
                                             _display_market_dimension(row.get("commercial_basis") or "")) if part)

    # Two series can share chemical, region and delivery term and still be different series: another
    # price type, grade, packaging or currency (Stefan, 20 October 2026, saw "doubles"). Where the short
    # name is shared, the label says what tells them apart.
    shared = {}
    for row in identities:
        shared[_base(row)] = shared.get(_base(row), 0) + 1

    def _label(row: dict) -> str:
        base = _base(row)
        if shared.get(base, 0) < 2:
            return base
        extra = []
        grade = str(row.get("product_grade_or_chemistry") or "")
        if grade and grade.upper() not in ("UNKNOWN", "NONE"):
            extra.append(grade)
        kind = str(row.get("observation_type") or "").replace("_", " ")
        if kind:
            extra.append(kind)
        packaging = str(row.get("packaging") or "")
        if packaging and packaging.upper() not in ("UNKNOWN", "NONE"):
            extra.append(packaging)
        if row.get("currency"):
            extra.append(str(row.get("currency")))
        return f"{base} ({', '.join(extra)})" if extra else base

    identities = _alphabetical(identities, _label)
    # The standard chooser (`records.clicked_record`): the markets in a table, and the one clicked
    # opens. Stefan, 24 November 2026: "under Edit/Delete we see a clickable table, once clicked
    # the record opens to be edited (not before). Throughout the whole application use this." It
    # was a dropdown that always had a market open, so the page opened on overrides for a market
    # nobody had chosen.
    from raw_material_forecasting.platform_admin.records import clicked_record

    by_key = {str(row.get("identity_key")): row for row in identities}
    opened = clicked_record(
        [{"Market": _label(row),
          "Chemical": display_chemical(row.get("product_family") or ""),
          "Geography": str(row.get("market_geography") or ""),
          "Terms": _display_market_dimension(row.get("commercial_basis") or "")}
         for row in identities],
        list(by_key), key="method_mgmt")

    def _one_market(identity_key: str) -> None:
        try:
            fit = client.model_fit(identity_key)
        except MarketDataEngineUnavailable:
            st.warning("The engine did not answer for this market.")
            return
        if not fit:
            st.info("The engine holds no governance record for this market yet.")
            return

        eligible = sorted(m["method_key"] for m in methods if m.get("status") == "PRODUCTION_ELIGIBLE" and m.get("selectable"))
        for row in admin_rows(fit):
            weeks = row["horizon_weeks"]
            with st.expander(f"{row['Horizon']}: {row['Active method']} ({row['Chosen']})", expanded=weeks == 4):
                st.caption(why_line(fit, weeks))
                a, b = st.columns(2)
                a.markdown(f"**Automatic winner:** {row['Automatic winner']}")
                b.markdown(f"**Override:** {row['Override']}")
                if row["Override recommendation"]:
                    st.warning(row["Override recommendation"])
                if row["challengers_ahead"]:
                    st.info("Challengers that out-scored the winner here: " + ", ".join(row["challengers_ahead"]))
                if row["alternatives"]:
                    st.markdown("**Top alternatives in the last review**")
                    st.dataframe(row["alternatives"], hide_index=True, width="stretch")
                if row["history"]:
                    st.markdown("**Method history**")
                    st.dataframe(row["history"], hide_index=True, width="stretch")
                with st.form(f"override_{identity_key}_{weeks}"):
                    method = st.selectbox("Put a method in force (Production Eligible only)",
                                          _alphabetical(eligible, method_name),
                                          format_func=method_name, key=f"ovr_m_{identity_key}_{weeks}")
                    actor = st.text_input("Your name", key=f"ovr_a_{identity_key}_{weeks}")
                    reason = st.text_input("Why", key=f"ovr_r_{identity_key}_{weeks}")
                    set_it, remove_it = st.columns(2)
                    do_set = set_it.form_submit_button("Set override")
                    do_remove = remove_it.form_submit_button("Remove override")
                if do_set or do_remove:
                    try:
                        if do_set:
                            client.set_override(identity_key, weeks, method, actor=actor, reason=reason)
                            st.success(f"{method_name(method)} is now in force for {weeks} weeks.")
                        else:
                            out = client.remove_override(identity_key, weeks, actor=actor, reason=reason)
                            st.success("Override removed." if out.get("status") == "REMOVED" else "There was no override.")
                        st.rerun()
                    except GovernanceRefused as refused:
                        st.error(str(refused))
                    except MarketDataEngineUnavailable as exc:
                        st.error(f"The engine did not answer: {exc}")

    if opened is not None:
        st.markdown(f"#### {_label(by_key[opened])}")
        _one_market(opened)

    st.markdown("### Challengers")
    challengers = [m for m in methods if m.get("status") == "CHALLENGER"]
    if not challengers:
        st.caption("No Challenger is waiting.")
    else:
        st.dataframe([{"Method": METHOD_NAMES.get(m["method_key"], m["method_key"]), "Kind": m.get("kind"),
                       "Licence": m.get("commercial_use")} for m in challengers], hide_index=True,
                     width="stretch")
        with st.form("approve_challenger"):
            method = st.selectbox("Approve a Challenger (it may then compete; it selects nothing)",
                                  _alphabetical([m["method_key"] for m in challengers], method_name),
                                  format_func=method_name)
            actor = st.text_input("Your name", key="approve_actor")
            reason = st.text_input("Why (the evidence)", key="approve_reason")
            if st.form_submit_button("Approve"):
                try:
                    client.approve_challenger(method, actor=actor, reason=reason)
                    st.success(f"{method_name(method)} is Production Eligible from the next review on.")
                    st.rerun()
                except GovernanceRefused as refused:
                    st.error(str(refused))


FORECAST_HORIZONS_SHOWN = (4, 8, 12)


def render_forecasts_available() -> None:
    """One row per market that has a forecast method in force. Downloadable, and nothing else.

    Stefan, 30 October 2026: *"a very simple table showing which forecasts are now available,
    downloadable as csv, not too complicated."* So: the market, the method in force at each
    horizon, and the date that market was last decided. No charts, no scores, no controls - the
    page next door is where a method is changed.

    A blank horizon cell is not missing data. It means the review found nothing that beat the flat
    control at that horizon for that market, which is a real answer and the most useful thing on
    the row: the baseline run of 29 October left 20 of 240 horizon slots that way.
    """
    st.markdown("### What forecasts are available")
    try:
        rows = MarketDataEngineClient(MARKET_DATA_ENGINE_URL, timeout=1.5).forecasts_available()
    except MarketDataEngineUnavailable:
        st.info("The market-data engine is not answering yet.")
        return
    if not rows:
        st.info("No market has a forecast method in force yet. Run a review to choose them.")
        return

    horizons = sorted({weeks for row in rows for weeks in row.get("horizons") or ()}) \
        or list(FORECAST_HORIZONS_SHOWN)
    table = []
    for row in rows:
        methods = row.get("methods") or {}
        entry = {
            "Chemical": display_chemical(row.get("chemical") or ""),
            # Raw material or feedstock, from `market_selection_ui.chemical_kind` - the one rule the
            # application already has for it, rather than a second list here. A foam producer buys
            # the raw materials; the feedstocks are what moves underneath them, which is why they
            # appear beneath the chart on Current Market rather than in the customer chemical list
            # (Stefan, 1 October 2026: "A foam producer does not buy them").
            "Material type": chemical_kind(row.get("chemical") or ""),
            "Geography": row.get("geography") or "",
            "Price basis": display_basis(row.get("basis") or ""),
            "Price kind": price_kinds.display_kind(row.get("price_kind")),
        }
        standing_in, refused = row.get("standing_in") or {}, row.get("refused") or {}
        for weeks in horizons:
            method = methods.get(str(weeks)) or ""
            # What the last nights produced, where it is not simply the method in force answering
            # (v1.2.137). Until this the table named seven methods that had refused every night.
            if str(weeks) in standing_in:
                method = f"{standing_in[str(weeks)]} (standing in for {method})"
            elif str(weeks) in refused:
                method = f"{method} - REFUSED, nothing stood in"
            entry[f"{weeks} weeks"] = method
        entry["Chosen on"] = str(row.get("chosen_at") or "")[:10]
        table.append(entry)

    frame = pd.DataFrame(table)
    with_all = sum(1 for row in rows if len(row.get("horizons") or ()) == len(horizons))
    st.caption(f"{len(rows)} markets carry a forecast method; {with_all} of them at all "
               f"{len(horizons)} horizons. A blank cell is a horizon where nothing beat the flat "
               f"control.")
    stood_in = sum(len(row.get("standing_in") or {}) for row in rows)
    unanswered = sum(len(row.get("refused") or {}) for row in rows)
    if stood_in or unanswered:
        st.warning(
            f"In the last four nights the method in force could not answer at {stood_in + unanswered} "
            f"market-horizons. Another method from the same review stood in at {stood_in}; "
            f"{unanswered} had no answer at all. They are marked in the table. The method in force "
            f"is asked first again every night.")
    st.dataframe(frame, width="stretch", hide_index=True)
    st.download_button(
        "Download this table (CSV)",
        data=frame.to_csv(index=False).encode("utf-8"),
        file_name=f"htc_forecasts_available_{date.today().isoformat()}.csv",
        mime="text/csv", key="forecasts_available_export")


def _collection_today(sources: list[dict], now: date) -> dict[str, object]:
    """What the collection looks like this morning, judged by the application's own rule.

    `source_status.classify_source` decides whether a source is delivering, stale, failed, not
    checked or monitor-only, and `summarize` counts those; Data & Sources shows the same words.
    Writing a second rule here - "FAILED or PARTIAL is a failure" - would have let the front page
    and the page it sends you to disagree about which sources are broken.

    What this adds is the one thing neither of them says: how many answered TODAY. That is a plain
    reading of `last_successful_collection` and nothing is derived from it.

    `is_due` is deliberately not used. It is the collector's queue - true whenever a source's next
    scheduled check has come round - so at any moment some are legitimately due. Lateness is STALE
    DATA, measured against each publisher's own cadence.
    """
    summary = summarize_sources(sources, today=now)
    judged = [(row, classify_source(row, today=now)) for row in sources]
    arrived = [row for row in sources
               if str(row.get("last_successful_collection") or "")[:10] == now.isoformat()]
    # One table, worst first. Until v1.2.51 the stale rows were counted in a footnote instead,
    # because the rule behind them was a flat fortnight for every publisher and called 51 of 93
    # sources stale - 36 of them monthly series printing monthly. The rule now measures each
    # publisher against its own rhythm, so a row that says STALE DATA is a row to act on and
    # belongs beside the ones that failed rather than in a line explaining itself away.
    waiting = sorted((pair for pair in judged
                      if pair[1].label in {"FAILED", "NO DATA", "NOT CHECKED", "STALE DATA"}),
                     key=lambda pair: (-pair[1].rank, pair[0].get("source_id") or ""))
    # Archives of a period that has closed and delivered it: held, complete, nothing to collect.
    finished = [(row, status) for row, status in judged if status.label == source_status.COMPLETE]
    return {"summary": summary, "arrived": arrived, "waiting": waiting, "finished": finished}


def _render_admin_dashboard_market() -> None:
    """Did the data arrive, and what state are the forecasts in.

    Stefan, 30 October 2026, choosing what the Dashboard should tell him when he opens Platform
    Admin. It counted companies and audit records until then - four numbers about the customers and
    none about the product.
    """
    client = MarketDataEngineClient(MARKET_DATA_ENGINE_URL, timeout=1.5)
    now = datetime.now(timezone.utc)

    st.markdown("### Did the data arrive")
    try:
        sources = client.sources()
    except MarketDataEngineUnavailable:
        st.warning("The market-data engine is not answering, so this morning's collection cannot "
                   "be reported. Start the application with Run-MVP.bat.")
        sources = []
    if sources:
        state = _collection_today(sources, now.date())
        summary = state["summary"]
        c1, c2, c3 = st.columns(3)
        c1.metric("Sources delivering", f"{summary['delivering']} of {summary['live_sources']}")
        c2.metric("Collected today", len(state["arrived"]))
        c3.metric("Needing attention", len(state["waiting"]))
        # The rows a person would act on, and only those. A list of healthy sources is not news.
        if state["waiting"]:
            st.dataframe(pd.DataFrame([{
                "Source": row.get("source_name") or row.get("source_id"),
                "What": status.label,
                "Detail": status.detail,
                "Next check": next_check_text(row),
            } for row, status in state["waiting"]]), width="stretch", hide_index=True)
        else:
            st.success("Every source we collect from is delivering.")
        st.caption(
            "Most publishers print weekly or monthly, so a day's count is not a target. A source "
            "counts as late once it has gone twice its own rhythm without printing, measured off "
            "the days it actually printed on."
            + (f" {len(state['finished'])} more are archives of a period that has closed, held "
               f"complete and not collected." if state["finished"] else ""))

    st.markdown("### What state are forecasts in")
    try:
        available = client.forecasts_available()
        review = client.review_status()
    except MarketDataEngineUnavailable:
        st.warning("The market-data engine is not answering, so the forecasts cannot be counted.")
        return
    horizons = {str(weeks) for weeks in FORECAST_HORIZONS_SHOWN}
    at_every_horizon = [row for row in available
                        if horizons <= {str(k) for k in (row.get("methods") or {})}]
    c1, c2, c3 = st.columns(3)
    c1.metric("Markets with a forecast", len(available))
    c2.metric(f"At all {len(FORECAST_HORIZONS_SHOWN)} horizons", len(at_every_horizon))
    c3.metric("Methods last reviewed", str(review.get("last_at") or "-")[:10])
    state = str(review.get("state") or "").replace("_", " ").lower()
    scope = str(review.get("last_scope") or "")
    if state == "running":
        st.caption("A review is running now.")
    elif state == "never run":
        st.caption("No review has ever finished, so no method has been chosen on measured evidence.")
    elif scope:
        st.caption(f"The last review covered {scope}.")


def _open_questions_waiting() -> int:
    """How many cases on the data nobody has worked yet. A number, so Platform Admin can ask for
    it without importing the engine client - which `test_admin_ui..._isolated_from_step6` forbids
    and should go on forbidding."""
    try:
        cases = MarketDataEngineClient(MARKET_DATA_ENGINE_URL, timeout=1.5).investigation_cases()
    except MarketDataEngineUnavailable:
        return 0
    return sum(1 for case in cases
               if str(case.get("status") or "").upper() not in {"CLOSED", "RULED_OUT"})


def _markets_held_for_platform_admin() -> list[tuple[str, str, bool]] | None:
    """Every (chemical, market) we publish, and whether Current Market offers it today.

    The Products page asks this so that a product is sold with its contents in view: which of a
    group's materials hold a current price in the regions being bought (CR-2026-11-26-01). It is
    the customer pages' own answer - the same identities, the same rule - so what the admin is
    told a customer will see is what the customer sees. None when the market data cannot be read.
    """
    snapshot = _load_step6_snapshot(readings="index")
    if snapshot is None:
        return None
    identities = [dict(row) for row in snapshot.identities if is_published_market(row)]
    current = {(one[0], one[1]) for one in customer_catalogue.offered(
        identities, snapshot.observations, customer_catalogue.CURRENT, sources=snapshot.sources)}
    held = {(str(row.get("product_family") or "").strip(),
             str(row.get("market_geography") or "").strip()) for row in identities}
    return sorted((chemical, geography, (chemical, geography) in current)
                  for chemical, geography in held if chemical and geography)


def _price_monitor_markets(scope, tell=None) -> list[dict] | None:
    """Every forecast market a product opens, as what the Forecast page's report is made from.

    The Price Monitor is "the forecast report as per the button on the forecast page" (Stefan,
    26 November 2026), for every market a customer's product covers. So this walks the Forecast
    page's own three selectors - chemical, geography, terms - over everything `scope` opens, and
    for each market gathers exactly what the button hands `page_reports.forecast_docx`: the same
    identities, the same forecast, the same export frame, the same "today". A market the page
    would show no forecast for is left out, as the page would leave the button out.

    None when the market data cannot be read at all; a market the engine does not answer for is
    skipped, and the document says how many markets it carries.

    `tell(done, of, name)` is called before each market, because each one asks the engine for a
    forecast and forty-six of them took eleven minutes the first time this ran.
    """
    snapshot = _load_step6_snapshot(readings="index")
    if snapshot is None:
        return None
    identities = [dict(row) for row in snapshot.identities
                  if is_published_market(row) and identity_allowed(row, scope)]
    observations = list(snapshot.observations)
    drawable = customer_catalogue.offered(identities, snapshot.observations,
                                          customer_catalogue.FORECAST,
                                          forecast_from_year=FORECAST_FROM_YEAR)
    priced_now = customer_catalogue.offered(identities, snapshot.observations,
                                            customer_catalogue.CURRENT, sources=snapshot.sources)
    horizons = tuple(weeks for weeks in (4, 8, 12) if weeks <= scope.forecast_horizon_months * 4)
    if not horizons:
        return []
    currency = display_currency()
    same_release = _engine_is_this_release()
    chemicals = chemicals_within_entitlement(customer_catalogue.chemicals_offered(drawable),
                                             scope.chemicals)
    wanted: list[tuple[str, str, str, tuple[str, ...]]] = []
    for chemical in _alphabetical(chemicals, display_chemical):
        geographies = filter_entitled_values(geographies_with_a_market(drawable, chemical),
                                             scope.geographies)
        for origin in _alphabetical(geographies, _display_market_dimension):
            for incoterm in filter_entitled_values(terms_with_a_market(drawable, chemical, origin),
                                                   scope.incoterms):
                keys = forecast_identity_keys(identities, observations, chemical, origin, incoterm)
                if keys:
                    wanted.append((chemical, origin, incoterm, keys))
    entries: list[dict] = []
    for number, (chemical, origin, incoterm, keys) in enumerate(wanted, 1):
        market = {"Chemical": _display_market_dimension(chemical), "Geography": origin,
                  INCOTERM_LABEL: _display_basis(incoterm)}
        if callable(tell):
            tell(number, len(wanted), " · ".join(market.values()))
        payload = _load_forecast(keys[0], horizons)
        if not payload:
            continue
        market_price = (_current_market_price(identities, snapshot.sources, priced_now,
                                              chemical, origin, incoterm,
                                              kind=_kind_that_is_forecast(identities, keys[0]))
                        if same_release else None)
        export = forecast_export_frame(payload, str(payload.get("currency") or ""),
                                       currency, _load_fx_table(), market_price=market_price)
        history, ahead = _report_series(export, currency)
        if not ahead:
            continue
        if market_price is not None:
            today_day, today_value = market_price
        else:
            state = selected_market_evidence(
                snapshot, keys,
                readings=_market_readings(tuple(sorted(keys)), currency, _snapshot_stamp()))
            today_value, today_day = state.reference_value, state.latest_observation_date
        entries.append({
            "market": market, "history": history, "forecast": ahead, "currency": currency,
            "today_price": (units.with_unit(f"{currency} {today_value:,.0f}")
                            if today_value is not None else ""),
            "priced": str(today_day or ""),
        })
    return entries


def _platform_admin_internal_renderers() -> dict[str, object]:
    return {
        "Markets held": _markets_held_for_platform_admin,
        "Price Monitor markets": _price_monitor_markets,
        "Import": render_document_import,
        "Reports": render_period_report,
        "Data & Sources": lambda: render_data_sources(None),
        "Chemical Relationships": lambda: render_chemical_relationships(None),
        "Relationship method": lambda: render_relationship_method(None),
        "Forecast Performance": lambda: render_forecast_performance(None),
        "Forecast Method Management": render_forecast_method_management,
        "Forecasts Available": render_forecasts_available,
        "Delivered Price": render_delivered_price,
        "Investigation": render_investigation,
        # Only where there is something to close. On a published deployment the processes this
        # stops do not exist, and the one it would stop is the service itself - for everybody.
        **({} if HOSTED_DEPLOYMENT else {"Close the application": _close_the_application}),
        # Two entries that are not pages: the Dashboard's market half, and one number it asks for.
        # Platform Admin draws its own commercial half and reaches the engine through these,
        # keeping its isolation from the engine intact.
        "Dashboard: market data": _render_admin_dashboard_market,
        "Open questions waiting": _open_questions_waiting,
        # The banner and the collection gauge, so Platform Admin is part of the application rather
        # than a plain page bolted to the side of it (Stefan, 30 October 2026).
        "Brand header": render_brand_header,
        "Collection gauge": render_collection_progress_gauge,
    }


def _require_a_store_worth_signing_in_to() -> None:
    """A published deployment with no store refuses everybody, and says nothing useful about it.

    `platform_store_url` falls back to a SQLite file when DATABASE_URL is unset. On this machine
    that file is the real one. In a container it is new and empty: no companies, no users, no
    credentials. Every sign-in then fails with "that email and password do not open an account" -
    the message deliberately written to tell a stranger nothing - and the one person who needs to
    know what is actually wrong is the owner, standing at his own front door.

    Stefan, 23 November 2026, on the first Streamlit Cloud deployment: *"My pw is not working."*
    It was not the password. There was no store to check it against.

    The container settles this in its entrypoint, which exits 78 before Streamlit ever starts.
    Streamlit Community Cloud has no entrypoint, so the fence lives here instead.
    """
    if not HOSTED_DEPLOYMENT:
        return
    if not platform_store_url().startswith("sqlite"):
        return
    render_brand_header()
    st.error(
        "**This deployment has no store, so nobody can sign in.**\n\n"
        "`DATABASE_URL` is not reaching the application. In the app's **Settings → Secrets**, it "
        "has to be a line of its own at the top of the file, not under a `[section]` heading:\n\n"
        "```toml\n"
        'DATABASE_URL = "postgresql+psycopg://..."\n'
        "```\n\n"
        "Save the secrets and the app restarts by itself."
    )
    st.stop()


def main() -> None:
    # The keys the application needs, from a file beside the store rather than from the project
    # folder, which is synced to OneDrive. Names only ever reach the log; values never leave here.
    secrets_file.load()
    st.set_page_config(page_title="PU Raw Material Forecasting", page_icon="📈", layout="wide", initial_sidebar_state="collapsed")
    apply_dashboard_css()
    _require_a_store_worth_signing_in_to()

    if _platform_admin_route_requested():
        render_platform_admin(internal_renderers=_platform_admin_internal_renderers())
        return

    runtime_access = resolve_runtime_access()
    render_brand_header()

    # A newer release on disk is code that is NOT running. Saying so here means the version in the
    # header can always be trusted to be the one answering.
    if restart_required():
        st.warning(restart_message())

    pages = application_pages(APPLICATION_PAGES, login_is_enabled=login_enabled())
    if runtime_access is not None:
        pages = application_pages(
            allowed_application_pages(runtime_access.scope, APPLICATION_PAGES),
            login_is_enabled=login_enabled(),
        )

    customer_pages = [page for page in pages if page != PLATFORM_ADMIN_PAGE]
    if not customer_pages:
        st.error("No application functions are included in the active entitlement.")
        st.stop()
    current = st.session_state.get("application_navigation")
    if current not in customer_pages:
        st.session_state["application_navigation"] = customer_pages[0]

    current_page = st.session_state.get("application_navigation", customer_pages[0])
    if current_page == "Current Market":
        render_collection_progress_gauge()

    # Closing the application is a thing the person does often and Platform Admin is not where they
    # are when they want to. Stefan, 28 September 2026: a button on the right of the page, not in
    # the banner and not behind Platform Admin. It sits in the top row beside Platform Admin, so it
    # is in the same place whichever page is open.
    nav_col, admin_col, close_col = st.columns([7, 1.35, 1.0])
    with nav_col:
        page = st.radio(
            "Application navigation",
            customer_pages,
            horizontal=True,
            label_visibility="collapsed",
            key="application_navigation",
        )
    with admin_col:
        if _platform_admin_control_visible():
            if st.button(
                "Platform Admin",
                key="platform_admin_entry",
                width="stretch",
            ):
                st.query_params["admin"] = "1"
                st.rerun()
    with close_col:
        # A signed-in customer gets Sign out here, never Close. Close stops the engine, the
        # collection run and the delivery worker - on a hosted deployment it would take the
        # service away from everybody else reading it, from a button a customer has no reason to
        # read as anything but "shut my own window". Stefan, 22 November 2026.
        if _signed_in_email():
            if st.button("Sign out", key="application_signout", width="stretch"):
                _sign_out()
        # Straight to the one question. Going through Platform Admin's own "Close the application"
        # button as well would ask twice for the same thing; the rule is shown once, one yes.
        elif st.button("Close", key="close_application_entry", width="stretch"):
            st.session_state["closing_the_application"] = True
            st.session_state["shutdown_asked"] = True
            st.rerun()

    # Then it takes over the page: closing stops the engine, the collection run and the delivery
    # worker, and the reader should be looking at what happened rather than at a market while it
    # happens. The same renderer Platform Admin uses, so there is one of it.
    if st.session_state.get("closing_the_application") and not HOSTED_DEPLOYMENT:
        if not st.session_state.get("shutdown_asked") and not st.session_state.get("shutdown_done"):
            # "Keep it running" was pressed. Back to where they were.
            st.session_state["closing_the_application"] = False
            st.rerun()
        _close_the_application()
        return

    if page == "Current Market":
        render_current_market(runtime_access)
    elif page == "Historic Pricing":
        render_historic_pricing(runtime_access)
    elif page == "Market Intelligence":
        render_market_intelligence(runtime_access)
    else:
        render_market_forecast(runtime_access)

    if runtime_access is not None:
        st.caption(f"Access scope: {runtime_access.company.name} · {runtime_access.user.email}")

    st.markdown(
        f'<div class="app-footer"><span>HTC Global &nbsp;|&nbsp; PU Observatory - Market Intelligence Engine</span><span>{FORECAST_SCREEN_NAME} &nbsp;|&nbsp; v{VERSION}</span></div>',
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
