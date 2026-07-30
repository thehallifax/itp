"""Deterministic presentation model for the Operations Wallboard."""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from analysis.infrastructure.models import asset_kind, health_of, state_of
from analysis.readiness import READINESS_PRECEDENCE, evaluate_readiness
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


def _action_text(value):
    """Turn a deterministic finding into a concise operator action."""
    summary = str(value.get("summary") or value.get("title") or "").strip()
    title = str(value.get("title") or "").strip()
    device = str(value.get("device") or "").strip()
    evidence = value.get("evidence") or {}
    combined = f"{title} {summary}".casefold()
    if "certificate" in combined or "days remaining" in combined:
        name = device or re.sub(
            r"(?i)certificate (expiry|expires?)[: ]*", "", title).strip()
        when = "today" if re.search(
            r"\b0 days? remaining\b|\bexpires? today\b", combined) else "now"
        return f"Renew {name or 'the affected'} certificate {when}"
    if "embedded device" in combined or (
            value.get("category") == "Printing" and "error" in combined):
        count = evidence.get("error_count") or evidence.get("device_count") \
            or len(value.get("affected_assets") or [])
        return (
            f"{count} printers require attention" if count
            else "Printers require attention")
    if value.get("category") == "Collector":
        return f"Restore monitoring for {device or 'the affected service'}"
    if str(value.get("rule_id") or "").startswith("wan."):
        return f"Restore {device or 'the affected WAN'}"
    recommended = str(value.get("recommended_action") or "").strip()
    return recommended or summary or title or "Review the affected service"


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


def _action_rows(operations, scopes, enabled_collectors, capabilities=None,
                 readiness=None):
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
            "issue": _action_text(value),
            "age": _action_age(value), "priority": int(value.get("priority", 0)),
            "domain": value.get("domain") or value.get("category", "Infrastructure"),
            "provider": value.get("provider", ""),
            "object_kind": value.get("object_kind", ""),
            "id": value.get("id", "")} for value in selected[:8]]
        empty = {
            "not_configured": "Monitoring not configured",
            "waiting_first_collection": "Waiting for first collection",
            "unavailable": "Collection unavailable — run Doctor",
        }.get((readiness or {}).get("overall", {}).get("state"),
              "No action required")
        rows.extend(scoped_rows or [{"scope": scope["scope"], "severity": "Info",
            "service": "Operations", "asset": "", "issue": empty,
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
            rows.append({"scope": scope["scope"], "asset": "No printers require attention",
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
                "interface": "", "label": "",
                "uplink": internet_service.get("summary") or "No WAN Telemetry",
                "role": "N/A", "state": "No WAN Telemetry",
                "latency_ms": "N/A", "packet_loss_percent": "N/A"})
            continue
        for value in sorted(selected, key=lambda item: (
                str(item.get("role", "")), str(item.get("name") or item.get("interface_name", "")))):
            interface = value.get("name") or value.get("interface_name") or "WAN"
            label = value.get("display_name") or value.get("friendly_label") or \
                str(value.get("role") or "WAN").title()
            rows.append({"scope": scope["scope"],
                "interface": interface, "label": label, "uplink": interface,
                "role": str(value.get("role") or "Unspecified").title(),
                "state": "Up" if value.get("available") is True else
                         "Down" if value.get("available") is False else "Unknown",
                "latency_ms": value.get("latency_ms", "N/A"),
                "packet_loss_percent": value.get("packet_loss_percent", "N/A")})
            for sample in value.get("samples", []):
                if sample.get("time") and (sample.get("rx_bps") is not None
                                           or sample.get("tx_bps") is not None):
                    samples.append({"scope": scope["scope"], "time": sample["time"],
                        "interface": interface, "label": label,
                        "uplink": interface,
                        "rx_bps": sample.get("rx_bps"), "tx_bps": sample.get("tx_bps")})
    return rows, sorted(samples, key=lambda value: (
        value["scope"], value["time"], value["uplink"]))


def _internet_rows(wan_rows, scopes, capability_enabled):
    rows = []
    for scope in scopes:
        selected = [value for value in wan_rows
                    if value["scope"] == scope["scope"]
                    and value.get("interface")]
        if not capability_enabled:
            label, color = "Not Enabled", "gray"
        elif not selected:
            label, color = "WAN Role Not Configured", "gray"
        else:
            down = [value for value in selected if value["state"] == "Down"]
            unknown = [value for value in selected if value["state"] == "Unknown"]
            healthy = len(selected) - len(down) - len(unknown)
            if down:
                label = f"{healthy} / {len(selected)} WANs Healthy"
                color = "red" if len(down) == len(selected) else "orange"
            elif unknown:
                label, color = "Not Yet Collected", "gray"
            else:
                label, color = (
                    f"{len(selected)} / {len(selected)} WANs Healthy", "green")
        rows.append({"scope": scope["scope"], "value": label, "color": color})
    return rows


def _monitoring_rows(collector_rows, scopes):
    rows = []
    for scope in scopes:
        selected = [value for value in collector_rows
                    if value["scope"] == scope["scope"]]
        by_collector = {}
        rank = {"Failed": 3, "Stale": 2, "Warning": 1, "Healthy": 0}
        for value in selected:
            if not value.get("collector") or not value.get("last_run"):
                continue
            current = by_collector.get(value["collector"])
            if current is None or rank.get(value.get("status"), 0) > \
                    rank.get(current.get("status"), 0):
                by_collector[value["collector"]] = value
        real = list(by_collector.values())
        issues = [value for value in real
                  if value.get("status") != "Healthy"]
        stale_services = sorted({
            service for value in issues
            for service in (value.get("services") or ["Monitoring"])})
        successes = sorted(
            (value.get("last_successful_run") or value.get("last_run")
             for value in real if value.get("status") == "Healthy"),
            reverse=True)
        if not real:
            label, color = "Collector Disabled", "gray"
        elif issues:
            label = (
                f"{len(issues)} collector requires attention"
                if len(issues) == 1
                else f"{len(issues)} collectors require attention")
            color = "orange"
        else:
            label, color = "Healthy", "green"
        detail = "; ".join(
            f"{value['collector']} {str(value.get('status', '')).casefold()} "
            f"({value.get('freshness') or 'never'})"
            for value in sorted(issues, key=lambda item: item["collector"]))
        rows.append({
            "scope": scope["scope"], "value": label, "color": color,
            "display": f"{label}\n{detail}" if detail else label,
            "collectors_with_issues": len(issues),
            "last_successful_collection": successes[0] if successes else None,
            "stale_services": stale_services,
        })
    return rows


def _certificate_rows(operations, scopes, enabled):
    candidates = [
        value for value in (
            list(operations.get("issues", []))
            + list(operations.get("risks", [])))
        if (
            "certificate" in (
            f"{value.get('rule_id', '')} {value.get('title', '')} "
            f"{value.get('summary', '')}").casefold()
            or "licen" in (
                f"{value.get('rule_id', '')} {value.get('title', '')}").casefold()
            or any(key in (value.get("evidence") or {})
                   for key in ("days_remaining", "expired")))]
    rows = []
    for scope in scopes:
        selected = [value for value in candidates
                    if _scope_matches(value, scope["scope"])]
        if not enabled:
            label, color = "Not Enabled", "gray"
        elif selected:
            noun = "Certificate" if len(selected) == 1 else "Certificates"
            label = f"{len(selected)} {noun}\nRequire Attention"
            color = "orange"
        else:
            label, color = "Certificates Healthy", "green"
        rows.append({
            "scope": scope["scope"], "value": label, "color": color})
    return rows


def _service_card_rows(service_scopes, scopes, service):
    rows = []
    for scope in scopes:
        value = next(item for item in
                     service_scopes[scope["scope"]]["services"]
                     if item["service"] == service)
        summary = str(value.get("summary") or "").strip()
        status = value["status"]
        rows.append({
            "scope": scope["scope"], "value": status,
            "display": f"{status}\n{summary}" if summary else status,
            "summary": summary,
        })
    return rows


def _firewall_rows(service_scopes, scopes, certificate_rows):
    """Present the canonical Security state with its highest useful cue."""
    certificates = {value["scope"]: value for value in certificate_rows}
    rows = []
    for scope in scopes:
        service = next(item for item in
                       service_scopes[scope["scope"]]["services"]
                       if item["service"] == "Security")
        status = service["status"]
        certificate = certificates.get(scope["scope"], {})
        certificate_label = str(certificate.get("value") or "")
        if status in {"Healthy", "Not Enabled"}:
            label = status
        elif "require attention" in certificate_label.casefold():
            count = certificate_label.split(" ", 1)[0]
            noun = "Certificate" if count == "1" else "Certificates"
            label = f"{count} {noun}\nRequire Attention"
        elif status == "Unknown":
            label = "Not Yet Collected"
        else:
            label = status
        rows.append({
            "scope": scope["scope"],
            "value": label,
            "status": status,
            "summary": str(service.get("summary") or "").strip(),
        })
    return rows


def _overall_rows(scope_health, service_scopes, scopes):
    rank = {"Critical": 4, "Warning": 3, "Unknown": 2,
            "Healthy": 1, "Not Enabled": 0}
    rows = []
    for scope in scopes:
        status = scope_health[scope["scope"]]
        services = service_scopes[scope["scope"]]["services"]
        matching = sorted(
            (value for value in services if value.get("status") == status),
            key=lambda value: (
                -rank.get(value.get("status"), 0),
                str(value.get("service", ""))))
        context = str(matching[0].get("summary") or "").strip() if matching else ""
        rows.append({
            "scope": scope["scope"], "value": status,
            "display": f"{status}\n{context}" if context else status,
            "context": context,
        })
    return rows


def _recent_changes(path, service_scopes, scopes, now):
    cutoff = now.timestamp() - 86400
    values = []
    for candidate in sorted(Path(path).glob("*.json")):
        payload = _read(candidate, {})
        observed = _time(payload.get("observed_at")
                         or payload.get("generated_at"))
        if not observed or observed.timestamp() < cutoff:
            continue
        for change in payload.get("changes", []):
            site_id = change.get("site_id") or "all"
            current = str(change.get("current_value") or "").casefold()
            previous = str(change.get("previous_value") or "").casefold()
            asset = change.get("entity_id") or "Infrastructure"
            field = str(change.get("field_path") or "")
            if current in {"up", "online", "healthy", "true"} and \
                    previous in {"down", "offline", "failed", "false"}:
                label = (
                    f"{asset} WAN restored" if "wan" in field
                    else f"{asset} recovered")
            elif current in {"stale", "failed", "offline", "down", "false"}:
                label = f"{asset} changed to {current}"
            else:
                continue
            values.append({
                "site_id": site_id, "time": observed, "change": label,
                "service": str(change.get("domain") or "Infrastructure").title(),
            })
    for scope in scopes:
        services = service_scopes[scope["scope"]]["services"]
        for service in services:
            changed = _time(service.get("last_change"))
            if not changed or changed.timestamp() < cutoff:
                continue
            values.append({
                "site_id": scope["scope"], "time": changed,
                "service": service["service"],
                "change": service.get("summary")
                or f"{service['service']} changed to {service['status']}",
            })
    rows = []
    ordered = sorted(values, key=lambda value: (
        -value["time"].timestamp(), value["service"], value["change"]))
    for scope in scopes:
        selected = [value for value in ordered
                    if scope["scope"] == "all"
                    or value["site_id"] in {"all", scope["scope"]}]
        rows.extend({
            "scope": scope["scope"],
            "time": value["time"].isoformat().replace("+00:00", "Z"),
            "service": value["service"], "change": value["change"],
        } for value in selected[:8])
        if not selected:
            rows.append({
                "scope": scope["scope"], "time": "",
                "service": "Operations",
                "change": "No operational changes in the last 24 hours",
            })
    return rows


class WallboardEngine:
    def __init__(self, infrastructure_state="/app/runtime/infrastructure/state.json",
                 operations_state="/app/runtime/operations/operations.json",
                 sites_state="/app/runtime/sites/sites.json",
                 dashboard_template="/app/dashboards/Operations/operations-wallboard.json",
                 summary_output="/app/runtime/dashboard/wallboard-summary.json",
                 dashboard_output="/app/runtime/dashboard/operations/operations-wallboard.json",
                 capability_registry="/app/runtime/dashboard/managed/registry.json",
                 service_health="/app/runtime/services/service-health.json",
                 readiness_state="/app/runtime/dashboard/readiness.json",
                 freshness_seconds=900):
        self.infrastructure_state = Path(infrastructure_state)
        self.operations_state = Path(operations_state)
        self.sites_state = Path(sites_state)
        self.dashboard_template = Path(dashboard_template)
        self.summary_output = Path(summary_output)
        self.dashboard_output = Path(dashboard_output)
        self.capability_registry = Path(capability_registry)
        self.service_health = Path(service_health)
        self.readiness_state = Path(readiness_state)
        self.freshness_seconds = max(1, int(freshness_seconds))

    def evaluate(self, now=None):
        now = now or datetime.now(timezone.utc)
        state = _read(self.infrastructure_state, {"assets": [], "sites": [], "summary": {},
            "collectors": [], "signals": {}})
        operations = _read(self.operations_state, {"issues": [], "risks": [], "recommendations": []})
        site_state = _read(self.sites_state, {"sites": [], "generated_at": None})
        capability_state = _read(
            self.capability_registry,
            {"capabilities": [], "enabled_collectors": []})
        service_state = _read(self.service_health, {"generated_at": None, "services": [
            ]})
        capabilities = set(capability_state.get("capabilities", []))
        enabled_collectors = set(capability_state.get("enabled_collectors", []))
        readiness = _read(self.readiness_state, {}) or \
            state.get("readiness") or evaluate_readiness(
            enabled_collectors=enabled_collectors,
            collector_records=state.get("collectors", []),
            capabilities=capabilities,
            assets=state.get("assets", []),
            operations_generated=bool(operations.get("generated_at")),
            deployment_configured=bool(state.get("deployment_id")),
            platform_running=True, now=now,
            credentials_configured=bool(enabled_collectors),
            stale_seconds=self.freshness_seconds)
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
            infra = "Critical" if critical else "Warning" if warning else \
                "Healthy" if assets else \
                readiness["infrastructure"]["display_label"]
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
                "observability_health": summary.get(
                    "observability_health",
                    readiness["observability"]["display_label"]),
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
        actions = _action_rows(
            operations, scopes, enabled_collectors, capabilities, readiness)
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
        readiness_rank = READINESS_PRECEDENCE[
            readiness["overall"]["state"]]
        operational_rank = {"Healthy": 0, "Warning": 3, "Critical": 5}
        scope_health = {
            scope: (
                readiness["overall"]["display_label"]
                if readiness_rank > operational_rank.get(status, 0)
                else status)
            for scope, status in scope_health.items()}
        collector_rows = []
        generated = now.astimezone(timezone.utc)
        site_names = {value["site_id"]: value["display_name"] for value in site_options}
        collector_capabilities = capability_state.get(
            "collector_capabilities") or {}
        for value in state.get("collectors", []):
            name = value.get("collector")
            if enabled_collectors and name not in enabled_collectors: continue
            last = value.get("last_run") or value.get("last_successful_run")
            observed = _time(last)
            collector_age = int((generated - observed).total_seconds()) if observed else None
            # Infrastructure state owns collector health. Reapplying one
            # wallboard-wide age threshold here incorrectly turns healthy
            # collectors with longer schedules into additional incidents.
            status = {
                "failed": "Failed", "stale": "Stale", "warning": "Warning",
                "healthy": "Healthy",
            }.get(str(value.get("status") or "").casefold(), "Warning")
            service_labels = {
                "internet": "Internet", "firewall": "Firewall",
                "printing": "Printing", "wireless": "Wireless",
                "switching": "Switching", "compute": "Compute",
                "identity": "Identity", "telemetry": "Monitoring",
            }
            services = sorted({
                service_labels.get(str(item).casefold(),
                                   str(item).replace("_", " ").title())
                for item in (
                    value.get("services") or value.get("capabilities")
                    or value.get("domains")
                    or collector_capabilities.get(name, []) or [])
                if item})
            attributed = [site_id for site_id in value.get("site_ids", [])
                          if site_id in site_names]
            if value.get("shared") is True:
                attributed = sorted(site_names)
            if not attributed:
                collector_rows.append({"scope": "all", "collector": name,
                    "site": "Unattributed", "status": status,
                    "freshness": _age_label(collector_age), "last_run": last,
                    "last_successful_run": value.get(
                        "last_successful_run"),
                    "services": services})
            for site_id in attributed:
                row = {"collector": name, "site": site_names[site_id],
                       "status": status, "freshness": _age_label(collector_age),
                       "last_run": last,
                       "last_successful_run": value.get(
                           "last_successful_run"),
                       "services": services}
                collector_rows.append({"scope": "all", **row})
                collector_rows.append({"scope": site_id, **row})
        if not collector_rows:
            collector_rows = [{
                "scope": scope["scope"],
                "collector": readiness["observability"]["display_label"],
                "site": "",
                "status": readiness["observability"]["display_label"],
                "freshness": readiness["observability"]["operator_action"],
                "last_run": None, "last_successful_run": None,
                "services": [],
            } for scope in scopes]
        monitoring = _monitoring_rows(collector_rows, scopes)
        internet = _internet_rows(
            wan_uplinks, scopes, "internet" in capabilities)
        certificate_rows = _certificate_rows(
            operations, scopes, "firewall" in capabilities)
        security_rows = _service_card_rows(
            service_scopes, scopes, "Security")
        firewall_rows = _firewall_rows(
            service_scopes, scopes, certificate_rows)
        overall_rows = _overall_rows(
            scope_health, service_scopes, scopes)
        runtime_root = self.operations_state.parent.parent \
            if self.operations_state.parent.name == "operations" \
            else self.operations_state.parent
        changes = _recent_changes(
            runtime_root / "state-history/changes",
            service_scopes, scopes, now)
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
            "readiness": readiness,
            "dashboard_uids": dashboard_uids,
            "overall_health": scope_health,
            "overall": overall_rows,
            "service_scopes": service_scopes,
            "service_health": service_scopes["all"]["services"],
            "topology": {"type": "logical_aggregate", "nodes": topology, "edges": topology_edges},
            "actions": actions, "printer_exceptions": printer_exceptions,
            "monitoring": monitoring,
            "internet": internet,
            "certificates": certificate_rows,
            "security": security_rows,
            "firewall": firewall_rows,
            "changes": changes,
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
