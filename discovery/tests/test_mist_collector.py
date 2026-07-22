import asyncio
import json

import httpx
import pytest

from collectors import CollectorRegistry
from collectors.config import load_config
from collectors.inventory import InventoryManager
from collectors.mist.client import MistClient
from collectors.mist.collector import MistCollector
from collectors.mist.models import MistAuthenticationError, MistAuthorizationError, MistError, MistPaginationError
from collectors.mist.normalizer import device_kind, metric_fields, metric_points, normalize_device, stable_id
from collectors.writer import InfluxWriter
from collectors.__main__ import inspection_lines
from collectors.base import BaseCollector
from collectors.scheduler import Scheduler


async def no_sleep(_): pass


def client_with(handler, **kwargs):
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="https://api.mist.test")
    return MistClient("https://api.mist.test", "org", "super-secret-token",
                      client=http, sleep=no_sleep, **kwargs), http


def test_mist_registered_and_missing_credentials():
    assert CollectorRegistry.get("mist") is MistCollector
    with pytest.raises(ValueError, match="MIST_ORG_ID"):
        MistCollector({"collectors": {"mist": {"enabled": True}}})


def test_configuration_environment_loading(tmp_path, monkeypatch):
    path = tmp_path / "config.yml"
    path.write_text("collectors:\n  mist:\n    enabled: true\n    organization_id: ${MIST_ORG_ID}\n    api_token: ${MIST_API_TOKEN}\n")
    monkeypatch.setenv("MIST_ORG_ID", "org-1"); monkeypatch.setenv("MIST_API_TOKEN", "token-1")
    config = load_config(path)
    assert config["collectors"]["mist"]["organization_id"] == "org-1"
    assert config["collectors"]["mist"]["api_token"] == "token-1"


def test_pagination_and_authorization_header():
    seen = []
    def handler(request):
        seen.append(request)
        page = int(request.url.params["page"])
        return httpx.Response(200, json=[{"id": page}], headers={"X-Page-Total": "2", "X-Page-Limit": "1"})
    client, http = client_with(handler, page_limit=1)
    try: assert asyncio.run(client.paginated_get("/items")) == [{"id": 1}, {"id": 2}]
    finally: asyncio.run(http.aclose())
    assert len(seen) == 2 and seen[0].headers["Authorization"] == "Token super-secret-token"


def test_pagination_safety_limit():
    client, http = client_with(lambda request: httpx.Response(200, json=[{}], headers={"X-Page-Limit": "1"}), page_limit=1, max_pages=2)
    try:
        with pytest.raises(MistPaginationError): asyncio.run(client.paginated_get("/items"))
    finally: asyncio.run(http.aclose())


@pytest.mark.parametrize("status,error", [(401, MistAuthenticationError), (403, MistAuthorizationError)])
def test_auth_errors_are_actionable_and_redacted(status, error):
    client, http = client_with(lambda request: httpx.Response(status, text="super-secret-token raw body"))
    try:
        with pytest.raises(error) as caught: asyncio.run(client.paginated_get("/items"))
    finally: asyncio.run(http.aclose())
    assert "super-secret-token" not in str(caught.value) and "raw body" not in str(caught.value)


@pytest.mark.parametrize("status", [429, 500, 503])
def test_transient_errors_retry(status):
    calls = 0
    def handler(request):
        nonlocal calls; calls += 1
        return httpx.Response(status, headers={"Retry-After": "0"}) if calls == 1 else httpx.Response(200, json=[])
    client, http = client_with(handler)
    try: assert asyncio.run(client.paginated_get("/items")) == []
    finally: asyncio.run(http.aclose())
    assert calls == 2 and client.retry_count == 1


def test_permanent_4xx_does_not_retry():
    calls = 0
    def handler(request):
        nonlocal calls; calls += 1; return httpx.Response(404, text="secret body")
    client, http = client_with(handler)
    try:
        with pytest.raises(MistError): asyncio.run(client.paginated_get("/items"))
    finally: asyncio.run(http.aclose())
    assert calls == 1


@pytest.mark.parametrize("device,expected", [
    ({"type": "ap", "model": "AP45"}, ("wireless-access-point", "access-point")),
    ({"type": "switch", "model": "EX4300"}, ("network-switch", "switch")),
    ({"type": "mxedge"}, ("mist-edge", "mist-edge")),
    ({"type": "gateway", "model": "SSR120"}, ("wan-edge", "wan-edge")),
])
def test_device_classification(device, expected):
    assert device_kind(device) == expected


def test_normalization_stable_id_and_offline_state():
    device = {"id": "device-1", "type": "ap", "model": "AP34", "site_id": "site-1", "name": "AP"}
    record = normalize_device(device, {"status": "disconnected"}, {"site-1": "School"}, "org", "customer", "default")
    assert stable_id(device, "org") == "mist:device-1"
    assert record["platform"] == "wireless-access-point" and record["operational_status"] == "disconnected"


def test_source_inventory_preserves_first_seen_stale_and_snmp(tmp_path):
    manager = InventoryManager(tmp_path / "devices.json")
    manager.write({"schema_version": 1, "devices": [{"ip": "192.0.2.1", "sys_object_id": "oid", "status": "active"}]})
    record = {"id": "mist:one", "source": "mist", "collector": "mist", "operational_status": "offline"}
    manager.update_source([record], "mist", "c", "s", "2026-01-01T00:00:00Z")
    result = manager.update_source([], "mist", "c", "s", "2026-01-02T00:00:00Z")
    snmp = next(d for d in result["devices"] if "sys_object_id" in d)
    mist = next(d for d in result["devices"] if d.get("source") == "mist")
    assert snmp["ip"] == "192.0.2.1"
    assert mist["first_seen"] == "2026-01-01T00:00:00Z" and mist["status"] == "stale"
    assert mist["operational_status"] == "offline"
    asset = next(item for item in manager.engine.list_assets() if item["source"] == "mist")
    assert asset["collector"] == "mist" and asset["lifecycle_state"] == "offline"


def test_missing_metric_fields_are_not_zero():
    assert metric_fields({"status": "connected"}) == {"online": True}
    record = {"id": "mist:one", "customer": "c", "site": "s", "vendor": "juniper",
              "platform": "wireless-access-point", "device_role": "access-point",
              "collector": "mist", "hostname": "ap", "model": "AP34"}
    points = metric_points(record, {"status": "connected", "num_clients": 4})
    assert points[0]["fields"] == {"online": True, "client_count": 4}
    assert {point["measurement"] for point in points} == {
        "infrastructure_device", "wireless_access_point", "device", "availability", "wireless"}


def test_line_protocol_escaping_and_field_types():
    line = InfluxWriter.line_protocol({"measurement": "test metric", "tags": {"site": "a,b=c"},
        "fields": {"integer": 2, "float": 1.5, "boolean": True, "string": 'a"b\nline'}})
    assert line == 'test\\ metric,site=a\\,b\\=c boolean=true,float=1.5,integer=2i,string="a\\"b\\nline"'
    assert InfluxWriter.line_protocol({"measurement": "m", "tags": {"empty": "", "none": None},
        "fields": {"empty": "", "none": None}}) is None


def test_batched_writes():
    requests = []
    class Client:
        def post(self, *args, **kwargs): requests.append(kwargs["content"]); return httpx.Response(204)
    writer = InfluxWriter(url="http://influx", token="token", database="db", batch_size=2, client=Client())
    points = [{"measurement": "m", "fields": {"v": n}} for n in range(5)]
    assert writer.write(points) == 5 and [len(value.splitlines()) for value in requests] == [2, 2, 1]


def test_collector_health_metric_generated(tmp_path):
    class API:
        api_requests = 0; retry_count = 0; rate_limit_remaining = 42
        async def sites(self): self.api_requests += 1; return [{"id": "site", "name": "School"}]
        async def inventory(self): self.api_requests += 1; return [{"id": "one", "site_id": "site", "type": "ap", "model": "AP34"}]
        async def device_stats(self): self.api_requests += 1; return [{"id": "one", "status": "connected", "num_clients": 2}]
        async def close(self): pass
    batches = []
    collector = MistCollector({"customer": "c", "site": "s", "collectors": {"mist": {
        "organization_id": "org", "api_token": "token"}}}, tmp_path / "devices.json",
        client=API(), writer=InfluxWriter(lambda points: batches.append(points) or len(points)))
    asyncio.run(collector.collect())
    assert any(point["measurement"] == "collector_health" for batch in batches for point in batch)


def test_scheduler_intervals_isolate_failures_and_prevent_overlap():
    class Example(BaseCollector):
        name = "example"; discovery_interval = 10; collection_interval = 2
        def __init__(self, fail=False): self.calls = 0; self.fail = fail
        async def discover(self):
            self.calls += 1
            if self.fail: raise RuntimeError("failure")
            await asyncio.sleep(0.01)
        def collect(self): return None
    async def exercise():
        good, bad = Example(), Example(True)
        scheduler = Scheduler([good, bad])
        first, overlap, failed = await asyncio.gather(
            scheduler._execute(good, "discover"), scheduler._execute(good, "discover"),
            scheduler._execute(bad, "discover"))
        return good, bad, overlap, failed
    good, bad, overlap, failed = asyncio.run(exercise())
    assert good.calls == 1 and bad.calls == 1 and overlap is None and failed is None
    assert good.discovery_interval != good.collection_interval


def test_failed_collection_writes_failed_health(tmp_path):
    class API:
        api_requests = 1; retry_count = 0; rate_limit_remaining = None
        async def device_stats(self): raise RuntimeError("API unavailable")
        async def close(self): pass
    batches = []
    collector = MistCollector({"customer": "c", "site": "s", "collectors": {"mist": {
        "organization_id": "org", "api_token": "token"}}}, tmp_path / "devices.json",
        client=API(), writer=InfluxWriter(lambda points: batches.append(points) or len(points)))
    with pytest.raises(RuntimeError): asyncio.run(collector.collect())
    health = next(point for batch in batches for point in batch if point["measurement"] == "collector_health")
    assert health["fields"]["success"] is False and health["fields"]["error_count"] == 1


def test_inspection_output_is_safe_and_regional():
    result = {"enabled": True, "api_hostname": "api.ac2.mist.com", "organization_id": "org-id",
        "site_count": 3, "device_count": 176, "device_types": {"wireless-access-point": 144},
        "measurements": {"infrastructure_device": {"tags": ["site"], "fields": ["online"],
            "field_counts": {"online": 163}, "points": 163}}, "points_produced": 302,
        "always_empty_fields": [], "high_cardinality_warnings": ["infrastructure_device.device_id=163"],
        "influx_write_completed": True, "points_written": 302}
    output = "\n".join(inspection_lines("mist", result))
    assert "api.ac2.mist.com" in output and "Devices: 176" in output and "online (163/163)" in output
    assert "api_token" not in output.lower() and "authorization" not in output.lower()


def test_scheduler_updates_health_file(tmp_path):
    class Healthy(BaseCollector):
        name = "healthy"
        def discover(self): return True
        def collect(self): return True
    path = tmp_path / "collector-health"
    scheduler = Scheduler([Healthy()], path)
    asyncio.run(scheduler._execute(scheduler.collectors[0], "collect"))
    assert path.exists()
