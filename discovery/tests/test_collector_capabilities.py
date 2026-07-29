import json
from datetime import datetime, timezone

import pytest

from analysis.readiness import evaluate_readiness
from analysis.services import ServiceEvaluator
from collectors.capabilities import (
    COLLECTION_STATES, MANIFESTS, Capability, CapabilityManifestEngine,
    validate_manifest)


def _config(**enabled):
    return {"deployment": {"id": "test"}, "collectors": {
        name: {"enabled": value} for name, value in enabled.items()}}


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def test_declarations_are_complete_and_valid():
    assert {"paloalto", "papercut", "snmp", "aruba", "framework"} == set(MANIFESTS)
    for values in MANIFESTS.values():
        assert values and len({value.id for value in values}) == len(values)
        for value in values:
            if value.support == "conditional":
                assert value.condition
            if value.support == "unsupported":
                assert value.reason


def test_unsupported_cannot_be_overwritten_by_runtime_success(tmp_path):
    _write(tmp_path / "scheduler/state.json", {"connectors": {"paloalto": {
        "last_collection_outcome": "success"}}})
    result = CapabilityManifestEngine(_config(paloalto=True), tmp_path).build()
    expiry = next(value for value in result["collectors"]["paloalto"]["capabilities"]
                  if value["id"] == "certificate_expiry")
    assert (expiry["support"], expiry["collection"]) == (
        "unsupported", "not_applicable")


@pytest.mark.parametrize("outcome,expected", [
    (None, "not_yet_collected"), ("failed", "failed"), ("success", "collected")])
def test_runtime_collection_states(tmp_path, outcome, expected):
    if outcome:
        _write(tmp_path / "scheduler/state.json", {"connectors": {"paloalto": {
            "last_collection_outcome": outcome}}})
    result = CapabilityManifestEngine(_config(paloalto=True), tmp_path).build()
    availability = next(
        value for value in result["collectors"]["paloalto"]["capabilities"]
        if value["id"] == "availability")
    assert availability["collection"] == expected


def test_disabled_and_zero_target_snmp_are_not_failures(tmp_path):
    disabled = CapabilityManifestEngine(_config(snmp=False), tmp_path).build()
    assert {value["collection"] for value in
            disabled["collectors"]["snmp"]["capabilities"]} <= {
                "disabled", "not_applicable"}
    _write(tmp_path / "inventory/source_runs.json", {"sources": {"snmp": {
        "last_run": {"success": True, "records_returned": 0}}}})
    enabled = CapabilityManifestEngine(_config(snmp=True), tmp_path).build()
    values = {value["id"]: value["collection"] for value in
              enabled["collectors"]["snmp"]["capabilities"]}
    assert values["discovery"] == "collected"
    assert values["device_polling"] == "unavailable"
    assert enabled["collectors"]["snmp"]["last_collection"]["status"] == "success"


def test_generation_is_deterministic_redacted_and_has_required_files(tmp_path):
    config = _config(paloalto=True)
    config["collectors"]["paloalto"]["api_key"] = "super-secret"
    engine = CapabilityManifestEngine(config, tmp_path)
    assert engine.generate() == engine.generate()
    content = (tmp_path / "capabilities/collectors.json").read_text()
    assert "super-secret" not in content
    for name in ("collectors", "paloalto", "papercut", "snmp", "aruba"):
        assert (tmp_path / f"capabilities/{name}.json").is_file()
    assert not (tmp_path / "capabilities/framework.json").exists()


def test_readiness_uses_manifest_failure():
    result = evaluate_readiness(
        enabled_collectors=["paloalto"],
        capability_manifest={"collectors": {"paloalto": {
            "execution": {"state": "failed"},
            "last_collection": {"observed_at": "2026-01-01T00:00:00Z"}}}},
        now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert result["collectors"][0]["state"] == "unavailable"


def test_invalid_declarations_fail_closed():
    with pytest.raises(ValueError):
        Capability("bad", "Bad", "conditional")
    with pytest.raises(ValueError):
        Capability("bad", "Bad", "unsupported")
    with pytest.raises(ValueError, match="schema version"):
        validate_manifest({"schema_version": 2, "collectors": {}})
    with pytest.raises(ValueError, match="collection state"):
        validate_manifest({"schema_version": 1, "collectors": {"bad": {
            "capabilities": [{"support": "supported", "collection": "bogus"}]}}})


def test_collection_state_vocabulary_is_stable():
    assert COLLECTION_STATES == {
        "collected", "not_yet_collected", "disabled", "unavailable", "failed",
        "partial", "not_applicable"}


def _service_context(capabilities):
    return {
        "capabilities": frozenset({"printing"}),
        "enabled_collectors": ("papercut",),
        "capability_manifest": {"papercut": {
            "capabilities": capabilities}},
        "assets": [], "findings": [], "signals": {}, "collectors": [],
    }


def test_unsupported_capability_does_not_degrade_service():
    evaluator = next(value for value in ServiceEvaluator.registered()
                     if value.definition.name == "Printing")
    result = evaluator.evaluate(_service_context([{
        "id": "consumables", "support": "unsupported",
        "collection": "not_applicable", "services": ["Printing"],
        "explanation": "Not exposed by the API."}]))
    assert result.status == "Unknown"


def test_failed_required_capability_degrades_service():
    evaluator = next(value for value in ServiceEvaluator.registered()
                     if value.definition.name == "Printing")
    result = evaluator.evaluate(_service_context([{
        "id": "availability", "support": "supported",
        "collection": "failed", "services": ["Printing"],
        "explanation": "Latest collection failed."}]))
    assert result.status == "Critical"
    assert result.evidence[0]["capability"] == "availability"
