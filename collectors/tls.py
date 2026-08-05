"""Shared HTTPS trust helpers for connector clients."""
from __future__ import annotations

import ssl
import socket
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def connector_tls_context(verify_tls=True, ca_bundle=None):
    """Return normal public trust extended by a deployment CA bundle."""
    if not verify_tls:
        return False
    context = ssl.create_default_context()
    bundle = str(ca_bundle or "").strip()
    if bundle:
        path = Path(bundle)
        if not path.is_file():
            raise ValueError(f"deployment CA bundle does not exist: {path}")
        context.load_verify_locations(cafile=str(path))
    return context


def deployment_ca_bundle(config):
    value = config.get("tls") or {}
    return str(value.get("ca_bundle") or "").strip() or None


def _name(value):
    return ", ".join(
        f"{key}={item}" for group in value or () for key, item in group)


def inspect_tls_peer(hostname, port=443, timeout=5):
    """Read bounded public certificate metadata without accepting it as trusted.

    This is diagnostic evidence only. The connector's real request still uses
    strict verification through :func:`connector_tls_context`.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((hostname, int(port)), timeout=timeout) as raw:
        with context.wrap_socket(raw, server_hostname=hostname) as wrapped:
            der = wrapped.getpeercert(binary_form=True)
    pem = ssl.DER_cert_to_PEM_cert(der)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as handle:
            handle.write(pem)
            temporary = Path(handle.name)
        decoded = ssl._ssl._test_decode_cert(str(temporary))
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    subject = _name(decoded.get("subject"))
    issuer = _name(decoded.get("issuer"))
    sans = sorted(
        value for kind, value in decoded.get("subjectAltName", ())
        if kind == "DNS")
    try:
        ssl.match_hostname(decoded, hostname)
        hostname_match = True
    except ssl.CertificateError:
        hostname_match = False
    not_before = decoded.get("notBefore", "")
    not_after = decoded.get("notAfter", "")
    expired = False
    if not_after:
        expired = datetime.fromtimestamp(
            ssl.cert_time_to_seconds(not_after), timezone.utc) < datetime.now(
                timezone.utc)
    return {
        "host": hostname,
        "resolved_address": socket.gethostbyname(hostname),
        "subject": subject,
        "issuer": issuer,
        "subject_alt_names": sans,
        "hostname_match": hostname_match,
        "not_before": not_before,
        "not_after": not_after,
        "expired": expired,
        "trust": "failed",
    }
