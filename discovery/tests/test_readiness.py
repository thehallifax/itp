import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from analysis.dashboards import DashboardRegistry
from analysis.infrastructure import InfrastructureStateEngine
from analysis.readiness import (
    aggregate_readiness, credentials_ready, evaluate_readiness)
from collectors.connector_registry import ConnectorMetadataRegistry


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 7, 27, 1, 0, tzinfo=timezone.utc)


def evaluate(**values):
    return evaluate_readiness(now=NOW, **values)


def collector(status="healthy", *, last_run="2026-07-27T00:59:00Z",
              last_success="2026-07-27T00:59:00Z"):
    return {"collector": "snmp", "status": status, "last_run": last_run,
            "last_successful_run": last_success}


def test_clean_deployment_is_not_configured_not_unknown():
    value = evaluate(
        enabled_collectors=[], collector_records=[], assets=[],
        deployment_configured=True, platform_running=True)
    assert value["overall"]["state"] == "not_configured"
    assert value["infrastructure"]["display_label"] == \
        "Discovery not configured"
    assert value["observability"]["display_label"] == "Monitoring not started"
    assert value["collectors"] == []


def test_enabled_collector_waits_for_first_collection():
    value = evaluate(enabled_collectors=["snmp"], collector_records=[])
    assert value["observability"]["state"] == "waiting_first_collection"
    assert value["observability"]["display_label"] == \
        "Awaiting first collection"
    assert value["collectors"][0]["display_label"] == "Waiting for first run"


def test_fresh_success_and_inventory_are_healthy():
    value = evaluate(
        enabled_collectors=["snmp"], collector_records=[collector()],
        assets=[{"canonical_id": "asset:1"}])
    assert value["observability"]["state"] == "healthy"
    assert value["infrastructure"]["state"] == "healthy"
    assert value["overall"]["display_label"] == "Healthy"


def test_failed_and_stale_collectors_are_unavailable_with_distinct_reasons():
    failed = evaluate(
        enabled_collectors=["snmp"],
        collector_records=[collector("failed", last_success=None)])
    assert failed["observability"]["state"] == "unavailable"
    assert failed["collectors"][0]["reason"] == "collection_failed"
    stale = evaluate(
        enabled_collectors=["snmp"], collector_records=[collector(
            last_run="2026-07-27T00:00:00Z")], stale_seconds=300)
    assert stale["observability"]["state"] == "unavailable"
    assert stale["collectors"][0]["reason"] == "collection_stale"
    assert stale["collectors"][0]["stale"] is True


def test_mixed_collector_degradation_and_inventory_remain_warning():
    value = evaluate(
        enabled_collectors=["snmp", "mist"],
        collector_records=[collector(), {
            "collector": "mist", "status": "failed",
            "last_run": "2026-07-27T00:59:00Z",
            "last_successful_run": None,
        }],
        assets=[{"canonical_id": "asset:1"}])
    assert value["observability"]["state"] == "warning"
    assert value["infrastructure"]["state"] == "healthy"
    assert value["overall"]["state"] == "warning"


def test_canonical_critical_infrastructure_is_preserved_and_rendered(tmp_path):
    inventory = tmp_path / "inventory"
    inventory.mkdir()
    (inventory / "assets.json").write_text(json.dumps({"assets": [{
        "source": "snmp", "collector": "snmp",
        "asset_id": "edge-1", "source_asset_id": "edge-1",
        "hostname": "EDGE-1", "device_type": "firewall",
        "online": False, "status": "offline", "site": "Test Site",
        "management_ip": "192.0.2.1",
    }]}))
    (inventory / "source_runs.json").write_text(json.dumps({"sources": {
        "snmp": {
            "consecutive_failures": 0,
            "last_run": {
                "success": True,
                "started_at": "2026-07-27T00:58:00Z",
                "completed_at": "2026-07-27T00:59:00Z",
            },
            "last_complete_successful_run": {
                "completed_at": "2026-07-27T00:59:00Z",
            },
        },
    }}))
    sites = tmp_path / "sites.yml"
    sites.write_text("""sites:
  - id: test
    display_name: Test Site
    aliases: [Test Site]
""")
    engine = InfrastructureStateEngine(
        inventory, tmp_path / "operations", tmp_path / "state",
        tmp_path / "dashboard", sites_config=sites,
        sites_output=tmp_path / "sites",
        readiness_config={
            "deployment_id": "test",
            "collectors": {"snmp": {"enabled": True}},
        })
    state = engine.run(NOW)
    assert state["readiness"]["observability"]["state"] == "healthy"
    assert state["readiness"]["infrastructure"]["state"] == "critical"
    assert state["readiness"]["overall"]["state"] == "critical"
    rendered = json.loads(
        (tmp_path / "dashboard/infrastructure-summary.json").read_text())
    assert rendered["infrastructure_health"] == "Critical"
    assert rendered["readiness"]["overall"]["state"] == "critical"


def test_critical_subordinate_cannot_be_masked_by_healthy_state():
    healthy = {"state": "healthy", "display_label": "Healthy"}
    critical = {"state": "critical", "display_label": "Critical"}
    assert aggregate_readiness(critical, healthy)["state"] == "critical"
    assert aggregate_readiness(healthy, critical)["state"] == "critical"


def test_success_without_inventory_waits_but_empty_findings_do_not_degrade():
    waiting = evaluate(
        enabled_collectors=["snmp"], collector_records=[collector()],
        assets=[], operations_generated=True)
    assert waiting["infrastructure"]["reason"] == "inventory_empty"
    available = evaluate(
        enabled_collectors=["snmp"], collector_records=[collector()],
        assets=[{"canonical_id": "asset:1"}], operations_generated=True)
    assert available["infrastructure"]["state"] == "healthy"
    assert available["onboarding"][-1]["complete"] is True


def test_demo_data_is_not_rendered_as_unconfigured():
    value = evaluate(
        enabled_collectors=["snmp"], collector_records=[], assets=[],
        demo=True)
    assert value["observability"]["state"] == "healthy"
    assert value["collectors"][0]["display_label"] == "Demo data active"
    assert all(step["complete"] for step in value["onboarding"][2:])


def test_demo_without_enabled_collectors_is_ready_not_unconfigured():
    value = evaluate(
        enabled_collectors=[], collector_records=[], assets=[], demo=True)
    assert value["enabled_collectors"] == []
    assert value["observability"]["state"] == "healthy"
    assert value["infrastructure"]["state"] == "healthy"
    assert value["overall"]["display_label"] == "Demo data active"
    assert all(step["complete"] for step in value["onboarding"][2:])


def test_onboarding_never_contains_credentials_or_secret_values(monkeypatch):
    secret = "sensitive-community-value"
    config = {"collectors": {"snmp": {"enabled": True}}}
    registry = ConnectorMetadataRegistry.load(ROOT)
    monkeypatch.setenv("NETWORK_SNMP_COMMUNITY", secret)
    assert credentials_ready(config, registry, {"NETWORK_SNMP_COMMUNITY": secret})
    rendered = json.dumps(evaluate(
        enabled_collectors=["snmp"], credentials_configured=True))
    assert secret not in rendered
    assert "NETWORK_SNMP_COMMUNITY" not in rendered


def test_clean_managed_dashboards_have_deliberate_empty_rows_and_no_generic_no_data(
        tmp_path):
    config = {
        "deployment_id": "test-deployment",
        "deployment": {"name": "Test", "type": "Home Lab"},
        "collectors": {"snmp": {"enabled": False}},
    }
    registry = DashboardRegistry(
        ROOT, config, tmp_path / "dashboard/managed",
        tmp_path / "dashboard/provisioning/dashboards.yml")
    first = registry.generate()
    rendered = {
        path.relative_to(tmp_path).as_posix(): path.read_text()
        for path in tmp_path.rglob("*") if path.is_file()}
    second = registry.generate()
    assert first == second
    assert rendered == {
        path.relative_to(tmp_path).as_posix(): path.read_text()
        for path in tmp_path.rglob("*") if path.is_file()}

    managed = tmp_path / "dashboard/managed"
    infrastructure = json.loads(
        (managed / "infrastructure/itp-infrastructure-overview.json").read_text())
    panels = {panel["title"]: panel for panel in infrastructure["panels"]}
    assert "Setup Status" in panels
    setup_rows = list(csv.DictReader(io.StringIO(
        panels["Setup Status"]["targets"][0]["csvContent"])))
    assert any(row["Check"] == "At least one collector enabled"
               and row["Status"] == "Action required" for row in setup_rows)
    assert "Discovery not configured" in panels[
        "Infrastructure Health"]["targets"][0]["csvContent"]
    assert "Monitoring not started" in panels[
        "Observability Health"]["targets"][0]["csvContent"]

    wallboard = json.loads(
        (managed / "operations/itp-operations-wallboard.json").read_text())
    wall_panels = {panel["title"]: panel for panel in wallboard["panels"]}
    assert "Collector Disabled" in wall_panels[
        "Overall Health"]["targets"][0]["csvContent"]
    assert "Collector Disabled" in wall_panels[
        "Monitoring"]["targets"][0]["csvContent"]
    assert "Collector State" not in wall_panels

    health = json.loads(
        (managed / "operations/itp-collector-health.json").read_text())
    health_panels = {panel["title"]: panel for panel in health["panels"]}
    assert "No collectors enabled" in health_panels[
        "Collectors Healthy"]["targets"][0]["csvContent"]
    run_rows = list(csv.DictReader(io.StringIO(
        health_panels["Collector Runs"]["targets"][0]["csvContent"])))
    assert run_rows[0]["State"] == "No collectors enabled"
    covered = (
        list(panels.values()) + list(wall_panels.values())
        + list(health_panels.values()))
    assert all(
        panel.get("fieldConfig", {}).get("defaults", {}).get("noValue")
        != "No data" for panel in covered)


def test_failed_collector_is_not_relabelled_as_onboarding(tmp_path):
    runtime = tmp_path
    state = runtime / "infrastructure/state.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({
        "generated_at": "2026-07-27T01:00:00Z",
        "assets": [],
        "collectors": [{
            "collector": "snmp", "status": "failed",
            "last_run": "2026-07-27T00:59:00Z",
            "last_successful_run": None,
        }],
    }))
    config = {
        "deployment_id": "test-deployment",
        "deployment": {"name": "Test", "type": "Home Lab"},
        "collectors": {"snmp": {"enabled": True}},
    }
    DashboardRegistry(
        ROOT, config, runtime / "dashboard/managed",
        runtime / "dashboard/provisioning/dashboards.yml").generate()
    readiness = json.loads(
        (runtime / "dashboard/readiness.json").read_text())
    assert readiness["collectors"][0]["state"] == "unavailable"
    dashboard = json.loads((
        runtime / "dashboard/managed/operations/itp-collector-health.json"
    ).read_text())
    healthy = next(panel for panel in dashboard["panels"]
                   if panel["title"] == "Collectors Healthy")
    assert healthy["datasource"]["type"] == "influxdb"
    assert "No collectors enabled" not in json.dumps(dashboard)
