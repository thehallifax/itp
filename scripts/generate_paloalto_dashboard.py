#!/usr/bin/env python3
"""Generate the classic Grafana PAN-OS operational dashboard."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

DATASOURCE = {"type": "influxdb", "uid": "ffsu5ap2kr5dse"}


def target(sql, ref="A"):
    return {"datasource": DATASOURCE, "format": "table", "rawQuery": True,
            "rawSql": sql, "query": sql, "refId": ref}


def stat(panel_id, title, x, y, w, sql, *, unit="short", mappings=None,
         thresholds=None, description="", string_field=None, show_name=True):
    return {"id": panel_id, "type": "stat", "title": title, "description": description,
        "gridPos": {"x": x, "y": y, "w": w, "h": 4}, "datasource": DATASOURCE,
        "targets": [target(sql)], "transformations": [],
        "fieldConfig": {"defaults": {
            "color": {"mode": "thresholds"}, "unit": unit, "mappings": mappings or [],
            **({"noValue": "No data"} if string_field else {}),
            "thresholds": thresholds or {"mode": "absolute", "steps": [
                {"color": "green", "value": None}]}}, "overrides": []},
        "options": {"colorMode": "none" if string_field else "background",
            "graphMode": "none",
            "justifyMode": "center", "orientation": "auto",
            "reduceOptions": {
                "calcs": [] if string_field else ["lastNotNull"],
                "fields": f"/^{string_field}$/" if string_field else "",
                "values": bool(string_field),
            },
            "showPercentChange": False,
            "textMode": "value" if string_field or not show_name
            else "value_and_name",
            "wideLayout": True}}


def text(panel_id, title, x, y, w, h, body, description=""):
    return {"id": panel_id, "type": "text", "title": title, "description": description,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "options": {"mode": "markdown", "content": body}}


def table(panel_id, title, x, y, w, h, sql, description=""):
    return {"id": panel_id, "type": "table", "title": title, "description": description,
        "gridPos": {"x": x, "y": y, "w": w, "h": h}, "datasource": DATASOURCE,
        "targets": [target(sql)], "fieldConfig": {"defaults": {
            "custom": {"align": "auto", "cellOptions": {"type": "auto"},
                       "filterable": True, "inspect": False},
            "mappings": [], "thresholds": {"mode": "absolute",
                "steps": [{"color": "green", "value": None}]}}, "overrides": []},
        "options": {"cellHeight": "sm", "footer": {"countRows": False,
            "fields": "", "reducer": ["sum"], "show": False}, "showHeader": True}}


def timeseries(panel_id, title, x, y, w, h, sql, *, unit="short", description=""):
    return {"id": panel_id, "type": "timeseries", "title": title,
        "description": description, "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "datasource": DATASOURCE, "targets": [target(sql)],
        "fieldConfig": {"defaults": {"unit": unit,
            "color": {"mode": "palette-classic"},
            "custom": {"drawStyle": "line", "lineInterpolation": "linear",
                "lineWidth": 1, "fillOpacity": 12, "showPoints": "never",
                "spanNulls": False}}, "overrides": []},
        "options": {"legend": {"displayMode": "table", "placement": "bottom",
            "calcs": ["lastNotNull"]}, "tooltip": {"mode": "multi", "sort": "desc"}}}


def row(panel_id, title, y):
    return {"id": panel_id, "type": "row", "title": title, "collapsed": False,
            "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []}


def where(table_name="device"):
    return (f"FROM {table_name} WHERE collector = 'paloalto' "
            "AND customer LIKE ${customer:sqlstring} "
            "AND site LIKE ${site:sqlstring} "
            "AND hostname LIKE ${device:sqlstring}")


def latest(column, table_name="device"):
    return (f"SELECT {column} {where(table_name)} "
            "AND time >= $__timeFrom AND time <= $__timeTo ORDER BY time DESC LIMIT 1")


def unavailable(title, detail):
    return (f"### {title}: not collected\n\n{detail}\n\n"
            "No query is issued because the current canonical Palo Alto measurements "
            "do not contain the required fields.")


def build():
    panels = []; pid = 1
    def add(value):
        nonlocal pid
        value["id"] = pid; pid += 1; panels.append(value)

    add(row(0, "Overview", 0))
    add(stat(0, "Hostname", 0, 1, 6, latest('hostname AS "Hostname"'),
             unit="string", string_field="Hostname"))
    add(stat(0, "Model", 6, 1, 6, latest('model AS "Model"'),
             unit="string", string_field="Model"))
    add(stat(0, "PAN-OS Version", 12, 1, 6,
             latest('firmware AS "PAN-OS Version"'), unit="string",
             string_field="PAN-OS Version"))
    add(stat(0, "Uptime", 18, 1, 6, latest("uptime_seconds AS uptime"),
             unit="s", description="Latest uptime returned by PAN-OS."))

    add(row(0, "Health", 5))
    add(stat(0, "HA Status", 0, 6, 6,
        latest('ha_status AS "HA Status"', "firewall"), unit="string",
        string_field="HA Status"))
    collector_status = ("SELECT CASE WHEN success THEN 1 ELSE 0 END AS collector_status "
        "FROM collector_health WHERE collector = 'paloalto' "
        "AND site LIKE ${site:sqlstring} AND time >= $__timeFrom AND time <= $__timeTo "
        "ORDER BY time DESC LIMIT 1")
    add(stat(0, "Collector Status", 6, 6, 6, collector_status,
        mappings=[{"type": "value", "options": {
            "0": {"color": "red", "index": 0, "text": "Failed"},
            "1": {"color": "green", "index": 1, "text": "Healthy"}}}],
        thresholds={"mode": "absolute", "steps": [{"color": "red", "value": None},
            {"color": "green", "value": 1}]}))
    add(stat(0, "Device Certificate Status", 12, 6, 6,
        latest('device_certificate_status AS "Device Certificate Status"', "firewall"),
        unit="string", string_field="Device Certificate Status"))
    add(stat(0, "Platform Family", 18, 6, 6,
        latest('platform_family AS "Platform Family"'), unit="string",
        string_field="Platform Family"))

    add(row(0, "Resources", 10))
    add(stat(0, "Management CPU", 0, 11, 5,
        latest("management_cpu_percent AS management_cpu", "performance"), unit="percent"))
    add(stat(0, "Data Plane CPU", 5, 11, 5,
        latest("dataplane_cpu_percent AS dataplane_cpu", "performance"), unit="percent"))
    add(stat(0, "Memory Used", 10, 11, 5,
        latest("memory_used_percent AS memory_used", "performance"), unit="percent"))
    add(stat(0, "Active Sessions", 15, 11, 4,
        latest("sessions_active AS active_sessions", "performance")))
    add(stat(0, "Session Utilisation", 19, 11, 5,
        latest("session_utilisation_percent AS session_utilisation", "performance"),
        unit="percent"))

    add(row(0, "Interfaces", 16))
    interface_sql = ("WITH latest AS (SELECT interface_name, admin_status, operational_status, "
        "speed, duplex, logical, wan_classified, wan_role, wan_display_name, "
        "rx_errors_total, rx_discards_total, time, ROW_NUMBER() OVER "
        "(PARTITION BY hostname, interface_name ORDER BY time DESC) AS rn "
        f"{where('interface')} AND time >= $__timeFrom AND time <= $__timeTo) "
        "SELECT interface_name AS \"Interface\", admin_status AS \"Admin\", "
        "operational_status AS \"Operational\", speed AS \"Speed\", duplex AS \"Duplex\", "
        "logical AS \"Logical\", wan_display_name AS \"WAN Name\", wan_role AS \"WAN Role\", "
        "rx_errors_total AS \"RX Errors\", rx_discards_total AS \"RX Discards\", "
        "time AS \"Observed\" FROM latest WHERE rn = 1 "
        "ORDER BY interface_name")
    add(table(0, "Interface Status", 0, 17, 24, 8, interface_sql,
        "Latest state per selected firewall and interface. Blank logical-interface state is unavailable."))
    traffic_sql = ("WITH samples AS (SELECT time, rx_bytes_total, "
        "tx_bytes_total, LAG(time) OVER (ORDER BY time) "
        "AS previous_time, LAG(rx_bytes_total) OVER (ORDER BY time) "
        "AS previous_rx, LAG(tx_bytes_total) OVER (ORDER BY time) AS previous_tx "
        f"{where('interface')} AND interface_name = ${{wan_interface:sqlstring}} "
        "AND wan_classified = true AND time >= $__timeFrom "
        "AND time <= $__timeTo) SELECT time, "
        "CASE WHEN rx_bytes_total >= previous_rx AND "
        "EXTRACT(EPOCH FROM (time - previous_time)) > 0 "
        "THEN (rx_bytes_total - previous_rx) * 8 / "
        "EXTRACT(EPOCH FROM (time - previous_time)) END "
        "AS \"Download\", CASE WHEN tx_bytes_total >= previous_tx AND "
        "EXTRACT(EPOCH FROM (time - previous_time)) > 0 "
        "THEN (tx_bytes_total - previous_tx) * 8 / "
        "EXTRACT(EPOCH FROM (time - previous_time)) END AS \"Upload\" FROM samples "
        "WHERE previous_time IS NOT NULL ORDER BY time")
    wan_panel = timeseries(
        0, "${wan_interface:text}", 0, 25, 12, 9, traffic_sql, unit="bps",
        description="Per-interface rates derived from cumulative counters. "
        "Download and Upload current values are shown in the legend; negative "
        "deltas after reset are omitted.")
    wan_panel.update({
        "repeat": "wan_interface",
        "repeatDirection": "h",
        "maxPerRow": 2,
    })
    add(wan_panel)
    fault_sql = ("SELECT time, interface_name, rx_errors_total, tx_errors_total, "
        "rx_discards_total "
        f"{where('interface')} AND time >= $__timeFrom AND time <= $__timeTo "
        "ORDER BY time")
    add(timeseries(0, "Interface Fault Counters", 0, 34, 24, 8, fault_sql,
        description="Cumulative counters. Missing PAN-OS counters remain null."))

    add(row(0, "Licensing", 42))
    license_sql = ("WITH latest AS (SELECT subscription_name, status, expiry_date, "
        "remaining_days, expired_days, expired, perpetual, expiry_state, time, "
        "ROW_NUMBER() OVER (PARTITION BY hostname, subscription_name ORDER BY time DESC) AS rn "
        f"{where('license')} AND time >= $__timeFrom AND time <= $__timeTo) "
        "SELECT subscription_name AS \"Subscription\", expiry_state AS \"State\", "
        "status AS \"Status\", expiry_date AS \"Expiry\", remaining_days AS \"Days Remaining\", "
        "expired_days AS \"Days Expired\", perpetual AS \"Perpetual\", time AS \"Observed\" "
        "FROM latest WHERE rn = 1 ORDER BY subscription_name")
    add(table(0, "Subscriptions and Expiry", 0, 43, 24, 6, license_sql))

    add(row(0, "Content Updates", 49))
    content_sql = ("WITH latest AS (SELECT package_name, version, release_time, "
        "release_time_raw, age_days, time, ROW_NUMBER() OVER "
        "(PARTITION BY hostname, package_name ORDER BY time DESC) AS rn "
        f"{where('content_package')} AND time >= $__timeFrom AND time <= $__timeTo) "
        "SELECT package_name AS \"Package\", version AS \"Version\", "
        "release_time AS \"Release Time\", age_days AS \"Age (days)\", "
        "release_time_raw AS \"Raw Release Time\", time AS \"Observed\" "
        "FROM latest WHERE rn = 1 ORDER BY package_name")
    add(table(0, "Installed Content Packages", 0, 50, 24, 7, content_sql))

    add(row(0, "Inventory", 57))
    inventory_sql = ("WITH latest AS (SELECT hostname, serial, model, management_ip, "
        "firmware, platform, platform_family, time, "
        "ROW_NUMBER() OVER (PARTITION BY hostname ORDER BY time DESC) AS rn "
        f"{where('device')} AND time >= $__timeFrom AND time <= $__timeTo) "
        "SELECT hostname AS \"Hostname\", serial AS \"Serial\", model AS \"Model\", "
        "management_ip AS \"Management IP\", firmware AS \"Software Version\", "
        "platform AS \"Platform\", platform_family AS \"Platform Family\", "
        "time AS \"Observed\" FROM latest WHERE rn = 1 ORDER BY hostname")
    add(table(0, "Firewall Inventory", 0, 58, 24, 6, inventory_sql,
        "Management IP is explicit unavailable text because it is not present in the device measurement."))

    add(row(0, "Collector Diagnostics", 64))
    health_filter = ("FROM collector_health WHERE collector = 'paloalto' "
        "AND site LIKE ${site:sqlstring} AND time >= $__timeFrom AND time <= $__timeTo "
        "ORDER BY time DESC LIMIT 1")
    latest_health_filter = ("FROM collector_health WHERE collector = 'paloalto' "
        "AND site LIKE ${site:sqlstring} ORDER BY time DESC LIMIT 1")
    add(stat(0, "Collector Result", 0, 65, 3, collector_status,
        mappings=[{"type": "value", "options": {
            "0": {"color": "red", "index": 0, "text": "Failed"},
            "1": {"color": "green", "index": 1, "text": "Successful"}}}],
        thresholds={"mode": "absolute", "steps": [{"color": "red", "value": None},
            {"color": "green", "value": 1}]}, show_name=False))
    add(stat(0, "Last Collection", 3, 65, 3,
             'SELECT CAST(time AS VARCHAR) AS "Last Collection" '
             + latest_health_filter,
             unit="string", string_field="Last Collection",
             show_name=False))
    add(stat(0, "Collection Duration", 6, 65, 3,
             "SELECT duration_ms AS collection_duration " + health_filter,
             unit="ms", show_name=False))
    add(stat(0, "Points Written", 9, 65, 3,
             "SELECT points_written AS points_written " + health_filter,
             show_name=False))
    add(stat(0, "API Requests", 12, 65, 3,
        "SELECT api_requests " + health_filter, show_name=False))
    add(stat(0, "Max API Duration", 15, 65, 3,
        "SELECT api_duration_ms_max " + health_filter,
        unit="ms", show_name=False))
    add(stat(0, "Partial", 18, 65, 3,
        "SELECT CASE WHEN partial THEN 1 ELSE 0 END AS partial " + health_filter,
        show_name=False))
    add(stat(0, "Errors", 21, 65, 3,
        "SELECT error_count " + health_filter, show_name=False))

    variable = lambda name, label, query, depends=False: {
        "name": name, "label": label, "type": "query", "datasource": DATASOURCE,
        "query": query, "definition": query, "refresh": 1, "sort": 1, "regex": "",
        "includeAll": True, "allValue": "'%'", "options": [],
        "current": {"text": "All", "value": "$__all"}}
    customer_q = "SELECT DISTINCT customer FROM device WHERE collector = 'paloalto' ORDER BY customer"
    site_q = ("SELECT DISTINCT site FROM device WHERE collector = 'paloalto' "
              "AND customer LIKE ${customer:sqlstring} ORDER BY site")
    device_q = ("SELECT DISTINCT hostname FROM device WHERE collector = 'paloalto' "
                "AND customer LIKE ${customer:sqlstring} AND site LIKE ${site:sqlstring} ORDER BY hostname")
    wan_q = ("SELECT DISTINCT COALESCE(NULLIF(wan_display_name, ''), "
        "INITCAP(wan_role), interface_name) || ' — ' || interface_name AS __text, "
        "interface_name AS __value FROM interface WHERE collector = 'paloalto' "
        "AND customer LIKE ${customer:sqlstring} AND site LIKE ${site:sqlstring} "
        "AND hostname LIKE ${device:sqlstring} AND wan_classified = true ORDER BY __text")
    wan_variable = variable("wan_interface", "WAN Interface", wan_q, True)
    wan_variable.update({
        "includeAll": True,
        "allValue": None,
        "multi": True,
        "hide": 2,
        "current": {"selected": True, "text": "All", "value": "$__all"},
    })
    return {"annotations": {"list": []}, "description":
        "Read-only operational view of canonical Palo Alto Networks PAN-OS telemetry.",
        "editable": True, "fiscalYearStartMonth": 0, "graphTooltip": 1,
        "id": None, "links": [], "panels": panels, "refresh": "2m",
        "schemaVersion": 41, "tags": ["itp", "paloalto", "firewall", "operations"],
        "templating": {"list": [variable("customer", "Customer", customer_q),
            variable("site", "Site", site_q, True),
            variable("device", "Device", device_q, True), wan_variable]},
        "time": {"from": "now-24h", "to": "now"}, "timepicker": {},
        "timezone": "browser", "title": "Palo Alto Operational Overview",
        "uid": "paloalto-operational-overview", "version": 1, "weekStart": ""}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output",
        default="runtime/dashboard/grafana/paloalto-overview.json")
    args = parser.parse_args()
    path = Path(args.output); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build(), indent=2, sort_keys=True) + "\n")
    print(path)


if __name__ == "__main__":
    main()
