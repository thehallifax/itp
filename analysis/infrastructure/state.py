"""Deterministic canonical infrastructure-state engine."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .adapters import SignalAdapter
from .fusion import FusionEngine
from .identity import normalize_site, short_hostname
from .models import asset_kind, asset_name, health_of, state_of
from .policy import (finding, infrastructure_health, management_ip_required,
                     observability_health)
from .renderer import write_state


def _read(path, fallback):
    try: return json.loads(Path(path).read_text())
    except FileNotFoundError: return fallback
    except json.JSONDecodeError as exc: raise ValueError(f"{Path(path).name} contains malformed JSON") from exc


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
                 dashboard_dir="/app/runtime/dashboard", status_freshness_seconds=300):
        self.inventory_dir = Path(inventory_dir); self.operations_dir = Path(operations_dir)
        self.output_dir = Path(output_dir); self.dashboard_dir = Path(dashboard_dir)
        self.fusion = FusionEngine(status_freshness_seconds)

    def _merge(self, results):
        assets, _, low = self.fusion.fuse(results)
        return assets, self._validate(assets, low)

    def _validate(self, assets, low_candidates):
        values = []
        for candidate in low_candidates:
            left = str(candidate["left"].get("asset_id") or "unknown")
            right = str(candidate["right"].get("asset_id") or "unknown")
            values.append(finding("low_confidence_identity", min(left, right),
                f"Possible duplicate retained separately: {min(left, right)} and {max(left, right)}.",
                severity="Low", actionable=True, explanation=candidate["reason"]))
        hostname_groups = defaultdict(list)
        for asset in assets:
            canonical = asset["canonical_id"]
            if not asset.get("site"):
                values.append(finding("missing_site", canonical, f"{asset_name(asset)} has no site.",
                    severity="Medium", actionable=True,
                    explanation="Site assignment is required for operational aggregation."))
            if not asset.get("management_ip"):
                required, explanation = management_ip_required(asset)
                values.append(finding("missing_management_ip", canonical,
                    f"{asset_name(asset)} has no management IP.",
                    severity="Medium" if required else "Info", actionable=required,
                    suppressed=not required, explanation=explanation))
            key = (short_hostname(asset.get("hostname")), normalize_site(asset.get("site")))
            if all(key): hostname_groups[key].append(asset)
            for conflict in asset.get("merge", {}).get("conflicts", []):
                actionable = conflict["severity"] in {"Critical", "High"}
                values.append(finding("identity_conflict", canonical,
                    f"{asset_name(asset)} has conflicting {conflict['field'].replace('_', ' ')} values.",
                    severity=conflict["severity"], actionable=actionable,
                    explanation=conflict["explanation"]))
        for (hostname, site), group in sorted(hostname_groups.items()):
            if len(group) > 1:
                for asset in group:
                    values.append(finding("duplicate_hostname", asset["canonical_id"],
                        f"Hostname {hostname} remains duplicated in site {site}.", severity="Medium",
                        actionable=True, explanation="Canonical identity evidence was insufficient for safe fusion."))
        deduplicated = {value["id"]: value for value in values}
        return sorted(deduplicated.values(), key=lambda value: (value["type"], value["canonical_id"], value["id"]))

    def evaluate(self, now=None):
        now = now or datetime.now(timezone.utc)
        results = []
        for adapter in SignalAdapter.registered(self.inventory_dir):
            try: results.append(adapter.collect())
            except FileNotFoundError: continue
        assets, fusion_statistics, low_candidates = self.fusion.fuse(results)
        findings = self._validate(assets, low_candidates)
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
                "collectors": sorted({source for value in values for source in value.get("sources", [])}),
                "issues": by_site_issues[site], "risks": by_site_risks[site]})
        states = Counter(state_of(value) for value in assets); health = Counter(health_of(value) for value in assets)
        actionable = [value for value in findings if value["actionable"] and not value["suppressed"]]
        informational = [value for value in findings if not value["actionable"] and not value["suppressed"]]
        suppressed = [value for value in findings if value["suppressed"]]
        infra_health = infrastructure_health(assets, actionable, wan_status)
        obs_health = observability_health(collectors, bool(assets))
        summary = {"sites": len(sites), "devices": len(assets), "online": states["online"],
            "offline": states["offline"], "actionable_warnings": len(actionable),
            "data_quality_findings": len(informational), "suppressed_findings": len(suppressed),
            "infrastructure_health": infra_health, "observability_health": obs_health,
            "warnings": len(actionable), "critical": health["critical"],
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
            "collectors": collectors, "warnings": findings, "validation_findings": findings,
            "fusion_statistics": fusion_statistics, "assets": assets,
            "reconciliations": reconciliations, "signals": signals}

    def run(self, now=None):
        state = self.evaluate(now); write_state(self.output_dir, self.dashboard_dir, state); return state
