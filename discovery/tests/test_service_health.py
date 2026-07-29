import csv
import json
from datetime import datetime, timezone

import pytest

from analysis.services import SERVICE_NAMES, ServiceEvaluator, ServiceHealthEngine


NOW = datetime(2026, 7, 23, 14, 0, tzinfo=timezone.utc)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def fixture(tmp_path, *, capabilities=(), assets=(), collectors=(), signals=None,
            issues=(), risks=(), enabled_collectors=(), collector_capabilities=None,
            sites_text=None):
    state = tmp_path / "infrastructure/state.json"
    operations = tmp_path / "operations/operations.json"
    registry = tmp_path / "dashboard/managed/registry.json"
    output = tmp_path / "services"
    write(state, {"assets": list(assets), "collectors": list(collectors),
                  "signals": signals or {}})
    write(operations, {"issues": list(issues), "risks": list(risks),
                       "recommendations": []})
    enabled = list(enabled_collectors) or [
        value["collector"] for value in collectors if value.get("collector")]
    write(registry, {"capabilities": list(capabilities),
                     "enabled_collectors": enabled,
                     "collector_capabilities": collector_capabilities or {}})
    sites = tmp_path / "sites.yml"
    if sites_text is not None:
        sites.write_text(sites_text)
    return ServiceHealthEngine(state, operations, registry, output,
                               sites_config=sites), output


def service(result, name, site_id="all"):
    scope = result["estate"] if site_id == "all" else next(
        value for value in result["sites"] if value["site_id"] == site_id)
    return next(value for value in scope["services"] if value["service"] == name)


@pytest.mark.parametrize(("name", "capability", "asset_type", "signal", "collectors"), [
    ("Internet", "internet", "", {"wan": [{
        "available": True, "classification_authoritative": True,
        "observed_at": "2026-07-23T13:59:00Z"}]}, []),
    ("Wireless", "wireless", "wireless-access-point", {}, []),
    ("Switching", "switching", "network-switch", {}, []),
    ("Printing", "printing", "printer", {}, []),
    ("Identity", "identity", "domain-controller", {}, []),
    ("Compute", "compute", "server", {}, []),
    ("Storage", "storage", "storage-array", {}, []),
    ("Voice", "voice", "sip-phone", {}, []),
    ("Email", "email", "mail-server", {}, []),
    ("Security", "firewall", "firewall", {}, []),
    ("Monitoring", "telemetry", "", {}, [
        {"collector": "snmp", "status": "healthy", "last_run": "2026-07-23T13:59:00Z"}]),
])
def test_each_service_evaluator_reports_healthy_from_positive_evidence(
        tmp_path, name, capability, asset_type, signal, collectors):
    assets = [] if not asset_type else [{
        "canonical_id": "asset:1", "hostname": "asset-1",
        "device_type": asset_type, "online": True,
        "last_seen_at": "2026-07-23T13:58:00Z",
    }]
    engine, _ = fixture(tmp_path, capabilities=[capability], assets=assets,
                        collectors=collectors, signals=signal)
    value = service(engine.evaluate(NOW), name)
    assert value["status"] == "Healthy"
    assert value["severity"] == "Info"
    assert value["evidence"]


def test_capability_disabled_services_are_explicit_and_do_not_use_stale_assets(tmp_path):
    engine, _ = fixture(tmp_path, assets=[{
        "canonical_id": "ap:1", "hostname": "AP-1",
        "device_type": "wireless-access-point", "online": False,
    }])
    result = engine.evaluate(NOW)
    assert len(result["estate"]["services"]) == len(SERVICE_NAMES) == 11
    assert all(value["status"] == "Not Enabled" for value in result["estate"]["services"])
    assert service(result, "Wireless")["affected_assets"] == []


def test_enabled_without_evidence_is_unknown_not_healthy(tmp_path):
    engine, _ = fixture(tmp_path, capabilities=["identity"])
    value = service(engine.evaluate(NOW), "Identity")
    assert value["status"] == "Unknown"
    assert value["summary"].startswith("No trustworthy")


def test_multi_collector_canonical_asset_is_counted_once_and_vendor_names_are_irrelevant(tmp_path):
    asset = {"canonical_id": "asset:firewall:1", "hostname": "EDGE-1",
             "device_type": "firewall", "online": False,
             "sources": ["first-source", "second-source"],
             "last_changed_at": "2026-07-23T13:30:00Z"}
    issue = {"id": "ops:1", "rule_id": "firewall.unavailable",
             "title": "Firewall unavailable", "category": "Firewall",
             "severity": "Critical", "priority": 95, "device": "EDGE-1",
             "summary": "Canonical firewall is offline.", "evidence": {}}
    engine, _ = fixture(tmp_path, capabilities=["firewall"], assets=[asset],
                        issues=[issue], enabled_collectors=["first-source", "second-source"])
    first = engine.evaluate(NOW)
    second = engine.evaluate(NOW)
    value = service(first, "Security")
    assert first == second
    assert value["status"] == "Critical"
    assert value["affected_assets"] == ["EDGE-1"]
    assert value["last_change"] == "2026-07-23T13:30:00Z"


def test_warning_and_affected_users_are_explainable(tmp_path):
    risk = {"id": "ops:risk", "rule_id": "wireless.capacity",
            "title": "Wireless capacity", "category": "Wireless",
            "severity": "Medium", "priority": 60, "device": "AP-1",
            "summary": "Capacity threshold exceeded.",
            "evidence": {"affected_users": 24}}
    engine, _ = fixture(tmp_path, capabilities=["wireless"], risks=[risk])
    value = service(engine.evaluate(NOW), "Wireless")
    assert value["status"] == "Warning"
    assert value["severity"] == "Medium"
    assert value["affected_users"] == 24
    assert value["affected_assets"] == ["AP-1"]
    assert value["evidence"][0]["rule_id"] == "wireless.capacity"


def test_internet_only_consumes_wan_network_findings(tmp_path):
    unrelated = {"id": "switch", "rule_id": "network.switch_offline",
                 "title": "Switch offline", "category": "Network",
                 "severity": "High", "priority": 80, "device": "SW-1",
                 "summary": "Switch offline.", "evidence": {}}
    engine, _ = fixture(tmp_path, capabilities=["internet"],
                        signals={"wan": [{"available": True,
                            "classification_authoritative": True,
                            "observed_at": "2026-07-23T13:59:00Z"}]},
                        issues=[unrelated])
    assert service(engine.evaluate(NOW), "Internet")["status"] == "Healthy"


@pytest.mark.parametrize(("signals", "expected"), [
    ([], "Unknown"),
    ([{"name": "Primary", "role": "primary", "available": False,
       "classification_authoritative": True,
       "observed_at": "2026-07-23T13:59:00Z"},
      {"name": "Backup", "role": "backup", "available": True,
       "classification_authoritative": True,
       "observed_at": "2026-07-23T13:59:00Z"}], "Warning"),
    ([{"name": "Primary", "role": "primary", "available": False,
       "classification_authoritative": True,
       "observed_at": "2026-07-23T13:59:00Z"},
      {"name": "Backup", "role": "backup", "available": False,
       "classification_authoritative": True,
       "observed_at": "2026-07-23T13:59:00Z"}], "Critical"),
    ([{"name": "Primary", "role": "primary", "available": True,
       "classification_authoritative": True,
       "observed_at": "2026-07-23T13:00:00Z"}], "Unknown"),
])
def test_internet_uses_authoritative_uplink_policy(tmp_path, signals, expected):
    engine, _ = fixture(tmp_path, capabilities=["internet"],
                        signals={"wan": signals})
    assert service(engine.evaluate(NOW), "Internet")["status"] == expected


def test_security_uses_subscription_and_certificate_evidence(tmp_path):
    signal = {"device": "PA-1", "observed_at": "2026-07-23T13:59:00Z",
        "device_certificate": {"classification": "valid", "status": "Valid"},
        "licenses": [{"name": "Threat Prevention", "expired": True,
                      "expiry_state": "expired", "expiry": "2026-01-01"}]}
    engine, _ = fixture(tmp_path, capabilities=["firewall"],
        assets=[{"canonical_id": "pa:1", "hostname": "PA-1",
                 "device_type": "firewall", "online": True}],
        signals={"security": [signal]})
    assert service(engine.evaluate(NOW), "Security")["status"] == "Warning"


def test_collector_failure_drives_monitoring_critical(tmp_path):
    engine, _ = fixture(tmp_path, capabilities=["telemetry"], collectors=[
        {"collector": "mist", "status": "healthy", "last_run": "2026-07-23T13:59:00Z"},
        {"collector": "snmp", "status": "failed", "last_run": "2026-07-23T13:58:00Z"},
    ])
    value = service(engine.evaluate(NOW), "Monitoring")
    assert value["status"] == "Critical"
    assert [item["collector"] for item in value["evidence"]] == ["mist", "snmp"]


def test_disabled_collector_state_and_findings_do_not_affect_monitoring(tmp_path):
    disabled_finding = {"id": "ops:disabled", "rule_id": "collector.failed",
        "title": "Collector failed: old", "category": "Collector", "severity": "High",
        "priority": 80, "device": "old", "summary": "Historical failure.", "evidence": {}}
    engine, _ = fixture(tmp_path, capabilities=["telemetry"], collectors=[
        {"collector": "active", "status": "healthy", "last_run": "2026-07-23T13:59:00Z"},
        {"collector": "old", "status": "failed", "last_run": "2026-07-20T00:00:00Z"},
    ], issues=[disabled_finding], enabled_collectors=["active"])
    value = service(engine.evaluate(NOW), "Monitoring")
    assert value["status"] == "Healthy"
    assert [item["collector"] for item in value["evidence"]] == ["active"]


def test_outputs_are_stable_valid_json_and_csv(tmp_path):
    engine, output = fixture(tmp_path, capabilities=["telemetry"],
        collectors=[{"collector": "snmp", "status": "healthy",
                     "last_run": "2026-07-23T13:59:00Z"}],
        enabled_collectors=["snmp"])
    result = engine.run(NOW)
    assert json.loads((output / "service-health.json").read_text()) == result
    with (output / "service-health.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 11
    assert [row["service"] for row in rows] == list(SERVICE_NAMES)
    assert json.loads(next(row for row in rows if row["service"] == "Monitoring")["evidence"])


def test_evaluator_registry_is_complete_and_deterministic():
    first = ServiceEvaluator.registered()
    second = ServiceEvaluator.registered()
    assert [value.definition.name for value in first] == sorted(SERVICE_NAMES)
    assert [value.definition.name for value in second] == sorted(SERVICE_NAMES)


SITES = """sites:
  - id: example-school
    display_name: example-school Reference Site
    aliases: [example-school, example-school Reference Site]
  - id: example-corporate
    display_name: Northwind College
    aliases: [Northwind, example-corporate]
"""


def test_per_site_partitioning_aliases_collectors_and_estate_aggregate(tmp_path):
    assets = [
        {"canonical_id": "fw:1", "hostname": "example-school-PA", "device_type": "firewall",
         "online": True, "site": "example-school", "sources": ["paloalto"]},
        {"canonical_id": "ap:1", "hostname": "example-corporate-AP", "device_type": "access-point",
         "online": False, "site": "Northwind", "sources": ["mist"]},
        {"canonical_id": "sw:1", "hostname": "example-corporate-SW", "device_type": "switch",
         "online": True, "site": "example-corporate", "sources": ["snmp"]},
    ]
    collectors = [
        {"collector": "paloalto", "status": "healthy",
         "site_ids": ["example-school"], "last_run": "2026-07-23T13:59:00Z"},
        {"collector": "mist", "status": "healthy",
         "site_ids": ["example-corporate"], "last_run": "2026-07-23T13:59:00Z"},
        {"collector": "snmp", "status": "healthy",
         "site_ids": ["example-corporate"], "last_run": "2026-07-23T13:59:00Z"},
        {"collector": "fortigate", "status": "healthy",
         "site_ids": ["example-corporate"], "last_run": "2026-07-23T13:59:00Z"},
    ]
    pa_risk = {"id": "pa-license", "rule_id": "PA-LICENCE-EXPIRED",
        "title": "Palo Alto licence", "category": "Security", "severity": "High",
        "priority": 80, "device": "example-school-PA", "site": "example-school Reference Site",
        "summary": "example-school Palo Alto licence expired.", "evidence": {}}
    issues = [
        {"id": "ap-down", "rule_id": "wireless.ap_offline",
         "title": "AP offline", "category": "Wireless", "severity": "High",
         "priority": 80, "device": "example-corporate-AP", "site": "Northwind",
         "summary": "Northwind AP is offline.", "evidence": {}},
        {"id": "forti-down", "rule_id": "firewall.unavailable",
         "title": "FortiGate unavailable", "category": "Firewall", "severity": "Critical",
         "priority": 95, "device": "example-corporate-FGT", "site": "example-corporate",
         "summary": "Northwind FortiGate unavailable.",
         "evidence": {"source_collector": "fortigate"}},
    ]
    caps = {"paloalto": ["firewall", "internet", "telemetry"],
            "mist": ["wireless", "switching", "telemetry"],
            "snmp": ["switching", "telemetry"],
            "fortigate": ["firewall", "internet", "telemetry"]}
    engine, _ = fixture(tmp_path, assets=assets, collectors=collectors,
        issues=issues, risks=[pa_risk],
        capabilities=sorted({item for values in caps.values() for item in values}),
        enabled_collectors=list(caps), collector_capabilities=caps, sites_text=SITES)
    result = engine.evaluate(NOW)
    assert [value["site_id"] for value in result["sites"]] == [
        "site:example-school", "site:example-corporate"]
    example_school = next(value for value in result["sites"] if value["site_id"] == "site:example-school")
    st = next(value for value in result["sites"] if value["site_id"] == "site:example-corporate")
    assert example_school["site_name"] == "example-school Reference Site"
    assert example_school["enabled_collectors"] == ["paloalto"]
    assert st["enabled_collectors"] == ["fortigate", "mist", "snmp"]
    example_school_security = service(result, "Security", "site:example-school")
    st_wireless = service(result, "Wireless", "site:example-corporate")
    assert example_school_security["status"] == "Warning"
    assert any("Palo Alto" in item.get("summary", "") for item in example_school_security["evidence"])
    assert not any("AP" in item.get("summary", "") or "FortiGate" in item.get("summary", "")
                   for item in example_school_security["evidence"])
    assert st_wireless["status"] == "Critical"
    assert any("AP" in item.get("summary", "") for item in st_wireless["evidence"])
    assert not any("Palo Alto" in item.get("summary", "")
                   for item in st_wireless["evidence"])
    assert result["estate"]["overall_status"] == "Critical"
    assert len(service(result, "Security")["affected_assets"]) == 2


def test_unmapped_sites_create_diagnostics_not_site_leakage(tmp_path):
    engine, _ = fixture(tmp_path, capabilities=["switching"],
        assets=[{"canonical_id": "unknown", "device_type": "switch",
                 "online": False, "site": "Unconfigured Campus"}],
        issues=[{"id": "unknown-finding", "rule_id": "network.switch_offline",
                 "category": "Network", "severity": "High", "priority": 80,
                 "device": "UNKNOWN", "site": "Nowhere", "summary": "Offline"}],
        sites_text=SITES)
    result = engine.evaluate(NOW)
    assert {value["record_id"] for value in result["diagnostics"]} >= {
        "unknown", "unknown-finding"}
    assert all(service(result, "Switching", site_id)["status"] == "Not Enabled"
               for site_id in ("site:example-school", "site:example-corporate"))
