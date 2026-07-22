"""Mist collector data and error models."""
from dataclasses import dataclass


class MistError(RuntimeError):
    pass


class MistAuthenticationError(MistError):
    pass


class MistAuthorizationError(MistError):
    pass


class MistPaginationError(MistError):
    pass


@dataclass(frozen=True)
class MistConfig:
    base_url: str
    organization_id: str
    api_token: str
    discovery_interval_seconds: int = 21600
    collection_interval_seconds: int = 120
    timeout_seconds: float = 20
