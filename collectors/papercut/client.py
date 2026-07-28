"""Defensive read-only client for the PaperCut System Health JSON API."""
import asyncio
import ssl
from urllib.parse import urlsplit, urlunsplit

import httpx

from .models import (
    PaperCutAuthenticationError, PaperCutError,
    PaperCutMalformedResponseError, PaperCutTLSError,
    PaperCutTimeoutError, PaperCutUnreachableError,
)


class PaperCutClient:
    def __init__(self, base_url, authorization_key="", timeout=20, *,
                 verify_tls=True, max_retries=2, client=None,
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
            verify=verify_tls, headers=self._headers)

    @staticmethod
    def normalize_url(value):
        value = str(value or "").strip()
        if "://" not in value:
            value = "https://" + value
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("PaperCut endpoint must be a valid HTTPS URL")
        path = parsed.path.rstrip("/")
        if path.endswith("/api/health"):
            path = path[:-len("/api/health")]
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
                cause = exc
                while cause is not None:
                    if isinstance(cause, ssl.SSLError):
                        raise PaperCutTLSError(
                            "PaperCut TLS verification failed") from exc
                    cause = cause.__cause__
                if attempt == self.max_retries:
                    raise PaperCutUnreachableError(
                        "PaperCut System Health endpoint is unreachable") from exc
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
                if response.status_code in {401, 403}:
                    raise PaperCutAuthenticationError(
                        f"PaperCut System Health authentication failed "
                        f"(HTTP {response.status_code})")
                if response.status_code < 300:
                    try:
                        payload = response.json()
                    except ValueError as exc:
                        raise PaperCutMalformedResponseError(
                            "PaperCut System Health returned invalid JSON") from exc
                    if not isinstance(payload, dict):
                        raise PaperCutMalformedResponseError(
                            "PaperCut System Health returned an unexpected JSON type")
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

    async def snapshot(self):
        base = await self.get("/api/health/")
        if not isinstance(base.get("applicationServer"), dict) or \
                not isinstance(base.get("database"), dict):
            raise PaperCutMalformedResponseError(
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
