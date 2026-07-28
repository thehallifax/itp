"""Canonical, site-partitioned and capability-aware Service Health Engine."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from analysis.sites import SiteRegistry
from .evaluators import ServiceEvaluator
from .models import SERVICE_NAMES, VIRTUALISATION_SERVICE_NAMES
from .renderer import write_service_health


def _read(path, fallback):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return fallback
    except json.JSONDecodeError as exc:
        raise ValueError(f"{Path(path).name} contains malformed JSON") from exc


def _asset_site(asset):
    site = asset.get("site") or {}
    if isinstance(site, dict):
        return site.get("site_id"), site.get("display_name")
    return asset.get("site_id"), site


def _overall(services):
    enabled = [value for value in services if value["status"] != "Not Enabled"]
    statuses = {value["status"] for value in enabled}
    if "Critical" in statuses:
        return "Critical"
    if "Warning" in statuses:
        return "Warning"
    if "Unknown" in statuses or not enabled:
        return "Unknown"
    return "Healthy"


class ServiceHealthEngine:
    """Partition canonical inputs by site before running service evaluators."""

    def __init__(self, infrastructure_state="/app/runtime/infrastructure/state.json",
                 operations_state="/app/runtime/operations/operations.json",
                 capability_registry="/app/runtime/dashboard/managed/registry.json",
                 output_dir="/app/runtime/services",
                 sites_config="/app/config/sites.yml",
                 capability_manifest="/app/runtime/capabilities/collectors.json"):
        self.infrastructure_state = Path(infrastructure_state)
        self.operations_state = Path(operations_state)
        self.capability_registry = Path(capability_registry)
        self.capability_manifest = Path(capability_manifest)
        self.output_dir = Path(output_dir)
        self.site_registry = SiteRegistry.load(sites_config)

    def _resolve(self, value, explicit_id, record_type, record_id, diagnostics):
        resolution = self.site_registry.resolver.resolve(value, explicit_id)
        if resolution.status != "resolved":
            diagnostics.append({"type": "unmapped_site", "record_type": record_type,
                "record_id": str(record_id or ""), "source_value": str(value or explicit_id or ""),
                "status": resolution.status, "explanation": resolution.explanation})
            return None
        return resolution

    def context(self):
        state = _read(self.infrastructure_state,
                      {"assets": [], "collectors": [], "signals": {}})
        operations = _read(self.operations_state,
                           {"issues": [], "risks": [], "recommendations": []})
        registry = _read(self.capability_registry,
                         {"capabilities": [], "enabled_collectors": [],
                          "collector_capabilities": {}})
        manifest = _read(self.capability_manifest, {"collectors": {}})
        enabled_collectors = tuple(sorted(registry.get("enabled_collectors", [])))
        enabled_set = set(enabled_collectors)
        diagnostics = []

        assets = []
        for original in state.get("assets", []):
            value = dict(original)
            site_id, site_name = _asset_site(value)
            resolution = self._resolve(site_name, site_id, "asset",
                value.get("canonical_id") or value.get("asset_id"), diagnostics)
            if resolution:
                value["site_id"] = resolution.site_id
                value["site_name"] = resolution.display_name
                value["site"] = {"site_id": resolution.site_id,
                                 "display_name": resolution.display_name}
            assets.append(value)

        collectors = []
        assets_by_source = {}
        for asset in assets:
            site_id, _ = _asset_site(asset)
            if not site_id:
                continue
            for source in asset.get("sources", []):
                assets_by_source.setdefault(source, set()).add(site_id)
        for original in state.get("collectors", []):
            if original.get("collector") not in enabled_set:
                continue
            value = dict(original)
            site_ids = set()
            for explicit in value.get("site_ids", []):
                resolution = self._resolve(None, explicit, "collector",
                    value.get("collector"), diagnostics)
                if resolution:
                    site_ids.add(resolution.site_id)
            site_ids.update(assets_by_source.get(value.get("collector"), set()))
            value["site_ids"] = sorted(site_ids)
            value["site_names"] = [
                self.site_registry.definition(site_id).display_name
                for site_id in value["site_ids"] if self.site_registry.definition(site_id)]
            if not site_ids and value.get("shared") is not True:
                diagnostics.append({"type": "collector_site_unattributed",
                    "record_type": "collector", "record_id": value.get("collector", ""),
                    "source_value": "", "status": "unknown",
                    "explanation": "Enabled collector has no canonical asset or explicit site attribution."})
            collectors.append(value)

        findings = []
        for kind in ("issues", "risks"):
            for original in operations.get(kind, []):
                source = str((original.get("evidence") or {}).get("source_collector") or "")
                collector_name = str(original.get("device") or "") \
                    if original.get("category") == "Collector" else ""
                if source and source not in enabled_set:
                    continue
                if collector_name and collector_name not in enabled_set:
                    continue
                value = {**original, "kind": kind[:-1]}
                resolution = self._resolve(value.get("site"), value.get("site_id"),
                    "finding", value.get("id"), diagnostics) \
                    if value.get("site") or value.get("site_id") else None
                if resolution:
                    value["site_id"] = resolution.site_id
                    value["site_name"] = resolution.display_name
                    value["site"] = resolution.display_name
                elif value.get("category") == "Collector" and collector_name:
                    collector = next((item for item in collectors
                                      if item.get("collector") == collector_name), {})
                    site_ids = collector.get("site_ids", [])
                    if len(site_ids) == 1:
                        value["site_id"] = site_ids[0]
                        definition = self.site_registry.definition(site_ids[0])
                        value["site_name"] = definition.display_name if definition else ""
                    elif not site_ids and collector.get("shared") is not True:
                        diagnostics.append({"type": "finding_site_unattributed",
                            "record_type": "finding", "record_id": value.get("id", ""),
                            "source_value": collector_name, "status": "unknown",
                            "explanation": "Collector finding cannot be assigned to a canonical site."})
                elif not value.get("site") and not value.get("site_id"):
                    diagnostics.append({"type": "finding_site_unattributed",
                        "record_type": "finding", "record_id": value.get("id", ""),
                        "source_value": "", "status": "unknown",
                        "explanation": "Finding has no canonical site identity."})
                findings.append(value)

        signals = {}
        for key, raw in state.get("signals", {}).items():
            values = raw if isinstance(raw, list) else [raw]
            resolved_values = []
            for index, original in enumerate(values):
                if not isinstance(original, dict):
                    diagnostics.append({"type": "signal_site_unattributed",
                        "record_type": "signal", "record_id": f"{key}:{index}",
                        "source_value": "", "status": "unknown",
                        "explanation": "Scalar signal has no canonical site identity."})
                    resolved_values.append(original)
                    continue
                value = dict(original)
                if value.get("site") or value.get("site_id"):
                    resolution = self._resolve(value.get("site"), value.get("site_id"),
                        "signal", f"{key}:{index}", diagnostics)
                    if resolution:
                        value["site_id"] = resolution.site_id
                        value["site_name"] = resolution.display_name
                        value["site"] = resolution.display_name
                else:
                    diagnostics.append({"type": "signal_site_unattributed",
                        "record_type": "signal", "record_id": f"{key}:{index}",
                        "source_value": "", "status": "unknown",
                        "explanation": "Signal has no canonical site identity."})
                resolved_values.append(value)
            signals[key] = resolved_values if isinstance(raw, list) else (
                resolved_values[0] if resolved_values else raw)

        diagnostics = sorted({json.dumps(value, sort_keys=True): value
                              for value in diagnostics}.values(),
                             key=lambda value: (value["type"], value["record_type"],
                                                value["record_id"], value["source_value"]))
        return {"assets": assets, "collectors": collectors, "signals": signals,
            "findings": findings,
            "capabilities": frozenset(registry.get("capabilities", [])),
            "collector_capabilities": registry.get("collector_capabilities", {}),
            "capability_manifest": manifest.get("collectors", {}),
            "enabled_collectors": enabled_collectors, "diagnostics": diagnostics}

    @staticmethod
    def _services(context):
        virtual = "virtualisation" in context["capabilities"]
        evaluators = {value.definition.name: value for value in
                      ServiceEvaluator.registered(include_virtualisation=virtual)}
        names = SERVICE_NAMES + (VIRTUALISATION_SERVICE_NAMES if virtual else ())
        return [evaluators[name].evaluate(context).to_dict() for name in names]

    @staticmethod
    def _site_context(context, site_id):
        relevant_collectors = [value for value in context["collectors"]
            if value.get("shared") is True or site_id in value.get("site_ids", [])]
        relevant_names = {value["collector"] for value in relevant_collectors}
        capabilities = {capability for name in relevant_names
                        for capability in context["collector_capabilities"].get(name, [])}
        assets = [value for value in context["assets"] if _asset_site(value)[0] == site_id]
        findings = [value for value in context["findings"]
                    if value.get("site_id") == site_id
                    or (value.get("category") == "Collector"
                        and value.get("device") in relevant_names)]
        signals = {}
        for key, raw in context["signals"].items():
            if isinstance(raw, list):
                signals[key] = [value for value in raw if isinstance(value, dict)
                                and value.get("site_id") == site_id]
            elif isinstance(raw, dict) and raw.get("site_id") == site_id:
                signals[key] = raw
        return {**context, "assets": assets, "collectors": relevant_collectors,
                "findings": findings, "signals": signals,
                "capabilities": frozenset(capabilities),
                "enabled_collectors": tuple(sorted(relevant_names))}

    def evaluate(self, now=None):
        now = now or datetime.now(timezone.utc)
        context = self.context()
        context["now"] = now
        sites = []
        for definition in self.site_registry.sites:
            site_context = self._site_context(context, definition.site_id)
            services = self._services(site_context)
            sites.append({"site_id": definition.site_id,
                "site_name": definition.display_name,
                "overall_status": _overall(services),
                "enabled_collectors": list(site_context["enabled_collectors"]),
                "capabilities": sorted(site_context["capabilities"]),
                "services": services})
        generated_at = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        deployment_id = os.getenv("ITP_DEPLOYMENT_ID", "")
        if sites:
            estate_services = self._rollup_services(sites, generated_at, deployment_id)
        else:
            estate_services = []
            for value in self._services(context):
                state = value["status"]
                estate_services.append({**value,
                    "deployment_id": deployment_id,
                    "service_id": value["service"].casefold().replace(" ", "_"),
                    "state": state,
                    "confidence": "low" if state == "Unknown" else "high",
                    "affected_site_count": 0,
                    "total_site_count": 0,
                    "affected_site_ids": [],
                    "last_evaluated": generated_at})
        return {"schema_version": 2,
            "generated_at": generated_at, "deployment_id": deployment_id,
            "sites": sites,
            "estate": {"site_id": "all", "site_name": "All Sites",
                "overall_status": _overall(estate_services),
                "enabled_collectors": list(context["enabled_collectors"]),
                "capabilities": sorted(context["capabilities"]),
                "services": estate_services},
            "diagnostics": context["diagnostics"]}

    def _rollup_services(self, sites, generated_at, deployment_id):
        """Roll site evaluations without inventing shared dependency relationships."""
        by_site = {site["site_id"]: site for site in sites}
        dependencies = {
            str(value.get("service", "")).casefold().replace("_", " "): value
            for value in self.site_registry.dependencies if isinstance(value, dict)}
        results = []
        service_names = list(SERVICE_NAMES)
        for site in sites:
            for value in site["services"]:
                if value["service"] not in service_names:
                    service_names.append(value["service"])
        for service_name in service_names:
            values = [(site["site_id"], value) for site in sites
                      for value in site["services"] if value["service"] == service_name]
            enabled = [(site_id, value) for site_id, value in values
                       if value["status"] != "Not Enabled"]
            statuses = {value["status"] for _, value in enabled}
            if not enabled:
                state, confidence = "Not Enabled", "high"
            elif "Critical" in statuses:
                state, confidence = "Critical", "high"
            elif "Warning" in statuses:
                state, confidence = "Warning", "high"
            elif "Unknown" in statuses:
                state, confidence = ("Warning", "medium") if "Healthy" in statuses \
                    else ("Unknown", "low")
            else:
                state, confidence = "Healthy", "high"
            affected = sorted(site_id for site_id, value in enabled
                              if value["status"] in {"Critical", "Warning", "Unknown"})
            evidence = [{"type": "site_rollup", "site_id": site_id,
                         "status": value["status"], "summary": value["summary"]}
                        for site_id, value in enabled]
            dependency = dependencies.get(service_name.casefold())
            if dependency:
                provider = self.site_registry.resolver.resolve(
                    None, dependency.get("provider_site_id"))
                consumers = []
                for site_id in dependency.get("consumer_site_ids", []):
                    resolution = self.site_registry.resolver.resolve(None, site_id)
                    if resolution.status == "resolved":
                        consumers.append(resolution.site_id)
                provider_value = next((value for site_id, value in enabled
                                       if site_id == provider.site_id), None)
                if provider.status == "resolved" and provider_value:
                    evidence.append({"type": "central_dependency",
                        "provider_site_id": provider.site_id,
                        "consumer_site_ids": sorted(set(consumers)),
                        "provider_status": provider_value["status"]})
                    if provider_value["status"] == "Critical":
                        state, confidence = "Critical", "high"
                        affected = sorted(set([provider.site_id, *consumers]))
            results.append({
                "deployment_id": deployment_id,
                "service_id": service_name.casefold().replace(" ", "_"),
                "service": service_name, "status": state, "state": state,
                "confidence": confidence,
                "affected_site_count": len(affected),
                "total_site_count": len(sites),
                "affected_site_ids": affected,
                "summary": (
                    f"{service_name} is {state.lower()} across "
                    f"{len(sites)} enabled site{'s' if len(sites) != 1 else ''}."),
                "affected_assets": sorted({asset for _, value in enabled
                                           for asset in value.get("affected_assets", [])}),
                "affected_users": None,
                "severity": {"Critical": "Critical", "Warning": "Medium"}.get(state, "Info"),
                "last_change": None,
                "last_evaluated": generated_at,
                "evidence": evidence,
            })
        return results

    def run(self, now=None):
        result = self.evaluate(now)
        write_service_health(self.output_dir, result)
        return result
