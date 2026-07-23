"""Atomic JSON/CSV output and classic-dashboard rendering."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from collectors.writer import atomic_write


CSV_FIELDS = ("kind", "id", "rule_id", "title", "category", "severity", "priority",
              "device", "site", "summary", "impact", "reason", "suggested_action")
PANEL_TITLES = {"issues": "Active Issues", "risks": "Operational Risks",
                "recommendations": "Recommendations"}
METRIC_PANELS = {
    "Infrastructure Health": ("infrastructure_health", "critical / warnings", ("critical", "warnings")),
    "Devices Online": ("devices_online", "devices total", "devices"),
    "Devices Offline": ("devices_offline", "devices total", "devices"),
    "Critical Alerts": ("critical", "devices total", "devices"),
    "Warnings": ("warnings", "devices total", "devices"),
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


def _metric_content(title, definition, summary):
    primary, label, secondary = definition; value = summary.get(primary)
    if value is None: return f"## {title}\n\nState unavailable"
    if isinstance(secondary, tuple):
        detail = " / ".join(str(summary.get(key, 0)) for key in secondary)
    else: detail = str(summary.get(secondary, 0))
    return f"# {value}\n\n**{label}:** {detail}"


def render_dashboard(template_path, output_path, result, infrastructure_summary=None):
    dashboard = json.loads(Path(template_path).read_text())
    infrastructure_summary = infrastructure_summary or {}
    for title, definition in METRIC_PANELS.items():
        panel = next((value for value in dashboard.get("panels", []) if value.get("title") == title), None)
        if panel is None: raise ValueError(f"dashboard template is missing {title} panel")
        panel["type"] = "text"; panel.pop("fieldConfig", None); panel.pop("targets", None)
        panel["description"] = "Generated from runtime/dashboard/infrastructure-summary.json."
        panel["options"] = {"mode": "markdown", "content": _metric_content(title, definition, infrastructure_summary)}
    for collection, title in PANEL_TITLES.items():
        panel = next((value for value in dashboard.get("panels", []) if value.get("title") == title), None)
        if panel is None: raise ValueError(f"dashboard template is missing {title} panel")
        panel["description"] = f"Generated from operations.json by deterministic rules at {result['generated_at']}."
        panel.setdefault("options", {})["content"] = _markdown(title, result[collection])
    dashboard["version"] = int(dashboard.get("version", 0)) + 1
    atomic_write(output_path, json.dumps(dashboard, indent=2, sort_keys=True) + "\n")
