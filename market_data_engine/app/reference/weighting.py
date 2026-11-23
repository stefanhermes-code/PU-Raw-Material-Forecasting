def weighted_median(values: list[tuple[float, float]]) -> float:
    if not values:
        raise ValueError("weighted_median requires at least one observation")
    clean = sorted((float(v), max(0.0, float(w))) for v, w in values)
    total = sum(w for _, w in clean)
    if total <= 0:
        raise ValueError("total weight must be positive")
    threshold = total / 2.0
    cumulative = 0.0
    for value, weight in clean:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return clean[-1][0]
