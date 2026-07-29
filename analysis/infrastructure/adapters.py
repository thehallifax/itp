"""Automatically registered adapters for existing collector outputs."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .models import AdapterResult


def _read(path, fallback):
    try: return json.loads(Path(path).read_text())
    except FileNotFoundError: return fallback
    except json.JSONDecodeError as exc: raise ValueError(f"{Path(path).name} contains malformed JSON") from exc


def _duration(run):
    try:
        start = datetime.fromisoformat(str(run["started_at"]).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(run["completed_at"]).replace("Z", "+00:00"))
        return round((end - start).total_seconds() * 1000)
    except (KeyError, TypeError, ValueError):
        return None


class SignalAdapter:
    name = ""; priority = 0
    _registry = []
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.name: SignalAdapter._registry.append(cls)
    def __init__(self, inventory_dir): self.inventory_dir = Path(inventory_dir)
    @classmethod
    def registered(cls, inventory_dir):
        return [value(inventory_dir) for value in sorted(cls._registry, key=lambda item: item.name)]
    def collect(self): raise NotImplementedError


class InventoryAdapter(SignalAdapter):
    name = "inventory"; priority = 300
    def collect(self):
        assets = _read(self.inventory_dir / "assets.json", {"assets": []}).get("assets", [])
        return AdapterResult(self.name, self.priority, assets=[dict(value) for value in assets])


class CollectorAdapter(SignalAdapter):
    collector = ""; priority = 200
    def collect(self):
        assets = _read(self.inventory_dir / "assets.json", {"assets": []}).get("assets", [])
        selected = [dict(value) for value in assets
                    if str(value.get("collector") or value.get("source", "")).lower() == self.collector]
        sources = _read(self.inventory_dir / "source_runs.json", {"sources": {}}).get("sources", {})
        state = sources.get(self.collector)
        collectors = []
        if state:
            run = state.get("last_run", {}); successful = state.get("last_complete_successful_run", {})
            collectors.append({"collector": self.collector, "last_run": run.get("completed_at"),
                "duration_ms": _duration(run),
                "status": "healthy" if run.get("success") is True else "failed" if run.get("success") is False else "unknown",
                "failures": int(state.get("consecutive_failures", 0)),
                "last_successful_run": successful.get("completed_at")})
        return AdapterResult(self.name, self.priority, assets=selected, collectors=collectors)


class MistAdapter(CollectorAdapter):
    name = "mist"; collector = "mist"; priority = 200


class FortiGateAdapter(CollectorAdapter):
    name = "fortigate"; collector = "fortigate"; priority = 200


class PaloAltoAdapter(CollectorAdapter):
    name = "paloalto"; collector = "paloalto"; priority = 200


class PaperCutAdapter(CollectorAdapter):
    name = "papercut"; collector = "papercut"; priority = 200


class ArubaCentralAdapter(CollectorAdapter):
    name = "aruba"; collector = "aruba"; priority = 200


class SNMPAdapter(CollectorAdapter):
    name = "snmp"; collector = "snmp"; priority = 100


class VirtualisationAdapter(SignalAdapter):
    name = "virtualisation"; priority = 200

    def collect(self):
        payload = _read(self.inventory_dir.parent / "virtualisation/assets.json",
                        {"assets": []})
        assets = [dict(value) for value in payload.get("assets", [])]
        status = _read(self.inventory_dir.parent /
                       "virtualisation/collection-status.json", {"collections": []})
        values = status.get("collections", [])
        collectors = [] if not values else [{
            "collector": "virtualisation",
            "last_run": max((value.get("last_attempt") or "" for value in values), default=None),
            "last_successful_run": max(
                (value.get("last_success") or "" for value in values), default=None),
            "duration_ms": sum(int(value.get("duration_ms") or 0) for value in values),
            "status": "failed" if any(value.get("result") == "failed" for value in values)
                else "warning" if any(value.get("partial") for value in values)
                else "healthy",
            "failures": sum(value.get("result") == "failed" for value in values),
        }]
        return AdapterResult(self.name, self.priority, assets=assets,
                             collectors=collectors)
