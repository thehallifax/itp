"""Common collector contract."""
from abc import ABC, abstractmethod


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

    @abstractmethod
    def discover(self):
        """Discover targets and update durable collector state."""

    @abstractmethod
    def collect(self):
        """Perform or configure metric collection."""
