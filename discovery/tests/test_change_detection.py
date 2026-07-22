import asyncio
import json

import pytest

from collectors.__main__ import _run
from collectors.inventory import InventoryEngine, InventoryError, InventoryManager
from collectors.mist.normalizer import normalize_device


T0 = "2026-01-01T00:00:00Z"
T1 = "2026-01-02T00:00:00Z"


def engine(tmp_path, **changes):
    return InventoryEngine(tmp_path, change_detection=changes or None)


def initial(value, **extra):
    record = {"source_asset_id": "device-1", "hostname": "edge-1", "serial_number": "SER-1",
              "mac_address": "00:11:22:33:44:55", "model": "MODEL-1", "site": "site-a",
              "management_ip": "192.0.2.1", "firmware_version": "1.0", **extra}
    return value.ingest("mist", [record], now=T0)["assets"][0]


@pytest.mark.parametrize("field,new,severity,change_type", [
    ("hostname", "edge-2", "low", "value_changed"),
    ("site", "site-b", "medium", "location_changed"),
    ("management_ip", "192.0.2.2", "low", "value_changed"),
    ("firmware_version", "1.1", "info", "firmware_changed"),
    ("model", "MODEL-2", "high", "classification_changed"),
])
def test_meaningful_field_change_classification(tmp_path, field, new, severity, change_type):
    value = engine(tmp_path); asset = initial(value)
    record = {key: asset.get(key) for key in ("source_asset_id", "hostname", "serial_number",
        "mac_address", "model", "site", "management_ip", "firmware_version")}
    record[field] = new
    value.ingest("mist", [record], now=T1)
    event = value.changes()[0]
    assert event["field"] == field and event["severity"] == severity
    assert event["change_type"] == change_type and event["reason"]
    assert set(("event_id", "asset_id", "source", "collector", "field", "previous_value",
        "new_value", "change_type", "severity", "reason", "detected_at", "observed_at",
        "source_run_id", "actor", "metadata")) <= set(event)


@pytest.mark.parametrize("field,new", [("serial_number", "SER-2"),
                                        ("mac_address", "00:11:22:33:44:66")])
def test_identity_conflict_is_high_and_preserves_trusted_value(tmp_path, field, new):
    value = engine(tmp_path); asset = initial(value); old = asset[field]
    record = {"source_asset_id": "device-1", field: new}
    result = value.ingest("mist", [record], now=T1)["assets"][0]
    event = value.changes()[0]
    assert result["asset_id"] == asset["asset_id"] and result[field] == old
    assert event["change_type"] == "identity_conflict" and event["severity"] == "high"


def test_identity_enrichment_is_info_and_retains_asset_id(tmp_path):
    value = engine(tmp_path)
    first = value.ingest("mist", [{"source_asset_id": "device-1"}], now=T0)["assets"][0]
    second = value.ingest("mist", [{"source_asset_id": "device-1", "serial_number": "SER-1"}], now=T1)["assets"][0]
    assert second["asset_id"] == first["asset_id"]
    event = value.changes()[0]
    assert event["field"] == "serial_number" and event["change_type"] == "identity_enriched"
    assert event["severity"] == "info"


def test_optional_added_and_authoritative_removed(tmp_path):
    value = engine(tmp_path); initial(value)
    value.ingest("mist", [{"source_asset_id": "device-1", "display_name": "Edge"}], now=T1)
    added = value.changes()[0]
    assert added["field"] == "display_name" and added["change_type"] == "value_added"
    value.ingest("mist", [{"source_asset_id": "device-1", "_authoritative_fields": ["display_name"]}],
                 now="2026-01-03T00:00:00Z")
    removed = value.changes()[0]
    assert removed["field"] == "display_name" and removed["change_type"] == "value_removed"


def test_non_authoritative_omission_and_partial_removal_are_suppressed(tmp_path):
    value = engine(tmp_path); asset = initial(value, display_name="Edge")
    value.ingest("mist", [{"source_asset_id": "device-1"}], now=T1)
    assert value.changes() == [] and value.get_asset(asset["asset_id"])["display_name"] == "Edge"
    value.ingest("mist", [{"source_asset_id": "device-1", "_authoritative_fields": ["display_name"]}],
                 now="2026-01-03T00:00:00Z", partial=True)
    assert value.changes() == [] and value.get_asset(asset["asset_id"])["display_name"] == "Edge"


def test_null_mac_format_hostname_case_and_identical_suppression(tmp_path):
    value = engine(tmp_path); asset = initial(value, display_name=None)
    value.ingest("mist", [{"source_asset_id": "device-1", "hostname": "EDGE-1",
        "mac_address": "0011.2233.4455", "display_name": ""}], now=T1)
    assert value.changes() == []
    current = value.get_asset(asset["asset_id"])
    assert current["hostname"] == "edge-1" and current["mac_address"] == "001122334455"
    value.ingest("mist", [{"source_asset_id": "device-1", "hostname": "edge-1"}],
                 now="2026-01-03T00:00:00Z")
    assert value.changes() == []


def test_duplicate_event_suppression(tmp_path):
    value = engine(tmp_path, duplicate_suppression_seconds=3600); initial(value)
    value.ingest("mist", [{"source_asset_id": "device-1", "hostname": "edge-2"}],
                 now="2026-01-01T00:10:00Z")
    value.ingest("mist", [{"source_asset_id": "device-1", "hostname": "edge-1"}],
                 now="2026-01-01T00:20:00Z")
    value.ingest("mist", [{"source_asset_id": "device-1", "hostname": "edge-2"}],
                 now="2026-01-01T00:30:00Z")
    assert len(value.changes(limit=10)) == 2


def test_history_retention_count_age_malformed_atomic_and_secret_safe(tmp_path):
    value = engine(tmp_path, history_max_events=2, history_retention_days=30)
    initial(value)
    for day, hostname in ((2, "edge-2"), (3, "edge-3"), (4, "edge-4")):
        value.ingest("mist", [{"source_asset_id": "device-1", "hostname": hostname,
            "api_token": "do-not-store", "access_token": "also-secret"}],
            now=f"2026-01-{day:02d}T00:00:00Z")
    assert len(value.changes(limit=10)) == 2
    value.ingest("mist", [{"source_asset_id": "device-1", "hostname": "edge-new"}],
                 now="2026-03-10T00:00:00Z")
    assert len(value.changes(limit=10)) == 1
    raw = value.change_history_path.read_text()
    assert "do-not-store" not in raw and "also-secret" not in raw
    assert not list(tmp_path.glob(".change_history.json.*"))
    value.change_history_path.write_text("{broken")
    with pytest.raises(InventoryError, match="change_history.json contains malformed JSON"):
        value.load_changes()


def test_suppression_configuration(tmp_path):
    value = engine(tmp_path, ignored_fields=["firmware_version"], minimum_severity="medium",
                   hostname_pattern_exclusions=["lab-*"], device_type_exclusions=["printer"])
    initial(value)
    value.ingest("mist", [{"source_asset_id": "device-1", "firmware_version": "2.0",
                           "hostname": "edge-2"}], now=T1)
    assert value.changes() == []
    other = engine(tmp_path / "host", hostname_pattern_exclusions=["lab-*"])
    initial(other, hostname="lab-one")
    other.ingest("mist", [{"source_asset_id": "device-1", "hostname": "lab-two"}], now=T1)
    assert other.changes() == []


def test_lifecycle_and_unrelated_sources_do_not_create_changes(tmp_path):
    value = InventoryEngine(tmp_path, stale_after_seconds=1, missing_after_seconds=2)
    asset = initial(value)
    run = value.begin_source_run("mist", started_at="2026-01-02T00:00:00Z")
    value.complete_source_run("mist", run, success=True, completed_at="2026-01-02T00:00:01Z")
    value.update_lifecycle("2026-01-02T00:00:02Z")
    value.ingest("snmp", [{"source_asset_id": "other", "hostname": "edge-2"}], now=T1)
    assert value.changes(asset["asset_id"]) == []


def test_retired_asset_inventory_change_is_detected_without_restoration(tmp_path):
    value = engine(tmp_path); asset = initial(value)
    value.retire(asset["asset_id"], "retired", "2026-01-01T12:00:00Z")
    result = value.ingest("mist", [{"source_asset_id": "device-1", "hostname": "edge-2"}], now=T1)
    current = next(item for item in result["assets"] if item["asset_id"] == asset["asset_id"])
    assert current["lifecycle_state"] == "retired" and value.changes(asset["asset_id"])[0]["field"] == "hostname"


def test_mist_authoritative_field_adapter(tmp_path):
    record = normalize_device({"id": "one", "name": "ap", "serial": "SER", "type": "ap"},
        {"status": "connected", "version": "1.0"}, {}, "org", "c", "s")
    assert {"source_asset_id", "hostname", "serial_number", "firmware_version"} <= set(record["_authoritative_fields"])
    manager = InventoryManager(tmp_path / "devices.json", {"persistence_path": str(tmp_path)})
    manager.update_source([record], "mist", "c", "s", T0)
    assert "_authoritative_fields" not in manager.read()["devices"][0]


def test_snmp_ingestion_detects_hostname_change_without_telegraf_contract_change(tmp_path):
    manager = InventoryManager(tmp_path / "devices.json")
    config = {"customer": "c", "site": "s", "discovery": {}, "snmp": {"communities": ["secret"]}}
    first = [("192.0.2.1", 0, ["switch", "1.3.6.1.4.1.9.1", "sw1", "rack"])]
    second = [("192.0.2.1", 0, ["switch", "1.3.6.1.4.1.9.1", "sw2", "rack"])]
    manager.update(config, first, now=T0); manager.update(config, second, now=T1)
    assert manager.engine.changes()[0]["field"] == "hostname"
    assert manager.read()["devices"][0]["community_index"] == 0


def test_cli_change_filters_json_and_summary(tmp_path, capsys):
    config = tmp_path / "config.yml"; config.write_text(f"inventory:\n  persistence_path: {tmp_path}\n")
    value = engine(tmp_path); asset = initial(value)
    value.ingest("mist", [{"source_asset_id": "device-1", "firmware_version": "2.0"}], now=T1)
    args = type("Args", (), {"command": "inventory", "config": str(config), "action": "changes",
        "asset_id": asset["asset_id"], "reason": None, "state": None, "source": "mist",
        "field": "firmware_version", "severity": "info", "since": "2025-01-01T00:00:00Z",
        "limit": 50, "json": True})()
    asyncio.run(_run(args)); output = json.loads(capsys.readouterr().out)
    assert len(output) == 1 and output[0]["field"] == "firmware_version"
    args.action = "changes-summary"; args.asset_id = None; args.json = False
    asyncio.run(_run(args)); output = capsys.readouterr().out
    assert "Total changes: 1" in output and "Firmware changes: 1" in output
