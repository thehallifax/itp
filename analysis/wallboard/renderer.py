"""Grafana-compatible wallboard summary and dashboard rendering."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from collectors.writer import atomic_write


DATASOURCE = {"type": "grafana-testdata-datasource", "uid": "itp-runtime-values"}
HEALTH_COLORS = {"Healthy": "green", "Fresh": "green", "Warning": "orange",
                 "Stale": "orange", "Critical": "red", "Failed": "red",
                 "Unknown": "gray", "Not Enabled": "gray",
                 "Awaiting telemetry": "gray"}


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
        "noValue": "No data",
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
    panels["Site Operational Status"]["options"]["content"] = (
        "# Operations Wallboard\n\n"
        "**Site:** ${site:text}  •  **Exception-driven operational view**")
    health_rows = [{"scope": scope, "value": value}
                   for scope, value in sorted(summary["overall_health"].items())]
    _stat(panels["Overall State"], health_rows, "value", True)
    refresh_rows = [{"scope": scope["scope"],
        "value": freshness.get("last_successful_refresh") or "Unavailable"}
        for scope in summary["scopes"]]
    _stat(panels["Last Service Health"], refresh_rows, "value", False)
    freshness_rows = [{"scope": scope["scope"], "value": freshness["status"]}
                      for scope in summary["scopes"]]
    _stat(panels["Data Freshness"], freshness_rows, "value", True)
    panels["Data Freshness"]["description"] = (
        f"Stale when canonical platform inputs exceed {freshness['threshold_seconds']} seconds.")

    enabled_service_names = {value["service"] for scope in summary["service_scopes"].values()
                             for value in scope["services"]
                             if value["status"] != "Not Enabled"}
    service_titles = {f"{name} Service": name for name in (
        "Internet", "Wireless", "Switching", "Printing",
        "Identity", "Compute", "Security", "Monitoring",
        "Storage", "Voice", "Email")}
    for title, name in service_titles.items():
        if name not in enabled_service_names:
            continue
        service_rows = []
        summaries = []
        for scope in summary["scopes"]:
            value = next(item for item in
                summary["service_scopes"][scope["scope"]]["services"]
                if item["service"] == name)
            service_rows.append({"scope": scope["scope"], "value": value["status"]})
            summaries.append(f"{scope['display_name']}: {value.get('summary', '')}")
        _stat(panels[title], service_rows, "value", True)
        panels[title]["description"] = "\n".join(summaries)

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
        panel["options"] = {"mode": "markdown", "content":
            "## Traffic unavailable\n\nNo time series exists for an authoritatively classified WAN uplink."}

    _table(panels["Printer Action Required"], summary["printer_exceptions"],
           ("scope", "asset", "location", "condition", "last_seen"))
    _table(panels["Collector State"], summary["collectors"],
           ("scope", "collector", "site", "status"))
    panels["Collector State"]["fieldConfig"]["overrides"] = [{
        "matcher": {"id": "byName", "options": "status"}, "properties": [
            {"id": "mappings", "value": [{"type": "value", "options": {
                "Healthy": {"color": "green", "index": 0, "text": "Healthy"},
                "Warning": {"color": "orange", "index": 1, "text": "Warning"},
                "Failed": {"color": "red", "index": 2, "text": "Failed"},
                "Stale": {"color": "orange", "index": 3, "text": "Stale"}}}]},
            {"id": "custom.cellOptions", "value": {"type": "color-background"}},
        ]}]
    _table(panels["Action Required"], summary["actions"],
           ("scope", "severity", "service", "asset", "issue", "age"))

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
    dashboard["panels"] = [panel for panel in dashboard["panels"]
                           if panel["title"] not in disabled_panels]
    if not {"Storage", "Voice", "Email"} & enabled_service_names:
        for panel in dashboard["panels"]:
            if panel["gridPos"]["y"] >= 6:
                panel["gridPos"]["y"] -= 2
    dashboard["version"] = int(dashboard.get("version", 0)) + 1
    atomic_write(dashboard_path, json.dumps(dashboard, indent=2, sort_keys=True) + "\n")
    return dashboard
