import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/generate_paloalto_dashboard.py"
OUTPUT = ROOT / "runtime/dashboard/grafana/paloalto-overview.json"
DATASOURCE_UID = "ffsu5ap2kr5dse"


def module():
    spec = importlib.util.spec_from_file_location("paloalto_dashboard", SCRIPT)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


def test_dashboard_is_classic_stable_and_runtime_provisioned():
    dashboard = module().build()
    assert dashboard["uid"] == "paloalto-operational-overview"
    assert dashboard["schemaVersion"] == 41
    assert isinstance(dashboard["panels"], list) and len(dashboard["panels"]) == 35
    assert "elements" not in dashboard and "layout" not in dashboard
    provision = (ROOT / "grafana/provisioning/dashboards/dashboards.yml").read_text()
    assert "/var/lib/grafana/runtime-dashboard/managed/vendor" in provision
    assert OUTPUT.exists()
    assert json.loads(OUTPUT.read_text()) == dashboard


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
    assert [value["name"] for value in variables] == ["customer", "site", "device"]
    assert all(value["allValue"] == "'%'" for value in variables)
    assert all("collector = 'paloalto'" in value["query"] for value in variables)
    assert "${customer:sqlstring}" in variables[1]["query"]
    assert "${site:sqlstring}" in variables[2]["query"]
    queries = [target["rawSql"] for panel in module().build()["panels"]
               for target in panel.get("targets", [])]
    assert all("'${customer}'" not in query and "'${site}'" not in query
               and "'${device}'" not in query for query in queries)


def test_operational_telemetry_panels_use_live_canonical_measurements():
    dashboard = module().build()
    panels = {panel["title"]: panel for panel in dashboard["panels"]}
    expected = {
        "Device Certificate Status": "FROM firewall",
        "Platform Family": "FROM device",
        "Management CPU": "FROM performance",
        "Data Plane CPU": "FROM performance",
        "Memory Used": "FROM performance",
        "Active Sessions": "FROM performance",
        "Session Utilisation": "FROM performance",
        "WAN RX / TX": "FROM interface",
        "Interface Fault Counters": "FROM interface",
        "Subscriptions and Expiry": "FROM license",
        "Installed Content Packages": "FROM content_package",
        "Max API Duration": "FROM collector_health",
    }
    for title, table in expected.items():
        assert table in panels[title]["targets"][0]["rawSql"]
    assert not [panel for panel in dashboard["panels"] if panel["type"] == "text"]


def test_required_operational_sections_and_panels_exist():
    dashboard = module().build()
    rows = [panel["title"] for panel in dashboard["panels"] if panel["type"] == "row"]
    assert rows == ["Overview", "Health", "Resources", "Interfaces", "Licensing",
                    "Content Updates", "Inventory", "Collector Diagnostics"]
    titles = {panel["title"] for panel in dashboard["panels"]}
    assert {"Hostname", "Model", "PAN-OS Version", "Uptime", "HA Status",
            "Collector Status", "Interface Status", "Firewall Inventory",
            "Last Collection", "Collection Duration", "Points Written",
            "Collector Result"} <= titles


def test_string_stats_render_confirmed_canonical_fields_without_reduction():
    dashboard = module().build()
    panels = {panel["title"]: panel for panel in dashboard["panels"]}
    expected = {
        "Hostname": 'hostname AS "Hostname"',
        "Model": 'model AS "Model"',
        "PAN-OS Version": 'firmware AS "PAN-OS Version"',
        "HA Status": 'ha_status AS "HA Status"',
    }
    for title, selection in expected.items():
        panel = panels[title]
        assert selection in panel["targets"][0]["rawSql"]
        assert panel["targets"][0]["refId"] == "A"
        assert panel["transformations"] == []
        assert panel["fieldConfig"]["defaults"]["unit"] == "string"
        assert panel["fieldConfig"]["defaults"]["noValue"] == "No data"
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


def test_generated_managed_dashboard_matches_source(tmp_path):
    dashboard = module().build()
    source = json.loads((ROOT / "dashboards/Vendor/paloalto-overview.json").read_text())
    runtime = json.loads(OUTPUT.read_text())
    managed_path = ROOT / "runtime/dashboard/managed/vendor/paloalto-operational-overview.json"
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
