"""Defensive read-only FortiGate HTTPS REST client."""
import asyncio
import ssl
from urllib.parse import urlsplit, urlunsplit

import httpx

from collectors.tls import connector_tls_context
from .models import (
    EndpointResult,
    FortiGateCredentialError,
    FortiGateError,
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
                 max_retries=2, backoff_limit=4, client=None, sleep=asyncio.sleep):
        if not host or not api_token:
            raise ValueError("FORTIGATE_HOST and FORTIGATE_API_TOKEN are required")
        self.base_url = self.normalize_url(host)
        self._headers = {"Authorization": f"Bearer {api_token}", "Accept": "application/json",
                         "User-Agent": "itp-fortigate-collector/1.0"}
        self.max_retries = max(0, int(max_retries)); self.backoff_limit = backoff_limit
        self.sleep = sleep; self.api_requests = 0; self.retry_count = 0
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(base_url=self.base_url,
            timeout=httpx.Timeout(timeout, connect=timeout),
            verify=connector_tls_context(verify_tls, ca_bundle),
            headers=self._headers)

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
                cause = exc
                while cause is not None:
                    if isinstance(cause, ssl.SSLError):
                        raise FortiGateTLSError("FortiGate TLS verification failed") from exc
                    cause = cause.__cause__
                if attempt == self.max_retries: raise FortiGateUnreachableError("FortiGate API is unreachable") from exc
                response = None
            except httpx.TransportError as exc:
                text = type(exc).__name__.lower()
                if "ssl" in text or "certificate" in str(exc).lower():
                    raise FortiGateTLSError("FortiGate TLS verification failed") from exc
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
