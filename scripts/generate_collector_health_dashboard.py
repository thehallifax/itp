#!/usr/bin/env python3
"""Generate the stable, vendor-neutral Collector Health dashboard."""
import json
from pathlib import Path

DS = {"type": "influxdb", "uid": "ffsu5ap2kr5dse"}


def target(sql):
    return {"datasource": DS, "format": "table", "query": sql, "rawSql": sql,
            "rawQuery": True, "refId": "A"}


def stat(panel_id, title, x, sql, unit="short", color="blue",
         warning_at=None):
    steps = [{"color": color, "value": None}]
    if warning_at is not None:
        steps.append({"color": "orange", "value": warning_at})
    return {"id": panel_id, "type": "stat", "title": title,
        "gridPos": {"x": x, "y": 0, "w": 6, "h": 5}, "datasource": DS,
        "targets": [target(sql)], "fieldConfig": {"defaults": {
            "unit": unit, "color": {"mode": "thresholds"}, "mappings": [],
            "thresholds": {"mode": "absolute", "steps": steps},
            "noValue": "No run in selected range"}, "overrides": []},
        "options": {"colorMode": "background", "graphMode": "none",
            "justifyMode": "center", "orientation": "auto",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "showPercentChange": False, "textMode": "value", "wideLayout": True}}


def build():
    selected = ("FROM collector_health WHERE collector LIKE ${collector:sqlstring} "
                "AND site LIKE ${site:sqlstring} AND time >= $__timeFrom AND time <= $__timeTo")
    latest = ("WITH latest AS (SELECT *, ROW_NUMBER() OVER "
              "(PARTITION BY collector ORDER BY time DESC) AS rn " + selected + ") ")
    panels = [
        stat(1, "Collectors Healthy", 0, latest +
             "SELECT SUM(CASE WHEN success THEN 1 ELSE 0 END) AS "
             "\"Healthy Collectors\" FROM latest WHERE rn = 1", color="green"),
        stat(2, "Collectors Requiring Attention", 6, latest +
             "SELECT SUM(CASE WHEN success THEN 0 ELSE 1 END) AS "
             "\"Collectors Requiring Attention\" FROM latest WHERE rn = 1",
             color="green", warning_at=1),
        stat(3, "Latest Duration", 12,
             "SELECT duration_ms AS \"Collection Duration\" " + selected +
             " ORDER BY time DESC LIMIT 1", "ms"),
        stat(4, "Latest Points Written", 18,
             "SELECT points_written AS \"Points Written\" " + selected +
             " ORDER BY time DESC LIMIT 1"),
        {"id": 5, "type": "table", "title": "Collector Runs",
         "gridPos": {"x": 0, "y": 5, "w": 24, "h": 12}, "datasource": DS,
         "targets": [target("SELECT time AS \"Time\", collector AS \"Collector\", "
            "site AS \"Site\", success AS \"Success\", partial AS \"Partial\", "
            "duration_ms AS \"Collection Duration\", api_requests AS \"API Requests\", "
            "points_written AS \"Points Written\", retry_count AS \"Retries\", "
            "error_count AS \"Errors\", diagnostic_category AS \"Result\" "
            + selected + " ORDER BY time DESC LIMIT 200")],
         "fieldConfig": {"defaults": {"custom": {"align": "auto",
             "cellOptions": {"type": "auto"}, "filterable": True}}, "overrides": []},
         "options": {"cellHeight": "sm", "footer": {"show": False}, "showHeader": True}},
    ]
    variable = lambda name, label, query: {"name": name, "label": label, "type": "query",
        "datasource": DS, "query": query, "definition": query, "refresh": 1, "sort": 1,
        "regex": "", "includeAll": True, "allValue": "'%'", "options": [],
        "current": {"text": "All", "value": "$__all"}}
    return {"annotations": {"list": []}, "description":
        "Vendor-neutral health and execution diagnostics for enabled ITP collectors.",
        "editable": False, "fiscalYearStartMonth": 0, "graphTooltip": 1, "id": None,
        "links": [], "panels": panels, "refresh": "1m", "schemaVersion": 41,
        "tags": ["itp", "itp-managed", "collectors", "health"],
        "templating": {"list": [
            variable("collector", "Collector",
                "SELECT DISTINCT collector FROM collector_health ORDER BY collector"),
            variable("site", "Site", "SELECT DISTINCT site FROM collector_health "
                "WHERE collector LIKE ${collector:sqlstring} ORDER BY site")]},
        "time": {"from": "now-24h", "to": "now"}, "timepicker": {},
        "timezone": "browser", "title": "Collector Health",
        "uid": "itp-collector-health", "version": 1, "weekStart": ""}


if __name__ == "__main__":
    path = Path("dashboards/Collectors/collector-health.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build(), indent=2, sort_keys=True) + "\n")
    print(path)
