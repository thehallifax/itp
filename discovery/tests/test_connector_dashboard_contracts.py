import json
from pathlib import Path

from analysis.dashboards import (
    PanelState,
    contracts_from_points,
    query_outcome_state,
    validate_dashboard_contract,
)
from collectors.mist.normalizer import metric_points, normalize_device
from collectors.papercut.models import PaperCutConfig
from collectors.papercut.normalizer import normalize

ROOT = Path(__file__).resolve().parents[2]


def load(path):
    return json.loads((ROOT / path).read_text())


def mist_points():
    source = load("collectors/mist/fixtures/dashboard-sanitized.json")
    sites = {value["id"]: value["name"] for value in source["sites"]}
    stats = {value["id"]: value for value in source["stats"]}
    points = []
    for device in source["inventory"]:
        record = normalize_device(
            device, stats[device["id"]], sites, "example-org",
            "example-customer", "Example Campus")
        points.extend(metric_points(record, stats[device["id"]]))
    points.append({
        "measurement": "collector_health",
        "tags": {"collector": "mist", "customer": "example-customer",
                 "site": "Example Campus"},
        "fields": source["collector_health"],
    })
    return points


def papercut_points(fixture="healthy.json"):
    settings = PaperCutConfig(
        base_url="https://print.example.invalid:9192",
        authorization_key="", customer="example", site="site:example")
    _records, points, _conditions = normalize(
        load(f"collectors/papercut/fixtures/{fixture}"), settings,
        "2026-01-01T00:00:00Z")
    points.append({
        "measurement": "collector_health",
        "tags": {"collector": "papercut", "deployment_id": "example",
                 "customer_id": "example", "site_id": "site:example"},
        "fields": {"success": True, "duration_ms": 125,
                   "points_written": len(points), "api_requests": 3,
                   "retry_count": 0, "error_count": 0},
    })
    return points


def test_mist_dashboard_queries_match_live_shaped_emitted_contract():
    dashboard = load("dashboards/vendor/mist-infrastructure-overview.json")
    contracts = contracts_from_points(mist_points())
    assert validate_dashboard_contract(dashboard, contracts) == []
    text = json.dumps(dashboard)
    assert "site_id LIKE ${site:sqlstring}" not in text
    assert "site LIKE ${site:sqlstring}" in text
    assert "Healthy - No current operational findings" not in text


def test_papercut_dashboard_queries_match_system_health_contract():
    dashboard = load("dashboards/Printing/papercut-overview.json")
    contracts = contracts_from_points(papercut_points())
    assert validate_dashboard_contract(dashboard, contracts) == []
    panels = {value["title"]: value for value in dashboard["panels"]}
    assert "Collector Health" in panels
    assert "toner" not in json.dumps(dashboard).casefold()
    assert "percent_remaining" not in json.dumps(dashboard)


def test_contract_validator_reports_measurement_and_missing_identity_column():
    dashboard = {"title": "Broken Mist", "panels": [{
        "title": "Device Health", "targets": [{"rawSql": (
            "SELECT hostname FROM infrastructure_device "
            "WHERE site_id LIKE ${site:sqlstring}")}]}]}
    errors = validate_dashboard_contract(
        dashboard, contracts_from_points(mist_points()))
    assert [(value.dashboard, value.panel, value.measurement, value.missing)
            for value in errors] == [(
                "Broken Mist", "Device Health", "infrastructure_device",
                "site_id")]


def test_contract_validator_reports_unemitted_measurement():
    dashboard = {"title": "Unsupported", "panels": [{
        "title": "Consumables", "targets": [{"rawSql": (
            "SELECT toner_percent FROM consumables")}]}]}
    errors = validate_dashboard_contract(
        dashboard, contracts_from_points(papercut_points()))
    assert len(errors) == 1
    assert errors[0].measurement == "consumables"
    assert errors[0].reason == "measurement is not emitted by the collector"


def test_schema_and_datasource_errors_never_become_healthy_empty_states():
    # Both schema and datasource failures arrive as unsuccessful executions;
    # neither may be interpreted using the panel's successful-empty mapping.
    assert query_outcome_state(
        succeeded=False, row_count=0,
        empty_state=PanelState.HEALTHY.value) == "Query failed"
    assert query_outcome_state(
        succeeded=False, row_count=0,
        empty_state="No current operational findings") == "Query failed"
    assert query_outcome_state(
        succeeded=True, row_count=0,
        empty_state="No current operational findings") == (
            "No current operational findings")
    assert query_outcome_state(
        succeeded=True, row_count=4,
        empty_state=PanelState.WAITING.value) == "Populated"


def test_papercut_error_fixture_has_no_fabricated_consumables():
    points = papercut_points("device-errors.json")
    assert any(point["measurement"] == "device"
               and point.get("fields", {}).get("error_state") is True
               for point in points)
    assert all("toner" not in key.casefold()
               for point in points for key in point.get("fields", {}))
