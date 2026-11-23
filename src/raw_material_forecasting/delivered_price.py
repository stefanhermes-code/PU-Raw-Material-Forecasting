from dataclasses import dataclass


@dataclass(frozen=True)
class DeliveredPriceBreakdown:
    usd_per_tonne: float
    target_currency_per_tonne: float
    components: dict[str, float]


def calculate_delivered_price(
    market_price_per_tonne: float,
    freight_per_tonne: float = 0.0,
    packaging_per_tonne: float = 0.0,
    insurance_per_tonne: float = 0.0,
    duty_per_tonne: float = 0.0,
    other_route_cost_per_tonne: float = 0.0,
    source_currency_per_usd: float = 1.0,
    target_currency_per_usd: float = 1.0,
) -> DeliveredPriceBreakdown:
    if source_currency_per_usd <= 0 or target_currency_per_usd <= 0:
        raise ValueError("FX rates must be positive")
    if market_price_per_tonne < 0:
        raise ValueError("market_price_per_tonne must be non-negative")

    components_source = {
        "market_price_per_tonne": float(market_price_per_tonne),
        "freight_per_tonne": float(freight_per_tonne),
        "packaging_per_tonne": float(packaging_per_tonne),
        "insurance_per_tonne": float(insurance_per_tonne),
        "duty_per_tonne": float(duty_per_tonne),
        "other_route_cost_per_tonne": float(other_route_cost_per_tonne),
    }
    total_source = sum(components_source.values())
    usd_total = total_source / source_currency_per_usd
    target_total = usd_total * target_currency_per_usd

    components_usd = {
        name: value / source_currency_per_usd
        for name, value in components_source.items()
    }
    return DeliveredPriceBreakdown(
        usd_per_tonne=usd_total,
        target_currency_per_tonne=target_total,
        components=components_usd,
    )
