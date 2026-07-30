"""Defensive read-only client for the PaperCut System Health JSON API."""
import asyncio
import socket
import ssl
from urllib.parse import urlsplit, urlunsplit

import httpx

from .models import (
    PaperCutAuthenticationError,
    PaperCutApplicationError,
    PaperCutAuthorizationError,
    PaperCutCertificateExpiredError,
    PaperCutConnectionError,
    PaperCutDNSError,
    PaperCutError,
    PaperCutHostnameMismatchError,
    PaperCutMalformedResponseError,
    PaperCutRedirectError,
    PaperCutTimeoutError,
    PaperCutTLSError,
    PaperCutUnknownIssuerError,
    PaperCutUnreachableError,
    PaperCutUnsupportedResponseError,
    PaperCutWrongEndpointError,
)
from collectors.tls import connector_tls_context


class PaperCutClient:
    def __init__(self, base_url, authorization_key="", timeout=20, *,
                 verify_tls=True, ca_bundle=None, max_retries=2, client=None,
                 sleep=asyncio.sleep):
        self.base_url = self.normalize_url(base_url)
        self._headers = {"Accept": "application/json",
                         "User-Agent": "itp-papercut-collector/1.0"}
        self.authorization_key = str(authorization_key or "").strip()
        self.max_retries = max(0, int(max_retries))
        self.sleep = sleep
        self.api_requests = 0
        self.retry_count = 0
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=timeout),
            verify=connector_tls_context(verify_tls, ca_bundle),
            headers=self._headers)

    @staticmethod
    def normalize_url(value):
        value = str(value or "").strip()
        if "://" not in value:
            value = "https://" + value
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("PaperCut endpoint must be a valid HTTPS URL")
        path = parsed.path.rstrip("/")
        if path == "/api/health":
            path = ""
        elif path:
            raise ValueError(
                "PaperCut endpoint must not include a path other than /api/health")
        return urlunsplit(("https", parsed.netloc, path, "", ""))

    async def get(self, path="/api/health/"):
        transient = {429, 502, 503, 504}
        for attempt in range(self.max_retries + 1):
            self.api_requests += 1
            try:
                headers = dict(self._headers)
                if self.authorization_key:
                    headers["Authorization"] = self.authorization_key

                response = await self.client.get(
                    self.base_url + "/" + path.lstrip("/"),
                    headers=headers)
            except httpx.TimeoutException as exc:
                if attempt == self.max_retries:
                    raise PaperCutTimeoutError(
                        "PaperCut System Health request timed out") from exc
                response = None
            except httpx.ConnectError as exc:
                category = self._connection_error(exc)
                if category:
                    raise category from exc
                if attempt == self.max_retries:
                    raise PaperCutConnectionError(
                        "PaperCut System Health TCP connection failed") from exc
                response = None
            except httpx.TransportError as exc:
                if "ssl" in str(exc).lower() or "certificate" in str(exc).lower():
                    raise PaperCutTLSError(
                        "PaperCut TLS verification failed") from exc
                if attempt == self.max_retries:
                    raise PaperCutUnreachableError(
                        "PaperCut System Health transport failure") from exc
                response = None
            if response is not None:
                if response.status_code == 401:
                    raise PaperCutAuthenticationError(
                        f"PaperCut System Health authentication failed "
                        "(HTTP 401)")
                if response.status_code == 403:
                    raise PaperCutAuthorizationError(
                        "PaperCut System Health access is forbidden (HTTP 403)")
                if response.status_code == 404:
                    raise PaperCutWrongEndpointError(
                        "PaperCut System Health endpoint was not found (HTTP 404); "
                        "configure the PaperCut application-server origin")
                if 300 <= response.status_code < 400:
                    location = urlsplit(response.headers.get("location", "")).path
                    suffix = f" to {location}" if location else ""
                    raise PaperCutRedirectError(
                        f"PaperCut System Health returned an HTTP "
                        f"{response.status_code} redirect{suffix}; configure the "
                        "System Health API origin")
                if response.status_code < 300:
                    try:
                        payload = response.json()
                    except ValueError as exc:
                        raise PaperCutMalformedResponseError(
                            "PaperCut System Health returned invalid JSON") from exc
                    if not isinstance(payload, dict):
                        raise PaperCutMalformedResponseError(
                            "PaperCut System Health returned an unexpected JSON type")
                    if payload.get("error") or payload.get("errors") or \
                            payload.get("success") is False:
                        raise PaperCutApplicationError(
                            "PaperCut System Health reported an application-level error")
                    return payload
                if response.status_code not in transient:
                    raise PaperCutError(
                        f"PaperCut System Health request failed "
                        f"(HTTP {response.status_code})")
                if attempt == self.max_retries:
                    raise PaperCutError(
                        "PaperCut System Health request failed after retries")
            self.retry_count += 1
            await self.sleep(min(2 ** attempt, 4))

    @staticmethod
    def _connection_error(error):
        causes = []
        current = error
        while current is not None and current not in causes:
            causes.append(current)
            current = current.__cause__ or current.__context__
        text = " ".join(str(value) for value in causes).casefold()
        if any(isinstance(value, socket.gaierror) for value in causes):
            return PaperCutDNSError(
                "PaperCut hostname could not be resolved by DNS")
        if any(isinstance(value, ssl.SSLError) for value in causes) or \
                "certificate" in text or "ssl" in text:
            if "expired" in text:
                return PaperCutCertificateExpiredError(
                    "PaperCut server certificate has expired")
            if "hostname" in text or "not valid for" in text:
                return PaperCutHostnameMismatchError(
                    "PaperCut server certificate does not match the configured hostname")
            if any(value in text for value in (
                    "unable to get local issuer", "self-signed",
                    "unknown ca", "certificate verify failed")):
                return PaperCutUnknownIssuerError(
                    "PaperCut server certificate chain is not trusted by the "
                    "collector. Import the private root and intermediate CA "
                    "certificates with ./itp credentials ca add.")
            return PaperCutTLSError("PaperCut TLS verification failed")
        if any(isinstance(value, ConnectionRefusedError) for value in causes):
            return PaperCutConnectionError(
                "PaperCut System Health TCP connection was refused")
        return None

    async def snapshot(self):
        base = await self.get("/api/health/")
        if not isinstance(base.get("applicationServer"), dict) or \
                not isinstance(base.get("database"), dict):
            raise PaperCutUnsupportedResponseError(
                "PaperCut System Health response is missing required sections")
        partial = False
        try:
            devices = await self.get("/api/health/devices")
        except PaperCutAuthenticationError:
            raise
        except PaperCutError:
            devices = {}
            partial = True
        return {"health": base, "devices": devices, "partial": partial}

    async def close(self):
        if self._owns_client:
            await self.client.aclose()
