"""Defensive read-only FortiGate HTTPS REST client."""
import asyncio
import socket
import ssl
from urllib.parse import urlsplit, urlunsplit

import httpx

from collectors.tls import (
    classify_certificate_issuer,
    connector_tls_context,
    inspect_tls_peer,
)
from .models import (
    EndpointResult,
    FortiGateCredentialError,
    FortiGateError,
    FortiGateCertificateExpiredError,
    FortiGateHostnameMismatchError,
    FortiGateIncompleteChainError,
    FortiGatePrivateCAError,
    FortiGatePermissionError,
    FortiGateTimeoutError,
    FortiGateTLSError,
    FortiGateUnreachableError,
)


class FortiGateClient:
    ENDPOINTS = {
        "system": "/api/v2/monitor/system/status",
        "resources": "/api/v2/monitor/system/resource/usage",
        "interfaces": "/api/v2/monitor/system/interface",
        "ha": "/api/v2/monitor/system/ha-peer",
    }

    def __init__(self, host, api_token, timeout=20, *, verify_tls=True,
                 ca_bundle=None,
                 max_retries=2, backoff_limit=4, client=None,
                 tls_inspector=inspect_tls_peer, sleep=asyncio.sleep):
        if not host or not api_token:
            raise ValueError("FORTIGATE_HOST and FORTIGATE_API_TOKEN are required")
        self.base_url = self.normalize_url(host)
        self._headers = {"Authorization": f"Bearer {api_token}", "Accept": "application/json",
                         "User-Agent": "itp-fortigate-collector/1.0"}
        self.max_retries = max(0, int(max_retries)); self.backoff_limit = backoff_limit
        self.sleep = sleep; self.api_requests = 0; self.retry_count = 0
        self.tls_inspector = tls_inspector
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(base_url=self.base_url,
            timeout=httpx.Timeout(timeout, connect=timeout),
            verify=connector_tls_context(verify_tls, ca_bundle),
            headers=self._headers)

    @staticmethod
    def _verification_error(exc):
        current = exc
        while current is not None:
            if isinstance(current, ssl.SSLCertVerificationError):
                return current
            if isinstance(current, ssl.SSLError):
                return current
            current = current.__cause__ or current.__context__
        return None

    async def _tls_error(self, exc):
        parsed = urlsplit(self.base_url)
        evidence = {}
        try:
            evidence = await asyncio.to_thread(
                self.tls_inspector, parsed.hostname, parsed.port or 443)
        except Exception:
            # The original verified handshake remains authoritative. Failure
            # to obtain optional leaf metadata must not mask it.
            evidence = {"host": parsed.hostname, "trust": "failed"}
        verification = self._verification_error(exc)
        verify_message = str(
            getattr(verification, "verify_message", "")
            or verification or exc)
        message = verify_message.casefold()
        code = getattr(verification, "verify_code", None)
        issuer = str(evidence.get("issuer") or "").casefold()
        subject = str(evidence.get("subject") or "").casefold()
        public_issuer = evidence.get("public_issuer")
        if public_issuer is None:
            public_issuer = classify_certificate_issuer(
                evidence.get("subject_attributes") or {"display": subject},
                evidence.get("issuer_attributes") or {"display": issuer})
        evidence["public_issuer"] = public_issuer
        evidence["verify_code"] = code
        evidence["verify_message"] = verify_message
        chain_failure = (
            code in {2, 18, 19, 20, 21}
            or "unable to get issuer" in message
            or "unable to verify the first certificate" in message
            or "issuer certificate" in message)
        if code == 10 or "expired" in message or evidence.get("expired"):
            return FortiGateCertificateExpiredError(
                "The FortiGate TLS certificate has expired.",
                remediation="Renew or replace the FortiGate server certificate.",
                evidence=evidence)
        if (code == 62 or "hostname mismatch" in message
                or evidence.get("hostname_match") is False):
            return FortiGateHostnameMismatchError(
                "The FortiGate certificate does not match the configured hostname.",
                remediation=(
                    "Use a hostname present in the certificate SANs or replace "
                    "the certificate with one covering this endpoint."),
                evidence=evidence)
        # Public issuer evidence takes precedence over generic self-signed or
        # issuer lookup codes. Some OpenSSL builds report a lone public leaf as
        # 18/19; that does not turn Let's Encrypt (or another public CA) private.
        if (public_issuer is True and chain_failure
                and evidence.get("hostname_match") is not False
                and not evidence.get("expired")):
            return FortiGateIncompleteChainError(
                "The FortiGate is not presenting the required public intermediate certificate.",
                remediation=(
                    "Install the full certificate chain on the FortiGate. No CA "
                    "import is required on the ITP host; do not import public "
                    "CA roots or intermediates into this deployment."),
                evidence=evidence)
        if public_issuer is False and chain_failure:
            return FortiGatePrivateCAError(
                "The FortiGate certificate is issued by an untrusted private CA.",
                remediation=(
                    "Import the private CA for this deployment with: ./itp "
                    "credentials ca add <certificate.pem> --deployment <id>"),
                evidence=evidence)
        return FortiGateTLSError(
            "The FortiGate certificate chain could not be verified inside the collector runtime.",
            remediation=(
                "Inspect the certificate chain and runtime trust store. Do not "
                "disable TLS verification."),
            evidence=evidence)

    @staticmethod
    def normalize_url(value):
        value = value.strip()
        if "://" not in value: value = "https://" + value
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("FORTIGATE_HOST must be a hostname or HTTPS URL")
        if parsed.path.rstrip("/"):
            raise ValueError(
                "FORTIGATE_HOST must not include an API path such as /api/v2")
        return urlunsplit(("https", parsed.netloc, "", "", ""))

    async def request(self, path, params=None):
        transient = {429, 500, 502, 503, 504}
        for attempt in range(self.max_retries + 1):
            self.api_requests += 1
            try:
                response = await self.client.get(self.base_url + "/" + path.lstrip("/"),
                                                 params=params, headers=self._headers)
            except httpx.TimeoutException as exc:
                if attempt == self.max_retries: raise FortiGateTimeoutError("FortiGate API request timed out") from exc
                response = None
            except httpx.ConnectError as exc:
                if self._verification_error(exc):
                    raise await self._tls_error(exc) from exc
                cause = exc
                while cause is not None:
                    if isinstance(cause, socket.gaierror):
                        error = FortiGateUnreachableError(
                            "FortiGate hostname could not be resolved")
                        error.category = "dns_failure"
                        raise error from exc
                    cause = cause.__cause__ or cause.__context__
                if attempt == self.max_retries: raise FortiGateUnreachableError("FortiGate API is unreachable") from exc
                response = None
            except httpx.TransportError as exc:
                text = type(exc).__name__.lower()
                if "ssl" in text or "certificate" in str(exc).lower():
                    raise await self._tls_error(exc) from exc
                if attempt == self.max_retries: raise FortiGateUnreachableError("FortiGate API transport failure") from exc
                response = None
            if response is not None:
                if response.status_code == 401: raise FortiGateCredentialError("FortiGate API authentication failed (HTTP 401)")
                if response.status_code == 403: raise FortiGatePermissionError("FortiGate API token lacks required read permission (HTTP 403)")
                if response.status_code < 400:
                    try: data = response.json()
                    except ValueError as exc: raise FortiGateError("FortiGate API returned invalid JSON") from exc
                    if not isinstance(data, (dict, list)): raise FortiGateError("FortiGate API returned an unexpected JSON type")
                    return data
                if response.status_code in (404, 405):
                    raise FortiGateError(f"FortiGate endpoint unsupported (HTTP {response.status_code})")
                if response.status_code not in transient:
                    raise FortiGateError(f"FortiGate API request failed with HTTP {response.status_code}")
                if attempt == self.max_retries:
                    raise FortiGateError(f"FortiGate API request failed after retries (HTTP {response.status_code})")
            self.retry_count += 1
            await self.sleep(min(2 ** attempt, self.backoff_limit))

    async def endpoint(self, name, *, optional=False):
        try:
            return EndpointResult(name=name, data=await self.paginated_request(self.ENDPOINTS[name]))
        except (FortiGateCredentialError, FortiGatePermissionError, FortiGateTLSError):
            raise
        except FortiGateError as exc:
            if not optional: raise
            category = "unsupported" if "unsupported" in str(exc).lower() else exc.category
            return EndpointResult(name=name, available=False, category=category, message=str(exc))

    async def paginated_request(self, path, *, max_pages=100):
        """Follow FortiOS ``next_idx`` pagination when an endpoint exposes it."""
        params = {}; combined = []
        for _page in range(max_pages):
            data = await self.request(path, params=params or None)
            if not isinstance(data, dict) or "next_idx" not in data:
                return data
            items = data.get("results", [])
            if not isinstance(items, list): return data
            combined.extend(items)
            next_index = data.get("next_idx")
            if next_index in (None, "", -1) or not data.get("limit_reached", True):
                return {**data, "results": combined}
            params["start"] = next_index
        raise FortiGateError(f"FortiGate pagination exceeded safety limit of {max_pages} pages")

    async def close(self):
        if self._owns_client: await self.client.aclose()
