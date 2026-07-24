import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from analysis.state_history import (
    FileStateStore,
    StateHistoryEngine,
    observation_from_payload,
)
from analysis.virtualisation import VirtualisationEngine


ROOT = Path(__file__).resolve().parents[2]
T1 = "2026-07-24T01:00:00Z"
T2 = "2026-07-24T02:00:00Z"


def payload(*entities, observed_at=T1):
    return {
        "schema_version": 1,
        "observed_at": observed_at,
        "site_id": "site:hq",
        "domain": "infrastructure",
        "entities": list(entities),
    }


def entity(entity_id, state=None, **values):
    return {
        "site_id": "site:hq",
        "domain": "infrastructure",
        "entity_type": "asset",
        "entity_id": entity_id,
        "state": state or {"hostname": entity_id, "status": "online"},
        **values,
    }


def engine(tmp_path, **values):
    return StateHistoryEngine(FileStateStore(tmp_path / "history"), **values)


def changes(result):
    return [
        value for change_set in result["change_sets"]
        for value in change_set["changes"]
    ]


def test_first_observation_and_empty_store_emit_entity_added(tmp_path):
    store = FileStateStore(tmp_path / "empty")
    assert store.latest("site:hq", "infrastructure") is None
    result = StateHistoryEngine(store).process_payload(payload(entity("switch-1")))
    assert result["change_count"] == 1
    assert changes(result)[0]["change_type"] == "entity_added"
    assert changes(result)[0]["previous_snapshot_id"] is None
    assert store.latest("site:hq", "infrastructure").snapshot_id == \
        result["snapshots"][0]["snapshot_id"]


def test_identical_repeated_observation_has_no_changes(tmp_path):
    history = engine(tmp_path)
    first = history.process_payload(payload(entity("switch-1")))
    second = history.process_payload(payload(entity("switch-1")))
    assert first["change_count"] == 1
    assert second["change_count"] == 0
    assert second["change_sets"][0]["changes"] == []


def test_entity_addition_and_removal_are_exact(tmp_path):
    history = engine(tmp_path)
    history.process_payload(payload(entity("switch-1")))
    added = history.process_payload(payload(
        entity("switch-1"), entity("switch-2"), observed_at=T2))
    assert [(value["change_type"], value["entity_id"]) for value in changes(added)] == [
        ("entity_added", "switch-2")]
    removed = history.process_payload(payload(
        entity("switch-2"), observed_at="2026-07-24T03:00:00Z"))
    assert [(value["change_type"], value["entity_id"]) for value in changes(removed)] == [
        ("entity_removed", "switch-1")]


def test_scalar_nested_and_status_changes_have_precise_paths(tmp_path):
    history = engine(tmp_path)
    history.process_payload(payload(entity("switch-1", {
        "hostname": "CORE-1", "status": "online",
        "location": {"building": "A", "rack": 1}})))
    result = history.process_payload(payload(entity("switch-1", {
        "hostname": "CORE-2", "status": "offline",
        "location": {"building": "A", "rack": 2}}), observed_at=T2))
    values = {(value["change_type"], value["field_path"]):
              (value["previous_value"], value["current_value"])
              for value in changes(result)}
    assert values == {
        ("field_changed", "hostname"): ("CORE-1", "CORE-2"),
        ("field_changed", "location.rack"): (1, 2),
        ("status_changed", "status"): ("online", "offline"),
    }
    status = next(value for value in changes(result)
                  if value["change_type"] == "status_changed")
    assert status["severity"] == "Critical"


def test_dictionary_order_does_not_create_changes(tmp_path):
    history = engine(tmp_path)
    history.process_payload(payload(entity("switch-1", {
        "status": "online", "details": {"model": "EX", "serial": "ABC"}})))
    result = history.process_payload(payload(entity("switch-1", {
        "details": {"serial": "ABC", "model": "EX"}, "status": "online"}),
        observed_at=T2))
    assert result["change_count"] == 0


def test_declared_unordered_collection_order_does_not_create_changes(tmp_path):
    history = engine(tmp_path)
    history.process_payload(payload(entity("switch-1", {
        "status": "online", "tags": ["core", "production"],
        "sources": [{"source": "snmp", "id": "2"},
                    {"source": "inventory", "id": "1"}]})))
    result = history.process_payload(payload(entity("switch-1", {
        "sources": [{"id": "1", "source": "inventory"},
                    {"id": "2", "source": "snmp"}],
        "tags": ["production", "core"], "status": "online"}),
        observed_at=T2))
    assert result["change_count"] == 0


def test_volatile_fields_are_explicitly_excluded(tmp_path):
    history = engine(tmp_path)
    history.process_payload(payload(entity("host-1", {
        "status": "online", "uptime_seconds": 60,
        "last_seen_at": T1, "generated_at": T1})))
    result = history.process_payload(payload(entity("host-1", {
        "status": "online", "uptime_seconds": 3660,
        "last_seen_at": T2, "generated_at": T2}), observed_at=T2))
    assert result["change_count"] == 0
    state = result["snapshots"][0]["entities"][0]["state"]
    assert not {"uptime_seconds", "last_seen_at", "generated_at"} & state.keys()


def test_change_ids_and_output_are_stable_for_equivalent_comparisons(tmp_path):
    outputs = []
    for name in ("left", "right"):
        history = StateHistoryEngine(FileStateStore(tmp_path / name))
        history.process_payload(payload(entity("switch-1")))
        outputs.append(history.process_payload(payload(entity("switch-1", {
            "hostname": "switch-1", "status": "offline"}), observed_at=T2)))
    assert outputs[0] == outputs[1]
    assert changes(outputs[0])[0]["change_id"] == changes(outputs[1])[0]["change_id"]


def test_explicit_timestamp_and_provenance_are_preserved(tmp_path):
    current = payload(entity(
        "switch-1", source="canonical-adapter", provider="platform",
        collected_at="2026-07-24T00:59:30Z"))
    current.pop("observed_at")
    result = engine(tmp_path).process_payload(current, observed_at=T1)
    snapshot = result["snapshots"][0]
    added = changes(result)[0]
    assert snapshot["observed_at"] == T1
    assert snapshot["source"] == "canonical-adapter"
    assert snapshot["provider"] == "platform"
    assert snapshot["entities"][0]["collected_at"] == "2026-07-24T00:59:30Z"
    assert added["source"] == "canonical-adapter"
    assert added["provider"] == "platform"


def test_atomic_state_persistence_leaves_only_complete_json(tmp_path):
    root = tmp_path / "history"
    result = StateHistoryEngine(FileStateStore(root)).process_payload(
        payload(entity("switch-1")))
    files = sorted(root.rglob("*.json"))
    assert len(files) == 3
    assert not list(root.rglob(".*"))
    assert all(json.loads(path.read_text()) for path in files)
    assert json.loads(files[0].read_text())
    assert result["snapshots"][0]["snapshot_id"]


@pytest.mark.parametrize("value", [
    None,
    [],
    {},
    {"observed_at": T1, "entities": "invalid"},
    {"observed_at": T1, "entities": [{"entity_id": "x", "state": []}]},
])
def test_malformed_input_is_rejected(value):
    with pytest.raises(ValueError):
        observation_from_payload(value)


def test_infrastructure_fixture_uses_canonical_assets(tmp_path):
    current = {
        "schema_version": 1, "generated_at": T1,
        "sites": [{"site_id": "site:hq", "display_name": "HQ"}],
        "assets": [{
            "canonical_id": "asset:canonical:1", "hostname": "CORE-1",
            "device_type": "switch", "status": "online",
            "site": {"site_id": "site:hq", "display_name": "HQ"},
            "sources": ["inventory", "snmp"],
        }],
    }
    result = engine(tmp_path).process_payload(current)
    snapshot = result["snapshots"][0]
    assert snapshot["domain"] == "infrastructure"
    assert snapshot["entities"][0]["entity_id"] == "asset:canonical:1"
    assert snapshot["entities"][0]["source"] == ""


def test_operations_fixture_uses_stable_operational_ids(tmp_path):
    current = {
        "schema_version": 1, "generated_at": T1,
        "issues": [{"id": "ops:1", "site_id": "site:hq",
                    "title": "Switch offline", "severity": "High",
                    "status": "active"}],
        "risks": [], "recommendations": [],
    }
    result = engine(tmp_path).process_payload(current)
    value = result["snapshots"][0]["entities"][0]
    assert (value["domain"], value["entity_type"], value["entity_id"]) == (
        "operations", "issue", "ops:1")


def test_virtualisation_fixture_is_provider_neutral_after_adaptation(tmp_path):
    state = VirtualisationEngine(
        ROOT, tmp_path / "virtualisation", "example", "site:hq").fixture("vmware")
    result = engine(tmp_path).process_payload(state)
    snapshot = result["snapshots"][0]
    assert snapshot["domain"] == "virtualisation"
    assert {value["entity_type"] for value in snapshot["entities"]} >= {
        "cluster", "host", "storage", "vm"}
    assert all(value["entity_id"].startswith("virt:")
               for value in snapshot["entities"])


def test_cli_is_machine_readable_and_does_not_load_local_config(tmp_path):
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(payload(entity("switch-1"))))
    store = tmp_path / "runtime/state-history"
    environment = {
        **os.environ,
        "COLLECTOR_CONFIG": str(tmp_path / "missing-config.yml"),
        "PYTHONPATH": str(ROOT),
    }
    completed = subprocess.run([
        sys.executable, "-m", "collectors", "state-history", "process",
        "--input", str(input_path), "--store", str(store), "--json",
    ], cwd=ROOT, env=environment, text=True, capture_output=True)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["change_count"] == 1
    assert FileStateStore(store).latest(
        "site:hq", "infrastructure").snapshot_id == \
        result["snapshots"][0]["snapshot_id"]


def test_cli_returns_nonzero_for_malformed_input(tmp_path):
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{not-json")
    completed = subprocess.run([
        sys.executable, "-m", "collectors", "state-history",
        "--input", str(invalid), "--store", str(tmp_path / "history"),
    ], cwd=ROOT, text=True, capture_output=True)
    assert completed.returncode != 0
    assert "invalid state-history input" in completed.stderr
