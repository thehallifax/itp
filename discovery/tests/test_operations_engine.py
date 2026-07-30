import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path

from analysis.operations import OperationsEngine, Rule
from analysis.operations.renderer import render_dashboard


NOW = datetime(2026, 7, 23, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[2]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def fixture(tmp_path):
    inventory = tmp_path / "inventory"; output = tmp_path / "operations"
    write(inventory / "assets.json", {"assets": [
        {"asset_id": "ap-1", "hostname": "AP-1", "device_type": "wireless-access-point",
         "vendor": "juniper", "online": False, "site": "HQ", "last_seen_at": "2026-07-22T00:00:00Z",
         "firmware_version": "1.0", "model": "AP34", "lifecycle_state": "offline"},
        {"asset_id": "switch-1", "hostname": "CORE-1", "device_type": "switch",
         "vendor": "juniper", "online": False, "site": "HQ", "last_seen_at": "2026-05-01T00:00:00Z",
         "lifecycle_state": "stale"},
        {"asset_id": "unknown-1", "hostname": "UNKNOWN", "online": True,
         "last_seen_at": "2026-07-22T00:00:00Z", "lifecycle_state": "active"},
    ]})
    write(inventory / "source_runs.json", {"sources": {"mist": {
        "consecutive_failures": 2,
        "last_run": {"success": False, "completed_at": "2026-07-22T20:00:00Z",
                     "error_category": "authentication"}}}})
    write(inventory / "reconciliation.json", {"reconciliations": [
        {"status": "ambiguous", "asset_ids": ["ap-1", "switch-1"]}]})
    write(output / "signals.json", {
        "approved_firmware": {"AP34": "2.0"},
        "certificates": [{"name": "portal", "expires_at": "2026-08-01T00:00:00Z", "site": "HQ"}],
        "printer_consumables": [{"device": "PRN-1", "supply": "black toner", "percent_remaining": 4}],
        "wan": [{"name": "Primary", "site": "HQ", "available": False, "packet_loss_percent": 12}],
    })
    write(tmp_path / "infrastructure/state.json", {
        "assets": json.loads((inventory / "assets.json").read_text())["assets"],
        "collectors": [{"collector": "mist", "status": "failed", "failures": 2,
                        "last_run": "2026-07-22T20:00:00Z", "last_successful_run": None}],
        "reconciliations": [{"status": "ambiguous", "asset_ids": ["ap-1", "switch-1"]}],
        "signals": json.loads((output / "signals.json").read_text()),
    })
    write(tmp_path / "dashboard/infrastructure-summary.json", {
        "readiness": {"capabilities": [
            "switching", "wireless", "firewall", "compute", "printing",
            "internet"]},
        "infrastructure_health": "Critical", "observability_health": "Warning",
        "devices": 3, "devices_online": 1,
        "devices_offline": 2, "critical": 1, "warnings": 2,
        "actionable_warnings": 2, "data_quality_findings": 1,
        "collectors_healthy": 0, "collectors_failed": 1,
        "switches_total": 1, "switches_online": 0, "switches_offline": 1,
        "aps_total": 1, "aps_online": 0, "aps_offline": 1,
        "firewalls_total": 0, "firewalls_healthy": 0, "firewalls_offline": 0,
        "servers_total": 0, "servers_healthy": 0, "servers_offline": 0,
        "printers_total": 0, "printers_healthy": 0, "printers_offline": 0,
    })
    return OperationsEngine(inventory, output,
        ROOT / "dashboards/Infrastructure Overview/infrastructure-overview.json",
        dashboard_output=output / "dashboard/infrastructure-overview.json",
        infrastructure_state=tmp_path / "infrastructure/state.json",
        infrastructure_summary=tmp_path / "dashboard/infrastructure-summary.json",
        capability_registry=tmp_path / "missing-registry.json",
        settings={"collector_overdue_seconds": 900}), output


def test_rules_are_auto_registered_and_stable():
    rules = Rule.registered()
    assert len(rules) == 20
    assert [rule.id for rule in rules] == sorted(rule.id for rule in rules)
    assert len({rule.id for rule in rules}) == 20


def test_engine_is_deterministic_explainable_and_sorted(tmp_path):
    engine, _ = fixture(tmp_path)
    first = engine.evaluate(NOW); second = engine.evaluate(NOW)
    assert first == second
    assert first["issues"] and first["risks"] and first["recommendations"]
    for collection in ("issues", "risks", "recommendations"):
        values = first[collection]
        assert [item["priority"] for item in values] == sorted(
            (item["priority"] for item in values), reverse=True)
        assert all(0 <= item["priority"] <= 100 for item in values)
        assert all(item["reason"] and item["impact"] and item["suggested_action"] for item in values)
    assert any(item["rule_id"] == "collector.failed" for item in first["issues"])
    assert any(item["rule_id"] == "collector.failed" for item in first["risks"])
    assert any(item["rule_id"] == "collector.failed" for item in first["recommendations"])


def test_outputs_and_runtime_dashboard_are_generated(tmp_path):
    engine, output = fixture(tmp_path); result = engine.run(NOW)
    assert json.loads((output / "operations.json").read_text()) == result
    with (output / "operations.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == sum(len(result[key]) for key in ("issues", "risks", "recommendations"))
    dashboard = json.loads((output / "dashboard/infrastructure-overview.json").read_text())
    panels = {panel["title"]: panel for panel in dashboard["panels"]}
    for title in ("Active Issues", "Operational Risks", "Recommendations"):
        assert panels[title]["type"] == "table"
        assert panels[title]["targets"][0]["scenarioId"] == "csv_content"
        assert "scope" in panels[title]["targets"][0]["csvContent"].splitlines()[0]
    assert panels["Devices Online"]["type"] == "stat"
    assert panels["Devices Online"]["targets"][0]["csvContent"] == "value\n1"
    assert panels["Switches"]["targets"][0]["csvContent"] == "value\n1"
    assert all(panels[title]["targets"][0]["scenarioId"] == "csv_content" for title in (
        "Infrastructure Health", "Observability Health", "Devices Online", "Devices Offline",
        "Data Quality Findings", "Collectors Healthy", "Switches", "Access Points",
        "Firewalls", "Servers", "Printers"))
    assert dashboard["uid"] == "itp-infrastructure-overview"


def test_infrastructure_overview_findings_are_canonical_site_scoped(tmp_path):
    def finding(item_id, site_id, site, title):
        return {"id": item_id, "priority": 80, "severity": "High",
            "title": title, "device": title.split()[0], "site_id": site_id,
            "site": site}
    result = {"generated_at": "2026-07-23T14:00:00Z",
        "issues": [
            finding("example-school-pa", "site:example-school", "example-school Reference Site",
                    "example-school-PA licence expired"),
            finding("example-corporate-ap", "site:example-corporate",
                    "Northwind College", "example-corporate-AP offline"),
            finding("example-corporate-forti", "site:example-corporate",
                    "Northwind College", "example-corporate-Forti unavailable")],
        "risks": [
            finding("example-school-risk", "site:example-school", "example-school Reference Site",
                    "example-school-PA security risk"),
            finding("example-corporate-risk", "site:example-corporate",
                    "Northwind College", "example-corporate-AP capacity risk")],
        "recommendations": [
            finding("example-school-rec", "site:example-school", "example-school Reference Site",
                    "example-school-PA renew licence"),
            finding("example-corporate-rec", "site:example-corporate",
                    "Northwind College", "example-corporate-AP investigate")]}
    scopes = [{"scope": "all"}, {"scope": "site:example-school"},
              {"scope": "site:example-corporate"}]
    summary = {"readiness": {"capabilities": []}, "site_options": [
        {"site_id": "site:example-school", "display_name": "example-school Reference Site"},
        {"site_id": "site:example-corporate",
         "display_name": "Northwind College"}],
        "scopes": scopes}
    output = tmp_path / "infrastructure-overview.json"
    render_dashboard(ROOT / "dashboards/Infrastructure Overview/infrastructure-overview.json",
                     output, result, summary)
    panels = {value["title"]: value for value in json.loads(output.read_text())["panels"]}
    for title in ("Active Issues", "Operational Risks", "Recommendations"):
        values = list(csv.DictReader(io.StringIO(
            panels[title]["targets"][0]["csvContent"])))
        example_school = [value for value in values if value["scope"] == "site:example-school"]
        st = [value for value in values if value["scope"] == "site:example-corporate"]
        assert example_school and st
        assert all("example-corporate" not in value["item"]
                   for value in example_school)
        assert all("example-school" not in value["item"] for value in st)
    all_issues = [value for value in list(csv.DictReader(io.StringIO(
        panels["Active Issues"]["targets"][0]["csvContent"])))
        if value["scope"] == "all"]
    assert len(all_issues) == 3


def test_executive_risks_aggregate_paloalto_subscriptions_but_keep_details(
        tmp_path):
    risks = [{
        "id": f"risk-{name}", "rule_id": "PA-LICENCE-EXPIRING",
        "priority": 60, "severity": "Medium",
        "title": f"Palo Alto licence expiring: {name}",
        "canonical_id": "paloalto:one", "device": "FW-1",
        "site_id": "site:hq", "site": "HQ",
        "evidence": {"source_collector": "paloalto", "licence": name,
                     "days_remaining": days},
    } for name, days in (("Threat", 10), ("URL Filtering", 20))]
    result = {
        "generated_at": "2026-07-23T14:00:00Z",
        "issues": [], "risks": risks, "recommendations": []}
    output = tmp_path / "overview.json"
    render_dashboard(
        ROOT / "dashboards/Infrastructure Overview/infrastructure-overview.json",
        output, result, {
            "readiness": {"capabilities": ["firewall"]},
            "site_options": [{"site_id": "site:hq", "display_name": "HQ"}],
            "scopes": [{"scope": "all"}, {"scope": "site:hq"}],
        })
    dashboard = json.loads(output.read_text())
    panel = next(
        value for value in dashboard["panels"]
        if value["title"] == "Operational Risks")
    rows = list(csv.DictReader(io.StringIO(
        panel["targets"][0]["csvContent"])))
    assert [row["item"] for row in rows] == [
        "Palo Alto subscriptions approaching expiry — 2 affected",
        "Palo Alto subscriptions approaching expiry — 2 affected",
    ]
    assert [value["evidence"]["licence"] for value in result["risks"]] == [
        "Threat", "URL Filtering"]


def test_disabled_collector_health_does_not_generate_operations_findings(tmp_path):
    engine, _ = fixture(tmp_path)
    write(tmp_path / "registry.json", {"enabled_collectors": ["snmp"]})
    engine.capability_registry = tmp_path / "registry.json"
    result = engine.evaluate(NOW)
    assert not any(value.get("device") == "mist"
                   for kind in ("issues", "risks", "recommendations")
                   for value in result[kind])
