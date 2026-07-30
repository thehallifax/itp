import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from analysis.dashboards import DashboardRegistry


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/generate_paloalto_dashboard.py"
SOURCE = ROOT / "dashboards/vendor/paloalto-overview.json"
DATASOURCE_UID = "ffsu5ap2kr5dse"


def module():
    spec = importlib.util.spec_from_file_location("paloalto_dashboard", SCRIPT)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


def generate(path):
    subprocess.run(
        [sys.executable, str(SCRIPT), "--output", str(path)],
        check=True, capture_output=True, text=True)
    return json.loads(path.read_text())


def test_dashboard_is_classic_stable_and_runtime_provisioned(tmp_path):
    dashboard = module().build()
    assert dashboard["uid"] == "paloalto-operational-overview"
    assert dashboard["schemaVersion"] == 41
    assert isinstance(dashboard["panels"], list) and len(dashboard["panels"]) == 42
    assert "elements" not in dashboard and "layout" not in dashboard
    provision = (ROOT / "grafana/provisioning/dashboards/dashboards.yml").read_text()
    assert "/var/lib/grafana/runtime-dashboard/managed/vendor" in provision
    assert generate(tmp_path / "runtime/dashboard/grafana/paloalto-overview.json") == dashboard


def test_every_live_target_uses_confirmed_flightsql_contract():
    dashboard = module().build()
    allowed_tables = {"device", "firewall", "interface", "performance",
                      "license", "content_package", "collector_health"}
    for panel in dashboard["panels"]:
        for target in panel.get("targets", []):
            assert target["datasource"]["uid"] == DATASOURCE_UID
            assert target["rawQuery"] is True and target["format"] == "table"
            assert target["rawSql"] == target["query"]
            assert any(f"FROM {table}" in target["rawSql"] for table in allowed_tables)
            assert "content_version" not in target["rawSql"]


def test_variables_are_sql_safe_and_paloalto_scoped():
    variables = module().build()["templating"]["list"]
    assert [value["name"] for value in variables] == [
        "customer", "site", "device", "wan_interface"]
    assert all(value["allValue"] == "'%'" for value in variables[:3])
    assert variables[3]["includeAll"] is True
    assert variables[3]["multi"] is True
    assert variables[3]["allValue"] is None
    assert variables[3]["hide"] == 2
    assert "__text" in variables[3]["query"]
    assert "__value" in variables[3]["query"]
    assert "wan_classified = true" in variables[3]["query"]
    assert all("collector = 'paloalto'" in value["query"] for value in variables)
    assert all("__text" in value["query"] and "__value" in value["query"]
               for value in variables)
    assert "customer_id AS __value" in variables[0]["query"]
    assert "site_name" in variables[1]["query"]
    assert "site_id AS __value" in variables[1]["query"]
    assert "device_id AS __value" in variables[2]["query"]
    assert "${customer:sqlstring}" in variables[1]["query"]
    assert "${site:sqlstring}" in variables[2]["query"]
    queries = [target["rawSql"] for panel in module().build()["panels"]
               for target in panel.get("targets", [])]
    assert all("'${customer}'" not in query and "'${site}'" not in query
               and "'${device}'" not in query for query in queries)
    assert all("customer LIKE" not in query and "site LIKE" not in query
               and "hostname LIKE" not in query for query in queries)
    assert all("customer_id LIKE ${customer:sqlstring}" in query
               for query in queries)
    assert all("site_id LIKE ${site:sqlstring}" in query
               for query in queries)
    assert all("device_id LIKE ${device:sqlstring}" in query
               for query in queries if "FROM collector_health" not in query)


def test_operational_telemetry_panels_use_live_canonical_measurements():
    dashboard = module().build()
    panels = {panel["title"]: panel for panel in dashboard["panels"]}
    expected = {
        "Device Certificate Status": "FROM firewall",
        "Platform Family": "FROM device",
        "CPU": "FROM performance",
        "Data Plane CPU": "FROM performance",
        "Memory Used": "FROM performance",
        "Active Sessions": "FROM performance",
        "Session Utilisation": "FROM performance",
        "Interface Inventory": "FROM interface",
        "WAN Throughput · ${wan_interface:text}": "FROM interface",
        "Interface Fault Counters": "FROM interface",
        "Licences": "FROM license",
        "Threat Package": "FROM content_package",
        "Antivirus Package": "FROM content_package",
        "WildFire Package": "FROM content_package",
        "URL Filtering": "FROM content_package",
        "Installed Content Packages": "FROM content_package",
        "Collection Latency": "FROM collector_health",
        "Quality Rule": "FROM collector_health",
    }
    for title, table in expected.items():
        assert table in panels[title]["targets"][0]["rawSql"]
    unavailable = {panel["title"]: panel for panel in dashboard["panels"]
                   if panel["type"] == "text"}
    assert set(unavailable) == {
        "Certificate Expiry", "Recent Configuration Commits"}
    assert all("Feature Not Enabled" in panel["options"]["content"]
               for panel in unavailable.values())


def test_wan_panel_repeats_per_interface_without_aggregation():
    dashboard = module().build()
    panel = next(value for value in dashboard["panels"]
                 if value["title"] ==
                 "WAN Throughput · ${wan_interface:text}")
    sql = panel["targets"][0]["rawSql"]
    assert panel["repeat"] == "wan_interface"
    assert panel["repeatDirection"] == "h"
    assert panel["maxPerRow"] == 2
    assert panel["gridPos"]["w"] == 12
    assert panel["gridPos"]["h"] >= 9
    assert "interface_name = ${wan_interface:sqlstring}" in sql
    assert 'AS "Download"' in sql and 'AS "Upload"' in sql
    assert "PARTITION BY hostname, interface_name" not in sql
    assert panel["fieldConfig"]["defaults"]["unit"] == "bps"
    assert panel["options"]["legend"]["calcs"] == ["lastNotNull"]


def test_collector_diagnostics_render_values_without_internal_names():
    dashboard = module().build()
    panels = {value["title"]: value for value in dashboard["panels"]}
    for title in (
            "Collector Health", "Last Successful Collection",
            "Collection Duration", "Points Written", "Collection Latency",
            "Quality Rule"):
        assert panels[title]["options"]["textMode"] == "value"
    last_collection = panels["Last Successful Collection"]["targets"][0]["rawSql"]
    assert 'CAST(time AS VARCHAR) AS "Last Collection"' in last_collection
    assert "ORDER BY time DESC LIMIT 1" in last_collection
    assert "success = true" in last_collection
    assert "$__timeFrom" not in last_collection
    assert "$__timeTo" not in last_collection
    assert panels["Last Successful Collection"]["options"]["reduceOptions"] == {
        "calcs": [], "fields": "/^Last Collection$/", "values": True}


def test_required_operational_sections_and_panels_exist():
    dashboard = module().build()
    rows = [panel["title"] for panel in dashboard["panels"] if panel["type"] == "row"]
    assert rows == ["Overview", "Health", "Resources", "Interfaces", "Security",
                    "Content Updates", "Inventory", "Operational"]
    titles = {panel["title"] for panel in dashboard["panels"]}
    assert {"Hostname", "Model", "Serial", "PAN-OS Version", "Platform Family",
            "Uptime", "HA Status",
            "Collector Status", "Interface Status", "Firewall Inventory",
            "Last Successful Collection", "Collection Duration",
            "Points Written", "Collector Health", "Quality Rule",
            "Recent Configuration Commits"} <= titles


def test_string_stats_render_confirmed_canonical_fields_without_reduction():
    dashboard = module().build()
    panels = {panel["title"]: panel for panel in dashboard["panels"]}
    expected = {
        "Hostname": 'hostname AS "Hostname"',
        "Model": 'model AS "Model"',
        "Serial": 'serial AS "Serial"',
        "PAN-OS Version": 'firmware AS "PAN-OS Version"',
        "Platform Family": 'AS "Platform Family"',
        "HA Status": 'AS "HA Status"',
    }
    for title, selection in expected.items():
        panel = panels[title]
        assert selection in panel["targets"][0]["rawSql"]
        assert panel["targets"][0]["refId"] == "A"
        assert panel["transformations"] == []
        assert panel["fieldConfig"]["defaults"]["unit"] == "string"
        assert panel["fieldConfig"]["defaults"]["noValue"] == \
            "Not Yet Collected"
        assert panel["fieldConfig"]["defaults"]["mappings"] == []
        assert panel["fieldConfig"]["overrides"] == []
        assert panel["options"]["reduceOptions"] == {
            "calcs": [], "fields": f"/^{title}$/", "values": True
        }
        assert panel["options"]["textMode"] == "value"
        assert panel["options"]["graphMode"] == "none"
        assert panel["options"]["colorMode"] == "none"

    assert "FROM device" in panels["Hostname"]["targets"][0]["rawSql"]
    assert "FROM device" in panels["Model"]["targets"][0]["rawSql"]
    assert "FROM device" in panels["PAN-OS Version"]["targets"][0]["rawSql"]
    assert "FROM firewall" in panels["HA Status"]["targets"][0]["rawSql"]
    assert "'Standalone'" in panels["HA Status"]["targets"][0]["rawSql"]
    assert "'PA-400 Series'" in \
        panels["Platform Family"]["targets"][0]["rawSql"]


def test_numeric_stats_hide_raw_query_aliases():
    panels = {panel["title"]: panel for panel in module().build()["panels"]}
    for title in (
            "CPU", "Data Plane CPU", "Memory Used", "Active Sessions",
            "Session Utilisation", "Collector Status"):
        assert panels[title]["options"]["textMode"] == "value"


def test_implemented_panels_have_explicit_empty_states_and_valid_columns():
    dashboard = module().build()
    for panel in dashboard["panels"]:
        if panel["type"] in {"stat", "table", "timeseries"}:
            assert panel["fieldConfig"]["defaults"]["noValue"] == \
                "Not Yet Collected"
    queries = [target["rawSql"] for panel in dashboard["panels"]
               for target in panel.get("targets", [])]
    assert all("admin_status" not in query for query in queries)
    assert all("No data" not in json.dumps(panel)
               for panel in dashboard["panels"])


def test_generated_managed_dashboard_matches_source(tmp_path):
    dashboard = module().build()
    source = json.loads(SOURCE.read_text())
    runtime = generate(tmp_path / "runtime/dashboard/grafana/paloalto-overview.json")
    managed_root = tmp_path / "runtime/dashboard/managed"
    DashboardRegistry(
        ROOT, {"collectors": {"paloalto": {"enabled": True}}},
        managed_root, tmp_path / "runtime/dashboard/provisioning/dashboards.yml",
    ).generate()
    managed_path = managed_root / "vendor/paloalto-operational-overview.json"
    managed = json.loads(managed_path.read_text())

    assert source == dashboard
    assert runtime == dashboard
    for title in ("Hostname", "Model", "PAN-OS Version", "HA Status"):
        expected = next(panel for panel in dashboard["panels"] if panel["title"] == title)
        actual = next(panel for panel in managed["panels"] if panel["title"] == title)
        assert actual["targets"] == expected["targets"]
        assert actual["transformations"] == expected["transformations"]
        assert actual["fieldConfig"] == expected["fieldConfig"]
        assert actual["options"] == expected["options"]
