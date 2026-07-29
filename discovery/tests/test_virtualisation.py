import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import yaml

from analysis.dashboards import DashboardRegistry
from analysis.infrastructure.adapters import VirtualisationAdapter
from analysis.virtualisation import VirtualisationEngine, canonical_id
from analysis.virtualisation.config import validate_virtualisation
from analysis.virtualisation.diagnostics import classify_error
from analysis.virtualisation.normalise import percentage, power_state
from analysis.virtualisation.telemetry import points
from collectors.hyperv.parser import parse as parse_hyperv
from collectors.proxmox.parser import parse as parse_proxmox
from collectors.vmware.parser import parse as parse_vmware


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 7, 24, tzinfo=timezone.utc)


def fixture(provider):
    return json.loads((ROOT / "collectors" / provider /
                       "fixtures/estate.json").read_text())


@pytest.mark.parametrize(("provider", "parser"), [
    ("vmware", parse_vmware), ("hyperv", parse_hyperv), ("proxmox", parse_proxmox)])
def test_provider_contracts_and_deterministic_rendering(tmp_path, provider, parser):
    assert parser(fixture(provider))["provider"] == provider
    engine = VirtualisationEngine(ROOT, tmp_path / provider, "example", "site:one")
    first = engine.run_fixture(provider)
    digest = hashlib.sha256(b"".join(
        path.read_bytes() for path in sorted((tmp_path / provider).glob("*")))).hexdigest()
    second = engine.run_fixture(provider)
    assert first == second
    assert digest == hashlib.sha256(b"".join(
        path.read_bytes() for path in sorted((tmp_path / provider).glob("*")))).hexdigest()
    assert json.loads((tmp_path / provider / "summary.json").read_text())["deployment_id"] == "example"
    assert (tmp_path / provider / "workloads.csv").read_text().startswith("canonical_id")


def test_canonical_ids_do_not_depend_on_display_name():
    first = canonical_id("vmware", "vm", "https://vc.example", "vm-101")
    second = canonical_id("vmware", "vm", "https://vc.example", "vm-101")
    assert first == second and first.startswith("virt:vm:")
    assert first != canonical_id("proxmox", "vm", "https://vc.example", "vm-101")


@pytest.mark.parametrize(("native", "expected"), [
    ("poweredOn", "running"), ("Off", "stopped"), ("Paused", "paused"),
    ("Suspended", "suspended"), ("Saved", "saved"), ("unexpected", "unknown")])
def test_power_state_normalisation(native, expected):
    assert power_state(native) == expected


def test_capacity_calculation_is_conservative():
    assert percentage(90, 100) == 90
    assert percentage(None, 100) is None
    assert percentage(1, 0) is None


def test_proxmox_qemu_and_lxc_remain_distinct(tmp_path):
    result = VirtualisationEngine(
        ROOT, tmp_path, "example", "site:one").fixture("proxmox")
    workloads = [value for value in result["objects"]
                 if value["kind"] in {"vm", "container"}]
    assert [(value["kind"], value["source_id"]) for value in workloads] == [
        ("container", "lxc-200"), ("vm", "qemu-100")]


def test_snapshot_governance_and_storage_pressure_are_findings(tmp_path):
    result = VirtualisationEngine(
        ROOT, tmp_path, "example", "site:one").fixture("vmware")
    rules = {value["rule_id"] for value in result["findings"]}
    assert "virtualisation.snapshot_stale" in rules
    assert "virtualisation.storage_capacity_critical" in rules
    assert all(value["recommended_operator_check"] for value in result["findings"])


def test_hyperv_partial_cluster_and_failure_evidence(tmp_path):
    result = VirtualisationEngine(
        ROOT, tmp_path, "example", "site:one").fixture("hyperv")
    rules = {value["rule_id"] for value in result["findings"]}
    assert {"virtualisation.cluster_degraded",
            "virtualisation.host_disconnected"} <= rules
    assert result["collections"][0]["partial"] is True


def test_telemetry_is_low_cardinality_and_preserves_tenancy(tmp_path):
    state = VirtualisationEngine(
        ROOT, tmp_path, "example", "site:one").fixture("proxmox")
    values = points(state)
    assert {value["measurement"] for value in values} >= {
        "virtualisation_host", "virtualisation_workload",
        "virtualisation_storage", "virtualisation_collection"}
    assert all(value["tags"]["deployment_id"] == "example" for value in values)
    assert all("reason" not in value["tags"] for value in values)


def test_canonical_asset_adapter_reads_profile_runtime(tmp_path):
    runtime = tmp_path / "runtime"
    engine = VirtualisationEngine(ROOT, runtime / "virtualisation",
                                  "example", "site:one")
    engine.run_fixture("proxmox")
    result = VirtualisationAdapter(runtime / "inventory").collect()
    assert any(value["device_type"] == "virtual-container" for value in result.assets)
    assert result.collectors[0]["collector"] == "virtualisation"


def test_profile_validation_accepts_multiple_sites_and_rejects_cross_site(tmp_path):
    sites = tmp_path / "sites.yml"
    sites.write_text("sites:\n  - id: one\n    name: One\n  - id: two\n    name: Two\n")
    config = {"virtualisation": {"enabled": True, "providers": [
        {"id": "vc-one", "provider": "vmware", "site_id": "one",
         "endpoint": "https://vc.example.invalid", "verify_tls": True},
        {"id": "pve-two", "provider": "proxmox", "site_id": "two",
         "endpoint": "https://pve.example.invalid:8006", "verify_tls": True},
    ]}}
    assert len(validate_virtualisation(config, sites, tmp_path)) == 2
    config["virtualisation"]["providers"][0]["site_id"] = "outside"
    with pytest.raises(ValueError, match="unknown site"):
        validate_virtualisation(config, sites, tmp_path)


def test_profiles_without_virtualisation_are_backwards_compatible():
    for name in ("example-school", "example-corporate"):
        config = yaml.safe_load((ROOT / "profiles" / name / "discovery.yml").read_text())
        assert validate_virtualisation(
            config, ROOT / "profiles" / name / "sites.yml", ROOT) == []


def test_dashboard_registry_is_capability_aware(tmp_path):
    disabled = DashboardRegistry(ROOT, {"collectors": {}}, tmp_path / "disabled",
                                 tmp_path / "disabled.yml").resolve()
    assert "itp-virtualisation-overview" not in {
        value["uid"] for value in disabled["dashboards"]}
    enabled = DashboardRegistry(ROOT, {
        "collectors": {}, "virtualisation": {"enabled": True}},
        tmp_path / "enabled", tmp_path / "enabled.yml").resolve()
    dashboard = next(value for value in enabled["dashboards"]
                     if value["uid"] == "itp-virtualisation-overview")
    assert dashboard["folder"] == "Virtualisation"
    generated = DashboardRegistry(ROOT, {
        "collectors": {}, "virtualisation": {"enabled": True}},
        tmp_path / "managed", tmp_path / "provisioning.yml")
    generated.generate()
    provisioned = json.loads(
        (tmp_path / "managed/virtualisation/itp-virtualisation-overview.json").read_text())
    assert provisioned["uid"] == "itp-virtualisation-overview"


def test_dashboard_is_classic_flightsql_and_sql_safe():
    dashboard = json.loads((ROOT / "dashboards/Virtualisation/virtualisation-overview.json").read_text())
    assert isinstance(dashboard["panels"], list)
    assert "elements" not in dashboard and "layout" not in dashboard
    assert len(dashboard["templating"]["list"]) == 7
    for panel in dashboard["panels"]:
        for target in panel["targets"]:
            assert target["rawQuery"] is True and target["format"] == "table"
            assert ":sqlstring}" in target["rawSql"]


@pytest.mark.parametrize(("exc", "category"), [
    (httpx.ReadTimeout("timeout"), "timeout"),
    (ValueError("bad response"), "malformed_response"),
])
def test_error_classification_never_exposes_credentials(exc, category):
    assert classify_error(exc) == category
    assert "secret" not in classify_error(exc)


def test_secret_examples_are_trackable_and_populated_files_ignored():
    gitignore = (ROOT / ".gitignore").read_text()
    assert "secrets/**/*.env" in gitignore
    for provider in ("vmware", "hyperv", "proxmox"):
        assert (ROOT / "secrets" / f"{provider}.env.example").exists()
