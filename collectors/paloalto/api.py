"""Bounded, read-only PAN-OS XML API client."""
import asyncio
import ssl
import time
from urllib.parse import urlsplit, urlunsplit
import xml.etree.ElementTree as ET

import httpx

from collectors.tls import connector_tls_context
from .models import (CapabilityResult, PaloAltoCredentialError, PaloAltoError,
                     PaloAltoPermissionError, PaloAltoTLSError, PaloAltoTimeoutError,
                     PaloAltoUnreachableError, PaloAltoUnsupportedError)


COMMANDS = {
    "system": "<show><system><info></info></system></show>",
    "ha": "<show><high-availability><state></state></high-availability></show>",
    "interfaces": "<show><interface>all</interface></show>",
    "resources": "<show><system><resources></resources></system></show>",
    "resource_monitor": (
        "<show><running><resource-monitor><second><last>1</last></second>"
        "</resource-monitor></running></show>"
    ),
    "sessions": "<show><session><info></info></session></show>",
    "interface_counters": "<show><counter><interface>all</interface></counter></show>",
    "licenses": "<request><license><info></info></license></request>",
}


class PaloAltoClient:
    def __init__(self, base_url, api_key, timeout=20, *, verify_tls=True, ca_bundle=None,
                 allow_http=False, max_retries=2, client=None, sleep=asyncio.sleep):
        if not api_key: raise ValueError("Palo Alto API key is required")
        self.base_url = self.normalize_url(base_url, allow_http=allow_http)
        self.endpoint_host = urlsplit(self.base_url).hostname or "unknown"
        self._secret = api_key
        self._headers = {"X-PAN-KEY": api_key, "Accept": "application/xml",
                         "User-Agent": "itp-paloalto-collector/1.0"}
        self.max_retries = max(0, min(3, int(max_retries)))
        self.sleep = sleep; self.api_requests = 0; self.retry_count = 0
        self.command_diagnostics = []
        self._owns_client = client is None
        verify = connector_tls_context(verify_tls, ca_bundle)
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(float(timeout), connect=float(timeout)),
            verify=verify, headers=self._headers)

    @staticmethod
    def normalize_url(value, *, allow_http=False):
        value = str(value or "").strip()
        if "://" not in value: value = "https://" + value
        parsed = urlsplit(value)
        allowed = {"https"} | ({"http"} if allow_http else set())
        if parsed.scheme not in allowed or not parsed.hostname:
            raise ValueError("Palo Alto base_url must be an HTTPS URL")
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))

    def _safe(self, operation, category, message):
        clean = " ".join(str(message or category).replace(self._secret, "[REDACTED]").split())[:240]
        return (f"collector=paloalto operation={operation} endpoint={self.endpoint_host} "
                f"category={category} message={clean}")

    async def op(self, operation, command=None):
        command = command or COMMANDS[operation]
        transient = {502, 503, 504}
        started = time.monotonic()
        retries_before = self.retry_count
        for attempt in range(self.max_retries + 1):
            self.api_requests += 1
            try:
                response = await self.client.get(
                    self.base_url.rstrip("/") + "/api/",
                    params={"type": "op", "cmd": command}, headers=self._headers)
            except httpx.TimeoutException as exc:
                if attempt == self.max_retries:
                    raise PaloAltoTimeoutError(self._safe(operation, "timeout", "request timed out")) from exc
                response = None
            except httpx.ConnectError as exc:
                cause = exc
                while cause:
                    if isinstance(cause, ssl.SSLError):
                        raise PaloAltoTLSError(self._safe(operation, "tls", "TLS verification failed")) from exc
                    cause = cause.__cause__
                if attempt == self.max_retries:
                    raise PaloAltoUnreachableError(
                        self._safe(operation, "unreachable", "endpoint is unreachable")) from exc
                response = None
            except httpx.TransportError as exc:
                if "certificate" in str(exc).lower() or "ssl" in type(exc).__name__.lower():
                    raise PaloAltoTLSError(self._safe(operation, "tls", "TLS verification failed")) from exc
                if attempt == self.max_retries:
                    raise PaloAltoUnreachableError(
                        self._safe(operation, "unreachable", "transport failure")) from exc
                response = None
            if response is not None:
                if response.status_code in transient:
                    if attempt == self.max_retries:
                        raise PaloAltoError(self._safe(
                            operation, "http", f"transient HTTP {response.status_code} after retries"))
                elif response.status_code >= 400:
                    raise PaloAltoError(self._safe(
                        operation, "http", f"API returned HTTP {response.status_code}"))
                else:
                    try:
                        result = self._validate(operation, response.text)
                    except Exception as exc:
                        self._record(operation, started, False, retries_before,
                                     getattr(exc, "category", "invalid_response"))
                        raise
                    self._record(operation, started, True, retries_before, "success")
                    return result
            self.retry_count += 1
            await self.sleep(min(2 ** attempt, 4))

    def _record(self, operation, started, success, retries_before, category):
        self.command_diagnostics.append({
            "command": operation,
            "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
            "success": bool(success),
            "retries": max(0, self.retry_count - retries_before),
            "category": str(category),
        })

    def _validate(self, operation, text):
        try: root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise PaloAltoError(self._safe(operation, "malformed_xml", "API returned malformed XML")) from exc
        status = str(root.attrib.get("status", "")).lower()
        if status == "error":
            message = " ".join((root.findtext(".//msg") or "PAN-OS API error").split())
            lowered = message.lower()
            if any(word in lowered for word in ("permission", "unauthorized", "not authorized")):
                raise PaloAltoPermissionError(self._safe(operation, "permission", message))
            if any(word in lowered for word in ("key", "authentication", "invalid credential")):
                raise PaloAltoCredentialError(self._safe(operation, "credential", "authentication failed"))
            if any(word in lowered for word in ("unknown command", "unsupported", "unexpected here")):
                raise PaloAltoUnsupportedError(self._safe(operation, "unsupported", message))
            if "busy" in lowered:
                raise PaloAltoError(self._safe(operation, "busy", message))
            raise PaloAltoError(self._safe(operation, "api_error", message))
        result = root.find("./result")
        if status != "success" or result is None:
            raise PaloAltoError(self._safe(operation, "empty_result", "successful result was not returned"))
        if result.find(".//job") is not None:
            raise PaloAltoUnsupportedError(
                self._safe(operation, "asynchronous_job", "unexpected asynchronous job response"))
        return result

    async def capability(self, name):
        before = len(self.command_diagnostics)
        started = time.monotonic()
        retries_before = self.retry_count
        try:
            data = await self.op(name)
            diagnostic = self.command_diagnostics[-1] if len(self.command_diagnostics) > before else {}
            return CapabilityResult(name=name, data=data,
                duration_ms=int(diagnostic.get("duration_ms", 0)),
                retries=int(diagnostic.get("retries", 0)))
        except (PaloAltoCredentialError, PaloAltoTLSError):
            raise
        except PaloAltoError as exc:
            if len(self.command_diagnostics) == before:
                self._record(name, started, False, retries_before, exc.category)
            diagnostic = self.command_diagnostics[-1] if len(self.command_diagnostics) > before else {}
            return CapabilityResult(name=name, available=False,
                                    category=exc.category, message=str(exc),
                                    duration_ms=int(diagnostic.get("duration_ms", 0)),
                                    retries=int(diagnostic.get("retries", 0)))

    async def close(self):
        if self._owns_client: await self.client.aclose()
