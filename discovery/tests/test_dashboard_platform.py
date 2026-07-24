import json
from pathlib import Path

import pytest
import yaml

from analysis.dashboards import DashboardRegistry, FOLDERS
from analysis.wallboard import WallboardEngine


ROOT = Path(__file__).resolve().parents[2]


def registry(tmp_path, enabled):
    config = {"collectors": {name: {"enabled": value} for name, value in enabled.items()}}
    return DashboardRegistry(ROOT, config, tmp_path / "managed", tmp_path / "dashboards.yml")


def test_manifests_are_complete_unique_and_future_extensible(tmp_path):
    value = registry(tmp_path, {}).manifests()
    assert [item.collector for item in value] == [
        "fortigate", "mist", "paloalto", "platform", "snmp", "virtualisation"]
    assert all(item.version == 1 for item in value)
    assert all(item.path.name in {"dashboard-manifest.yml", "platform-manifest.yml"}
               for item in value)


def test_always_dashboards_and_disabled_collectors_are_omitted(tmp_path):
    resolved = registry(tmp_path, {
        "mist": False, "fortigate": False, "paloalto": False, "snmp": True}).generate()
    assert {value["uid"] for value in resolved["dashboards"]} == {
        "itp-operations-wallboard", "itp-infrastructure-overview", "itp-collector-health"}
    assert "wireless" not in resolved["capabilities"]
    assert "firewall" not in resolved["capabilities"]
    assert not list((tmp_path / "managed/vendor").glob("*.json"))


def test_enabled_vendor_dashboards_and_capabilities_are_selected(tmp_path):
    resolved = registry(tmp_path, {
        "mist": True, "fortigate": True, "paloalto": True, "snmp": True}).generate()
    assert {value["uid"] for value in resolved["dashboards"]} == {
        "itp-operations-wallboard", "itp-infrastructure-overview", "itp-collector-health",
        "mist-infrastructure-overview", "fortigate-infrastructure-overview",
        "paloalto-operational-overview"}
    assert set(resolved["capabilities"]) == {
        "firewall", "internet", "inventory", "switching", "telemetry", "wireless"}
    assert len(list((tmp_path / "managed/vendor").glob("*.json"))) == 3


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
        assert dashboard["editable"] is False


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
