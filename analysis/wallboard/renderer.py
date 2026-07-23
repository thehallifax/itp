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
                 "Unknown": "gray", "Awaiting telemetry": "gray"}


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
    panel["transformations"] = _scope_filter() + [{"id": "organize", "options": {
        "excludeByName": {"scope": True}, "renameByName": {"value": field.replace("_", " ").title()}}}]
    panel["fieldConfig"] = {"defaults": {"color": {"mode": "thresholds"},
        "mappings": _mapping() if health else [],
        "thresholds": {"mode": "absolute", "steps": [{"color": "green" if health else "blue", "value": None}]}},
        "overrides": []}


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
    variable["options"] = [{"selected": True, "text": "All Sites", "value": "all"}] + [
        {"selected": False, "text": value["display_name"], "value": value["site_id"]}
        for value in summary["site_options"]]
    variable["query"] = ",".join(
        str(value["display_name"]).replace(",", "\\,") + " : " + value["site_id"]
        for value in summary["site_options"])
    panels = {value["title"]: value for value in dashboard["panels"]}
    panels["Operations Wallboard"]["options"]["content"] = (
        f"# Operations Wallboard\n\n**Scope:** ${{site:text}}  •  "
        f"**Generated:** {summary['generated_at']}  •  "
        f"**Freshness:** {summary['freshness']['status']} "
        f"({summary['freshness']['age_seconds'] if summary['freshness']['age_seconds'] is not None else 'N/A'}s)")
    for title, field, health in (
        ("Infrastructure Health", "infrastructure_health", True),
        ("Observability Health", "observability_health", True),
        ("Sites", "sites", False), ("Devices Online", "online", False),
        ("Devices Offline", "offline", False), ("Actionable Warnings", "actionable_warnings", False),
        ("Active Issues", "active_issues", False), ("Collectors Healthy", "collectors_healthy", False)):
        _stat(panels[title], summary["scopes"], field, health)
    for title, name, fields in (
        ("Network", "network", ("scope", "Total", "Online", "Offline", "Warning", "Unknown")),
        ("Wireless", "wireless", ("scope", "Total", "Online", "Offline", "Warning", "Clients", "Failed Auth")),
        ("Security and Edge", "security", ("scope", "Total", "Online", "Offline", "Warning", "WAN")),
        ("Compute", "compute", ("scope", "Total", "Online", "Offline", "Unknown")),
        ("Printing", "printing", ("scope", "Total", "Online", "Offline", "Warning", "Consumables"))):
        _table(panels[title], _domain_rows(summary, name), fields)
    service_rows = [{"scope": scope["scope"], **{
        name: "Awaiting telemetry" for name in summary["services"]}} for scope in summary["scopes"]]
    _table(panels["Services"], service_rows, ("scope", *summary["services"]))
    _topology(panels["Logical Infrastructure View"], summary["topology"])
    for title, kind, fields in (
        ("Top Active Issues", "issues", ("scope", "priority", "severity", "category", "title", "device", "site")),
        ("Top Operational Risks", "risks", ("scope", "priority", "severity", "category", "title", "device", "site")),
        ("Top Recommendations", "recommendations", ("scope", "priority", "impact", "title", "reason", "suggested_action"))):
        _table(panels[title], summary["attention"][kind], fields)
    freshness = summary["freshness"]
    collector_rows = []
    for value in summary["collectors"]:
        last = value.get("last_run"); parsed = None
        try:
            from datetime import datetime, timezone
            parsed = datetime.fromisoformat(str(last).replace("Z", "+00:00")).astimezone(timezone.utc)
            age = max(0, int((datetime.fromisoformat(summary["generated_at"].replace("Z", "+00:00")) - parsed).total_seconds()))
        except (TypeError, ValueError):
            age = None
        collector_rows.append({**value, "data_age_seconds": age})
    _table(panels["Collector Health and Data Freshness"], collector_rows,
        ("collector", "status", "last_run", "last_successful_run", "duration_ms", "failures", "data_age_seconds"),
        scoped=False)
    panels["Collector Health and Data Freshness"]["fieldConfig"]["overrides"] = [{
        "matcher": {"id": "byName", "options": "status"}, "properties": [
            {"id": "mappings", "value": [{"type": "value", "options": {
                "healthy": {"color": "green", "index": 0, "text": "Healthy"},
                "failed": {"color": "red", "index": 1, "text": "Failed"},
                "unknown": {"color": "gray", "index": 2, "text": "Unknown"}}}]},
            {"id": "custom.cellOptions", "value": {"type": "color-background"}},
        ]}]
    for title in ("Primary and Secondary WAN Traffic", "Core and Internet-Bound Traffic", "WAN Quality"):
        panels[title]["options"]["content"] = (
            "## Awaiting telemetry\n\nNo reliable WAN interface classification or path time series is available. "
            "No interface or value has been inferred.")
    dashboard["version"] = int(dashboard.get("version", 0)) + 1
    atomic_write(dashboard_path, json.dumps(dashboard, indent=2, sort_keys=True) + "\n")
    return dashboard
