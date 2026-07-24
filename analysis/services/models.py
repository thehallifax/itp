"""Typed canonical service-health models."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


SERVICE_NAMES = (
    "Internet", "Wireless", "Switching", "Printing", "Identity", "Compute",
    "Storage", "Voice", "Email", "Security", "Monitoring",
)
VIRTUALISATION_SERVICE_NAMES = (
    "Virtualisation Management Plane", "Hypervisor Cluster", "Compute Capacity",
    "Virtual Machine Hosting", "Shared Storage", "Workload Availability",
)
SERVICE_STATUSES = ("Healthy", "Warning", "Critical", "Unknown", "Not Enabled")
STATUS_SEVERITY = {
    "Healthy": "Info",
    "Warning": "Medium",
    "Critical": "Critical",
    "Unknown": "Info",
    "Not Enabled": "Info",
}


@dataclass(frozen=True)
class ServiceHealth:
    """One stable, vendor-neutral service evaluation."""

    service: str
    status: str
    summary: str
    affected_assets: tuple[str, ...] = ()
    affected_users: int | None = None
    severity: str = "Info"
    last_change: str | None = None
    evidence: tuple[dict, ...] = field(default_factory=tuple)

    def __post_init__(self):
        if self.service not in SERVICE_NAMES + VIRTUALISATION_SERVICE_NAMES:
            raise ValueError(f"unsupported canonical service: {self.service}")
        if self.status not in SERVICE_STATUSES:
            raise ValueError(f"unsupported service status: {self.status}")

    def to_dict(self):
        value = asdict(self)
        value["affected_assets"] = list(self.affected_assets)
        value["evidence"] = list(self.evidence)
        return value
