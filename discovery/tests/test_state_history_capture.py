import json
import asyncio
import subprocess
import sys

import pytest

from analysis.state_history import (
    FileStateStore,
    ObservationCompleteness,
    ObservationScope,
    PipelineRun,
    PipelineStateCapture,
    StateHistoryEngine,
    observation_from_payload,
)
from collectors.scheduler import Scheduler


T1 = "2026-07-24T01:00:00Z"
T2 = "2026-07-24T02:00:00Z"


def payload(at, entities):
    return {"generated_at": at, "entities": entities}


def entity(identifier, status="online", site="site-a", source="mist"):
    return {"site_id": site, "domain": "infrastructure",
            "entity_type": "asset", "entity_id": identifier,
            "source": source, "provider": source,
            "state": {"status": status, "name": identifier}}


def run(identifier, at, completeness="complete", site="site-a",
        observed=("mist",), expected=("mist",), failed=()):
    scope = ObservationScope(
        site, "infrastructure", completeness,
        expected_sources=tuple(sorted(expected)),
        observed_sources=tuple(sorted(observed)),
        failed_sources=tuple(sorted(failed)))
    return PipelineRun(identifier, T1, at, "success", (scope,))


def capture(engine, metadata, state):
    return engine.capture(metadata, observation_from_payload(state))


def test_complete_capture_emits_removal(tmp_path):
    engine = StateHistoryEngine(FileStateStore(tmp_path))
    capture(engine, run("one", T1), payload(T1, [entity("a"), entity("b")]))
    result = capture(engine, run("two", T2), payload(T2, [entity("a")]))
    assert [change["change_type"] for change in result.change_sets[0]["changes"]] == [
        "entity_removed"]


@pytest.mark.parametrize("completeness", [
    "partial", "failed", "skipped", "unknown",
])
def test_non_complete_capture_suppresses_removal_but_keeps_changes(
        tmp_path, completeness):
    engine = StateHistoryEngine(FileStateStore(tmp_path))
    capture(engine, run("one", T1), payload(T1, [entity("a"), entity("b")]))
    metadata = run("two-" + completeness, T2, completeness,
                   observed=("mist",), expected=("mist",))
    result = capture(
        engine, metadata, payload(T2, [entity("a", "offline"), entity("c")]))
    changes = result.change_sets[0]["changes"]
    assert "entity_removed" not in {item["change_type"] for item in changes}
    assert {(item["entity_id"], item["change_type"]) for item in changes} >= {
        ("a", "status_changed"), ("c", "entity_added")}
    assert result.scope_results[0]["removals_suppressed"] == 1


def test_failed_provider_and_site_are_isolated(tmp_path):
    store = FileStateStore(tmp_path)
    engine = StateHistoryEngine(store)
    first = payload(T1, [
        entity("a", site="site-a", source="mist"),
        entity("b", site="site-a", source="fortigate"),
        entity("c", site="site-b", source="mist"),
    ])
    scopes = (
        ObservationScope("site-a", "infrastructure", "complete",
                         expected_sources=("fortigate", "mist"),
                         observed_sources=("fortigate", "mist")),
        ObservationScope("site-b", "infrastructure", "complete",
                         expected_sources=("mist",), observed_sources=("mist",)),
    )
    capture(engine, PipelineRun("one", T1, T1, "success", scopes), first)
    site_b_prior = store.latest("site-b", "infrastructure").snapshot_id
    scopes = (
        ObservationScope("site-a", "infrastructure", "partial",
                         expected_sources=("fortigate", "mist"),
                         observed_sources=("mist",), failed_sources=("fortigate",)),
        ObservationScope("site-b", "infrastructure", "failed",
                         expected_sources=("mist",), failed_sources=("mist",)),
    )
    result = capture(engine, PipelineRun("two", T1, T2, "success", scopes),
                     payload(T2, [entity("a", "offline", source="mist")]))
    assert result.scope_results[0]["removals_suppressed"] == 1
    assert result.scope_results[1]["captured"] is False
    assert store.latest("site-b", "infrastructure").snapshot_id == site_b_prior


def test_capture_is_idempotent(tmp_path):
    engine = StateHistoryEngine(FileStateStore(tmp_path))
    metadata = run("same", T1)
    first = capture(engine, metadata, payload(T1, [entity("a")]))
    second = capture(engine, metadata, payload(T1, [entity("a")]))
    assert second == first
    assert len(list((tmp_path / "runs").glob("*.json"))) == 1


def test_persistence_failure_preserves_latest(tmp_path, monkeypatch):
    store = FileStateStore(tmp_path)
    engine = StateHistoryEngine(store)
    capture(engine, run("one", T1), payload(T1, [entity("a")]))
    prior = store.latest("site-a", "infrastructure").snapshot_id
    monkeypatch.setattr(store, "write_capture_result",
                        lambda result: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        capture(engine, run("two", T2), payload(T2, [entity("a", "offline")]))
    assert store.latest("site-a", "infrastructure").snapshot_id == prior


def test_disabled_pipeline_capture_creates_no_store(tmp_path):
    store = tmp_path / "history"
    capture_bridge = PipelineStateCapture({"enabled": False,
                                           "store_path": str(store)})
    assert capture_bridge.capture_generated(
        payload(T1, [entity("a")]), started_at=T1) is None
    assert not store.exists()


def test_pipeline_capture_defaults_to_non_authoritative(tmp_path):
    bridge = PipelineStateCapture({"enabled": True,
                                   "store_path": str(tmp_path)})
    result = bridge.capture_generated(
        payload(T1, [entity("a")]), started_at=T1, completed_at=T1,
        canonical_output="state.json")
    assert result.status == "degraded"
    assert result.scope_results[0]["removal_authoritative"] is False


def test_invalid_completeness_and_empty_store(tmp_path):
    with pytest.raises(ValueError, match="unsupported observation completeness"):
        ObservationScope("site", "domain", "maybe")
    assert FileStateStore(tmp_path).latest("site", "domain") is None


def test_capture_and_inspect_cli(tmp_path):
    state = tmp_path / "state.json"
    metadata = tmp_path / "run.json"
    state.write_text(json.dumps(payload(T1, [entity("a")])))
    metadata.write_text(json.dumps(run("cli-run", T1).to_dict()))
    command = [sys.executable, "-m", "collectors", "state-history"]
    captured = subprocess.run(
        command + ["capture-run", "--input", str(state),
                   "--run-metadata", str(metadata), "--store", str(tmp_path / "s"),
                   "--json"], text=True, capture_output=True)
    assert captured.returncode == 0, captured.stderr
    inspected = subprocess.run(
        command + ["inspect-run", "--run-id", "cli-run",
                   "--store", str(tmp_path / "s"), "--json"],
        text=True, capture_output=True)
    assert inspected.returncode == 0, inspected.stderr
    assert json.loads(inspected.stdout)["run_id"] == "cli-run"


def test_pipeline_success_captures_and_generation_failure_does_not(tmp_path):
    class Engine:
        def __init__(self, result=None, error=None):
            self.result, self.error = result, error
        def run(self):
            if self.error:
                raise self.error
            return self.result

    class Capture:
        def __init__(self):
            self.calls = []
        def capture_generated(self, value, **kwargs):
            self.calls.append(value)
            return None

    output = payload(T1, [entity("a")])
    bridge = Capture()
    scheduler = Scheduler([], infrastructure_engine=Engine(output),
                          operations_engine=Engine({
                              "issues": [], "risks": [], "recommendations": []}),
                          state_history_capture=bridge)
    assert asyncio.run(scheduler._execute_operations()) is not None
    assert bridge.calls == [output]

    bridge = Capture()
    scheduler = Scheduler([], infrastructure_engine=Engine(error=ValueError("bad")),
                          operations_engine=Engine({}), state_history_capture=bridge)
    assert asyncio.run(scheduler._execute_operations()) is None
    assert bridge.calls == []
