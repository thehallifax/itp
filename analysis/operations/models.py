"""Typed, vendor-neutral operational intelligence models."""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


CATEGORIES = {"Network", "Wireless", "Firewall", "Server", "Printing", "Storage",
              "Collector", "Inventory", "Lifecycle", "Security", "Virtualisation"}
SEVERITIES = {"Critical", "High", "Medium", "Low", "Info", "Unknown"}
SEVERITY_BASE = {"Critical": 90, "High": 75, "Medium": 55, "Low": 35, "Info": 15,
                 "Unknown": 0}


def parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class OperationalItem:
    kind: str
    rule_id: str
    title: str
    category: str
    severity: str
    priority: int
    canonical_id: str = ""
    device: str = ""
    site_id: str = ""
    site: str = ""
    summary: str = ""
    recommended_action: str = ""
    impact: str = ""
    reason: str = ""
    suggested_action: str = ""
    evidence: dict = field(default_factory=dict)
    deployment_id: str = ""
    domain: str = ""
    provider: str = ""
    object_kind: str = ""
    object_id: str = ""
    cluster_id: str = ""
    host_id: str = ""
    workload_id: str = ""
    confidence: str = ""
    source_finding_id: str = ""
    first_observed: str | None = None
    last_observed: str | None = None
    affected_service_ids: tuple[str, ...] = ()
    id: str = ""

    def __post_init__(self):
        if self.kind not in {"issue", "risk", "recommendation"}:
            raise ValueError("operational item kind must be issue, risk, or recommendation")
        if self.category not in CATEGORIES or self.severity not in SEVERITIES:
            raise ValueError("operational item has unsupported category or severity")
        object.__setattr__(self, "priority", max(0, min(100, int(self.priority))))
        if not self.id:
            identity = "|".join((self.rule_id, self.kind, self.canonical_id or self.device,
                                 self.site_id or self.site, self.title))
            object.__setattr__(self, "id", "ops:" + hashlib.sha256(identity.encode()).hexdigest()[:20])

    def to_dict(self):
        value = asdict(self)
        value["affected_service_ids"] = list(self.affected_service_ids)
        return value


@dataclass
class OperationalContext:
    now: datetime
    assets: list = field(default_factory=list)
    source_states: dict = field(default_factory=dict)
    reconciliations: list = field(default_factory=list)
    signals: dict = field(default_factory=dict)
    settings: dict = field(default_factory=dict)

    def age_days(self, value):
        parsed = parse_time(value)
        return None if parsed is None else max(0, (self.now - parsed).total_seconds() / 86400)


def priority(severity, weight=0):
    """Return an explainable score: severity base plus a documented rule weight."""
    return max(0, min(100, SEVERITY_BASE[severity] + int(weight)))
