"""Deterministic operational intelligence orchestration."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .models import OperationalContext
from .renderer import render_dashboard, write_outputs
from .rules import Rule


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
                 settings=None):
        self.inventory_dir = Path(inventory_dir); self.output_dir = Path(output_dir)
        self.dashboard_template = Path(dashboard_template); self.settings = settings or {}
        self.dashboard_output = Path(dashboard_output)
        self.infrastructure_state = Path(infrastructure_state)
        self.infrastructure_summary = Path(infrastructure_summary)

    def context(self, now=None):
        now = now or datetime.now(timezone.utc)
        state = _read(self.infrastructure_state, {"assets": [], "collectors": [],
                                                  "reconciliations": [], "signals": {}})
        sources = {}
        for collector in state.get("collectors", []):
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
        unique = {value.id: value for value in items}
        ordered = sorted(unique.values(), key=lambda value: (-value.priority, value.title, value.id))
        result = {"generated_at": context.now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                  "issues": [], "risks": [], "recommendations": []}
        for value in ordered: result[value.kind + "s"].append(value.to_dict())
        return result

    def run(self, now=None):
        result = self.evaluate(now); write_outputs(self.output_dir, result)
        if self.dashboard_template.exists():
            render_dashboard(self.dashboard_template, self.dashboard_output, result,
                             _read(self.infrastructure_summary, {}))
        return result
