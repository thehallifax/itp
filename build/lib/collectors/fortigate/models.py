"""FortiGate API configuration, results, and classified failures."""
from dataclasses import dataclass, field


class FortiGateError(RuntimeError):
    category = "invalid_response"


class FortiGateCredentialError(FortiGateError):
    category = "credential"


class FortiGatePermissionError(FortiGateError):
    category = "permission"


class FortiGateTLSError(FortiGateError):
    category = "tls"


class FortiGateTimeoutError(FortiGateError):
    category = "timeout"


class FortiGateUnreachableError(FortiGateError):
    category = "unreachable"


@dataclass(frozen=True)
class FortiGateConfig:
    base_url: str
    api_token: str
    customer: str
    site: str
    verify_tls: bool = True
    timeout_seconds: float = 20
    discovery_interval_seconds: int = 21600
    collection_interval_seconds: int = 60
    max_retries: int = 2


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
