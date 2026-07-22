import asyncio
import json
import time

import pytest

from collectors.inventory import InventoryEngine, InventoryError
from collectors.mist.collector import MistCollector
from collectors.mist.models import MistAuthenticationError
from collectors.scheduler import Scheduler
from collectors.snmp.collector import SNMPCollector
from collectors.__main__ import _run


T0 = "2026-01-01T00:00:00Z"


def observed(engine, source="mist", record=None, seen=T0, completed="2026-01-01T00:00:01Z"):
    run = engine.begin_source_run(source, started_at=seen)
    result = engine.ingest(source, [record or {"source_asset_id": "one", "online": True}],
                           now=seen, source_run_id=run)
    engine.complete_source_run(source, run, success=True, records_returned=1, completed_at=completed)
    return result["assets"][0], run


def empty_success(engine, source="mist", started="2026-01-02T00:00:00Z",
                  completed="2026-01-02T00:00:01Z", partial=False):
    run = engine.begin_source_run(source, started_at=started)
    engine.complete_source_run(source, run, success=True, records_returned=0,
                               completed_at=completed, partial=partial)
    return run


def test_first_second_offline_and_rediscovered_transitions(tmp_path):
    value = InventoryEngine(tmp_path, stale_after_seconds=10, missing_after_seconds=20)
    asset, first_run = observed(value)
    assert asset["lifecycle_state"] == "discovered"
    second_run = value.begin_source_run("mist", started_at="2026-01-01T00:00:02Z")
    second = value.ingest("mist", [{"source_asset_id": "one", "online": True}],
                          now="2026-01-01T00:00:02Z", source_run_id=second_run)["assets"][0]
    value.complete_source_run("mist", second_run, success=True, records_returned=1,
                              completed_at="2026-01-01T00:00:03Z")
    assert second["lifecycle_state"] == "active"

    offline_run = value.begin_source_run("mist", started_at="2026-01-01T00:00:04Z")
    offline = value.ingest("mist", [{"source_asset_id": "one", "online": False}],
                           now="2026-01-01T00:00:04Z", source_run_id=offline_run)["assets"][0]
    value.complete_source_run("mist", offline_run, success=True, records_returned=1,
                              completed_at="2026-01-01T00:00:05Z")
    assert offline["lifecycle_state"] == "offline"
    assert value.update_lifecycle("2026-02-01T00:00:00Z")["assets"][0]["lifecycle_state"] == "offline"

    empty_success(value, started="2026-02-01T00:00:01Z", completed="2026-02-01T00:00:02Z")
    assert value.update_lifecycle("2026-02-01T00:00:03Z")["assets"][0]["lifecycle_state"] == "missing"
    rediscovered, _ = observed(value, seen="2026-02-01T00:00:04Z", completed="2026-02-01T00:00:05Z")
    assert rediscovered["lifecycle_state"] == "active"
    assert value.history(rediscovered["asset_id"])[0]["reason"] == "rediscovered"


def test_failed_disabled_stopped_and_unrelated_sources_do_not_age(tmp_path):
    value = InventoryEngine(tmp_path, stale_after_seconds=1, missing_after_seconds=2)
    asset, _ = observed(value)
    failed = value.begin_source_run("mist", started_at="2026-02-01T00:00:00Z")
    value.complete_source_run("mist", failed, success=False, completed_at="2026-02-01T00:00:01Z",
                              error_category="MistAuthenticationError")
    empty_success(value, "snmp", "2026-02-01T00:00:02Z", "2026-02-01T00:00:03Z")
    result = value.update_lifecycle("2026-02-01T00:00:04Z")["assets"][0]
    assert result["asset_id"] == asset["asset_id"] and result["lifecycle_state"] == "discovered"
    # No run represents both a disabled collector and a stopped scheduler.
    assert value.update_lifecycle("2027-01-01T00:00:00Z")["assets"][0]["lifecycle_state"] == "discovered"


def test_successful_empty_run_ages_but_partial_run_does_not(tmp_path):
    value = InventoryEngine(tmp_path, stale_after_seconds=10, missing_after_seconds=20)
    observed(value)
    empty_success(value, started="2026-01-01T00:00:15Z", completed="2026-01-01T00:00:16Z", partial=True)
    assert value.update_lifecycle("2026-01-01T00:00:17Z")["assets"][0]["lifecycle_state"] == "discovered"
    empty_success(value, started="2026-01-01T00:00:18Z", completed="2026-01-01T00:00:19Z")
    stale = value.update_lifecycle("2026-01-01T00:00:19Z")["assets"][0]
    assert stale["lifecycle_state"] == "stale" and stale["lifecycle_reason"] == "stale_threshold_exceeded"
    empty_success(value, started="2026-01-01T00:00:20Z", completed="2026-01-01T00:00:21Z")
    missing = value.update_lifecycle("2026-01-01T00:00:21Z")["assets"][0]
    assert missing["lifecycle_state"] == "missing" and missing["lifecycle_reason"] == "missing_threshold_exceeded"


def test_early_empty_run_does_not_age_after_scheduler_stops(tmp_path):
    value = InventoryEngine(tmp_path, stale_after_seconds=100, missing_after_seconds=200)
    observed(value)
    empty_success(value, started="2026-01-01T00:00:10Z", completed="2026-01-01T00:00:11Z")
    assert value.update_lifecycle("2027-01-01T00:00:00Z")["assets"][0]["lifecycle_state"] == "discovered"


def test_retire_restore_idempotence_and_history(tmp_path):
    value = InventoryEngine(tmp_path)
    asset, _ = observed(value)
    retired = value.retire(asset["asset_id"], "decommissioned", "2026-01-02T00:00:00Z")
    assert retired["lifecycle_state"] == "retired" and retired["retired_at"]
    count = len(value.history(asset["asset_id"]))
    assert value.retire(asset["asset_id"], "duplicate", "2026-01-03T00:00:00Z") == retired
    assert len(value.history(asset["asset_id"])) == count
    observed(value, seen="2026-01-04T00:00:00Z", completed="2026-01-04T00:00:01Z")
    assert value.get_asset(asset["asset_id"])["lifecycle_state"] == "retired"
    restored = value.restore(asset["asset_id"], "returned to service", "2026-01-05T00:00:00Z")
    assert restored["lifecycle_state"] == "active" and "retired_at" not in restored
    count = len(value.history(asset["asset_id"]))
    assert value.restore(asset["asset_id"], "duplicate", "2026-01-06T00:00:00Z") == restored
    assert len(value.history(asset["asset_id"])) == count
    reasons = [event["reason"] for event in value.history(asset["asset_id"])]
    assert "manually_retired" in reasons and "manually_restored" in reasons


def test_history_retention_count_age_and_secret_redaction(tmp_path):
    value = InventoryEngine(tmp_path, lifecycle_history_max_events=2, lifecycle_history_retention_days=30)
    asset, _ = observed(value, seen="2026-01-01T00:00:00Z")
    value.retire(asset["asset_id"], "old", "2026-01-02T00:00:00Z")
    value.restore(asset["asset_id"], "contains token but value is safe text", "2026-01-03T00:00:00Z")
    assert len(value.history(limit=10)) == 2
    value.retire(asset["asset_id"], "new", "2026-03-01T00:00:00Z")
    events = value.history(limit=10)
    assert len(events) == 1 and events[0]["occurred_at"].startswith("2026-03")
    raw = value.history_path.read_text().lower()
    assert "api_token" not in raw and "authorization" not in raw


def test_source_run_counters_malformed_and_redacted(tmp_path):
    value = InventoryEngine(tmp_path)
    first = value.begin_source_run("mist", started_at=T0)
    value.complete_source_run("mist", first, success=False, completed_at="2026-01-01T00:00:01Z",
                              error_category="AuthenticationError")
    second = value.begin_source_run("mist", started_at="2026-01-01T00:00:02Z")
    value.complete_source_run("mist", second, success=True, records_returned=3,
                              completed_at="2026-01-01T00:00:03Z")
    state = value.load_source_runs()["sources"]["mist"]
    assert state["consecutive_successes"] == 1 and state["consecutive_failures"] == 0
    assert "secret-value" not in value.source_runs_path.read_text()
    value.source_runs_path.write_text("{broken")
    with pytest.raises(InventoryError, match="source_runs.json contains malformed JSON"):
        value.load_source_runs()


def test_scheduler_lifecycle_overlap_and_interval(tmp_path, monkeypatch):
    class SlowEngine:
        calls = 0
        def update_lifecycle(self):
            self.calls += 1; time.sleep(0.03)
            return {"lifecycle_summary": {"assets_evaluated": 1, "transitions": 0,
                "stale": 0, "missing": 0}}
    slow = SlowEngine(); scheduler = Scheduler([], inventory_engine=slow, lifecycle_interval=17)
    first, overlap = asyncio.run(_concurrent_lifecycle(scheduler))
    assert slow.calls == 1 and first is not None and overlap is None

    sleeps = []
    async def stop(interval):
        sleeps.append(interval); raise asyncio.CancelledError
    monkeypatch.setattr(asyncio, "sleep", stop)
    with pytest.raises(asyncio.CancelledError): asyncio.run(scheduler._lifecycle_loop())
    assert 0 < sleeps[0] <= 17


async def _concurrent_lifecycle(scheduler):
    first = asyncio.create_task(scheduler._execute_lifecycle())
    await asyncio.sleep(0.005)
    overlap = await scheduler._execute_lifecycle()
    return await first, overlap


def test_history_filtering(tmp_path):
    value = InventoryEngine(tmp_path)
    asset, _ = observed(value)
    value.retire(asset["asset_id"], "done", "2026-01-02T00:00:00Z")
    assert len(value.history(asset["asset_id"], state="retired", source="mist", limit=1)) == 1


def test_mist_authentication_failure_records_safe_failed_run(tmp_path):
    class API:
        api_requests = 0; retry_count = 0; rate_limit_remaining = None
        async def sites(self): raise MistAuthenticationError("credential-value")
        async def inventory(self): return []
        async def device_stats(self): return []
        async def close(self): pass
    existing = InventoryEngine(tmp_path, stale_after_seconds=1, missing_after_seconds=2)
    asset, _ = observed(existing)
    collector = MistCollector({"customer": "c", "site": "s", "inventory": {
        "stale_after_seconds": 1, "missing_after_seconds": 2,
        "persistence_path": str(tmp_path)}, "collectors": {"mist": {
        "organization_id": "org", "api_token": "credential-value"}}},
        tmp_path / "devices.json", client=API())
    with pytest.raises(MistAuthenticationError): asyncio.run(collector.discover())
    state = collector.inventory.engine.load_source_runs()["sources"]["mist"]
    assert state["last_run"]["success"] is False
    assert state["last_run"]["error_category"] == "MistAuthenticationError"
    assert "credential-value" not in collector.inventory.engine.source_runs_path.read_text()
    unchanged = collector.inventory.engine.update_lifecycle("2026-02-01T00:00:00Z")["assets"][0]
    assert unchanged["asset_id"] == asset["asset_id"] and unchanged["lifecycle_state"] == "discovered"


def test_snmp_success_and_failure_source_runs(tmp_path, monkeypatch):
    config = {"customer": "c", "site": "s", "inventory": {"persistence_path": str(tmp_path)},
              "discovery": {"concurrency": 1}, "snmp": {"communities": ["community"]},
              "networks": []}
    monkeypatch.setattr("collectors.snmp.collector.enumerate_targets", lambda _config: [])
    collector = SNMPCollector(config, tmp_path / "devices.json", tmp_path / "generated")
    asyncio.run(collector.discover())
    state = collector.inventory.engine.load_source_runs()["sources"]["snmp"]
    assert state["last_run"]["success"] is True and state["last_run"]["records_returned"] == 0

    def fail(_config): raise RuntimeError("community must not be persisted")
    monkeypatch.setattr("collectors.snmp.collector.enumerate_targets", fail)
    with pytest.raises(RuntimeError): asyncio.run(collector.discover())
    state = collector.inventory.engine.load_source_runs()["sources"]["snmp"]
    assert state["last_run"]["success"] is False
    assert "community must not be persisted" not in collector.inventory.engine.source_runs_path.read_text()


def test_cli_retire_history_and_json(tmp_path, capsys):
    config = tmp_path / "config.yml"
    config.write_text(f"inventory:\n  persistence_path: {tmp_path}\n")
    value = InventoryEngine(tmp_path)
    asset, _ = observed(value)
    args = type("Args", (), {"command": "inventory", "config": str(config), "action": "retire",
        "asset_id": asset["asset_id"], "reason": "maintenance", "state": None,
        "source": None, "limit": 50, "json": False})()
    asyncio.run(_run(args))
    assert "Lifecycle state: retired" in capsys.readouterr().out
    args.action = "history"; args.reason = None; args.json = True
    asyncio.run(_run(args))
    events = json.loads(capsys.readouterr().out)
    assert events[0]["reason"] == "manually_retired" and events[0]["actor"] == "operator"
