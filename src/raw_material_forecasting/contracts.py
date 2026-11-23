from dataclasses import dataclass
from datetime import date


_RIGHTS = {
    "HTC_OWNED": {"research": True, "operational": True},
    "PUBLIC_REDISTRIBUTABLE": {"research": True, "operational": True},
    "PUBLIC_REPRINT_WITH_ATTRIBUTION": {"research": True, "operational": False},
    "PUBLIC_CITATION_ONLY": {"research": True, "operational": False},
    "LICENSED_HTC_INTERNAL": {"research": True, "operational": True},
    "FORMER_EMPLOYER": {"research": False, "operational": False},
    "UNLICENSED_SUBSCRIPTION": {"research": False, "operational": False},
    "UNKNOWN": {"research": False, "operational": False},
}


@dataclass(frozen=True)
class SourceRights:
    classification: str

    def __post_init__(self):
        key = self.classification.strip().upper()
        if key not in _RIGHTS:
            raise ValueError(f"unknown rights classification: {self.classification}")
        object.__setattr__(self, "classification", key)

    @property
    def eligible_for_model(self) -> bool:
        return _RIGHTS[self.classification]["research"]

    @property
    def eligible_for_operational(self) -> bool:
        return _RIGHTS[self.classification]["operational"]


@dataclass(frozen=True)
class CanonicalIdentity:
    product_family: str
    grade: str
    origin: str
    destination: str
    price_basis: str
    packaging: str
    currency: str

    def __post_init__(self):
        for field_name in (
            "product_family", "grade", "origin", "destination",
            "price_basis", "packaging", "currency"
        ):
            value = getattr(self, field_name)
            if not str(value).strip():
                raise ValueError(f"{field_name} is required")

    @property
    def key(self) -> str:
        return " | ".join([
            self.product_family, self.grade, self.origin, self.destination,
            self.price_basis, self.packaging, self.currency,
        ])


@dataclass(frozen=True)
class MarketObservation:
    identity: CanonicalIdentity
    observation_date: date
    publication_date: date
    price_per_tonne: float
    source_id: str
    rights: SourceRights
    observation_role: str = "MARKET_TARGET"

    def __post_init__(self):
        if not self.rights.eligible_for_model:
            raise ValueError("source is not eligible for model use")
        if self.price_per_tonne <= 0:
            raise ValueError("price_per_tonne must be positive")
        if self.publication_date < self.observation_date:
            raise ValueError("publication_date cannot precede observation_date")


@dataclass(frozen=True)
class PurchaseTransaction:
    identity: CanonicalIdentity
    transaction_date: date
    price_per_tonne: float
    quantity_tonnes: float
    source_id: str
    observation_role: str = "DELIVERED_PRICE_VALIDATION"

    def __post_init__(self):
        if self.price_per_tonne <= 0:
            raise ValueError("price_per_tonne must be positive")
        if self.quantity_tonnes <= 0:
            raise ValueError("quantity_tonnes must be positive")
