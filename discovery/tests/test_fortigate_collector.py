import asyncio
import json
import ssl

import httpx
import pytest

from collectors.__main__ import _enabled_collectors
from collectors.config import load_config
from collectors.fortigate.client import FortiGateClient
from collectors.fortigate.collector import FortiGateCollector
from collectors.fortigate.models import (EndpointResult, FortiGatePermissionError,
                                          FortiGateTimeoutError)
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
    assert captured["verify"] is True
    with pytest.raises(FortiGateTimeoutError): asyncio.run(api.request("/test"))
    assert api.retry_count == 1 and delays == [1]


def test_tls_failure_is_classified_separately():
    class Client:
        async def get(self, *_args, **_kwargs):
            try:
                raise ssl.SSLCertVerificationError("untrusted certificate")
            except ssl.SSLError as cause:
                raise httpx.ConnectError("TLS connection failed") from cause
    from collectors.fortigate.models import FortiGateTLSError
    api = FortiGateClient("fg.example.test", "token", client=Client())
    with pytest.raises(FortiGateTLSError, match="TLS verification failed"):
        asyncio.run(api.request("/test"))


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
