"""Deterministic presentation model for the Operations Wallboard."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from analysis.infrastructure.models import asset_kind, health_of, state_of
from analysis.services.models import SERVICE_NAMES
from collectors.writer import atomic_write
from .renderer import write_wallboard


def _read(path, fallback):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return fallback
    except json.JSONDecodeError as exc:
        raise ValueError(f"{Path(path).name} contains malformed JSON") from exc


def _time(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _counts(assets, predicate):
    values = [asset for asset in assets if predicate(asset)]
    states = Counter(state_of(asset) for asset in values)
    health = Counter(health_of(asset) for asset in values)
    return {"total": len(values), "online": states["online"], "offline": states["offline"],
            "warning": health["warning"], "healthy": health["healthy"],
            "critical": health["critical"], "unknown": states["unknown"]}


def _site_id(asset):
    site = asset.get("site") or {}
    return site.get("site_id") if isinstance(site, dict) else asset.get("site_id")


def _scope_matches(value, scope):
    return scope == "all" or value.get("site_id") == scope


def _action_age(value):
    evidence = value.get("evidence") or {}
    seconds = evidence.get("age_seconds")
    if seconds is not None:
        seconds = max(0, int(seconds))
        return f"{seconds // 86400}d" if seconds >= 86400 else \
               f"{seconds // 3600}h" if seconds >= 3600 else f"{seconds // 60}m"
    return "Just now"


def _age_label(seconds):
    if seconds is None:
        return "Unavailable"
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "Just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def _action_rows(operations, scopes, enabled_collectors, capabilities=None):
    """Return immediate technician work, excluding governance recommendations."""
    candidates = []
    capabilities = capabilities or set()
    domains = {"Wireless": "wireless", "Printing": "printing", "Server": "compute",
               "Storage": "storage", "Firewall": "firewall", "Security": "firewall",
               "Network": "switching", "Virtualisation": "virtualisation"}
    services = {"Wireless": "Wireless", "Printing": "Printing", "Server": "Compute",
                "Storage": "Storage", "Firewall": "Security", "Security": "Security",
                "Collector": "Monitoring", "Inventory": "Monitoring",
                "Lifecycle": "Monitoring", "Virtualisation": "Virtualisation"}
    for value in operations.get("issues", []):
        required = domains.get(value.get("category"))
        if required and required not in capabilities: continue
        if value.get("category") == "Collector" and value.get("device") not in enabled_collectors:
            continue
        candidates.append(value)
    for value in operations.get("risks", []):
        required = domains.get(value.get("category"))
        if required and required not in capabilities: continue
        if (value.get("severity") not in {"Critical", "High"}
                and not (value.get("category") == "Virtualisation"
                         and value.get("severity") == "Medium")):
            continue
        if value.get("category") == "Collector" and value.get("device") not in enabled_collectors:
            continue
        candidates.append(value)
    # Issues win over risks describing the same rule/asset.
    unique = {}
    for value in sorted(candidates, key=lambda item: (
            0 if item.get("kind") == "issue" else 1, -int(item.get("priority", 0)),
            item.get("title", ""), item.get("id", ""))):
        key = (value.get("rule_id"), value.get("canonical_id") or value.get("device"))
        unique.setdefault(key, value)
    severity_rank = {"Critical": 5, "High": 4, "Medium": 3, "Low": 2,
                     "Unknown": 1, "Info": 0}
    ordered = sorted(unique.values(), key=lambda value: (
        -severity_rank.get(value.get("severity", "Info"), 0),
        -int((value.get("evidence") or {}).get("age_seconds", 0)),
        -int(value.get("priority", 0)),
        value.get("canonical_id") or value.get("device") or "",
        value.get("title", ""), value.get("id", "")))
    rows = []
    for scope in scopes:
        selected = [value for value in ordered if _scope_matches(value, scope["scope"])]
        scoped_rows = [{"scope": scope["scope"], "severity": value.get("severity", "Info"),
            "service": ("Internet" if str(value.get("rule_id", "")).startswith("wan.")
                        else "Switching" if value.get("category") == "Network"
                        else services.get(value.get("category"), value.get("category", "Infrastructure"))),
            "asset": value.get("device") or "Platform",
            "issue": value.get("summary") or value.get("title"),
            "age": _action_age(value), "priority": int(value.get("priority", 0)),
            "domain": value.get("domain") or value.get("category", "Infrastructure"),
            "provider": value.get("provider", ""),
            "object_kind": value.get("object_kind", ""),
            "id": value.get("id", "")} for value in selected[:8]]
        rows.extend(scoped_rows or [{"scope": scope["scope"], "severity": "Info",
            "service": "Operations", "asset": "", "issue": "No action required",
            "age": "", "priority": 0, "domain": "", "provider": "",
            "object_kind": "", "id": ""}])
    return rows


def _printer_exceptions(assets, operations, scopes):
    """Select only service-blocking printer conditions."""
    include = ("offline", "paper jam", "waste toner full", "toner empty",
               "staples empty", "service blocking", "service-blocking")
    exclude = ("paper tray empty", "low paper")
    values = []
    for asset in assets:
        if "print" not in asset_kind(asset): continue
        name = asset.get("display_name") or asset.get("hostname") or asset.get("canonical_id")
        common = {"asset": name, "location": asset.get("location") or "Unknown",
                  "site_id": _site_id(asset), "last_seen": asset.get("last_seen_at") or "Unknown"}
        if asset.get("online") is False:
            values.append({**common, "condition": "Offline"})
        for condition in (asset.get("extensions") or {}).get("printer_conditions", []):
            condition = condition if isinstance(condition, dict) else {"condition": str(condition)}
            label = str(condition.get("condition") or condition.get("name") or "")
            lowered = label.lower()
            percentage = condition.get("percent_remaining")
            actionable = condition.get("actionable") is True or any(token in lowered for token in include)
            if "toner" in lowered and percentage is not None and float(percentage) < 5:
                actionable = True; label = f"{label} ({float(percentage):g}% remaining)"
            if any(token in lowered for token in exclude): actionable = False
            if actionable:
                values.append({**common, "condition": label,
                    "last_seen": condition.get("observed_at") or common["last_seen"]})
    for value in operations.get("issues", []):
        if value.get("category") != "Printing": continue
        label = str(value.get("summary") or value.get("title") or "")
        lowered = label.lower()
        if any(token in lowered for token in exclude): continue
        if not any(token in lowered for token in include): continue
        values.append({"asset": value.get("device") or "Printer",
            "location": (value.get("evidence") or {}).get("location") or "Unknown",
            "condition": label, "last_seen": _action_age(value),
            "site_id": value.get("site_id")})
    rows = []
    deduplicated = {(value["asset"], value["condition"]): value for value in values}
    for scope in scopes:
        selected = [value for value in deduplicated.values()
                    if scope["scope"] == "all" or value.get("site_id") == scope["scope"]]
        if selected:
            rows.extend({"scope": scope["scope"], **value}
                        for value in sorted(selected, key=lambda item: (
                            item["asset"], item["condition"])))
        else:
            rows.append({"scope": scope["scope"], "asset": "No printer action required",
                "location": "", "condition": "", "last_seen": ""})
    return rows


def _wan_rows(signals, scopes, service_scopes):
    authoritative = [value for value in signals.get("wan", [])
        if value.get("classification_authoritative") is True
        or str(value.get("role", "")).lower() in {"primary", "secondary"}]
    rows = []; samples = []
    for scope in scopes:
        internet_service = next((value for value in
            service_scopes.get(scope["scope"], {}).get("services", [])
            if value.get("service") == "Internet"), {})
        selected = [value for value in authoritative
                    if scope["scope"] == "all" or value.get("site_id") == scope["scope"]]
        if not selected:
            rows.append({"scope": scope["scope"],
                "uplink": internet_service.get("summary") or "WAN classification unavailable",
                "role": "N/A", "state": internet_service.get("status", "Unknown"),
                "latency_ms": "N/A", "packet_loss_percent": "N/A"})
            continue
        for value in sorted(selected, key=lambda item: (
                str(item.get("role", "")), str(item.get("name") or item.get("interface_name", "")))):
            rows.append({"scope": scope["scope"],
                "uplink": value.get("name") or value.get("interface_name") or "WAN",
                "role": str(value.get("role") or "Unspecified").title(),
                "state": "Up" if value.get("available") is True else
                         "Down" if value.get("available") is False else "Unknown",
                "latency_ms": value.get("latency_ms", "N/A"),
                "packet_loss_percent": value.get("packet_loss_percent", "N/A")})
            for sample in value.get("samples", []):
                if sample.get("time") and (sample.get("rx_bps") is not None
                                           or sample.get("tx_bps") is not None):
                    samples.append({"scope": scope["scope"], "time": sample["time"],
                        "uplink": value.get("name") or value.get("interface_name") or "WAN",
                        "rx_bps": sample.get("rx_bps"), "tx_bps": sample.get("tx_bps")})
    return rows, sorted(samples, key=lambda value: (
        value["scope"], value["time"], value["uplink"]))


class WallboardEngine:
    def __init__(self, infrastructure_state="/app/runtime/infrastructure/state.json",
                 operations_state="/app/runtime/operations/operations.json",
                 sites_state="/app/runtime/sites/sites.json",
                 dashboard_template="/app/dashboards/Operations/operations-wallboard.json",
                 summary_output="/app/runtime/dashboard/wallboard-summary.json",
                 dashboard_output="/app/runtime/dashboard/operations/operations-wallboard.json",
                 capability_registry="/app/runtime/dashboard/managed/registry.json",
                 service_health="/app/runtime/services/service-health.json",
                 freshness_seconds=900):
        self.infrastructure_state = Path(infrastructure_state)
        self.operations_state = Path(operations_state)
        self.sites_state = Path(sites_state)
        self.dashboard_template = Path(dashboard_template)
        self.summary_output = Path(summary_output)
        self.dashboard_output = Path(dashboard_output)
        self.capability_registry = Path(capability_registry)
        self.service_health = Path(service_health)
        self.freshness_seconds = max(1, int(freshness_seconds))

    def evaluate(self, now=None):
        now = now or datetime.now(timezone.utc)
        state = _read(self.infrastructure_state, {"assets": [], "sites": [], "summary": {},
            "collectors": [], "signals": {}})
        operations = _read(self.operations_state, {"issues": [], "risks": [], "recommendations": []})
        site_state = _read(self.sites_state, {"sites": [], "generated_at": None})
        capability_state = _read(self.capability_registry, {"capabilities": [
            "firewall", "internet", "wireless", "switching", "printing", "compute"]})
        service_state = _read(self.service_health, {"generated_at": None, "services": [
            ]})
        capabilities = set(capability_state.get("capabilities", []))
        enabled_collectors = set(capability_state.get("enabled_collectors", []))
        dashboard_uids = sorted(value.get("uid") for value in
            capability_state.get("dashboards", []) if value.get("uid"))
        site_options = [{"site_id": value["site_id"], "display_name": value["display_name"]}
                        for value in sorted(site_state.get("sites", []),
                                           key=lambda value: value["site_id"])]
        scopes = [{"scope": "all", "display_name": "All Sites", "assets": state.get("assets", [])}]
        for site in site_options:
            scopes.append({"scope": site["site_id"], "display_name": site["display_name"],
                "assets": [asset for asset in state.get("assets", [])
                           if _site_id(asset) == site["site_id"]]})
        service_scopes = {"all": service_state.get("estate", {
            "site_id": "all", "site_name": "All Sites", "overall_status": "Unknown",
            "services": []})}
        service_scopes.update({value["site_id"]: value
                               for value in service_state.get("sites", [])
                               if value.get("site_id")})
        for scope in scopes:
            service_scopes.setdefault(scope["scope"], {
                "site_id": scope["scope"], "site_name": scope["display_name"],
                "overall_status": "Unknown", "services": []})
            values = {value.get("service"): value for value in
                      service_scopes[scope["scope"]].get("services", [])}
            for name in SERVICE_NAMES:
                values.setdefault(name, {"service": name, "status": "Unknown",
                    "severity": "Info",
                    "summary": f"No canonical {name.lower()} state exists for this site.",
                    "affected_assets": [], "affected_users": None,
                    "last_change": None, "evidence": []})
            names = list(SERVICE_NAMES) + sorted(
                name for name in values if name and name not in SERVICE_NAMES)
            service_scopes[scope["scope"]]["services"] = [values[name] for name in names]
        scope_values = []
        topology = []; topology_edges = []
        for scope in scopes:
            assets = scope["assets"]; states = Counter(state_of(asset) for asset in assets)
            scoped_services = {value["service"]: value for value in
                               service_scopes[scope["scope"]]["services"]}
            enabled = lambda name: scoped_services[name]["status"] != "Not Enabled"
            switch = _counts(assets, lambda asset: "switch" in asset_kind(asset))
            wireless = _counts(assets, lambda asset: "access-point" in asset_kind(asset)
                               or "wireless" in asset_kind(asset))
            firewall = _counts(assets, lambda asset: "firewall" in asset_kind(asset)
                               or "security-appliance" in asset_kind(asset))
            servers = _counts(assets, lambda asset: "server" in asset_kind(asset))
            printers = _counts(assets, lambda asset: "print" in asset_kind(asset))
            critical = [asset for asset in assets if health_of(asset) == "critical"]
            warning = [asset for asset in assets if health_of(asset) in {"warning", "offline"}]
            infra = "Critical" if critical else "Warning" if warning else "Healthy" if assets else "Unknown"
            summary = state.get("summary", {})
            if scope["scope"] == "all": infra = summary.get("infrastructure_health", infra)
            scope_values.append({"scope": scope["scope"], "display_name": scope["display_name"],
                "sites": len(site_options) if scope["scope"] == "all" else 1,
                "devices": len(assets), "online": states["online"], "offline": states["offline"],
                "actionable_warnings": summary.get("actionable_warnings", 0) if scope["scope"] == "all" else 0,
                "active_issues": sum(value.get("site_id") == scope["scope"] for value in operations.get("issues", []))
                    if scope["scope"] != "all" else len(operations.get("issues", [])),
                "collectors_healthy": summary.get("collectors_healthy", 0),
                "collectors_total": summary.get("collectors_healthy", 0) + summary.get("collectors_failed", 0),
                "infrastructure_health": infra,
                "observability_health": summary.get("observability_health", "Unknown"),
                "domains": {"network": {**switch, "available": enabled("Switching")},
                    "wireless": {**wireless, "available": enabled("Wireless"),
                    "clients_connected": state.get("wireless", {}).get("clients_connected"),
                    "clients_failed_authentication": state.get("wireless", {}).get("clients_failed_authentication")},
                    "security": {**firewall, "available": enabled("Security"),
                        "wan_status": state.get("firewalls", {}).get("wan_status")},
                    "compute": {**servers, "available": enabled("Compute")},
                    "printing": {**printers, "available": enabled("Printing"),
                        "consumables": state.get("printers", {}).get("consumables")}}})
            groups = (
                ("Internet / WAN", [], state.get("firewalls", {}).get("wan_status"),
                    "internet" in capabilities),
                ("Firewalls / Edge", [asset for asset in assets if "firewall" in asset_kind(asset)], None,
                    "firewall" in capabilities),
                ("Core", [asset for asset in assets if "core" in asset_kind(asset)], None),
                ("Distribution", [asset for asset in assets if "distribution" in asset_kind(asset)], None),
                ("Access Switching", [asset for asset in assets if "switch" in asset_kind(asset)
                    and "core" not in asset_kind(asset) and "distribution" not in asset_kind(asset)], None),
                ("Wireless", [asset for asset in assets if "access-point" in asset_kind(asset)
                    or "wireless" in asset_kind(asset)], None, "wireless" in capabilities),
                ("Servers", [asset for asset in assets if "server" in asset_kind(asset)], None,
                    "compute" in capabilities),
                ("Printers", [asset for asset in assets if "print" in asset_kind(asset)], None,
                    "printing" in capabilities),
            )
            node_ids = ("internet", "edge", "core", "distribution", "access",
                        "wireless", "servers", "printers")
            normalized_groups = [value if len(value) == 4 else (*value, "switching" in capabilities)
                                 for value in groups]
            for order, (label, members, explicit, enabled) in enumerate(normalized_groups, 1):
                member_health = Counter(health_of(asset) for asset in members)
                status = "Not enabled" if not enabled else explicit or ("Critical" if member_health["critical"] else
                    "Warning" if member_health["warning"] or member_health["offline"] else
                    "Healthy" if members else "Awaiting telemetry")
                color = {"Healthy": "green", "Warning": "orange", "Critical": "red",
                         "Online": "green", "Offline": "red"}.get(status, "gray")
                topology.append({"scope": scope["scope"], "id": node_ids[order - 1],
                    "title": label, "subTitle": "Logical aggregate",
                    "mainStat": str(len(members)) if members else "N/A",
                    "secondaryStat": status, "color": color})
            topology_edges.extend({"scope": scope["scope"], "id": f"logical-{index}-{index + 1}",
                "source": node_ids[index - 1], "target": node_ids[index]}
                for index in range(1, len(node_ids)))
        actions = _action_rows(operations, scopes, enabled_collectors, capabilities)
        printer_exceptions = _printer_exceptions(state.get("assets", []), operations, scopes)
        printing_by_scope = {scope["scope"]: next(value for value in
            service_scopes[scope["scope"]]["services"] if value["service"] == "Printing")
            for scope in scopes}
        printer_exceptions = [value for value in printer_exceptions
            if printing_by_scope[value["scope"]]["status"] != "Not Enabled"]
        printer_exceptions.extend({"scope": scope["scope"],
            "asset": "Printing not enabled", "location": "",
            "condition": "", "last_seen": ""} for scope in scopes
            if printing_by_scope[scope["scope"]]["status"] == "Not Enabled")
        wan_uplinks, wan_samples = _wan_rows(
            state.get("signals", {}), scopes, service_scopes)
        generated_at = _time(service_state.get("generated_at"))
        age = max(0, int((now - generated_at).total_seconds())) if generated_at else None
        freshness = "Unknown" if age is None else "Fresh" if age <= self.freshness_seconds else "Stale"
        scope_health = {scope["scope"]:
                        service_scopes[scope["scope"]].get("overall_status", "Unknown")
                        for scope in scopes}
        collector_rows = []
        generated = now.astimezone(timezone.utc)
        site_names = {value["site_id"]: value["display_name"] for value in site_options}
        for value in state.get("collectors", []):
            name = value.get("collector")
            if enabled_collectors and name not in enabled_collectors: continue
            last = value.get("last_run") or value.get("last_successful_run")
            observed = _time(last)
            collector_age = int((generated - observed).total_seconds()) if observed else None
            status = "Failed" if value.get("status") == "failed" else \
                     "Stale" if collector_age is None or collector_age > self.freshness_seconds else \
                     "Healthy" if (value.get("status") == "healthy"
                         or value.get("last_successful_run") == last) else "Warning"
            attributed = [site_id for site_id in value.get("site_ids", [])
                          if site_id in site_names]
            if value.get("shared") is True:
                attributed = sorted(site_names)
            if not attributed:
                collector_rows.append({"scope": "all", "collector": name,
                    "site": "Unattributed", "status": status,
                    "freshness": _age_label(collector_age), "last_run": last})
            for site_id in attributed:
                row = {"collector": name, "site": site_names[site_id],
                       "status": status, "freshness": _age_label(collector_age),
                       "last_run": last}
                collector_rows.append({"scope": "all", **row})
                collector_rows.append({"scope": site_id, **row})
        site_matrix = []
        matrix_services = ("Internet", "Wireless", "Switching", "Security", "Monitoring")
        for site in site_options:
            scoped = service_scopes[site["site_id"]]
            services = {value["service"]: value["status"] for value in scoped["services"]}
            site_matrix.append({"site_id": site["site_id"], "site": site["display_name"],
                "overall": scoped.get("overall_status", "Unknown"),
                **{name.casefold(): services.get(name, "Unknown") for name in matrix_services}})
        site_counts = Counter(value["overall"] for value in site_matrix)
        return {"generated_at": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "deployment_id": state.get("deployment_id", ""),
            "selected_scope": "all", "site_options": site_options, "scopes": scope_values,
            "capabilities": sorted(capabilities),
            "enabled_collectors": sorted(enabled_collectors),
            "dashboard_uids": dashboard_uids,
            "overall_health": scope_health,
            "service_scopes": service_scopes,
            "service_health": service_scopes["all"]["services"],
            "topology": {"type": "logical_aggregate", "nodes": topology, "edges": topology_edges},
            "actions": actions, "printer_exceptions": printer_exceptions,
            "collectors": sorted(collector_rows, key=lambda value: (
                value["scope"], value["collector"], value["site"])),
            "freshness": {"source": "runtime/services/service-health.json",
                "service_health_generated_at": (
                    generated_at.isoformat().replace("+00:00", "Z") if generated_at else None),
                "last_successful_refresh": (
                    generated_at.isoformat().replace("+00:00", "Z") if generated_at else None),
                "age_seconds": age, "age_display": _age_label(age),
                "status": freshness,
                "threshold_seconds": self.freshness_seconds},
            "services": {name: None for name in ("DNS", "DHCP", "Active Directory", "PaperCut", "Certificates")},
            "wan": {"uplinks": wan_uplinks, "samples": wan_samples,
                    "latency_ms": None, "packet_loss_percent": None},
            "estate": {"enabled": len(site_options) > 1,
                "site_counts": {name.casefold(): site_counts[name] for name in
                                ("Healthy", "Warning", "Critical", "Unknown")},
                "sites_requiring_attention": sum(site_counts[name] for name in
                                                 ("Warning", "Critical")),
                "site_status_matrix": site_matrix,
                "high_impact_findings": [value for value in actions
                                         if value.get("scope") == "all"][:10]}}

    def run(self, now=None):
        value = self.evaluate(now)
        write_wallboard(value, self.dashboard_template, self.summary_output, self.dashboard_output)
        estate_path = self.operations_state.parent / "estate-state.json"
        atomic_write(estate_path, json.dumps({
            "schema_version": 1, "generated_at": value["generated_at"],
            "deployment_id": value.get("deployment_id", ""),
            **value["estate"],
        }, indent=2, sort_keys=True) + "\n")
        return value
