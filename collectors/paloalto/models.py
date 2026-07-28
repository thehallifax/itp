"""Typed configuration and safe PAN-OS collector failures."""
from __future__ import annotations

from dataclasses import dataclass, field


class PaloAltoError(RuntimeError):
    category = "invalid_response"


class PaloAltoCredentialError(PaloAltoError):
    category = "credential"


class PaloAltoPermissionError(PaloAltoError):
    category = "permission"


class PaloAltoTLSError(PaloAltoError):
    category = "tls"


class PaloAltoTimeoutError(PaloAltoError):
    category = "timeout"


class PaloAltoUnreachableError(PaloAltoError):
    category = "unreachable"


class PaloAltoUnsupportedError(PaloAltoError):
    category = "unsupported"


@dataclass(frozen=True)
class PaloAltoConfig:
    base_url: str
    api_key: str
    api_key_env: str
    customer: str
    site: str
    verify_tls: bool = True
    ca_bundle: str | None = None
    timeout_seconds: float = 20
    discovery_interval_seconds: int = 21600
    collection_interval_seconds: int = 60
    max_retries: int = 2
    expected_interfaces: tuple[str, ...] = ()
    collect_interfaces: bool = True
    collect_ha: bool = True
    collect_system_resources: bool = True
    collect_licenses: bool = True
    collect_content_versions: bool = True
    licence_expiry_days: int = 30
    wan_interfaces: tuple["WanInterface", ...] = ()
    content_warning_days: int = 30
    content_critical_days: int = 90
    customer_name: str = ""
    site_name: str = ""
    deployment_id: str = ""


@dataclass(frozen=True)
class WanInterface:
    name: str
    role: str
    display_name: str


@dataclass
class CapabilityResult:
    name: str
    data: object = None
    available: bool = True
    category: str = "success"
    message: str = ""
    duration_ms: int = 0
    retries: int = 0


@dataclass
class Snapshot:
    capabilities: dict[str, CapabilityResult] = field(default_factory=dict)

    @property
    def partial(self):
        return any(not value.available for key, value in self.capabilities.items()
                   if key != "system")
