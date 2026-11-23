from app.domain.enums import QualificationStatus
from app.domain.schemas import MarketIdentity

def qualify_direct_price(identity: MarketIdentity, price: float | None) -> QualificationStatus:
    if price is None or not identity.product_family or not identity.market_geography or not identity.currency or not identity.unit:
        return QualificationStatus.REJECTED
    if identity.incoterm_or_commercial_basis is None or identity.packaging is None or identity.product_grade_or_chemistry is None:
        return QualificationStatus.PARTIAL
    return QualificationStatus.QUALIFIED
