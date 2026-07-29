import asyncio
import functools
import json
from pathlib import Path

import httpx
import pytest

from analysis.dashboards import DashboardRegistry
from collectors.aruba.client import ArubaCentralClient, ArubaOAuthTokenManager
from collectors.aruba.collector import ArubaCentralCollector, validate_settings
from collectors.aruba.models import (
    ArubaCentralCredentialError,
    ArubaCentralPermissionError,
    ArubaCentralTokenExpiredError,
    ArubaCentralUnavailableError,
    ArubaCentralUnsupportedError,
)
from collectors.aruba.normalizer import normalize
from collectors.capabilities import CapabilityManifestEngine

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((
    ROOT / "collectors/aruba/fixtures/inventory.json").read_text())


def async_test(function):
    @functools.wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return run


def config(**overrides):
    settings = {
        "enabled": True,
        "execution": "central",
        "base_url": "https://central.example.test",
        "token_url": "https://sso.example.test/as/token.oauth2",
        "auth_mode": "client_credentials",
        "client_id": "fixture-client",
        "client_secret": "fixture-secret",
        "account_id": "account-reference",
        "site": "site:reference",
        "customer": "reference",
        "customer_id": "reference",
    }
    settings.update(overrides)
    return {
        "schema_version": 1,
        "deployment_id": "reference",
        "customer_id": "reference",
        "customer": "reference",
        "site": "site:reference",
        "site_name": "Reference Campus",
        "collectors": {"aruba": settings},
        "inventory": {},
        "writer": {},
    }


@async_test
async def test_oauth_client_credentials_and_cached_token():
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(200, json={
            "access_token": "access-one", "expires_in": 7200})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    manager = ArubaOAuthTokenManager(
        "https://sso.example.test/token", "client", "secret",
        client=client, now=lambda: 100)
    assert await manager.token() == "access-one"
    assert await manager.token() == "access-one"
    assert len(requests) == 1
    assert "client_credentials" in (await requests[0].aread()).decode()
    assert "secret" not in repr(manager)
    await client.aclose()


@async_test
async def test_refresh_token_rotation():
    async def handler(request):
        return httpx.Response(200, json={
            "access_token": "access-two",
            "refresh_token": "refresh-two", "expires_in": 7200})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    manager = ArubaOAuthTokenManager(
        "https://central.example.test/oauth2/token", "client", "secret",
        refresh_token="refresh-one", auth_mode="refresh_token",
        client=client, now=lambda: 100)
    assert await manager.token() == "access-two"
    assert manager._refresh_token == "refresh-two"
    await client.aclose()


@async_test
async def test_api_401_forces_one_automatic_refresh():
    class Tokens:
        def __init__(self):
            self.calls = []

        async def token(self, force=False):
            self.calls.append(force)
            return "renewed" if force else "expired"

        async def close(self):
            pass

    async def handler(request):
        if request.headers["Authorization"] == "Bearer expired":
            return httpx.Response(401)
        return httpx.Response(200, json={"devices": []})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tokens = Tokens()
    client = ArubaCentralClient(
        "https://central.example.test", tokens, client=http)
    assert await client.get("access_points") == {"devices": []}
    assert tokens.calls == [False, True]
    await http.aclose()


@async_test
async def test_rejected_refreshed_token_is_token_expired():
    class Tokens:
        async def token(self, force=False):
            return "still-expired"

        async def close(self):
            pass

    async def handler(request):
        return httpx.Response(401)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = ArubaCentralClient(
        "https://central.example.test", Tokens(), client=http)
    with pytest.raises(ArubaCentralTokenExpiredError):
        await client.get("access_points")
    await http.aclose()


@async_test
@pytest.mark.parametrize("status,error", [
    (403, ArubaCentralPermissionError),
    (404, ArubaCentralUnsupportedError),
    (503, ArubaCentralUnavailableError),
])
async def test_api_failure_categories(status, error):
    class Tokens:
        async def token(self, force=False):
            return "token"

        async def close(self):
            pass

    async def handler(request):
        return httpx.Response(status)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = ArubaCentralClient(
        "https://central.example.test", Tokens(), client=http,
        max_retries=0)
    with pytest.raises(error):
        await client.get("access_points")
    await http.aclose()


@async_test
async def test_invalid_oauth_credentials_are_actionable_and_redacted():
    async def handler(request):
        return httpx.Response(401, json={"error": "invalid_client"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    manager = ArubaOAuthTokenManager(
        "https://sso.example.test/token", "client", "do-not-log",
        client=http)
    with pytest.raises(ArubaCentralCredentialError) as captured:
        await manager.token()
    assert "do-not-log" not in str(captured.value)
    await http.aclose()


def test_inventory_normalization_uses_canonical_identity():
    settings = validate_settings(config())
    records, points = normalize(
        FIXTURE, settings, "2026-01-01T00:00:00Z")
    assert [value["id"] for value in records] == [
        "aruba:CN00000001", "aruba:CN00000002"]
    assert all(value["deployment_id"] == "reference" for value in records)
    assert all(value["customer_id"] == "reference" for value in records)
    assert all(value["site_id"] == "site:reference" for value in records)
    assert records[0]["device_type"] == "access-point"
    assert records[0]["extensions"]["aruba_central"]["group"] == "Campus"
    assert records[1]["online"] is False
    assert {value["measurement"] for value in points} == {
        "device", "availability", "wireless"}
    assert all(value["tags"]["device_id"].startswith("aruba:")
               for value in points)


class FixtureClient:
    api_requests = 0
    retry_count = 0

    async def snapshot(self):
        self.api_requests += 4
        devices = FIXTURE["devices"]["devices"]
        return {
            "groups": FIXTURE["groups"],
            "sites": FIXTURE["sites"],
            "device_classes": {
                "access_points": {"aps": [devices[0]]},
                "switches": {"switches": [devices[1]]},
                "gateways": {"gateways": []},
            },
            "alerts": FIXTURE["alerts"],
            "partial": False,
            "diagnostics": {
                "access_points": {"state": "collected"},
                "switches": {"state": "collected"},
                "gateways": {"state": "collected"},
                "alerts": {"state": "collected"},
            },
        }

    async def close(self):
        pass


class EmptyFixtureClient(FixtureClient):
    async def snapshot(self):
        return {
            "groups": {"groups": []}, "sites": {"sites": []},
            "device_classes": {
                "access_points": {"aps": []},
                "switches": {"switches": []},
                "gateways": {"gateways": []},
            },
            "alerts": {"alerts": []},
            "partial": False,
            "diagnostics": {
                "access_points": {"state": "collected"},
                "switches": {"state": "collected"},
                "gateways": {"state": "collected"},
                "alerts": {"state": "collected"},
            },
        }


class APOnlyFixtureClient(FixtureClient):
    async def snapshot(self):
        snapshot = await super().snapshot()
        snapshot["device_classes"]["switches"] = {"switches": []}
        return snapshot


class FixtureWriter:
    def __init__(self):
        self.points = []

    def write(self, points):
        self.points.extend(points)
        return len(points)


@async_test
async def test_discovery_collection_and_health_are_deterministic(tmp_path):
    writer = FixtureWriter()
    collector = ArubaCentralCollector(
        config(), tmp_path / "inventory/devices.json",
        client=FixtureClient(), writer=writer)
    discovered = await collector.discover()
    collected = await collector.collect()
    assert len([value for value in discovered["devices"]
                if value["source"] == "aruba"]) == 2
    assert {key: collected[key] for key in (
        "status", "points_written", "assets_returned", "partial")} == {
            "status": "success", "points_written": 5,
            "assets_returned": 2, "partial": False}
    assert collected["capability_states"]["inventory"] == "collected"
    assert collected["capability_resources"]["access_point_inventory"] == 1
    assert collected["capability_resources"]["switch_inventory"] == 1
    assert collected["capability_resources"]["gateway_inventory"] == 0
    assert writer.points[-1]["measurement"] == "collector_health"
    assert writer.points[-1]["tags"]["site_id"] == "site:reference"
    assert writer.points[-1]["fields"]["devices_returned"] == 2


@async_test
async def test_no_devices_is_distinct_from_collector_failure(tmp_path):
    collector = ArubaCentralCollector(
        config(), tmp_path / "inventory/devices.json",
        client=EmptyFixtureClient(), writer=FixtureWriter())
    inspected = await collector.inspect()
    collected = await collector.collect()
    assert inspected["diagnostics"]["category"] == "no_devices"
    assert inspected["diagnostics"]["authentication_successful"] is True
    assert collected["status"] == "no_devices"
    assert collected["capability_states"]["inventory"] == "collected"
    assert collected["capability_resources"]["inventory"] == 0


@async_test
async def test_ap_only_tenant_with_no_switches_or_gateways_is_healthy(tmp_path):
    collector = ArubaCentralCollector(
        config(), tmp_path / "inventory/devices.json",
        client=APOnlyFixtureClient(), writer=FixtureWriter())
    inspected = await collector.inspect()
    collected = await collector.collect()
    assert inspected["access_point_count"] == 1
    assert inspected["switch_count"] == 0
    assert inspected["gateway_count"] == 0
    assert inspected["partial"] is False
    assert inspected["diagnostics"]["category"] == "healthy"
    assert collected["status"] == "success"
    assert collected["partial"] is False
    for capability in ("access_point_inventory", "switch_inventory",
                       "gateway_inventory"):
        assert collected["capability_states"][capability] == "collected"
    assert collected["capability_resources"]["switch_inventory"] == 0
    assert collected["capability_resources"]["gateway_inventory"] == 0


class StaticTokens:
    async def token(self, force=False):
        return "safe-token"

    async def close(self):
        pass


def central_response(path, *, switch_status=200, gateway_status=200,
                     alert_status=200, ap_status=200):
    statuses = {
        "/monitoring/v2/aps": ap_status,
        "/monitoring/v1/switches": switch_status,
        "/monitoring/v1/gateways": gateway_status,
        "/central/v1/alerts": alert_status,
    }
    payloads = {
        "/configuration/v2/groups": {"groups": [{"group": "Campus"}]},
        "/central/v2/sites": {"sites": [{"site_name": "Reference"}]},
        "/monitoring/v2/aps": {
            "aps": [FIXTURE["devices"]["devices"][0]]},
        "/monitoring/v1/switches": {"switches": []},
        "/monitoring/v1/gateways": {"gateways": []},
        "/central/v1/alerts": {"alerts": []},
    }
    return httpx.Response(statuses.get(path, 200), json=payloads.get(path, {}))


@async_test
@pytest.mark.parametrize(("device_class", "status", "expected"), [
    ("switches", 404, "unsupported_endpoint"),
    ("switches", 403, "insufficient_permissions"),
    ("gateways", 503, "api_unavailable"),
])
async def test_optional_device_class_failure_preserves_ap_inventory(
        device_class, status, expected):
    async def handler(request):
        kwargs = {
            "switch_status": status
        } if device_class == "switches" else {
            "gateway_status": status
        }
        return central_response(request.url.path, **kwargs)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = ArubaCentralClient(
        "https://central.example.test", StaticTokens(), client=http,
        max_retries=0)
    snapshot = await client.snapshot()
    settings = validate_settings(config())
    records, _ = normalize(snapshot, settings, "2026-01-01T00:00:00Z")
    assert [record["device_type"] for record in records] == ["access-point"]
    assert snapshot["partial"] is False
    assert snapshot["diagnostics"][device_class]["state"] == expected
    await http.aclose()


@async_test
async def test_ap_endpoint_failure_fails_collection():
    async def handler(request):
        return central_response(request.url.path, ap_status=503)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = ArubaCentralClient(
        "https://central.example.test", StaticTokens(), client=http,
        max_retries=0)
    with pytest.raises(ArubaCentralUnavailableError):
        await client.snapshot()
    await http.aclose()


@async_test
async def test_optional_alert_failure_does_not_fail_inventory():
    async def handler(request):
        return central_response(request.url.path, alert_status=403)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = ArubaCentralClient(
        "https://central.example.test", StaticTokens(), client=http,
        max_retries=0)
    snapshot = await client.snapshot()
    records, _ = normalize(
        snapshot, validate_settings(config()), "2026-01-01T00:00:00Z")
    assert len(records) == 1
    assert snapshot["partial"] is False
    assert snapshot["diagnostics"]["alerts"]["state"] == \
        "insufficient_permissions"
    await http.aclose()


@async_test
async def test_optional_device_class_endpoint_can_be_explicitly_disabled():
    requested = []

    async def handler(request):
        requested.append(request.url.path)
        return central_response(request.url.path)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = ArubaCentralClient(
        "https://central.example.test", StaticTokens(), client=http,
        endpoints={"switches": ""})
    snapshot = await client.snapshot()
    assert snapshot["diagnostics"]["switches"] == {
        "state": "endpoint_disabled", "resource_count": 0}
    assert "/monitoring/v1/switches" not in requested
    assert snapshot["partial"] is False
    await http.aclose()


def test_capability_manifest_exposes_ops015_dimensions(tmp_path):
    source = {
        "sources": {"aruba": {"last_run": {
            "success": True, "partial": False, "records_returned": 2,
            "completed_at": "2026-01-01T00:00:00Z"}}}}
    path = tmp_path / "inventory/source_runs.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(source))
    result = CapabilityManifestEngine(config(), tmp_path).build()
    manifest = result["collectors"]["aruba"]
    assert manifest["identity"] == {
        "deployment_id": "reference",
        "customer_id": "reference",
        "site_id": "site:reference"}
    capabilities = {value["id"]: value for value in manifest["capabilities"]}
    for name in (
        "access_point_inventory", "switch_inventory", "gateway_inventory",
        "inventory", "device_health", "firmware", "alerts", "client_counts",
        "groups", "sites", "account", "collector_diagnostics",
    ):
        assert capabilities[name]["support"] in {"supported", "conditional"}
        assert capabilities[name]["configured"] is True
        assert isinstance(capabilities[name]["available"], bool)
        assert isinstance(capabilities[name]["collectable"], bool)
    assert capabilities["inventory"]["collection"] == "collected"


def test_optional_device_class_failure_does_not_degrade_collector_state(tmp_path):
    scheduler = {
        "connectors": {
            "aruba": {
                "last_collection_outcome": "success",
                "last_collection_result": {
                    "capability_states": {
                        "account": "collected",
                        "groups": "collected",
                        "sites": "collected",
                        "inventory": "collected",
                        "access_point_inventory": "collected",
                        "switch_inventory": "unavailable",
                        "gateway_inventory": "collected",
                        "device_health": "collected",
                        "firmware": "collected",
                        "alerts": "unavailable",
                        "client_counts": "collected",
                        "collector_diagnostics": "collected",
                    },
                    "capability_resources": {
                        "access_point_inventory": 24,
                        "switch_inventory": 0,
                        "gateway_inventory": 0,
                    },
                },
            },
        },
    }
    path = tmp_path / "scheduler/state.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(scheduler))
    manifest = CapabilityManifestEngine(config(), tmp_path).build()[
        "collectors"]["aruba"]
    capabilities = {
        value["id"]: value for value in manifest["capabilities"]
    }
    assert manifest["execution"]["state"] == "collected"
    assert capabilities["access_point_inventory"]["resource_count"] == 24
    assert capabilities["switch_inventory"]["collection"] == "unavailable"
    assert capabilities["switch_inventory"]["resource_count"] == 0
    assert capabilities["switch_inventory"]["health_impact"] is False


def test_dashboard_registry_exposes_service_capabilities_without_dashboard(
        tmp_path):
    result = DashboardRegistry(
        ROOT, {"collectors": {"aruba": {"enabled": True}}},
        tmp_path / "managed", tmp_path / "dashboards.yml").resolve()
    assert {"inventory", "switching", "telemetry", "wireless"} <= set(
        result["capabilities"])
    assert not [value for value in result["dashboards"]
                if value["collector"] == "aruba"]


@pytest.mark.parametrize("overrides,message", [
    ({"client_id": ""}, "client_id"),
    ({"client_secret": ""}, "client_secret"),
    ({"auth_mode": "refresh_token", "refresh_token": ""}, "refresh_token"),
    ({"base_url": "http://central.example.test"}, "HTTPS"),
])
def test_configuration_validation(overrides, message):
    with pytest.raises(ValueError, match=message):
        validate_settings(config(**overrides))
