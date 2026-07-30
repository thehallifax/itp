import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from analysis.operator import (
    DaemonAlreadyRunningError,
    DaemonLock,
    DaemonStateStore,
    OperatorDaemon,
    OperatorStatusEngine,
    start_background,
)
from collectors.scheduler import Scheduler


NOW = datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc)


class Registry:
    def all(self):
        return (SimpleNamespace(
            id="alpha", display_name="Alpha", domains=("switching",)),)


class Collector:
    name = "alpha"
    discovery_interval = 17
    collection_interval = 3

    def discover(self):
        return {}

    def collect(self):
        return {"points_written": 2}


def config(enabled=True):
    return {
        "deployment": {"name": "Test", "type": "Home Lab"},
        "site": "site-a",
        "collectors": {
            "alpha": {
                "enabled": enabled, "collection_interval_seconds": 3}},
    }


def factory(*_):
    return Collector()


class OnceScheduler:
    def __init__(self, collectors):
        self.collectors = collectors

    async def execute_once(self, phase):
        assert phase == "collect"
        return ({
            "connector": "alpha", "status": "success", "duration_ms": 4,
            "value": {"points_written": 2}, "exception_type": "", "reason": "",
        },)


class ContinuousScheduler(OnceScheduler):
    async def run_continuous(self, *, stop_event, on_start, on_outcome,
                             include_discovery):
        assert include_discovery is True
        await on_start("alpha", "collect")
        await on_outcome({
            "connector": "alpha", "status": "success", "duration_ms": 4,
            "value": {"points_written": 2}, "exception_type": "", "reason": "",
        }, "collect")
        stop_event.set()


def test_pid_lock_rejects_live_process_and_recovers_stale_lock(tmp_path):
    lock_path = tmp_path / "daemon.pid"
    first = DaemonLock(lock_path).acquire()
    try:
        with pytest.raises(DaemonAlreadyRunningError):
            DaemonLock(lock_path).acquire()
    finally:
        first.release()
    lock_path.write_text("99999999\n")
    recovered = DaemonLock(lock_path).acquire()
    assert lock_path.read_text().strip() == str(os.getpid())
    recovered.release()
    assert not lock_path.exists()


def test_daemon_once_records_pipeline_and_stops_cleanly(tmp_path):
    daemon = OperatorDaemon(
        tmp_path, config(), registry=Registry(), collector_factory=factory,
        scheduler_factory=OnceScheduler, runtime_dir=tmp_path / "runtime",
        now_fn=lambda: NOW)
    result = daemon.run(once=True)

    assert result["summary"]["overall"] == "success"
    assert result["pipeline_run"]["source_coverage"] == ["alpha"]
    state = DaemonStateStore(tmp_path / "runtime").read()
    assert state["status"] == "Stopped"
    assert state["pid"] is None
    assert state["last_successful_collection"] == "2026-07-24T08:00:00Z"
    assert not (tmp_path / "runtime/daemon/daemon.pid").exists()


def test_daemon_construction_failure_exits_before_scheduler(tmp_path):
    def invalid_factory(*_):
        raise ValueError("api_token=must-not-leak")

    daemon = OperatorDaemon(
        tmp_path, config(), registry=Registry(),
        collector_factory=invalid_factory,
        scheduler_factory=OnceScheduler,
        runtime_dir=tmp_path / "runtime", now_fn=lambda: NOW)
    with pytest.raises(RuntimeError, match="initialization is invalid") as error:
        daemon.run()
    assert "must-not-leak" not in str(error.value)
    state = DaemonStateStore(tmp_path / "runtime").read()
    assert state["status"] == "Stopped"
    assert not (tmp_path / "runtime/daemon/daemon.pid").exists()


def test_foreground_daemon_updates_heartbeat_and_handles_shutdown(tmp_path):
    daemon = OperatorDaemon(
        tmp_path, config(), registry=Registry(), collector_factory=factory,
        scheduler_factory=ContinuousScheduler,
        runtime_dir=tmp_path / "runtime", now_fn=lambda: NOW)
    assert daemon.run() is None
    state = DaemonStateStore(tmp_path / "runtime").read()
    assert state["status"] == "Stopped"
    assert state["last_heartbeat"] == "2026-07-24T08:00:00Z"
    assert state["last_successful_collection"] == "2026-07-24T08:00:00Z"
    assert state["current_collection"] == []


def test_daemon_state_reports_running_starting_stopped_and_uptime(tmp_path):
    store = DaemonStateStore(tmp_path)
    store.write(
        status="Starting", pid=None, started_at="2026-07-24T07:59:55Z",
        last_heartbeat="2026-07-24T07:59:59Z")
    assert store.snapshot(NOW)["status"] == "Starting"
    store.write(status="Running", pid=os.getpid())
    running = store.snapshot(NOW)
    assert running["status"] == "Running"
    assert running["uptime_seconds"] == 5
    store.write(status="Running", pid=99999999)
    assert store.snapshot(NOW)["status"] == "Stopped"


def test_status_includes_daemon_health(tmp_path):
    runtime = tmp_path / "runtime"
    DaemonStateStore(runtime).write(
        status="Running", pid=os.getpid(),
        started_at="2026-07-24T07:59:00Z",
        last_heartbeat="2026-07-24T07:59:59Z",
        last_successful_collection="2026-07-24T07:59:58Z",
        current_collection=["alpha"])
    result = OperatorStatusEngine(
        tmp_path, config(), registry=Registry(), runtime_dir=runtime,
        now_fn=lambda: NOW).run()
    assert result["daemon"] == {
        "schema_version": 1, "status": "Running", "pid": os.getpid(),
        "started_at": "2026-07-24T07:59:00Z",
        "last_heartbeat": "2026-07-24T07:59:59Z",
        "last_successful_collection": "2026-07-24T07:59:58Z",
        "current_collection": ["alpha"], "uptime_seconds": 60,
    }


def test_scheduler_uses_connector_intervals_and_survives_failures():
    class Good(Collector):
        name = "good"
        collection_interval = 0.01
        discovery_interval = 0.02

    class Bad(Good):
        name = "bad"

        def collect(self):
            raise RuntimeError("api_token=must-not-leak")

    async def exercise():
        stop = asyncio.Event()
        outcomes = []

        async def receive(outcome, phase):
            outcomes.append((phase, outcome))
            if (any(item[1]["connector"] == "good"
                    and item[0] == "collect" for item in outcomes)
                    and any(item[1]["connector"] == "bad"
                            and item[0] == "collect" for item in outcomes)):
                stop.set()

        await Scheduler([Good(), Bad()]).run_continuous(
            stop_event=stop, on_outcome=receive, include_discovery=True)
        return outcomes

    outcomes = asyncio.run(exercise())
    collect = {item["connector"]: item for phase, item in outcomes
               if phase == "collect"}
    assert collect["good"]["status"] == "success"
    assert collect["bad"]["status"] == "failed"
    assert "must-not-leak" not in json.dumps(outcomes)


def test_background_start_uses_foreground_child_and_persists_starting(
        tmp_path, monkeypatch):
    script = tmp_path / "scripts/itp.py"
    script.parent.mkdir()
    script.write_text("# test")
    calls = []

    class Process:
        pid = 4321

    def popen(arguments, **options):
        calls.append((arguments, options))
        return Process()

    monkeypatch.setattr(
        "analysis.operator.daemon.subprocess.Popen", popen)
    messages = []
    assert start_background(
        script, tmp_path / "runtime", messages.append) == 4321
    arguments, options = calls[0]
    assert arguments[-2:] == ["daemon", "--foreground"]
    assert options["stdin"] is not None
    state = DaemonStateStore(tmp_path / "runtime").read()
    assert state["status"] == "Starting"
    assert state["pid"] == 4321
    assert "4321" in messages[0]


def test_background_start_preserves_explicit_deployment(tmp_path, monkeypatch):
    script = tmp_path / "scripts/itp.py"
    script.parent.mkdir()
    script.write_text("# test")
    calls = []

    class Process:
        pid = 4321

    monkeypatch.setattr(
        "analysis.operator.daemon.subprocess.Popen",
        lambda arguments, **_options: calls.append(arguments) or Process())
    start_background(
        script, tmp_path / "runtime",
        arguments=("--deployment", "example"))
    assert calls[0][-4:] == [
        "daemon", "--foreground", "--deployment", "example"]


def test_daemon_coalesces_notification_evaluation(tmp_path, monkeypatch):
    evaluations = []

    class Notifications:
        enabled = True

        def __init__(self, *_args, **_kwargs):
            pass

        def evaluate(self, value):
            evaluations.append(value)

    monkeypatch.setattr(
        "analysis.operator.daemon.NotificationEngine", Notifications)
    value = config()
    value["notifications"] = {"enabled": True}
    daemon = OperatorDaemon(
        tmp_path, value, registry=Registry(), collector_factory=factory,
        scheduler_factory=ContinuousScheduler,
        runtime_dir=tmp_path / "runtime", now_fn=lambda: NOW)
    daemon.run()
    assert len(evaluations) == 1
    assert evaluations[0]["latest_pipeline_run"]["status"] == "success"
