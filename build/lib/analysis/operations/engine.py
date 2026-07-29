"""Deterministic operational intelligence orchestration."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .models import OperationalContext
from .renderer import render_dashboard, write_outputs
from .rules import Rule
from analysis.sites import SiteRegistry
from analysis.virtualisation.operations import VirtualisationOperationsAdapter


def _read(path, fallback):
    path = Path(path)
    try: return json.loads(path.read_text())
    except FileNotFoundError: return fallback
    except json.JSONDecodeError as exc: raise ValueError(f"{path.name} contains malformed JSON") from exc


class OperationsEngine:
    def __init__(self, inventory_dir="/app/runtime/inventory", output_dir="/app/runtime/operations",
                 dashboard_template="/app/dashboards/Infrastructure Overview/infrastructure-overview.json",
                 dashboard_output="/app/runtime/dashboard/grafana/infrastructure-overview.json",
                 infrastructure_state="/app/runtime/infrastructure/state.json",
                 infrastructure_summary="/app/runtime/dashboard/infrastructure-summary.json",
                 settings=None, sites_config="/app/config/sites.yml",
                 capability_registry="/app/runtime/dashboard/managed/registry.json",
                 virtualisation_dir="/app/runtime/virtualisation"):
        self.inventory_dir = Path(inventory_dir); self.output_dir = Path(output_dir)
        self.dashboard_template = Path(dashboard_template); self.settings = settings or {}
        self.dashboard_output = Path(dashboard_output)
        self.infrastructure_state = Path(infrastructure_state)
        self.infrastructure_summary = Path(infrastructure_summary)
        self.capability_registry = Path(capability_registry)
        self.virtualisation_dir = Path(virtualisation_dir)
        self.site_registry = SiteRegistry.load(sites_config)

    def context(self, now=None):
        now = now or datetime.now(timezone.utc)
        state = _read(self.infrastructure_state, {"assets": [], "collectors": [],
                                                  "reconciliations": [], "signals": {}})
        registry = _read(self.capability_registry, {})
        enabled = set(registry.get("enabled_collectors", []))
        sources = {}
        for collector in state.get("collectors", []):
            if enabled and collector.get("collector") not in enabled:
                continue
            status = collector.get("status")
            sources[collector["collector"]] = {
                "consecutive_failures": collector.get("failures", 0),
                "last_run": {"success": True if status == "healthy" else False if status == "failed" else None,
                             "completed_at": collector.get("last_run")},
                "last_complete_successful_run": {"completed_at": collector.get("last_successful_run")},
            }
        return OperationalContext(now=now, assets=state.get("assets", []),
            source_states=sources, reconciliations=state.get("reconciliations", []),
            signals=state.get("signals", {}),
            settings=self.settings)

    def evaluate(self, now=None):
        context = self.context(now); items = []
        for rule in Rule.registered(): items.extend(rule.evaluate(context))
        if self.virtualisation_dir.exists():
            virtualisation = self._virtualisation_state()
            settings = self.settings.get("virtualisation", self.settings)
            items.extend(VirtualisationOperationsAdapter(
                expectations=settings.get("workload_expectations", []),
                stale_seconds=settings.get("stale_after_seconds", 900),
            ).promote(virtualisation, context.now))
        resolved = []
        for value in items:
            if value.site_id or not value.site:
                resolved.append(value); continue
            site = self.site_registry.resolver.resolve(value.site)
            resolved.append(replace(value, site_id=site.site_id or "",
                                    site=site.display_name or value.site))
        items = resolved
        unique = {value.id: value for value in items}
        ordered = sorted(unique.values(), key=lambda value: (-value.priority, value.title, value.id))
        result = {"generated_at": context.now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                  "issues": [], "risks": [], "recommendations": []}
        for value in ordered: result[value.kind + "s"].append(value.to_dict())
        return result

    def _virtualisation_state(self):
        values = {}
        for name, key in (("platforms.json", "platforms"), ("clusters.json", "clusters"),
                          ("hosts.json", "hosts"), ("workloads.json", "workloads"),
                          ("storage.json", "storage"), ("networks.json", "networks"),
                          ("snapshots.json", "snapshots")):
            values.update({item["canonical_id"]: item for item in
                _read(self.virtualisation_dir / name, {}).get(key, [])})
        findings = _read(self.virtualisation_dir / "findings.json", {}).get("findings", [])
        summary = _read(self.virtualisation_dir / "summary.json", {})
        collections = _read(
            self.virtualisation_dir / "collection-status.json", {}).get("collections", [])
        return {"generated_at": summary.get("generated_at"),
                "deployment_id": summary.get("deployment_id", ""),
                "objects": list(values.values()), "findings": findings,
                "collections": collections}

    def run(self, now=None):
        result = self.evaluate(now); write_outputs(self.output_dir, result)
        if self.dashboard_template.exists():
            render_dashboard(self.dashboard_template, self.dashboard_output, result,
                             _read(self.infrastructure_summary, {}))
        return result
