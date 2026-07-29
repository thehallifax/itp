import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from analysis.dashboards import DashboardRegistry
from analysis.sites import SiteRegistry
from collectors.base import BaseCollector
from collectors.config import load_config
from collectors.scheduler import Scheduler
from collectors.writer import InfluxWriter
from itp_profiles import DeploymentProfile, ProfileError, discover_profiles
from scripts import itp as itp_cli


ROOT = Path(__file__).resolve().parents[2]


def _profile_tree(tmp_path, name="test"):
    profile = tmp_path / "profiles" / name
    profile.mkdir(parents=True)
    (tmp_path / "secrets" / name).mkdir(parents=True)
    for filename, value in (
        ("discovery.yml", {"schema_version": 1, "collectors": {}}),
        ("sites.yml", {"sites": [{"id": name, "display_name": "Test", "aliases": []}]}),
        ("dashboards.yml", {"enabled": True}),
    ):
        (profile / filename).write_text(yaml.safe_dump(value))
    manifest = {
        "profile": {"id": name, "name": "Test", "environment": "test",
                    "timezone": "Australia/Perth", "runtime_mode": "central"},
        "identity": {"customer_id": name},
        "deployment": {"mode": "standalone"},
        "paths": {
            "discovery_config": f"profiles/{name}/discovery.yml",
            "sites_config": f"profiles/{name}/sites.yml",
            "dashboards_config": f"profiles/{name}/dashboards.yml",
            "secrets_dir": f"secrets/{name}",
            "runtime_dir": f"runtime/{name}",
        },
        "telemetry": {"deployment_id": name, "influx_bucket": f"itp_{name}",
                      "influx_org": "local_org"},
        "grafana": {"folder_prefix": name.upper(), "provisioning_namespace": name},
        "ports": {"grafana": 3900, "influxdb": 8981},
    }
    (profile / "profile.yml").write_text(yaml.safe_dump(manifest))
    return profile, manifest


def test_tracked_profiles_are_discovered_and_isolated():
    profiles = {value.id: value for value in discover_profiles(ROOT)}
    assert set(profiles) >= {"example-school", "example-corporate"}
    assert profiles["example-school"].paths.runtime != profiles["example-corporate"].paths.runtime
    assert profiles["example-school"].paths.secrets == ROOT / "secrets"
    assert profiles["example-corporate"].paths.secrets == ROOT / "secrets"
    assert profiles["example-school"].compose_project == "itp-example-school"
    assert profiles["example-corporate"].grafana_port != profiles["example-school"].grafana_port
    assert profiles["example-school"].deployment_mode == "standalone"
    assert profiles["example-school"].customer_id == "example-school"


def test_cluster_member_requires_explicit_shared_services(tmp_path):
    profile, manifest = _profile_tree(tmp_path)
    manifest["deployment"] = {
        "mode": "cluster_member", "cluster_id": "managed-cluster"}
    (profile / "profile.yml").write_text(yaml.safe_dump(manifest))
    with pytest.raises(ProfileError, match="shared Grafana and InfluxDB"):
        DeploymentProfile.load("test", tmp_path)
    manifest["deployment"]["shared_services"] = {
        "grafana_url": "https://grafana.example.invalid",
        "influxdb_url": "https://influx.example.invalid"}
    (profile / "profile.yml").write_text(yaml.safe_dump(manifest))
    value = DeploymentProfile.load("test", tmp_path)
    assert value.deployment_mode == "cluster_member"
    assert value.cluster_id == "managed-cluster"
    assert value.env()["INFLUXDB_HOST"] == \
        "https://influx.example.invalid"


def test_standalone_port_conflict_is_actionable(tmp_path, monkeypatch):
    _profile_tree(tmp_path)
    value = DeploymentProfile.load("test", tmp_path)
    monkeypatch.setattr(itp_cli, "_project_running", lambda profile: False)
    monkeypatch.setattr(itp_cli, "port_available", lambda port: False)
    monkeypatch.setattr(
        itp_cli, "_port_owner",
        lambda port: {"project": "another-project", "container": "grafana"})
    with pytest.raises(ProfileError, match=(
            "Grafana port 3900.*another-project.*cluster-member")):
        itp_cli.preflight_start(value)


def test_profile_rejects_mismatch_traversal_and_missing_files(tmp_path):
    profile, manifest = _profile_tree(tmp_path)
    manifest["profile"]["id"] = "duplicate"
    (profile / "profile.yml").write_text(yaml.safe_dump(manifest))
    with pytest.raises(ProfileError, match="mismatched"):
        DeploymentProfile.load("test", tmp_path)
    manifest["profile"]["id"] = "test"
    manifest["paths"]["runtime_dir"] = "../outside"
    (profile / "profile.yml").write_text(yaml.safe_dump(manifest))
    with pytest.raises(ProfileError, match="escapes"):
        DeploymentProfile.load("test", tmp_path)
    manifest["paths"]["runtime_dir"] = "runtime/test"
    (profile / "profile.yml").write_text(yaml.safe_dump(manifest))
    (profile / "sites.yml").unlink()
    with pytest.raises(ProfileError, match="missing required sites"):
        DeploymentProfile.load("test", tmp_path)


def test_profile_config_and_site_aliases_do_not_cross_boundaries(monkeypatch):
    example_school = DeploymentProfile.load("example-school", ROOT)
    example_corporate = DeploymentProfile.load("example-corporate", ROOT)
    monkeypatch.setenv("ITP_DEPLOYMENT_ID", "example-school")
    example_school_config = load_config(example_school.paths.discovery)
    monkeypatch.setenv("ITP_DEPLOYMENT_ID", "example-corporate")
    example_corporate_config = load_config(example_corporate.paths.discovery)
    assert example_school_config["collectors"]["paloalto"]["enabled"] is True
    assert example_corporate_config["collectors"]["paloalto"]["enabled"] is False
    assert SiteRegistry.load(example_school.paths.sites).resolver.resolve("example-corporate").status == "unknown"
    assert SiteRegistry.load(example_corporate.paths.sites).resolver.resolve("example-school").status == "unknown"


def test_profile_secret_loading_is_local_and_preserves_indirection(tmp_path, monkeypatch):
    profile_root, _ = _profile_tree(tmp_path)
    secret = tmp_path / "secrets/test/vendor.env"
    secret.write_text("VENDOR_TOKEN=profile-only\n")
    monkeypatch.delenv("VENDOR_TOKEN", raising=False)
    value = DeploymentProfile.load("test", tmp_path)
    value.load_secrets()
    assert os.environ["VENDOR_TOKEN"] == "profile-only"
    assert load_config(profile_root / "discovery.yml")["collectors"] == {}


def test_runtime_activation_is_profile_scoped(tmp_path, monkeypatch):
    _profile_tree(tmp_path, "one")
    _profile_tree(tmp_path, "two")
    one = DeploymentProfile.load("one", tmp_path)
    two = DeploymentProfile.load("two", tmp_path)
    one.activate(load_secrets=False)
    assert os.environ["ITP_RUNTIME_DIR"].endswith("runtime/one")
    two.activate(load_secrets=False)
    assert os.environ["ITP_RUNTIME_DIR"].endswith("runtime/two")
    assert one.paths.inventory != two.paths.inventory


def test_writer_adds_deployment_tag_without_replacing_site(monkeypatch):
    captured = []
    monkeypatch.setenv("ITP_DEPLOYMENT_ID", "example-school")
    writer = InfluxWriter(
        delegate=lambda points: captured.extend(points) or len(points),
        deployment_id="example-school")
    writer.write([{"measurement": "device", "tags": {"site_id": "site:example-school"},
                   "fields": {"online": True}}])
    assert captured[0]["tags"] == {"site_id": "site:example-school", "deployment_id": "example-school"}


def test_profile_schedulers_do_not_share_locks():
    class Collector(BaseCollector):
        name = "same"
        async def discover(self): return True
        async def collect(self): return True
    first, second = Collector(), Collector()
    left, right = Scheduler([first]), Scheduler([second])
    asyncio.run(left._execute(first, "discover"))
    asyncio.run(right._execute(second, "discover"))
    assert left._locks[first] is not right._locks[second]


def test_dashboard_generation_is_profile_runtime_only(monkeypatch, tmp_path):
    profile = DeploymentProfile.load("example-school", ROOT)
    runtime = tmp_path / "runtime/example-school"
    monkeypatch.setenv("ITP_DEPLOYMENT_ID", "example-school")
    monkeypatch.setenv("ITP_RUNTIME_DIR", str(runtime))
    config = load_config(profile.paths.discovery)
    output = runtime / "dashboard/managed"
    provision = runtime / "dashboard/provisioning/dashboards.yml"
    result = DashboardRegistry(ROOT, config, output, provision).generate()
    assert result["enabled_collectors"] == ["aruba", "paloalto", "snmp"]
    assert provision.exists()
    dashboards = list(output.glob("*/*.json"))
    assert dashboards
    assert all("itp-profile:example-school" in json.loads(path.read_text())["tags"]
               for path in dashboards)
    assert not str(output).startswith(str(ROOT / "runtime/example-corporate"))


def test_legacy_configuration_emits_deprecation_warning(monkeypatch, tmp_path):
    monkeypatch.delenv("ITP_PROFILE", raising=False)
    discovery = tmp_path / "discovery"
    discovery.mkdir()
    config = discovery / "config.yml"
    config.write_text((ROOT / "discovery/config.example.yml").read_text())
    with pytest.warns(DeprecationWarning, match="deployment profile"):
        load_config(config)


def test_repository_profile_validation_is_secret_and_docker_independent():
    completed = subprocess.run(
        [sys.executable, "-m", "discovery.cli", "validate-profiles",
         "--root", str(ROOT)],
        cwd=ROOT, text=True, capture_output=True)
    assert completed.returncode == 0, completed.stderr
    assert "[PASS] example-school" in completed.stdout
    assert "[PASS] example-corporate" in completed.stdout
    assert "Validated 2 deployment profile(s)" in completed.stdout
