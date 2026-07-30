import csv
import json
from datetime import datetime, timezone

from analysis.infrastructure import InfrastructureStateEngine, SignalAdapter
from analysis.infrastructure.fusion import FusionEngine
from analysis.infrastructure.models import AdapterResult
from analysis.operations import OperationsEngine


NOW = datetime(2026, 7, 23, tzinfo=timezone.utc)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value))


def result(name, priority, *assets):
    return AdapterResult(name, priority, assets=list(assets))


def asset(source, asset_id, **values):
    return {"source": source, "collector": source, "asset_id": asset_id,
            "source_asset_id": asset_id, **values}


def fuse(*results, freshness=300):
    return FusionEngine(freshness).fuse(results)


def engine_fixture(tmp_path):
    inventory = tmp_path / "inventory"; operations = tmp_path / "operations"
    sites = tmp_path / "sites.yml"
    sites.write_text("""sites:
  - id: hq
    display_name: HQ
    aliases: [HQ]
  - id: branch
    display_name: Branch
    aliases: [Branch]
""")
    write(inventory / "assets.json", {"assets": [
        asset("mist", "inv-switch", serial_number="SW1", hostname="CORE-1",
              device_type="switch", online=True, site="HQ", management_ip="10.0.0.1", vendor="juniper"),
        asset("mist", "inv-ap", serial_number="AP1", hostname="AP-1",
              device_type="wireless-access-point", online=False, site="HQ",
              management_ip="10.0.0.2", vendor="juniper"),
        asset("snmp", "inv-printer", serial_number="PR1", hostname="PRN-1",
              device_type="printer", online=None, site="Branch", management_ip="10.1.0.2", vendor="hp"),
    ]})
    write(inventory / "source_runs.json", {"sources": {
        "mist": {"consecutive_failures": 0, "last_run": {"success": True,
            "started_at": "2026-07-22T23:59:00Z", "completed_at": "2026-07-23T00:00:00Z"},
            "last_complete_successful_run": {"completed_at": "2026-07-23T00:00:00Z"}},
        "snmp": {"consecutive_failures": 1, "last_run": {"success": False,
            "started_at": "2026-07-22T23:58:00Z", "completed_at": "2026-07-23T00:00:00Z"}},
    }})
    write(operations / "operations.json", {"issues": [{"site": "HQ"}], "risks": [{"site": "Branch"}]})
    return InfrastructureStateEngine(inventory, operations, tmp_path / "state", tmp_path / "dashboard",
                                     sites_config=sites, sites_output=tmp_path / "sites")


def test_four_existing_output_adapters_register_and_clean_bootstrap(tmp_path):
    assert [adapter.name for adapter in SignalAdapter.registered(tmp_path)] == [
        "aruba", "fortigate", "inventory", "mist", "paloalto", "papercut", "snmp",
        "virtualisation"]
    state = InfrastructureStateEngine(tmp_path / "missing", tmp_path / "operations",
                                      tmp_path / "state", tmp_path / "dashboard",
                                      sites_output=tmp_path / "sites").run(NOW)
    assert state["summary"]["devices"] == 0 and state["collectors"] == []
    assert state["summary"]["infrastructure_health"] == \
        "Discovery not configured"
    assert state["summary"]["observability_health"] == \
        "Monitoring not started"
    assert json.loads((tmp_path / "state/state.json").read_text()) == state


def test_collector_adapter_projects_latest_run_not_historical_failure(
        tmp_path):
    inventory = tmp_path / "inventory"
    write(inventory / "source_runs.json", {"sources": {"papercut": {
        "consecutive_failures": 0,
        "last_failed_run": {
            "success": False, "completed_at": "2026-07-22T23:00:00Z"},
        "last_run": {
            "success": True, "partial": False,
            "started_at": "2026-07-22T23:59:00Z",
            "completed_at": "2026-07-23T00:00:00Z"},
        "last_complete_successful_run": {
            "completed_at": "2026-07-23T00:00:00Z"},
    }}})
    adapter = next(value for value in SignalAdapter.registered(inventory)
                   if value.name == "papercut")
    collector = adapter.collect().collectors[0]
    assert collector["status"] == "healthy"
    assert collector["failures"] == 0
    assert collector["last_successful_run"] == "2026-07-23T00:00:00Z"


def test_partial_success_is_warning_not_healthy(tmp_path):
    inventory = tmp_path / "inventory"
    write(inventory / "source_runs.json", {"sources": {"paloalto": {
        "consecutive_failures": 0,
        "last_run": {
            "success": True, "partial": True,
            "started_at": "2026-07-22T23:59:00Z",
            "completed_at": "2026-07-23T00:00:00Z"},
    }}})
    adapter = next(value for value in SignalAdapter.registered(inventory)
                   if value.name == "paloalto")
    assert adapter.collect().collectors[0]["status"] == "warning"


def test_same_serial_merges_with_provenance_and_stable_id():
    mist = asset("mist", "m1", serial_number=" ab 12 ", hostname="Vendor-Name", site="HQ", online=True)
    snmp = asset("snmp", "s1", serial_number="AB12", hostname="SNMP-Name", site="HQ", management_ip="192.0.2.10")
    assets, stats, _ = fuse(result("mist", 200, mist), result("snmp", 100, snmp))
    value = assets[0]
    assert len(assets) == 1 and stats["exact_matches"] == 1 and stats["records_fused"] == 1
    assert value["canonical_id"].startswith("asset:canonical:")
    assert value["management_ip"] == "192.0.2.10"
    assert value["sources"] == ["mist", "snmp"]
    assert value["field_provenance"]["management_ip"]["source"] == "snmp"
    assert value["merge"]["confidence"] == "exact"
    assert fuse(result("snmp", 100, snmp), result("mist", 200, mist))[0][0]["canonical_id"] == value["canonical_id"]


def test_hostname_site_and_fqdn_short_form_merge_mist_snmp():
    mist = asset("mist", "m1", hostname="SW-CORE-01.example.test.", site="Example Site", device_type="network-switch")
    snmp = asset("snmp", "s1", hostname="sw-core-01", site="Example Site", device_type="switch")
    assets, stats, _ = fuse(result("mist", 200, mist), result("snmp", 100, snmp))
    assert len(assets) == 1 and stats["high_confidence_matches"] == 1
    assert "hostname" in assets[0]["merge"]["matched_on"]


def test_same_hostname_different_sites_and_incompatible_types_do_not_merge():
    left = asset("mist", "a", hostname="gateway", site="Site A", device_type="switch")
    other_site = asset("snmp", "b", hostname="gateway", site="Site B", device_type="switch")
    incompatible = asset("snmp", "c", hostname="gateway", site="Site A", device_type="printer")
    assets, stats, low = fuse(result("mist", 200, left), result("snmp", 100, other_site, incompatible))
    assert len(assets) == 3 and stats["records_fused"] == 0
    assert len(low) >= 2


def test_conflicting_serials_do_not_merge_through_serialless_bridge():
    left = asset("mist", "a", hostname="core", site="HQ", device_type="switch", serial_number="ONE")
    bridge = asset("snmp", "b", hostname="core", site="HQ", device_type="switch")
    right = asset("fortigate", "c", hostname="core", site="HQ", device_type="switch", serial_number="TWO")
    assets, stats, low = fuse(result("mist", 200, left), result("snmp", 100, bridge),
                              result("fortigate", 200, right))
    assert len(assets) == 2 and stats["records_fused"] == 1
    assert any("conflict" in value["reason"] for value in low)


def test_inventory_precedence_and_lower_priority_management_ip_fallback():
    inventory = asset("inventory", "i", serial_number="ABC", hostname="Canonical", site="HQ")
    snmp = asset("snmp", "s", serial_number="ABC", hostname="Other", site="HQ", management_ip="192.0.2.20")
    value = fuse(result("snmp", 100, snmp), result("inventory", 300, inventory))[0][0]
    assert value["hostname"] == "Canonical" and value["management_ip"] == "192.0.2.20"
    assert value["field_provenance"]["hostname"]["source"] == "inventory"
    assert value["field_provenance"]["management_ip"]["source"] == "snmp"


def test_fresh_vendor_status_wins_and_fresh_disagreement_is_conflict():
    vendor = asset("mist", "m", serial_number="ABC", online=False,
        last_seen_at="2026-07-23T00:00:00Z")
    stale = asset("snmp", "s", serial_number="ABC", online=True,
        last_seen_at="2026-07-22T23:00:00Z")
    value = fuse(result("mist", 200, vendor), result("snmp", 100, stale), freshness=300)[0][0]
    assert value["status"] == "offline"
    assert not any(conflict["field"] == "status" for conflict in value["merge"]["conflicts"])
    fresh = {**stale, "last_seen_at": "2026-07-22T23:59:00Z"}
    value = fuse(result("mist", 200, vendor), result("snmp", 100, fresh), freshness=300)[0][0]
    assert value["status"] == "offline"
    assert any(conflict["field"] == "status" for conflict in value["merge"]["conflicts"])


def test_device_aware_management_ip_policy_and_remaining_hostname_collision():
    engine = InfrastructureStateEngine()
    ap = {"canonical_id": "ap", "hostname": "AP", "device_type": "access-point",
          "status": "offline", "online": False, "site": "HQ", "merge": {"conflicts": []}}
    core = {"canonical_id": "core", "hostname": "CORE", "device_type": "switch",
            "device_role": "core", "status": "online", "online": True, "site": "HQ",
            "merge": {"conflicts": []}}
    duplicate = {**core, "canonical_id": "core-2"}
    findings = engine._validate([ap, core, duplicate], [])
    ap_finding = next(value for value in findings if value["canonical_id"] == "ap")
    assert ap_finding["type"] == "missing_management_ip" and ap_finding["suppressed"]
    assert any(value["canonical_id"] == "core" and value["actionable"] for value in findings)
    assert sum(value["type"] == "duplicate_hostname" for value in findings) == 2


def test_site_aggregation_health_separation_and_renderer_schema(tmp_path):
    engine = engine_fixture(tmp_path); first = engine.evaluate(NOW); second = engine.evaluate(NOW)
    assert first == second
    summary = first["summary"]
    assert summary["devices"] == 3 and summary["online"] == 1 and summary["offline"] == 1
    assert summary["infrastructure_health"] == "Warning"
    assert summary["observability_health"] == "Warning"
    assert summary["collectors_healthy"] == 1 and summary["collectors_failed"] == 1
    collectors = {value["collector"]: value for value in first["collectors"]}
    assert collectors["mist"]["site_ids"] == ["site:hq"]
    assert collectors["snmp"]["site_ids"] == ["site:branch"]
    assert [site["site_id"] for site in first["sites"]] == ["site:branch", "site:hq"]
    state = engine.run(NOW)
    flat = json.loads((tmp_path / "dashboard/infrastructure-summary.json").read_text())
    assert flat["infrastructure_health"] == "Warning" and flat["observability_health"] == "Warning"
    with (tmp_path / "state/state.csv").open(newline="") as handle: rows = list(csv.DictReader(handle))
    assert len(rows) == 3
    assert set(("canonical_id", "sources", "merge_confidence", "matched_on", "conflict_count")) <= rows[0].keys()
    assert json.loads((tmp_path / "state/state.json").read_text()) == state
    assert json.loads((tmp_path / "sites/sites.json").read_text())["sites"][0]["site_id"] == "site:branch"


def test_operations_uses_only_fused_canonical_assets(tmp_path):
    mist = asset("mist", "m", serial_number="ABC", hostname="AP-1", site="HQ",
                 device_type="access-point", online=False)
    snmp = asset("snmp", "s", serial_number="ABC", hostname="AP-1", site="HQ",
                 device_type="access-point", online=False)
    canonical = fuse(result("mist", 200, mist), result("snmp", 100, snmp))[0]
    state_path = tmp_path / "infrastructure/state.json"
    write(state_path, {"assets": canonical, "collectors": [], "reconciliations": [], "signals": {}})
    engine = OperationsEngine(tmp_path / "inventory", tmp_path / "operations",
        tmp_path / "missing-dashboard.json", infrastructure_state=state_path)
    issues = [value for value in engine.evaluate(NOW)["issues"] if value["rule_id"] == "wireless.ap_offline"]
    assert len(issues) == 1 and issues[0]["canonical_id"] == canonical[0]["canonical_id"]
