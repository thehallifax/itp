"""Shared HTTPS trust helpers for connector clients."""
from __future__ import annotations

import ssl
import socket
from datetime import datetime, timezone
from pathlib import Path

from cryptography import x509
from cryptography.x509.oid import NameOID


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


NAME_LABELS = {
    NameOID.COMMON_NAME: "commonName",
    NameOID.COUNTRY_NAME: "countryName",
    NameOID.LOCALITY_NAME: "localityName",
    NameOID.ORGANIZATION_NAME: "organizationName",
    NameOID.ORGANIZATIONAL_UNIT_NAME: "organizationalUnitName",
    NameOID.STATE_OR_PROVINCE_NAME: "stateOrProvinceName",
}


def _name_attributes(value):
    result = {}
    for attribute in value:
        normalized_key = NAME_LABELS.get(
            attribute.oid, attribute.oid.dotted_string)[:64]
        if len(result) < 32 or normalized_key in result:
            values = result.setdefault(normalized_key, [])
            if len(values) < 10:
                values.append(str(attribute.value)[:512])
    return {key: values[0] if len(values) == 1 else values
            for key, values in sorted(result.items())}


def _display_name(attributes):
    return ", ".join(
        f"{key}={value}" for key, value in attributes.items())[:1024]


def _decode_peer_certificate(der, hostname):
    """Decode bounded leaf metadata entirely in memory."""
    certificate = x509.load_der_x509_certificate(der)
    subject_attributes = _name_attributes(certificate.subject)
    issuer_attributes = _name_attributes(certificate.issuer)
    try:
        sans = sorted(certificate.extensions.get_extension_for_class(
            x509.SubjectAlternativeName).value.get_values_for_type(
                x509.DNSName))[:100]
    except x509.ExtensionNotFound:
        sans = []
    common_names = certificate.subject.get_attributes_for_oid(
        NameOID.COMMON_NAME)
    match_data = {"subjectAltName": [("DNS", value) for value in sans]}
    if common_names:
        match_data["subject"] = ((
            ("commonName", str(common_names[0].value)[:512]),),)
    try:
        ssl.match_hostname(match_data, hostname)
        hostname_match = True
    except ssl.CertificateError:
        hostname_match = False
    if hasattr(certificate, "not_valid_before_utc"):
        not_before_value = certificate.not_valid_before_utc
        not_after_value = certificate.not_valid_after_utc
    else:  # cryptography < 42 compatibility
        not_before_value = certificate.not_valid_before.replace(
            tzinfo=timezone.utc)
        not_after_value = certificate.not_valid_after.replace(
            tzinfo=timezone.utc)
    return {
        "subject": _display_name(subject_attributes),
        "issuer": _display_name(issuer_attributes),
        "subject_attributes": subject_attributes,
        "issuer_attributes": issuer_attributes,
        "public_issuer": classify_certificate_issuer(
            subject_attributes, issuer_attributes),
        "subject_alt_names": sans,
        "hostname_match": hostname_match,
        "not_before": not_before_value.isoformat().replace("+00:00", "Z"),
        "not_after": not_after_value.isoformat().replace("+00:00", "Z"),
        "expired": not_after_value < datetime.now(timezone.utc),
    }


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
    evidence = {"host": hostname, "trust": "failed"}
    try:
        resolved_address = socket.gethostbyname(hostname)
        evidence["resolved_address"] = resolved_address
    except (OSError, socket.gaierror):
        evidence["inspection_status"] = "dns_failure"
        return evidence
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    presented_chain_length = None
    try:
        with socket.create_connection(
                (hostname, int(port)), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=hostname) as wrapped:
                der = wrapped.getpeercert(binary_form=True)
                get_chain = getattr(
                    wrapped._sslobj, "get_unverified_chain", None)
                if get_chain:
                    try:
                        presented_chain_length = len(get_chain())
                    except Exception:
                        # Chain length is optional diagnostic evidence. Runtime
                        # SSL implementations expose this through a private API,
                        # so incompatibility must not discard leaf metadata.
                        pass
    except (OSError, socket.timeout, ssl.SSLError):
        evidence["inspection_status"] = "connection_failed"
        return evidence
    evidence["presented_chain_length"] = presented_chain_length
    if not der:
        evidence["inspection_status"] = "certificate_unavailable"
        return evidence
    try:
        evidence.update(_decode_peer_certificate(der, hostname))
    except (TypeError, ValueError, x509.UnsupportedAlgorithm):
        evidence["inspection_status"] = "decode_failed"
        return evidence
    evidence["inspection_status"] = "complete"
    return evidence
