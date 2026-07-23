import json
import csv
import io
from datetime import datetime, timezone
from pathlib import Path

import yaml

from analysis.wallboard import WallboardEngine


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "dashboards/Operations/operations-wallboard.json"
NOW = datetime(2026, 7, 23, 1, 0, tzinfo=timezone.utc)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def fixture(tmp_path):
    assets = [
        {"canonical_id": "switch-1", "hostname": "CORE-1", "device_type": "switch",
         "device_role": "core", "online": True, "site": {"site_id": "site:hq",
         "display_name": "HQ", "source_values": []}},
        {"canonical_id": "ap-1", "hostname": "AP-1", "device_type": "access-point",
         "online": False, "site": {"site_id": "site:hq", "display_name": "HQ",
         "source_values": []}},
        {"canonical_id": "printer-1", "hostname": "PRN-1", "device_type": "printer",
         "online": True, "site": {"site_id": "site:branch", "display_name": "Branch",
         "source_values": []}},
    ]
    write(tmp_path / "infrastructure.json", {"generated_at": "2026-07-23T00:58:00Z",
        "assets": assets, "sites": [], "summary": {"infrastructure_health": "Warning",
        "observability_health": "Critical", "actionable_warnings": 1,
        "collectors_healthy": 1, "collectors_failed": 1},
        "wireless": {"clients_connected": None, "clients_failed_authentication": None},
        "firewalls": {"wan_status": None}, "printers": {"consumables": None},
        "collectors": [
            {"collector": "mist", "status": "healthy", "last_run": "2026-07-23T00:59:00Z",
             "last_successful_run": "2026-07-23T00:59:00Z", "duration_ms": 100, "failures": 0},
            {"collector": "snmp", "status": "failed", "last_run": "2026-07-23T00:50:00Z",
             "last_successful_run": "2026-07-22T23:00:00Z", "duration_ms": 200, "failures": 2},
        ]})
    findings = []
    for index in range(7):
        findings.append({"id": f"issue-{index}", "priority": 70 + index, "severity": "High",
            "category": "Network", "title": f"Issue {index}", "device": f"Device {index}",
            "site": "HQ", "site_id": "site:hq", "impact": "Service impact",
            "reason": "Deterministic reason", "suggested_action": "Investigate"})
    write(tmp_path / "operations.json", {"generated_at": "2026-07-23T00:59:00Z",
        "issues": list(reversed(findings)), "risks": findings[:2], "recommendations": findings[:1]})
    write(tmp_path / "sites.json", {"generated_at": "2026-07-23T00:57:00Z", "sites": [
        {"site_id": "site:branch", "display_name": "Branch"},
        {"site_id": "site:hq", "display_name": "HQ"}]})
    return WallboardEngine(tmp_path / "infrastructure.json", tmp_path / "operations.json",
        tmp_path / "sites.json", TEMPLATE, tmp_path / "summary.json",
        tmp_path / "grafana/operations-wallboard.json", freshness_seconds=300)


def test_template_uid_classic_schema_panel_ids_and_no_scroll_height():
    dashboard = json.loads(TEMPLATE.read_text())
    assert dashboard["uid"] == "itp-operations-wallboard"
    assert dashboard["title"] == "Operations Wallboard"
    assert isinstance(dashboard["panels"], list)
    assert "elements" not in dashboard and "layout" not in dashboard
    ids = [panel["id"] for panel in dashboard["panels"]]
    assert ids == list(range(1, 24)) and len(ids) == len(set(ids))
    assert max(panel["gridPos"]["y"] + panel["gridPos"]["h"] for panel in dashboard["panels"]) == 27
    assert dashboard["panels"][0]["gridPos"]["h"] >= 3


def test_template_is_provisioned_under_operations_and_links_are_uid_relative():
    providers = yaml.safe_load(
        (ROOT / "grafana/provisioning/dashboards/dashboards.yml").read_text())["providers"]
    operations = next(value for value in providers if value["folder"] == "Operations")
    assert operations["folderUid"] == "itp-folder-operations"
    assert operations["options"]["path"] == "/var/lib/grafana/runtime-dashboard/operations"
    dashboard = json.loads(TEMPLATE.read_text())
    assert {link["url"] for link in dashboard["links"]} == {
        "/d/itp-infrastructure-overview", "/d/mist-infrastructure-overview",
        "/d/fortigate-infrastructure-overview"}
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    assert "./runtime/dashboard:/var/lib/grafana/runtime-dashboard:ro" in compose["services"]["grafana"]["volumes"]


def test_required_domains_attention_and_health_mappings(tmp_path):
    engine = fixture(tmp_path); engine.run(NOW)
    dashboard = json.loads((tmp_path / "grafana/operations-wallboard.json").read_text())
    panels = {panel["title"]: panel for panel in dashboard["panels"]}
    assert {"Network", "Wireless", "Security and Edge", "Compute", "Printing", "Services"} <= panels.keys()
    assert {"Top Active Issues", "Top Operational Risks", "Top Recommendations"} <= panels.keys()
    for title in ("Infrastructure Health", "Observability Health"):
        options = panels[title]["fieldConfig"]["defaults"]["mappings"][0]["options"]
        assert options["Healthy"]["color"] == "green"
        assert options["Warning"]["color"] == "orange"
        assert options["Critical"]["color"] == "red"
        assert options["Unknown"]["color"] == "gray"
    live_panels = [panel for panel in dashboard["panels"] if panel.get("targets")]
    assert live_panels
    assert all(target["scenarioId"] == "csv_content"
               for panel in live_panels for target in panel["targets"])
    provisioned_uids = {value["uid"] for value in yaml.safe_load(
        (ROOT / "grafana/provisioning/datasources/influxdb.yml").read_text())["datasources"]}
    assert all(target["datasource"]["uid"] in provisioned_uids
               for panel in live_panels for target in panel["targets"])
    assert all(list(csv.DictReader(io.StringIO(target["csvContent"])))
               for panel in live_panels for target in panel["targets"])


def test_site_variable_all_option_and_consistent_scope_filtering(tmp_path):
    fixture(tmp_path).run(NOW)
    dashboard = json.loads((tmp_path / "grafana/operations-wallboard.json").read_text())
    variable = next(value for value in dashboard["templating"]["list"] if value["name"] == "site")
    assert variable["current"]["value"] == "all" and variable["allValue"] == "all"
    assert [(value["text"], value["value"]) for value in variable["options"]] == [
        ("All Sites", "all"), ("Branch", "site:branch"), ("HQ", "site:hq")]
    scoped = [panel for panel in dashboard["panels"] if panel["title"] not in {
        "Operations Wallboard", "Primary and Secondary WAN Traffic",
        "Core and Internet-Bound Traffic", "WAN Quality",
        "Collector Health and Data Freshness"}]
    assert all("${site}" in json.dumps(panel.get("transformations", [])) for panel in scoped)
    assert "${site:text}" in dashboard["panels"][0]["options"]["content"]
    panels = {panel["title"]: panel for panel in dashboard["panels"]}
    online_rows = list(csv.DictReader(io.StringIO(panels["Devices Online"]["targets"][0]["csvContent"])))
    assert next(value for value in online_rows if value["scope"] == "all")["value"] == "2 / 3 (67%)"
    assert next(value for value in online_rows if value["scope"] == "site:hq")["value"] == "1 / 2 (50%)"
    assert next(value for value in online_rows if value["scope"] == "site:branch")["value"] == "1 / 1 (100%)"


def test_health_separation_ratios_nulls_and_freshness(tmp_path):
    result = fixture(tmp_path).run(NOW)
    estate = result["scopes"][0]
    assert estate["infrastructure_health"] == "Warning"
    assert estate["observability_health"] == "Critical"
    assert estate["online"] <= estate["devices"]
    assert estate["domains"]["wireless"]["clients_connected"] is None
    assert result["wan"] == {"primary_traffic": None, "secondary_traffic": None,
                             "core_traffic": None, "latency_ms": None,
                             "packet_loss_percent": None}
    assert result["freshness"]["oldest_generated_at"] == "2026-07-23T00:57:00Z"
    assert result["freshness"]["age_seconds"] == 180
    assert result["freshness"]["status"] == "Fresh"
    dashboard = json.loads((tmp_path / "grafana/operations-wallboard.json").read_text())
    panels = {panel["title"]: panel for panel in dashboard["panels"]}
    assert "2 / 3 (67%)" in panels["Devices Online"]["targets"][0]["csvContent"]
    assert "1 / 2 (50%)" in panels["Collectors Healthy"]["targets"][0]["csvContent"]
    assert "N/A" in panels["Wireless"]["targets"][0]["csvContent"]
    offline_rows = list(csv.DictReader(io.StringIO(panels["Devices Offline"]["targets"][0]["csvContent"])))
    assert any(value["value"] == "0" for value in offline_rows)


def test_top_five_and_topology_are_deterministically_ordered(tmp_path):
    result = fixture(tmp_path).evaluate(NOW)
    estate_issues = [value for value in result["attention"]["issues"] if value["scope"] == "all"]
    assert len(estate_issues) == 5
    assert [value["priority"] for value in estate_issues] == [76, 75, 74, 73, 72]
    estate_nodes = [value for value in result["topology"]["nodes"] if value["scope"] == "all"]
    assert [value["id"] for value in estate_nodes] == [
        "internet", "edge", "core", "distribution", "access", "wireless", "servers", "printers"]
    assert result["topology"]["type"] == "logical_aggregate"
    estate_edges = [value for value in result["topology"]["edges"] if value["scope"] == "all"]
    assert len(estate_edges) == 7
    assert set(estate_nodes[0]) == {
        "scope", "id", "title", "subTitle", "mainStat", "secondaryStat", "color"}
    assert set(estate_edges[0]) == {"scope", "id", "source", "target"}


def test_topology_and_wan_fallbacks_are_honest(tmp_path):
    fixture(tmp_path).run(NOW)
    dashboard = json.loads((tmp_path / "grafana/operations-wallboard.json").read_text())
    panels = {panel["title"]: panel for panel in dashboard["panels"]}
    assert "not discovered physical topology" in panels["Logical Infrastructure View"]["description"]
    topology = panels["Logical Infrastructure View"]
    assert topology["type"] == "nodeGraph"
    assert {target["refId"] for target in topology["targets"]} == {"nodes", "edges"}
    nodes = next(target for target in topology["targets"] if target["refId"] == "nodes")
    edges = next(target for target in topology["targets"] if target["refId"] == "edges")
    assert set(next(csv.DictReader(io.StringIO(nodes["csvContent"])))) == {
        "scope", "id", "title", "subTitle", "mainStat", "secondaryStat", "color"}
    assert set(next(csv.DictReader(io.StringIO(edges["csvContent"])))) == {
        "scope", "id", "source", "target"}
    for title in ("Primary and Secondary WAN Traffic", "Core and Internet-Bound Traffic", "WAN Quality"):
        assert "Awaiting telemetry" in panels[title]["options"]["content"]
        assert "inferred" in panels[title]["options"]["content"]


def test_clean_runtime_bootstrap_and_deterministic_generation(tmp_path):
    engine = WallboardEngine(tmp_path / "missing-infrastructure.json",
        tmp_path / "missing-operations.json", tmp_path / "missing-sites.json",
        TEMPLATE, tmp_path / "summary.json", tmp_path / "grafana/wallboard.json")
    first = engine.run(NOW)
    first_dashboard = (tmp_path / "grafana/wallboard.json").read_text()
    second = engine.run(NOW)
    assert first == second
    assert first_dashboard == (tmp_path / "grafana/wallboard.json").read_text()
    assert first["scopes"][0]["devices"] == 0
    assert first["scopes"][0]["infrastructure_health"] == "Unknown"
    assert first["freshness"]["status"] == "Unknown"
