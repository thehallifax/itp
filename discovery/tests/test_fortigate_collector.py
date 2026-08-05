import asyncio
import json
import ssl
import subprocess
import sys

import httpx
import pytest

from collectors.__main__ import _enabled_collectors
from collectors.base import ExecutionModeMismatch, RuntimePlacementCollector
from collectors.config import load_config
from collectors.fortigate.client import FortiGateClient
from collectors.fortigate.collector import FortiGateCollector
from collectors.fortigate.models import (
    EndpointResult, FortiGateCertificateExpiredError,
    FortiGateCredentialError,
    FortiGateHostnameMismatchError, FortiGateIncompleteChainError,
    FortiGatePermissionError, FortiGatePrivateCAError, FortiGateTimeoutError,
    FortiGateTLSError)
from collectors.fortigate.normalizer import normalize, stable_id


def config(**overrides):
    settings = {"enabled": True, "execution": "edge", "host": "fg.example.test",
        "api_token": "super-secret-token", "customer": "tenant-a", "site": "site-a"}
    settings.update(overrides)
    return {"customer": "fallback", "site": "fallback", "inventory": {"enabled": False},
            "collectors": {"fortigate": settings}}


def test_execution_placement_parsing_and_invalid_value(tmp_path):
    valid = tmp_path / "valid.yml"
    valid.write_text("collectors:\n  mist:\n    enabled: false\n    execution: either\n")
    assert load_config(valid)["collectors"]["mist"]["execution"] == "either"
    invalid = tmp_path / "invalid.yml"
    invalid.write_text("collectors:\n  mist:\n    execution: branch-office\n")
    with pytest.raises(ValueError, match="unsupported execution placement"):
        load_config(invalid)


def test_runtime_mode_filtering(monkeypatch):
    created = []
    monkeypatch.setattr("collectors.__main__.CollectorRegistry.create",
                        lambda name, *_: created.append(name) or type("C", (), {"name": name})())
    cfg = {"collectors": {"mist": {"enabled": True, "execution": "central"},
                           "fortigate": {"enabled": True, "execution": "edge"}}}
    monkeypatch.setenv("ITP_RUNTIME_MODE", "central")
    assert [item.name for item in _enabled_collectors(cfg)] == ["mist"]
    monkeypatch.setenv("ITP_RUNTIME_MODE", "edge")
    assert [item.name for item in _enabled_collectors(cfg)] == ["fortigate"]
    monkeypatch.setenv("ITP_RUNTIME_MODE", "invalid")
    with pytest.raises(ValueError, match="unsupported ITP_RUNTIME_MODE"):
        _enabled_collectors(cfg)
    monkeypatch.setenv("ITP_RUNTIME_MODE", "central")
    assert [item.name for item in _enabled_collectors(
        {"collectors": {"mist": {"enabled": True}}}
    )] == ["mist"]


def test_selected_connector_does_not_initialize_unrelated_collectors(monkeypatch):
    created = []
    monkeypatch.setattr(
        "collectors.__main__.CollectorRegistry.create",
        lambda name, *_: created.append(name) or
        type("C", (), {"name": name})())
    cfg = {"collectors": {
        "mist": {"enabled": True},
        "fortigate": {"enabled": True, "execution": "either"}}}
    assert [item.name for item in _enabled_collectors(
        cfg, names={"fortigate"})] == ["fortigate"]
    assert created == ["fortigate"]


@pytest.mark.parametrize("execution,runtime", [
    ("edge", "central"), ("central", "edge")])
def test_incompatible_execution_modes_are_structured(execution, runtime):
    collector = RuntimePlacementCollector("example", execution, runtime)
    with pytest.raises(ExecutionModeMismatch) as caught:
        collector.collect()
    assert caught.value.diagnostic_payload() == {
        "category": "execution_mode_mismatch",
        "message": (
            f"Collector requires execution mode '{execution}' but deployment "
            f"is running in '{runtime}' mode."),
        "collector_execution_mode": execution,
        "deployment_execution_mode": runtime,
        "remediation": (
            "Run the collector in a compatible runtime or change its explicit "
            "execution setting to a supported mode."),
    }


@pytest.mark.parametrize("execution,runtime", [
    ("edge", "edge"), ("central", "central"),
    ("either", "central"), ("either", "edge")])
def test_compatible_execution_modes_are_eligible(execution, runtime):
    from collectors.registry import CollectorRegistry
    eligible, resolved = CollectorRegistry.execution_eligible(
        "fortigate", {"execution": execution}, runtime)
    assert eligible is True
    assert resolved == execution


def test_cli_json_reports_execution_mode_mismatch_without_generic_value_error(
        tmp_path):
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        "collectors:\n"
        "  fortigate:\n"
        "    enabled: true\n"
        "    execution: edge\n")
    result = subprocess.run([
        sys.executable, "-m", "collectors",
        "--config", str(config_path),
        "inspect", "fortigate", "--json",
    ], cwd=__import__("pathlib").Path(__file__).resolve().parents[2],
       env={**__import__("os").environ, "ITP_RUNTIME_MODE": "central"},
       text=True, capture_output=True, check=False)
    assert result.returncode == 1
    payload = json.loads(next(
        line for line in result.stdout.splitlines()
        if line.startswith("{")))
    assert payload["diagnostic"]["category"] == "execution_mode_mismatch"
    assert payload["diagnostic"]["collector_execution_mode"] == "edge"
    assert payload["diagnostic"]["deployment_execution_mode"] == "central"
    assert "ValueError" not in result.stdout + result.stderr


def test_auth_header_normalization_and_token_redaction():
    seen = {}
    async def handler(request):
        seen["authorization"] = request.headers["Authorization"]
        return httpx.Response(403, request=request)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    api = FortiGateClient("fg.example.test/", "super-secret-token", client=client)
    assert api.base_url == "https://fg.example.test"
    with pytest.raises(FortiGatePermissionError) as error:
        asyncio.run(api.request("/test"))
    assert seen["authorization"] == "Bearer super-secret-token"
    assert "super-secret-token" not in str(error.value)
    asyncio.run(client.aclose())


def test_http_401_after_tls_is_authentication_not_tls_failure():
    async def handler(request):
        return httpx.Response(401, request=request)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    api = FortiGateClient("fg.example.test", "secret-token", client=client)
    with pytest.raises(FortiGateCredentialError) as raised:
        asyncio.run(api.request("/test"))
    assert raised.value.category == "authentication_failed"
    assert "secret-token" not in str(raised.value)
    asyncio.run(client.aclose())


def test_tls_inspection_runs_only_after_verified_failure_and_receives_no_secret():
    inspected = []

    async def successful(request):
        return httpx.Response(200, request=request, json={"status": "success"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(successful))
    api = FortiGateClient(
        "fg.example.test", "secret-token", client=client,
        tls_inspector=lambda *values: inspected.append(values))
    assert asyncio.run(api.request("/test")) == {"status": "success"}
    assert inspected == []
    asyncio.run(client.aclose())

    class FailedClient:
        async def get(self, *_args, **_kwargs):
            try:
                raise verification_error(
                    20, "unable to get local issuer certificate")
            except ssl.SSLError as cause:
                raise httpx.ConnectError("verified TLS failed") from cause

    def inspect(host, port):
        inspected.append((host, port))
        return {"host": host, "issuer": "CN=Unknown",
                "hostname_match": True, "expired": False, "trust": "failed"}

    api = FortiGateClient(
        "fg.example.test:8443", "secret-token", client=FailedClient(),
        tls_inspector=inspect)
    with pytest.raises(FortiGateTLSError):
        asyncio.run(api.request("/test"))
    assert inspected == [("fg.example.test", 8443)]


def test_tls_defaults_timeout_and_retry(monkeypatch):
    captured = {}
    class AsyncClient:
        def __init__(self, **kwargs): captured.update(kwargs)
        async def get(self, *_args, **_kwargs): raise httpx.ReadTimeout("slow")
        async def aclose(self): pass
    monkeypatch.setattr(httpx, "AsyncClient", AsyncClient)
    delays = []
    async def sleep(value): delays.append(value)
    api = FortiGateClient("fg.example.test", "token", max_retries=1, sleep=sleep)
    assert isinstance(captured["verify"], ssl.SSLContext)
    with pytest.raises(FortiGateTimeoutError): asyncio.run(api.request("/test"))
    assert api.retry_count == 1 and delays == [1]


def verification_error(code, message):
    error = ssl.SSLCertVerificationError(1, message)
    error.verify_code = code
    error.verify_message = message
    return error


def test_tls_failure_is_classified_separately():
    class Client:
        async def get(self, *_args, **_kwargs):
            try:
                raise verification_error(20, "unable to get local issuer certificate")
            except ssl.SSLError as cause:
                raise httpx.ConnectError("TLS connection failed") from cause
    api = FortiGateClient(
        "fg.example.test", "token", client=Client(),
        tls_inspector=lambda *_: {
            "host": "fg.example.test", "issuer": "Unknown Issuer",
            "subject": "CN=fg.example.test", "hostname_match": True,
            "expired": False, "trust": "failed"})
    with pytest.raises(FortiGateTLSError) as raised:
        asyncio.run(api.request("/test"))
    assert raised.value.category == "tls_trust_failure"
    assert raised.value.category != "unreachable"
    assert raised.value.diagnostic_payload()["tls"]["trust"] == "failed"


@pytest.mark.parametrize("code,message,evidence,error_type,category", [
    (10, "certificate has expired", {"expired": True},
     FortiGateCertificateExpiredError, "tls_certificate_expired"),
    (62, "hostname mismatch", {"hostname_match": False},
     FortiGateHostnameMismatchError, "tls_hostname_mismatch"),
    (18, "self signed certificate", {
        "subject": "CN=Private Root CA", "issuer": "CN=Private Root CA",
        "hostname_match": True},
     FortiGatePrivateCAError, "tls_untrusted_private_ca"),
    (20, "unable to get local issuer certificate", {
        "subject": "CN=firewall.example.test",
        "issuer": "CN=Sectigo RSA Domain Validation Secure Server CA",
        "hostname_match": True},
     FortiGateIncompleteChainError, "tls_incomplete_chain"),
])
def test_tls_diagnostics_are_specific_and_safe(
        code, message, evidence, error_type, category):
    api = FortiGateClient(
        "fg.example.test", "secret-token", client=object(),
        tls_inspector=lambda *_: {"host": "fg.example.test",
                                  "trust": "failed", **evidence})
    error = asyncio.run(api._tls_error(verification_error(code, message)))
    assert isinstance(error, error_type)
    payload = error.diagnostic_payload()
    assert payload["category"] == category
    assert "secret-token" not in json.dumps(payload)
    if category == "tls_incomplete_chain":
        assert "No CA import is required" in payload["remediation"]
        assert "credentials ca add" not in payload["remediation"]
    if category == "tls_untrusted_private_ca":
        assert "credentials ca add" in payload["remediation"]


def test_live_shaped_public_leaf_only_chain_is_incomplete_not_private():
    evidence = {
        "host": "fortigate.example.edu.au",
        "resolved_address": "192.0.2.10",
        "subject": "commonName=*.example.edu.au",
        "issuer": "countryName=US, organizationName=Let's Encrypt, commonName=YR2",
        "subject_attributes": {"commonName": "*.example.edu.au"},
        "issuer_attributes": {
            "countryName": "US", "organizationName": "Let's Encrypt",
            "commonName": "YR2"},
        "hostname_match": True,
        "expired": False,
        "not_before": "Jun 15 03:08:43 2026 GMT",
        "not_after": "Sep 13 03:08:42 2026 GMT",
        "presented_chain_length": 1,
        "trust": "failed",
    }
    api = FortiGateClient(
        "fortigate.example.edu.au", "must-not-appear", client=object(),
        tls_inspector=lambda *_: dict(evidence))

    error = asyncio.run(api._tls_error(verification_error(
        21, "unable to verify the first certificate")))
    payload = error.diagnostic_payload()

    assert isinstance(error, FortiGateIncompleteChainError)
    assert payload["category"] == "tls_incomplete_chain"
    assert payload["tls"]["public_issuer"] is True
    assert payload["tls"]["presented_chain_length"] == 1
    assert payload["tls"]["verify_code"] == 21
    assert payload["tls"]["verify_message"] == (
        "unable to verify the first certificate")
    assert "full certificate chain" in payload["remediation"]
    assert "No CA import is required" in payload["remediation"]
    assert "credentials ca add" not in payload["remediation"]
    assert "must-not-appear" not in json.dumps(payload)


def test_ambiguous_chain_failure_remains_neutral_trust_failure():
    api = FortiGateClient(
        "fg.example.test", "token", client=object(),
        tls_inspector=lambda *_: {
            "host": "fg.example.test", "subject": "CN=fg.example.test",
            "issuer": "CN=Unknown Issuer", "hostname_match": True,
            "expired": False, "presented_chain_length": 1,
            "trust": "failed"})
    error = asyncio.run(api._tls_error(verification_error(
        21, "unable to verify the first certificate")))
    assert type(error) is FortiGateTLSError
    assert error.category == "tls_trust_failure"
    assert "credentials ca add" not in error.remediation
    assert "verify_tls: false" not in error.remediation
    assert "Do not disable TLS verification" in error.remediation


def test_inspection_exception_retains_authoritative_failure_evidence():
    def unavailable(*_args):
        raise AttributeError("runtime chain inspection unavailable")

    api = FortiGateClient(
        "fg.example.test", "token", client=object(),
        tls_inspector=unavailable)
    error = asyncio.run(api._tls_error(verification_error(
        20, "unable to get local issuer certificate")))
    payload = error.diagnostic_payload()
    assert type(error) is FortiGateTLSError
    assert payload["tls"] == {
        "host": "fg.example.test",
        "inspection_status": "failed",
        "public_issuer": None,
        "trust": "failed",
        "verify_code": 20,
        "verify_message": "unable to get local issuer certificate",
    }


def test_optional_endpoint_and_partial_collection(tmp_path):
    class Client:
        api_requests = 0; retry_count = 0; base_url = "https://fg.example.test"
        async def endpoint(self, name, optional=False):
            self.api_requests += 1
            if name == "system": return EndpointResult(name, {"results": {"hostname": "edge-fw", "serial": "FG123", "version": "7.4"}})
            if name == "resources": return EndpointResult(name, {"results": {"cpu": 12, "memory": 48}})
            if name == "interfaces": return EndpointResult(name, {"results": [{"name": "port1", "status": "up", "rx_bytes": 10, "tx_bytes": 20}]})
            return EndpointResult(name, available=False, category="unsupported", message="unsupported")
        async def close(self): pass
    writes = []
    class Writer:
        def write(self, points): writes.extend(points); return len(points)
    collector = FortiGateCollector(config(), str(tmp_path / "devices.json"), client=Client(), writer=Writer())
    assert asyncio.run(collector.collect()) > 0
    health = [point for point in writes if point["measurement"] == "collector_health"][-1]
    assert health["fields"]["success"] is True
    assert health["fields"]["partial"] is True
    assert health["fields"]["error_count"] == 1
    assert health["tags"]["diagnostic_category"] == "partial"
    measurements = {point["measurement"] for point in writes}
    assert {"infrastructure_device", "network_interface", "security_appliance",
            "fortigate_system", "fortigate_performance", "fortigate_interfaces",
            "collector_health"} <= measurements


def test_stable_identity_normalization_and_snmp_compatibility():
    settings = type("Settings", (), {"base_url": "https://10.0.0.1", "customer": "c", "site": "s"})()
    endpoint_data = {"system": {"results": {"hostname": "FG-A", "serial": "FGSERIAL",
        "model": "FG-100F", "version": "7.4", "ip": "10.0.0.1", "uptime": 100}},
        "resources": {"results": {"cpu": 5, "memory": 40}},
        "interfaces": {"results": [{"name": "port1", "in_octets": 10, "out_octets": 20}]}}
    record, points = normalize(endpoint_data, settings)
    assert stable_id({"serial": "FGSERIAL"}, "10.0.0.1") == "fortigate:FGSERIAL"
    assert record["id"] == "fortigate:FGSERIAL"
    assert record["ip"] == "10.0.0.1"
    assert record["source_asset_id"] == "FGSERIAL"
    assert {point["measurement"] for point in points} == {
        "infrastructure_device", "network_interface", "security_appliance",
        "device", "availability", "performance", "interface", "firewall"}
    fallback = stable_id({"hostname": "FG-A"}, "10.0.0.1")
    assert fallback == stable_id({"hostname": "fg-a"}, "10.0.0.1")
