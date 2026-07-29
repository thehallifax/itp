import json
from pathlib import Path

import pytest
import yaml

from analysis.infrastructure.fusion import FusionEngine
from analysis.infrastructure.models import AdapterResult
from collectors.config import load_config
from collectors.paloalto.mapper import map_snapshot
from collectors.paloalto.models import PaloAltoConfig
from collectors.papercut.models import PaperCutConfig
from collectors.papercut.normalizer import normalize
from collectors.writer import InfluxWriter
from itp_profiles import DeploymentProfile, IdentityResolver, ProfileError


ROOT = Path(__file__).resolve().parents[2]


def test_example_school_profile_resolves_one_stable_identity():
    profile = DeploymentProfile.load("example-school", ROOT)
    resolver = IdentityResolver.from_sites_file(
        profile.deployment_id, profile.customer_id, profile.paths.sites)
    identity = resolver.resolve_site("example-school")
    assert (identity.deployment_id, identity.customer_id, identity.site_id) == (
        "example-school", "example-school", "site:example-school")
    assert resolver.resolve_site(identity.site_name).site_id == "site:example-school"


def test_display_and_alias_changes_do_not_change_site_identity(tmp_path):
    first = tmp_path / "first.yml"
    second = tmp_path / "second.yml"
    first.write_text(yaml.safe_dump({"sites": [{
        "id": "reference", "display_name": "Reference Site",
        "aliases": ["old"]}]}))
    second.write_text(yaml.safe_dump({"sites": [{
        "id": "reference", "display_name": "Renamed Site",
        "aliases": ["new"]}]}))
    assert IdentityResolver.from_sites_file(
        "deployment", "customer", first).resolve_site("old").site_id == \
        IdentityResolver.from_sites_file(
            "deployment", "customer", second).resolve_site("new").site_id


def test_profile_overlay_cannot_replace_canonical_site_id(tmp_path):
    profile = tmp_path / "profiles/example"
    profile.mkdir(parents=True)
    (tmp_path / "secrets/example").mkdir(parents=True)
    (profile / "discovery.yml").write_text(
        "schema_version: 1\ncollectors: {}\n")
    (profile / "dashboards.yml").write_text("enabled: true\n")
    (profile / "sites.yml").write_text(
        "sites:\n- id: canonical\n  display_name: Reference\n")
    (profile / "sites.local.yml").write_text(
        "sites:\n- id: replacement\n  display_name: Local\n")
    (profile / "profile.yml").write_text(yaml.safe_dump({
        "profile": {"id": "example", "name": "Example",
                    "timezone": "Australia/Perth"},
        "identity": {"customer_id": "example"},
        "deployment": {"mode": "standalone"},
        "paths": {
            "discovery_config": "profiles/example/discovery.yml",
            "sites_config": "profiles/example/sites.yml",
            "dashboards_config": "profiles/example/dashboards.yml",
            "secrets_dir": "secrets/example",
            "runtime_dir": "runtime/example"},
        "telemetry": {"deployment_id": "example",
                      "influx_bucket": "itp_example",
                      "influx_org": "local_org"},
        "grafana": {"folder_prefix": "Example",
                    "provisioning_namespace": "example"},
        "ports": {"grafana": 3900, "influxdb": 8900}}))
    with pytest.raises(ProfileError, match="preserve the canonical site ID"):
        DeploymentProfile.load("example", tmp_path)


def test_profile_config_normalises_legacy_alias_at_boundary(
        monkeypatch, tmp_path):
    profile = DeploymentProfile.load("example-school", ROOT)
    profile_root = tmp_path / "profiles/example-school"
    profile_root.mkdir(parents=True)
    legacy = yaml.safe_load(profile.paths.discovery.read_text())
    legacy["site"] = "example-school"
    (profile_root / "discovery.yml").write_text(
        yaml.safe_dump(legacy, sort_keys=False))
    (profile_root / "sites.yml").write_text(
        profile.paths.canonical_sites.read_text())
    monkeypatch.setenv("ITP_PROFILE", "example-school")
    monkeypatch.setenv("ITP_DEPLOYMENT_ID", "example-school")
    monkeypatch.setenv("ITP_CUSTOMER_ID", "example-school")
    monkeypatch.setenv("ITP_SITES_CONFIG", str(profile_root / "sites.yml"))
    monkeypatch.setenv(
        "ITP_CONNECTORS_CONFIG",
        str(ROOT / "config/connectors.local.example.yml"))
    with pytest.warns(DeprecationWarning, match="normalized"):
        config = load_config(profile_root / "discovery.yml")
    assert config["site_id"] == "site:example-school"
    assert config["site"] == config["site_id"]
    assert config["collectors"]["paloalto"]["site"] == "site:example-school"


def test_writer_rewrites_connector_identity_and_preserves_source_metadata():
    captured = []
    writer = InfluxWriter(
        delegate=lambda points: captured.extend(points) or len(points),
        deployment_id="deployment", customer_id="customer",
        site_id="site:canonical", site_name="Canonical Site")
    writer.write([{"measurement": "device", "tags": {
            "site_id": "site:one", "site": "site:two"},
            "fields": {"online": True}}])
    tags = captured[0]["tags"]
    assert tags["site_id"] == tags["site"] == "site:canonical"
    assert tags["site_name"] == "Canonical Site"
    assert tags["source_site_id"] == "site:one"
    assert tags["source_site_name"] == "site:two"


def test_asset_identity_is_stable_across_display_name_changes():
    def fuse(site_name):
        assets, _, _ = FusionEngine().fuse([AdapterResult(
            "paloalto", 200, assets=[{
                "source": "paloalto", "collector": "paloalto",
                "source_asset_id": "0123456789",
                "deployment_id": "example-school", "customer_id": "example-school",
                "site_id": "site:example-school", "site": site_name,
                "serial_number": "0123456789", "hostname": "FW-01",
                "device_type": "firewall", "online": True}])])
        return assets[0]
    before = fuse("Reference Site")
    after = fuse("Renamed Site")
    assert before["canonical_id"] == after["canonical_id"]
    assert before["site_id"] == after["site_id"] == "site:example-school"


def test_paloalto_and_papercut_points_carry_canonical_identity():
    pa_config = PaloAltoConfig(
        "https://192.0.2.1", "test", "PALOALTO_API_KEY",
        "example-school", "site:example-school", deployment_id="example-school",
        site_name="Reference Site")
    _, pa_points = map_snapshot({"system": {
        "serial": "0123456789", "hostname": "FW-01",
        "management_ip": "192.0.2.1"}}, pa_config,
        "2026-01-01T00:00:00Z")
    paper_config = PaperCutConfig(
        "https://print.example.invalid", "", "example-school", "site:example-school",
        deployment_id="example-school", site_name="Reference Site")
    fixture = json.loads(
        (ROOT / "collectors/papercut/fixtures/healthy.json").read_text())
    _, paper_points, _ = normalize(
        fixture, paper_config, "2026-01-01T00:00:00Z")
    for point in [*pa_points, *paper_points]:
        assert point["tags"]["deployment_id"] == "example-school"
        assert point["tags"]["customer_id"] == point["tags"]["customer"] == "example-school"
        assert point["tags"]["site_id"] == point["tags"]["site"] == "site:example-school"
