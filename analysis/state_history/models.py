"""Typed, provider-neutral state-history domain models."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


SCHEMA_VERSION = 1
CHANGE_TYPES = frozenset({
    "entity_added", "entity_removed", "field_changed", "status_changed",
})


@dataclass(frozen=True)
class EntityState:
    """One canonical entity at an observation boundary."""

    site_id: str
    domain: str
    entity_type: str
    entity_id: str
    state: dict[str, Any]
    observed_at: str
    collected_at: str | None = None
    source: str = ""
    provider: str = ""

    def __post_init__(self):
        if not all((self.site_id, self.domain, self.entity_type, self.entity_id)):
            raise ValueError(
                "entity state requires site_id, domain, entity_type and entity_id")
        if not isinstance(self.state, dict):
            raise ValueError("entity state payload must be a mapping")

    @property
    def identity(self):
        return (self.site_id, self.domain, self.entity_type, self.entity_id)

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class Observation:
    """Canonical input prepared for state-history processing."""

    observed_at: str
    entities: tuple[EntityState, ...]
    scopes: tuple[tuple[str, str], ...]
    collected_at: str | None = None
    source: str = ""
    provider: str = ""
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self):
        if not self.observed_at:
            raise ValueError("observation requires observed_at")
        if self.schema_version < 1:
            raise ValueError("observation schema_version must be positive")
        if len({value.identity for value in self.entities}) != len(self.entities):
            raise ValueError("observation contains duplicate canonical entity identities")


@dataclass(frozen=True)
class StateSnapshot:
    """Immutable state for one canonical site/domain scope."""

    snapshot_id: str
    observed_at: str
    site_id: str
    domain: str
    entities: tuple[EntityState, ...]
    collected_at: str | None = None
    source: str = ""
    provider: str = ""
    schema_version: int = SCHEMA_VERSION

    def to_dict(self):
        value = asdict(self)
        value["entities"] = [entity.to_dict() for entity in self.entities]
        return value

    @classmethod
    def from_dict(cls, value):
        try:
            entities = tuple(EntityState(**entity) for entity in value["entities"])
            return cls(**{**value, "entities": entities})
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid persisted state snapshot") from exc


@dataclass(frozen=True)
class StateChange:
    """One exact, explainable difference between canonical snapshots."""

    change_id: str
    previous_snapshot_id: str | None
    current_snapshot_id: str
    site_id: str
    domain: str
    entity_type: str
    entity_id: str
    change_type: str
    field_path: str
    previous_value: Any
    current_value: Any
    observed_at: str
    source: str = ""
    provider: str = ""
    severity: str | None = None

    def __post_init__(self):
        if self.change_type not in CHANGE_TYPES:
            raise ValueError(f"unsupported state change type: {self.change_type}")

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class ChangeSet:
    """Deterministic changes produced for one site/domain comparison."""

    change_set_id: str
    previous_snapshot_id: str | None
    current_snapshot_id: str
    site_id: str
    domain: str
    observed_at: str
    changes: tuple[StateChange, ...] = field(default_factory=tuple)
    schema_version: int = SCHEMA_VERSION

    def to_dict(self):
        value = asdict(self)
        value["changes"] = [change.to_dict() for change in self.changes]
        value["change_count"] = len(self.changes)
        return value
