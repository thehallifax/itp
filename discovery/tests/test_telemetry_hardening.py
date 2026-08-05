import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest
import httpx

from analysis.readiness import evaluate_readiness
from collectors.base import BaseCollector, RuntimePlacementCollector
from collectors.connector_registry import ConnectorMetadataRegistry
from collectors.inventory import InventoryManager
from collectors.scheduler import Scheduler
from collectors.writer import InfluxWriteError, InfluxWriter
from telemetry import (
    CollectorHealth,
    DeploymentMetadata,
    TelemetryValidationError,
    coerce_boolean_integer,
    coerce_float,
    coerce_integer,
    normalize_point,
    timestamp_ns,
)

ROOT = Path(__file__).resolve().parents[2]


def test_collector_lifecycle_contract_is_uniform_and_framework_health_owned():
    assert BaseCollector.lifecycle == (
        "discover", "collect", "normalise", "validate", "write", "health",
        "summary")

    class ExampleCollector(BaseCollector):
        name = "example"

        def discover(self):
            return []

        def collect(self):
            return []

    collector = ExampleCollector()
    assert collector.normalise([]) == []
    assert collector.validate([]) == []
    assert collector.health() is None
    assert collector.summary([]) == {"result": []}


def test_deployment_identity_overrides_source_identity():
    metadata = DeploymentMetadata(
        "deployment", "customer", "site:canonical",
        "Customer", "Canonical Site")
    point = normalize_point({
        "measurement": "device",
        "tags": {
            "collector": "mist",
            "customer": "source-customer",
            "site": "Source Campus",
            "site_id": "mist-site-1",
        },
        "fields": {"online": True},
    }, metadata)
    assert point["tags"]["deployment_id"] == "deployment"
    assert point["tags"]["customer_id"] == point["tags"]["customer"] == (
        "customer")
    assert point["tags"]["site_id"] == point["tags"]["site"] == (
        "site:canonical")
    assert point["tags"]["site_name"] == "Canonical Site"
    assert point["tags"]["source_site_id"] == "mist-site-1"
    assert point["tags"]["source_site_name"] == "Source Campus"


def test_schema_coercion_and_iso_timestamp_are_deterministic():
    point = normalize_point({
        "measurement": "performance",
        "tags": {"collector": "papercut"},
        "fields": {
            "cpu_percent": 17.2,
            "active_sessions": "42",
            "memory_used_percent": "38.5",
        },
        "timestamp": "2026-07-29T00:00:00Z",
    }, DeploymentMetadata())
    assert point["fields"] == {
        "active_sessions": 42,
        "cpu_percent": 17,
        "memory_used_percent": "38.5",
    }
    assert point["timestamp"] == timestamp_ns("2026-07-29T00:00:00Z")
    assert coerce_integer("17.4") == 17
    assert coerce_float("17.4") == 17.4
    assert coerce_boolean_integer(True) == 1


def test_schema_failure_identifies_measurement_field_connector_and_line():
    with pytest.raises(TelemetryValidationError) as failure:
        normalize_point({
            "measurement": "performance",
            "tags": {"collector": "papercut"},
            "fields": {"cpu_percent": "not-numeric"},
        }, DeploymentMetadata(), 3)
    assert str(failure.value) == (
        "Measurement: performance; Field: cpu_percent; Expected: integer; "
        "Received: str; Connector: papercut; Line: 3")


def test_framework_health_contains_success_skip_and_diagnostics_contract():
    point = CollectorHealth.from_outcome({
        "connector": "fortigate",
        "status": "skipped",
        "duration_ms": 0,
        "reason": (
            "Collector requires execution mode 'edge' but deployment is "
            "running in 'central' mode."),
        "value": None,
    }, runtime="central", execution_mode="edge").point()
    assert point["tags"] == {
        "collector": "fortigate",
        "diagnostic_category": "skipped",
        "execution_mode": "edge",
        "health_owner": "framework",
        "phase": "collect",
        "runtime": "central",
        "status": "skipped",
    }
    assert point["fields"]["skip_reason"].startswith(
        "Collector requires execution mode")
    assert point["fields"]["success"] is False


def test_scheduler_emits_health_for_runtime_mismatch():
    points = []
    writer = InfluxWriter(
        delegate=lambda values: points.extend(values) or len(values),
        deployment_id="deployment", customer_id="customer",
        site_id="site:canonical", accept_legacy_health=False)
    outcomes = asyncio.run(Scheduler(
        [RuntimePlacementCollector("fortigate", "edge", "central")],
        health_writer=writer, runtime_mode="central").execute_once())
    assert outcomes[0]["status"] == "skipped"
    health = points[0]
    assert health["measurement"] == "collector_health"
    assert health["tags"]["runtime"] == "central"
    assert health["tags"]["site_id"] == "site:canonical"
    assert health["fields"]["skip_reason"].startswith(
        "Collector requires execution mode")


def test_inventory_rewrites_source_site_before_persistence(tmp_path):
    manager = InventoryManager(tmp_path / "devices.json")
    manager.update_source([{
        "id": "mist:one",
        "source": "mist",
        "site": "Vendor Campus",
        "external_site_id": "vendor-site",
    }], "mist", "customer", "site:canonical", "2026-07-29T00:00:00Z")
    record = manager.read()["devices"][0]
    assert record["site"] == record["site_id"] == "site:canonical"
    assert record["source_site_id"] == "vendor-site"
    assert record["source_site_name"] == "Vendor Campus"


def test_runtime_capabilities_and_dashboard_site_contracts():
    registry = ConnectorMetadataRegistry.load(ROOT)
    assert registry.get("fortigate").runtime_modes == ("central", "edge")
    assert registry.get("mist").runtime_modes == ("central",)
    assert registry.get("paloalto").runtime_modes == ("central", "edge")


def test_influx_writer_reports_safe_failure_stage_and_category():
    writer = InfluxWriter(url="", token="", database="")
    with pytest.raises(InfluxWriteError) as raised:
        writer.write([{"measurement": "m", "fields": {"value": 1}}])
    assert raised.value.stage == "write"
    assert raised.value.category == "configuration_incomplete"
    assert "token" not in str(raised.value).casefold()


class InfluxResponseClient:
    def __init__(self, status, body, content_type="text/plain"):
        self.status = status
        self.body = body
        self.content_type = content_type

    def post(self, url, **_kwargs):
        request = httpx.Request("POST", url)
        if self.content_type == "application/json":
            return httpx.Response(
                self.status, json=self.body, request=request)
        return httpx.Response(
            self.status, text=self.body,
            headers={"Content-Type": self.content_type}, request=request)


@pytest.mark.parametrize(("status", "body"), [
    (400, "line 2: field type conflict for field 'cpu_percent': got float, expected integer"),
    (422, "line 1: duplicate field 'status'"),
])
def test_influx_http_failure_preserves_bounded_point_context(status, body):
    writer = InfluxWriter(
        url="http://influx", token="private-token", database="db",
        client=InfluxResponseClient(status, body), retries=0)
    points = [
        {"measurement": "device", "fields": {"online": True}},
        {"measurement": "performance", "tags": {"token": "private-token"},
         "fields": {"cpu_percent": 1.5}},
    ]
    with pytest.raises(InfluxWriteError) as raised:
        writer.write(points)
    diagnostic = raised.value.diagnostic_payload()
    assert diagnostic["http_status"] == status
    assert diagnostic["measurements"] == ["device", "performance"]
    assert diagnostic["offending_field"] in {"cpu_percent", "status"}
    assert "private-token" not in json.dumps(diagnostic)
    assert diagnostic["failing_line"].startswith(
        "performance" if status == 400 else "device")
    if status == 400:
        assert diagnostic["field_type_conflict"] == {
            "received": "float", "existing": "integer"}


def test_influx_failure_extracts_offending_tag_name():
    writer = InfluxWriter(
        url="http://influx", token="token", database="db",
        client=InfluxResponseClient(
            422, "line 1: invalid tag key 'interface role'"), retries=0)
    with pytest.raises(InfluxWriteError) as raised:
        writer.write([{"measurement": "interface",
            "tags": {"interface_name": "port1"}, "fields": {"online": True}}])
    diagnostic = raised.value.diagnostic_payload()
    assert diagnostic["http_status"] == 422
    assert diagnostic["offending_tag"] == "interface role"
    assert "invalid tag key" in diagnostic["response"]


def test_influx_json_error_preserves_full_response_and_message():
    writer = InfluxWriter(
        url="http://influx", token="token", database="db",
        client=InfluxResponseClient(400, {
            "error": "write rejected", "code": "invalid_line_protocol",
            "detail": "duplicate field 'online'"}, "application/json"),
        retries=0)
    with pytest.raises(InfluxWriteError) as raised:
        writer.write([{"measurement": "device", "fields": {"online": True}}])
    diagnostic = raised.value.diagnostic_payload()
    assert json.loads(diagnostic["response"])["detail"] == (
        "duplicate field 'online'")
    assert diagnostic["influx_error"] == "write rejected"


@pytest.mark.parametrize(("value", "category", "detail"), [
    (float("nan"), "invalid_numeric_value", "nan"),
    (float("inf"), "invalid_numeric_value", "inf"),
    ({"nested": True}, "unsupported_field_type", "dict"),
])
def test_influx_local_encoding_failures_identify_field_and_type(
        value, category, detail):
    with pytest.raises(InfluxWriteError) as raised:
        InfluxWriter.line_protocol({
            "measurement": "fortigate_performance",
            "fields": {"cpu_percent": value}})
    diagnostic = raised.value.diagnostic_payload()
    assert diagnostic["category"] == category
    assert diagnostic["measurements"] == ["fortigate_performance"]
    assert diagnostic["offending_field"] == "cpu_percent"
    assert detail in diagnostic["invalid_value"].casefold()


def test_influx_debug_mode_logs_bounded_redacted_lines_and_response(
        monkeypatch, caplog):
    monkeypatch.setenv("ITP_DEBUG_INFLUX_WRITES", "1")
    writer = InfluxWriter(
        url="http://influx", token="private-token", database="db",
        client=InfluxResponseClient(
            400, "authorization=private-token invalid field 'value'"),
        retries=0)
    with caplog.at_level(logging.INFO, logger="collector.influx_writer"):
        with pytest.raises(InfluxWriteError):
            writer.write([{"measurement": "m",
                "tags": {"api_token": "private-token"},
                "fields": {"value": 1}}])
    assert "measurements=m points=1" in caplog.text
    assert "http_status=400" in caplog.text
    assert "private-token" not in caplog.text
    assert "[REDACTED]" in caplog.text
    for relative in (
            "dashboards/Collectors/collector-health.json",
            "dashboards/Printing/papercut-overview.json",
            "dashboards/vendor/fortigate-overview.json",
            "dashboards/vendor/mist-infrastructure-overview.json"):
        payload = json.loads((ROOT / relative).read_text())
        queries = [
            target.get("rawSql", "")
            for panel in payload.get("panels", [])
            for target in panel.get("targets", [])]
        variables = payload.get("templating", {}).get("list", [])
        queries.extend(str(value.get("query") or "") for value in variables)
        assert not any(" site LIKE " in query for query in queries)
        site = next(
            (value for value in variables if value.get("name") == "site"),
            None)
        if site:
            assert "site_id" in str(site["query"])
            assert "__value" in str(site["query"])


def test_successful_zero_point_collection_is_explained_not_marked_healthy():
    result = evaluate_readiness(
        enabled_collectors=["papercut"],
        collector_records=[{
            "collector": "papercut",
            "status": "success",
            "last_run": "2026-07-29T00:00:00Z",
            "last_successful_run": "2026-07-29T00:00:00Z",
            "points_written": 0,
        }],
        now=datetime(2026, 7, 29, tzinfo=timezone.utc))
    collector = result["collectors"][0]
    assert collector["state"] == "waiting_first_collection"
    assert collector["display_label"] == "No telemetry received"
    assert "Collector healthy" in collector["operator_action"]
