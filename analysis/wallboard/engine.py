"""Deterministic presentation model for the Operations Wallboard."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from analysis.infrastructure.models import asset_kind, health_of, state_of
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


class WallboardEngine:
    def __init__(self, infrastructure_state="/app/runtime/infrastructure/state.json",
                 operations_state="/app/runtime/operations/operations.json",
                 sites_state="/app/runtime/sites/sites.json",
                 dashboard_template="/app/dashboards/Operations/operations-wallboard.json",
                 summary_output="/app/runtime/dashboard/wallboard-summary.json",
                 dashboard_output="/app/runtime/dashboard/operations/operations-wallboard.json",
                 freshness_seconds=900):
        self.infrastructure_state = Path(infrastructure_state)
        self.operations_state = Path(operations_state)
        self.sites_state = Path(sites_state)
        self.dashboard_template = Path(dashboard_template)
        self.summary_output = Path(summary_output)
        self.dashboard_output = Path(dashboard_output)
        self.freshness_seconds = max(1, int(freshness_seconds))

    def evaluate(self, now=None):
        now = now or datetime.now(timezone.utc)
        state = _read(self.infrastructure_state, {"assets": [], "sites": [], "summary": {},
            "collectors": [], "signals": {}})
        operations = _read(self.operations_state, {"issues": [], "risks": [], "recommendations": []})
        site_state = _read(self.sites_state, {"sites": [], "generated_at": None})
        site_options = [{"site_id": value["site_id"], "display_name": value["display_name"]}
                        for value in sorted(site_state.get("sites", []),
                                           key=lambda value: value["site_id"])]
        scopes = [{"scope": "all", "display_name": "All Sites", "assets": state.get("assets", [])}]
        for site in site_options:
            scopes.append({"scope": site["site_id"], "display_name": site["display_name"],
                "assets": [asset for asset in state.get("assets", [])
                           if _site_id(asset) == site["site_id"]]})
        scope_values = []
        topology = []; topology_edges = []
        for scope in scopes:
            assets = scope["assets"]; states = Counter(state_of(asset) for asset in assets)
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
                "domains": {"network": switch, "wireless": {**wireless,
                    "clients_connected": state.get("wireless", {}).get("clients_connected"),
                    "clients_failed_authentication": state.get("wireless", {}).get("clients_failed_authentication")},
                    "security": {**firewall, "wan_status": state.get("firewalls", {}).get("wan_status")},
                    "compute": servers, "printing": {**printers,
                        "consumables": state.get("printers", {}).get("consumables")}}})
            groups = (
                ("Internet / WAN", [], state.get("firewalls", {}).get("wan_status")),
                ("Firewalls / Edge", [asset for asset in assets if "firewall" in asset_kind(asset)], None),
                ("Core", [asset for asset in assets if "core" in asset_kind(asset)], None),
                ("Distribution", [asset for asset in assets if "distribution" in asset_kind(asset)], None),
                ("Access Switching", [asset for asset in assets if "switch" in asset_kind(asset)
                    and "core" not in asset_kind(asset) and "distribution" not in asset_kind(asset)], None),
                ("Wireless", [asset for asset in assets if "access-point" in asset_kind(asset)
                    or "wireless" in asset_kind(asset)], None),
                ("Servers", [asset for asset in assets if "server" in asset_kind(asset)], None),
                ("Printers", [asset for asset in assets if "print" in asset_kind(asset)], None),
            )
            node_ids = ("internet", "edge", "core", "distribution", "access",
                        "wireless", "servers", "printers")
            for order, (label, members, explicit) in enumerate(groups, 1):
                member_health = Counter(health_of(asset) for asset in members)
                status = explicit or ("Critical" if member_health["critical"] else
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
        attention = {}
        for kind in ("issues", "risks", "recommendations"):
            rows = []
            ordered = sorted(operations.get(kind, []),
                key=lambda value: (-int(value.get("priority", 0)), value.get("title", ""), value.get("id", "")))
            for scope in scopes:
                selected = ordered if scope["scope"] == "all" else [
                    value for value in ordered if value.get("site_id") == scope["scope"]]
                rows.extend({"scope": scope["scope"], **value} for value in selected[:5])
            attention[kind] = rows
        timestamps = {"sites": site_state.get("generated_at"), "infrastructure": state.get("generated_at"),
                      "operations": operations.get("generated_at")}
        parsed = [value for value in (_time(item) for item in timestamps.values()) if value]
        oldest = min(parsed) if parsed else None
        age = max(0, int((now - oldest).total_seconds())) if oldest else None
        freshness = "Unknown" if age is None else "Fresh" if age <= self.freshness_seconds else "Stale"
        return {"generated_at": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "selected_scope": "all", "site_options": site_options, "scopes": scope_values,
            "topology": {"type": "logical_aggregate", "nodes": topology, "edges": topology_edges},
            "attention": attention, "collectors": state.get("collectors", []),
            "freshness": {"sources": timestamps,
                "oldest_generated_at": oldest.isoformat().replace("+00:00", "Z") if oldest else None,
                "age_seconds": age, "status": freshness,
                "threshold_seconds": self.freshness_seconds},
            "services": {name: None for name in ("DNS", "DHCP", "Active Directory", "PaperCut", "Certificates")},
            "wan": {"primary_traffic": None, "secondary_traffic": None, "core_traffic": None,
                    "latency_ms": None, "packet_loss_percent": None}}

    def run(self, now=None):
        value = self.evaluate(now)
        write_wallboard(value, self.dashboard_template, self.summary_output, self.dashboard_output)
        return value
