import asyncio
import json
from pathlib import Path

import pytest

from collectors.__main__ import _run
from collectors.inventory import InventoryEngine, InventoryError, InventoryManager, stable_asset_id


NOW = "2026-01-01T00:00:00Z"


def engine(tmp_path, **kwargs):
    return InventoryEngine(tmp_path, **kwargs)


def test_stable_asset_id_uses_serial_and_repeats(tmp_path):
    record = {"serial_number": " ABC 123 ", "source_asset_id": "device-1", "mac_address": "00:11:22:33:44:55"}
    assert stable_asset_id("mist", record) == stable_asset_id("mist", {**record, "hostname": "renamed"})
    inventory = engine(tmp_path).ingest("mist", [record], now=NOW)
    first = inventory["assets"][0]["asset_id"]
    inventory = engine(tmp_path).ingest("mist", [{**record, "hostname": "new"}], now="2026-01-02T00:00:00Z")
    assert len(inventory["assets"]) == 1 and inventory["assets"][0]["asset_id"] == first


def test_richer_identity_does_not_replace_existing_source_asset_id(tmp_path):
    value = engine(tmp_path)
    first = value.ingest("mist", [{"source_asset_id": "device-1", "hostname": "ap"}], now=NOW)["assets"][0]
    second = value.ingest("mist", [{"source_asset_id": "device-1", "serial": "NEW-SERIAL"}],
                          now="2026-01-02T00:00:00Z")["assets"][0]
    assert first["asset_id"] == second["asset_id"]


def test_serial_and_mac_reconciliation(tmp_path):
    value = engine(tmp_path)
    value.ingest("mist", [{"serial": "SER-1", "mac": "00:11:22:33:44:55"}], now=NOW)
    result = value.ingest("snmp", [{"serial_number": "SER-1", "mac_address": "001122334455"}], now=NOW)
    assert {item["reconciliation_status"] for item in result["assets"]} == {"exact_match"}
    assert value.reconcile()["reconciliations"][0]["evidence"] == ["serial_number", "mac_address"]

    other = engine(tmp_path / "mac")
    other.ingest("mist", [{"source_asset_id": "one", "mac": "00:11:22:33:44:66"}], now=NOW)
    result = other.ingest("snmp", [{"management_ip": "192.0.2.2", "mac": "001122334466"}], now=NOW)
    assert {item["reconciliation_status"] for item in result["assets"]} == {"strong_match"}


def test_hostname_only_is_ambiguous_and_never_merged(tmp_path):
    value = engine(tmp_path)
    value.ingest("mist", [{"source_asset_id": "one", "hostname": "edge-1"}], now=NOW)
    result = value.ingest("snmp", [{"management_ip": "192.0.2.1", "hostname": "EDGE-1"}], now=NOW)
    assert len(result["assets"]) == 2
    assert {item["reconciliation_status"] for item in result["assets"]} == {"ambiguous"}


def test_conflicting_strong_identifiers_are_explained(tmp_path):
    value = engine(tmp_path)
    value.ingest("mist", [{"serial": "A", "mac": "00:11:22:33:44:55", "hostname": "edge"}], now=NOW)
    result = value.ingest("snmp", [{"serial": "B", "mac": "00:11:22:33:44:66", "hostname": "edge"}], now=NOW)
    assert {item["reconciliation_status"] for item in result["assets"]} == {"conflicting"}
    assert "strong identifiers conflict" in value.reconcile()["reconciliations"][0]["evidence"][0]


def test_timestamps_and_lifecycle_transitions(tmp_path):
    value = engine(tmp_path, stale_after_seconds=100, missing_after_seconds=200)
    first = value.ingest("mist", [{"source_asset_id": "one", "online": True}], now=NOW)["assets"][0]
    assert first["lifecycle_state"] == "discovered" and first["first_seen_at"] == NOW
    second_time = "2026-01-01T00:00:10Z"
    second = value.ingest("mist", [{"source_asset_id": "one", "online": True}], now=second_time)["assets"][0]
    assert second["lifecycle_state"] == "active" and second["first_seen_at"] == NOW
    assert second["last_seen_at"] == second_time
    run = value.begin_source_run("mist", started_at="2026-01-01T00:02:00Z")
    value.complete_source_run("mist", run, success=True, records_returned=0,
                              completed_at="2026-01-01T00:02:01Z")
    assert value.update_lifecycle("2026-01-01T00:02:02Z")["assets"][0]["lifecycle_state"] == "stale"
    missing_run = value.begin_source_run("mist", started_at="2026-01-01T00:03:40Z")
    value.complete_source_run("mist", missing_run, success=True, records_returned=0,
                              completed_at="2026-01-01T00:03:41Z")
    assert value.update_lifecycle("2026-01-01T00:04:00Z")["assets"][0]["lifecycle_state"] == "missing"


def test_offline_and_retired_are_deterministic(tmp_path):
    value = engine(tmp_path)
    asset = value.ingest("mist", [{"source_asset_id": "one", "online": False}], now=NOW)["assets"][0]
    assert asset["lifecycle_state"] == "offline"
    value.set_lifecycle(asset["asset_id"], "retired", NOW)
    value.ingest("mist", [{"source_asset_id": "one", "online": True}], now="2026-01-02T00:00:00Z")
    assert value.update_lifecycle("2026-02-01T00:00:00Z")["assets"][0]["lifecycle_state"] == "retired"


def test_persistence_is_atomic_deterministic_and_redacted(tmp_path):
    value = engine(tmp_path)
    value.ingest("mist", [{"source_asset_id": "one", "hostname": "ap", "api_token": "do-not-store",
                           "raw_metadata": {"authorization": "secret", "safe": "value"}}], now=NOW)
    first = value.assets_path.read_bytes()
    value.save(value.load())
    assert value.assets_path.read_bytes() == first
    assert b"do-not-store" not in first and b"authorization" not in first
    assert not list(tmp_path.glob(".assets.json.*"))


def test_legacy_loading_and_malformed_handling(tmp_path):
    legacy = tmp_path / "devices.json"
    legacy.write_text(json.dumps({"customer": "c", "site": "s", "devices": [
        {"ip": "192.0.2.1", "hostname": "switch", "sys_object_id": "1.3.6", "status": "active"}]}))
    value = InventoryEngine(tmp_path, legacy_path=legacy)
    asset = value.load()["assets"][0]
    assert asset["source"] == "snmp" and asset["management_ip"] == "192.0.2.1"
    value.assets_path.write_text("{broken")
    with pytest.raises(InventoryError, match="assets.json contains malformed JSON"):
        value.load()


def test_manager_preserves_legacy_snmp_output_and_ingests_assets(tmp_path):
    manager = InventoryManager(tmp_path / "devices.json")
    config = {"customer": "c", "site": "s", "discovery": {}, "snmp": {"communities": ["secret"]}}
    discovery = [("192.0.2.1", 0, ["switch", "1.3.6.1.4.1.9.1", "sw1", "rack"])]
    legacy = manager.update(config, discovery, now=NOW)
    assert legacy["devices"][0]["community_index"] == 0
    assert manager.engine.list_assets()[0]["collector"] == "snmp"
    assert "secret" not in manager.engine.assets_path.read_text()


def test_cli_summary_and_json_output(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.yml"
    config.write_text(f"inventory:\n  persistence_path: {tmp_path}\n")
    InventoryEngine(tmp_path).ingest("mist", [{"source_asset_id": "one", "vendor": "juniper"}], now=NOW)
    args = type("Args", (), {"command": "inventory", "config": str(config), "action": "summary",
                              "asset_id": None, "json": False})()
    asyncio.run(_run(args))
    assert "Total assets: 1" in capsys.readouterr().out
    args.json = True
    asyncio.run(_run(args))
    assert json.loads(capsys.readouterr().out)["total_assets"] == 1
