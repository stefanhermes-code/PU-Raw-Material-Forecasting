from app.domain.enums import EvidenceClass

def classify_record(data: dict) -> EvidenceClass:
    t = str(data.get("type", "")).lower()
    if t in {"supplier_quote", "actual_purchase"}:
        return EvidenceClass.VALIDATION_ONLY
    if t in {"maintenance", "planned_maintenance", "capacity_start", "duty_change"} and data.get("start_date"):
        return EvidenceClass.KNOWN_FUTURE_VARIABLE
    if t in {"outage", "force_majeure", "restart", "geopolitical_disruption", "producer_price_announcement",
             # Capacity and corporate direction. These describe who will be able to sell what, which
             # is regime information on a longer horizon than an outage. They were being quarantined:
             # the event parsers already emitted "capacity_addition" and nothing here recognised it,
             # so every capacity headline the engine read was thrown away unseen.
             "capacity_addition", "capacity_investment", "corporate_venture", "corporate_exit",
             # One plant's line in a weekly maintenance table: which plant, what capacity, running
             # at what load, down since when. The same regime information an outage headline
             # carries, with the numbers attached instead of implied.
             #
             # v1.2.80 emitted this type and nothing here knew it, so every row was quarantined -
             # the identical fault the capacity_addition note above describes, four comment lines
             # away, repeated eleven months later. `test_every_event_type_a_parser_emits_is_known`
             # now fails the build instead of waiting for someone to notice.
             "plant_status"}:
        # A producer announcing a settlement, listed or spot price is regime information about the
        # market, not a governed market price: it carries no Incoterm and is one seller's quote.
        return EvidenceClass.EVENT_REGIME_INFORMATION
    if t in {"feature", "feedstock", "demand", "operating_rate", "macro", "capacity_utilisation", "producer_margin"}:
        return EvidenceClass.LEADING_EXPLANATORY_VARIABLE
    if t in {"customs_unit_value", "trade_unit_value", "index"}:
        return EvidenceClass.HISTORICAL_LAGGING_MARKET_INDICATOR
    if t == "price" and data.get("product") and data.get("price") is not None and data.get("currency") and (data.get("geography") or data.get("market_geography")):
        return EvidenceClass.DIRECT_MARKET_PRICE
    return EvidenceClass.REJECT_QUARANTINE
