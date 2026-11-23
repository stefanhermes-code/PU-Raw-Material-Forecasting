from collections import defaultdict
from app.domain.schemas import MarketReferencePoint
from app.reference.weighting import weighted_median

def compatible_observations(rows: list[dict], identity_key: str) -> list[dict]:
    return [r for r in rows if r.get("identity_key") == identity_key]

class ReferenceCalculator:
    def __init__(self):
        self._history: dict[str, list[MarketReferencePoint]] = defaultdict(list)

    def calculate(self, identity_key: str, observations) -> MarketReferencePoint:
        compatible = [o for o in observations if o.identity.identity_key == identity_key]
        seen_evidence = set()
        rows = []
        for observation in compatible:
            if observation.evidence_id in seen_evidence:
                continue
            seen_evidence.add(observation.evidence_id)
            rows.append(observation)
        if not rows:
            raise LookupError(f"no compatible observations for {identity_key}")
        value = weighted_median([(o.price, o.weight) for o in rows])
        point = MarketReferencePoint(identity_key=identity_key, reference_value=value, observation_ids=[o.observation_id for o in rows], version=len(self._history[identity_key])+1)
        self._history[identity_key].append(point)
        return point

    def latest(self, identity_key: str):
        return self._history.get(identity_key, [None])[-1]
