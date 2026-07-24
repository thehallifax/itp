"""Typed, provider-neutral state-history domain models."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


SCHEMA_VERSION = 1
CHANGE_TYPES = frozenset({
    "entity_added", "entity_removed", "field_changed", "status_changed",
})
PIPELINE_STATUSES = frozenset({"success", "partial", "failed", "skipped"})


class ObservationCompleteness(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ObservationScope:
    """Removal authority and source coverage for one site/domain scope."""

    site_id: str
    domain: str
    completeness: str
    expected_sources: tuple[str, ...] = ()
    observed_sources: tuple[str, ...] = ()
    failed_sources: tuple[str, ...] = ()
    skipped_sources: tuple[str, ...] = ()
    expected_providers: tuple[str, ...] = ()
    observed_providers: tuple[str, ...] = ()
    failed_providers: tuple[str, ...] = ()
    skipped_providers: tuple[str, ...] = ()
    warning_details: tuple[str, ...] = ()

    def __post_init__(self):
        if not self.site_id or not self.domain:
            raise ValueError("observation scope requires site_id and domain")
        try:
            ObservationCompleteness(self.completeness)
        except ValueError as exc:
            raise ValueError(
                f"unsupported observation completeness: {self.completeness}") from exc
        for name in ("expected_sources", "observed_sources", "failed_sources",
                     "skipped_sources", "expected_providers",
                     "observed_providers", "failed_providers",
                     "skipped_providers", "warning_details"):
            value = getattr(self, name)
            if tuple(sorted(set(value))) != value:
                raise ValueError(f"observation scope {name} must be unique and sorted")
        covered = set(self.observed_sources) | set(self.failed_sources) | \
            set(self.skipped_sources)
        if self.expected_sources and not covered <= set(self.expected_sources):
            raise ValueError("scope source coverage contains an unexpected source")
        provider_covered = set(self.observed_providers) | \
            set(self.failed_providers) | set(self.skipped_providers)
        if self.expected_providers and not provider_covered <= \
                set(self.expected_providers):
            raise ValueError("scope provider coverage contains an unexpected provider")
        if self.completeness == ObservationCompleteness.COMPLETE.value:
            if (self.failed_sources or self.skipped_sources
                    or self.failed_providers or self.skipped_providers):
                raise ValueError("complete scope cannot contain failed or skipped sources")
            if self.expected_sources and set(self.observed_sources) != \
                    set(self.expected_sources):
                raise ValueError("complete scope must observe every expected source")
            if self.expected_providers and set(self.observed_providers) != \
                    set(self.expected_providers):
                raise ValueError("complete scope must observe every expected provider")

    @property
    def authority(self):
        return (self.site_id, self.domain)

    def to_dict(self):
        value = asdict(self)
        for name in ("expected_sources", "observed_sources", "failed_sources",
                     "skipped_sources", "expected_providers",
                     "observed_providers", "failed_providers",
                     "skipped_providers", "warning_details"):
            value[name] = list(value[name])
        return value

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise ValueError("pipeline scope metadata must be a mapping")
        names = ("expected_sources", "observed_sources", "failed_sources",
                 "skipped_sources", "expected_providers",
                 "observed_providers", "failed_providers",
                 "skipped_providers", "warning_details")
        try:
            return cls(**{**value, **{
                name: tuple(sorted(set(value.get(name) or []))) for name in names}})
        except TypeError as exc:
            raise ValueError("invalid pipeline scope metadata") from exc


@dataclass(frozen=True)
class PipelineRun:
    """One canonical pipeline execution and its explicit scope coverage."""

    run_id: str
    started_at: str
    completed_at: str
    status: str
    scopes: tuple[ObservationScope, ...]
    canonical_output: str = ""
    warning_details: tuple[str, ...] = ()
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self):
        if not self.run_id or not self.started_at or not self.completed_at:
            raise ValueError(
                "pipeline run requires run_id, started_at and completed_at")
        if self.status not in PIPELINE_STATUSES:
            raise ValueError(f"unsupported pipeline run status: {self.status}")
        authorities = [value.authority for value in self.scopes]
        if len(authorities) != len(set(authorities)):
            raise ValueError("pipeline run contains duplicate scope authority")
        if tuple(sorted(self.warning_details)) != self.warning_details:
            raise ValueError("pipeline warning details must be sorted")

    def to_dict(self):
        scopes = [value.to_dict() for value in self.scopes]
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "status": self.status,
            "canonical_output": self.canonical_output,
            "source_coverage": sorted({
                source for scope in self.scopes
                for source in scope.observed_sources}),
            "provider_coverage": sorted({
                provider for scope in self.scopes
                for provider in scope.observed_providers}),
            "site_coverage": sorted({scope.site_id for scope in self.scopes}),
            "domain_coverage": sorted({scope.domain for scope in self.scopes}),
            "expected_scopes": [
                scope for scope in scopes if scope["expected_sources"]],
            "observed_scopes": [
                scope for scope in scopes if scope["observed_sources"]],
            "failed_scopes": [
                scope for scope in scopes
                if scope["completeness"] == ObservationCompleteness.FAILED.value
                or scope["failed_sources"]],
            "skipped_scopes": [
                scope for scope in scopes
                if scope["completeness"] == ObservationCompleteness.SKIPPED.value
                or scope["skipped_sources"]],
            "scopes": scopes,
            "warning_details": list(self.warning_details),
        }

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise ValueError("pipeline run metadata must be a mapping")
        try:
            scopes = tuple(sorted(
                (ObservationScope.from_dict(scope)
                 for scope in value.get("scopes", [])),
                key=lambda scope: scope.authority))
            return cls(
                run_id=str(value.get("run_id") or ""),
                started_at=str(value.get("started_at") or ""),
                completed_at=str(value.get("completed_at") or ""),
                status=str(value.get("status") or ""),
                scopes=scopes,
                canonical_output=str(value.get("canonical_output") or ""),
                warning_details=tuple(sorted(set(
                    value.get("warning_details") or []))),
                schema_version=int(value.get("schema_version", SCHEMA_VERSION)),
            )
        except (TypeError, ValueError) as exc:
            if isinstance(exc, ValueError) and str(exc).startswith(
                    ("unsupported", "pipeline run", "observation scope",
                     "complete scope", "scope source")):
                raise
            raise ValueError("invalid pipeline run metadata") from exc


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
    run_id: str = ""
    completeness: str = ObservationCompleteness.COMPLETE.value
    warning_details: tuple[str, ...] = ()

    def to_dict(self):
        value = asdict(self)
        value["entities"] = [entity.to_dict() for entity in self.entities]
        value["warning_details"] = list(self.warning_details)
        return value

    @classmethod
    def from_dict(cls, value):
        try:
            entities = tuple(EntityState(**entity) for entity in value["entities"])
            return cls(**{**value, "entities": entities,
                          "warning_details": tuple(
                              value.get("warning_details") or [])})
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
    run_id: str = ""
    completeness: str = ObservationCompleteness.COMPLETE.value
    removals_suppressed: int = 0

    def to_dict(self):
        value = asdict(self)
        value["changes"] = [change.to_dict() for change in self.changes]
        value["change_count"] = len(self.changes)
        return value


@dataclass(frozen=True)
class CaptureResult:
    """Persisted outcome of one idempotent pipeline capture."""

    capture_id: str
    run_id: str
    status: str
    pipeline_run: dict[str, Any]
    scope_results: tuple[dict[str, Any], ...] = ()
    snapshots: tuple[dict[str, Any], ...] = ()
    change_sets: tuple[dict[str, Any], ...] = ()
    warning_details: tuple[str, ...] = ()
    error: str = ""
    schema_version: int = SCHEMA_VERSION

    def to_dict(self):
        return {
            **asdict(self),
            "scope_results": list(self.scope_results),
            "snapshots": list(self.snapshots),
            "change_sets": list(self.change_sets),
            "warning_details": list(self.warning_details),
            "change_count": sum(
                int(value.get("change_count", 0))
                for value in self.change_sets),
        }

    @classmethod
    def from_dict(cls, value):
        try:
            value = {key: item for key, item in value.items()
                     if key != "change_count"}
            return cls(
                **{**value,
                   "scope_results": tuple(value.get("scope_results") or []),
                   "snapshots": tuple(value.get("snapshots") or []),
                   "change_sets": tuple(value.get("change_sets") or []),
                   "warning_details": tuple(
                       value.get("warning_details") or [])})
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid persisted capture result") from exc
