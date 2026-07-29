#!/usr/bin/env python3
"""Render deterministic, profile-isolated Operations Wallboard evidence."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analysis.dashboards import DashboardRegistry
from analysis.operations import OperationsEngine
from analysis.services import ServiceHealthEngine
from analysis.virtualisation import VirtualisationEngine
from analysis.wallboard import WallboardEngine
from collectors.writer import atomic_write


NOW = datetime(2026, 7, 24, 0, 5, tzinfo=timezone.utc)
SCENARIOS = ("example-corporate", "vmware", "hyperv", "proxmox")


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def render(scenario, output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    site_id = "site:evidence"
    sites_config = output / "config/sites.yml"
    sites_config.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(sites_config, """sites:
  - id: evidence
    display_name: Release Evidence — Long Canonical Site Name
""")
    registry_path = output / "dashboard/managed/registry.json"
    infrastructure_path = output / "infrastructure/state.json"
    operations_path = output / "operations/operations.json"

    if scenario == "example-corporate":
        capabilities = ["switching", "telemetry", "wireless"]
        enabled = ["mist", "snmp"]
        collector_capabilities = {
            "mist": ["inventory", "telemetry", "wireless"],
            "snmp": ["inventory", "switching", "telemetry"],
        }
        assets = [
            {"canonical_id": "evidence:ap:1", "hostname": "example-corporate-AP-Offline-Example",
             "display_name": "example-corporate-AP-Offline-Example", "device_type": "access-point",
             "online": False, "sources": ["mist"], "site_id": site_id,
             "site": {"site_id": site_id,
                      "display_name": "Release Evidence — Long Canonical Site Name"}},
            {"canonical_id": "evidence:switch:1", "hostname": "example-corporate-Core-Switch",
             "display_name": "example-corporate-Core-Switch", "device_type": "switch",
             "online": True, "sources": ["snmp"], "site_id": site_id,
             "site": {"site_id": site_id,
                      "display_name": "Release Evidence — Long Canonical Site Name"}},
        ]
        collectors = [
            {"collector": "mist", "status": "warning", "site_ids": [site_id],
             "last_run": "2026-07-24T00:03:00Z",
             "last_successful_run": "2026-07-24T00:03:00Z"},
            {"collector": "snmp", "status": "healthy", "site_ids": [site_id],
             "last_run": "2026-07-24T00:04:00Z",
             "last_successful_run": "2026-07-24T00:04:00Z"},
        ]
        write(operations_path, {"generated_at": "2026-07-24T00:05:00Z",
            "issues": [{"id": f"ops:evidence:wireless:{index}", "kind": "issue",
                "rule_id": f"wireless.fixture.{index}", "title": "Wireless attention required",
                "category": "Wireless", "severity": "High" if index < 3 else "Medium",
                "priority": 85 - index,
                "canonical_id": f"evidence:ap:{index}",
                "device": f"example-corporate-AP-{index:02d}-Sanitized-Example",
                "site_id": site_id, "site": "Release Evidence — Long Canonical Site Name",
                "summary": "Sanitized wireless evidence requires operator attention.",
                "evidence": {"age_seconds": index * 240,
                             "source_collector": "mist"}}
                for index in range(1, 6)],
            "risks": [], "recommendations": []})
    else:
        # Generic Compute/Storage cards are intentionally omitted: provider
        # fixtures exercise the more specific canonical virtualisation services.
        capabilities = ["telemetry", "virtualisation"]
        enabled = ["virtualisation"]
        collector_capabilities = {"virtualisation": capabilities}
        virtual_dir = output / "virtualisation"
        VirtualisationEngine(ROOT, virtual_dir, f"evidence-{scenario}",
                             site_id).run_fixture(scenario)
        assets = json.loads((virtual_dir / "assets.json").read_text())["assets"]
        for asset in assets:
            asset["sources"] = ["virtualisation"]
            asset["site"] = {"site_id": site_id,
                             "display_name": "Release Evidence — Long Canonical Site Name"}
        collectors = [{"collector": "virtualisation", "status": "healthy",
            "site_ids": [site_id], "last_run": "2026-07-24T00:04:00Z",
            "last_successful_run": "2026-07-24T00:04:00Z"}]

    write(infrastructure_path, {"schema_version": 1,
        "generated_at": "2026-07-24T00:04:00Z",
        "deployment_id": f"evidence-{scenario}", "assets": assets,
        "collectors": collectors, "signals": {}, "reconciliations": [],
        "summary": {"infrastructure_health": "Warning",
                    "observability_health": "Warning",
                    "actionable_warnings": 1,
                    "collectors_healthy": len(collectors),
                    "collectors_failed": 0}})
    write(registry_path, {"schema_version": 1, "enabled_collectors": enabled,
        "capabilities": capabilities, "collector_capabilities": collector_capabilities,
        "dashboards": [{"uid": "itp-operations-wallboard"},
                       {"uid": "itp-collector-health"}]})

    if scenario != "example-corporate":
        OperationsEngine(
            output_dir=output / "operations",
            dashboard_template=output / "missing-dashboard.json",
            infrastructure_state=infrastructure_path,
            capability_registry=registry_path,
            sites_config=sites_config,
            virtualisation_dir=output / "virtualisation",
            settings={"virtualisation": {"stale_after_seconds": 3600}},
        ).run(NOW)

    services_path = output / "services"
    ServiceHealthEngine(infrastructure_path, operations_path, registry_path,
                        services_path, sites_config).run(NOW)
    write(output / "sites/sites.json", {"schema_version": 1,
        "generated_at": "2026-07-24T00:05:00Z",
        "sites": [{"site_id": site_id,
                   "display_name": "Release Evidence — Long Canonical Site Name"}]})
    WallboardEngine(
        infrastructure_path, operations_path, output / "sites/sites.json",
        ROOT / "dashboards/Operations/operations-wallboard.json",
        output / "dashboard/wallboard-summary.json",
        output / "dashboard/operations/operations-wallboard.json",
        registry_path, services_path / "service-health.json",
        freshness_seconds=900,
    ).run(NOW)

    config = {"collectors": {}, "deployment_id": f"evidence-{scenario}"}
    if scenario == "example-corporate":
        config["collectors"] = {"mist": {"enabled": True},
                                "snmp": {"enabled": True}}
    else:
        config["virtualisation"] = {"enabled": True}
    previous = os.environ.get("ITP_RUNTIME_DIR")
    os.environ["ITP_RUNTIME_DIR"] = str(output)
    try:
        DashboardRegistry(ROOT, config, output / "dashboard/managed",
            output / "dashboard/provisioning/dashboards.yml").generate()
    finally:
        if previous is None:
            os.environ.pop("ITP_RUNTIME_DIR", None)
        else:
            os.environ["ITP_RUNTIME_DIR"] = previous
    dashboard = output / "dashboard/managed/operations/itp-operations-wallboard.json"
    print(f"Scenario: {scenario}")
    print(f"Runtime: {output}")
    print(f"Dashboard: {dashboard}")
    return dashboard


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=SCENARIOS)
    parser.add_argument("--output")
    args = parser.parse_args()
    output = args.output or ROOT / "runtime/evidence" / args.scenario
    render(args.scenario, output)


if __name__ == "__main__":
    main()
