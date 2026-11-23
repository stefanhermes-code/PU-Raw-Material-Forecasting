def evidence_weight(source_reliability: int, identity_complete: bool, corroborated: bool = False) -> float:
    base = max(1, min(5, int(source_reliability))) / 5.0
    if not identity_complete:
        base *= 0.65
    if corroborated:
        base *= 1.15
    return round(base, 4)
