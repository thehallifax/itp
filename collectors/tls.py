"""Shared HTTPS trust helpers for connector clients."""
from __future__ import annotations

import ssl
import socket
import tempfile
from datetime import datetime, timezone
from pathlib import Path


PUBLIC_CA_IDENTITIES = (
    "amazon trust services",
    "comodoca",
    "comodo",
    "digicert",
    "entrust",
    "global sign",
    "globalsign",
    "godaddy",
    "google trust services",
    "internet security research group",
    "isrg",
    "let's encrypt",
    "letsencrypt",
    "microsoft ecc root",
    "microsoft identity verification root",
    "microsoft rsa root",
    "sectigo",
    "ssl.com",
    "starfield",
    "zerossl",
)

PRIVATE_CA_IDENTITIES = (
    "active directory",
    "enterprise ca",
    "internal ca",
    "local ca",
    "private ca",
)


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
        f"{key}={item}" for group in value or () for key, item in group)[:1024]


def _name_attributes(value):
    result = {}
    for group in value or ():
        for key, item in group:
            normalized_key = str(key)[:64]
            if len(result) < 32 or normalized_key in result:
                values = result.setdefault(normalized_key, [])
                if len(values) < 10:
                    values.append(str(item)[:512])
    return {key: values[0] if len(values) == 1 else values
            for key, values in sorted(result.items())}


def classify_certificate_issuer(subject, issuer):
    """Return True for public, False for private, or None when ambiguous.

    Recognition uses normalized X.509 issuer attributes, never endpoint
    hostnames. Unknown issuers deliberately remain unclassified.
    """
    issuer_text = " ".join(
        str(value) for value in (issuer or {}).values()).casefold()
    subject_text = " ".join(
        str(value) for value in (subject or {}).values()).casefold()
    if any(identity in issuer_text for identity in PUBLIC_CA_IDENTITIES):
        return True
    if any(identity in issuer_text for identity in PRIVATE_CA_IDENTITIES):
        return False
    if issuer_text and issuer_text == subject_text:
        return False
    return None


def inspect_tls_peer(hostname, port=443, timeout=5):
    """Read bounded public certificate metadata without accepting it as trusted.

    This is diagnostic evidence only. The connector's real request still uses
    strict verification through :func:`connector_tls_context`.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    resolved_address = socket.gethostbyname(hostname)
    presented_chain_length = None
    with socket.create_connection((hostname, int(port)), timeout=timeout) as raw:
        with context.wrap_socket(raw, server_hostname=hostname) as wrapped:
            der = wrapped.getpeercert(binary_form=True)
            get_chain = getattr(wrapped._sslobj, "get_unverified_chain", None)
            if get_chain:
                try:
                    presented_chain_length = len(get_chain())
                except Exception:
                    # Chain length is optional diagnostic evidence. Runtime
                    # SSL implementations expose this through a private API,
                    # so incompatibility must not discard leaf metadata.
                    pass
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
    subject_attributes = _name_attributes(decoded.get("subject"))
    issuer_attributes = _name_attributes(decoded.get("issuer"))
    sans = sorted(
        value for kind, value in decoded.get("subjectAltName", ())
        if kind == "DNS")[:100]
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
        "resolved_address": resolved_address,
        "subject": subject,
        "issuer": issuer,
        "subject_attributes": subject_attributes,
        "issuer_attributes": issuer_attributes,
        "public_issuer": classify_certificate_issuer(
            subject_attributes, issuer_attributes),
        "subject_alt_names": sans,
        "hostname_match": hostname_match,
        "not_before": not_before,
        "not_after": not_after,
        "expired": expired,
        "presented_chain_length": presented_chain_length,
        "trust": "failed",
    }
