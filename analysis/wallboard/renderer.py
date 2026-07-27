"""Grafana-compatible wallboard summary and dashboard rendering."""
from __future__ import annotations

import csv
import copy
import io
import json
from pathlib import Path

from collectors.writer import atomic_write


DATASOURCE = {"type": "grafana-testdata-datasource", "uid": "itp-runtime-values"}
HEALTH_COLORS = {"Healthy": "green", "Fresh": "green", "Warning": "orange",
                 "Stale": "orange", "Critical": "red", "Failed": "red",
                 "Unknown": "gray", "Not Enabled": "gray",
                 "Awaiting telemetry": "gray",
                 "Monitoring not started": "gray",
                 "Awaiting first collection": "gray",
                 "Collectors unavailable": "red",
                 "Unavailable": "red",
                 "Waiting for first run": "gray",
                 "Waiting for first success": "gray"}
PROVIDER_LABELS = {"vmware": "VMware", "hyperv": "Hyper-V", "proxmox": "Proxmox"}
OBJECT_LABELS = {"cluster": "Cluster", "host": "Host", "storage": "Storage",
                 "vm": "Virtual Machine", "virtual_machine": "Virtual Machine",
                 "container": "Container", "virtual_container": "Container",
                 "manager": "Manager", "snapshot": "Snapshot"}


def _csv(rows, fields):
    stream = io.StringIO(); writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows: writer.writerow(row)
    return stream.getvalue().rstrip()


def _scope_filter():
    return [{"id": "filterByValue", "options": {"filters": [{
        "config": {"id": "equal", "options": {"value": "${site}"}},
        "fieldName": "scope"}], "match": "all", "type": "include"}}]


def _target(csv_content):
    return {"refId": "A", "scenarioId": "csv_content", "csvContent": csv_content,
            "datasource": DATASOURCE}


def _mapping():
    return [{"type": "value", "options": {name: {"color": color, "index": index, "text": name}
        for index, (name, color) in enumerate(HEALTH_COLORS.items())}}]


def _stat(panel, rows, field, health=False):
    values = []
    for row in rows:
        value = row.get(field)
        if field == "online" and row.get("devices"):
            value = f"{value} / {row['devices']} ({100 * value / row['devices']:.0f}%)"
        if field == "collectors_healthy" and row.get("collectors_total"):
            value = f"{value} / {row['collectors_total']} ({100 * value / row['collectors_total']:.0f}%)"
        values.append({"scope": row["scope"], "value": value})
    panel["datasource"] = DATASOURCE
    panel["targets"] = [_target(_csv(values, ("scope", "value")))]
    display_field = field.replace("_", " ").title()
    panel["transformations"] = _scope_filter() + [{"id": "organize", "options": {
        "excludeByName": {"scope": True}, "renameByName": {"value": display_field}}}]
    panel["fieldConfig"] = {"defaults": {"color": {"mode": "thresholds"},
        "mappings": _mapping() if health else [],
        "noValue": "State unavailable",
        "thresholds": {"mode": "absolute", "steps": [{"color": "green" if health else "blue", "value": None}]}},
        "overrides": []}
    # Grafana 13's Stat reducer ignores string-only CSV fields when numeric
    # reduction is enabled. Select the post-transform field explicitly and
    # retain row values, matching the working vendor string Stat convention.
    panel["options"].update({"textMode": "value", "reduceOptions": {
        "calcs": [], "fields": f"/^{display_field}$/", "values": True}})


def _table(panel, rows, fields, scoped=True):
    panel["datasource"] = DATASOURCE; panel["targets"] = [_target(_csv(rows, fields))]
    panel["transformations"] = (_scope_filter() + [{"id": "organize", "options": {
        "excludeByName": {"scope": True}}}]) if scoped else []
    panel["fieldConfig"] = {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"},
        "filterable": False, "inspect": False}}, "overrides": []}
    panel["options"] = {"cellHeight": "sm", "footer": {"show": False}, "showHeader": True}


def _column_widths(panel, widths):
    panel["fieldConfig"]["overrides"].extend({
        "matcher": {"id": "byName", "options": name},
        "properties": [{"id": "custom.width", "value": width},
                       {"id": "custom.cellOptions", "value": {"type": "auto"}}],
    } for name, width in widths.items())


def _layout(dashboard):
    """Pack capability-gated panels into a deliberate, overlap-free grid."""
    panels = {value["title"]: value for value in dashboard["panels"]}
    summary = ["Site Operational Status", "Overall State", "Last Service Health",
               "Data Freshness", "Monitoring Service"]
    widths = [8, 4, 4, 4, 4]
    x = 0
    for title, width in zip(summary, widths):
        if title not in panels:
            continue
        panels[title]["gridPos"] = {"x": x, "y": 0, "w": width, "h": 3}
        x += width

    y = 3
    service_titles = [title for title in (
        "Internet Service", "Wireless Service", "Switching Service",
        "Management Plane", "Hypervisor Cluster", "Compute Capacity",
        "VM Hosting", "Shared Storage", "Workload Availability",
        "Security Service", "Compute Service", "Identity Service",
        "Printing Service", "Storage Service", "Voice Service", "Email Service")
        if title in panels]
    for index, title in enumerate(service_titles):
        row, column = divmod(index, 6)
        panels[title]["gridPos"] = {"x": column * 4, "y": y + row * 3,
                                    "w": 4, "h": 3}
    y += max(1, (len(service_titles) + 5) // 6) * 3

    infrastructure = [title for title in (
        "Wireless Access Points", "Switches", "Servers", "Firewalls")
        if title in panels]
    compact_width = 4 if len(infrastructure) == 4 else \
                    5 if len(infrastructure) == 3 else \
                    6 if len(infrastructure) == 2 else 8
    for index, title in enumerate(infrastructure):
        panels[title]["gridPos"] = {"x": index * compact_width, "y": y,
                                    "w": compact_width, "h": 4}
    collector_x = len(infrastructure) * compact_width
    panels["Collector State"]["gridPos"] = {
        "x": collector_x, "y": y, "w": 24 - collector_x, "h": 4}
    y += 4

    if "Printer Action Required" in panels:
        panels["Printer Action Required"]["gridPos"] = {"x": 0, "y": y, "w": 24, "h": 4}
        y += 4
    if "Internet / WAN" in panels:
        panels["Internet / WAN"]["gridPos"] = {"x": 0, "y": y, "w": 12, "h": 5}
        panels["WAN Traffic"]["gridPos"] = {"x": 12, "y": y, "w": 12, "h": 5}
        y += 5
    panels["Action Required"]["gridPos"] = {"x": 0, "y": y, "w": 24, "h": 7}


def _topology(panel, topology):
    panel["type"] = "nodeGraph"; panel["datasource"] = DATASOURCE
    node_fields = ("scope", "id", "title", "subTitle", "mainStat", "secondaryStat", "color")
    edge_fields = ("scope", "id", "source", "target")
    nodes = _target(_csv(topology["nodes"], node_fields)); nodes.update({"refId": "nodes", "alias": "nodes"})
    edges = _target(_csv(topology["edges"], edge_fields)); edges.update({"refId": "edges", "alias": "edges"})
    panel["targets"] = [nodes, edges]
    panel["transformations"] = _scope_filter()
    panel["fieldConfig"] = {"defaults": {}, "overrides": []}
    panel["options"] = {"nodes": {"mainStatUnit": "short", "secondaryStatUnit": "short"},
                        "edges": {}}


def _domain_rows(summary, name):
    rows = []
    for scope in summary["scopes"]:
        value = scope["domains"][name]
        if not value.get("available", True):
            rows.append({"scope": scope["scope"], "Total": "Not enabled",
                "Online": "N/A", "Offline": "N/A", "Warning": "N/A",
                "Unknown": "N/A", "Clients": "N/A", "Failed Auth": "N/A",
                "WAN": "Not enabled", "Consumables": "N/A"})
            continue
        row = {"scope": scope["scope"], "Total": value.get("total"),
            "Online": value.get("online", value.get("healthy")),
            "Offline": value.get("offline"), "Warning": value.get("warning"),
            "Unknown": value.get("unknown")}
        if name == "wireless":
            row["Clients"] = value.get("clients_connected") if value.get("clients_connected") is not None else "N/A"
            row["Failed Auth"] = (value.get("clients_failed_authentication")
                                  if value.get("clients_failed_authentication") is not None else "N/A")
        if name == "security": row["WAN"] = value.get("wan_status") or "Awaiting telemetry"
        if name == "printing":
            row["Consumables"] = value.get("consumables") if value.get("consumables") is not None else "N/A"
        rows.append(row)
    return rows


def write_wallboard(summary, template_path, summary_path, dashboard_path):
    atomic_write(summary_path, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    dashboard = json.loads(Path(template_path).read_text())
    variable = next(value for value in dashboard["templating"]["list"] if value["name"] == "site")
    if len(summary["site_options"]) == 1:
        only = summary["site_options"][0]
        variable["options"] = [{"selected": True, "text": only["display_name"],
                                "value": only["site_id"]}]
        variable["current"] = {"selected": True, "text": only["display_name"],
                               "value": only["site_id"]}
    else:
        variable["options"] = [{"selected": True, "text": "All Sites", "value": "all"}] + [
            {"selected": False, "text": value["display_name"], "value": value["site_id"]}
            for value in summary["site_options"]]
        variable["current"] = {"selected": True, "text": "All Sites", "value": "all"}
    variable["query"] = ",".join(
        str(value["display_name"]).replace(",", "\\,") + " : " + value["site_id"]
        for value in summary["site_options"])
    panels = {value["title"]: value for value in dashboard["panels"]}
    freshness = summary["freshness"]
    readiness = summary.get("readiness") or {}
    readiness_state = readiness.get("overall", {}).get("state")
    issue_rows = [{"scope": scope["scope"],
        "value": (f"{scope['active_issues']} active issues"
                  if scope["active_issues"] else {
                      "not_configured": "Monitoring not configured",
                      "waiting_first_collection": "Waiting for first collection",
                      "unavailable": "Collection unavailable",
                  }.get(readiness_state, "No active issues"))}
        for scope in summary["scopes"]]
    panels["Site Operational Status"]["type"] = "stat"
    panels["Site Operational Status"]["options"] = copy.deepcopy(
        panels["Overall State"]["options"])
    _stat(panels["Site Operational Status"], issue_rows, "value", False)
    panels["Site Operational Status"]["description"] = (
        "Active operational issues for the selected canonical site (${site:text}).")
    health_rows = [{"scope": scope, "value": value}
                   for scope, value in sorted(summary["overall_health"].items())]
    _stat(panels["Overall State"], health_rows, "value", True)
    refresh_rows = [{"scope": scope["scope"],
        "value": freshness.get("age_display") or "Unavailable"}
        for scope in summary["scopes"]]
    _stat(panels["Last Service Health"], refresh_rows, "value", False)
    panels["Last Service Health"]["description"] = (
        "Last canonical Service Health evaluation: "
        f"{freshness.get('last_successful_refresh') or 'Unavailable'}.")
    freshness_rows = [{"scope": scope["scope"], "value": freshness["status"]}
                      for scope in summary["scopes"]]
    _stat(panels["Data Freshness"], freshness_rows, "value", True)
    panels["Data Freshness"]["description"] = (
        f"Stale when canonical platform inputs exceed {freshness['threshold_seconds']} seconds.")

    enabled_service_names = {value["service"] for scope in summary["service_scopes"].values()
                             for value in scope["services"]
                             if value["status"] != "Not Enabled"}
    virtual_services = (
        "Virtualisation Management Plane", "Hypervisor Cluster", "Compute Capacity",
        "Virtual Machine Hosting", "Shared Storage", "Workload Availability")
    service_titles = {f"{name} Service": name for name in (
        "Internet", "Wireless", "Switching", "Printing",
        "Identity", "Compute", "Security", "Monitoring",
        "Storage", "Voice", "Email")}
    service_titles.update({
        "Management Plane": "Virtualisation Management Plane",
        "Hypervisor Cluster": "Hypervisor Cluster",
        "Compute Capacity": "Compute Capacity",
        "VM Hosting": "Virtual Machine Hosting",
        "Shared Storage": "Shared Storage",
        "Workload Availability": "Workload Availability",
    })
    prototype = panels["Compute Service"]
    for offset, name in enumerate(virtual_services, 25):
        title = {
            "Virtualisation Management Plane": "Management Plane",
            "Virtual Machine Hosting": "VM Hosting",
        }.get(name, name)
        if title not in panels:
            panel = copy.deepcopy(prototype)
            panel.update({"id": offset, "title": title})
            dashboard["panels"].append(panel)
            panels[title] = panel
    for title, name in service_titles.items():
        if name not in enabled_service_names:
            continue
        service_rows = []
        summaries = []
        for scope in summary["scopes"]:
            value = next(item for item in
                summary["service_scopes"][scope["scope"]]["services"]
                if item["service"] == name)
            status = value["status"]
            if status == "Unknown":
                status = {
                    "waiting_first_collection": "Awaiting telemetry",
                    "unavailable": "Unavailable",
                }.get(readiness_state, status)
            service_rows.append({"scope": scope["scope"], "value": status})
            summaries.append(f"{scope['display_name']}: {value.get('summary', '')}")
        _stat(panels[title], service_rows, "value", True)
        panels[title]["description"] = (
            f"Canonical service: {name}.\n" + "\n".join(summaries))

    def health_card(title, domain):
        rows = []
        for scope in summary["scopes"]:
            value = scope["domains"][domain]
            rows.append({"scope": scope["scope"],
                "Healthy": value.get("online", value.get("healthy", 0))
                    if value.get("available", True) else "Not enabled",
                "Offline": value.get("offline", 0) if value.get("available", True) else "N/A",
                "Unknown": value.get("unknown", 0) if value.get("available", True) else "N/A"})
        _table(panels[title], rows, ("scope", "Healthy", "Offline", "Unknown"))
        panels[title]["fieldConfig"]["overrides"] = [
            {"matcher": {"id": "byName", "options": "Healthy"}, "properties": [
                {"id": "color", "value": {"fixedColor": "green", "mode": "fixed"}},
                {"id": "custom.cellOptions", "value": {"type": "color-background"}}]},
            {"matcher": {"id": "byName", "options": "Unknown"}, "properties": [
                {"id": "color", "value": {"fixedColor": "gray", "mode": "fixed"}},
                {"id": "custom.cellOptions", "value": {"type": "color-background"}}]},
            {"matcher": {"id": "byName", "options": "Offline"}, "properties": [
                {"id": "thresholds", "value": {"mode": "absolute", "steps": [
                    {"color": "green", "value": None}, {"color": "red", "value": 1}]}},
                {"id": "custom.cellOptions", "value": {"type": "color-background"}}]}]

    for title, domain in (("Wireless Access Points", "wireless"), ("Switches", "network"),
                          ("Servers", "compute"), ("Firewalls", "security")):
        health_card(title, domain)

    _table(panels["Internet / WAN"], summary["wan"]["uplinks"],
           ("scope", "uplink", "role", "state", "latency_ms", "packet_loss_percent"))
    samples = summary["wan"].get("samples", [])
    if samples:
        panel = panels["WAN Traffic"]; panel["type"] = "timeseries"
        panel["targets"] = [_target(_csv(samples,
            ("scope", "time", "uplink", "rx_bps", "tx_bps")))]
        panel["transformations"] = _scope_filter()
        panel["fieldConfig"] = {"defaults": {"unit": "bps",
            "color": {"mode": "palette-classic"}}, "overrides": []}
    else:
        panel = panels["WAN Traffic"]; panel["type"] = "text"; panel.pop("datasource", None)
        panel["targets"] = []; panel["transformations"] = []
        message = {
            "not_configured": (
                "## Internet capability not enabled\n\n"
                "Enable a collector that provides authoritative Internet telemetry."),
            "waiting_first_collection": (
                "## Waiting for WAN telemetry\n\n"
                "Complete the first successful collection."),
            "unavailable": (
                "## WAN telemetry unavailable\n\n"
                "Review Collector Health and run Doctor."),
        }.get(readiness_state, (
            "## WAN not classified\n\nNo time series exists for an "
            "authoritatively classified WAN uplink."))
        panel["options"] = {"mode": "markdown", "content": message}

    _table(panels["Printer Action Required"], summary["printer_exceptions"],
           ("scope", "asset", "location", "condition", "last_seen"))
    _table(panels["Collector State"], summary["collectors"],
           ("scope", "collector", "site", "status", "freshness"))
    panels["Collector State"]["fieldConfig"]["defaults"]["custom"]["inspect"] = True
    _column_widths(panels["Collector State"], {
        "collector": 140, "site": 320, "status": 110, "freshness": 120})
    panels["Collector State"]["fieldConfig"]["overrides"].append({
        "matcher": {"id": "byName", "options": "status"}, "properties": [
            {"id": "mappings", "value": [{"type": "value", "options": {
                "Healthy": {"color": "green", "index": 0, "text": "Healthy"},
                "Warning": {"color": "orange", "index": 1, "text": "Warning"},
                "Failed": {"color": "red", "index": 2, "text": "Failed"},
                "Stale": {"color": "orange", "index": 3, "text": "Stale"},
                "Monitoring not started": {"color": "gray", "index": 4,
                    "text": "Monitoring not started"},
                "Awaiting first collection": {"color": "gray", "index": 5,
                    "text": "Awaiting first collection"},
                "Collectors unavailable": {"color": "red", "index": 6,
                    "text": "Collectors unavailable"}}}]},
            {"id": "custom.cellOptions", "value": {"type": "color-background"}},
        ]})
    action_rows = [{**value,
        "domain": str(value.get("domain") or "").replace("_", " ").title(),
        "provider": PROVIDER_LABELS.get(
            str(value.get("provider") or "").casefold(), value.get("provider") or ""),
        "object_kind": OBJECT_LABELS.get(
            str(value.get("object_kind") or "").casefold(),
            str(value.get("object_kind") or "").replace("_", " ").title())}
        for value in summary["actions"]]
    _table(panels["Action Required"], action_rows,
           ("scope", "severity", "service", "domain", "provider", "object_kind",
            "asset", "issue", "age"))
    organize = panels["Action Required"]["transformations"][-1]["options"]
    organize["renameByName"] = {
        "severity": "Severity", "service": "Service", "domain": "Domain",
        "provider": "Provider", "object_kind": "Object Type", "asset": "Asset",
        "issue": "Issue", "age": "Age"}
    _column_widths(panels["Action Required"], {
        "severity": 90, "domain": 120, "provider": 100, "object_kind": 120,
        "service": 165, "asset": 190, "issue": 520, "age": 80})
    panels["Action Required"]["description"] = (
        "Provider-neutral active operations, including virtualisation management, "
        "cluster, host, workload, capacity, storage, collection and snapshot evidence.")

    available = set(summary.get("dashboard_uids", []))
    def links(*uids):
        return [{"title": "Open details", "url": f"/d/{uid}?var-site=${{site}}",
                 "targetBlank": False} for uid in uids if uid in available]
    panels["Wireless Access Points"]["links"] = links("mist-infrastructure-overview",
                                                       "itp-infrastructure-overview")
    panels["Switches"]["links"] = links("itp-infrastructure-overview")
    firewall_uids = ("paloalto-operational-overview", "fortigate-infrastructure-overview",
                     "itp-infrastructure-overview")
    panels["Firewalls"]["links"] = links(*firewall_uids)
    panels["Internet / WAN"]["links"] = links(*firewall_uids)
    printing = [uid for uid in available if "print" in uid.lower()]
    panels["Printer Action Required"]["links"] = links(*printing)
    panels["Collector State"]["links"] = links("itp-collector-health")
    disabled_panels = {title for title, name in service_titles.items()
                       if name not in enabled_service_names}
    service_for_domain = {"Wireless Access Points": "Wireless",
                          "Switches": "Switching", "Servers": "Compute",
                          "Firewalls": "Security"}
    disabled_panels.update(title for title, service in service_for_domain.items()
                           if service not in enabled_service_names)
    if "Printing" not in enabled_service_names:
        disabled_panels.add("Printer Action Required")
    if "internet" not in summary.get("capabilities", []):
        disabled_panels.update(("Internet / WAN", "WAN Traffic"))
    dashboard["panels"] = [panel for panel in dashboard["panels"]
                           if panel["title"] not in disabled_panels]
    _layout(dashboard)
    dashboard["version"] = int(dashboard.get("version", 0)) + 1
    atomic_write(dashboard_path, json.dumps(dashboard, indent=2, sort_keys=True) + "\n")
    return dashboard
