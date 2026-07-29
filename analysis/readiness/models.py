"""Typed, vendor-neutral readiness state."""
from __future__ import annotations

from dataclasses import asdict, dataclass


SEMANTIC_STATES = (
    "not_configured",
    "waiting_first_collection",
    "unavailable",
    "healthy",
    "warning",
    "critical",
)


@dataclass(frozen=True)
class ReadinessState:
    state: str
    reason: str
    configured: bool
    enabled: bool
    first_run_completed: bool
    last_success: str | None
    stale: bool
    display_label: str
    operator_action: str

    def __post_init__(self):
        if self.state not in SEMANTIC_STATES:
            raise ValueError(f"unsupported readiness state: {self.state}")

    def to_dict(self):
        return asdict(self)
