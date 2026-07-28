"""Typed PaperCut configuration and safe failure categories."""
from dataclasses import dataclass


@dataclass(frozen=True)
class PaperCutConfig:
    base_url: str
    authorization_key: str
    customer: str
    site: str
    verify_tls: bool = True
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


class PaperCutAuthenticationError(PaperCutError):
    category = "authentication"


class PaperCutTLSError(PaperCutError):
    category = "tls"


class PaperCutTimeoutError(PaperCutError):
    category = "timeout"


class PaperCutUnreachableError(PaperCutError):
    category = "unreachable"


class PaperCutMalformedResponseError(PaperCutError):
    category = "invalid_response"
