"""Mist collector data and error models."""
from __future__ import annotations

from dataclasses import dataclass


class MistError(RuntimeError):
    category = "api_failure"

    @property
    def diagnostic_payload(self):
        return {"category": self.category, "message": str(self)}


class MistAuthenticationError(MistError):
    category = "authentication_failed"


class MistAuthorizationError(MistError):
    category = "insufficient_permissions"


class MistPaginationError(MistError):
    category = "pagination_failed"


class MistConfigurationError(MistError):
    category = "configuration_incomplete"


class MistOrganizationError(MistError):
    category = "organization_not_found"


@dataclass(frozen=True)
class MistConfig:
    base_url: str
    organization_id: str
    api_token: str
    discovery_interval_seconds: int = 21600
    collection_interval_seconds: int = 120
    timeout_seconds: float = 20
    verify_tls: bool = True
    ca_bundle: str | None = None
