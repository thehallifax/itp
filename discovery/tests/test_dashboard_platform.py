import json
import os
import stat
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
    dashboard = json.loads((
        tmp_path / "managed/printing/papercut-operational-overview.json"
    ).read_text())
    panels = {panel["title"]: panel for panel in dashboard["panels"]}
    assert panels["Uptime"]["options"]["colorMode"] == "none"
    assert "informational" in panels["Uptime"]["description"]
    for title in (
            "Printer and Device Summary", "Licensing",
            "Active Operational Findings"):
        assert panels[title]["fieldConfig"]["defaults"]["noValue"] == \
            "No Matching Records"


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes are not authoritative")
def test_generated_dashboard_artifacts_are_cross_container_readable(tmp_path):
    value = registry(tmp_path, {"paloalto": True, "papercut": True})
    value.generate()
    provisioning = tmp_path / "dashboards.yml"
    managed = (
        tmp_path / "managed/operations/itp-operations-wallboard.json")

    for path in (provisioning, managed):
        assert stat.S_IMODE(path.stat().st_mode) == 0o644
        assert path.stat().st_mode & stat.S_IROTH
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o755

    value.generate()
    assert stat.S_IMODE(provisioning.stat().st_mode) == 0o644
    assert stat.S_IMODE(managed.stat().st_mode) == 0o644


def test_unselected_services_are_absent_and_enabled_collectors_remain_visible(
        tmp_path):
    runtime = tmp_path / "runtime"
    config = {
        "deployment_id": "example",
        "collectors": {
            "paloalto": {"enabled": True},
            "papercut": {"enabled": True},
        },
    }
    value = DashboardRegistry(
        ROOT, config, runtime / "dashboard/managed",
        runtime / "dashboard/provisioning/dashboards.yml")
    value.generate()
    dashboard = json.loads((
        runtime / "dashboard/managed/infrastructure/"
        "itp-infrastructure-overview.json").read_text())
    titles = {panel["title"] for panel in dashboard["panels"]}
    assert {"DNS", "DHCP", "Active Directory"}.isdisjoint(titles)
    assert {"PaperCut", "Firewalls"} <= titles
    assert (
        runtime / "dashboard/managed/vendor/"
        "paloalto-operational-overview.json").is_file()
    assert (
        runtime / "dashboard/managed/printing/"
        "papercut-operational-overview.json").is_file()


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


def _write_current_runtime_state(runtime):
    def write(relative, value):
        path = runtime / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    generated = "2026-07-30T01:00:00Z"
    write("inventory/source_runs.json", {"sources": {
        "paloalto": {"last_run": {
            "success": True, "completed_at": generated},
            "last_complete_successful_run": {"completed_at": generated},
            "last_failed_run": {
                "success": False,
                "completed_at": "2026-07-29T01:00:00Z",
                "error_category": "historical_failure"},
            "consecutive_failures": 0},
        "papercut": {"last_run": {
            "success": True, "completed_at": generated},
            "last_complete_successful_run": {"completed_at": generated},
            "consecutive_failures": 0},
    }})
    write("infrastructure/state.json", {
        "generated_at": generated, "deployment_id": "example",
        "assets": [{
            "canonical_id": "asset:paloalto:1", "hostname": "EDGE-1",
            "device_type": "firewall", "online": True,
            "site": {"site_id": "site:example-corporate",
                     "display_name": "Northwind College"},
            "sources": ["paloalto"]}], "signals": {"wan": [{
                "name": "ethernet1/1", "display_name": "Primary",
                "role": "primary", "classification_authoritative": True,
                "available": True, "site_id": "site:example-corporate",
                "samples": [{"time": generated,
                             "rx_bps": 1000, "tx_bps": 500}],
            }]},
        "collectors": [
            {"collector": "paloalto", "status": "healthy",
             "last_run": generated, "last_successful_run": generated},
            {"collector": "papercut", "status": "healthy",
             "last_run": generated, "last_successful_run": generated}],
        "summary": {"devices": 0, "online": 0, "offline": 0,
                    "collectors_healthy": 2, "collectors_failed": 0}})
    write("operations/operations.json", {
        "generated_at": generated, "issues": [], "risks": [],
        "recommendations": []})
    services = [
        {"service": name, "status": (
            "Healthy" if name in {"Internet", "Security", "Monitoring"}
            else "Not Enabled"), "summary": "Current evidence.",
         "severity": "Info", "affected_assets": [], "affected_users": None,
         "last_change": generated, "evidence": []}
        for name in (
            "Internet", "Wireless", "Switching", "Printing", "Identity",
            "Compute", "Storage", "Voice", "Email", "Security",
            "Monitoring")]
    write("services/service-health.json", {
        "generated_at": generated, "sites": [],
        "estate": {"site_id": "all", "site_name": "All Sites",
                   "overall_status": "Healthy", "services": services}})
    write("capabilities/collectors.json", {"collectors": {
        name: {
            "execution": {"state": "collected"},
            "last_collection": {
                "observed_at": generated, "last_success": generated,
                "points_written": 10}}
        for name in ("paloalto", "papercut")}})


def test_state_derived_refresh_uses_runtime_and_preserves_vendor_dashboards(
        tmp_path):
    runtime = tmp_path / "runtime"
    value = DashboardRegistry(
        ROOT, {"deployment_id": "example", "collectors": {
            "paloalto": {"enabled": True},
            "papercut": {"enabled": True}}},
        runtime / "dashboard/managed",
        runtime / "dashboard/provisioning/dashboards.yml")
    value.generate()
    vendor_paths = (
        runtime / "dashboard/managed/vendor/"
        "paloalto-operational-overview.json",
        runtime / "dashboard/managed/printing/"
        "papercut-operational-overview.json",
    )
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns)
              for path in vendor_paths}
    bootstrap = json.loads((
        runtime / "dashboard/managed/operations/"
        "itp-operations-wallboard.json").read_text())
    assert any(
        "Waiting for first collection" in str(
            target.get("csvContent") or "")
        for panel in bootstrap["panels"]
        for target in panel.get("targets", []))
    _write_current_runtime_state(runtime)

    result = value.refresh_state_derived()

    assert {Path(path).name for path in result["published"]} == {
        "itp-operations-wallboard.json",
        "itp-infrastructure-overview.json",
        "itp-collector-health.json"}
    assert all((path.read_bytes(), path.stat().st_mtime_ns) == before[path]
               for path in vendor_paths)
    dashboard = json.loads((
        runtime / "dashboard/managed/operations/"
        "itp-operations-wallboard.json").read_text())
    active = "\n".join(
        str(value.get(key) or "")
        for panel in dashboard["panels"]
        for key, value in (
            ("content", panel.get("options", {})),
            ("csvContent", (panel.get("targets") or [{}])[0])))
    assert "Waiting for first collection" not in active
    assert "## Not Yet Collected" not in active
    assert "historical_failure" not in active
    assert "all,Healthy" in active
    assert any(
        panel.get("fieldConfig", {}).get("defaults", {}).get("noValue")
        == "Not Yet Collected" for panel in dashboard["panels"])


def test_state_derived_render_failure_preserves_managed_wallboard(
        tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    value = DashboardRegistry(
        ROOT, {"collectors": {}}, runtime / "dashboard/managed",
        runtime / "dashboard/provisioning/dashboards.yml")
    value.generate()
    destination = runtime / (
        "dashboard/managed/operations/itp-operations-wallboard.json")
    previous = destination.read_bytes()
    original = value._managed_dashboard

    def fail(path, declaration, capabilities):
        if declaration["uid"] == "itp-operations-wallboard":
            raise ValueError("invalid generated wallboard")
        return original(path, declaration, capabilities)

    monkeypatch.setattr(value, "_managed_dashboard", fail)
    with pytest.raises(ValueError, match="invalid generated wallboard"):
        value.refresh_state_derived()
    assert destination.read_bytes() == previous


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
