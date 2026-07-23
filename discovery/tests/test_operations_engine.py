import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from analysis.operations import OperationsEngine, Rule


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
    return OperationsEngine(inventory, output,
        ROOT / "dashboards/Infrastructure Overview/infrastructure-overview.json",
        {"collector_overdue_seconds": 900}), output


def test_fourteen_rules_are_auto_registered_and_stable():
    rules = Rule.registered()
    assert len(rules) == 14
    assert [rule.id for rule in rules] == sorted(rule.id for rule in rules)
    assert len({rule.id for rule in rules}) == 14


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
    assert "Top 10 Active Issues" in panels["Active Issues"]["options"]["content"]
    assert "Top 10 Operational Risks" in panels["Operational Risks"]["options"]["content"]
    assert "Top 10 Recommendations" in panels["Recommendations"]["options"]["content"]
    assert dashboard["uid"] == "itp-infrastructure-overview"
