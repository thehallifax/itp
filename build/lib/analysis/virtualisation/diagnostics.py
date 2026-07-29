"""Sanitized provider error classification."""
import socket
import ssl

import httpx


def classify_error(exc):
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, (ssl.SSLError, httpx.ConnectError)) and "certificate" in str(exc).lower():
        return "tls"
    if isinstance(exc, socket.gaierror):
        return "dns"
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 401:
            return "authentication"
        if status == 403:
            return "permission"
        if status == 404:
            return "endpoint_unsupported"
        return "http"
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return "malformed_response"
    return "unknown"
