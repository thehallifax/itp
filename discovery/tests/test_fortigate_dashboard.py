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
    assert "fortigate_system" in reachable
    assert "fortigate_performance" in reachable
    assert "fortigate_interfaces" in reachable
    assert "INTERVAL '2 minutes'" in reachable

    assert panels["HA Status"]["targets"][0]["rawSql"] == (
        "SELECT 'Not collected' AS ha_status"
    )

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

    assert 'AS "CPU"' in panels["CPU %"]["targets"][0]["rawSql"]
    assert 'AS "Memory"' in panels["Memory %"]["targets"][0]["rawSql"]
