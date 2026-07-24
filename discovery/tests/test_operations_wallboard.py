import csv
import io
import json
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


def fixture(tmp_path, *, capabilities=None, assets=None, operations=None, signals=None,
            service_statuses=None, service_generated_at="2026-07-23T00:59:00Z"):
    assets = assets if assets is not None else [
        {"canonical_id": "switch-1", "hostname": "CORE-1", "device_type": "switch",
         "online": True, "site": {"site_id": "site:hq", "display_name": "HQ"}},
        {"canonical_id": "ap-1", "hostname": "AP-1", "device_type": "access-point",
         "online": False, "site": {"site_id": "site:hq", "display_name": "HQ"}},
        {"canonical_id": "printer-1", "hostname": "PRN-1", "device_type": "printer",
         "online": True, "location": "Library",
         "site": {"site_id": "site:branch", "display_name": "Branch"}},
    ]
    write(tmp_path / "infrastructure.json", {"generated_at": "2026-07-23T00:58:00Z",
        "assets": assets, "summary": {"infrastructure_health": "Warning",
        "observability_health": "Warning", "actionable_warnings": 1,
        "collectors_healthy": 2, "collectors_failed": 0},
        "wireless": {"clients_connected": None, "clients_failed_authentication": None},
        "firewalls": {"wan_status": None}, "printers": {"consumables": None},
        "signals": signals or {}, "collectors": [
            {"collector": "mist", "status": "healthy",
             "site_ids": ["site:hq"], "last_run": "2026-07-23T00:59:00Z"},
            {"collector": "snmp", "status": "healthy",
             "site_ids": ["site:branch", "site:hq"],
             "last_run": "2026-07-23T00:59:00Z"}]})
    if operations is None:
        findings = [{"id": f"issue-{index}", "priority": 70 + index,
            "severity": "High", "category": "Network", "title": f"Issue {index}",
            "summary": f"Switch issue {index}", "device": f"Device {index}",
            "site": "HQ", "site_id": "site:hq", "kind": "issue",
            "rule_id": f"switch-{index}", "evidence": {"age_seconds": index * 60}}
            for index in range(10)]
        operations = {"issues": list(reversed(findings)), "risks": [],
                      "recommendations": []}
    write(tmp_path / "operations.json",
          {"generated_at": "2026-07-23T00:59:00Z", **operations})
    write(tmp_path / "sites.json", {"generated_at": "2026-07-23T00:57:00Z",
        "sites": [{"site_id": "site:branch", "display_name": "Branch"},
                  {"site_id": "site:hq", "display_name": "HQ"}]})
    capabilities = capabilities if capabilities is not None else [
        "switching", "wireless", "printing", "firewall", "internet", "compute",
        "telemetry"]
    write(tmp_path / "registry.json", {"capabilities": capabilities,
        "enabled_collectors": ["mist", "snmp"], "dashboards": [
            {"uid": "itp-infrastructure-overview"},
            {"uid": "mist-infrastructure-overview"},
            {"uid": "itp-collector-health"}]})
    capability_for = {"Internet": "internet", "Wireless": "wireless",
        "Switching": "switching", "Printing": "printing", "Identity": "identity",
        "Compute": "compute", "Storage": "storage", "Voice": "voice",
        "Email": "email", "Security": "firewall", "Monitoring": "telemetry"}
    service_statuses = service_statuses or {"Wireless": "Warning", "Compute": "Unknown"}
    def services():
        return [{
            "service": name,
            "status": ("Not Enabled" if capability_for[name] not in capabilities
                       else service_statuses.get(name, "Healthy")),
            "severity": "Info", "summary": f"{name} canonical summary.",
            "affected_assets": [], "affected_users": None, "last_change": None,
            "evidence": []} for name in capability_for]
    service_values = services()
    enabled_statuses = {value["status"] for value in service_values
                        if value["status"] != "Not Enabled"}
    overall = ("Critical" if "Critical" in enabled_statuses else
               "Warning" if "Warning" in enabled_statuses else
               "Unknown" if "Unknown" in enabled_statuses or not enabled_statuses
               else "Healthy")
    write(tmp_path / "service-health.json", {"generated_at": service_generated_at,
        "schema_version": 2,
        "estate": {"site_id": "all", "site_name": "All Sites",
            "overall_status": overall, "services": service_values},
        "sites": [{"site_id": site_id, "site_name": site_name,
            "overall_status": overall, "services": services()}
            for site_id, site_name in (
                ("site:branch", "Branch"), ("site:hq", "HQ"))],
        "diagnostics": []})
    return WallboardEngine(tmp_path / "infrastructure.json", tmp_path / "operations.json",
        tmp_path / "sites.json", TEMPLATE, tmp_path / "summary.json",
        tmp_path / "grafana/operations-wallboard.json",
        capability_registry=tmp_path / "registry.json",
        service_health=tmp_path / "service-health.json", freshness_seconds=300)


def rows(panel):
    return list(csv.DictReader(io.StringIO(panel["targets"][0]["csvContent"])))


def test_single_screen_classic_layout_uid_and_managed_tags():
    dashboard = json.loads(TEMPLATE.read_text())
    assert dashboard["uid"] == "itp-operations-wallboard"
    assert dashboard["title"] == "Operations Wallboard"
    assert set(dashboard["tags"]) >= {
        "exception-driven", "itp", "itp-managed", "operations", "wallboard"}
    assert isinstance(dashboard["panels"], list) and len(dashboard["panels"]) == 24
    assert {panel["id"] for panel in dashboard["panels"]} == set(range(1, 25))
    assert max(panel["gridPos"]["y"] + panel["gridPos"]["h"]
               for panel in dashboard["panels"]) == 18
    assert all(panel["gridPos"]["x"] + panel["gridPos"]["w"] <= 24
               for panel in dashboard["panels"])
    assert "elements" not in dashboard and "layout" not in dashboard


def test_normalized_operations_provisioning_and_runtime_mount():
    providers = yaml.safe_load(
        (ROOT / "grafana/provisioning/dashboards/dashboards.yml").read_text())["providers"]
    operations = next(value for value in providers if value["folder"] == "Operations")
    assert operations["folderUid"] == "itp-folder-operations"
    assert operations["options"]["path"].endswith("/managed/operations")
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    assert "${ITP_RUNTIME_DIR:-./runtime}/dashboard:/var/lib/grafana/runtime-dashboard:ro" in \
        compose["services"]["grafana"]["volumes"]


def test_health_cards_use_healthy_offline_unknown_semantics(tmp_path):
    fixture(tmp_path).run(NOW)
    dashboard = json.loads((tmp_path / "grafana/operations-wallboard.json").read_text())
    panels = {panel["title"]: panel for panel in dashboard["panels"]}
    assert {"Wireless Access Points", "Switches", "Servers", "Firewalls"} <= panels.keys()
    wireless = next(value for value in rows(panels["Wireless Access Points"])
                    if value["scope"] == "all")
    assert wireless == {"scope": "all", "Healthy": "0", "Offline": "1", "Unknown": "0"}
    switching = next(value for value in rows(panels["Switches"])
                     if value["scope"] == "all")
    assert switching["Healthy"] == "1" and switching["Offline"] == "0"
    assert "Total" not in switching
    overrides = panels["Switches"]["fieldConfig"]["overrides"]
    assert any(value["matcher"]["options"] == "Healthy" for value in overrides)
    assert any(value["matcher"]["options"] == "Offline" for value in overrides)


def test_capability_disabled_domains_are_not_broken(tmp_path):
    fixture(tmp_path, capabilities=["switching", "telemetry"]).run(NOW)
    dashboard = json.loads((tmp_path / "grafana/operations-wallboard.json").read_text())
    panels = {panel["title"]: panel for panel in dashboard["panels"]}
    for title in ("Wireless Access Points", "Servers", "Firewalls"):
        assert title not in panels
    assert "Printer Action Required" not in panels
    assert "Printing Service" not in panels


def test_no_actionable_printer_conditions(tmp_path):
    engine = fixture(tmp_path, operations={"issues": [], "risks": [], "recommendations": []})
    result = engine.evaluate(NOW)
    estate = next(value for value in result["printer_exceptions"] if value["scope"] == "all")
    assert estate["asset"] == "No printer action required"


def test_printer_filter_includes_only_service_blocking_conditions(tmp_path):
    printer = {"canonical_id": "p1", "hostname": "PRN-1", "device_type": "printer",
        "online": True, "location": "Library", "last_seen_at": "2026-07-23T00:50:00Z",
        "site": {"site_id": "site:hq", "display_name": "HQ"}, "extensions": {
            "printer_conditions": [
                {"condition": "Paper jam", "actionable": True},
                {"condition": "Black toner", "percent_remaining": 4},
                {"condition": "Paper tray empty", "actionable": True},
                {"condition": "Low paper", "actionable": True},
                {"condition": "Staples empty", "actionable": True}]}}
    result = fixture(tmp_path, assets=[printer], operations={
        "issues": [], "risks": [], "recommendations": []}).evaluate(NOW)
    conditions = [value["condition"] for value in result["printer_exceptions"]
                  if value["scope"] == "all"]
    assert conditions == ["Black toner (4% remaining)", "Paper jam", "Staples empty"]
    assert all("paper tray" not in value.lower() and "low paper" not in value.lower()
               for value in conditions)


def test_multiple_authoritative_wan_uplinks_and_samples(tmp_path):
    wan = [{"name": "ethernet1/1", "role": "primary", "available": True,
            "site_id": "site:hq", "samples": [
                {"time": "2026-07-23T00:58:00Z", "rx_bps": 1000, "tx_bps": 500}]},
           {"name": "ethernet1/2", "role": "secondary", "available": False,
            "site_id": "site:hq", "samples": [
                {"time": "2026-07-23T00:58:00Z", "rx_bps": 0, "tx_bps": 0}]}]
    result = fixture(tmp_path, signals={"wan": wan}).run(NOW)
    estate = [value for value in result["wan"]["uplinks"] if value["scope"] == "all"]
    assert [(value["role"], value["state"]) for value in estate] == [
        ("Primary", "Up"), ("Secondary", "Down")]
    assert len(result["wan"]["samples"]) == 4  # estate and site-scoped frames
    dashboard = json.loads((tmp_path / "grafana/operations-wallboard.json").read_text())
    traffic = next(value for value in dashboard["panels"] if value["title"] == "WAN Traffic")
    assert traffic["type"] == "timeseries" and traffic["targets"][0]["scenarioId"] == "csv_content"


def test_unclassified_interfaces_do_not_become_wan(tmp_path):
    result = fixture(tmp_path, signals={"wan": [{
        "name": "ethernet1/9", "available": True}]},
        service_statuses={"Internet": "Unknown", "Wireless": "Warning",
                          "Compute": "Unknown"}).run(NOW)
    assert all(value["uplink"] == "Internet canonical summary."
               for value in result["wan"]["uplinks"])
    assert all(value["state"] == "Unknown" for value in result["wan"]["uplinks"])
    dashboard = json.loads((tmp_path / "grafana/operations-wallboard.json").read_text())
    traffic = next(value for value in dashboard["panels"] if value["title"] == "WAN Traffic")
    assert traffic["type"] == "text"
    assert "authoritatively classified" in traffic["options"]["content"]


def test_action_required_is_consolidated_filtered_and_ordered(tmp_path):
    result = fixture(tmp_path).run(NOW)
    estate = [value for value in result["actions"] if value["scope"] == "all"]
    assert len(estate) == 8
    assert [value["priority"] for value in estate] == list(range(79, 71, -1))
    assert [value["age"] for value in estate[:2]] == ["9m", "8m"]
    dashboard = json.loads((tmp_path / "grafana/operations-wallboard.json").read_text())
    titles = {value["title"] for value in dashboard["panels"]}
    assert "Action Required" in titles
    assert not {"Top Active Issues", "Top Operational Risks", "Top Recommendations"} & titles


def test_collector_summary_is_compact_and_enabled_only(tmp_path):
    result = fixture(tmp_path).run(NOW)
    assert result["collectors"] == [
        {"scope": "all", "collector": "mist", "site": "HQ",
         "status": "Healthy", "last_run": "2026-07-23T00:59:00Z"},
        {"scope": "all", "collector": "snmp", "site": "Branch",
         "status": "Healthy", "last_run": "2026-07-23T00:59:00Z"},
        {"scope": "all", "collector": "snmp", "site": "HQ",
         "status": "Healthy", "last_run": "2026-07-23T00:59:00Z"},
        {"scope": "site:branch", "collector": "snmp", "site": "Branch",
         "status": "Healthy", "last_run": "2026-07-23T00:59:00Z"},
        {"scope": "site:hq", "collector": "mist", "site": "HQ",
         "status": "Healthy", "last_run": "2026-07-23T00:59:00Z"},
        {"scope": "site:hq", "collector": "snmp", "site": "HQ",
         "status": "Healthy", "last_run": "2026-07-23T00:59:00Z"}]
    dashboard = json.loads((tmp_path / "grafana/operations-wallboard.json").read_text())
    collector = next(value for value in dashboard["panels"] if value["title"] == "Collector State")
    assert set(rows(collector)[0]) == {"scope", "collector", "site", "status"}


def test_site_summary_freshness_links_and_supported_csv_contract(tmp_path):
    fixture(tmp_path).run(NOW)
    dashboard = json.loads((tmp_path / "grafana/operations-wallboard.json").read_text())
    variable = dashboard["templating"]["list"][0]
    assert [(value["text"], value["value"]) for value in variable["options"]] == [
        ("All Sites", "all"), ("Branch", "site:branch"), ("HQ", "site:hq")]
    panels = {value["title"]: value for value in dashboard["panels"]}
    assert "${site:text}" in panels["Site Operational Status"]["options"]["content"]
    assert "Last Service Health" in panels
    assert panels["Collector State"]["links"][0]["url"].startswith("/d/itp-collector-health")
    assert panels["Wireless Access Points"]["links"][0]["url"].startswith(
        "/d/mist-infrastructure-overview")
    for panel in dashboard["panels"]:
        for target in panel.get("targets", []):
            assert target["scenarioId"] == "csv_content"
            assert list(csv.DictReader(io.StringIO(target["csvContent"])))
    assert not any("rawSql" in target for panel in dashboard["panels"]
                   for target in panel.get("targets", []))


def test_clean_bootstrap_and_deterministic_generation(tmp_path):
    engine = WallboardEngine(tmp_path / "missing-infrastructure.json",
        tmp_path / "missing-operations.json", tmp_path / "missing-sites.json",
        TEMPLATE, tmp_path / "summary.json", tmp_path / "grafana/wallboard.json",
        service_health=tmp_path / "missing-service-health.json")
    first = engine.run(NOW); rendered = (tmp_path / "grafana/wallboard.json").read_text()
    second = engine.run(NOW)
    assert first == second
    assert rendered == (tmp_path / "grafana/wallboard.json").read_text()
    assert first["overall_health"]["all"] == "Unknown"
    assert first["freshness"]["status"] == "Unknown"


def test_overall_status_uses_only_canonical_service_health(tmp_path):
    result = fixture(tmp_path, assets=[], operations={
        "issues": [], "risks": [], "recommendations": []},
        service_statuses={"Internet": "Critical", "Switching": "Warning",
                          "Compute": "Unknown"}).evaluate(NOW)
    assert set(result["overall_health"].values()) == {"Critical"}

    result = fixture(tmp_path / "warning", assets=[], operations={
        "issues": [], "risks": [], "recommendations": []},
        service_statuses={"Internet": "Healthy", "Switching": "Warning",
                          "Compute": "Unknown"}).evaluate(NOW)
    assert set(result["overall_health"].values()) == {"Warning"}

    result = fixture(tmp_path / "unknown", assets=[{
        "canonical_id": "offline", "device_type": "switch", "online": False}],
        operations={"issues": [{"severity": "Critical", "category": "Network",
            "priority": 100, "rule_id": "test", "device": "offline"}],
            "risks": [], "recommendations": []},
        service_statuses={"Internet": "Healthy", "Switching": "Healthy",
                          "Compute": "Unknown"}).evaluate(NOW)
    assert set(result["overall_health"].values()) == {"Unknown"}


def test_not_enabled_services_do_not_degrade_overall(tmp_path):
    statuses = {name: "Not Enabled" for name in (
        "Wireless", "Printing", "Identity", "Compute", "Storage", "Voice", "Email")}
    statuses.update({"Internet": "Healthy", "Switching": "Healthy",
                     "Security": "Healthy", "Monitoring": "Healthy"})
    result = fixture(tmp_path, service_statuses=statuses).run(NOW)
    assert set(result["overall_health"].values()) == {"Healthy"}
    dashboard = json.loads((tmp_path / "grafana/operations-wallboard.json").read_text())
    titles = {panel["title"] for panel in dashboard["panels"]}
    assert "Wireless Service" not in titles and "Compute Service" not in titles
    assert "Internet Service" in titles and "Monitoring Service" in titles


def test_secondary_enabled_services_get_compact_cards(tmp_path):
    statuses = {"Storage": "Warning", "Voice": "Healthy", "Email": "Unknown"}
    result = fixture(tmp_path, capabilities=[
        "storage", "voice", "email", "telemetry"], service_statuses=statuses).run(NOW)
    dashboard = json.loads((tmp_path / "grafana/operations-wallboard.json").read_text())
    panels = {panel["title"]: panel for panel in dashboard["panels"]}
    assert {"Storage Service", "Voice Service", "Email Service"} <= panels.keys()
    assert set(result["overall_health"].values()) == {"Warning"}
    assert max(panel["gridPos"]["y"] + panel["gridPos"]["h"]
               for panel in dashboard["panels"]) <= 18


def test_stale_service_health_is_explicit(tmp_path):
    result = fixture(tmp_path,
        service_generated_at="2026-07-23T00:50:00Z").evaluate(NOW)
    assert result["freshness"]["status"] == "Stale"
    assert result["freshness"]["age_seconds"] == 600
    assert result["freshness"]["source"] == "runtime/services/service-health.json"


def test_zero_offline_devices_and_unknown_are_not_green(tmp_path):
    result = fixture(tmp_path, assets=[{
        "canonical_id": "switch-1", "hostname": "SW-1",
        "device_type": "switch", "online": True,
        "site": {"site_id": "site:hq", "display_name": "HQ"}}]).run(NOW)
    dashboard = json.loads((tmp_path / "grafana/operations-wallboard.json").read_text())
    panels = {panel["title"]: panel for panel in dashboard["panels"]}
    switching = next(value for value in rows(panels["Switches"])
                     if value["scope"] == "all")
    assert switching["Offline"] == "0"
    unknown = next(value for value in panels["Switches"]["fieldConfig"]["overrides"]
                   if value["matcher"]["options"] == "Unknown")
    assert unknown["properties"][0]["value"]["fixedColor"] == "gray"


def test_selected_site_uses_site_service_actions_and_collectors_without_leakage(tmp_path):
    operations = {"issues": [{"id": "hq-pa", "priority": 90,
        "severity": "High", "category": "Security", "title": "PA licence",
        "summary": "HQ Palo Alto licence expired", "device": "HQ-PA",
        "site": "HQ", "site_id": "site:hq", "kind": "issue",
        "rule_id": "PA-LICENCE-EXPIRED", "evidence": {}}],
        "risks": [], "recommendations": []}
    engine = fixture(tmp_path, operations=operations)
    payload = json.loads((tmp_path / "service-health.json").read_text())
    branch = next(value for value in payload["sites"]
                  if value["site_id"] == "site:branch")
    hq = next(value for value in payload["sites"] if value["site_id"] == "site:hq")
    branch["overall_status"] = "Healthy"
    hq["overall_status"] = "Warning"
    next(value for value in branch["services"]
         if value["service"] == "Security")["status"] = "Not Enabled"
    next(value for value in hq["services"]
         if value["service"] == "Security")["status"] = "Warning"
    write(tmp_path / "service-health.json", payload)
    result = engine.run(NOW)
    assert result["overall_health"]["site:branch"] == "Healthy"
    assert result["overall_health"]["site:hq"] == "Warning"
    branch_actions = [value for value in result["actions"]
                      if value["scope"] == "site:branch"]
    hq_actions = [value for value in result["actions"]
                  if value["scope"] == "site:hq"]
    assert [value["issue"] for value in branch_actions] == ["No action required"]
    assert [value["issue"] for value in hq_actions] == ["HQ Palo Alto licence expired"]
    assert {value["collector"] for value in result["collectors"]
            if value["scope"] == "site:branch"} == {"snmp"}
    assert {value["collector"] for value in result["collectors"]
            if value["scope"] == "site:hq"} == {"mist", "snmp"}
    dashboard = json.loads((tmp_path / "grafana/operations-wallboard.json").read_text())
    security = next(value for value in dashboard["panels"]
                    if value["title"] == "Security Service")
    status_rows = rows(security)
    assert next(value for value in status_rows
                if value["scope"] == "site:branch")["value"] == "Not Enabled"
    assert next(value for value in status_rows
                if value["scope"] == "site:hq")["value"] == "Warning"
