"""Atomic JSON/CSV output and classic-dashboard rendering."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from collectors.writer import atomic_write


CSV_FIELDS = ("kind", "id", "rule_id", "title", "category", "severity", "priority",
              "canonical_id", "device", "site_id", "site", "summary", "impact", "reason", "suggested_action")
PANEL_TITLES = {"issues": "Active Issues", "risks": "Operational Risks",
                "recommendations": "Recommendations"}
METRIC_PANELS = {
    "Sites": ("sites", "canonical sites", "sites"),
    "Healthy Sites": ("healthy_sites", "canonical sites", "sites"),
    "Warning Sites": ("warning_sites", "canonical sites", "sites"),
    "Critical Sites": ("critical_sites", "canonical sites", "sites"),
    "Infrastructure Health": ("infrastructure_health", "critical / warnings", ("critical", "warnings")),
    "Observability Health": ("observability_health", "healthy / failed collectors", ("collectors_healthy", "collectors_failed")),
    "Devices Online": ("devices_online", "devices total", "devices"),
    "Devices Offline": ("devices_offline", "devices total", "devices"),
    "Actionable Warnings": ("actionable_warnings", "data quality findings", "data_quality_findings"),
    "Collectors Healthy": ("collectors_healthy", "collectors failed", "collectors_failed"),
    "Switches": ("switches_total", "online / offline", ("switches_online", "switches_offline")),
    "Access Points": ("aps_total", "online / offline", ("aps_online", "aps_offline")),
    "Firewalls": ("firewalls_total", "healthy / offline", ("firewalls_healthy", "firewalls_offline")),
    "Servers": ("servers_total", "healthy / offline", ("servers_healthy", "servers_offline")),
    "Printers": ("printers_total", "healthy / offline", ("printers_healthy", "printers_offline")),
}


def write_outputs(output_dir, result):
    output_dir = Path(output_dir); output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(output_dir / "operations.json", json.dumps(result, indent=2, sort_keys=True) + "\n")
    stream = io.StringIO(); writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for kind in ("issues", "risks", "recommendations"):
        for value in result[kind]: writer.writerow({"kind": kind[:-1], **value})
    atomic_write(output_dir / "operations.csv", stream.getvalue())


def _markdown(title, items):
    if not items:
        return f"## {title}\n\nNo current items were produced by the enabled deterministic rules."
    lines = [f"## Top 10 {title}", "", "| Priority | Severity | Item | Device / Site |", "| ---: | --- | --- | --- |"]
    for value in items[:10]:
        location = " / ".join(part for part in (value.get("device"), value.get("site")) if part) or "—"
        title_value = str(value["title"]).replace("|", "\\|")
        location = location.replace("|", "\\|")
        lines.append(f"| **{value['priority']}** | {value['severity']} | {title_value} | {location} |")
    return "\n".join(lines)


def _finding_table(panel, title, items, site_values):
    scopes = [("all", "All"), *[(value["site_id"], value["display_name"])
                                for value in site_values]]
    rows = []
    for scope, _ in scopes:
        selected = items if scope == "all" else [
            value for value in items if value.get("site_id") == scope]
        if selected:
            for value in selected[:10]:
                rows.append({"scope": scope, "priority": value.get("priority", 0),
                    "severity": value.get("severity", "Info"),
                    "item": value.get("title", ""),
                    "device": value.get("device", ""),
                    "site": value.get("site", "")})
        else:
            rows.append({"scope": scope, "priority": "", "severity": "Info",
                "item": f"No current {title.lower()}", "device": "", "site": ""})
    stream = io.StringIO()
    fields = ("scope", "priority", "severity", "item", "device", "site")
    writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader()
    for row in rows: writer.writerow(row)
    datasource = {"type": "grafana-testdata-datasource", "uid": "itp-runtime-values"}
    panel["type"] = "table"; panel["datasource"] = datasource
    panel["targets"] = [{"refId": "A", "scenarioId": "csv_content",
        "csvContent": stream.getvalue().rstrip(), "datasource": datasource}]
    panel["transformations"] = [{"id": "filterByValue", "options": {"filters": [{
        "config": {"id": "equal", "options": {"value": "${site}"}},
        "fieldName": "scope"}], "match": "all", "type": "include"}},
        {"id": "organize", "options": {"excludeByName": {"scope": True}}}]
    panel["fieldConfig"] = {"defaults": {"custom": {"align": "auto",
        "cellOptions": {"type": "auto"}, "filterable": False,
        "inspect": False}}, "overrides": []}
    panel["options"] = {"cellHeight": "sm", "footer": {"show": False},
                        "showHeader": True}


def _metric_content(title, definition, summary):
    primary, label, secondary = definition; value = summary.get(primary)
    if value is None: return f"## {title}\n\nState unavailable"
    if isinstance(secondary, tuple):
        detail = " / ".join(str(summary.get(key, 0)) for key in secondary)
    else: detail = str(summary.get(secondary, 0))
    return f"# {value}\n\n**{label}:** {detail}"


def _stat_panel(panel, title, definition, summary):
    primary, label, secondary = definition; value = summary.get(primary)
    if isinstance(secondary, tuple): detail = " / ".join(str(summary.get(key, 0)) for key in secondary)
    else: detail = str(summary.get(secondary, 0))
    if title == "Devices Online" and summary.get("devices"):
        detail += f" ({100 * value / summary['devices']:.1f}%)"
    if title == "Collectors Healthy":
        total = summary.get("collectors_healthy", 0) + summary.get("collectors_failed", 0)
        if total: detail += f" ({100 * value / total:.1f}% healthy)"
    panel["type"] = "stat"
    panel["datasource"] = {"type": "grafana-testdata-datasource", "uid": "itp-runtime-values"}
    stream = io.StringIO()
    if summary.get("scopes"):
        writer = csv.DictWriter(stream, fieldnames=("scope", "value")); writer.writeheader()
        for scope in summary["scopes"]:
            writer.writerow({"scope": scope["scope"],
                             "value": scope.get(primary) if scope.get(primary) is not None else "Unknown"})
        csv_content = stream.getvalue().rstrip()
    else:
        csv_content = "value\n" + (str(value) if value is not None else "Unknown")
    panel["targets"] = [{"refId": "A", "scenarioId": "csv_content",
        "csvContent": csv_content,
        "datasource": panel["datasource"]}]
    panel["transformations"] = ([{"id": "filterByValue", "options": {"filters": [{
        "config": {"id": "equal", "options": {"value": "${site}"}}, "fieldName": "scope"}],
        "match": "all", "type": "include"}}, {"id": "organize", "options": {
        "excludeByName": {"scope": True}}}] if summary.get("scopes") else [])
    panel["description"] = ("Generated from runtime/dashboard/infrastructure-summary.json. "
                            f"{label.capitalize()}: {detail}.")
    health_mapping = {name: {"color": color, "index": index, "text": name}
        for index, (name, color) in enumerate((("Healthy", "green"), ("Warning", "orange"),
                                               ("Critical", "red"), ("Unknown", "gray")))}
    concerning = title in {"Devices Offline", "Actionable Warnings", "Warning Sites", "Critical Sites"}
    panel["fieldConfig"] = {"defaults": {
        "color": {"mode": "thresholds"},
        "mappings": [{"type": "value", "options": health_mapping}]
            if title in {"Infrastructure Health", "Observability Health"} else [],
        "thresholds": {"mode": "absolute", "steps": ([{"color": "green", "value": None},
            {"color": "red" if title in {"Devices Offline", "Critical Sites"} else "orange", "value": 1}]
            if concerning else [{"color": "green" if title in {"Devices Online", "Collectors Healthy"}
                                  else "blue", "value": None}])}}, "overrides": []}
    panel["options"] = {"colorMode": "background" if title in {
        "Infrastructure Health", "Observability Health", "Devices Offline", "Actionable Warnings"} else "value",
        "graphMode": "none", "justifyMode": "auto", "orientation": "auto",
        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
        "textMode": "auto", "wideLayout": True}


def render_dashboard(template_path, output_path, result, infrastructure_summary=None):
    dashboard = json.loads(Path(template_path).read_text())
    infrastructure_summary = infrastructure_summary or {}
    site_values = infrastructure_summary.get("site_options", [])
    variable = next((value for value in dashboard.get("templating", {}).get("list", [])
                     if value.get("name") == "site"), None)
    if variable is not None:
        if len(site_values) == 1:
            options = [{"selected": True, "text": site_values[0]["display_name"],
                        "value": site_values[0]["site_id"]}]
        else:
            options = [{"selected": True, "text": "All Sites", "value": "all"}] + [
                {"selected": False, "text": value["display_name"], "value": value["site_id"]}
                for value in site_values]
        variable["options"] = options
        variable["query"] = ",".join(
            str(value["display_name"]).replace(",", "\\,") + " : " + value["site_id"]
            for value in site_values)
    for title, definition in METRIC_PANELS.items():
        panel = next((value for value in dashboard.get("panels", []) if value.get("title") == title), None)
        if panel is None: raise ValueError(f"dashboard template is missing {title} panel")
        _stat_panel(panel, title, definition, infrastructure_summary)
    for collection, title in PANEL_TITLES.items():
        panel = next((value for value in dashboard.get("panels", []) if value.get("title") == title), None)
        if panel is None: raise ValueError(f"dashboard template is missing {title} panel")
        panel["description"] = f"Generated from operations.json by deterministic rules at {result['generated_at']}."
        _finding_table(panel, title, result[collection], site_values)
    dashboard["version"] = int(dashboard.get("version", 0)) + 1
    atomic_write(output_path, json.dumps(dashboard, indent=2, sort_keys=True) + "\n")
