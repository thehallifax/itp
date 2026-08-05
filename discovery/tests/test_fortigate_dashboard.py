import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FORTIGATE_DASHBOARD = ROOT / "dashboards/vendor/fortigate-overview.json"
MIST_DASHBOARD = ROOT / "dashboards/vendor/mist-infrastructure-overview.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_fortigate_dashboard_uses_classic_schema_and_mist_datasource() -> None:
    dashboard = _load(FORTIGATE_DASHBOARD)
    mist = _load(MIST_DASHBOARD)

    assert isinstance(dashboard["panels"], list)
    assert len(dashboard["panels"]) == 12
    assert "elements" not in dashboard
    assert "layout" not in dashboard

    expected_datasource = mist["panels"][0]["datasource"]
    for panel in dashboard["panels"]:
        assert panel["datasource"] == expected_datasource
        assert panel["targets"]
        assert all(target["rawSql"] for target in panel["targets"])
        assert all(target["rawQuery"] for target in panel["targets"])
        assert all(target["format"] == "table" for target in panel["targets"])


def test_fortigate_dashboard_has_required_variables_and_measurements() -> None:
    dashboard = _load(FORTIGATE_DASHBOARD)
    variables = dashboard["templating"]["list"]

    assert [variable["label"] for variable in variables] == [
        "Customer",
        "Site",
        "Device",
        "Time Range",
    ]

    queries = "\n".join(
        target["query"]
        for panel in dashboard["panels"]
        for target in panel["targets"]
    )
    for measurement in (
        "fortigate_system",
        "fortigate_performance",
        "fortigate_interfaces",
        "collector_health",
    ):
        assert measurement in queries

    assert "SELECT *" not in queries.upper()


def test_fortigate_queries_match_confirmed_live_schema() -> None:
    dashboard = _load(FORTIGATE_DASHBOARD)
    panels = {panel["title"]: panel for panel in dashboard["panels"]}

    assert "WAN Throughput" not in panels
    assert "LAN Throughput" not in panels
    assert "Throughput by Interface" in panels
    assert "Top Interfaces by Traffic" in panels

    reachable = panels["Device Reachable"]["targets"][0]["rawSql"]
    assert "FROM availability" in reachable
    assert "available" in reachable
    assert "COUNT(*) = 0 THEN -1" in reachable

    ha = panels["HA Status"]["targets"][0]["rawSql"]
    assert "FROM firewall" in ha
    assert "ha_status" in ha and "ha_mode" in ha
    assert "Not applicable" in ha

    system_information = panels["System Information"]["targets"][0]["rawSql"]
    assert "description" not in system_information
    for field in (
        "device_ip",
        "vendor",
        "platform",
        "device_role",
        "uptime_ticks",
        "firmware",
    ):
        assert field in system_information

    interface_queries = "\n".join(
        panels[title]["targets"][0]["rawSql"]
        for title in ("Throughput by Interface", "Top Interfaces by Traffic")
    )
    assert "EXTRACT(EPOCH FROM (time - previous_time))" in interface_queries
    assert "LIKE 'wan%'" not in interface_queries
    assert "LIKE 'lan%'" not in interface_queries

    cpu = panels["CPU %"]["targets"][0]["rawSql"]
    memory = panels["Memory %"]["targets"][0]["rawSql"]
    assert "FROM performance" in cpu and "cpu_percent" in cpu
    assert "FROM performance" in memory and "memory_used_percent" in memory
    assert 'AS "CPU"' in cpu
    assert 'AS "Memory"' in memory
    assert "FROM performance" in panels["CPU History"]["targets"][0]["rawSql"]
    assert "FROM performance" in panels["Memory History"]["targets"][0]["rawSql"]

    interface_status = panels["Interface Status"]["targets"][0]["rawSql"]
    assert "FROM network_interface" in interface_status
    for value in ("interface_name", "interface_description", "wan_role",
                  "operational_status", "speed_bps", "rx_bytes", "tx_bytes",
                  "receive_bps", "transmit_bps"):
        assert value in interface_status
    for title in ("Throughput by Interface", "Top Interfaces by Traffic"):
        assert panels[title]["transformations"] == [{
            "id": "partitionByValues", "options": {"fields": ["interface_name"]}}]


def test_fortigate_variables_use_valid_canonical_identity_sources() -> None:
    dashboard = _load(FORTIGATE_DASHBOARD)
    variables = {value["name"]: value for value in dashboard["templating"]["list"]}
    for name in ("customer", "site", "device"):
        query = variables[name]["query"]
        assert "FROM infrastructure_device" in query
        assert "collector = 'fortigate'" in query
        assert " = ' THEN" not in query
    assert "customer_id AS __value" in variables["customer"]["query"]
    assert "site_id AS __value" in variables["site"]["query"]
    assert "hostname AS __value" in variables["device"]["query"]


def test_fortigate_stat_empty_states_preserve_valid_zero() -> None:
    panels = {panel["title"]: panel for panel in
              _load(FORTIGATE_DASHBOARD)["panels"]}
    assert panels["Device Reachable"]["fieldConfig"]["defaults"][
        "mappings"][0]["options"]["-1"]["text"] == "No telemetry collected"
    for title in ("CPU %", "Memory %"):
        assert panels[title]["fieldConfig"]["defaults"]["min"] == 0
        assert panels[title]["fieldConfig"]["defaults"][
            "noValue"] == "No telemetry collected"
