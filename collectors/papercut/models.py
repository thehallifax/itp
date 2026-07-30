"""Typed PaperCut configuration and safe failure categories."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PaperCutConfig:
    base_url: str
    authorization_key: str
    customer: str
    site: str
    verify_tls: bool = True
    ca_bundle: str | None = None
    timeout_seconds: float = 20
    discovery_interval_seconds: int = 21600
    collection_interval_seconds: int = 60
    max_retries: int = 2
    disk_warning_percent: float = 85
    jvm_warning_percent: float = 85
    held_jobs_warning: int = 25
    upgrade_assurance_warning_days: int = 90
    uptime_advisory_days: int = 180
    customer_name: str = ""
    site_name: str = ""
    deployment_id: str = ""


class PaperCutError(RuntimeError):
    category = "invalid_response"

    def __init__(self, message, *, diagnostic=None):
        super().__init__(message)
        self.diagnostic = dict(diagnostic or {})

    def diagnostic_payload(self):
        return {
            "category": self.category,
            "message": str(self),
            **self.diagnostic,
        }


class PaperCutAuthenticationError(PaperCutError):
    category = "authentication"


class PaperCutAuthorizationError(PaperCutError):
    category = "authorization"


class PaperCutTLSError(PaperCutError):
    category = "tls"


class PaperCutCertificateExpiredError(PaperCutTLSError):
    category = "tls_certificate_expired"


class PaperCutHostnameMismatchError(PaperCutTLSError):
    category = "tls_hostname_mismatch"


class PaperCutUnknownIssuerError(PaperCutTLSError):
    category = "tls_unknown_issuer"


class PaperCutDNSError(PaperCutError):
    category = "dns"


class PaperCutConnectionError(PaperCutError):
    category = "connection"


class PaperCutTimeoutError(PaperCutError):
    category = "timeout"


class PaperCutUnreachableError(PaperCutError):
    category = "unreachable"


class PaperCutMalformedResponseError(PaperCutError):
    category = "invalid_response"


class PaperCutWrongEndpointError(PaperCutError):
    category = "wrong_endpoint"


class PaperCutRedirectError(PaperCutError):
    category = "redirect"


class PaperCutUnsupportedResponseError(PaperCutError):
    category = "unsupported_response"


class PaperCutApplicationError(PaperCutError):
    category = "application_error"


class PaperCutInvalidRequestError(PaperCutError):
    category = "invalid_request"
