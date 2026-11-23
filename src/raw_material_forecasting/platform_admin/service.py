from __future__ import annotations

import json
from dataclasses import is_dataclass, replace
from datetime import date, datetime, timezone
from uuid import uuid4

from .models import (
    AuditRecord,
    CommercialPackage,
    Company,
    CompanyEntitlement,
    DeliveryProfile,
    EntitlementScope,
    Recipient,
    TemporaryEntitlement,
    UserAccess,
    UserCredential,
    RuntimeAccess,
    PlatformAdminCredential,
    effective_scope,
    intersect_scope,
    scope_contains,
    scope_to_dict,
    switched_off,
)
from .repository import PlatformAdminRepository
from .auth import (
    PLATFORM_ADMIN_EMAIL,
    hash_password,
    is_platform_admin_email,
    normalise_email,
    password_complaint,
    verify_password,
)


class PlatformAdminError(RuntimeError):
    pass


class EntitlementViolation(PlatformAdminError):
    pass


class RecipientDomainViolation(PlatformAdminError):
    pass


class SignInRefused(PlatformAdminError):
    """The email and password did not open an account.

    One exception with one message for every reason the pair failed - no such user, wrong
    password, no password handed out yet. A sign-in screen that says "no account with that
    address" tells a stranger which of our customers' addresses are real.
    """


class ConfigurationError(PlatformAdminError):
    pass


def _jsonable(value):
    if isinstance(value, EntitlementScope):
        return scope_to_dict(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (set, frozenset, tuple, list)):
        return sorted(_jsonable(v) for v in value)
    if is_dataclass(value):
        return {name: _jsonable(getattr(value, name)) for name in value.__dataclass_fields__}
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    return value


def _json(value) -> str:
    if value is None:
        return "{}"
    return json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"))


def _dimension_allows(allowed: frozenset[str], requested: frozenset[str]) -> bool:
    return not requested or "*" in allowed or requested.issubset(allowed)


class PlatformAdminService:
    def __init__(self, repo: PlatformAdminRepository, now_fn=None):
        self.repo = repo
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def _now(self) -> datetime:
        value = self._now_fn()
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _audit(self, *, company_id: str, entity_type: str, entity_id: str, action: str,
               actor: str, old_value, new_value, reason: str, effective_date: date | None = None) -> AuditRecord:
        if not actor.strip():
            raise ConfigurationError("actor is required for audited changes")
        if not reason.strip():
            raise ConfigurationError("reason is required for audited changes")
        now = self._now()
        record = AuditRecord(
            audit_id=str(uuid4()), company_id=company_id, entity_type=entity_type,
            entity_id=entity_id, action=action, actor=actor.strip(),
            old_value_json=_json(old_value), new_value_json=_json(new_value),
            reason=reason.strip(), effective_date=effective_date or now.date(), changed_at=now,
        )
        self.repo.append_audit(record)
        return record

    def platform_admin_password_is_set(self) -> bool:
        return self.repo.get_platform_admin_credential() is not None

    def set_initial_platform_admin_password(self, email: str, password: str) -> None:
        if not is_platform_admin_email(email):
            raise ConfigurationError("only Stefan Hermes may initialize Platform Admin access")
        if self.repo.get_platform_admin_credential() is not None:
            raise ConfigurationError("the Platform Admin password has already been set")
        if not password:
            raise ConfigurationError("password cannot be empty")
        digest = hash_password(password)
        credential = PlatformAdminCredential(
            email=PLATFORM_ADMIN_EMAIL,
            password_hash=digest.password_hash,
            password_salt=digest.password_salt,
            password_iterations=digest.password_iterations,
            password_set_at=self._now(),
        )
        self.repo.set_platform_admin_credential(credential)
        self._audit(
            company_id="__platform__",
            entity_type="platform_admin_auth",
            entity_id=PLATFORM_ADMIN_EMAIL,
            action="INITIAL_PASSWORD_SET",
            actor=PLATFORM_ADMIN_EMAIL,
            old_value=None,
            new_value={"email": PLATFORM_ADMIN_EMAIL, "password_set": True},
            reason="Initial Platform Admin password set",
        )

    def verify_platform_admin_password(self, email: str, password: str) -> bool:
        if not is_platform_admin_email(email):
            return False
        credential = self.repo.get_platform_admin_credential()
        if credential is None or not is_platform_admin_email(credential.email):
            return False
        return verify_password(
            password,
            password_hash=credential.password_hash,
            password_salt=credential.password_salt,
            password_iterations=credential.password_iterations,
        )

    def create_company(self, company: Company, *, actor: str, reason: str) -> Company:
        old = self.repo.get_company(company.company_id)
        self.repo.upsert_company(company)
        self._audit(
            company_id=company.company_id, entity_type="company", entity_id=company.company_id,
            action="UPDATE" if old else "CREATE", actor=actor, old_value=old,
            new_value=company, reason=reason,
        )
        return company

    def save_package(self, package: CommercialPackage, *, actor: str, reason: str) -> CommercialPackage:
        old = self.repo.get_package(package.package_id)
        self.repo.upsert_package(package)
        self._audit(
            company_id="HTC", entity_type="commercial_package", entity_id=package.package_id,
            action="UPDATE" if old else "CREATE", actor=actor, old_value=old,
            new_value=package, reason=reason,
        )
        return package

    def set_permanent_entitlement(self, entitlement: CompanyEntitlement, *, actor: str, reason: str) -> CompanyEntitlement:
        if not self.repo.get_company(entitlement.company_id):
            raise ConfigurationError(f"unknown company: {entitlement.company_id}")
        old = self.repo.get_company_entitlement(entitlement.company_id)
        self.repo.set_company_entitlement(entitlement)
        self._audit(
            company_id=entitlement.company_id, entity_type="company_entitlement",
            entity_id=entitlement.entitlement_id, action="UPDATE" if old else "SET",
            actor=actor, old_value=old, new_value=entitlement, reason=reason,
        )
        return entitlement

    def create_entitlement_from_package(self, *, company_id: str, package_id: str, entitlement_id: str,
                                        effective_from: datetime, actor: str, reason: str) -> CompanyEntitlement:
        package = self.repo.get_package(package_id)
        if package is None or not package.active:
            raise ConfigurationError(f"active package not found: {package_id}")
        entitlement = CompanyEntitlement(
            entitlement_id=entitlement_id, company_id=company_id, scope=package.scope,
            effective_from=effective_from, effective_to=None, package_id=package_id,
        )
        return self.set_permanent_entitlement(entitlement, actor=actor, reason=reason)

    def grant_temporary_entitlement(self, entitlement: TemporaryEntitlement, *, actor: str, reason: str) -> TemporaryEntitlement:
        if entitlement.effective_to <= entitlement.effective_from:
            raise ConfigurationError("temporary entitlement must expire after it starts")
        if not self.repo.get_company(entitlement.company_id):
            raise ConfigurationError(f"unknown company: {entitlement.company_id}")
        old = next((x for x in self.repo.list_temporary_entitlements(entitlement.company_id)
                    if x.entitlement_id == entitlement.entitlement_id), None)
        self.repo.upsert_temporary_entitlement(entitlement)
        self._audit(
            company_id=entitlement.company_id, entity_type="temporary_entitlement",
            entity_id=entitlement.entitlement_id, action="UPDATE" if old else "GRANT",
            actor=actor, old_value=old, new_value=entitlement, reason=reason,
        )
        return entitlement

    def effective_company_scope(self, company_id: str, *, at: datetime | None = None) -> EntitlementScope:
        permanent = self.repo.get_company_entitlement(company_id)
        if permanent is None:
            raise ConfigurationError(f"company has no permanent entitlement: {company_id}")
        return effective_scope(permanent, self.repo.list_temporary_entitlements(company_id), at=at or self._now())


    def resolve_user_access(self, email: str, *, at: datetime | None = None) -> RuntimeAccess:
        user = self.repo.find_user_by_email(email)
        if user is None or not user.active:
            raise EntitlementViolation("no active application user is configured for this identity")
        company = self.repo.get_company(user.company_id)
        if company is None or company.status not in {"ACTIVE", "TRIAL"}:
            raise EntitlementViolation("the user's company is not active")
        today = (at or self._now()).date()
        if today < company.contract_effective_from or (company.contract_effective_to is not None and today > company.contract_effective_to):
            raise EntitlementViolation("the company's contract is not active")
        parent = self.effective_company_scope(user.company_id, at=at or self._now())
        scope = intersect_scope(parent, user.scope)
        if not scope.application_access:
            raise EntitlementViolation("application access is not included in the active entitlement")
        return RuntimeAccess(company=company, user=user, scope=scope)

    # ----------------------------------------------------------------- signing a customer in

    def user_password_is_set(self, email: str) -> bool:
        return self.repo.get_user_credential(email) is not None

    def set_user_password(self, email: str, password: str, *, actor: str, reason: str,
                          must_change: bool = True) -> None:
        """Give an application user a password. Stefan hands out the first one; this writes it.

        `must_change` defaults to true because the first password travels - it is said on a call or
        written in a mail - and the account has to stop being guarded by it the moment the customer
        is in. The customer's own change calls this with false.
        """
        user = self.repo.find_user_by_email(email)
        if user is None:
            raise ConfigurationError(f"no application user is configured for {normalise_email(email)}")
        if not user.active:
            raise ConfigurationError("this application user is switched off")
        complaint = password_complaint(password)
        if complaint:
            raise ConfigurationError(complaint)
        digest = hash_password(password)
        existed = self.repo.get_user_credential(user.email) is not None
        self.repo.set_user_credential(UserCredential(
            email=normalise_email(user.email),
            user_id=user.user_id,
            password_hash=digest.password_hash,
            password_salt=digest.password_salt,
            password_iterations=digest.password_iterations,
            password_set_at=self._now(),
            must_change=must_change,
        ))
        # The password itself is never written here, and nor is anything derived from it. What the
        # history records is that this account's password was set, by whom, and whether the holder
        # still has to replace it.
        self._audit(
            company_id=user.company_id, entity_type="user_auth", entity_id=user.user_id,
            action="PASSWORD_RESET" if existed else "PASSWORD_SET", actor=actor,
            old_value={"password_set": existed},
            new_value={"password_set": True, "must_change": must_change},
            reason=reason,
        )

    def password_change_required(self, email: str) -> bool:
        credential = self.repo.get_user_credential(email)
        return credential is not None and credential.must_change

    def change_own_password(self, email: str, current_password: str, new_password: str) -> None:
        """The customer replacing their own password. Proving the old one is part of it."""
        credential = self.repo.get_user_credential(email)
        if credential is None or not verify_password(
            current_password,
            password_hash=credential.password_hash,
            password_salt=credential.password_salt,
            password_iterations=credential.password_iterations,
        ):
            raise SignInRefused("that is not the current password for this account")
        if new_password == current_password:
            raise ConfigurationError("the new password has to be a different one")
        user = self.repo.find_user_by_email(email)
        if user is None:
            raise SignInRefused("that email and password do not open an account")
        self.set_user_password(
            user.email, new_password, actor=normalise_email(user.email),
            reason="Password changed by the account holder", must_change=False,
        )

    def sign_in(self, email: str, password: str, *, at: datetime | None = None) -> RuntimeAccess:
        """Email and password in, the signed-in customer's entitlement out.

        The password is checked first and refused with one message whatever went wrong. Only then
        does the entitlement get resolved, so the reasons that are a customer's own business - a
        contract that has run out, an entitlement without application access - are said only to
        somebody who has proved they hold the account.
        """
        credential = self.repo.get_user_credential(email)
        if credential is None or not verify_password(
            password,
            password_hash=credential.password_hash,
            password_salt=credential.password_salt,
            password_iterations=credential.password_iterations,
        ):
            raise SignInRefused("that email and password do not open an account")
        return self.resolve_user_access(credential.email, at=at)

    def create_user(self, user: UserAccess, *, actor: str, reason: str) -> UserAccess:
        parent = self.effective_company_scope(user.company_id)
        if not scope_contains(parent, user.scope):
            raise EntitlementViolation("user scope exceeds the company's effective entitlement")
        old = self.repo.get_user(user.user_id)
        self.repo.upsert_user(user)
        self._audit(
            company_id=user.company_id, entity_type="user", entity_id=user.user_id,
            action="UPDATE" if old else "CREATE", actor=actor, old_value=old,
            new_value=user, reason=reason,
        )
        return user

    def set_approved_domains(self, company_id: str, domains: set[str], *, actor: str, reason: str) -> set[str]:
        if not self.repo.get_company(company_id):
            raise ConfigurationError(f"unknown company: {company_id}")
        normalized = {d.strip().lower() for d in domains if d.strip()}
        old = self.repo.list_approved_domains(company_id)
        self.repo.replace_approved_domains(company_id, normalized)
        self._audit(
            company_id=company_id, entity_type="approved_domains", entity_id=company_id,
            action="SET", actor=actor, old_value=old, new_value=normalized, reason=reason,
        )
        return normalized

    def add_recipient(self, recipient: Recipient, *, actor: str, reason: str,
                      allow_external: bool = False, exception_reason: str | None = None) -> Recipient:
        if "@" not in recipient.email:
            raise RecipientDomainViolation("recipient email is invalid")
        domain = recipient.email.rsplit("@", 1)[1].strip().lower()
        approved = self.repo.list_approved_domains(recipient.company_id)
        is_approved = domain in approved
        saved = recipient
        if not is_approved:
            if not allow_external:
                raise RecipientDomainViolation("recipient email domain is not approved for this company")
            if not exception_reason or not exception_reason.strip():
                raise RecipientDomainViolation("external recipient exception requires an explicit reason")
            saved = replace(recipient, external_exception=True, exception_reason=exception_reason.strip())
        else:
            saved = replace(recipient, external_exception=False, exception_reason=None)
        old = self.repo.get_recipient(saved.recipient_id)
        self.repo.upsert_recipient(saved)
        self._audit(
            company_id=saved.company_id, entity_type="recipient", entity_id=saved.recipient_id,
            action="UPDATE" if old else "CREATE", actor=actor, old_value=old,
            new_value=saved, reason=reason,
        )
        return saved

    def save_delivery_profile(self, profile: DeliveryProfile, *, actor: str, reason: str) -> DeliveryProfile:
        parent = self.effective_company_scope(profile.company_id)
        if not scope_contains(parent, profile.scope):
            raise EntitlementViolation("delivery profile scope exceeds the company's effective entitlement")
        if not _dimension_allows(parent.delivery_frequencies, frozenset({profile.frequency})):
            raise EntitlementViolation("delivery frequency is not included in the company entitlement")
        if not _dimension_allows(parent.report_formats, profile.formats):
            raise EntitlementViolation("delivery format is not included in the company entitlement")
        if not _dimension_allows(parent.delivery_channels, profile.channels):
            raise EntitlementViolation("delivery channel is not included in the company entitlement")
        known_recipient_ids = {r.recipient_id for r in self.repo.list_recipients(profile.company_id)}
        if not profile.recipient_ids.issubset(known_recipient_ids):
            raise EntitlementViolation("delivery profile includes recipients outside the company")
        old = self.repo.get_delivery_profile(profile.profile_id)
        self.repo.upsert_delivery_profile(profile)
        self._audit(
            company_id=profile.company_id, entity_type="delivery_profile", entity_id=profile.profile_id,
            action="UPDATE" if old else "CREATE", actor=actor, old_value=old,
            new_value=profile, reason=reason,
        )
        return profile

    # What each kind of record is called in the change history, which of its fields names it, and
    # how it is written back. The three go together, so they are written together.
    _KINDS = {
        "Company": ("company", "company_id", "upsert_company"),
        "CommercialPackage": ("commercial_package", "package_id", "upsert_package"),
        "CompanyEntitlement": ("company_entitlement", "entitlement_id", "set_company_entitlement"),
        "TemporaryEntitlement": ("temporary_entitlement", "entitlement_id", "upsert_temporary_entitlement"),
        "UserAccess": ("user", "user_id", "upsert_user"),
        "Recipient": ("recipient", "recipient_id", "upsert_recipient"),
        "DeliveryProfile": ("delivery_profile", "profile_id", "upsert_delivery_profile"),
    }

    def deactivate(self, record, *, actor: str, reason: str):
        """Switch a record off and keep the row. Platform Admin has no delete.

        Stefan, 31 October 2026, choosing what Delete means in the standard record structure. A
        company's entitlement, its users, its recipients and its deliveries all point at rows that
        have to stay readable, or the change history stops meaning anything.

        `switched_off` decides what off means for each kind - there is one of those, in `models`,
        read by the screens as well, so a row the page shows as off is a row this has switched off.

        Written straight to the store rather than through `create_company` and the rest: their
        checks exist to stop a scope being granted wider than the company's entitlement, and
        switching something off grants nothing. A delivery profile whose company entitlement has
        since narrowed could otherwise never be switched off, which is when you would want to.
        """
        kind = self._KINDS.get(type(record).__name__)
        if kind is None:
            raise ConfigurationError(f"no rule for switching off a {type(record).__name__}")
        entity_type, id_field, writer = kind
        now = self._now()
        off = switched_off(record, at=now)
        getattr(self.repo, writer)(off)
        self._audit(
            company_id=getattr(record, "company_id", "HTC"), entity_type=entity_type,
            entity_id=getattr(record, id_field), action="DEACTIVATE", actor=actor,
            old_value=record, new_value=off, reason=reason,
        )
        return off

    def list_audit(self, company_id: str | None = None) -> list[AuditRecord]:
        return self.repo.list_audit(company_id)
