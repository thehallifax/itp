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
            sites_text=None, capability_manifests=None):
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
    manifest = tmp_path / "capabilities/collectors.json"
    if capability_manifests is not None:
        write(manifest, {"collectors": capability_manifests})
    sites = tmp_path / "sites.yml"
    if sites_text is not None:
        sites.write_text(sites_text)
    return ServiceHealthEngine(state, operations, registry, output,
                               sites_config=sites,
                               capability_manifest=manifest), output


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


def printing_manifest(state="collected"):
    return {"papercut": {"capabilities": [
        {"id": "server_health", "services": ["Printing"],
         "support": "supported", "collection": "collected"},
        {"id": "printer_operational_status", "services": ["Printing"],
         "support": "conditional", "collection": state},
        {"id": "printer_consumables", "services": ["Printing"],
         "support": "unsupported", "collection": "not_applicable"},
    ]}}


def printing_service(tmp_path, assets, state="collected"):
    engine, _ = fixture(
        tmp_path, capabilities=["printing"], assets=assets,
        enabled_collectors=["papercut"],
        collector_capabilities={"papercut": ["printing"]},
        capability_manifests=printing_manifest(state))
    return service(engine.evaluate(NOW), "Printing")


def printer(name, *, online=True, conditions=(), lifecycle="active"):
    return {
        "canonical_id": "printer:" + name, "hostname": name,
        "device_type": "printer", "online": online,
        "lifecycle_state": lifecycle,
        "last_seen_at": "2026-07-23T13:59:00Z",
        "extensions": {"printer_conditions": list(conditions)},
    }


def test_papercut_server_healthy_without_device_health_is_unknown(tmp_path):
    server = {"canonical_id": "server:print", "hostname": "PRINT-1",
              "device_type": "server", "device_role": "print-management-server",
              "online": True, "extensions": {"printing_health": {
                  "server_health_available": True,
                  "printer_health_available": False}}}
    value = printing_service(tmp_path, [server], "unavailable")
    assert value["status"] == "Unknown"
    assert "per-printer health has not been collected" in value["summary"]


@pytest.mark.parametrize(("percentage", "expected"), [
    (2, "Critical"), (10, "Critical"), (11, "Warning"), (30, "Warning")])
def test_printing_toner_thresholds_are_deterministic(
        tmp_path, percentage, expected):
    value = printing_service(tmp_path, [printer("PRN-1", conditions=[{
        "condition": "Low toner", "condition_type": "consumable",
        "percent_remaining": percentage}])])
    assert value["status"] == expected


def test_printing_explicit_error_partial_stale_and_healthy_states(tmp_path):
    error = printing_service(tmp_path / "error", [printer("PRN-ERROR", online=False,
        conditions=[{"condition": "In Error",
                     "condition_type": "operational_error"}])])
    partial = printing_service(tmp_path / "partial", [printer("PRN-OK")], "partial")
    stale = printing_service(tmp_path / "stale", [printer(
        "PRN-STALE", lifecycle="stale")])
    healthy = printing_service(tmp_path / "healthy", [printer("PRN-OK")])
    assert error["status"] == "Critical"
    assert partial["status"] == "Warning"
    assert stale["status"] == "Warning"
    assert healthy["status"] == "Healthy"


def test_printing_multiple_assets_are_ordered_and_vendor_data_does_not_leak(tmp_path):
    value = printing_service(tmp_path, [
        printer("PRN-Z", conditions=[{"condition": "Low toner",
            "condition_type": "consumable", "percent_remaining": 25}]),
        printer("PRN-A", online=False, conditions=[{"condition": "In Error",
            "condition_type": "operational_error"}]),
    ])
    assert value["status"] == "Critical"
    assert value["affected_assets"] == ["PRN-A", "PRN-Z"]
    condition_evidence = [item for item in value["evidence"]
                          if item.get("type") == "printer_condition"]
    assert all("papercut" not in json.dumps(item).casefold()
               for item in condition_evidence)


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


def test_internet_evidence_separates_interface_identity_from_display_name(tmp_path):
    signal = {"name": "ethernet1/6", "interface_name": "ethernet1/6",
              "display_name": "WAN 2", "role": "secondary",
              "available": False, "classification_authoritative": True,
              "observed_at": "2026-07-23T13:59:00Z"}
    engine, _ = fixture(tmp_path, capabilities=["internet"],
                        signals={"wan": [signal]})
    internet = service(engine.evaluate(NOW), "Internet")
    assert internet["affected_assets"] == ["WAN 2"]
    assert internet["evidence"][0]["interface"] == "ethernet1/6"
    assert internet["evidence"][0]["display_name"] == "WAN 2"


def internet_manifest(collector, support="conditional", collection="unavailable"):
    return {collector: {"capabilities": [{
        "id": "wan_classification", "services": ["Internet"],
        "support": support, "collection": collection,
    }]}}


def _paloalto_internet(tmp_path, signals):
    engine, _ = fixture(tmp_path, capabilities=["internet"],
        enabled_collectors=["paloalto"],
        collector_capabilities={"paloalto": ["internet"]},
        capability_manifests=internet_manifest(
            "paloalto", collection="collected"),
        signals={"wan": signals})
    return service(engine.evaluate(NOW), "Internet")


def test_paloalto_only_valid_primary_wan_is_healthy(tmp_path):
    internet = _paloalto_internet(tmp_path, [{
        "interface_name": "ethernet1/1", "display_name": "Primary",
        "role": "primary", "available": True,
        "classification_authoritative": True,
        "observed_at": "2026-07-23T13:59:00Z",
    }])
    assert internet["status"] == "Healthy"
    assert internet["affected_assets"] == []


def test_paloalto_primary_up_remains_healthy(tmp_path):
    internet = _paloalto_internet(tmp_path, [
        {"interface_name": "ethernet1/1", "display_name": "Primary",
         "role": "primary", "available": True,
         "classification_authoritative": True,
         "observed_at": "2026-07-23T13:59:00Z"},
        {"interface_name": "ethernet1/2", "display_name": "Backup",
         "role": "secondary", "available": True,
         "classification_authoritative": True,
         "observed_at": "2026-07-23T13:59:00Z"},
    ])
    assert internet["status"] == "Healthy"


def test_paloalto_primary_down_secondary_up_is_warning(tmp_path):
    internet = _paloalto_internet(tmp_path, [
        {"interface_name": "ethernet1/1", "display_name": "Primary",
         "role": "primary", "available": False,
         "classification_authoritative": True,
         "observed_at": "2026-07-23T13:59:00Z"},
        {"interface_name": "ethernet1/2", "display_name": "Backup",
         "role": "secondary", "available": True,
         "classification_authoritative": True,
         "observed_at": "2026-07-23T13:59:00Z"},
    ])
    assert internet["status"] == "Warning"
    assert internet["affected_assets"] == ["Primary"]


def test_all_paloalto_wans_down_is_critical(tmp_path):
    internet = _paloalto_internet(tmp_path, [
        {"interface_name": "ethernet1/1", "display_name": "Primary",
         "role": "primary", "available": False,
         "classification_authoritative": True,
         "observed_at": "2026-07-23T13:59:00Z"},
        {"interface_name": "ethernet1/2", "display_name": "Backup",
         "role": "secondary", "available": False,
         "classification_authoritative": True,
         "observed_at": "2026-07-23T13:59:00Z"},
    ])
    assert internet["status"] == "Critical"
    assert internet["affected_assets"] == ["Backup", "Primary"]


def test_paloalto_wan_evaluation_requires_no_fortigate_specific_fields(tmp_path):
    signal = {
        "interface_name": "ethernet1/1", "display_name": "Primary",
        "role": "primary", "available": True,
        "classification_authoritative": True,
        "observed_at": "2026-07-23T13:59:00Z",
    }
    assert not {"missing", "collector", "wan_classified"} & set(signal)
    assert _paloalto_internet(tmp_path, [signal])["status"] == "Healthy"


def test_mixed_paloalto_and_fortigate_wan_sources_are_deterministic(tmp_path):
    manifests = {
        **internet_manifest("paloalto", collection="collected"),
        **internet_manifest("fortigate", collection="collected"),
    }
    signals = [
        {"interface_name": "ethernet1/1", "display_name": "PA Primary",
         "role": "primary", "available": True,
         "classification_authoritative": True,
         "observed_at": "2026-07-23T13:59:00Z"},
        {"interface_name": "wan1", "display_name": "FG Primary",
         "role": "primary", "available": True,
         "classification_authoritative": True,
         "observed_at": "2026-07-23T13:59:00Z"},
    ]
    engine, _ = fixture(tmp_path, capabilities=["internet"],
        enabled_collectors=["paloalto", "fortigate"],
        collector_capabilities={
            "paloalto": ["internet"], "fortigate": ["internet"]},
        capability_manifests=manifests, signals={"wan": signals})
    first = service(engine.evaluate(NOW), "Internet")
    second = service(engine.evaluate(NOW), "Internet")
    assert first == second
    assert first["status"] == "Healthy"
    assert [value["interface"] for value in first["evidence"]] == [
        "ethernet1/1", "wan1"]


def test_fortigate_internet_configuration_guidance_is_vendor_aware(tmp_path):
    engine, _ = fixture(tmp_path, capabilities=["internet"],
        enabled_collectors=["fortigate"],
        collector_capabilities={"fortigate": ["internet"]},
        capability_manifests=internet_manifest("fortigate"))
    internet = service(engine.evaluate(NOW), "Internet")
    assert internet["status"] == "Configuration Required"
    assert internet["summary"] == (
        "Configure one or more WAN interfaces in the FortiGate collector configuration.")
    assert "Palo Alto" not in json.dumps(internet)


def test_fortigate_missing_configured_wan_is_warning(tmp_path):
    engine, _ = fixture(tmp_path, capabilities=["internet"],
        enabled_collectors=["fortigate"],
        collector_capabilities={"fortigate": ["internet"]},
        capability_manifests=internet_manifest(
            "fortigate", collection="collected"),
        signals={"wan": [{
            "interface_name": "wan-example", "display_name": "Primary",
            "role": "primary", "available": None, "missing": True,
            "classification_authoritative": True,
            "observed_at": "2026-07-23T13:59:00Z",
        }]})
    internet = service(engine.evaluate(NOW), "Internet")
    assert internet["status"] == "Warning"
    assert internet["summary"] == (
        "A configured WAN interface was not discovered.")
    assert internet["affected_assets"] == ["Primary"]


def test_paloalto_internet_configuration_guidance_is_vendor_aware(tmp_path):
    engine, _ = fixture(tmp_path, capabilities=["internet"],
        enabled_collectors=["paloalto"],
        collector_capabilities={"paloalto": ["internet"]},
        capability_manifests=internet_manifest("paloalto"))
    internet = service(engine.evaluate(NOW), "Internet")
    assert internet["status"] == "Configuration Required"
    assert "Palo Alto collector configuration" in internet["summary"]
    assert "FortiGate" not in json.dumps(internet)


def test_no_firewall_collector_means_internet_not_enabled(tmp_path):
    engine, _ = fixture(tmp_path, capabilities=[], enabled_collectors=[])
    internet = service(engine.evaluate(NOW), "Internet")
    assert internet["status"] == "Not Enabled"


def test_unsupported_firewall_wan_telemetry_is_not_available(tmp_path):
    engine, _ = fixture(tmp_path, capabilities=["internet"],
        enabled_collectors=["future-firewall"],
        collector_capabilities={"future-firewall": ["internet"]},
        capability_manifests={"future-firewall": {"capabilities": [{
            "id": "interfaces", "services": ["Internet"],
            "support": "supported", "collection": "failed",
        }]}})
    internet = service(engine.evaluate(NOW), "Internet")
    assert internet["status"] == "Not Available"
    assert internet["summary"] == (
        "WAN telemetry is not available for the active firewall collector.")


def test_multiple_firewall_collectors_are_evaluated_deterministically(tmp_path):
    manifests = {
        **internet_manifest("paloalto"),
        **internet_manifest("fortigate"),
    }
    engine, _ = fixture(tmp_path, capabilities=["internet"],
        enabled_collectors=["paloalto", "fortigate"],
        collector_capabilities={
            "paloalto": ["internet"], "fortigate": ["internet"]},
        capability_manifests=manifests)
    first = service(engine.evaluate(NOW), "Internet")
    second = service(engine.evaluate(NOW), "Internet")
    assert first == second
    assert first["status"] == "Configuration Required"
    assert first["summary"].startswith("Configure one or more WAN interfaces in the FortiGate")
    assert "Palo Alto collector configuration" in first["summary"]


def test_multiple_firewalls_preserve_healthy_evidence_and_report_failed_source(tmp_path):
    manifests = {
        **internet_manifest("fortigate", collection="failed"),
        **internet_manifest("paloalto", collection="collected"),
    }
    engine, _ = fixture(tmp_path, capabilities=["internet"],
        enabled_collectors=["fortigate", "paloalto"],
        collector_capabilities={
            "fortigate": ["internet"], "paloalto": ["internet"]},
        capability_manifests=manifests, signals={"wan": [{
            "name": "ethernet1/1", "role": "primary", "available": True,
            "classification_authoritative": True,
            "observed_at": "2026-07-23T13:59:00Z",
        }]})
    internet = service(engine.evaluate(NOW), "Internet")
    assert internet["status"] == "Warning"
    assert "another firewall source failed" in internet["summary"]


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
