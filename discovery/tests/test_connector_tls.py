import ssl
from pathlib import Path

import httpx
import certifi
import pytest
import yaml

from collectors.paloalto.api import PaloAltoClient
from collectors.papercut.client import PaperCutClient
from collectors.tls import classify_certificate_issuer, connector_tls_context

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
