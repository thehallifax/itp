"""Deterministic collector manifest and Grafana provisioning registry."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from collectors.writer import atomic_write


FOLDERS = {
    "Operations": ("itp-folder-operations", "operations"),
    "Infrastructure": ("itp-folder-infrastructure", "infrastructure"),
    "Security": ("itp-folder-security", "security"),
    "Wireless": ("itp-folder-wireless", "wireless"),
    "Printing": ("itp-folder-printing", "printing"),
    "Compute": ("itp-folder-compute", "compute"),
    "Identity": ("itp-folder-identity", "identity"),
    "Virtualisation": ("itp-folder-virtualisation", "virtualisation"),
    "Vendor": ("itp-folder-vendor", "vendor"),
}
CAPABILITIES = {"firewall", "internet", "wireless", "switching", "printing",
                "identity", "compute", "storage", "voice", "email",
                "inventory", "telemetry", "virtualisation"}


@dataclass(frozen=True)
class Manifest:
    collector: str
    version: int
    capabilities: tuple[str, ...]
    requires: tuple[str, ...]
    dashboards: tuple[dict, ...]
    path: Path


class DashboardRegistry:
    """Select and materialize only managed dashboards for enabled collectors."""

    def __init__(self, root, config, output_root=None, provisioning_path=None):
        self.root = Path(root)
        self.config = config
        self.output_root = Path(output_root or self.root / "runtime/dashboard/managed")
        self.provisioning_path = Path(
            provisioning_path or self.root / "grafana/provisioning/dashboards/dashboards.yml")

    def manifests(self):
        paths = [self.root / "dashboards/platform-manifest.yml"]
        paths.extend(sorted((self.root / "collectors").glob("*/dashboard-manifest.yml")))
        paths.append(self.root / "analysis/virtualisation/dashboard-manifest.yml")
        values = []
        for path in paths:
            if not path.exists(): continue
            try: raw = yaml.safe_load(path.read_text())
            except yaml.YAMLError as exc:
                raise ValueError(f"invalid dashboard manifest {path}") from exc
            if not isinstance(raw, dict): raise ValueError(f"dashboard manifest {path} must be a mapping")
            collector = str(raw.get("collector") or "")
            capabilities = tuple(sorted(set(raw.get("capabilities") or [])))
            unknown = set(capabilities) - CAPABILITIES
            if not collector or unknown:
                raise ValueError(f"dashboard manifest {path} has invalid collector or capabilities")
            dashboards = tuple(raw.get("dashboards") or [])
            for dashboard in dashboards:
                if dashboard.get("folder") not in FOLDERS or not dashboard.get("uid") or not dashboard.get("source"):
                    raise ValueError(f"dashboard manifest {path} has an invalid dashboard declaration")
            values.append(Manifest(collector, int(raw.get("version", 1)), capabilities,
                                   tuple(sorted(set(raw.get("requires") or []))),
                                   dashboards, path))
        names = [value.collector for value in values]
        if len(names) != len(set(names)): raise ValueError("dashboard collector manifests must be unique")
        return sorted(values, key=lambda value: value.collector)

    def enabled_collectors(self):
        settings = self.config.get("collectors", {})
        enabled = {name for name, value in settings.items()
                   if isinstance(value, dict) and value.get("enabled") is True}
        if (self.config.get("virtualisation") or {}).get("enabled") is True:
            enabled.add("virtualisation")
        return tuple(sorted(enabled))

    def resolve(self):
        enabled = set(self.enabled_collectors())
        selected_manifests = [value for value in self.manifests()
                              if value.collector == "platform" or value.collector in enabled]
        available_names = {value.collector for value in selected_manifests}
        missing_manifests = enabled - available_names
        if missing_manifests:
            raise ValueError("enabled collectors lack dashboard manifests: " +
                             ", ".join(sorted(missing_manifests)))
        for manifest in selected_manifests:
            missing = set(manifest.requires) - available_names
            if missing:
                raise ValueError(f"dashboard manifest {manifest.collector} requires: {', '.join(sorted(missing))}")
        capabilities = sorted({capability for value in selected_manifests
                               for capability in value.capabilities})
        collector_capabilities = {
            value.collector: list(value.capabilities)
            for value in selected_manifests if value.collector != "platform"
        }
        dashboards = []
        for manifest in selected_manifests:
            for declaration in manifest.dashboards:
                if not set(declaration.get("requires_capabilities") or []) <= set(capabilities):
                    continue
                dashboards.append({**declaration, "collector": manifest.collector,
                    "manifest_version": manifest.version})
        dashboards.sort(key=lambda value: (value["folder"], value["uid"]))
        uids = [value["uid"] for value in dashboards]
        if len(uids) != len(set(uids)): raise ValueError("resolved dashboard UIDs must be unique")
        return {"schema_version": 1, "enabled_collectors": sorted(enabled),
            "capabilities": capabilities,
            "collector_capabilities": collector_capabilities,
            "dashboards": dashboards}

    def _source(self, declaration):
        runtime = declaration.get("runtime_source")
        profile_runtime = self.config.get("deployment_id") and __import__("os").getenv("ITP_RUNTIME_DIR")
        if runtime and profile_runtime and runtime.startswith("runtime/"):
            candidate = Path(profile_runtime) / runtime.removeprefix("runtime/")
            if candidate.exists():
                return candidate
        if runtime and (self.root / runtime).exists(): return self.root / runtime
        source = self.root / declaration["source"]
        if not source.exists(): raise ValueError(f"dashboard source does not exist: {declaration['source']}")
        return source

    def _managed_dashboard(self, path, declaration, capabilities):
        try: dashboard = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid dashboard JSON: {path}") from exc
        if dashboard.get("uid") != declaration["uid"] or not isinstance(dashboard.get("panels"), list):
            raise ValueError(f"dashboard {path} does not satisfy its manifest UID/classic schema")
        if "elements" in dashboard or "layout" in dashboard:
            raise ValueError(f"dashboard {path} uses unsupported v2 schema")
        tags = set(dashboard.get("tags") or [])
        tags.update(("itp", "itp-managed", f"itp-collector:{declaration['collector']}"))
        tags.update(f"itp-capability:{value}" for value in capabilities)
        tags.update(declaration.get("tags") or [])
        deployment_id = str(self.config.get("deployment_id") or "").strip()
        if deployment_id:
            tags.add(f"itp-profile:{deployment_id}")
            for variable in dashboard.get("templating", {}).get("list", []):
                if variable.get("name") == "deployment":
                    variable["query"] = deployment_id
                    variable["current"] = {"selected": True, "text": deployment_id,
                                           "value": deployment_id}
                    variable["options"] = [{"selected": True, "text": deployment_id,
                                            "value": deployment_id}]
        dashboard["tags"] = sorted(tags)
        dashboard["editable"] = False
        return dashboard

    def provisioning(self):
        providers = []
        for folder, (uid, slug) in FOLDERS.items():
            providers.append({"name": f"ITP Managed {folder}", "orgId": 1,
                "folder": folder, "folderUid": uid, "type": "file",
                "disableDeletion": False, "allowUiUpdates": False,
                "updateIntervalSeconds": 30,
                "options": {"path": f"/var/lib/grafana/runtime-dashboard/managed/{slug}"}})
        return {"apiVersion": 1, "providers": providers}

    def generate(self):
        resolved = self.resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)
        expected = set()
        for declaration in resolved["dashboards"]:
            slug = FOLDERS[declaration["folder"]][1]
            destination = self.output_root / slug / (declaration["uid"] + ".json")
            dashboard = self._managed_dashboard(
                self._source(declaration), declaration, resolved["capabilities"])
            atomic_write(destination, json.dumps(dashboard, indent=2, sort_keys=True) + "\n")
            expected.add(destination.resolve())
        for path in sorted(self.output_root.glob("*/*.json")):
            if path.resolve() not in expected: path.unlink()
        for _, slug in FOLDERS.values():
            (self.output_root / slug).mkdir(parents=True, exist_ok=True)
        atomic_write(self.output_root / "registry.json",
            json.dumps(resolved, indent=2, sort_keys=True) + "\n")
        atomic_write(self.provisioning_path,
            yaml.safe_dump(self.provisioning(), sort_keys=False))
        return resolved
