from __future__ import annotations

import json
import threading
import weakref
from datetime import datetime, timezone

from raw_material_forecasting.platform_store import protect_tables
from sqlalchemy import Boolean, Column, Date, DateTime, Integer, LargeBinary, MetaData, String, Table, Text, delete, insert, select, update
from sqlalchemy.engine import Engine

from .auth import normalise_email
from .models import (
    AuditRecord,
    CommercialPackage,
    Company,
    CompanyEntitlement,
    DeliveryProfile,
    Recipient,
    Subscription,
    TemporaryEntitlement,
    UserAccess,
    UserCredential,
    PlatformAdminCredential,
    scope_from_dict,
    scope_to_dict,
)

metadata = MetaData()

companies = Table(
    "pa_company", metadata,
    Column("company_id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("status", String, nullable=False),
    Column("contract_effective_from", Date, nullable=False),
    Column("contract_effective_to", Date),
)
packages = Table(
    "pa_package", metadata,
    Column("package_id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("scope_json", Text, nullable=False),
    Column("active", Boolean, nullable=False),
)
permanent_entitlements = Table(
    "pa_company_entitlement", metadata,
    Column("company_id", String, primary_key=True),
    Column("entitlement_id", String, nullable=False, unique=True),
    Column("scope_json", Text, nullable=False),
    Column("effective_from", DateTime(timezone=True), nullable=False),
    Column("effective_to", DateTime(timezone=True)),
    Column("package_id", String),
)
temporary_entitlements = Table(
    "pa_temporary_entitlement", metadata,
    Column("entitlement_id", String, primary_key=True),
    Column("company_id", String, nullable=False),
    Column("scope_json", Text, nullable=False),
    Column("effective_from", DateTime(timezone=True), nullable=False),
    Column("effective_to", DateTime(timezone=True), nullable=False),
    Column("reason_type", String, nullable=False),
    Column("reason", Text, nullable=False),
    Column("created_by", String, nullable=False),
    Column("approved_by", String, nullable=False),
)
# A product a customer holds (CR-2026-11-26-01). What was bought, in the words it was sold in;
# the scope it opens is worked out from these, never stored beside them, so that a change to a
# group's materials or a region's markets reaches every customer who holds it.
subscriptions = Table(
    "pa_subscription", metadata,
    Column("subscription_id", String, primary_key=True),
    Column("company_id", String, nullable=False),
    Column("product", String, nullable=False),
    Column("edition", String, nullable=False),
    Column("material_group", String, nullable=False),
    Column("regions_json", Text, nullable=False),
    Column("effective_from", DateTime(timezone=True), nullable=False),
    Column("effective_to", DateTime(timezone=True)),
    Column("annual_discount", Integer, nullable=False, server_default="0"),
)
# Every email the application tried to send (v1.2.135): to whom, what, when, and whether Microsoft
# took it. Kept here, beside the change history, because it answers the same kind of question -
# "did we tell them, and when" - and is written by the same hands.
mails = Table(
    "pa_mail", metadata,
    Column("mail_id", String, primary_key=True),
    Column("company_id", String, nullable=False, default=""),
    Column("purpose", String, nullable=False),
    Column("sender", String, nullable=False),
    Column("recipients_json", Text, nullable=False),
    Column("subject", Text, nullable=False),
    Column("attachments_json", Text, nullable=False),
    Column("status", String, nullable=False),
    Column("error", Text, nullable=False, default=""),
    Column("sent_at", DateTime(timezone=True), nullable=False),
    Column("sent_by", String, nullable=False),
)
# An edition of the Price Monitor made for a customer (v1.2.136), and the document itself.
#
# The document is kept in the store and not on a disk. The live site's disk is emptied whenever the
# host restarts it, and "open the October report and see who it was sent to" has to work in March.
# It is a megabyte or two a customer a month, and `list_monitor_issues` never reads it: the store's
# traffic allowance is shared by eight projects, and a list that pulled every document it named
# would spend it.
monitor_issues = Table(
    "pa_monitor_issue", metadata,
    Column("issue_id", String, primary_key=True),
    Column("subscription_id", String, nullable=False),
    Column("company_id", String, nullable=False),
    Column("edition", String, nullable=False),
    Column("title", Text, nullable=False),
    Column("file_name", String, nullable=False),
    Column("markets", Integer, nullable=False),
    Column("size_bytes", Integer, nullable=False),
    Column("status", String, nullable=False),
    Column("made_at", DateTime(timezone=True), nullable=False),
    Column("made_by", String, nullable=False),
    Column("sent_at", DateTime(timezone=True)),
    Column("sent_by", String, nullable=False, default=""),
    Column("recipients_json", Text, nullable=False, default="[]"),
    Column("mail_id", String, nullable=False, default=""),
    Column("note", Text, nullable=False, default=""),
    Column("content", LargeBinary, nullable=False),
)
users = Table(
    "pa_user", metadata,
    Column("user_id", String, primary_key=True),
    Column("company_id", String, nullable=False),
    Column("email", String, nullable=False),
    Column("role", String, nullable=False),
    Column("scope_json", Text, nullable=False),
    Column("active", Boolean, nullable=False),
    Column("name", String, nullable=False, server_default=""),
)
approved_domains = Table(
    "pa_approved_domain", metadata,
    Column("company_id", String, primary_key=True),
    Column("domains_json", Text, nullable=False),
)
recipients = Table(
    "pa_recipient", metadata,
    Column("recipient_id", String, primary_key=True),
    Column("company_id", String, nullable=False),
    Column("email", String, nullable=False),
    Column("name", String, nullable=False),
    Column("external_exception", Boolean, nullable=False),
    Column("exception_reason", Text),
    Column("active", Boolean, nullable=False, server_default="1"),
)
delivery_profiles = Table(
    "pa_delivery_profile", metadata,
    Column("profile_id", String, primary_key=True),
    Column("company_id", String, nullable=False),
    Column("name", String, nullable=False),
    Column("scope_json", Text, nullable=False),
    Column("frequency", String, nullable=False),
    Column("formats_json", Text, nullable=False),
    Column("channels_json", Text, nullable=False),
    Column("recipient_ids_json", Text, nullable=False),
    Column("active", Boolean, nullable=False),
)
# What a customer signs in with. Keyed on the normalised email rather than on user_id, because the
# sign-in form has an email and nothing else to look the account up by.
user_credentials = Table(
    "pa_user_auth", metadata,
    Column("email", String, primary_key=True),
    Column("user_id", String, nullable=False),
    Column("password_hash", Text, nullable=False),
    Column("password_salt", Text, nullable=False),
    Column("password_iterations", Integer, nullable=False),
    Column("password_set_at", DateTime(timezone=True), nullable=False),
    Column("must_change", Boolean, nullable=False, server_default="1"),
)
platform_admin_credentials = Table(
    "pa_platform_admin_auth", metadata,
    Column("singleton_key", String, primary_key=True),
    Column("email", String, nullable=False, unique=True),
    Column("password_hash", Text, nullable=False),
    Column("password_salt", Text, nullable=False),
    Column("password_iterations", Integer, nullable=False),
    Column("password_set_at", DateTime(timezone=True), nullable=False),
)

audits = Table(
    "pa_audit", metadata,
    Column("audit_id", String, primary_key=True),
    Column("company_id", String, nullable=False),
    Column("entity_type", String, nullable=False),
    Column("entity_id", String, nullable=False),
    Column("action", String, nullable=False),
    Column("actor", String, nullable=False),
    Column("old_value_json", Text, nullable=False),
    Column("new_value_json", Text, nullable=False),
    Column("reason", Text, nullable=False),
    Column("effective_date", Date, nullable=False),
    Column("changed_at", DateTime(timezone=True), nullable=False),
)


# --- chemical routes ---------------------------------------------------------------------------
# The governed production graph. It lives here, beside entitlements, because Platform Admin is where
# it is edited and audited - and because the application reaches the market-data engine over HTTP and
# cannot write to it. The engine has no need of this graph: it collects facts about chemicals, and
# what those facts mean is decided on this side.
chemical_nodes = Table(
    "pa_chemical_node", metadata,
    Column("code", String, primary_key=True),
    Column("display_name", String, nullable=False),
    # False for a technically necessary intermediate that no one quotes a price for - phosgene, MDA,
    # crude MDI, reformate. The route stays true; the forecasting layer simply has nothing to read.
    Column("price_tracked", Boolean, nullable=False, default=True),
    # Where a chain terminates once it stops being petroleum: CRUDE, NATURAL_GAS, BRINE, AIR, WATER,
    # BIOMASS. A brine-rooted chain behaves nothing like a crude-rooted one.
    Column("root_class", String),
    Column("is_root", Boolean, nullable=False, default=False),
    Column("aliases_json", Text, nullable=False, default="[]"),
    Column("note", Text, nullable=False, default=""),
    Column("active", Boolean, nullable=False, default=True),
)
chemical_route_edges = Table(
    "pa_chemical_route_edge", metadata,
    Column("edge_id", String, primary_key=True),
    # Mandatory, not optional: propylene oxide alone has five commercial routes, and an edge true for
    # HPPO must not be read as true for POSM.
    Column("route_id", String, nullable=False),
    Column("from_chemical", String, nullable=False),
    Column("relation", String, nullable=False),
    Column("to_chemical", String, nullable=False),
    Column("technical_qualifier", Text, nullable=False, default=""),
    Column("note", Text, nullable=False, default=""),
    Column("active", Boolean, nullable=False, default=True),
)

def _scope_json(scope) -> str:
    return json.dumps(scope_to_dict(scope), sort_keys=True, separators=(",", ":"))


def _set_json(values) -> str:
    return json.dumps(sorted(values), separators=(",", ":"))


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# Engines whose schema has already been settled. Weak, so an engine a test drops goes with it.
_SCHEMA_SETTLED: "weakref.WeakSet[Engine]" = weakref.WeakSet()
_SCHEMA_LOCK = threading.Lock()


class PlatformAdminRepository:
    def __init__(self, engine: Engine):
        self.engine = engine
        self._settle_the_schema_once()

    def _settle_the_schema_once(self) -> None:
        """`create_all` and the later columns, once per engine rather than once per page run.

        Both are reflection: `create_all` asks the store which of seventeen tables exist, and
        `_add_missing_columns` asks for one table's columns. Streamlit rebuilds this repository on
        every click, so both ran on every click - a round trip to Supabase for an answer that
        cannot have changed since the process started, on top of the connection the question
        needed (Stefan, 23 November 2026: the Subscriptions page refusing to connect at all).

        The schema is settled by migrations on a deployment and by this on a fresh SQLite file.
        Either way it is settled once.
        """
        with _SCHEMA_LOCK:
            if self.engine in _SCHEMA_SETTLED:
                return
        metadata.create_all(self.engine)
        self._add_missing_columns()
        protect_tables(self.engine, "platform admin")
        with _SCHEMA_LOCK:
            _SCHEMA_SETTLED.add(self.engine)

    # Columns added to a table that already exists. `create_all` makes tables that are missing and
    # leaves the rest alone, so without this a store created before 31 October 2026 would be read
    # for a column it has not got, on both SQLite and Postgres.
    LATER_COLUMNS = (("pa_recipient", "active", "BOOLEAN NOT NULL DEFAULT TRUE"),
                     # v1.2.142: the discount agreed on a product, and who a user is.
                     ("pa_subscription", "annual_discount", "INTEGER NOT NULL DEFAULT 0"),
                     ("pa_user", "name", "VARCHAR NOT NULL DEFAULT ''"))

    def _add_missing_columns(self) -> None:
        from sqlalchemy import inspect, text

        inspector = inspect(self.engine)
        for table, column, declaration in self.LATER_COLUMNS:
            if not inspector.has_table(table):
                continue
            if column in {c["name"] for c in inspector.get_columns(table)}:
                continue
            with self.engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}"))

    def _upsert(self, table: Table, key_col, key, values: dict) -> None:
        with self.engine.begin() as conn:
            exists = conn.execute(select(key_col).where(key_col == key)).first()
            if exists:
                conn.execute(update(table).where(key_col == key).values(**values))
            else:
                conn.execute(insert(table).values(**values))

    def upsert_company(self, company: Company) -> None:
        self._upsert(companies, companies.c.company_id, company.company_id, {
            "company_id": company.company_id,
            "name": company.name,
            "status": company.status,
            "contract_effective_from": company.contract_effective_from,
            "contract_effective_to": company.contract_effective_to,
        })

    def get_company(self, company_id: str) -> Company | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(companies).where(companies.c.company_id == company_id)).mappings().first()
        return Company(**dict(row)) if row else None

    def list_companies(self) -> list[Company]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(companies).order_by(companies.c.name)).mappings().all()
        return [Company(**dict(r)) for r in rows]

    def upsert_package(self, package: CommercialPackage) -> None:
        self._upsert(packages, packages.c.package_id, package.package_id, {
            "package_id": package.package_id,
            "name": package.name,
            "scope_json": _scope_json(package.scope),
            "active": package.active,
        })

    def get_package(self, package_id: str) -> CommercialPackage | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(packages).where(packages.c.package_id == package_id)).mappings().first()
        if not row:
            return None
        return CommercialPackage(row["package_id"], row["name"], scope_from_dict(json.loads(row["scope_json"])), bool(row["active"]))

    def list_packages(self) -> list[CommercialPackage]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(packages).order_by(packages.c.name)).mappings().all()
        return [CommercialPackage(r["package_id"], r["name"], scope_from_dict(json.loads(r["scope_json"])), bool(r["active"])) for r in rows]

    def set_company_entitlement(self, entitlement: CompanyEntitlement) -> None:
        self._upsert(permanent_entitlements, permanent_entitlements.c.company_id, entitlement.company_id, {
            "company_id": entitlement.company_id,
            "entitlement_id": entitlement.entitlement_id,
            "scope_json": _scope_json(entitlement.scope),
            "effective_from": entitlement.effective_from,
            "effective_to": entitlement.effective_to,
            "package_id": entitlement.package_id,
        })

    def get_company_entitlement(self, company_id: str) -> CompanyEntitlement | None:
        with self.engine.connect() as conn:
            r = conn.execute(select(permanent_entitlements).where(permanent_entitlements.c.company_id == company_id)).mappings().first()
        if not r:
            return None
        return CompanyEntitlement(r["entitlement_id"], r["company_id"], scope_from_dict(json.loads(r["scope_json"])), _as_utc(r["effective_from"]), _as_utc(r["effective_to"]), r["package_id"])

    def upsert_temporary_entitlement(self, entitlement: TemporaryEntitlement) -> None:
        self._upsert(temporary_entitlements, temporary_entitlements.c.entitlement_id, entitlement.entitlement_id, {
            "entitlement_id": entitlement.entitlement_id,
            "company_id": entitlement.company_id,
            "scope_json": _scope_json(entitlement.scope),
            "effective_from": entitlement.effective_from,
            "effective_to": entitlement.effective_to,
            "reason_type": entitlement.reason_type,
            "reason": entitlement.reason,
            "created_by": entitlement.created_by,
            "approved_by": entitlement.approved_by,
        })

    def list_temporary_entitlements(self, company_id: str) -> list[TemporaryEntitlement]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(temporary_entitlements).where(temporary_entitlements.c.company_id == company_id).order_by(temporary_entitlements.c.effective_from)).mappings().all()
        return [TemporaryEntitlement(r["entitlement_id"], r["company_id"], scope_from_dict(json.loads(r["scope_json"])), _as_utc(r["effective_from"]), _as_utc(r["effective_to"]), r["reason_type"], r["reason"], r["created_by"], r["approved_by"]) for r in rows]

    def upsert_subscription(self, subscription: Subscription) -> None:
        self._upsert(subscriptions, subscriptions.c.subscription_id, subscription.subscription_id, {
            "subscription_id": subscription.subscription_id,
            "company_id": subscription.company_id,
            "product": subscription.product,
            "edition": subscription.edition,
            "material_group": subscription.material_group,
            "regions_json": _set_json(subscription.regions),
            "effective_from": subscription.effective_from,
            "effective_to": subscription.effective_to,
            "annual_discount": int(subscription.annual_discount or 0),
        })

    @staticmethod
    def _subscription(r) -> Subscription:
        return Subscription(r["subscription_id"], r["company_id"], r["product"], r["edition"],
                            r["material_group"], frozenset(json.loads(r["regions_json"])),
                            _as_utc(r["effective_from"]), _as_utc(r["effective_to"]),
                            int(r["annual_discount"] or 0))

    def get_subscription(self, subscription_id: str) -> Subscription | None:
        with self.engine.connect() as conn:
            r = conn.execute(select(subscriptions).where(
                subscriptions.c.subscription_id == subscription_id)).mappings().first()
        return self._subscription(r) if r else None

    def list_subscriptions(self, company_id: str | None = None) -> list[Subscription]:
        query = select(subscriptions).order_by(subscriptions.c.company_id,
                                               subscriptions.c.effective_from)
        if company_id is not None:
            query = query.where(subscriptions.c.company_id == company_id)
        with self.engine.connect() as conn:
            rows = conn.execute(query).mappings().all()
        return [self._subscription(r) for r in rows]

    # ---- the Price Monitor ---------------------------------------------------------------------
    _ISSUE_COLUMNS = ("issue_id", "subscription_id", "company_id", "edition", "title", "file_name",
                      "markets", "size_bytes", "status", "made_at", "made_by", "sent_at", "sent_by",
                      "recipients_json", "mail_id", "note")

    @staticmethod
    def _issue_values(issue) -> dict:
        return {
            "issue_id": issue.issue_id, "subscription_id": issue.subscription_id,
            "company_id": issue.company_id, "edition": issue.edition, "title": issue.title,
            "file_name": issue.file_name, "markets": issue.markets,
            "size_bytes": issue.size_bytes, "status": issue.status, "made_at": issue.made_at,
            "made_by": issue.made_by, "sent_at": issue.sent_at, "sent_by": issue.sent_by,
            "recipients_json": json.dumps(list(issue.recipients)), "mail_id": issue.mail_id,
            "note": issue.note}

    def add_monitor_issue(self, issue, content: bytes) -> None:
        """Keep one edition and its document. A `price_monitor.MonitorIssue`."""
        with self.engine.begin() as conn:
            conn.execute(insert(monitor_issues).values(**self._issue_values(issue), content=content))

    def update_monitor_issue(self, issue) -> None:
        """What became of an edition: sent, held back, replaced. The document is not rewritten."""
        with self.engine.begin() as conn:
            conn.execute(update(monitor_issues)
                         .where(monitor_issues.c.issue_id == issue.issue_id)
                         .values(**self._issue_values(issue)))

    def list_monitor_issues(self, limit: int = 200) -> list:
        """The newest editions first, WITHOUT their documents. See the table's own comment."""
        from raw_material_forecasting.price_monitor import MonitorIssue

        columns = [monitor_issues.c[name] for name in self._ISSUE_COLUMNS]
        with self.engine.connect() as conn:
            rows = conn.execute(select(*columns).order_by(monitor_issues.c.made_at.desc())
                                .limit(int(limit))).mappings().all()
        return [MonitorIssue(
            r["issue_id"], r["subscription_id"], r["company_id"], r["edition"], r["title"],
            r["file_name"], int(r["markets"]), int(r["size_bytes"]), r["status"],
            _as_utc(r["made_at"]), r["made_by"], _as_utc(r["sent_at"]), r["sent_by"] or "",
            tuple(json.loads(r["recipients_json"] or "[]")), r["mail_id"] or "", r["note"] or "")
            for r in rows]

    def monitor_issue_content(self, issue_id: str) -> bytes | None:
        """One edition's document. Asked for one at a time, when somebody opens or sends it."""
        with self.engine.connect() as conn:
            row = conn.execute(select(monitor_issues.c.content)
                               .where(monitor_issues.c.issue_id == issue_id)).first()
        return bytes(row[0]) if row else None

    def add_mail(self, mail) -> None:
        """Record one email the application tried to send. A `mail.MailRecord`."""
        with self.engine.begin() as conn:
            conn.execute(insert(mails).values(
                mail_id=mail.mail_id, company_id=mail.company_id, purpose=mail.purpose,
                sender=mail.sender, recipients_json=json.dumps(list(mail.recipients)),
                subject=mail.subject, attachments_json=json.dumps(list(mail.attachments)),
                status=mail.status, error=mail.error, sent_at=mail.sent_at, sent_by=mail.sent_by))

    def list_mail(self, limit: int = 200) -> list:
        """The newest emails first. A page of them, never the whole table."""
        from raw_material_forecasting.mail import MailRecord

        with self.engine.connect() as conn:
            rows = conn.execute(
                select(mails).order_by(mails.c.sent_at.desc()).limit(int(limit))).mappings().all()
        return [MailRecord(
            r["mail_id"], r["company_id"], r["purpose"], r["sender"],
            tuple(json.loads(r["recipients_json"])), r["subject"],
            tuple(json.loads(r["attachments_json"])), r["status"], r["error"] or "",
            _as_utc(r["sent_at"]), r["sent_by"]) for r in rows]

    def upsert_user(self, user: UserAccess) -> None:
        self._upsert(users, users.c.user_id, user.user_id, {
            "user_id": user.user_id, "company_id": user.company_id, "email": user.email,
            "role": user.role, "scope_json": _scope_json(user.scope), "active": user.active,
            "name": str(user.name or "").strip(),
        })

    def get_user(self, user_id: str) -> UserAccess | None:
        with self.engine.connect() as conn:
            r = conn.execute(select(users).where(users.c.user_id == user_id)).mappings().first()
        if not r:
            return None
        return UserAccess(r["user_id"], r["company_id"], r["email"], r["role"], scope_from_dict(json.loads(r["scope_json"])), bool(r["active"]), str(r["name"] or ""))

    def list_users(self, company_id: str) -> list[UserAccess]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(users).where(users.c.company_id == company_id).order_by(users.c.email)).mappings().all()
        return [UserAccess(r["user_id"], r["company_id"], r["email"], r["role"], scope_from_dict(json.loads(r["scope_json"])), bool(r["active"]), str(r["name"] or "")) for r in rows]

    def find_user_by_email(self, email: str) -> UserAccess | None:
        normalized = email.strip().lower()
        with self.engine.connect() as conn:
            rows = conn.execute(select(users)).mappings().all()
        for r in rows:
            if str(r["email"]).strip().lower() == normalized:
                return UserAccess(r["user_id"], r["company_id"], r["email"], r["role"], scope_from_dict(json.loads(r["scope_json"])), bool(r["active"]), str(r["name"] or ""))
        return None

    def get_user_credential(self, email: str) -> UserCredential | None:
        normalized = normalise_email(email)
        with self.engine.connect() as conn:
            r = conn.execute(
                select(user_credentials).where(user_credentials.c.email == normalized)
            ).mappings().first()
        if not r:
            return None
        return UserCredential(
            email=r["email"],
            user_id=r["user_id"],
            password_hash=r["password_hash"],
            password_salt=r["password_salt"],
            password_iterations=int(r["password_iterations"]),
            password_set_at=_as_utc(r["password_set_at"]),
            must_change=bool(r["must_change"]),
        )

    def set_user_credential(self, credential: UserCredential) -> None:
        normalized = normalise_email(credential.email)
        self._upsert(user_credentials, user_credentials.c.email, normalized, {
            "email": normalized,
            "user_id": credential.user_id,
            "password_hash": credential.password_hash,
            "password_salt": credential.password_salt,
            "password_iterations": credential.password_iterations,
            "password_set_at": credential.password_set_at,
            "must_change": credential.must_change,
        })

    def get_platform_admin_credential(self) -> PlatformAdminCredential | None:
        with self.engine.connect() as conn:
            r = conn.execute(
                select(platform_admin_credentials).where(platform_admin_credentials.c.singleton_key == "primary")
            ).mappings().first()
        if not r:
            return None
        return PlatformAdminCredential(
            email=r["email"],
            password_hash=r["password_hash"],
            password_salt=r["password_salt"],
            password_iterations=int(r["password_iterations"]),
            password_set_at=_as_utc(r["password_set_at"]),
        )

    def set_platform_admin_credential(self, credential: PlatformAdminCredential) -> None:
        self._upsert(
            platform_admin_credentials,
            platform_admin_credentials.c.singleton_key,
            "primary",
            {
                "singleton_key": "primary",
                "email": credential.email,
                "password_hash": credential.password_hash,
                "password_salt": credential.password_salt,
                "password_iterations": credential.password_iterations,
                "password_set_at": credential.password_set_at,
            },
        )

    def replace_approved_domains(self, company_id: str, domains: set[str]) -> None:
        normalized = {d.strip().lower() for d in domains if d.strip()}
        self._upsert(approved_domains, approved_domains.c.company_id, company_id, {"company_id": company_id, "domains_json": _set_json(normalized)})

    def list_approved_domains(self, company_id: str) -> set[str]:
        with self.engine.connect() as conn:
            r = conn.execute(select(approved_domains.c.domains_json).where(approved_domains.c.company_id == company_id)).first()
        return set(json.loads(r[0])) if r else set()

    def upsert_recipient(self, recipient: Recipient) -> None:
        self._upsert(recipients, recipients.c.recipient_id, recipient.recipient_id, {
            "recipient_id": recipient.recipient_id, "company_id": recipient.company_id,
            "email": recipient.email, "name": recipient.name,
            "external_exception": recipient.external_exception,
            "exception_reason": recipient.exception_reason, "active": recipient.active,
        })

    def get_recipient(self, recipient_id: str) -> Recipient | None:
        with self.engine.connect() as conn:
            r = conn.execute(select(recipients).where(recipients.c.recipient_id == recipient_id)).mappings().first()
        return Recipient(**dict(r)) if r else None

    def list_recipients(self, company_id: str) -> list[Recipient]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(recipients).where(recipients.c.company_id == company_id).order_by(recipients.c.email)).mappings().all()
        return [Recipient(**dict(r)) for r in rows]

    def upsert_delivery_profile(self, profile: DeliveryProfile) -> None:
        self._upsert(delivery_profiles, delivery_profiles.c.profile_id, profile.profile_id, {
            "profile_id": profile.profile_id, "company_id": profile.company_id, "name": profile.name,
            "scope_json": _scope_json(profile.scope), "frequency": profile.frequency,
            "formats_json": _set_json(profile.formats), "channels_json": _set_json(profile.channels),
            "recipient_ids_json": _set_json(profile.recipient_ids), "active": profile.active,
        })

    def get_delivery_profile(self, profile_id: str) -> DeliveryProfile | None:
        with self.engine.connect() as conn:
            r = conn.execute(select(delivery_profiles).where(delivery_profiles.c.profile_id == profile_id)).mappings().first()
        if not r:
            return None
        return DeliveryProfile(r["profile_id"], r["company_id"], r["name"], scope_from_dict(json.loads(r["scope_json"])), r["frequency"], frozenset(json.loads(r["formats_json"])), frozenset(json.loads(r["channels_json"])), frozenset(json.loads(r["recipient_ids_json"])), bool(r["active"]))

    def list_delivery_profiles(self, company_id: str) -> list[DeliveryProfile]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(delivery_profiles).where(delivery_profiles.c.company_id == company_id).order_by(delivery_profiles.c.name)).mappings().all()
        return [DeliveryProfile(r["profile_id"], r["company_id"], r["name"], scope_from_dict(json.loads(r["scope_json"])), r["frequency"], frozenset(json.loads(r["formats_json"])), frozenset(json.loads(r["channels_json"])), frozenset(json.loads(r["recipient_ids_json"])), bool(r["active"])) for r in rows]

    def append_audit(self, audit: AuditRecord) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(audits).values(
                audit_id=audit.audit_id, company_id=audit.company_id, entity_type=audit.entity_type,
                entity_id=audit.entity_id, action=audit.action, actor=audit.actor,
                old_value_json=audit.old_value_json, new_value_json=audit.new_value_json,
                reason=audit.reason, effective_date=audit.effective_date, changed_at=audit.changed_at,
            ))

    def list_audit(self, company_id: str | None = None) -> list[AuditRecord]:
        q = select(audits)
        if company_id:
            q = q.where(audits.c.company_id == company_id)
        q = q.order_by(audits.c.changed_at.desc(), audits.c.audit_id.desc())
        with self.engine.connect() as conn:
            rows = conn.execute(q).mappings().all()
        return [AuditRecord(
            audit_id=r["audit_id"], company_id=r["company_id"], entity_type=r["entity_type"], entity_id=r["entity_id"],
            action=r["action"], actor=r["actor"], old_value_json=r["old_value_json"], new_value_json=r["new_value_json"],
            reason=r["reason"], effective_date=r["effective_date"], changed_at=_as_utc(r["changed_at"]),
        ) for r in rows]

    # --- chemical routes -------------------------------------------------------------------------

    def replace_chemical_graph(self, nodes, edges) -> None:
        """Load the governed graph wholesale. Used to seed, and to re-seed from a corrected file.

        A whole-graph replace rather than a merge: the seed file is the source of truth for a
        version, and a partial merge would leave edges from a previous version behind with no way to
        tell them apart.
        """
        with self.engine.begin() as connection:
            connection.execute(delete(chemical_route_edges))
            connection.execute(delete(chemical_nodes))
            for node in nodes:
                connection.execute(insert(chemical_nodes).values(
                    code=node.code, display_name=node.display_name,
                    price_tracked=bool(node.price_tracked), root_class=node.root_class,
                    is_root=bool(node.is_root), aliases_json=json.dumps(list(node.aliases)),
                    note=node.note, active=bool(node.active)))
            for edge in edges:
                connection.execute(insert(chemical_route_edges).values(
                    edge_id="|".join(edge.key), route_id=edge.route_id,
                    from_chemical=edge.from_chemical, relation=edge.relation,
                    to_chemical=edge.to_chemical,
                    technical_qualifier=edge.technical_qualifier,
                    note=edge.note, active=bool(edge.active)))

    def list_chemical_nodes(self) -> list:
        from ..chemical_routes import ChemicalNode

        with self.engine.begin() as connection:
            rows = connection.execute(select(chemical_nodes)).mappings().all()
        return [
            ChemicalNode(
                code=row["code"], display_name=row["display_name"],
                price_tracked=bool(row["price_tracked"]), root_class=row["root_class"],
                is_root=bool(row["is_root"]),
                aliases=tuple(json.loads(row["aliases_json"] or "[]")),
                note=row["note"] or "", active=bool(row["active"]),
            )
            for row in rows
        ]

    def list_chemical_route_edges(self) -> list:
        from ..chemical_routes import RouteEdge

        with self.engine.begin() as connection:
            rows = connection.execute(select(chemical_route_edges)).mappings().all()
        return [
            RouteEdge(
                route_id=row["route_id"], from_chemical=row["from_chemical"],
                relation=row["relation"], to_chemical=row["to_chemical"],
                technical_qualifier=row["technical_qualifier"] or "",
                note=row["note"] or "", active=bool(row["active"]),
            )
            for row in rows
        ]

    def set_chemical_edge_active(self, edge_id: str, active: bool) -> None:
        """Deactivate, never delete: a route that shaped a forecast is part of the record."""
        with self.engine.begin() as connection:
            connection.execute(update(chemical_route_edges)
                               .where(chemical_route_edges.c.edge_id == edge_id)
                               .values(active=bool(active)))

    def set_chemical_price_tracked(self, code: str, price_tracked: bool) -> None:
        with self.engine.begin() as connection:
            connection.execute(update(chemical_nodes)
                               .where(chemical_nodes.c.code == code)
                               .values(price_tracked=bool(price_tracked)))
