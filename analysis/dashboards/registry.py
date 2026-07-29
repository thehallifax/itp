"""Deterministic collector manifest and Grafana provisioning registry."""
from __future__ import annotations

import json
import csv
import io
import os
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path

import yaml

from collectors.writer import atomic_write
from collectors.connector_registry import ConnectorMetadataRegistry
from analysis.readiness import credentials_ready, evaluate_readiness
from analysis.readiness import empty_infrastructure_summary
from analysis.operations.renderer import render_dashboard
from analysis.wallboard import WallboardEngine


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


class DashboardPackRegistry:
    """Select and materialize only managed dashboards for enabled collectors."""

    def __init__(self, root, config, output_root=None, provisioning_path=None,
                 *, registry_validation_mode="strict"):
        self.root = Path(root)
        self.config = config
        self.output_root = Path(output_root or self.root / "runtime/dashboard/managed")
        self.provisioning_path = Path(
            provisioning_path or self.root / "grafana/provisioning/dashboards/dashboards.yml")
        self.registry_validation_mode = registry_validation_mode

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
        packs = []
        for manifest in selected_manifests:
            pack_dashboards = []
            for declaration in manifest.dashboards:
                if not set(declaration.get("requires_capabilities") or []) <= set(capabilities):
                    continue
                value = {**declaration, "collector": manifest.collector,
                         "manifest_version": manifest.version}
                dashboards.append(value)
                pack_dashboards.append(declaration["uid"])
            packs.append({
                "id": manifest.collector,
                "version": manifest.version,
                "dashboards": sorted(pack_dashboards),
                "capabilities": list(manifest.capabilities),
            })
        dashboards.sort(key=lambda value: (value["folder"], value["uid"]))
        uids = [value["uid"] for value in dashboards]
        if len(uids) != len(set(uids)): raise ValueError("resolved dashboard UIDs must be unique")
        return {"schema_version": 1, "enabled_collectors": sorted(enabled),
            "capabilities": capabilities,
            "collector_capabilities": collector_capabilities,
            "packs": sorted(packs, key=lambda value: value["id"]),
            "dashboards": dashboards}

    def _source(self, declaration):
        runtime = declaration.get("runtime_source")
        runtime_root = (
            self.output_root.parent.parent
            if self.output_root.parent.name == "dashboard"
            else self.output_root.parent)
        if runtime and runtime.startswith("runtime/"):
            candidate = runtime_root / runtime.removeprefix("runtime/")
            if candidate.exists():
                return candidate
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
        tags.add(f"itp-pack-version:{declaration['manifest_version']}")
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
        self._apply_capability_states(dashboard, declaration)
        if dashboard.get("uid") == "itp-collector-health":
            self._collector_health_empty_state(
                dashboard, getattr(self, "_readiness", {}))
        return dashboard

    def _apply_capability_states(self, dashboard, declaration):
        """Apply canonical empty-state labels without changing panel queries."""
        collector = declaration.get("collector")
        if collector in {"platform", "core"}:
            return
        runtime = (
            self.output_root.parent.parent
            if self.output_root.parent.name == "dashboard"
            else self.output_root.parent)
        manifest = self._read(
            runtime / f"capabilities/{collector}.json", {})
        labels = {
            "disabled": "Collector Disabled",
            "not_yet_collected": "Not Yet Collected",
            "unavailable": "Currently Unavailable",
            "failed": "Collection Failed",
            "partial": "Partial Data",
            "not_applicable": "Feature Not Enabled",
        }
        for capability in manifest.get("capabilities", []):
            state = capability.get("collection")
            if state == "collected":
                continue
            for panel in dashboard.get("panels", []):
                title = str(panel.get("title") or "")
                if not any(str(value).casefold() in title.casefold()
                           for value in capability.get("panels", []) if value):
                    continue
                defaults = panel.setdefault("fieldConfig", {}).setdefault(
                    "defaults", {})
                defaults["noValue"] = labels.get(state, "Not Yet Collected")
                explanation = capability.get("explanation")
                if explanation:
                    existing = str(panel.get("description") or "").strip()
                    panel["description"] = (
                        f"{existing}\n\nCapability state: {explanation}".strip())

    @staticmethod
    def _read(path, fallback):
        try:
            return json.loads(Path(path).read_text())
        except (OSError, json.JSONDecodeError):
            return fallback

    def readiness(self, resolved):
        runtime = (
            self.output_root.parent.parent
            if self.output_root.parent.name == "dashboard"
            else self.output_root.parent)
        state = self._read(
            runtime / "infrastructure/state.json",
            {"assets": [], "collectors": []})
        provisioning = self._read(
            runtime / "provisioning/state.json", {})
        operations = self._read(
            runtime / "operations/operations.json", {})
        capability_manifest = self._read(
            runtime / "capabilities/collectors.json", {})
        demo = (
            str(self.config.get("deployment_id") or "").casefold() == "demo"
            or (runtime / "demo.json").is_file())
        timestamp = (
            state.get("generated_at") or operations.get("generated_at")
            or provisioning.get("last_attempt")
            or "1970-01-01T00:00:00Z")
        try:
            now = datetime.fromisoformat(
                str(timestamp).replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            now = datetime(1970, 1, 1, tzinfo=timezone.utc)
        ready = demo or credentials_ready(
            self.config, ConnectorMetadataRegistry.load(
                self.root,
                validation_mode=self.registry_validation_mode),
            os.environ)
        value = evaluate_readiness(
            enabled_collectors=resolved["enabled_collectors"],
            collector_records=state.get("collectors", []),
            capability_manifest=capability_manifest,
            capabilities=resolved["capabilities"],
            assets=state.get("assets", []),
            operations_generated=bool(operations.get("generated_at")),
            deployment_configured=bool(
                self.config.get("deployment_id")
                or self.config.get("deployment")),
            platform_running=provisioning.get("status") == "complete",
            credentials_configured=ready, demo=demo, now=now)
        atomic_write(
            runtime / "dashboard/readiness.json",
            json.dumps(value, indent=2, sort_keys=True) + "\n")
        return value

    @staticmethod
    def _collector_health_empty_state(dashboard, readiness):
        collectors = readiness.get("collectors") or []
        waiting = not collectors or all(
            value.get("state") == "waiting_first_collection"
            for value in collectors)
        default_label = (
            "No collectors enabled" if not collectors
            else "Waiting for first run")
        for panel in dashboard.get("panels", []):
            defaults = panel.setdefault("fieldConfig", {}).setdefault(
                "defaults", {})
            defaults["noValue"] = (
                "No run in selected range"
                if not waiting else default_label)
        if not waiting:
            return
        datasource = {
            "type": "grafana-testdata-datasource",
            "uid": "itp-runtime-values",
        }
        labels = {
            "Collectors Healthy": default_label,
            "Collectors Requiring Attention": default_label,
            "Latest Duration": "No run yet",
            "Latest Points Written": "No run yet",
        }
        for panel in dashboard.get("panels", []):
            if panel.get("title") not in labels:
                continue
            label = labels[panel["title"]]
            panel["datasource"] = datasource
            panel["targets"] = [{
                "refId": "A", "scenarioId": "csv_content",
                "csvContent": f"State\n{label}",
                "datasource": datasource,
            }]
            panel["transformations"] = []
            panel["fieldConfig"]["defaults"].update({
                "unit": "string",
                "mappings": [],
                "thresholds": {"mode": "absolute", "steps": [{
                    "color": "gray", "value": None}]},
            })
            panel["options"]["textMode"] = "value"
            panel["options"]["reduceOptions"] = {
                "calcs": [], "fields": "/^State$/", "values": True}
        table = next(
            panel for panel in dashboard["panels"]
            if panel.get("title") == "Collector Runs")
        stream = io.StringIO()
        fields = ("State", "Explanation", "Operator Action")
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerow({
            "State": default_label,
            "Explanation": (
                "Collection is disabled until a collector is configured."
                if not collectors
                else "An enabled collector has not completed its first run."),
            "Operator Action": readiness.get(
                "observability", {}).get("operator_action", ""),
        })
        table["datasource"] = datasource
        table["targets"] = [{
            "refId": "A", "scenarioId": "csv_content",
            "csvContent": stream.getvalue().rstrip(),
            "datasource": datasource,
        }]

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
        self._readiness = self.readiness(resolved)
        self.output_root.mkdir(parents=True, exist_ok=True)
        atomic_write(self.output_root / "registry.json",
            json.dumps(resolved, indent=2, sort_keys=True) + "\n")
        runtime = (
            self.output_root.parent.parent
            if self.output_root.parent.name == "dashboard"
            else self.output_root.parent)
        summary_path = runtime / "dashboard/infrastructure-summary.json"
        summary = self._read(
            summary_path, empty_infrastructure_summary(self._readiness))
        summary["readiness"] = self._readiness
        if not resolved["enabled_collectors"]:
            empty = empty_infrastructure_summary(self._readiness)
            summary.update(empty)
        atomic_write(
            summary_path, json.dumps(summary, indent=2, sort_keys=True) + "\n")
        operations = self._read(
            runtime / "operations/operations.json",
            {"generated_at": self._readiness["generated_at"],
             "issues": [], "risks": [], "recommendations": []})
        render_dashboard(
            self.root / "dashboards/Infrastructure Overview/infrastructure-overview.json",
            runtime / "dashboard/grafana/infrastructure-overview.json",
            operations, summary)
        readiness_now = datetime.fromisoformat(
            self._readiness["generated_at"].replace("Z", "+00:00"))
        WallboardEngine(
            infrastructure_state=runtime / "infrastructure/state.json",
            operations_state=runtime / "operations/operations.json",
            sites_state=runtime / "sites/sites.json",
            dashboard_template=self.root /
                "dashboards/Operations/operations-wallboard.json",
            summary_output=runtime / "dashboard/wallboard-summary.json",
            dashboard_output=runtime /
                "dashboard/operations/operations-wallboard.json",
            capability_registry=self.output_root / "registry.json",
            service_health=runtime / "services/service-health.json",
            readiness_state=runtime / "dashboard/readiness.json").run(
                readiness_now)
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
        atomic_write(self.provisioning_path,
            yaml.safe_dump(self.provisioning(), sort_keys=False))
        return resolved


# Backwards-compatible public name used by existing collectors and profiles.
DashboardRegistry = DashboardPackRegistry
