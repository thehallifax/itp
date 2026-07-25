"""Typed notification domain contracts."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum


class NotificationSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    RECOVERY = "recovery"


class NotificationDeliveryStatus(str, Enum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    SUPPRESSED = "suppressed"
    ACKNOWLEDGED = "acknowledged"


@dataclass(frozen=True)
class NotificationFingerprint:
    rule_id: str
    subject: str
    scope: str = ""

    @property
    def value(self):
        import hashlib
        import json
        material = json.dumps(
            [self.rule_id, self.subject, self.scope],
            separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(material.encode()).hexdigest()


@dataclass(frozen=True)
class NotificationRule:
    id: str
    title: str
    severity: NotificationSeverity
    source: str


@dataclass(frozen=True)
class NotificationEvent:
    id: str
    fingerprint: str
    rule_id: str
    severity: str
    title: str
    summary: str
    source: str
    subject: str
    first_seen: str
    last_seen: str
    occurrence_count: int = 1
    active: bool = True
    acknowledged: bool = False
    recovery_of: str = ""
    test: bool = False
    schema_version: int = 1

    def __post_init__(self):
        NotificationSeverity(self.severity)
        if self.occurrence_count < 1:
            raise ValueError("notification occurrence_count must be positive")

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class NotificationChannel:
    id: str
    channel_type: str
    enabled: bool = True


@dataclass(frozen=True)
class NotificationDelivery:
    id: str
    event_id: str
    channel: str
    status: str
    attempted_at: str
    delivered_at: str | None = None
    exception_type: str = ""
    detail: str = ""
    schema_version: int = 1

    def __post_init__(self):
        NotificationDeliveryStatus(self.status)

    def to_dict(self):
        return asdict(self)
