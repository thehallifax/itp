import asyncio
import json
from datetime import datetime, timezone

import pytest

from collectors.scheduler import Scheduler


NOW = datetime(2026, 7, 28, 1, 2, 3, tzinfo=timezone.utc)


class Collector:
    name = "example"
    discovery_interval = 120
    collection_interval = 30

    def __init__(self, events, *, discovery_error=False,
                 collection_error=False):
        self.events = events
        self.discovery_error = discovery_error
        self.collection_error = collection_error

    async def discover(self):
        self.events.append("discover.begin")
        await asyncio.sleep(0)
        if self.discovery_error:
            raise RuntimeError("token=must-not-appear")
        self.events.append("discover.complete")

    async def collect(self):
        self.events.append("collect.begin")
        await asyncio.sleep(0)
        if self.collection_error:
            raise RuntimeError("password=must-not-appear")
        self.events.append("collect.complete")


def scheduler(tmp_path, collector):
    return Scheduler(
        [collector], state_path=tmp_path / "scheduler/state.json",
        now_fn=lambda: NOW)


def test_startup_is_sequential_ready_and_secret_safe(tmp_path, caplog):
    events = []
    value = scheduler(tmp_path, Collector(events))
    result = asyncio.run(value.startup())

    assert events == [
        "discover.begin", "discover.complete",
        "collect.begin", "collect.complete"]
    assert result["lifecycle_state"] == "ready"
    state = json.loads((tmp_path / "scheduler/state.json").read_text())
    assert state["lifecycle_state"] == "ready"
    assert state["initial_discovery"]["outcome"] == "success"
    assert state["initial_collection"]["outcome"] == "success"
    assert state["last_successful_discovery"].endswith("Z")
    assert state["last_successful_collection"].endswith("Z")
    assert "skipped_overlap" not in caplog.text
    assert "must-not-appear" not in caplog.text


def test_discovery_only_run_remains_scheduled_after_initial_discovery(tmp_path):
    events = []
    collector = Collector(events)
    collector.discovery_interval = 3600
    value = scheduler(tmp_path, collector)

    async def exercise():
        task = asyncio.create_task(value.run(discovery_only=True))
        for _ in range(20):
            if events == ["discover.begin", "discover.complete"]:
                break
            await asyncio.sleep(0)
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert events == ["discover.begin", "discover.complete"]


def test_initial_discovery_failure_skips_dependent_collection(tmp_path):
    events = []
    value = scheduler(
        tmp_path, Collector(events, discovery_error=True))
    result = asyncio.run(value.startup())

    assert events == ["discover.begin"]
    assert result["collection"] == ()
    state = value.state.value
    assert state["lifecycle_state"] == "degraded"
    assert state["initial_discovery"]["outcome"] == "failed"
    assert state["initial_collection"]["outcome"] == \
        "skipped_prerequisite"
    assert state["last_skip_reason"] == "prerequisite_unavailable"
    assert state["consecutive_discovery_failures"] == 1
    assert "must-not-appear" not in json.dumps(state)


def test_failed_prerequisite_does_not_block_other_connector(tmp_path):
    events = []
    unavailable = Collector(events, discovery_error=True)
    unavailable.name = "unavailable"
    healthy = Collector(events)
    healthy.name = "healthy"
    value = Scheduler(
        [unavailable, healthy],
        state_path=tmp_path / "scheduler/state.json",
        now_fn=lambda: NOW)

    result = asyncio.run(value.startup())

    by_id = {item["connector"]: item for item in result["collection"]}
    assert by_id["healthy"]["status"] == "success"
    assert "unavailable" not in by_id
    assert events.count("collect.begin") == 1
    assert events.count("collect.complete") == 1
    assert value.state.value["initial_collection"]["outcome"] == "failed"
    assert value.state.value["initial_collection"]["outcome"] != \
        "skipped_prerequisite"


def test_initial_collection_failure_is_recoverable_degraded(tmp_path):
    events = []
    value = scheduler(
        tmp_path, Collector(events, collection_error=True))
    result = asyncio.run(value.startup())

    assert result["lifecycle_state"] == "degraded"
    assert events == [
        "discover.begin", "discover.complete", "collect.begin"]
    assert value.state.value["consecutive_collection_failures"] == 1
    assert value.state.value["last_collection_outcome"] == "failed"
    assert "must-not-appear" not in json.dumps(value.state.value)


def test_success_resets_failure_counter(tmp_path):
    events = []
    collector = Collector(events, collection_error=True)
    value = scheduler(tmp_path, collector)
    asyncio.run(value.startup())
    assert value.state.value["consecutive_collection_failures"] == 1
    collector.collection_error = False
    asyncio.run(value._execute_detailed(collector, "collect"))
    assert value.state.value["consecutive_collection_failures"] == 0
    assert value.state.value["last_error_class"] is None
    assert value.state.value["last_safe_error_summary"] is None
    assert value.state.value["last_failure"] == NOW.isoformat().replace(
        "+00:00", "Z")


def test_recoverable_failure_returns_to_ready_after_later_successes(
        tmp_path):
    collector = Collector([], discovery_error=True)
    value = scheduler(tmp_path, collector)
    asyncio.run(value.startup())
    assert value.state.value["lifecycle_state"] == "degraded"
    collector.discovery_error = False
    asyncio.run(value._execute_detailed(collector, "discover"))
    asyncio.run(value._execute_detailed(collector, "collect"))
    assert value.state.value["lifecycle_state"] == "ready"
    assert value.state.value["consecutive_discovery_failures"] == 0
    assert value.state.value["consecutive_collection_failures"] == 0
    assert value.state.value["last_error_class"] is None
    assert value.state.value["last_safe_error_summary"] is None
    assert value.state.value["last_skip_reason"] is None


def test_scheduler_root_error_remains_until_every_connector_recovers(tmp_path):
    first = Collector([], collection_error=True)
    first.name = "papercut"
    second = Collector([], collection_error=True)
    second.name = "paloalto"
    value = Scheduler(
        [first, second], state_path=tmp_path / "scheduler/state.json",
        now_fn=lambda: NOW)
    asyncio.run(value.startup())
    assert value.state.value["last_error_class"] == "RuntimeError"

    first.collection_error = False
    asyncio.run(value._execute_detailed(first, "collect"))
    assert value.state.value["last_error_class"] == "RuntimeError"

    second.collection_error = False
    asyncio.run(value._execute_detailed(second, "collect"))
    assert value.state.value["last_error_class"] is None
    assert value.state.value["last_safe_error_summary"] is None
    assert value.state.value["last_failure"] == NOW.isoformat().replace(
        "+00:00", "Z")


def test_overlap_reason_identifies_active_phase(tmp_path):
    events = []

    class Slow(Collector):
        async def discover(self):
            self.events.append("discover.begin")
            await asyncio.sleep(0.02)

    async def exercise():
        collector = Slow(events)
        value = scheduler(tmp_path, collector)
        active = asyncio.create_task(
            value._execute_detailed(collector, "discover"))
        await asyncio.sleep(0)
        skipped = await value._execute_detailed(collector, "collect")
        await active
        return skipped

    skipped = asyncio.run(exercise())
    assert skipped["status"] == "skipped"
    assert skipped["reason"] == "active_discovery"


def test_first_recurring_deadline_is_completion_based(
        tmp_path, monkeypatch):
    events = []
    collector = Collector(events)
    value = scheduler(tmp_path, collector)
    observed = []

    async def wait_for(awaitable, timeout):
        awaitable.close()
        observed.append(timeout)
        return True

    monkeypatch.setattr(
        "collectors.scheduler.asyncio.wait_for", wait_for)

    async def exercise():
        stop = asyncio.Event()
        async def bounded(awaitable, timeout):
            result = await wait_for(awaitable, timeout)
            stop.set()
            return result
        monkeypatch.setattr(
            "collectors.scheduler.asyncio.wait_for", bounded)
        await value._continuous_loop(
            collector, "collect", collector.collection_interval, stop)

    asyncio.run(exercise())
    assert observed == [collector.collection_interval]
    assert value.state.value["next_collection_run"] == \
        "2026-07-28T01:02:33Z"


def test_continuous_shutdown_reaches_stopped_without_orphans(tmp_path):
    events = []
    value = scheduler(tmp_path, Collector(events))

    async def exercise():
        stop = asyncio.Event()
        async def ready(_):
            stop.set()
        await value.run_continuous(
            stop_event=stop, on_ready=ready)

    asyncio.run(exercise())
    assert value.state.value["lifecycle_state"] == "stopped"
    assert value.state.value["active_phase"] is None
    assert value.state.value["current_run_id"] is None
    assert value.state.value["next_discovery_run"] is None
    assert value.state.value["next_collection_run"] is None


def test_cancellation_during_startup_transitions_to_stopped(tmp_path):
    class Blocking(Collector):
        entered = None

        async def discover(self):
            self.entered.set()
            await asyncio.Event().wait()

    collector = Blocking([])
    value = scheduler(tmp_path, collector)

    async def exercise():
        collector.entered = asyncio.Event()
        stop = asyncio.Event()
        task = asyncio.create_task(
            value.run_continuous(stop_event=stop))
        await collector.entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not [
            item for item in asyncio.all_tasks()
            if item is not asyncio.current_task() and not item.done()]

    asyncio.run(exercise())
    assert value.state.value["lifecycle_state"] == "stopped"
    assert value.state.value["active_phase"] is None


def test_state_file_remains_valid_after_every_observed_write(
        tmp_path, monkeypatch):
    path = tmp_path / "scheduler/state.json"
    observed = []
    from collectors import scheduler as module
    real_write = module.atomic_write

    def validating_write(target, content):
        json.loads(content)
        real_write(target, content)
        observed.append(json.loads(path.read_text()))

    monkeypatch.setattr(module, "atomic_write", validating_write)
    value = Scheduler(
        [Collector([])], state_path=path, now_fn=lambda: NOW)
    asyncio.run(value.startup())
    assert observed
    assert all(item["schema_version"] == 1 for item in observed)


def test_analysis_cycle_refreshes_state_derived_dashboards_in_order(
        tmp_path, caplog):
    events = []

    class Engine:
        def __init__(self, name, result=None):
            self.name = name
            self.result = result

        def run(self):
            events.append(self.name)
            return self.result

        def generate(self):
            events.append(self.name)

    class Registry:
        output_root = tmp_path / "dashboard/managed"

        def refresh_state_derived(self):
            events.append("dashboards")

    value = Scheduler(
        [], capability_engine=Engine("capabilities"),
        infrastructure_engine=Engine("infrastructure", {"assets": []}),
        operations_engine=Engine("operations", {
            "issues": [], "risks": [], "recommendations": []}),
        service_health_engine=Engine("services"),
        wallboard_engine=Engine("legacy-wallboard"),
        dashboard_registry=Registry())

    with caplog.at_level("INFO", logger="collector.operations"):
        asyncio.run(value._execute_operations())

    assert events == [
        "capabilities", "infrastructure", "operations", "services",
        "dashboards"]
    assert "dashboard.render.begin" in caplog.text
    assert "dashboard.render.complete" in caplog.text
    assert str(
        tmp_path / "dashboard/managed/operations/"
        "itp-operations-wallboard.json") in caplog.text


def test_recovery_analysis_cycle_regenerates_wallboard_again(tmp_path):
    generations = []

    class Engine:
        def run(self):
            return {"issues": [], "risks": [], "recommendations": []}

    class Registry:
        output_root = tmp_path / "dashboard/managed"

        def refresh_state_derived(self):
            generations.append("refreshed")

    value = Scheduler(
        [], operations_engine=Engine(), dashboard_registry=Registry())
    asyncio.run(value._execute_operations())
    asyncio.run(value._execute_operations())
    assert generations == ["refreshed", "refreshed"]


def test_production_dashboard_render_failure_is_structured_and_safe(
        tmp_path, caplog):
    class Engine:
        def run(self):
            return {"issues": [], "risks": [], "recommendations": []}

    class Registry:
        output_root = tmp_path / "dashboard/managed"

        def refresh_state_derived(self):
            raise ValueError("token=must-not-appear")

    value = Scheduler(
        [], operations_engine=Engine(), dashboard_registry=Registry())
    with caplog.at_level("INFO", logger="collector.operations"):
        assert asyncio.run(value._execute_operations()) is None
    assert "dashboard.render.begin" in caplog.text
    assert "dashboard.render.failed" in caplog.text
    assert "error_class=ValueError" in caplog.text
    assert "must-not-appear" not in caplog.text
