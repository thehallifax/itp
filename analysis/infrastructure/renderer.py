"""Flat Grafana summary renderer."""
from __future__ import annotations

import csv
import io
import json
from collections import Counter
from pathlib import Path

from collectors.writer import atomic_write
from .models import asset_kind, health_of, state_of


def _scope_value(state, scope, display_name, assets):
    states = Counter(state_of(value) for value in assets)
    count = lambda predicate: [value for value in assets if predicate(value)]
    switches = count(lambda value: "switch" in asset_kind(value))
    aps = count(lambda value: "access-point" in asset_kind(value) or "wireless" in asset_kind(value))
    firewalls = count(lambda value: "firewall" in asset_kind(value) or "security-appliance" in asset_kind(value))
    servers = count(lambda value: "server" in asset_kind(value))
    printers = count(lambda value: "print" in asset_kind(value))
    site_state = next((value for value in state["sites"] if value["site_id"] == scope), {})
    canonical_ids = {value["canonical_id"] for value in assets}
    actionable = sum(value.get("actionable") and not value.get("suppressed")
                     and value.get("canonical_id") in canonical_ids
                     for value in state.get("validation_findings", []))
    summary = state["summary"]
    if scope == "all":
        scoped_collectors = state.get("collectors", [])
    else:
        scoped_collectors = [value for value in state.get("collectors", [])
                             if value.get("shared") is True
                             or scope in value.get("site_ids", [])]
    collector_failed = sum(value.get("status") == "failed" for value in scoped_collectors)
    collector_healthy = sum(value.get("status") == "healthy" for value in scoped_collectors)
    readiness = state.get("readiness") or {}
    readiness_infrastructure = readiness.get("infrastructure") or {}
    readiness_observability = readiness.get("observability") or {}
    observability = ("Warning" if collector_failed else
        "Healthy" if scoped_collectors and collector_healthy == len(scoped_collectors)
        else "Warning" if scoped_collectors else
        readiness_observability.get("display_label", "Unknown"))
    scope_health = summary["infrastructure_health"] if scope == "all" else \
        site_state.get("infrastructure_health",
                       readiness_infrastructure.get("display_label", "Unknown"))
    return {"scope": scope, "display_name": display_name,
        "sites": len(state["sites"]) if scope == "all" else 1,
        "healthy_sites": summary["healthy_sites"] if scope == "all" else int(scope_health == "Healthy"),
        "warning_sites": summary["warning_sites"] if scope == "all" else int(scope_health == "Warning"),
        "critical_sites": summary["critical_sites"] if scope == "all" else int(scope_health == "Critical"),
        "devices": len(assets), "devices_online": states["online"], "devices_offline": states["offline"],
        "actionable_warnings": summary["actionable_warnings"] if scope == "all" else actionable,
        "data_quality_findings": summary["data_quality_findings"] if scope == "all" else 0,
        "infrastructure_health": scope_health,
        "observability_health": observability,
        "collectors_healthy": collector_healthy,
        "collectors_failed": collector_failed,
        "switches_total": len(switches), "switches_online": sum(state_of(value) == "online" for value in switches),
        "switches_offline": sum(state_of(value) == "offline" for value in switches),
        "aps_total": len(aps), "aps_online": sum(state_of(value) == "online" for value in aps),
        "aps_offline": sum(state_of(value) == "offline" for value in aps),
        "firewalls_total": len(firewalls), "firewalls_healthy": sum(health_of(value) == "healthy" for value in firewalls),
        "firewalls_offline": sum(state_of(value) == "offline" for value in firewalls),
        "servers_total": len(servers), "servers_healthy": sum(health_of(value) == "healthy" for value in servers),
        "servers_offline": sum(state_of(value) == "offline" for value in servers),
        "printers_total": len(printers), "printers_healthy": sum(health_of(value) == "healthy" for value in printers),
        "printers_offline": sum(state_of(value) == "offline" for value in printers)}


def flat_summary(state):
    summary = state["summary"]
    readiness = state.get("readiness") or {}
    infrastructure = readiness.get("infrastructure") or {}
    observability = readiness.get("observability") or {}
    value = {
        "generated_at": state["generated_at"],
        "infrastructure_health": infrastructure.get(
            "display_label", summary["infrastructure_health"]),
        "observability_health": observability.get(
            "display_label", summary["observability_health"]),
        "readiness": readiness,
        "sites": summary["sites"], "healthy_sites": summary["healthy_sites"],
        "warning_sites": summary["warning_sites"], "critical_sites": summary["critical_sites"],
        "site_options": [{"site_id": value["site_id"], "display_name": value["display_name"]}
                         for value in state["sites"]],
        "devices": summary["devices"],
        "devices_online": summary["online"], "devices_offline": summary["offline"],
        "warnings": summary["actionable_warnings"],
        "actionable_warnings": summary["actionable_warnings"],
        "data_quality_findings": summary["data_quality_findings"],
        "critical": summary["critical"],
        "collectors_healthy": summary["collectors_healthy"],
        "collectors_failed": summary["collectors_failed"],
        "switches_total": state["network"]["switches"]["total"],
        "switches_online": state["network"]["switches"]["online"],
        "switches_offline": state["network"]["switches"]["offline"],
        "aps_total": state["wireless"]["aps"]["total"],
        "aps_online": state["wireless"]["aps"]["online"],
        "aps_offline": state["wireless"]["aps"]["offline"],
        "firewalls_total": state["firewalls"]["firewalls"],
        "firewalls_healthy": state["firewalls"]["healthy"],
        "firewalls_offline": state["firewalls"]["offline"],
        "servers_total": state["servers"]["servers"],
        "servers_healthy": state["servers"]["healthy"],
        "servers_offline": state["servers"]["offline"],
        "printers_total": state["printers"]["total"],
        "printers_healthy": state["printers"]["healthy"],
        "printers_offline": state["printers"]["offline"],
    }
    scopes = [_scope_value(state, "all", "All Sites", state["assets"])]
    for site in state["sites"]:
        site_id = site["site_id"]
        assets = [asset for asset in state["assets"]
                  if isinstance(asset.get("site"), dict) and asset["site"].get("site_id") == site_id]
        scopes.append(_scope_value(state, site_id, site["display_name"], assets))
    value["scopes"] = scopes
    return value


def write_state(output_dir, dashboard_dir, state):
    output_dir = Path(output_dir); output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(output_dir / "state.json", json.dumps(state, indent=2, sort_keys=True) + "\n")
    fields = ("canonical_id", "hostname", "site_id", "site", "device_type", "serial_number",
              "management_ip", "status", "sources", "merge_confidence", "matched_on",
              "conflict_count")
    stream = io.StringIO(); writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for asset in state["assets"]:
        site = asset.get("site") or {}
        writer.writerow({**asset, "site_id": site.get("site_id", ""),
            "site": site.get("display_name", ""), "sources": ";".join(asset.get("sources", [])),
            "merge_confidence": asset.get("merge", {}).get("confidence", "unmerged"),
            "matched_on": ";".join(asset.get("merge", {}).get("matched_on", [])),
            "conflict_count": len(asset.get("merge", {}).get("conflicts", []))})
    atomic_write(output_dir / "state.csv", stream.getvalue())
    summary = flat_summary(state)
    atomic_write(Path(dashboard_dir) / "infrastructure-summary.json",
                 json.dumps(summary, indent=2, sort_keys=True) + "\n")
    if state.get("readiness"):
        atomic_write(Path(dashboard_dir) / "readiness.json",
                     json.dumps(state["readiness"], indent=2,
                                sort_keys=True) + "\n")
    return summary
