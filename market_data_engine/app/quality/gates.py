from __future__ import annotations
from datetime import datetime


def enforce_available_as_of(available_from: datetime, cutoff: datetime) -> bool:
    if available_from > cutoff:
        raise ValueError("record was not available at cutoff")
    return True


def ensure_no_identity_inference(fields: dict) -> bool:
    # This gate is intentionally conservative: explicit None/UNKNOWN values are valid.
    # Inference is prevented upstream by resolver rules; this function validates the
    # representation rather than manufacturing defaults.
    prohibited_markers = {"ASSUMED", "INFERRED", "GUESSED"}
    for key in ("packaging", "commercial_basis", "grade"):
        value = fields.get(key)
        if isinstance(value, str) and value.upper() in prohibited_markers:
            raise ValueError(f"{key} contains an inferred marker")
    return True
