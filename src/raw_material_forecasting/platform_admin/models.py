from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Iterable


def _norm(values: Iterable[str]) -> frozenset[str]:
    return frozenset(str(value).strip() for value in values if str(value).strip())


@dataclass(frozen=True)
class EntitlementScope:
    application_access: bool = False
    features: frozenset[str] = frozenset()
    chemicals: frozenset[str] = frozenset()
    geographies: frozenset[str] = frozenset()
    incoterms: frozenset[str] = frozenset()
    packaging: frozenset[str] = frozenset()
    forecast_horizon_months: int = 0
    report_formats: frozenset[str] = frozenset()
    delivery_frequencies: frozenset[str] = frozenset()
    delivery_channels: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.forecast_horizon_months < 0:
            raise ValueError("forecast_horizon_months cannot be negative")
        for field_name in (
            "features",
            "chemicals",
            "geographies",
            "incoterms",
            "packaging",
            "report_formats",
            "delivery_frequencies",
            "delivery_channels",
        ):
            object.__setattr__(self, field_name, _norm(getattr(self, field_name)))


@dataclass(frozen=True)
class CompanyEntitlement:
    entitlement_id: str
    company_id: str
    scope: EntitlementScope
    effective_from: datetime
    effective_to: datetime | None
    package_id: str | None

    def is_active(self, at: datetime) -> bool:
        return self.effective_from <= at and (self.effective_to is None or at < self.effective_to)


@dataclass(frozen=True)
class TemporaryEntitlement:
    entitlement_id: str
    company_id: str
    scope: EntitlementScope
    effective_from: datetime
    effective_to: datetime
    reason_type: str
    reason: str
    created_by: str
    approved_by: str

    def is_active(self, at: datetime) -> bool:
        return self.effective_from <= at < self.effective_to


@dataclass(frozen=True)
class Subscription:
    """One product a customer holds: what was bought, for which group and regions, from when.

    CR-2026-11-26-01. Until this there was one entitlement per customer and its scope was ticked by
    hand, so nothing in the application knew what had been SOLD - Direct Access or the Price
    Monitor, Flexible Foam or Full Chain, one region or three. A subscription is that sentence,
    kept as it was said; `products.scope_for` works out what it opens.

    A customer can hold several (Stefan, 26 November 2026: sold separately), which is why each
    carries its own dates rather than borrowing the customer's contract dates.
    """
    subscription_id: str
    company_id: str
    product: str
    edition: str
    material_group: str
    regions: frozenset[str]
    effective_from: datetime
    effective_to: datetime | None
    # US dollars taken off the ANNUAL list fee for this customer (v1.2.142). The amount for the
    # term is worked out from it and the dates, never stored: see `products.price`.
    annual_discount: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "regions", _norm(self.regions))
        object.__setattr__(self, "annual_discount", int(self.annual_discount or 0))

    def is_active(self, at: datetime) -> bool:
        return self.effective_from <= at and (self.effective_to is None or at < self.effective_to)


def _union_dimension(left: frozenset[str], right: frozenset[str]) -> frozenset[str]:
    if "*" in left or "*" in right:
        return frozenset({"*"})
    return left | right


def merge_scope(left: EntitlementScope, right: EntitlementScope) -> EntitlementScope:
    return EntitlementScope(
        application_access=left.application_access or right.application_access,
        features=_union_dimension(left.features, right.features),
        chemicals=_union_dimension(left.chemicals, right.chemicals),
        geographies=_union_dimension(left.geographies, right.geographies),
        incoterms=_union_dimension(left.incoterms, right.incoterms),
        packaging=_union_dimension(left.packaging, right.packaging),
        forecast_horizon_months=max(left.forecast_horizon_months, right.forecast_horizon_months),
        report_formats=_union_dimension(left.report_formats, right.report_formats),
        delivery_frequencies=_union_dimension(left.delivery_frequencies, right.delivery_frequencies),
        delivery_channels=_union_dimension(left.delivery_channels, right.delivery_channels),
    )


def effective_scope(
    permanent: CompanyEntitlement,
    temporaries: Iterable[TemporaryEntitlement],
    at: datetime | None = None,
) -> EntitlementScope:
    at = at or datetime.now(timezone.utc)
    result = permanent.scope if permanent.is_active(at) else EntitlementScope()
    for temporary in temporaries:
        if temporary.company_id == permanent.company_id and temporary.is_active(at):
            result = merge_scope(result, temporary.scope)
    return result


def _dimension_contains(parent: frozenset[str], child: frozenset[str]) -> bool:
    if not child:
        return True
    if "*" in parent:
        return True
    if "*" in child:
        return "*" in parent
    return child.issubset(parent)


def scope_contains(parent: EntitlementScope, child: EntitlementScope) -> bool:
    if child.application_access and not parent.application_access:
        return False
    if child.forecast_horizon_months > parent.forecast_horizon_months:
        return False
    dimensions = (
        (parent.features, child.features),
        (parent.chemicals, child.chemicals),
        (parent.geographies, child.geographies),
        (parent.incoterms, child.incoterms),
        (parent.packaging, child.packaging),
        (parent.report_formats, child.report_formats),
        (parent.delivery_frequencies, child.delivery_frequencies),
        (parent.delivery_channels, child.delivery_channels),
    )
    return all(_dimension_contains(p, c) for p, c in dimensions)


def user_scope_fits(company: EntitlementScope, user: EntitlementScope) -> bool:
    """Whether a user's scope may be saved under this company. `*` on a user means "all of ours".

    `scope_contains` refuses a `*` wherever the company's own scope is a list, and until
    v1.2.133 that was the rule for a user too. It could not survive a company whose scope is
    worked out from a product: Flexible Foam names four materials and thirty-five feedstocks by
    code, the user form offers none of those codes, and `*` - the one way left to say "whatever
    they bought" - was refused. A user of a product customer could not have been created at all.

    A user's `*` cannot widen anything. What a user sees is `intersect_scope(company, user)`,
    worked out at every sign-in, and `*` there takes the company's side. So `*` is allowed, and a
    scope that NAMES something the company does not hold - a chemical, a market, a forecast
    horizon longer than the company's - is refused exactly as before. The one scope that carries
    a horizon it does not mean is `ALL_OF_THE_COMPANYS`, whose number stands for "the company's"
    and is cut to it at sign-in.
    """
    if user.application_access and not company.application_access:
        return False
    if user != ALL_OF_THE_COMPANYS and user.forecast_horizon_months > company.forecast_horizon_months:
        return False
    dimensions = (
        (company.features, user.features),
        (company.chemicals, user.chemicals),
        (company.geographies, user.geographies),
        (company.incoterms, user.incoterms),
        (company.packaging, user.packaging),
        (company.report_formats, user.report_formats),
        (company.delivery_frequencies, user.delivery_frequencies),
        (company.delivery_channels, user.delivery_channels),
    )
    return all("*" in u or _dimension_contains(c, u) for c, u in dimensions)


# The scope of a user who is to see whatever their company holds. See `user_scope_fits`.
ALL_OF_THE_COMPANYS = EntitlementScope(
    application_access=True, features=frozenset({"*"}), chemicals=frozenset({"*"}),
    geographies=frozenset({"*"}), incoterms=frozenset({"*"}), packaging=frozenset({"*"}),
    forecast_horizon_months=36, report_formats=frozenset({"*"}),
    delivery_frequencies=frozenset({"*"}), delivery_channels=frozenset({"*"}))

from dataclasses import asdict
from datetime import date


@dataclass(frozen=True)
class Company:
    company_id: str
    name: str
    status: str
    contract_effective_from: date
    contract_effective_to: date | None


@dataclass(frozen=True)
class CommercialPackage:
    package_id: str
    name: str
    scope: EntitlementScope
    active: bool = True


@dataclass(frozen=True)
class UserAccess:
    user_id: str
    company_id: str
    email: str
    role: str
    scope: EntitlementScope
    active: bool = True
    # Who this is (v1.2.142). An address alone does not say: s.hermes@htcglobal.asia under
    # Sheela Foam is Stefan Hermes looking at what their buyers are given.
    name: str = ""


@dataclass(frozen=True)
class Recipient:
    recipient_id: str
    company_id: str
    email: str
    name: str
    external_exception: bool = False
    exception_reason: str | None = None
    # Switched off rather than deleted, so a delivery profile that named this recipient, and every
    # audit record about them, still reads (Stefan, 31 October 2026).
    active: bool = True


@dataclass(frozen=True)
class DeliveryProfile:
    profile_id: str
    company_id: str
    name: str
    scope: EntitlementScope
    frequency: str
    formats: frozenset[str]
    channels: frozenset[str]
    recipient_ids: frozenset[str]
    active: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "formats", _norm(self.formats))
        object.__setattr__(self, "channels", _norm(self.channels))
        object.__setattr__(self, "recipient_ids", _norm(self.recipient_ids))


@dataclass(frozen=True)
class AuditRecord:
    audit_id: str
    company_id: str
    entity_type: str
    entity_id: str
    action: str
    actor: str
    old_value_json: str
    new_value_json: str
    reason: str
    effective_date: date
    changed_at: datetime


# A company in one of these is not trading. The other two - ACTIVE and TRIAL - are.
CLOSED_COMPANY_STATUS = "CLOSED"
COMPANY_STATUS_OFF = frozenset({"SUSPENDED", CLOSED_COMPANY_STATUS})


def is_on(record: Any, *, at: datetime) -> bool:
    """Whether this record is in force. The one reader of the seven different ways of being off."""
    if isinstance(record, Company):
        return record.status not in COMPANY_STATUS_OFF
    if isinstance(record, (CompanyEntitlement, TemporaryEntitlement, Subscription)):
        return record.is_active(at)
    return bool(getattr(record, "active", True))


def is_deleted(record: Any, *, at: datetime) -> bool:
    """Whether this record has been deleted - for an entitlement or a grant, whether it has ended.

    Not the opposite of `is_on`, and the difference is the reason this exists. A suspended
    customer is off and has not been deleted. An entitlement that starts next month is off and
    has not been deleted. Both have to stay on the page: the first because somebody will lift the
    suspension, the second because it was typed in this morning. What leaves the page (v1.2.124,
    Stefan, 24 November 2026: "Yes, hide them") is what `switched_off` produces, and only that.

    An entitlement deleted and one that ran out read the same, because deleting one IS ending it
    now. So the pages call those two kinds "ended", which is true of both.
    """
    if isinstance(record, Company):
        return record.status == CLOSED_COMPANY_STATUS
    if isinstance(record, (CompanyEntitlement, TemporaryEntitlement, Subscription)):
        return record.effective_to is not None and record.effective_to <= at
    return not bool(getattr(record, "active", True))


def switched_off(record: Any, *, at: datetime) -> Any:
    """The same record, switched off. The one writer of those seven ways.

    An entitlement ends rather than flips: `effective_to` becomes now, so the history of what the
    customer was entitled to and until when stays exactly as it was. Nothing here removes a row.
    """
    if isinstance(record, Company):
        return replace(record, status=CLOSED_COMPANY_STATUS)
    if isinstance(record, CompanyEntitlement):
        return replace(record, effective_to=at)
    if isinstance(record, (TemporaryEntitlement, Subscription)):
        return replace(record, effective_to=at)
    if isinstance(record, (CommercialPackage, UserAccess, Recipient, DeliveryProfile)):
        return replace(record, active=False)
    raise TypeError(f"no rule for switching off a {type(record).__name__}")


def scope_to_dict(scope: EntitlementScope) -> dict:
    return {
        "application_access": scope.application_access,
        "features": sorted(scope.features),
        "chemicals": sorted(scope.chemicals),
        "geographies": sorted(scope.geographies),
        "incoterms": sorted(scope.incoterms),
        "packaging": sorted(scope.packaging),
        "forecast_horizon_months": scope.forecast_horizon_months,
        "report_formats": sorted(scope.report_formats),
        "delivery_frequencies": sorted(scope.delivery_frequencies),
        "delivery_channels": sorted(scope.delivery_channels),
    }


def scope_from_dict(data: dict) -> EntitlementScope:
    return EntitlementScope(
        application_access=bool(data.get("application_access", False)),
        features=frozenset(data.get("features", [])),
        chemicals=frozenset(data.get("chemicals", [])),
        geographies=frozenset(data.get("geographies", [])),
        incoterms=frozenset(data.get("incoterms", [])),
        packaging=frozenset(data.get("packaging", [])),
        forecast_horizon_months=int(data.get("forecast_horizon_months", 0)),
        report_formats=frozenset(data.get("report_formats", [])),
        delivery_frequencies=frozenset(data.get("delivery_frequencies", [])),
        delivery_channels=frozenset(data.get("delivery_channels", [])),
    )


def _intersect_dimension(left: frozenset[str], right: frozenset[str]) -> frozenset[str]:
    if "*" in left:
        return right
    if "*" in right:
        return left
    return left & right


def intersect_scope(left: EntitlementScope, right: EntitlementScope) -> EntitlementScope:
    return EntitlementScope(
        application_access=left.application_access and right.application_access,
        features=_intersect_dimension(left.features, right.features),
        chemicals=_intersect_dimension(left.chemicals, right.chemicals),
        geographies=_intersect_dimension(left.geographies, right.geographies),
        incoterms=_intersect_dimension(left.incoterms, right.incoterms),
        packaging=_intersect_dimension(left.packaging, right.packaging),
        forecast_horizon_months=min(left.forecast_horizon_months, right.forecast_horizon_months),
        report_formats=_intersect_dimension(left.report_formats, right.report_formats),
        delivery_frequencies=_intersect_dimension(left.delivery_frequencies, right.delivery_frequencies),
        delivery_channels=_intersect_dimension(left.delivery_channels, right.delivery_channels),
    )


@dataclass(frozen=True)
class RuntimeAccess:
    company: Company
    user: UserAccess
    scope: EntitlementScope


@dataclass(frozen=True)
class UserCredential:
    """What a customer signs in with. One row per application user, keyed on their email.

    Kept apart from `UserAccess` on purpose: the screens list users, the audit writes them and the
    entitlement checks read them, and none of those three has any business carrying a password. A
    user with no row here cannot sign in at all, which is what an account looks like between
    Stefan creating it and handing over its first password.

    `must_change` is true while the password is the one that was handed over. The sign-in then
    makes the customer replace it before the application opens, so a password that has been in an
    email or a message is never the one guarding the account.
    """

    email: str
    user_id: str
    password_hash: str
    password_salt: str
    password_iterations: int
    password_set_at: datetime
    must_change: bool = True


@dataclass(frozen=True)
class PlatformAdminCredential:
    email: str
    password_hash: str
    password_salt: str
    password_iterations: int
    password_set_at: datetime
