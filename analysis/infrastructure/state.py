"""Deterministic canonical infrastructure-state engine."""
from __future__ import annotations

import json
import os
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
from analysis.readiness import (
    aggregate_readiness, credentials_ready, evaluate_readiness)
from analysis.sites import SiteRegistry
from collectors.connector_registry import ConnectorMetadataRegistry


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
                 dashboard_dir="/app/runtime/dashboard", status_freshness_seconds=300,
                 sites_config="/app/config/sites.yml", sites_output="/app/runtime/sites",
                 readiness_config=None, platform_running=True,
                 registry_validation_mode="strict"):
        self.inventory_dir = Path(inventory_dir); self.operations_dir = Path(operations_dir)
        self.output_dir = Path(output_dir); self.dashboard_dir = Path(dashboard_dir)
        self.fusion = FusionEngine(status_freshness_seconds)
        self.site_registry = SiteRegistry.load(sites_config)
        self.sites_output = Path(sites_output)
        self.readiness_config = readiness_config or {}
        self.platform_running = bool(platform_running)
        self.registry_validation_mode = registry_validation_mode

    def _canonicalize_sites(self, assets):
        for asset in assets:
            site_id = asset.get("site_id"); definition = self.site_registry.definition(site_id)
            source_values = []
            for record in asset.get("source_records", []):
                value = record.pop("site_value", None)
                if value not in (None, ""):
                    candidate = {"source": record["source"], "value": value}
                    if candidate not in source_values: source_values.append(candidate)
            source_values.sort(key=lambda value: (value["source"], str(value["value"])))
            display = definition.display_name if definition else str(asset.get("site") or "")
            asset["site"] = {"site_id": site_id, "display_name": display,
                             "source_values": source_values}
        return assets

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
            site = asset.get("site") or {}
            site_id = site.get("site_id") if isinstance(site, dict) else None
            site_display = site.get("display_name") if isinstance(site, dict) else str(site)
            if not site_id:
                values.append(finding("missing_site", canonical, f"{asset_name(asset)} has no site.",
                    severity="Medium", actionable=True,
                    explanation="Site assignment is required for operational aggregation."))
            if not asset.get("management_ip"):
                required, explanation = management_ip_required(asset)
                values.append(finding("missing_management_ip", canonical,
                    f"{asset_name(asset)} has no management IP.",
                    severity="Medium" if required else "Info", actionable=required,
                    suppressed=not required, explanation=explanation))
            key = (short_hostname(asset.get("hostname")), normalize_site(site_id or site_display))
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
        results, used_aliases, unknown_sites = self.site_registry.resolve_records(results)
        assets, fusion_statistics, low_candidates = self.fusion.fuse(results)
        assets = self._canonicalize_sites(assets)
        findings = self._validate(assets, low_candidates)
        site_validation = self.site_registry.validation(used_aliases, unknown_sites)
        site_statistics = self.site_registry.statistics(used_aliases)
        collector_values = {}
        for result in sorted(results, key=lambda value: (-value.priority, value.name)):
            for value in result.collectors: collector_values.setdefault(value["collector"], value)
        collectors = [collector_values[key] for key in sorted(collector_values)]
        for collector in collectors:
            name = collector["collector"]
            site_ids = sorted({asset.get("site", {}).get("site_id")
                               for asset in assets
                               if name in asset.get("sources", [])
                               and asset.get("site", {}).get("site_id")})
            collector["site_ids"] = site_ids
            collector["site_names"] = [
                self.site_registry.definition(site_id).display_name
                for site_id in site_ids if self.site_registry.definition(site_id)]
        switch = _count(assets, lambda value: "switch" in asset_kind(value))
        aps = _count(assets, lambda value: "access-point" in asset_kind(value) or "wireless" in asset_kind(value))
        firewall = _count(assets, lambda value: "firewall" in asset_kind(value) or "security-appliance" in asset_kind(value))
        servers = _count(assets, lambda value: "server" in asset_kind(value))
        printers = _count(assets, lambda value: "print" in asset_kind(value))
        signals = _read(self.operations_dir / "signals.json", {})
        signals = dict(signals)
        wan_signals = list(signals.get("wan") or [])
        security_signals = list(signals.get("security") or [])
        for asset in assets:
            extensions = asset.get("extensions") or {}
            if not isinstance(extensions, dict):
                continue
            site = asset.get("site") or {}
            site_id = site.get("site_id") if isinstance(site, dict) else asset.get("site_id")
            site_name = site.get("display_name") if isinstance(site, dict) else site
            for value in extensions.get("wan_interfaces") or []:
                wan_signals.append({**value, "site_id": site_id, "site": site_name})
            certificate = extensions.get("device_certificate") or {}
            licenses = extensions.get("licenses") or []
            content = extensions.get("content_packages") or []
            if certificate or licenses or content:
                security_signals.append({
                    "device": asset.get("hostname") or asset.get("display_name"),
                    "site_id": site_id, "site": site_name,
                    "observed_at": asset.get("last_seen_at"),
                    "device_certificate": certificate,
                    "licenses": licenses, "content_packages": content,
                })
        if wan_signals:
            signals["wan"] = sorted(wan_signals, key=lambda value: (
                str(value.get("site_id", "")), str(value.get("role", "")),
                str(value.get("interface_name") or value.get("name", ""))))
        if security_signals:
            signals["security"] = sorted(security_signals, key=lambda value: (
                str(value.get("site_id", "")), str(value.get("device", ""))))
        reconciliations = _read(self.inventory_dir / "reconciliation.json",
                                {"reconciliations": []}).get("reconciliations", [])
        wan = signals.get("wan")
        wan_status = None if not wan else ("Offline" if any(value.get("available") is False for value in wan) else "Online")
        consumable_signals = signals.get("printer_consumables")
        consumables = None if consumable_signals is None else sum(
            value.get("percent_remaining") is not None and float(value["percent_remaining"]) <= 15
            for value in consumable_signals)
        operations = _read(self.operations_dir / "operations.json", {"issues": [], "risks": []})
        by_site_issues = Counter(value.get("site_id") for value in operations.get("issues", []) if value.get("site_id"))
        by_site_risks = Counter(value.get("site_id") for value in operations.get("risks", []) if value.get("site_id"))
        site_assets = defaultdict(list)
        for asset in assets: site_assets[str(asset.get("site", {}).get("site_id") or "site:unassigned")].append(asset)
        sites = []
        for site_id, values in sorted(site_assets.items()):
            states = Counter(state_of(value) for value in values)
            site_health = Counter(health_of(value) for value in values)
            overall = "Critical" if site_health["critical"] else \
                "Warning" if site_health["offline"] or site_health["warning"] else \
                "Healthy" if values else "Unknown"
            definition = self.site_registry.definition(site_id)
            display = definition.display_name if definition else "Unassigned"
            sites.append({"site_id": site_id, "display_name": display, "site": display,
                "devices": len(values), "online": states["online"],
                "offline": states["offline"],
                "infrastructure_health": overall,
                "collectors": sorted({source for value in values for source in value.get("sources", [])}),
                "issues": by_site_issues[site_id], "risks": by_site_risks[site_id]})
        states = Counter(state_of(value) for value in assets); health = Counter(health_of(value) for value in assets)
        actionable = [value for value in findings if value["actionable"] and not value["suppressed"]]
        informational = [value for value in findings if not value["actionable"] and not value["suppressed"]]
        suppressed = [value for value in findings if value["suppressed"]]
        infra_health = infrastructure_health(assets, actionable, wan_status)
        obs_health = observability_health(collectors, bool(assets))
        site_health = Counter(value["infrastructure_health"] for value in sites)
        summary = {"sites": len(sites), "devices": len(assets), "online": states["online"],
            "offline": states["offline"], "actionable_warnings": len(actionable),
            "data_quality_findings": len(informational), "suppressed_findings": len(suppressed),
            "infrastructure_health": infra_health, "observability_health": obs_health,
            "healthy_sites": site_health["Healthy"], "warning_sites": site_health["Warning"],
            "critical_sites": site_health["Critical"],
            "warnings": len(actionable), "critical": health["critical"],
            "collectors_healthy": sum(value["status"] == "healthy" for value in collectors),
            "collectors_failed": sum(value["status"] == "failed" for value in collectors)}
        configured_collectors = self.readiness_config.get("collectors") or {}
        enabled_collectors = sorted(
            name for name, value in configured_collectors.items()
            if isinstance(value, dict) and value.get("enabled") is True)
        if not self.readiness_config:
            enabled_collectors = sorted(
                value["collector"] for value in collectors
                if value.get("collector"))
        demo = str(self.readiness_config.get("deployment_id") or "").casefold() == "demo"
        registry = ConnectorMetadataRegistry.load(
            Path(__file__).resolve().parents[2],
            validation_mode=self.registry_validation_mode)
        readiness = evaluate_readiness(
            enabled_collectors=enabled_collectors,
            collector_records=collectors,
            capability_manifest=_read(
                self.inventory_dir.parent / "capabilities/collectors.json",
                {}),
            assets=assets,
            operations_generated=(
                self.operations_dir / "operations.json").is_file(),
            deployment_configured=bool(
                self.readiness_config.get("deployment_id")
                or not self.readiness_config),
            platform_running=self.platform_running,
            credentials_configured=credentials_ready(
                self.readiness_config, registry, os.environ)
                if self.readiness_config else bool(collectors),
            demo=demo, now=now,
            stale_seconds=self.fusion.freshness_seconds)
        if (assets and readiness["infrastructure"]["state"] == "healthy"
                and infra_health in {"Warning", "Critical"}):
            readiness["infrastructure"].update({
                "state": infra_health.casefold(),
                "reason": "infrastructure_degradation",
                "display_label": infra_health,
                "operator_action": "Review active infrastructure findings.",
            })
            readiness["overall"] = aggregate_readiness(
                readiness["observability"], readiness["infrastructure"])
        summary["infrastructure_health"] = \
            readiness["infrastructure"]["display_label"]
        summary["observability_health"] = \
            readiness["observability"]["display_label"]
        summary["readiness_state"] = readiness["overall"]["state"]
        return {"generated_at": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "deployment_id": os.getenv("ITP_DEPLOYMENT_ID", ""),
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
            "site_validation": site_validation, "site_registry_statistics": site_statistics,
            "fusion_statistics": fusion_statistics, "assets": assets,
            "reconciliations": reconciliations, "signals": signals,
            "readiness": readiness}

    def run(self, now=None):
        state = self.evaluate(now); write_state(self.output_dir, self.dashboard_dir, state)
        operations = _read(self.operations_dir / "operations.json", {"issues": [], "risks": [], "recommendations": []})
        self.site_registry.write(self.sites_output, self.dashboard_dir, state, operations,
                                 state.get("site_validation", []), state.get("site_registry_statistics", {}))
        return state
