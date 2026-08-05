"""FortiGate API configuration, results, and classified failures."""
from __future__ import annotations

from dataclasses import dataclass, field


class FortiGateError(RuntimeError):
    category = "invalid_response"

    def __init__(self, message, *, remediation="", evidence=None):
        super().__init__(message)
        self.remediation = remediation
        self.evidence = dict(evidence or {})

    def diagnostic_payload(self):
        payload = {"category": self.category, "message": str(self)}
        if self.remediation:
            payload["remediation"] = self.remediation
        if self.evidence:
            payload["tls"] = self.evidence
        return payload


class FortiGateCredentialError(FortiGateError):
    category = "authentication_failed"


class FortiGatePermissionError(FortiGateError):
    category = "permission_denied"


class FortiGateTLSError(FortiGateError):
    category = "tls_trust_failure"


class FortiGateCertificateExpiredError(FortiGateTLSError):
    category = "tls_certificate_expired"


class FortiGateHostnameMismatchError(FortiGateTLSError):
    category = "tls_hostname_mismatch"


class FortiGateIncompleteChainError(FortiGateTLSError):
    category = "tls_incomplete_chain"


class FortiGatePrivateCAError(FortiGateTLSError):
    category = "tls_untrusted_private_ca"


class FortiGateTimeoutError(FortiGateError):
    category = "timeout"


class FortiGateUnreachableError(FortiGateError):
    category = "tcp_connection_failed"


@dataclass(frozen=True)
class FortiGateConfig:
    base_url: str
    api_token: str
    customer: str
    site: str
    verify_tls: bool = True
    ca_bundle: str | None = None
    timeout_seconds: float = 20
    discovery_interval_seconds: int = 21600
    collection_interval_seconds: int = 60
    max_retries: int = 2
    wan_interfaces: tuple["WanInterface", ...] = ()


@dataclass(frozen=True)
class WanInterface:
    name: str
    role: str
    display_name: str


@dataclass
class EndpointResult:
    name: str
    data: object = None
    available: bool = True
    category: str = "success"
    message: str = ""


@dataclass
class CollectionResult:
    endpoints: dict[str, EndpointResult] = field(default_factory=dict)

    @property
    def partial(self):
        return any(not item.available for item in self.endpoints.values())
