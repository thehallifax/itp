import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from analysis.operations import OperationsEngine
from analysis.services import ServiceHealthEngine
from analysis.virtualisation import VirtualisationEngine
from analysis.virtualisation.operations import (
    VirtualisationOperationsAdapter, validate_expectations,
)


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 7, 24, tzinfo=timezone.utc)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def sites(path):
    path.write_text("sites:\n  - id: head-office\n    display_name: Head Office\n")


def test_finding_promotion_is_deterministic_and_management_failure_stays_unknown():
    obj = {"canonical_id": "virt:manager:1", "kind": "manager", "provider": "vmware",
        "deployment_id": "example", "site_id": "head-office", "display_name": "vCenter",
        "observed_at": "2026-07-24T00:00:00Z"}
    finding = {"id": "virt:finding:1", "rule_id": "virtualisation.manager_unreachable",
        "canonical_id": obj["canonical_id"], "provider": "vmware",
        "deployment_id": "example", "site_id": "head-office", "object_name": "vCenter",
        "severity": "Critical", "confidence": "high", "reason": "Endpoint unreachable.",
        "recommended_operator_check": "Check management connectivity.",
        "evidence": {"reachable": False}, "first_observed": obj["observed_at"],
        "last_observed": obj["observed_at"]}
    adapter = VirtualisationOperationsAdapter(stale_seconds=3600)
    state = {"generated_at": "2026-07-24T00:00:00Z", "objects": [obj],
             "findings": [finding]}
    first = adapter.promote(state, NOW)
    second = adapter.promote(state, NOW)
    assert first == second
    assert first[0].severity == "Unknown"
    assert first[0].kind == "risk"
    assert first[0].affected_service_ids == ("virtualisation_management_plane",)
    assert first[0].id.startswith("ops:")


def test_expected_workload_state_and_conflict_validation():
    adapter = VirtualisationOperationsAdapter([{
        "priority": 10, "match": {"name": "dc*"},
        "expected_state": "running", "criticality": "critical"}])
    obj = {"canonical_id": "virt:vm:1", "kind": "vm", "provider": "hyperv",
        "deployment_id": "example", "site_id": "head-office", "name": "dc01",
        "display_name": "DC01", "power_state": "stopped", "tags": [],
        "observed_at": "2026-07-24T00:00:00Z"}
    result = adapter.promote({"generated_at": "2026-07-24T00:00:00Z",
        "objects": [obj], "findings": []}, NOW)
    assert result[0].rule_id == "virtualisation.expected_workload_stopped"
    assert result[0].severity == "Critical"
    with pytest.raises(ValueError, match="conflicting"):
        validate_expectations([
            {"match": {"name": "dc*"}, "expected_state": "running"},
            {"match": {"name": "dc*"}, "expected_state": "any"},
        ])


def test_stopped_unexpected_workload_is_not_an_incident_and_stale_is_unknown():
    obj = {"canonical_id": "virt:container:1", "kind": "container",
        "provider": "proxmox", "deployment_id": "example", "site_id": "head-office",
        "name": "lab", "display_name": "Lab", "power_state": "stopped",
        "tags": [], "observed_at": "2026-07-23T00:00:00Z"}
    recent = VirtualisationOperationsAdapter().promote({
        "generated_at": "2026-07-24T00:00:00Z", "objects": [obj], "findings": []}, NOW)
    assert recent == []
    stale = VirtualisationOperationsAdapter(stale_seconds=300).promote({
        "generated_at": "2026-07-23T00:00:00Z", "objects": [obj], "findings": []}, NOW)
    assert [(value.rule_id, value.severity) for value in stale] == [
        ("virtualisation.evidence_stale", "Unknown")]


def test_standalone_host_is_critical_but_clustered_host_is_degraded():
    finding = {"id": "virt:f:host", "rule_id": "virtualisation.host_disconnected",
        "canonical_id": "host", "provider": "hyperv", "deployment_id": "example",
        "site_id": "head-office", "object_name": "HV01", "severity": "Critical",
        "confidence": "high", "reason": "Disconnected.", "evidence": {},
        "recommended_operator_check": "Check host.", "last_observed": "2026-07-24T00:00:00Z"}
    base = {"kind": "host", "canonical_id": "host", "provider": "hyperv",
        "deployment_id": "example", "site_id": "head-office", "display_name": "HV01",
        "observed_at": "2026-07-24T00:00:00Z"}
    adapter = VirtualisationOperationsAdapter(stale_seconds=3600)
    standalone = adapter.promote({"generated_at": "2026-07-24T00:00:00Z",
        "objects": [base], "findings": [finding]}, NOW)[0]
    clustered = adapter.promote({"generated_at": "2026-07-24T00:00:00Z",
        "objects": [{**base, "cluster_id": "cluster"}], "findings": [finding]}, NOW)[0]
    assert standalone.severity == "Critical"
    assert clustered.severity == "High"
    assert standalone.affected_service_ids == (
        "hypervisor_cluster", "compute_capacity", "virtual_machine_hosting")
    assert clustered.affected_service_ids == ("hypervisor_cluster",)


def test_storage_pressure_is_scoped_until_workload_impact_is_confirmed():
    storage = {"canonical_id": "virt:storage:1", "kind": "storage",
        "provider": "vmware", "deployment_id": "example",
        "site_id": "head-office", "display_name": "Shared Datastore",
        "shared": True, "observed_at": "2026-07-24T00:00:00Z"}
    base = {"id": "virt:f:storage", "canonical_id": storage["canonical_id"],
        "provider": "vmware", "deployment_id": "example",
        "site_id": "head-office", "object_name": "Shared Datastore",
        "severity": "Critical", "confidence": "high",
        "recommended_operator_check": "Review storage.", "last_observed": storage["observed_at"]}
    adapter = VirtualisationOperationsAdapter(stale_seconds=3600)
    pressure = adapter.promote({"generated_at": storage["observed_at"],
        "objects": [storage], "findings": [{**base,
            "rule_id": "virtualisation.storage_capacity_critical",
            "reason": "Storage utilisation is critical.", "evidence": {}}]}, NOW)[0]
    impacted = adapter.promote({"generated_at": storage["observed_at"],
        "objects": [storage], "findings": [{**base,
            "rule_id": "virtualisation.storage_inaccessible",
            "reason": "Storage is inaccessible.",
            "evidence": {"workload_impact_confirmed": True}}]}, NOW)[0]
    assert pressure.affected_service_ids == ("shared_storage",)
    assert impacted.affected_service_ids == (
        "shared_storage", "virtual_machine_hosting", "workload_availability")


@pytest.mark.parametrize("provider", ["vmware", "hyperv", "proxmox"])
def test_operations_runtime_and_virtualisation_services_are_profile_scoped(tmp_path, provider):
    virtual = tmp_path / "virtualisation"
    VirtualisationEngine(ROOT, virtual, "example", "head-office").run_fixture(provider)
    site_config = tmp_path / "sites.yml"
    sites(site_config)
    write(tmp_path / "infrastructure/state.json", {"assets": [], "collectors": [],
        "reconciliations": [], "signals": {}})
    write(tmp_path / "dashboard/managed/registry.json", {
        "enabled_collectors": ["virtualisation"],
        "capabilities": ["compute", "storage", "telemetry", "virtualisation"],
        "collector_capabilities": {"virtualisation": [
            "compute", "storage", "telemetry", "virtualisation"]}})
    operations = OperationsEngine(
        output_dir=tmp_path / "operations",
        dashboard_template=tmp_path / "missing.json",
        infrastructure_state=tmp_path / "infrastructure/state.json",
        capability_registry=tmp_path / "dashboard/managed/registry.json",
        sites_config=site_config, virtualisation_dir=virtual,
        settings={"virtualisation": {"stale_after_seconds": 3600}})
    result = operations.run(NOW)
    assert any(value["domain"] == "virtualisation" for value in result["risks"])
    assert json.loads((tmp_path / "operations/operations.json").read_text()) == result
    # Feed canonical virtualisation assets and promoted operations into service health.
    assets = json.loads((virtual / "assets.json").read_text())["assets"]
    for asset in assets:
        asset["sources"] = ["virtualisation"]
    write(tmp_path / "infrastructure/state.json", {"assets": assets,
        "collectors": [{"collector": "virtualisation", "status": "healthy",
                        "site_ids": ["head-office"]}], "signals": {}})
    services = ServiceHealthEngine(
        tmp_path / "infrastructure/state.json",
        tmp_path / "operations/operations.json",
        tmp_path / "dashboard/managed/registry.json",
        tmp_path / "services", site_config).run(NOW)
    names = {value["service"]: value for value in services["estate"]["services"]}
    assert names["Shared Storage"]["status"] in {"Healthy", "Warning", "Critical", "Unknown"}
    assert names["Virtualisation Management Plane"]["status"] in {"Healthy", "Unknown"}


def test_virtualisation_fixture_service_boundaries_are_conservative(tmp_path):
    expected = {
        "vmware": {"Shared Storage": "Critical", "Compute Capacity": "Healthy",
                   "Virtual Machine Hosting": "Warning",
                   "Workload Availability": "Healthy"},
        "hyperv": {"Hypervisor Cluster": "Warning", "Compute Capacity": "Healthy",
                   "Workload Availability": "Healthy"},
        "proxmox": {"Shared Storage": "Warning", "Compute Capacity": "Healthy",
                    "Workload Availability": "Healthy"},
    }
    for provider, statuses in expected.items():
        virtual = tmp_path / provider / "virtualisation"
        VirtualisationEngine(ROOT, virtual, "example", "head-office").run_fixture(provider)
        site_config = tmp_path / provider / "sites.yml"
        sites(site_config)
        state_path = tmp_path / provider / "state.json"
        assets = json.loads((virtual / "assets.json").read_text())["assets"]
        for asset in assets:
            asset["sources"] = ["virtualisation"]
        write(state_path, {"assets": assets, "collectors": [{
            "collector": "virtualisation", "status": "healthy",
            "site_ids": ["head-office"]}], "signals": {}})
        registry = tmp_path / provider / "registry.json"
        write(registry, {"enabled_collectors": ["virtualisation"],
            "capabilities": ["telemetry", "virtualisation"],
            "collector_capabilities": {"virtualisation": [
                "telemetry", "virtualisation"]}})
        operations = OperationsEngine(
            output_dir=tmp_path / provider / "operations",
            dashboard_template=tmp_path / "missing.json",
            infrastructure_state=state_path, capability_registry=registry,
            sites_config=site_config, virtualisation_dir=virtual,
            settings={"virtualisation": {"stale_after_seconds": 3600}}).run(NOW)
        services = ServiceHealthEngine(state_path,
            tmp_path / provider / "operations/operations.json", registry,
            tmp_path / provider / "services", site_config).run(NOW)
        actual = {item["service"]: item["status"]
                  for item in services["estate"]["services"]}
        assert {name: actual[name] for name in statuses} == statuses
        assert operations["issues"] or operations["risks"]


def test_profile_without_virtualisation_has_no_virtualisation_services_or_findings(tmp_path):
    site_config = tmp_path / "sites.yml"
    sites(site_config)
    write(tmp_path / "state.json", {"assets": [], "collectors": [], "signals": {}})
    write(tmp_path / "operations.json", {"issues": [], "risks": [], "recommendations": []})
    write(tmp_path / "registry.json", {"enabled_collectors": [], "capabilities": [],
        "collector_capabilities": {}})
    result = ServiceHealthEngine(tmp_path / "state.json", tmp_path / "operations.json",
        tmp_path / "registry.json", tmp_path / "services", site_config).evaluate(NOW)
    virtual = [value for value in result["estate"]["services"]
               if value["service"].startswith(("Virtual", "Hypervisor", "Compute Capacity",
                                               "Shared Storage", "Workload"))]
    assert virtual == []


def test_virtualisation_dependency_propagates_confirmed_storage_not_manager_unknown(tmp_path):
    config = tmp_path / "sites.yml"
    config.write_text("""sites:
  - id: head-office
    display_name: Head Office
  - id: branch
    display_name: Branch
dependencies:
  - service: shared_storage
    provider_site_id: head-office
    consumer_site_ids: [branch]
""")
    engine = ServiceHealthEngine(sites_config=config)
    storage = lambda status: {"service": "Shared Storage", "status": status,
        "summary": status, "affected_assets": [], "evidence": []}
    manager = lambda status: {"service": "Virtualisation Management Plane",
        "status": status, "summary": status, "affected_assets": [], "evidence": []}
    rolled = engine._rollup_services([
        {"site_id": "site:head-office", "services": [storage("Critical"), manager("Unknown")]},
        {"site_id": "site:branch", "services": [storage("Healthy"), manager("Healthy")]},
    ], "2026-07-24T00:00:00Z", "example")
    shared = next(value for value in rolled if value["service"] == "Shared Storage")
    management = next(value for value in rolled
                      if value["service"] == "Virtualisation Management Plane")
    assert shared["affected_site_ids"] == ["site:branch", "site:head-office"]
    assert management["status"] == "Warning"
    assert management["affected_site_ids"] == ["site:head-office"]
