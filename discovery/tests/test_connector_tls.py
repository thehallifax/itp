import ssl

import httpx

from collectors.paloalto.api import PaloAltoClient
from collectors.papercut.client import PaperCutClient
from collectors.tls import connector_tls_context


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
