import csv
import json
from datetime import datetime, timezone

from analysis.infrastructure import InfrastructureStateEngine, SignalAdapter
from analysis.infrastructure.models import AdapterResult


NOW = datetime(2026, 7, 23, tzinfo=timezone.utc)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value))


def engine_fixture(tmp_path):
    inventory = tmp_path / "inventory"; operations = tmp_path / "operations"
    write(inventory / "assets.json", {"assets": [
        {"asset_id": "inv-switch", "serial_number": "SW1", "hostname": "CORE-1",
         "device_type": "switch", "online": True, "site": "HQ", "management_ip": "10.0.0.1",
         "collector": "mist", "vendor": "juniper"},
        {"asset_id": "inv-ap", "serial_number": "AP1", "hostname": "AP-1",
         "device_type": "wireless-access-point", "online": False, "site": "HQ",
         "management_ip": "10.0.0.2", "collector": "mist", "vendor": "juniper"},
        {"asset_id": "inv-printer", "serial_number": "PR1", "hostname": "PRN-1",
         "device_type": "printer", "online": None, "site": "Branch", "management_ip": "10.1.0.2",
         "collector": "snmp", "vendor": "hp"},
    ]})
    write(inventory / "source_runs.json", {"sources": {
        "mist": {"consecutive_failures": 0, "last_run": {"success": True,
            "started_at": "2026-07-22T23:59:00Z", "completed_at": "2026-07-23T00:00:00Z"},
            "last_complete_successful_run": {"completed_at": "2026-07-23T00:00:00Z"}},
        "snmp": {"consecutive_failures": 1, "last_run": {"success": False,
            "started_at": "2026-07-22T23:58:00Z", "completed_at": "2026-07-23T00:00:00Z"}},
    }})
    write(operations / "operations.json", {"issues": [{"site": "HQ"}], "risks": [{"site": "Branch"}]})
    return InfrastructureStateEngine(inventory, operations, tmp_path / "state", tmp_path / "dashboard")


def test_four_existing_output_adapters_register_and_absence_is_safe(tmp_path):
    assert [adapter.name for adapter in SignalAdapter.registered(tmp_path)] == [
        "fortigate", "inventory", "mist", "snmp"]
    state = InfrastructureStateEngine(tmp_path / "missing", tmp_path / "operations",
                                      tmp_path / "state", tmp_path / "dashboard").evaluate(NOW)
    assert state["summary"]["devices"] == 0 and state["collectors"] == []


def test_merge_priority_duplicate_suppression_and_conflicting_state():
    engine = InfrastructureStateEngine()
    inventory = AdapterResult("inventory", 300, assets=[
        {"asset_id": "inventory-id", "serial_number": "ABC", "hostname": "CANONICAL", "online": True}])
    vendor = AdapterResult("mist", 200, assets=[
        {"asset_id": "vendor-id", "serial_number": "ABC", "hostname": "VENDOR", "online": False,
         "management_ip": "10.0.0.1"}])
    assets, warnings = engine._merge([vendor, inventory])
    assert len(assets) == 1
    assert assets[0]["hostname"] == "CANONICAL"
    assert assets[0]["management_ip"] == "10.0.0.1"
    assert assets[0]["online"] is True
    assert any(value["type"] == "conflicting_device_state" for value in warnings)
    assert any(value["type"] == "duplicate_serial" for value in warnings)


def test_site_and_domain_aggregation_is_deterministic(tmp_path):
    engine = engine_fixture(tmp_path)
    first = engine.evaluate(NOW); second = engine.evaluate(NOW)
    assert first == second
    assert first["summary"] == {"sites": 2, "devices": 3, "online": 1, "offline": 1,
        "warnings": 0, "critical": 0, "collectors_healthy": 1, "collectors_failed": 1}
    assert [site["site"] for site in first["sites"]] == ["Branch", "HQ"]
    assert first["sites"][0]["risks"] == 1 and first["sites"][1]["issues"] == 1
    assert first["network"]["switches"]["total"] == 1
    assert first["wireless"]["aps"]["offline"] == 1
    assert first["wireless"]["clients_connected"] is None


def test_renderer_writes_state_csv_and_flat_summary(tmp_path):
    engine = engine_fixture(tmp_path); state = engine.run(NOW)
    rendered = json.loads((tmp_path / "state/state.json").read_text())
    summary = json.loads((tmp_path / "dashboard/infrastructure-summary.json").read_text())
    assert rendered == state
    assert summary["devices"] == 3 and summary["devices_online"] == 1
    assert summary["infrastructure_health"] == "Critical"
    assert summary["switches_total"] == 1 and summary["aps_offline"] == 1
    with (tmp_path / "state/state.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["asset_id"] for row in rows] == ["inv-ap", "inv-printer", "inv-switch"]
