"""Deterministic canonical infrastructure-state engine."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .adapters import SignalAdapter
from .models import asset_kind, asset_name, health_of, state_of
from .renderer import write_state


def _read(path, fallback):
    try: return json.loads(Path(path).read_text())
    except FileNotFoundError: return fallback
    except json.JSONDecodeError as exc: raise ValueError(f"{Path(path).name} contains malformed JSON") from exc


def _identity(asset):
    serial = str(asset.get("serial_number") or "").strip().upper()
    if serial: return "serial:" + serial
    hostname = str(asset.get("hostname") or asset.get("display_name") or "").strip().lower()
    if hostname: return "hostname:" + hostname
    management = str(asset.get("management_ip") or "").strip()
    if management: return "management-ip:" + management
    return "asset:" + str(asset.get("asset_id") or "unknown")


def _count(assets, predicate):
    selected = [value for value in assets if predicate(value)]
    states = Counter(state_of(value) for value in selected)
    health = Counter(health_of(value) for value in selected)
    return {"total": len(selected), "online": states["online"], "offline": states["offline"],
            "warning": health["warning"], "unknown": states["unknown"],
            "healthy": health["healthy"], "critical": health["critical"]}


class InfrastructureStateEngine:
    def __init__(self, inventory_dir="/app/runtime/inventory",
                 operations_dir="/app/runtime/operations",
                 output_dir="/app/runtime/infrastructure",
                 dashboard_dir="/app/runtime/dashboard"):
        self.inventory_dir = Path(inventory_dir); self.operations_dir = Path(operations_dir)
        self.output_dir = Path(output_dir); self.dashboard_dir = Path(dashboard_dir)

    def _merge(self, results):
        merged = {}; owners = {}; warnings = []; serials = defaultdict(list); hostnames = defaultdict(list)
        for result in sorted(results, key=lambda value: (-value.priority, value.name)):
            for record in sorted(result.assets, key=lambda value: (_identity(value), str(value.get("asset_id", "")))):
                value = dict(record); identity = _identity(value)
                serial = str(value.get("serial_number") or "").strip().upper()
                hostname = str(value.get("hostname") or "").strip().lower()
                if serial: serials[serial].append(str(value.get("asset_id") or identity))
                if hostname: hostnames[hostname].append(str(value.get("asset_id") or identity))
                if identity not in merged:
                    merged[identity] = value; owners[identity] = result.name
                    continue
                existing = merged[identity]
                if (existing.get("online") is not None and value.get("online") is not None and
                        existing.get("online") != value.get("online")):
                    warnings.append({"type": "conflicting_device_state", "identity": identity,
                        "message": f"Conflicting online state from {owners[identity]} and {result.name}."})
                for key, item in value.items():
                    if existing.get(key) in (None, "", []): existing[key] = item
        for serial, ids in sorted(serials.items()):
            unique = sorted(set(ids))
            if len(unique) > 1: warnings.append({"type": "duplicate_serial", "value": serial, "assets": unique})
        for hostname, ids in sorted(hostnames.items()):
            unique = sorted(set(ids))
            if len(unique) > 1: warnings.append({"type": "duplicate_hostname", "value": hostname, "assets": unique})
        assets = []
        for identity, value in sorted(merged.items()):
            value["identity"] = identity; value["state"] = state_of(value); value["health"] = health_of(value)
            if not value.get("site"):
                warnings.append({"type": "missing_site", "identity": identity,
                    "message": f"{asset_name(value)} has no site."})
            if not value.get("management_ip"):
                warnings.append({"type": "missing_management_ip", "identity": identity,
                    "message": f"{asset_name(value)} has no management IP."})
            assets.append(value)
        return assets, sorted(warnings, key=lambda value: (value["type"], value.get("identity", ""), value.get("value", "")))

    def evaluate(self, now=None):
        now = now or datetime.now(timezone.utc)
        results = []
        for adapter in SignalAdapter.registered(self.inventory_dir):
            try: results.append(adapter.collect())
            except FileNotFoundError: continue
        assets, warnings = self._merge(results)
        collector_values = {}
        for result in sorted(results, key=lambda value: (-value.priority, value.name)):
            for value in result.collectors: collector_values.setdefault(value["collector"], value)
        collectors = [collector_values[key] for key in sorted(collector_values)]
        switch = _count(assets, lambda value: "switch" in asset_kind(value))
        aps = _count(assets, lambda value: "access-point" in asset_kind(value) or "wireless" in asset_kind(value))
        firewall = _count(assets, lambda value: "firewall" in asset_kind(value) or "security-appliance" in asset_kind(value))
        servers = _count(assets, lambda value: "server" in asset_kind(value))
        printers = _count(assets, lambda value: "print" in asset_kind(value))
        signals = _read(self.operations_dir / "signals.json", {})
        reconciliations = _read(self.inventory_dir / "reconciliation.json",
                                {"reconciliations": []}).get("reconciliations", [])
        wan = signals.get("wan")
        wan_status = None if not wan else ("Offline" if any(value.get("available") is False for value in wan) else "Online")
        consumable_signals = signals.get("printer_consumables")
        consumables = None if consumable_signals is None else sum(
            value.get("percent_remaining") is not None and float(value["percent_remaining"]) <= 15
            for value in consumable_signals)
        operations = _read(self.operations_dir / "operations.json", {"issues": [], "risks": []})
        by_site_issues = Counter(value.get("site") for value in operations.get("issues", []) if value.get("site"))
        by_site_risks = Counter(value.get("site") for value in operations.get("risks", []) if value.get("site"))
        site_assets = defaultdict(list)
        for asset in assets: site_assets[str(asset.get("site") or "Unassigned")].append(asset)
        sites = []
        for site, values in sorted(site_assets.items()):
            states = Counter(state_of(value) for value in values)
            sites.append({"site": site, "devices": len(values), "online": states["online"],
                "offline": states["offline"],
                "collectors": sorted({str(value.get("collector") or value.get("source")) for value in values
                                      if value.get("collector") or value.get("source")}),
                "issues": by_site_issues[site], "risks": by_site_risks[site]})
        states = Counter(state_of(value) for value in assets); health = Counter(health_of(value) for value in assets)
        summary = {"sites": len(sites), "devices": len(assets), "online": states["online"],
            "offline": states["offline"], "warnings": health["warning"] + len(warnings),
            "critical": health["critical"],
            "collectors_healthy": sum(value["status"] == "healthy" for value in collectors),
            "collectors_failed": sum(value["status"] == "failed" for value in collectors)}
        return {"generated_at": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "sites": sites, "summary": summary,
            "network": {"switches": {key: switch[key] for key in ("total", "online", "offline", "warning", "unknown")}},
            "wireless": {"aps": {key: aps[key] for key in ("total", "online", "offline", "warning", "unknown")},
                         "clients_connected": signals.get("wireless", {}).get("clients_connected"),
                         "clients_failed_authentication": signals.get("wireless", {}).get("clients_failed_authentication")},
            "firewalls": {"firewalls": firewall["total"], "healthy": firewall["healthy"],
                          "warning": firewall["warning"], "critical": firewall["critical"],
                          "offline": firewall["offline"], "wan_status": wan_status},
            "servers": {"servers": servers["total"], "healthy": servers["healthy"],
                        "offline": servers["offline"], "unknown": servers["unknown"]},
            "printers": {"total": printers["total"], "healthy": printers["healthy"],
                         "warning": printers["warning"], "consumables": consumables,
                         "offline": printers["offline"]},
            "collectors": collectors, "warnings": warnings, "assets": assets,
            "reconciliations": reconciliations, "signals": signals}

    def run(self, now=None):
        state = self.evaluate(now); write_state(self.output_dir, self.dashboard_dir, state); return state
