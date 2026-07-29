import json
from pathlib import Path

import pytest
import yaml

from analysis.dashboards import (
    DashboardPackRegistry, DashboardRegistry, FOLDERS)
from collectors.connector_registry import ConnectorMetadataRegistry
from analysis.wallboard import WallboardEngine


ROOT = Path(__file__).resolve().parents[2]


def registry(tmp_path, enabled):
    config = {"collectors": {name: {"enabled": value} for name, value in enabled.items()}}
    return DashboardRegistry(ROOT, config, tmp_path / "managed", tmp_path / "dashboards.yml")


def test_managed_dashboard_empty_states_come_from_capability_manifest(tmp_path):
    capability_dir = tmp_path / "capabilities"
    capability_dir.mkdir()
    (capability_dir / "paloalto.json").write_text(json.dumps({
        "capabilities": [
            {"id": "certificate_expiry", "collection": "not_applicable",
             "panels": ["Certificate Expiry"],
             "explanation": "Authoritative expiry is not collected."},
            {"id": "configuration_commits", "collection": "failed",
             "panels": ["Recent Configuration Commits"],
             "explanation": "Latest collection failed."},
        ]}))
    registry(tmp_path, {"paloalto": True}).generate()
    dashboard = json.loads((
        tmp_path / "managed/vendor/paloalto-operational-overview.json"
    ).read_text())
    panels = {value["title"]: value for value in dashboard["panels"]}
    assert panels["Certificate Expiry"]["fieldConfig"]["defaults"]["noValue"] == \
        "Feature Not Enabled"
    assert panels["Recent Configuration Commits"]["fieldConfig"][
        "defaults"]["noValue"] == "Collection Failed"
    assert "Authoritative expiry is not collected." in panels[
        "Certificate Expiry"]["description"]


def test_manifests_are_complete_unique_and_future_extensible(tmp_path):
    value = registry(tmp_path, {}).manifests()
    assert [item.collector for item in value] == [
        "aruba", "fortigate", "mist", "paloalto", "papercut", "platform", "snmp",
        "virtualisation"]
    assert all(item.version == 1 for item in value)
    assert all(item.path.name in {"dashboard-manifest.yml", "platform-manifest.yml"}
               for item in value)
    assert isinstance(registry(tmp_path, {}), DashboardPackRegistry)


def test_always_dashboards_and_disabled_collectors_are_omitted(tmp_path):
    resolved = registry(tmp_path, {
        "mist": False, "fortigate": False, "paloalto": False, "snmp": True}).generate()
    assert {value["uid"] for value in resolved["dashboards"]} == {
        "itp-operations-wallboard", "itp-infrastructure-overview",
        "itp-collector-health", "itp-snmp-overview"}
    assert "wireless" not in resolved["capabilities"]
    assert "firewall" not in resolved["capabilities"]
    assert not list((tmp_path / "managed/vendor").glob("*.json"))


def test_collector_health_uses_operator_friendly_stats(tmp_path):
    source = json.loads(
        (ROOT / "dashboards/Collectors/collector-health.json").read_text())
    panels = {value["title"]: value for value in source["panels"]}
    assert {"Collectors Healthy", "Collectors Requiring Attention",
            "Latest Duration", "Latest Points Written"} <= panels.keys()
    for title in ("Collectors Healthy", "Collectors Requiring Attention",
                  "Latest Duration", "Latest Points Written"):
        assert panels[title]["options"]["textMode"] == "value"
    sql = "\n".join(panel["targets"][0]["rawSql"]
                    for panel in source["panels"])
    assert "PARTITION BY collector ORDER BY" in sql
    assert "PARTITION BY collector, site" not in sql
    assert 'AS "Healthy Collectors"' in sql
    assert 'AS "Collectors Requiring Attention"' in sql
    assert 'AS "Collection Duration"' in sql
    assert 'AS "Points Written"' in sql
    assert " AS healthy" not in sql and " AS failed" not in sql

    registry(tmp_path, {}).generate()
    managed = json.loads((
        tmp_path / "managed/operations/itp-collector-health.json"
    ).read_text())
    managed_panels = {value["title"]: value for value in managed["panels"]}
    assert set(panels) == set(managed_panels)


def test_enabled_vendor_dashboards_and_capabilities_are_selected(tmp_path):
    resolved = registry(tmp_path, {
        "mist": True, "fortigate": True, "paloalto": True, "snmp": True}).generate()
    assert {value["uid"] for value in resolved["dashboards"]} == {
        "itp-operations-wallboard", "itp-infrastructure-overview", "itp-collector-health",
        "mist-infrastructure-overview", "fortigate-infrastructure-overview",
        "paloalto-operational-overview", "itp-snmp-overview"}
    assert set(resolved["capabilities"]) == {
        "firewall", "internet", "inventory", "switching", "telemetry", "wireless"}
    assert len(list((tmp_path / "managed/vendor").glob("*.json"))) == 3


def test_enabled_papercut_pack_is_provisioned_in_printing(tmp_path):
    resolved = registry(tmp_path, {"papercut": True}).generate()
    assert {value["uid"] for value in resolved["dashboards"]} == {
        "itp-operations-wallboard", "itp-infrastructure-overview",
        "itp-collector-health", "papercut-operational-overview"}
    assert {"inventory", "printing", "telemetry"} <= set(
        resolved["capabilities"])
    assert (
        tmp_path / "managed/printing/papercut-operational-overview.json"
    ).is_file()


def test_generation_is_deterministic_managed_and_removes_stale_only(tmp_path):
    value = registry(tmp_path, {"mist": True})
    first = value.generate()
    rendered = {path.relative_to(tmp_path).as_posix(): path.read_text()
                for path in tmp_path.rglob("*") if path.is_file()}
    user = tmp_path / "user-created.json"; user.write_text('{"title":"User dashboard"}')
    stale = tmp_path / "managed/vendor/stale.json"; stale.write_text("{}")
    second = value.generate()
    rerendered = {path.relative_to(tmp_path).as_posix(): path.read_text()
                  for path in tmp_path.rglob("*") if path.is_file() and path != user}
    assert first == second
    assert "managed/vendor/stale.json" not in rerendered
    assert user.read_text() == '{"title":"User dashboard"}'
    assert rendered == rerendered
    for path in (tmp_path / "managed").glob("*/*.json"):
        dashboard = json.loads(path.read_text())
        assert "itp-managed" in dashboard["tags"]
        assert any(value.startswith("itp-pack-version:")
                   for value in dashboard["tags"])
        assert dashboard["editable"] is False


def test_pack_versions_metadata_and_disabled_pack_cleanup(tmp_path):
    output = tmp_path / "managed"
    enabled = DashboardRegistry(
        ROOT, {"collectors": {"snmp": {"enabled": True}}},
        output, tmp_path / "dashboards.yml").generate()
    assert {value["id"] for value in enabled["packs"]} == {"platform", "snmp"}
    assert (output / "infrastructure/itp-snmp-overview.json").is_file()
    disabled = DashboardRegistry(
        ROOT, {"collectors": {"snmp": {"enabled": False}}},
        output, tmp_path / "dashboards.yml").generate()
    assert {value["id"] for value in disabled["packs"]} == {"platform"}
    assert not (output / "infrastructure/itp-snmp-overview.json").exists()
    user = tmp_path / "user-dashboard.json"
    user.write_text("{}")
    assert user.exists()


def test_connector_metadata_links_dashboard_manifests():
    metadata = ConnectorMetadataRegistry.load(ROOT)
    for name in ("snmp", "mist", "fortigate", "paloalto", "papercut", "aruba"):
        connector = metadata.get(name)
        assert connector.dashboard_manifest
        assert (ROOT / connector.dashboard_manifest).is_file()


def test_snmp_example_pack_is_classic_and_flightsql_compatible():
    dashboard = json.loads(
        (ROOT / "dashboards/network/snmp-overview.json").read_text())
    assert dashboard["uid"] == "itp-snmp-overview"
    assert isinstance(dashboard["panels"], list) and dashboard["panels"]
    assert "elements" not in dashboard and "layout" not in dashboard
    for panel in dashboard["panels"]:
        for target in panel["targets"]:
            assert target["rawQuery"] is True
            assert target["format"] == "table"
            assert target["rawSql"]


def test_grafana_uses_infrastructure_overview_as_managed_home():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    environment = compose["services"]["grafana"]["environment"]
    assert (
        "GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH="
        "/var/lib/grafana/runtime-dashboard/managed/infrastructure/"
        "itp-infrastructure-overview.json") in environment


def test_normalized_provisioning_has_fixed_unique_folders(tmp_path):
    value = registry(tmp_path, {})
    value.generate()
    provision = yaml.safe_load((tmp_path / "dashboards.yml").read_text())
    providers = provision["providers"]
    assert [item["folder"] for item in providers] == list(FOLDERS)
    assert len({item["folderUid"] for item in providers}) == len(FOLDERS) == 9
    assert all(item["allowUiUpdates"] is False for item in providers)
    assert all("/runtime-dashboard/managed/" in item["options"]["path"]
               for item in providers)


def test_enabled_collector_without_manifest_fails_clearly(tmp_path):
    with pytest.raises(ValueError, match="lack dashboard manifests"):
        registry(tmp_path, {"future-collector": True}).resolve()


def test_wallboard_domains_follow_capabilities_not_vendor_names(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"assets": [], "summary": {}, "collectors": [],
                                 "firewalls": {}, "wireless": {}, "printers": {}}))
    operations = tmp_path / "operations.json"
    operations.write_text('{"issues":[],"risks":[],"recommendations":[]}')
    sites = tmp_path / "sites.json"; sites.write_text('{"sites":[]}')
    capabilities = tmp_path / "registry.json"
    capabilities.write_text(json.dumps({"capabilities": ["firewall", "telemetry"]}))
    service_health = tmp_path / "service-health.json"
    service_health.write_text(json.dumps({"generated_at": "2026-07-23T00:00:00Z",
        "estate": {"site_id": "all", "site_name": "All Sites",
            "overall_status": "Unknown", "services": [
                {"service": name, "status": status, "summary": "", "severity": "Info",
                 "affected_assets": [], "affected_users": None, "last_change": None,
                 "evidence": []}
                for name, status in (("Internet", "Not Enabled"), ("Wireless", "Not Enabled"),
                    ("Switching", "Not Enabled"), ("Printing", "Not Enabled"),
                    ("Identity", "Not Enabled"), ("Compute", "Not Enabled"),
                    ("Storage", "Not Enabled"), ("Voice", "Not Enabled"),
                    ("Email", "Not Enabled"), ("Security", "Unknown"),
                    ("Monitoring", "Unknown"))]}, "sites": []}))
    engine = WallboardEngine(state, operations, sites,
        ROOT / "dashboards/Operations/operations-wallboard.json",
        tmp_path / "summary.json", tmp_path / "wallboard.json",
        capability_registry=capabilities, service_health=service_health)
    result = engine.evaluate()
    domains = result["scopes"][0]["domains"]
    assert domains["security"]["available"] is True
    assert domains["wireless"]["available"] is False
    assert domains["network"]["available"] is False
    assert result["capabilities"] == ["firewall", "telemetry"]
