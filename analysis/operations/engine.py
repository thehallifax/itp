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
                 settings=None):
        self.inventory_dir = Path(inventory_dir); self.output_dir = Path(output_dir)
        self.dashboard_template = Path(dashboard_template); self.settings = settings or {}

    def context(self, now=None):
        now = now or datetime.now(timezone.utc)
        inventory = _read(self.inventory_dir / "assets.json", {"assets": []})
        sources = _read(self.inventory_dir / "source_runs.json", {"sources": {}})
        reconciliation = _read(self.inventory_dir / "reconciliation.json", {"reconciliations": []})
        signals = _read(self.output_dir / "signals.json", {})
        return OperationalContext(now=now, assets=inventory.get("assets", []),
            source_states=sources.get("sources", {}),
            reconciliations=reconciliation.get("reconciliations", []), signals=signals,
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
            render_dashboard(self.dashboard_template,
                self.output_dir / "dashboard" / "infrastructure-overview.json", result)
        return result
