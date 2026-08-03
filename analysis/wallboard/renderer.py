"""Grafana-compatible wallboard summary and dashboard rendering."""
from __future__ import annotations

import csv
import copy
import io
import json
from pathlib import Path

from collectors.writer import atomic_write


DATASOURCE = {"type": "grafana-testdata-datasource", "uid": "itp-runtime-values"}
INFLUX_DATASOURCE = {"type": "influxdb", "uid": "ffsu5ap2kr5dse"}
MIXED_DATASOURCE = {"type": "datasource", "uid": "-- Mixed --"}
HEALTH_COLORS = {"Healthy": "green", "Fresh": "green", "Warning": "orange",
                 "Stale": "orange", "Critical": "red", "Failed": "red",
                 "Unknown": "gray", "Not Enabled": "gray",
                 "Awaiting telemetry": "gray",
                 "Monitoring not started": "gray",
                 "Awaiting first collection": "gray",
                 "Collectors unavailable": "red",
                 "Unavailable": "red",
                 "Not Yet Collected": "gray",
                 "WAN Role Not Configured": "gray",
                 "Feature Unavailable": "gray",
                 "Collector Disabled": "gray",
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


def _sql_literal(value):
    return "'" + str(value or "").replace("'", "''") + "'"


def _wan_sql_target(interface, device_id):
    filters = [
        "collector = 'paloalto'",
        "wan_classified = true",
        f"interface_name = {_sql_literal(interface)}",
        "(${site:sqlstring} = 'all' OR site_id = ${site:sqlstring})",
        "time >= $__timeFrom",
        "time <= $__timeTo",
    ]
    if device_id:
        filters.insert(3, f"device_id = {_sql_literal(device_id)}")
    sql = (
        'SELECT time, rx_bps AS "Download", tx_bps AS "Upload" '
        "FROM interface WHERE " + " AND ".join(filters) + " ORDER BY time")
    return {
        "refId": "A",
        "datasource": INFLUX_DATASOURCE,
        "format": "table",
        "rawQuery": True,
        "rawSql": sql,
        "query": sql,
    }


def _mapping(value_colors=None):
    colors = {**HEALTH_COLORS, **(value_colors or {})}
    return [{"type": "value", "options": {
        name: {"color": color, "index": index, "text": name}
        for index, (name, color) in enumerate(colors.items())}}]


def _service_status(value):
    """Constrain wallboard service tiles to the operator-facing vocabulary."""
    if value in {"Healthy", "Warning", "Critical", "Not Enabled",
                 "Not Yet Collected", "Collector Disabled"}:
        return value
    lowered = str(value or "").casefold()
    if "not configured" in lowered or "not started" in lowered \
            or "disabled" in lowered:
        return "Collector Disabled"
    if "unavailable" in lowered or "failed" in lowered:
        return "Critical"
    return "Not Yet Collected"


def _stat(panel, rows, field, health=False, value_colors=None):
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
        "mappings": _mapping(value_colors) if health or value_colors else [],
        "noValue": "Not Yet Collected",
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
    panel["fieldConfig"] = {"defaults": {
        "custom": {"align": "auto", "cellOptions": {"type": "auto"},
                   "filterable": False, "inspect": False},
        "noValue": "Not Yet Collected"}, "overrides": []}
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
    summary = [
        "Issues", "Overall Health", "Monitoring", "Security",
        "Internet", "Firewall", "Printing", "Certificates"]
    for index, title in enumerate(summary):
        if title not in panels:
            continue
        panels[title]["gridPos"] = {
            "x": index * 3, "y": 0, "w": 3, "h": 3}

    y = 3
    service_titles = [title for title in (
        "Wireless Service", "Switching Service",
        "Management Plane", "Hypervisor Cluster", "Compute Capacity",
        "VM Hosting", "Shared Storage", "Workload Availability",
        "Compute Service", "Identity Service",
        "Storage Service", "Voice Service", "Email Service")
        if title in panels]
    infrastructure = [title for title in (
        "Wireless Access Points", "Switches", "Servers")
        if title in panels]

    wan_panels = sorted(
        (title for title in panels if title.startswith("WAN ·")),
        key=str.casefold)
    last_wan = len(wan_panels) - 1
    for index, title in enumerate(wan_panels):
        row, column = divmod(index, 2)
        single = len(wan_panels) == 1
        final_odd = len(wan_panels) > 1 and index == last_wan and \
            len(wan_panels) % 2 == 1
        panels[title]["gridPos"] = {
            "x": 0 if single or final_odd else column * 12,
            "y": y + row * 8,
            "w": 24 if single or final_odd else 12,
            "h": 8}
    if wan_panels:
        y += ((len(wan_panels) + 1) // 2) * 8
    if "WAN Telemetry" in panels:
        panels["WAN Telemetry"]["gridPos"] = {
            "x": 0, "y": y, "w": 24, "h": 4}
        y += 4
    panels["Action Required"]["gridPos"] = {
        "x": 0, "y": y, "w": 12, "h": 7}
    panels["Changes Since Yesterday"]["gridPos"] = {
        "x": 12, "y": y, "w": 12, "h": 7}
    y += 7
    if "Printers Requiring Attention" in panels:
        panels["Printers Requiring Attention"]["gridPos"] = {
            "x": 0, "y": y, "w": 24, "h": 6}
        y += 6

    # Optional capability and inventory detail belongs below the three
    # operational rows. Keeping the rows above fully occupied prevents
    # Grafana's classic grid compaction from lifting one WAN graph above its
    # peer when only a subset of optional detail panels is enabled.
    for index, title in enumerate(service_titles):
        row, column = divmod(index, 6)
        panels[title]["gridPos"] = {"x": column * 4, "y": y + row * 3,
                                    "w": 4, "h": 3}
    if service_titles:
        y += ((len(service_titles) + 5) // 6) * 3
    compact_width = 5 if len(infrastructure) == 3 else \
                    6 if len(infrastructure) == 2 else 8
    for index, title in enumerate(infrastructure):
        panels[title]["gridPos"] = {"x": index * compact_width, "y": y,
                                    "w": compact_width, "h": 4}


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
    title_changes = {
        "Site Operational Status": "Issues",
        "Overall State": "Overall Health",
        "Last Service Health": "Certificates",
        "Data Freshness": "Security",
        "Freshness": "Security",
        "Internet Service": "Internet",
        "Security Service": "Firewall",
        "Printing Service": "Printing",
        "Monitoring Service": "Monitoring",
        "Collector State": "Changes Since Yesterday",
        "Printer Action Required": "Printers Requiring Attention",
    }
    for panel in dashboard["panels"]:
        panel["title"] = title_changes.get(panel["title"], panel["title"])
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
    panels["Issues"]["type"] = "stat"
    panels["Issues"]["options"] = copy.deepcopy(
        panels["Overall Health"]["options"])
    _stat(panels["Issues"], issue_rows, "value", False)
    panels["Issues"]["description"] = (
        "Active operational issues for the selected canonical site (${site:text}).")
    overall_rows = [{**value, "value": _service_status(value["value"])}
                    for value in summary["overall"]]
    overall_colors = {
        value["value"]: HEALTH_COLORS.get(value["value"], "gray")
        for value in overall_rows}
    _stat(
        panels["Overall Health"], overall_rows, "value",
        value_colors=overall_colors)
    panels["Overall Health"]["description"] = (
        "State: the highest canonical Service Health severity for the selected "
        "site. Evidence: enabled service evaluations and active findings. "
        "Drill-down: open Infrastructure Overview for service evidence.")
    security_rows = [{**value, "value": _service_status(value["value"])}
                     for value in summary["security"]]
    security_colors = {
        value["value"]: HEALTH_COLORS.get(value["value"], "gray")
        for value in security_rows}
    _stat(
        panels["Security"], security_rows, "value",
        value_colors=security_colors)
    panels["Security"]["description"] = (
        "State: canonical Security service severity. Evidence: certificates, "
        "subscriptions, threat services, and security findings. Drill-down: "
        "open the firewall operational dashboard.")
    certificate_colors = {
        value["value"]: value["color"]
        for value in summary["certificates"]}
    _stat(
        panels["Certificates"], summary["certificates"], "value",
        value_colors=certificate_colors)
    panels["Certificates"]["description"] = (
        "Certificate conditions promoted by canonical Operations findings.")
    monitoring_service = []
    monitoring_details = []
    for scope in summary["scopes"]:
        service = next(value for value in
                       summary["service_scopes"][scope["scope"]]["services"]
                       if value["service"] == "Monitoring")
        value = next(value for value in summary["monitoring"]
                     if value["scope"] == scope["scope"])
        status = service["status"]
        if status == "Unknown":
            status = ("Collector Disabled"
                      if not summary.get("enabled_collectors")
                      else "Feature Unavailable")
        monitoring_service.append({
            "scope": scope["scope"], "value": status})
        services = ", ".join(value["stale_services"]) or "none"
        monitoring_details.append(
            f"{value['scope']}: collectors with issues="
            f"{value['collectors_with_issues']}; last successful collection="
            f"{value['last_successful_collection'] or 'never'}; "
            f"stale services={services}")
    _stat(panels["Monitoring"], monitoring_service, "value", True)
    panels["Monitoring"]["description"] = (
        "State: canonical Monitoring service severity. Evidence: collector "
        "outcomes, freshness, and last successful collection. Drill-down: "
        "open Collector Health.\n" + "\n".join(monitoring_details))

    enabled_service_names = {value["service"] for scope in summary["service_scopes"].values()
                             for value in scope["services"]
                             if value["status"] != "Not Enabled"}
    virtual_services = (
        "Virtualisation Management Plane", "Hypervisor Cluster", "Compute Capacity",
        "Virtual Machine Hosting", "Shared Storage", "Workload Availability")
    service_titles = {f"{name} Service": name for name in (
        "Wireless", "Switching", "Identity", "Compute",
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
                status = "Not Yet Collected"
            service_rows.append({"scope": scope["scope"], "value": status})
            summaries.append(f"{scope['display_name']}: {value.get('summary', '')}")
        _stat(panels[title], service_rows, "value", True)
        panels[title]["description"] = (
            f"Canonical service: {name}.\n" + "\n".join(summaries))

    def capability_detail(value):
        items = [item for item in value.get("evidence", [])
                 if item.get("type") == "collector_capability"]
        groups = {
            "Evidence collected": sorted({
                item.get("capability") for item in items
                if item.get("collection") == "collected"}),
            "Evidence unavailable": sorted({
                item.get("capability") for item in items
                if item.get("collection") in {"failed", "partial", "unavailable"}}),
            "Capabilities not enabled": sorted({
                item.get("capability") for item in items
                if item.get("support") == "unsupported"
                or item.get("collection") in {"disabled", "not_applicable"}}),
        }
        return " ".join(
            f"{label}: {', '.join(values)}."
            for label, values in groups.items() if values)

    def canonical_card(title, service, evidence, drilldown):
        rows = []
        summaries = []
        for scope in summary["scopes"]:
            value = next(item for item in
                summary["service_scopes"][scope["scope"]]["services"]
                if item["service"] == service)
            rows.append({"scope": scope["scope"],
                         "value": _service_status(value["status"])})
            summaries.append(
                f"{scope['display_name']}: {value.get('summary', '')} "
                f"{capability_detail(value)}".strip())
        _stat(panels[title], rows, "value", True)
        panels[title]["description"] = (
            f"State: canonical {service} service severity. Evidence: "
            f"{evidence}. Drill-down: {drilldown}.\n" + "\n".join(summaries))

    firewall_colors = {
        value["value"]: HEALTH_COLORS.get(value["status"], "gray")
        for value in summary["firewall"]}
    _stat(panels["Firewall"], summary["firewall"], "value",
          value_colors=firewall_colors)
    panels["Firewall"]["description"] = (
        "State: canonical Security service severity, with the highest-priority "
        "firewall cue when one is available. Evidence: firewall availability, "
        "certificates, subscriptions, and security findings. Drill-down: open "
        "the firewall operational dashboard.")
    canonical_card(
        "Printing", "Printing",
        "printer availability, device errors, services, and consumables",
        "open the PaperCut operational dashboard")
    internet_colors = {
        value["value"]: value["color"] for value in summary["internet"]}
    _stat(
        panels["Internet"], summary["internet"], "value",
        value_colors=internet_colors)
    panels["Internet"]["description"] = (
        "State: canonical Internet service severity. Evidence: authoritative "
        "WAN classification and current interface state; the count is healthy "
        "WANs over classified WANs. Drill-down: open the firewall WAN view.")

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

    prototype = panels["WAN Traffic"]
    interfaces = sorted({
        (value.get("interface_name") or value["interface"],
         value.get("display_name") or value["label"],
         value.get("role") or "Unspecified",
         value.get("device_id") or "")
        for value in summary["wan"]["uplinks"]
        if value.get("interface_name") or value.get("interface")
    }, key=lambda value: (
        value[2].casefold(), value[1].casefold(), value[0].casefold(),
        value[3].casefold()))
    wan_panels = []
    for offset, (interface_name, display_name, role, device_id) in enumerate(
            interfaces, 40):
        panel = copy.deepcopy(prototype)
        panel.update({"id": offset,
                      "title": f"WAN · {role} · {display_name}",
                      "type": "timeseries"})
        samples = [value for value in summary["wan"].get("samples", [])
                   if (value.get("interface_name") or value.get("interface"))
                   == interface_name
                   and (not device_id or value.get("device_id") == device_id)]
        panel["datasource"] = MIXED_DATASOURCE
        panel["targets"] = [_wan_sql_target(interface_name, device_id)]
        if samples:
            fallback = [{"time": value.get("time"),
                         "Download": value.get("rx_bps"),
                         "Upload": value.get("tx_bps")}
                        for value in samples if value.get("scope") == "all"]
            fallback_target = _target(_csv(
                fallback, ("time", "Download", "Upload")))
            fallback_target.update({"refId": "B", "hide": True})
            panel["targets"].append(fallback_target)
        panel["transformations"] = []
        panel["fieldConfig"] = {"defaults": {
            "unit": "bps", "color": {"mode": "palette-classic"},
            "custom": {"drawStyle": "line", "lineWidth": 2,
                       "lineInterpolation": "linear", "fillOpacity": 12,
                       "showPoints": "never", "spanNulls": False,
                       "insertNulls": 180000},
            "noValue": "Awaiting throughput telemetry"},
            "overrides": []}
        panel["options"] = {
            "legend": {
                "calcs": ["lastNotNull"],
                "displayMode": "table",
                "placement": "bottom",
                "showLegend": True,
                "width": 280,
            },
            "tooltip": {"mode": "multi", "sort": "desc"},
        }
        panel["description"] = (
            f"{display_name} ({interface_name}, {role}). Collector-derived "
            "canonical interface "
            "rates at collection cadence. Gaps longer than three minutes are "
            "not connected. A hidden state-derived CSV target remains available "
            "for portable inspection.")
        wan_panels.append(panel)
    dashboard["panels"] = [
        panel for panel in dashboard["panels"]
        if panel["title"] not in {"Internet / WAN", "WAN Traffic"}]
    dashboard["panels"].extend(wan_panels)
    panels = {value["title"]: value for value in dashboard["panels"]}
    if not interfaces and "internet" in summary.get("capabilities", []):
        panel = copy.deepcopy(prototype)
        panel.update({"id": 40, "title": "WAN Telemetry", "type": "text"})
        panel.pop("datasource", None)
        panel["targets"] = []
        panel["transformations"] = []
        panel["options"] = {"mode": "markdown", "content":
            "## WAN role not configured\n\nSelect at least one authoritative "
            "WAN interface in the Palo Alto collector configuration."}
        dashboard["panels"].append(panel)
        panels[panel["title"]] = panel

    printer_rows = summary["printer_exceptions"]
    printer_empty = bool(printer_rows) and all(
        value.get("asset") == "No printers require attention"
        for value in printer_rows)
    printer_fields = ("scope", "asset") if printer_empty else (
        "scope", "asset", "location", "condition", "last_seen")
    _table(panels["Printers Requiring Attention"],
           printer_rows, printer_fields)
    printer_organize = panels[
        "Printers Requiring Attention"]["transformations"][-1]["options"]
    if printer_empty:
        printer_organize["renameByName"] = {"asset": "Status"}
        _column_widths(
            panels["Printers Requiring Attention"], {"asset": 800})
    else:
        printer_organize["renameByName"] = {
            "asset": "Printer", "location": "Location",
            "condition": "Condition", "last_seen": "Last Seen"}
        _column_widths(panels["Printers Requiring Attention"], {
            "asset": 180, "location": 150,
            "condition": 240, "last_seen": 170})
    _table(
        panels["Changes Since Yesterday"], summary["changes"],
        ("scope", "time", "service", "change"))
    _column_widths(panels["Changes Since Yesterday"], {
        "time": 190, "service": 150, "change": 620})
    action_rows = [{**value,
        "domain": str(value.get("domain") or "").replace("_", " ").title(),
        "provider": PROVIDER_LABELS.get(
            str(value.get("provider") or "").casefold(), value.get("provider") or ""),
        "object_kind": OBJECT_LABELS.get(
            str(value.get("object_kind") or "").casefold(),
            str(value.get("object_kind") or "").replace("_", " ").title())}
        for value in summary["actions"]]
    _table(panels["Action Required"], action_rows,
           ("scope", "severity", "service", "asset", "issue", "age"))
    organize = panels["Action Required"]["transformations"][-1]["options"]
    organize["renameByName"] = {
        "severity": "Severity", "service": "Service", "asset": "Asset",
        "issue": "Action", "age": "Age"}
    _column_widths(panels["Action Required"], {
        "severity": 90, "service": 165, "asset": 210,
        "issue": 700, "age": 80})
    panels["Action Required"]["description"] = (
        "Priority-ordered operator actions. Open the relevant service "
        "dashboard for diagnostic evidence.")

    available = set(summary.get("dashboard_uids", []))
    def links(*uids):
        return [{"title": "Open details", "url": f"/d/{uid}?var-site=${{site}}",
                 "targetBlank": False} for uid in uids if uid in available]
    panels["Wireless Access Points"]["links"] = links("mist-infrastructure-overview",
                                                       "itp-infrastructure-overview")
    panels["Switches"]["links"] = links("itp-infrastructure-overview")
    firewall_uids = ("paloalto-operational-overview", "fortigate-infrastructure-overview",
                     "itp-infrastructure-overview")
    panels["Firewall"]["links"] = links(*firewall_uids)
    panels["Security"]["links"] = links(*firewall_uids)
    panels["Internet"]["links"] = links(*firewall_uids)
    for title in (value for value in panels if value.startswith("WAN ·")):
        panels[title]["links"] = links(*firewall_uids)
    printing = [uid for uid in available if "print" in uid.lower()]
    panels["Printing"]["links"] = links(*printing)
    panels["Printers Requiring Attention"]["links"] = links(*printing)
    panels["Monitoring"]["links"] = links("itp-collector-health")
    disabled_panels = {title for title, name in service_titles.items()
                       if name not in enabled_service_names}
    service_for_domain = {"Wireless Access Points": "Wireless",
                          "Switches": "Switching", "Servers": "Compute"}
    disabled_panels.update(title for title, service in service_for_domain.items()
                           if service not in enabled_service_names)
    disabled_panels.add("Firewalls")
    if "Printing" not in enabled_service_names:
        disabled_panels.add("Printers Requiring Attention")
    dashboard["panels"] = [panel for panel in dashboard["panels"]
                           if panel["title"] not in disabled_panels]
    _layout(dashboard)
    dashboard["version"] = int(dashboard.get("version", 0)) + 1
    atomic_write(
        dashboard_path, json.dumps(dashboard, indent=2, sort_keys=True) + "\n",
        mode=0o644, directory_mode=0o755)
    return dashboard
