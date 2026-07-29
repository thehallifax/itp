"""Typed Aruba Central configuration and safe failure categories."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ArubaCentralConfig:
    base_url: str
    token_url: str
    client_id: str
    client_secret: str
    refresh_token: str
    access_token: str
    auth_mode: str
    account_id: str
    customer: str
    site: str
    deployment_id: str
    customer_name: str = ""
    site_name: str = ""
    verify_tls: bool = True
    timeout_seconds: float = 20
    discovery_interval_seconds: int = 21600
    collection_interval_seconds: int = 120
    max_retries: int = 2
    endpoints: dict[str, str] = field(default_factory=dict)


class ArubaCentralError(RuntimeError):
    category = "invalid_response"


class ArubaCentralCredentialError(ArubaCentralError):
    category = "invalid_credentials"


class ArubaCentralTokenExpiredError(ArubaCentralError):
    category = "token_expired"


class ArubaCentralPermissionError(ArubaCentralError):
    category = "insufficient_permissions"


class ArubaCentralUnavailableError(ArubaCentralError):
    category = "api_unavailable"


class ArubaCentralUnsupportedError(ArubaCentralError):
    category = "unsupported_endpoint"


class ArubaCentralMalformedError(ArubaCentralError):
    category = "invalid_response"


class ArubaCentralNoDevicesError(ArubaCentralError):
    category = "no_devices"
