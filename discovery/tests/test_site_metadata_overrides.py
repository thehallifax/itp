import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from analysis.infrastructure import InfrastructureStateEngine
from analysis.operations import OperationsEngine
from analysis.sites import SiteRegistry
from itp_profiles import DeploymentProfile

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)

# These are the precise fictional site labels intentionally distributed in
# tracked examples. This is deliberately not a pattern such as "* School".
APPROVED_FICTIONAL_SITE_LABELS = frozenset({
    "Example College",
    "Example Corporate",
    "Example Education Head Office",
    "Example School",
    "Hills Campus",
    "North Campus",
    "Northwind College",
    "River Campus",
    "South Campus",
    "example-school Reference Site",
})
TRACKED_SITE_EXAMPLE_FILES = (
    "config/sites.yml",
    "examples/deployments/multi-site-flat/sites.yml",
    "examples/deployments/multi-site-hierarchical/sites.yml",
    "examples/deployments/single-site/sites.yml",
    "profiles/example-corporate/sites.yml",
    "profiles/example-school/sites.yml",
)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False))


def profile_tree(tmp_path):
    profile = tmp_path / "profiles/example"
    write(profile / "discovery.yml", {"schema_version": 1, "collectors": {}})
    write(profile / "dashboards.yml", {"enabled": True})
    write(profile / "sites.yml", {"sites": [{
        "id": "example-school", "display_name": "example-school Reference Site",
        "aliases": ["example-school", "example-school Reference Site"]}, {
        "id": "example-corporate", "display_name": "Northwind College",
        "aliases": ["example-corporate", "Northwind College"]}]})
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
        "id": "example-school", "display_name": "Production Campus West",
        "aliases": ["example-school", "Production West"]}, {
        "id": "example-corporate",
        "display_name": "Production Campus East",
        "aliases": ["example-corporate", "Production East"]}]})

    deployment = DeploymentProfile.load("example", tmp_path)
    assert deployment.paths.sites == local.resolve()
    assert deployment.env()["ITP_SITES_CONFIG"] == str(local.resolve())
    registry = SiteRegistry.load(deployment.paths.sites)
    assert [(site.site_id, site.display_name) for site in registry.sites] == [
        ("site:example-corporate", "Production Campus East"),
        ("site:example-school", "Production Campus West"),
    ]


def test_display_override_preserves_identity_and_all_runtime_projections(
        tmp_path):
    profile = profile_tree(tmp_path)
    local = profile / "sites.local.yml"
    write(local, {"sites": [{
        "id": "example-school", "display_name": "Production Campus West",
        "aliases": ["example-school", "Production West"]}, {
        "id": "example-corporate",
        "display_name": "Production Campus East",
        "aliases": ["example-corporate", "Production East"]}]})
    inventory = tmp_path / "runtime/example/inventory"
    inventory.mkdir(parents=True)
    (inventory / "assets.json").write_text(json.dumps({"assets": [
        {"source": "snmp", "collector": "snmp", "asset_id": "west-1",
         "source_asset_id": "west-1", "hostname": "EDGE-WEST",
         "device_type": "switch", "device_role": "core", "online": False,
         "site": "example-school", "management_ip": "192.0.2.10"},
        {"source": "mist", "collector": "mist", "asset_id": "east-1",
         "source_asset_id": "east-1", "hostname": "AP-EAST",
         "device_type": "access-point", "online": True, "site": "example-corporate"},
    ]}))
    (inventory / "source_runs.json").write_text('{"sources": {}}')
    runtime = tmp_path / "runtime/example"
    engine = InfrastructureStateEngine(
        inventory, runtime / "operations", runtime / "infrastructure",
        runtime / "dashboard", sites_config=local,
        sites_output=runtime / "sites")
    state = engine.run(NOW)
    assert {value["site_id"] for value in state["sites"]} == {
        "site:example-school", "site:example-corporate"}
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
    assert issue["site_id"] == "site:example-school"
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
        ("Production Campus West", "site:example-school"),
        ("Production Campus East", "site:example-corporate"),
    }


def test_tracked_site_metadata_uses_only_approved_fictional_labels():
    found = set()
    for relative in TRACKED_SITE_EXAMPLE_FILES:
        path = ROOT / relative
        payload = yaml.safe_load(path.read_text())
        for site in payload["sites"]:
            label = site.get("display_name", site.get("name"))
            assert label in APPROVED_FICTIONAL_SITE_LABELS, (
                f"unapproved tracked site label {label!r} in {relative}")
            found.add(label)
    assert found == APPROVED_FICTIONAL_SITE_LABELS


def test_tracked_defaults_are_demo_safe_with_stable_ids():
    registry = SiteRegistry.load(ROOT / "config/sites.yml")
    assert {(site.site_id, site.display_name) for site in registry.sites} == {
        ("site:example-school", "example-school Reference Site"),
        ("site:example-corporate", "Northwind College"),
    }
