from __future__ import annotations
from dataclasses import dataclass
from datetime import date

@dataclass(frozen=True)
class TradeMeasure:
    product_code: str
    measure_type: str
    origin: str
    destination: str
    exporter: str
    effective_from: date
    effective_to: date | None
    rate_pct: float
    rate_per_tonne: float
    source_ref: str

@dataclass(frozen=True)
class TradeMeasureCost:
    total_per_tonne: float
    components: dict[str,float]


def resolve_trade_measures(measures, *, product_code:str, origin:str, destination:str, exporter:str, on_date:date):
    base=[m for m in measures if m.product_code==product_code and m.origin==origin and m.destination==destination and m.effective_from<=on_date and (m.effective_to is None or on_date<=m.effective_to)]
    exact=[m for m in base if m.exporter==exporter]
    specific_types={m.measure_type for m in exact}
    fallback=[m for m in base if m.exporter=='ALL_OTHERS' and m.measure_type not in specific_types]
    return tuple(exact+fallback)


def calculate_trade_measure_cost(customs_value_per_tonne:float, measures)->TradeMeasureCost:
    if customs_value_per_tonne<0: raise ValueError('customs value must be non-negative')
    components={}
    for m in measures:
        amount=customs_value_per_tonne*float(m.rate_pct)/100.0+float(m.rate_per_tonne)
        components[m.measure_type]=components.get(m.measure_type,0.0)+amount
    return TradeMeasureCost(sum(components.values()),components)
