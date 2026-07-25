"""Typed diagnostic result contract."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


STATUSES = frozenset({"pass", "warn", "fail", "skip", "unavailable"})
SEVERITIES = frozenset({"info", "warning", "error"})


@dataclass(frozen=True)
class DiagnosticCheck:
    check_id: str
    category: str
    subject: str
    status: str
    severity: str
    summary: str
    detail: str = ""
    remediation: str = ""
    command: str = ""
    metadata: dict = field(default_factory=dict)
    duration_ms: int = 0
    exception_type: str = ""

    def __post_init__(self):
        if self.status not in STATUSES:
            raise ValueError(f"invalid diagnostic status: {self.status}")
        if self.severity not in SEVERITIES:
            raise ValueError(f"invalid diagnostic severity: {self.severity}")

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class DoctorReport:
    generated_at: str
    deployment_identity: str
    mode: dict
    checks: tuple[DiagnosticCheck, ...]
    errors: tuple[dict, ...] = ()
    schema_version: int = 1

    @property
    def summary(self):
        return {
            status: sum(check.status == status for check in self.checks)
            for status in ("pass", "warn", "fail", "skip", "unavailable")
        }

    @property
    def overall_status(self):
        if self.summary["fail"]:
            return "fail"
        if self.summary["warn"] or self.summary["unavailable"]:
            return "warn"
        return "pass"

    def exit_code(self, strict=False):
        if self.summary["fail"]:
            return 1
        if strict and (self.summary["warn"] or self.summary["unavailable"]):
            return 1
        return 0

    def to_dict(self, strict=False):
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "deployment_identity": self.deployment_identity,
            "mode": {**self.mode, "strict": bool(strict)},
            "overall_status": self.overall_status,
            "exit_code": self.exit_code(strict),
            "exit_code_meaning": {
                "0": "no failures; warnings allowed unless strict",
                "1": "one or more failures, or warnings in strict mode",
                "2": "invalid usage or unknown connector",
                "3": "doctor failed before producing a valid report",
            },
            "summary": self.summary,
            "checks": [check.to_dict() for check in self.checks],
            "errors": list(self.errors),
        }
