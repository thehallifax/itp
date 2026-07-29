"""Read-only Aruba Central REST client with shared OAuth token lifecycle."""
from __future__ import annotations

import asyncio
import time
from typing import ClassVar
from urllib.parse import urlsplit, urlunsplit

import httpx

from .models import (
    ArubaCentralCredentialError,
    ArubaCentralMalformedError,
    ArubaCentralPermissionError,
    ArubaCentralTokenExpiredError,
    ArubaCentralUnavailableError,
    ArubaCentralUnsupportedError,
)


class ArubaOAuthTokenManager:
    """Acquire and renew one bearer token without exposing credential values."""

    def __init__(self, token_url, client_id, client_secret, *,
                 refresh_token="", access_token="", auth_mode="client_credentials",
                 timeout=20, verify_tls=True, client=None, now=time.monotonic):
        self.token_url = token_url
        self.client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._access_token = access_token
        self.auth_mode = auth_mode
        self.expires_at = 0.0
        self.now = now
        self._lock = asyncio.Lock()
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=timeout), verify=verify_tls)

    async def token(self, force=False):
        if not force and self._access_token and self.now() < self.expires_at - 60:
            return self._access_token
        async with self._lock:
            if not force and self._access_token and self.now() < self.expires_at - 60:
                return self._access_token
            if self.auth_mode == "refresh_token":
                if not self._refresh_token:
                    raise ArubaCentralTokenExpiredError(
                        "Aruba Central refresh token is unavailable")
                data = {
                    "grant_type": "refresh_token",
                    "client_id": self.client_id,
                    "client_secret": self._client_secret,
                    "refresh_token": self._refresh_token,
                }
            else:
                data = {
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self._client_secret,
                }
            try:
                request = {
                    "headers": {"Accept": "application/json"},
                    "params" if self.auth_mode == "refresh_token"
                    else "data": data,
                }
                response = await self.client.post(self.token_url, **request)
            except httpx.TransportError as exc:
                raise ArubaCentralUnavailableError(
                    "Aruba Central token endpoint is unavailable") from exc
            if response.status_code in {400, 401}:
                raise ArubaCentralCredentialError(
                    "Aruba Central OAuth credentials were rejected")
            if response.status_code == 403:
                raise ArubaCentralPermissionError(
                    "Aruba Central OAuth client lacks permission")
            if response.status_code >= 500:
                raise ArubaCentralUnavailableError(
                    "Aruba Central token service is unavailable")
            if response.status_code >= 400:
                raise ArubaCentralTokenExpiredError(
                    f"Aruba Central token acquisition failed (HTTP {response.status_code})")
            try:
                payload = response.json()
            except ValueError as exc:
                raise ArubaCentralMalformedError(
                    "Aruba Central token endpoint returned invalid JSON") from exc
            token = str(payload.get("access_token") or "")
            if not token:
                raise ArubaCentralMalformedError(
                    "Aruba Central token response omitted access_token")
            self._access_token = token
            if payload.get("refresh_token"):
                self._refresh_token = str(payload["refresh_token"])
            self.expires_at = self.now() + max(
                60, int(payload.get("expires_in") or 3600))
            return token

    async def close(self):
        if self._owns_client:
            await self.client.aclose()


class ArubaCentralClient:
    ENDPOINTS: ClassVar[dict[str, str]] = {
        "groups": "/configuration/v2/groups",
        "sites": "/central/v2/sites",
        "access_points": "/monitoring/v2/aps",
        "switches": "/monitoring/v1/switches",
        "gateways": "/monitoring/v1/gateways",
        "alerts": "/central/v1/alerts",
    }

    def __init__(self, base_url, token_manager, timeout=20, *, verify_tls=True,
                 max_retries=2, client=None, sleep=asyncio.sleep,
                 endpoints=None):
        self.base_url = self.normalize_url(base_url)
        self.token_manager = token_manager
        self.max_retries = max(0, min(5, int(max_retries)))
        self.sleep = sleep
        self.api_requests = 0
        self.retry_count = 0
        self.endpoints = {**self.ENDPOINTS, **(endpoints or {})}
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=timeout), verify=verify_tls,
            headers={"Accept": "application/json",
                     "User-Agent": "itp-aruba-central-collector/1.0"})

    @staticmethod
    def normalize_url(value):
        value = str(value or "").strip()
        if "://" not in value:
            value = "https://" + value
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Aruba Central base_url must be a valid HTTPS URL")
        return urlunsplit(("https", parsed.netloc, parsed.path.rstrip("/"), "", ""))

    async def get(self, operation, params=None):
        path = self.endpoints[operation]
        refreshed = False
        for attempt in range(self.max_retries + 1):
            token = await self.token_manager.token(force=refreshed)
            self.api_requests += 1
            try:
                response = await self.client.get(
                    self.base_url + path,
                    params=params, headers={"Authorization": f"Bearer {token}"})
            except httpx.TransportError as exc:
                if attempt == self.max_retries:
                    raise ArubaCentralUnavailableError(
                        f"Aruba Central {operation} endpoint is unavailable") from exc
                response = None
            if response is not None:
                if response.status_code == 401 and not refreshed:
                    refreshed = True
                    continue
                if response.status_code == 401:
                    raise ArubaCentralTokenExpiredError(
                        "Aruba Central access token could not be renewed")
                if response.status_code == 403:
                    raise ArubaCentralPermissionError(
                        f"Aruba Central permission denied for {operation}")
                if response.status_code in {404, 405, 501}:
                    print("\n=== Aruba API Debug ===")
                    print("Operation :", operation)
                    print("URL       :", self.base_url + path)
                    print("Status    :", response.status_code)
                    print("Response  :", response.text[:1000])
                    print("=======================\n")

                    raise ArubaCentralUnsupportedError(
                        f"Aruba Central endpoint is unsupported: {operation}"
                    )
#                if response.status_code in {404, 405, 501}:
#                    raise ArubaCentralUnsupportedError(
#                        f"Aruba Central endpoint is unsupported: {operation}")
                if response.status_code in {429, 502, 503, 504}:
                    if attempt == self.max_retries:
                        raise ArubaCentralUnavailableError(
                            f"Aruba Central {operation} endpoint is unavailable")
                elif response.status_code >= 400:
                    raise ArubaCentralUnavailableError(
                        f"Aruba Central {operation} request failed "
                        f"(HTTP {response.status_code})")
                else:
                    try:
                        payload = response.json()
                    except ValueError as exc:
                        raise ArubaCentralMalformedError(
                            f"Aruba Central {operation} returned invalid JSON") from exc
                    if not isinstance(payload, (dict, list)):
                        raise ArubaCentralMalformedError(
                            f"Aruba Central {operation} returned an unexpected JSON type")
                    return payload
            self.retry_count += 1
            await self.sleep(min(2 ** attempt, 4))

    async def _optional(self, operation, params=None):
        if not self.endpoints.get(operation):
            return [], {
                "state": "endpoint_disabled", "resource_count": 0,
            }
        try:
            payload = await self.get(operation, params)
            return payload, {
                "state": "collected", "resource_count": None,
            }
        except (
            ArubaCentralMalformedError,
            ArubaCentralPermissionError,
            ArubaCentralUnavailableError,
            ArubaCentralUnsupportedError,
        ) as exc:
            return [], {
                "state": exc.category, "resource_count": 0,
            }

    async def snapshot(self):
        # Groups are useful enrichment metadata, but some Aruba Central and
        # GreenLake tenants do not expose the legacy configuration/v2/groups
        # endpoint. AP and site inventory must still be collected.
        (groups, group_diagnostic), sites, access_points = await asyncio.gather(
            self._optional("groups"),
            self.get("sites"),
            self.get("access_points"),
        )
        switches, switch_diagnostic = await self._optional("switches")
        gateways, gateway_diagnostic = await self._optional("gateways")
        alerts, alert_diagnostic = await self._optional(
            "alerts", {"limit": 100})
        return {
            "groups": groups,
            "sites": sites,
            "device_classes": {
                "access_points": access_points,
                "switches": switches,
                "gateways": gateways,
            },
            "alerts": alerts,
            # Switches, gateways and alerts are additive capabilities. Their
            # absence or endpoint failure must not downgrade successful AP
            # collection for an AP-only Central tenant.
            "partial": False,
            "diagnostics": {
                "groups": group_diagnostic,
                "access_points": {"state": "collected", "resource_count": None},
                "switches": switch_diagnostic,
                "gateways": gateway_diagnostic,
                "alerts": alert_diagnostic,
            },
        }

    async def close(self):
        if self._owns_client:
            await self.client.aclose()
        await self.token_manager.close()
