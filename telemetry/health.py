"""Framework-owned collector execution health projection."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CollectorHealth:
    collector: str
    runtime: str
    duration_ms: int
    status: str
    phase: str = "collect"
    points_generated: int = 0
    points_written: int = 0
    retries: int = 0
    execution_mode: str = "either"
    skip_reason: str = ""
    diagnostics: str = ""
    api_latency_ms: int = 0
    api_requests: int = 0

    @classmethod
    def from_outcome(cls, outcome, *, runtime="central",
                     execution_mode="either", phase="collect"):
        value = outcome.get("value")
        summary = value if isinstance(value, dict) else {}
        written = (
            summary.get("points_written", 0)
            if isinstance(summary, dict)
            else value if isinstance(value, int) else 0)
        generated = summary.get(
            "points_generated", summary.get("points_produced", written))
        return cls(
            collector=str(outcome.get("connector") or "unknown"),
            runtime=runtime,
            duration_ms=max(0, int(outcome.get("duration_ms") or 0)),
            status=str(outcome.get("status") or "failed"),
            phase=phase,
            points_generated=max(0, int(generated or 0)),
            points_written=max(0, int(written or 0)),
            retries=max(0, int(summary.get(
                "retries", summary.get("retry_count", 0)) or 0)),
            execution_mode=execution_mode,
            skip_reason=str(outcome.get("reason") or ""),
            diagnostics=str(outcome.get("exception_type") or ""),
            api_latency_ms=max(0, int(summary.get("api_latency_ms") or 0)),
            api_requests=max(0, int(summary.get("api_requests") or 0)),
        )

    def point(self):
        return {
            "measurement": "collector_health",
            "tags": {
                "collector": self.collector,
                "runtime": self.runtime,
                "status": self.status,
                "execution_mode": self.execution_mode,
                "phase": self.phase,
                "health_owner": "framework",
                "diagnostic_category": self.status,
            },
            "fields": {
                "success": self.status == "success",
                "partial": self.status == "partial",
                "duration_ms": self.duration_ms,
                "points_generated": self.points_generated,
                "points_written": self.points_written,
                "retry_count": self.retries,
                "skip_reason": self.skip_reason or "none",
                "diagnostics": self.diagnostics or "none",
                "api_latency_ms": self.api_latency_ms,
                "api_requests": self.api_requests,
                "error_count": 1 if self.status == "failed" else 0,
                "devices_returned": 0,
            },
        }
