#!/usr/bin/env python3
"""Generate the classic single-screen Operations Wallboard template."""
import json
from pathlib import Path

DS = {"type": "grafana-testdata-datasource", "uid": "itp-runtime-values"}


def panel(panel_id, title, kind, x, y, w, h, description=""):
    value = {"id": panel_id, "title": title, "type": kind,
        "description": description, "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "datasource": DS, "targets": [], "fieldConfig": {"defaults": {}, "overrides": []}}
    if kind == "text": value["options"] = {"mode": "markdown", "content": ""}
    elif kind == "table":
        value["options"] = {"cellHeight": "sm", "footer": {"show": False}, "showHeader": True}
    elif kind == "stat":
        value["options"] = {"colorMode": "background", "graphMode": "none",
            "justifyMode": "center", "orientation": "auto",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "showPercentChange": False, "textMode": "value_and_name", "wideLayout": True}
    return value


def build():
    panels = [
        panel(1, "Site Operational Status", "text", 0, 0, 8, 2),
        panel(2, "Overall State", "stat", 8, 0, 4, 2),
        panel(3, "Last Service Health", "stat", 12, 0, 6, 2),
        panel(4, "Data Freshness", "stat", 18, 0, 6, 2),
    ]
    for offset, name in enumerate((
            "Internet", "Wireless", "Switching", "Printing",
            "Identity", "Compute", "Security", "Monitoring")):
        panels.append(panel(5 + offset, f"{name} Service", "stat",
                            offset * 3, 2, 3, 2))
    for offset, name in enumerate(("Storage", "Voice", "Email")):
        panels.append(panel(22 + offset, f"{name} Service", "stat",
                            offset * 3, 4, 3, 2))
    panels.extend([
        panel(13, "Wireless Access Points", "table", 0, 6, 6, 3),
        panel(14, "Switches", "table", 6, 6, 6, 3),
        panel(15, "Servers", "table", 12, 6, 6, 3),
        panel(16, "Firewalls", "table", 18, 6, 6, 3),
        panel(17, "Internet / WAN", "table", 0, 9, 7, 4),
        panel(18, "WAN Traffic", "timeseries", 7, 9, 7, 4,
              "RX/TX is shown only for authoritatively classified WAN uplinks."),
        panel(19, "Printer Action Required", "table", 14, 9, 5, 4),
        panel(20, "Collector State", "table", 19, 9, 5, 4),
        panel(21, "Action Required", "table", 0, 13, 24, 5),
    ])
    panels[17]["options"] = {"legend": {"displayMode": "list", "placement": "bottom",
        "showLegend": True}, "tooltip": {"mode": "multi", "sort": "desc"}}
    return {"annotations": {"list": []}, "description":
        "Single-screen, exception-driven, capability-aware infrastructure operations wallboard.",
        "editable": False, "fiscalYearStartMonth": 0, "graphTooltip": 1, "id": None,
        "links": [], "panels": panels, "refresh": "1m", "schemaVersion": 41,
        "tags": ["itp", "itp-managed", "operations", "wallboard", "exception-driven"],
        "templating": {"list": [{"name": "site", "label": "Site", "type": "custom",
            "query": "", "current": {"selected": True, "text": "All Sites", "value": "all"},
            "options": [{"selected": True, "text": "All Sites", "value": "all"},
            ], "includeAll": False, "hide": 0}]},
        "time": {"from": "now-6h", "to": "now"}, "timepicker": {},
        "timezone": "browser", "title": "Operations Wallboard",
        "uid": "itp-operations-wallboard", "version": 1, "weekStart": ""}


if __name__ == "__main__":
    path = Path("dashboards/Operations/operations-wallboard.json")
    path.write_text(json.dumps(build(), indent=2, sort_keys=True) + "\n")
    print(path)
