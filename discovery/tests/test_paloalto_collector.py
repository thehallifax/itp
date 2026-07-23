import asyncio
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx
import pytest

from analysis.infrastructure.adapters import AdapterResult
from analysis.infrastructure.fusion import FusionEngine
from collectors.paloalto.api import PaloAltoClient
from collectors.paloalto.collector import PaloAltoCollector, validate_settings
from collectors.paloalto.mapper import map_snapshot
from collectors.paloalto.models import (CapabilityResult, PaloAltoCredentialError,
                                        Snapshot)
from collectors.paloalto import parser


SYSTEM = """<response status="success"><result><system>
<hostname>pa-edge-01</hostname><serial>0123456789</serial><model>PA-440</model>
<sw-version>11.1.4-h1</sw-version><ip-address>192.0.2.1</ip-address>
<uptime>12 days, 3 hours, 5 minutes</uptime><family>vm</family>
<app-version>8891-9012</app-version><av-version>4912-5421</av-version>
</system></result></response>"""
HA_HEALTHY = """<response status="success"><result><enabled>yes</enabled>
<group><mode>Active-Passive</mode><local-info><state>active</state><priority>100</priority>
</local-info><peer-info><state>passive</state><serial>0099999999</serial>
<configuration-synchronized>yes</configuration-synchronized></peer-info></group>
</result></response>"""
HA_DEGRADED = """<response status="success"><result><enabled>yes</enabled>
<group><local-info><state>suspended</state></local-info><peer-info><state>down</state>
</peer-info></group></result></response>"""
INTERFACES = """<response status="success"><result><ifnet>
<entry name="ae1"><type>aggregate</type><state>up</state><status>up</status></entry>
<entry name="ethernet1/1"><type>ethernet</type><state>up</state><status>up</status>
<speed>10000</speed><duplex>full</duplex><mac>00:11:22:33:44:55</mac></entry>
<entry name="ethernet1/2"><type>ethernet</type><state>up</state><status>down</status></entry>
<entry name="ethernet1/1.10"><type>layer3</type><state>up</state><status>up</status>
<aggregate-group>ae1</aggregate-group><zone>trusted</zone></entry>
</ifnet></result></response>"""
LICENSES = """<response status="success"><result><licenses>
<entry name="Threat Prevention"><expires>2024-01-01</expires><expired>yes</expired></entry>
<entry name="WildFire"><expires>2099-01-01</expires><expired>no</expired></entry>
</licenses></result></response>"""


def result(xml):
    return ET.fromstring(xml).find("./result")


def config(**updates):
    settings = {"enabled": True, "site": "customer-site-slug",
        "customer": "Example Customer", "base_url": "https://192.0.2.1",
        "api_key_env": "PALOALTO_API_KEY", "verify_tls": True,
        "expected_interfaces": ["ethernet1/2"]}
    settings.update(updates)
    return {"collectors": {"paloalto": settings}, "inventory": {}}


def test_parser_identity_ha_interfaces_licences_and_resources():
    identity = parser.system(result(SYSTEM))
    assert identity["hostname"] == "pa-edge-01"
    assert identity["model"] == "PA-440"
    assert identity["software_version"] == "11.1.4-h1"
    assert identity["uptime_seconds"] == 12 * 86400 + 3 * 3600 + 5 * 60
    assert parser.ha(result(HA_HEALTHY))["status"] == "healthy"
    assert parser.ha(result(HA_DEGRADED))["status"] == "degraded"
    assert parser.ha(result('<response status="success"><result><enabled>no</enabled></result></response>'))["status"] == "standalone"
    interfaces = parser.interfaces(result(INTERFACES))
    assert [item["name"] for item in interfaces] == [
        "ae1", "ethernet1/1", "ethernet1/1.10", "ethernet1/2"]
    assert next(item for item in interfaces if item["name"].endswith(".10"))["logical"] is True
    assert next(item for item in interfaces if item["name"] == "ae1")["logical"] is False
    licences = parser.licenses(result(LICENSES),
        now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert licences[0]["expired"] is True
    assert parser.resources(result(
        '<response status="success"><result>CPU load 17% memory used 62% sessions 1,234</result></response>'
    )) == {"management_cpu_percent": 17.0, "memory_percent": 62.0,
           "session_count": 1234.0}
    assert parser.resources(result(
        '<response status="success"><result>not structured on this release</result></response>')) == {}


def test_mapper_is_canonical_and_does_not_create_ha_peer(monkeypatch):
    monkeypatch.setenv("PALOALTO_API_KEY", "obviously-fake-test-key")
    settings = validate_settings(config())
    parsed = {"system": parser.system(result(SYSTEM)), "ha": parser.ha(result(HA_HEALTHY)),
              "interfaces": parser.interfaces(result(INTERFACES)),
              "licenses": parser.licenses(result(LICENSES)), "resources": {}}
    record, points = map_snapshot(parsed, settings, "2026-01-01T00:00:00Z")
    assert record["source"] == "paloalto"
    assert record["vendor"] == "Palo Alto Networks"
    assert record["device_type"] == "firewall"
    assert record["serial_number"] == "0123456789"
    assert record["site"] == "customer-site-slug"
    assert record["extensions"]["interface_summary"]["expected_down"] == ["ethernet1/2"]
    assert all("0099999999" not in str(point) for point in points)
    assert {point["measurement"] for point in points} >= {
        "device", "availability", "firewall", "interface"}


def test_fuses_with_snmp_by_serial(monkeypatch):
    monkeypatch.setenv("PALOALTO_API_KEY", "fake")
    record, _ = map_snapshot({"system": parser.system(result(SYSTEM))},
                             validate_settings(config()), "2026-01-01T00:00:00Z")
    snmp = {"source": "snmp", "collector": "snmp", "source_asset_id": "192.0.2.1",
            "serial_number": "0123456789", "hostname": "pa-edge-01",
            "management_ip": "192.0.2.1", "site": "customer-site-slug",
            "device_type": "firewall", "online": True}
    assets, stats, _ = FusionEngine().fuse([
        AdapterResult("paloalto", 200, assets=[record]),
        AdapterResult("snmp", 100, assets=[snmp])])
    assert len(assets) == 1
    assert assets[0]["sources"] == ["paloalto", "snmp"]
    assert stats["records_fused"] == 1


def test_configuration_tls_defaults_custom_ca_and_disabled_secret(tmp_path, monkeypatch):
    monkeypatch.delenv("PALOALTO_API_KEY", raising=False)
    with pytest.raises(ValueError, match="PALOALTO_API_KEY"):
        validate_settings(config())
    settings = validate_settings(config(), require_key=False)
    assert settings.verify_tls is True
    assert PaloAltoClient.normalize_url("192.0.2.1") == "https://192.0.2.1"
    with pytest.raises(ValueError, match="HTTPS"):
        PaloAltoClient.normalize_url("http://192.0.2.1")
    ca = tmp_path / "ca.pem"; ca.write_text("test")
    monkeypatch.setenv("PALOALTO_API_KEY", "fake")
    assert validate_settings(config(ca_bundle=str(ca))).ca_bundle == str(ca)


def test_api_header_auth_response_validation_and_secret_redaction():
    seen = {}
    def handler(request):
        seen["headers"] = dict(request.headers); seen["url"] = str(request.url)
        return httpx.Response(200, text=SYSTEM)
    client = PaloAltoClient("https://192.0.2.1/", "super-secret-value",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    parsed = asyncio.run(client.op("system"))
    asyncio.run(client.close())
    assert parsed.findtext(".//hostname") == "pa-edge-01"
    assert seen["headers"]["x-pan-key"] == "super-secret-value"
    assert "super-secret-value" not in seen["url"]

    bad = PaloAltoClient("https://192.0.2.1", "super-secret-value",
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request:
            httpx.Response(200, text='<response status="error"><msg>Invalid API key super-secret-value</msg></response>'))))
    with pytest.raises(PaloAltoCredentialError) as caught:
        asyncio.run(bad.op("system"))
    assert "super-secret-value" not in str(caught.value)


@pytest.mark.parametrize("payload,category", [
    ("not xml", "invalid_response"),
    ('<response status="success"></response>', "invalid_response"),
    ('<response status="error"><msg>Not authorized for command</msg></response>', "permission"),
    ('<response status="error"><msg>Unknown command</msg></response>', "unsupported"),
])
def test_api_safe_failure_categories(payload, category):
    client = PaloAltoClient("https://198.51.100.10", "fake",
        client=httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=payload))))
    with pytest.raises(Exception) as caught:
        asyncio.run(client.op("system"))
    assert getattr(caught.value, "category") == category
    assert "fake" not in str(caught.value)


class FakeWriter:
    def __init__(self): self.points = []
    def write(self, points): self.points.extend(points); return len(points)


class FakeClient:
    api_requests = 0; retry_count = 0; base_url = "https://192.0.2.1"
    def __init__(self, optional_failure=False): self.optional_failure = optional_failure
    async def op(self, name):
        self.api_requests += 1
        return result(SYSTEM)
    async def capability(self, name):
        self.api_requests += 1
        if self.optional_failure and name == "licenses":
            return CapabilityResult(name, available=False, category="permission",
                                    message="safe permission error")
        fixtures = {"ha": HA_HEALTHY, "interfaces": INTERFACES, "resources":
                    '<response status="success"><result>CPU load 17%</result></response>',
                    "licenses": LICENSES}
        return CapabilityResult(name, data=result(fixtures[name]))
    async def close(self): pass


def test_partial_collection_and_clean_idempotent_inventory(tmp_path, monkeypatch):
    monkeypatch.setenv("PALOALTO_API_KEY", "fake")
    writer = FakeWriter()
    collector = PaloAltoCollector(config(), tmp_path / "devices.json",
                                  client=FakeClient(optional_failure=True), writer=writer)
    first = asyncio.run(collector.discover())
    second = asyncio.run(collector.discover())
    collected = asyncio.run(collector.collect())
    assert len(first["devices"]) == len(second["devices"]) == 1
    assert collected["status"] == "partial"
    assert collected["capabilities_unavailable"] == ["licenses"]
    health = [point for point in writer.points if point["measurement"] == "collector_health"][-1]
    assert health["fields"]["success"] is True
    assert health["fields"]["partial"] is True
    assets = json.loads((tmp_path / "assets.json").read_text())["assets"]
    assert len(assets) == 1
    assert assets[0]["collector"] == "paloalto"
