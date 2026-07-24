"""Profile-scoped virtualisation collection, analysis and rendering."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from collectors.hyperv.parser import parse as parse_hyperv
from collectors.proxmox.parser import parse as parse_proxmox
from collectors.vmware.parser import parse as parse_vmware
from .health import evaluate as evaluate_health
from .mapping import map_contract
from .renderer import render


PARSERS = {"vmware": parse_vmware, "hyperv": parse_hyperv, "proxmox": parse_proxmox}


class VirtualisationEngine:
    def __init__(self, root, output_dir, deployment_id, site_id,
                 thresholds=None):
        self.root = Path(root)
        self.output_dir = Path(output_dir)
        self.deployment_id = deployment_id
        self.site_id = site_id
        self.thresholds = thresholds or {}

    def fixture(self, provider):
        path = self.root / "collectors" / provider / "fixtures" / "estate.json"
        contract = PARSERS[provider](json.loads(path.read_text()))
        observed = datetime.fromisoformat(
            str(contract.get("collected_at") or "2026-07-24T00:00:00Z")
            .replace("Z", "+00:00"))
        return self.evaluate([(provider, f"fixture://{provider}", contract)], observed)

    def evaluate(self, collections, now=None):
        generated = (now or datetime.now(timezone.utc)).astimezone(
            timezone.utc).isoformat().replace("+00:00", "Z")
        objects, statuses = [], []
        for item in sorted(collections, key=lambda value: (
                value[0], value[1])):
            provider, endpoint, contract = item[:3]
            site_id = item[3] if len(item) > 3 else self.site_id
            mapped = map_contract(contract, endpoint, self.deployment_id,
                                  site_id, generated)
            objects.extend(mapped)
            diagnostics = sorted(contract.get("diagnostics", []),
                                 key=lambda value: json.dumps(value, sort_keys=True))
            statuses.append({
                "provider": provider, "endpoint": endpoint,
                "last_attempt": generated, "last_success": generated,
                "duration_ms": 0, "result": "partial" if diagnostics else "success",
                "partial": bool(diagnostics),
                "diagnostic_category": diagnostics[0].get("category")
                    if diagnostics else "success",
                "diagnostics": diagnostics,
            })
        objects = sorted(objects, key=lambda value: (
            value["provider"], value["kind"], value["canonical_id"]))
        findings = evaluate_health(objects, self.thresholds)
        kinds = Counter(value["kind"] for value in objects)
        severities = Counter(value["severity"] for value in findings)
        summary = {
            "schema_version": 1, "generated_at": generated,
            "deployment_id": self.deployment_id,
            "providers": len({value["provider"] for value in objects}),
            "managers": kinds["manager"], "clusters": kinds["cluster"],
            "hosts": kinds["host"], "vms": kinds["vm"],
            "containers": kinds["container"],
            "running_workloads": sum(value.get("power_state") == "running"
                                     for value in objects),
            "warnings": severities["Medium"] + severities["High"],
            "critical_findings": severities["Critical"],
            "unknown_findings": sum(value["rule_id"].endswith("unknown")
                                    for value in findings),
        }
        assets = [self._asset(value) for value in objects
                  if value["kind"] in {"manager", "cluster", "host", "vm",
                                      "container", "storage", "network"}]
        return {"schema_version": 1, "generated_at": generated,
                "deployment_id": self.deployment_id, "objects": objects,
                "findings": findings, "summary": summary,
                "collections": statuses, "assets": assets}

    @staticmethod
    def _asset(value):
        roles = {
            "manager": ("virtualisation-manager", "management-plane"),
            "cluster": ("virtualisation-cluster", "compute-cluster"),
            "host": ("hypervisor", "compute"),
            "vm": ("virtual-machine", "compute-workload"),
            "container": ("virtual-container", "compute-workload"),
            "storage": ("virtual-storage", "storage"),
            "network": ("virtual-network", "network"),
        }
        device_type, role = roles[value["kind"]]
        return {
            "source": value["provider"], "collector": "virtualisation",
            "asset_id": value["canonical_id"], "source_asset_id": value["source_id"],
            "canonical_id": value["canonical_id"], "hostname": value["name"],
            "display_name": value["display_name"], "site_id": value["site_id"],
            "site": value["site_id"], "vendor": value["provider"],
            "platform": value["provider"], "device_type": device_type,
            "device_role": role,
            "online": value.get("reachable") if value["kind"] == "manager"
                else value.get("connection_state") == "connected"
                if value["kind"] == "host" else value.get("power_state") == "running"
                if value["kind"] in {"vm", "container"} else None,
            "source_last_seen_at": value["observed_at"],
            "relationships": {
                key: value.get(key) for key in ("manager_id", "cluster_id", "host_id")
                if value.get(key)},
            "extensions": {"virtualisation": value},
        }

    def run_fixture(self, provider):
        return render(self.output_dir, self.fixture(provider))
