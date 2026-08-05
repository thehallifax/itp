import ssl
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import certifi
import pytest
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from collectors.paloalto.api import PaloAltoClient
from collectors.papercut.client import PaperCutClient
from collectors.tls import (
    classify_certificate_issuer,
    connector_tls_context,
    inspect_tls_peer,
    merge_tls_evidence,
)

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_image_installs_and_initializes_public_ca_store():
    dockerfile = (ROOT / "discovery/Dockerfile").read_text()
    assert "apt-get install -y --no-install-recommends ca-certificates" in dockerfile
    assert "update-ca-certificates" in dockerfile
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    for service in ("collector", "discovery"):
        assert compose["services"][service]["build"]["dockerfile"] == (
            "discovery/Dockerfile")


def test_python_default_context_has_usable_public_roots():
    paths = ssl.get_default_verify_paths()
    assert paths.cafile or paths.capath
    assert ssl.create_default_context().get_ca_certs()


def test_deployment_ca_augments_without_removing_public_roots(tmp_path):
    source = Path(certifi.where()).read_text()
    certificate = source.split("-----END CERTIFICATE-----", 1)[0] + (
        "-----END CERTIFICATE-----\n")
    bundle = tmp_path / "deployment-ca.pem"
    bundle.write_text(certificate)
    public_count = len(ssl.create_default_context().get_ca_certs())
    combined = connector_tls_context(True, bundle)
    assert len(combined.get_ca_certs()) >= public_count
    bundle.unlink()
    assert len(connector_tls_context(True).get_ca_certs()) == public_count


@pytest.mark.parametrize("issuer", [
    "Let's Encrypt YR2",
    "Internet Security Research Group ISRG Root X1",
    "DigiCert TLS RSA SHA256 2020 CA1",
    "GlobalSign RSA OV SSL CA 2018",
    "Sectigo RSA Domain Validation Secure Server CA",
    "COMODO RSA Certification Authority",
    "Amazon Trust Services RSA 2048 M02",
    "Google Trust Services WR2",
    "Microsoft RSA Root Certificate Authority 2017",
])
def test_recognized_public_issuer_attributes(issuer):
    assert classify_certificate_issuer(
        {"commonName": "firewall.example.test"},
        {"organizationName": issuer}) is True


def test_unknown_issuer_is_not_assumed_private():
    assert classify_certificate_issuer(
        {"commonName": "firewall.example.test"},
        {"organizationName": "Unclassified Certificate Authority"}) is None


def test_internal_microsoft_ca_is_not_mistaken_for_public_microsoft_root():
    assert classify_certificate_issuer(
        {"commonName": "firewall.example.test"},
        {"commonName": "Active Directory Enterprise CA"}) is False


def synthetic_public_leaf():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "*.example.edu.au")]))
        .issuer_name(x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Let's Encrypt"),
            x509.NameAttribute(NameOID.COMMON_NAME, "YR2")]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName("*.example.edu.au"),
            x509.DNSName("example.edu.au")]), critical=False)
        .sign(key, hashes.SHA256()))
    return certificate.public_bytes(serialization.Encoding.DER)


@pytest.mark.parametrize(
    "chain_mode", ["unavailable", "attribute_unavailable", "raises"])
def test_inspection_only_connection_decodes_leaf_in_memory_and_chain_is_optional(
        monkeypatch, chain_mode):
    der = synthetic_public_leaf()
    calls = []

    class SSLObject:
        if chain_mode == "raises":
            def get_unverified_chain(self):
                raise RuntimeError("chain API unavailable")

    class Wrapped:
        if chain_mode != "attribute_unavailable":
            _sslobj = SSLObject()
        def __enter__(self): return self
        def __exit__(self, *_args): calls.append("tls.closed")
        def getpeercert(self, *, binary_form=False):
            assert binary_form is True
            calls.append("leaf.der")
            return der

    class Context:
        check_hostname = True
        verify_mode = ssl.CERT_REQUIRED
        def wrap_socket(self, raw, *, server_hostname):
            assert raw == "raw-socket"
            assert server_hostname == "firewall.example.edu.au"
            calls.append("tls.wrap")
            return Wrapped()

    class Raw:
        def __enter__(self): return "raw-socket"
        def __exit__(self, *_args): calls.append("tcp.closed")

    monkeypatch.setattr(ssl, "SSLContext", lambda _protocol: Context())
    monkeypatch.setattr("collectors.tls.socket.gethostbyname",
                        lambda host: "192.0.2.10")
    monkeypatch.setattr("collectors.tls.socket.create_connection",
                        lambda address, timeout: Raw())

    evidence = inspect_tls_peer("firewall.example.edu.au", 8443, timeout=3)

    assert evidence["public_issuer"] is True
    assert evidence["issuer_attributes"]["organizationName"] == "Let's Encrypt"
    assert evidence["hostname_match"] is True
    assert evidence["expired"] is False
    assert evidence["presented_chain_length"] is None
    assert calls == ["tls.wrap", "leaf.der", "tls.closed", "tcp.closed"]
    source = (ROOT / "collectors/tls.py").read_text()
    assert "NamedTemporaryFile" not in source
    assert "Authorization" not in source


def test_tls_evidence_merge_preserves_inspection_and_authoritative_fields():
    inspection = {
        "host": "wrong.example.test",
        "issuer": "organizationName=Let's Encrypt, commonName=YR2",
        "public_issuer": True,
        "inspection_status": "complete",
        "hostname_match": True,
        "presented_chain_length": None,
        "trust": "trusted",
        "verify_code": None,
        "verify_message": None,
        "unbounded_unknown_field": "discarded",
    }
    merged = merge_tls_evidence(
        "firewall.example.test", inspection,
        verify_code=20, verify_message="unable to get local issuer certificate")
    assert merged == {
        "host": "firewall.example.test",
        "hostname_match": True,
        "inspection_status": "complete",
        "issuer": "organizationName=Let's Encrypt, commonName=YR2",
        "presented_chain_length": None,
        "public_issuer": True,
        "trust": "failed",
        "verify_code": 20,
        "verify_message": "unable to get local issuer certificate",
    }


def test_inspection_connection_failure_preserves_bounded_partial_evidence(
        monkeypatch):
    monkeypatch.setattr("collectors.tls.socket.gethostbyname",
                        lambda host: "192.0.2.11")
    monkeypatch.setattr(
        "collectors.tls.socket.create_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(socket.timeout()))
    evidence = inspect_tls_peer("firewall.example.test", 8443, timeout=1)
    assert evidence == {
        "host": "firewall.example.test",
        "resolved_address": "192.0.2.11",
        "inspection_status": "connection_failed",
        "trust": "failed",
    }


def test_custom_ca_extends_default_public_trust(monkeypatch, tmp_path):
    calls = []

    class Context:
        def load_verify_locations(self, *, cafile):
            calls.append(cafile)

    context = Context()
    monkeypatch.setattr(ssl, "create_default_context", lambda: context)
    bundle = tmp_path / "deployment-ca.pem"
    bundle.write_text("certificate")
    assert connector_tls_context(True, bundle) is context
    assert calls == [str(bundle)]


def test_tls_verification_cannot_be_silently_disabled_by_ca_bundle(tmp_path):
    missing = tmp_path / "missing.pem"
    try:
        connector_tls_context(True, missing)
    except ValueError as error:
        assert "does not exist" in str(error)
    else:
        raise AssertionError("missing deployment CA bundle was accepted")


def test_papercut_disable_is_connector_scoped(monkeypatch):
    verify_values = []

    class Client:
        def __init__(self, **kwargs):
            verify_values.append(kwargs["verify"])

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    PaperCutClient(
        "https://print.example.invalid", verify_tls=False)
    PaloAltoClient(
        "https://firewall.example.invalid", "api-key")
    assert verify_values[0] is False
    assert isinstance(verify_values[1], ssl.SSLContext)
