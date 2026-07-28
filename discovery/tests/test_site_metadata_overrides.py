import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml

from analysis.infrastructure import InfrastructureStateEngine
from analysis.operations import OperationsEngine
from analysis.sites import SiteRegistry
from itp_profiles import DeploymentProfile


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False))


def profile_tree(tmp_path):
    profile = tmp_path / "profiles/example"
    write(profile / "discovery.yml", {"schema_version": 1, "collectors": {}})
    write(profile / "dashboards.yml", {"enabled": True})
    write(profile / "sites.yml", {"sites": [{
        "id": "MLC", "display_name": "MLC Reference Site",
        "aliases": ["MLC", "MLC Reference Site"]}, {
        "id": "st-brigids-lesmurdie", "display_name": "Northwind College",
        "aliases": ["SBC", "Northwind College"]}]})
    write(profile / "profile.yml", {
        "profile": {"id": "example", "name": "Example", "environment": "production",
                    "timezone": "Australia/Perth", "runtime_mode": "central"},
        "paths": {
            "discovery_config": "profiles/example/discovery.yml",
            "sites_config": "profiles/example/sites.yml",
            "dashboards_config": "profiles/example/dashboards.yml",
            "secrets_dir": "secrets/example",
            "runtime_dir": "runtime/example"},
        "telemetry": {"deployment_id": "example", "influx_bucket": "itp_example",
                      "influx_org": "itp"},
        "grafana": {"folder_prefix": "EXAMPLE",
                    "provisioning_namespace": "example"},
        "ports": {"grafana": 3900, "influxdb": 8900}})
    (tmp_path / "secrets/example").mkdir(parents=True)
    return profile


def test_profile_selects_ignored_local_site_registry(tmp_path):
    profile = profile_tree(tmp_path)
    local = profile / "sites.local.yml"
    write(local, {"sites": [{
        "id": "MLC", "display_name": "Production Campus West",
        "aliases": ["MLC", "Production West"]}, {
        "id": "st-brigids-lesmurdie",
        "display_name": "Production Campus East",
        "aliases": ["SBC", "Production East"]}]})

    deployment = DeploymentProfile.load("example", tmp_path)
    assert deployment.paths.sites == local.resolve()
    assert deployment.env()["ITP_SITES_CONFIG"] == str(local.resolve())
    registry = SiteRegistry.load(deployment.paths.sites)
    assert [(site.site_id, site.display_name) for site in registry.sites] == [
        ("site:st-brigids-lesmurdie", "Production Campus East"),
        ("site:MLC", "Production Campus West"),
    ]


def test_display_override_preserves_identity_and_all_runtime_projections(
        tmp_path):
    profile = profile_tree(tmp_path)
    local = profile / "sites.local.yml"
    write(local, {"sites": [{
        "id": "MLC", "display_name": "Production Campus West",
        "aliases": ["MLC", "Production West"]}, {
        "id": "st-brigids-lesmurdie",
        "display_name": "Production Campus East",
        "aliases": ["SBC", "Production East"]}]})
    inventory = tmp_path / "runtime/example/inventory"
    inventory.mkdir(parents=True)
    (inventory / "assets.json").write_text(json.dumps({"assets": [
        {"source": "snmp", "collector": "snmp", "asset_id": "west-1",
         "source_asset_id": "west-1", "hostname": "EDGE-WEST",
         "device_type": "switch", "device_role": "core", "online": False,
         "site": "MLC", "management_ip": "192.0.2.10"},
        {"source": "mist", "collector": "mist", "asset_id": "east-1",
         "source_asset_id": "east-1", "hostname": "AP-EAST",
         "device_type": "access-point", "online": True, "site": "SBC"},
    ]}))
    (inventory / "source_runs.json").write_text('{"sources": {}}')
    runtime = tmp_path / "runtime/example"
    engine = InfrastructureStateEngine(
        inventory, runtime / "operations", runtime / "infrastructure",
        runtime / "dashboard", sites_config=local,
        sites_output=runtime / "sites")
    state = engine.run(NOW)
    assert {value["site_id"] for value in state["sites"]} == {
        "site:MLC", "site:st-brigids-lesmurdie"}
    assert {value["display_name"] for value in state["sites"]} == {
        "Production Campus West", "Production Campus East"}

    operations = OperationsEngine(
        inventory, runtime / "operations",
        ROOT / "dashboards/Infrastructure Overview/infrastructure-overview.json",
        dashboard_output=runtime / "dashboard/grafana/infrastructure-overview.json",
        infrastructure_state=runtime / "infrastructure/state.json",
        infrastructure_summary=runtime / "dashboard/infrastructure-summary.json",
        sites_config=local).run(NOW)
    issue = next(value for value in operations["issues"]
                 if value["device"] == "EDGE-WEST")
    assert issue["site_id"] == "site:MLC"
    assert issue["site"] == "Production Campus West"

    sites = json.loads((runtime / "sites/sites.json").read_text())
    assert {value["display_name"] for value in sites["sites"]} == {
        "Production Campus West", "Production Campus East"}
    dashboard = json.loads(
        (runtime / "dashboard/grafana/infrastructure-overview.json").read_text())
    variable = next(value for value in dashboard["templating"]["list"]
                    if value["name"] == "site")
    assert {(value["text"], value["value"]) for value in variable["options"]} == {
        ("All Sites", "all"),
        ("Production Campus West", "site:MLC"),
        ("Production Campus East", "site:st-brigids-lesmurdie"),
    }


def test_tracked_files_contain_no_production_site_names():
    tracked = subprocess.run(
        ["git", "grep", "-I", "-n", "-e",
         "Methodist" + " Ladies", "-e", "St " + "Brigid"],
        cwd=ROOT, text=True, capture_output=True)
    assert tracked.returncode == 1, tracked.stdout


def test_tracked_defaults_are_demo_safe_with_stable_ids():
    registry = SiteRegistry.load(ROOT / "config/sites.yml")
    assert {(site.site_id, site.display_name) for site in registry.sites} == {
        ("site:MLC", "MLC Reference Site"),
        ("site:st-brigids-lesmurdie", "Northwind College"),
    }
