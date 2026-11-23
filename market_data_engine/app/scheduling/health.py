from __future__ import annotations
from collections import defaultdict

class SourceHealthTracker:
    def __init__(self, failure_threshold: int = 3):
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        self.failure_threshold = failure_threshold
        self._failures = defaultdict(int)

    def record_failure(self, source_id: str) -> None:
        self._failures[source_id] += 1

    def record_success(self, source_id: str) -> None:
        self._failures[source_id] = 0

    def failure_count(self, source_id: str) -> int:
        return self._failures[source_id]

    def is_healthy(self, source_id: str) -> bool:
        return self.failure_count(source_id) < self.failure_threshold
