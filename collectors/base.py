"""Common collector contract."""
from abc import ABC, abstractmethod


class CollectorSkipped(RuntimeError):
    """A configured collector could not execute in the current runtime."""

    def __init__(self, reason):
        self.reason = str(reason)
        super().__init__(self.reason)


class RuntimePlacementCollector:
    """Observable placeholder for an enabled runtime-ineligible collector."""

    def __init__(self, name, configured_execution, current_runtime):
        self.name = name
        self.execution = configured_execution
        self.current_runtime = current_runtime
        self.discovery_interval = 43200
        self.collection_interval = 30

    def _skip(self):
        raise CollectorSkipped(
            f"configured_for_{self.execution}_runtime;"
            f"current_runtime={self.current_runtime}")

    discover = _skip
    collect = _skip


class BaseCollector(ABC):
    """Authentication, discovery, and collection boundary for one source.

    Implementations may adapt vendor responses to the shared telemetry contract,
    but persistence, analysis, reporting, and dashboards stay downstream.
    """
    name = ""
    discovery_interval = 43200
    collection_interval = 30
    execution = "either"
    schema_version = 1
    lifecycle = (
        "discover",
        "collect",
        "normalise",
        "validate",
        "write",
        "health",
        "summary",
    )

    @abstractmethod
    def discover(self):
        """Discover targets and update durable collector state."""

    @abstractmethod
    def collect(self):
        """Perform or configure metric collection."""

    def normalise(self, result):
        """Return source output ready for framework telemetry normalisation."""
        return result

    def validate(self, result):
        """Return source output accepted by collector-specific validation."""
        return result

    def write(self, points):
        """Write points through the configured framework writer."""
        writer = getattr(self, "writer", None)
        if writer is None:
            raise RuntimeError(f"{self.name or 'collector'} has no writer")
        return writer.write(points)

    def health(self):
        """Return no collector-owned health; the scheduler owns health output."""

    def summary(self, result):
        """Return a deterministic lifecycle summary."""
        if isinstance(result, dict):
            return dict(result)
        return {"result": result}
