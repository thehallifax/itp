"""Continuous registry-driven collection runtime."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import inspect
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from collectors.scheduler import Scheduler
from collectors.writer import atomic_write
from .engine import OperatorCollectEngine, _parse, _utc
from .engine import OperatorStatusEngine
from analysis.notifications import NotificationEngine


def _pid_running(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        return True
    except (OSError, TypeError, ValueError):
        return False


class DaemonAlreadyRunningError(RuntimeError):
    pass


class DaemonLock:
    """Portable exclusive PID file with stale-lock recovery."""

    def __init__(self, path):
        self.path = Path(path)
        self.pid = os.getpid()
        self.acquired = False

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                descriptor = os.open(
                    str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                try:
                    existing = int(self.path.read_text().strip())
                except (OSError, ValueError):
                    existing = 0
                if existing and _pid_running(existing):
                    raise DaemonAlreadyRunningError(
                        f"ITP daemon is already running (PID {existing})")
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                continue
            with os.fdopen(descriptor, "w") as handle:
                handle.write(f"{self.pid}\n")
            self.acquired = True
            return self

    def release(self):
        if not self.acquired:
            return
        try:
            if self.path.read_text().strip() == str(self.pid):
                self.path.unlink()
        except FileNotFoundError:
            pass
        self.acquired = False

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_):
        self.release()


class DaemonStateStore:
    def __init__(self, runtime_dir):
        self.root = Path(runtime_dir) / "daemon"
        self.state_path = self.root / "state.json"
        self.lock_path = self.root / "daemon.pid"

    def read(self):
        try:
            value = json.loads(self.state_path.read_text())
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def write(self, **changes):
        value = {
            "schema_version": 1,
            "status": "Stopped",
            "pid": None,
            "started_at": None,
            "last_heartbeat": None,
            "last_successful_collection": None,
            "current_collection": [],
            **self.read(),
            **changes,
        }
        value["current_collection"] = sorted(set(
            value.get("current_collection") or []))
        atomic_write(
            self.state_path, json.dumps(value, indent=2, sort_keys=True) + "\n")
        return value

    def snapshot(self, now=None):
        value = {
            "schema_version": 1, "status": "Stopped", "pid": None,
            "started_at": None, "last_heartbeat": None,
            "last_successful_collection": None,
            "current_collection": [], **self.read(),
        }
        now = now or datetime.now(timezone.utc)
        pid = value.get("pid")
        heartbeat = _parse(value.get("last_heartbeat"))
        startup_pending = (
            value.get("status") == "Starting" and not pid and heartbeat
            and (now.astimezone(timezone.utc)
                 - heartbeat.astimezone(timezone.utc)).total_seconds() <= 30)
        if (value.get("status") in {"Running", "Starting"}
                and not startup_pending and not _pid_running(pid)):
            value = self.write(
                status="Stopped", pid=None, current_collection=[])
        started = _parse(value.get("started_at"))
        value["uptime_seconds"] = max(
            0, int((now.astimezone(timezone.utc) - started).total_seconds())
        ) if started and value.get("status") in {"Running", "Starting"} else 0
        return value


class OperatorDaemon:
    def __init__(self, root, config, *, registry=None, collector_factory=None,
                 scheduler_factory=Scheduler, runtime_dir=None, now_fn=None):
        self.root = Path(root)
        self.config = config
        self.runtime_dir = Path(runtime_dir or self.root / "runtime")
        self.now = now_fn or (lambda: datetime.now(timezone.utc))
        self.collect_engine = OperatorCollectEngine(
            root, config, registry=registry, collector_factory=collector_factory,
            scheduler_factory=scheduler_factory, runtime_dir=self.runtime_dir,
            now_fn=self.now)
        self.scheduler_factory = scheduler_factory
        self.state = DaemonStateStore(self.runtime_dir)
        self.stop_event = None
        self._current = set()
        self._configured = ()
        self._notification_task = None
        self.notifications = NotificationEngine(
            self.runtime_dir, config.get("notifications"))

    def request_stop(self):
        if self.stop_event:
            self.stop_event.set()

    def _install_signals(self, loop):
        for name in ("SIGINT", "SIGTERM"):
            selected = getattr(signal, name, None)
            if selected is None:
                continue
            try:
                loop.add_signal_handler(selected, self.request_stop)
            except (NotImplementedError, RuntimeError):
                try:
                    signal.signal(selected, lambda *_: self.request_stop())
                except (OSError, RuntimeError, ValueError):
                    logging.getLogger("operator.daemon").debug(
                        "signal_handler=unavailable signal=%s", name)

    async def _heartbeat(self):
        while not self.stop_event.is_set():
            self.state.write(
                status="Running", pid=os.getpid(),
                last_heartbeat=_utc(self.now()),
                current_collection=self._current)
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass

    async def _on_start(self, connector, phase):
        if phase != "collect":
            return
        self._current.add(connector)
        self.state.write(
            status="Running", pid=os.getpid(),
            last_heartbeat=_utc(self.now()),
            current_collection=self._current)

    async def _on_outcome(self, outcome, phase):
        if phase != "collect":
            return
        connector = outcome["connector"]
        metadata = next(
            value for value, collector in self._configured
            if collector.name == connector)
        now = _utc(self.now())
        self.collect_engine.record(
            self._configured, (outcome,), started=now, completed=now,
            scope_metadata=(metadata,))
        self._current.discard(connector)
        changes = {
            "status": "Running", "pid": os.getpid(),
            "last_heartbeat": now, "current_collection": self._current}
        if outcome["status"] == "success":
            changes["last_successful_collection"] = now
        self.state.write(**changes)
        self._schedule_notification_evaluation()

    def _schedule_notification_evaluation(self):
        if not self.notifications.enabled:
            return
        if self._notification_task and not self._notification_task.done():
            return
        self._notification_task = asyncio.create_task(
            self._evaluate_notifications())

    async def _evaluate_notifications(self):
        # Coalesce connector completions from the same scheduler tick.
        await asyncio.sleep(0.05)
        try:
            status = OperatorStatusEngine(
                self.root, self.config, registry=self.collect_engine.registry,
                runtime_dir=self.runtime_dir, now_fn=self.now).run()
            await asyncio.to_thread(self.notifications.evaluate, status)
        except Exception as exc:
            logging.getLogger("operator.daemon").error(
                "notification_evaluation=failed exception_type=%s",
                type(exc).__name__)

    async def _run(self, once):
        started = _utc(self.now())
        self.stop_event = asyncio.Event()
        self._install_signals(asyncio.get_running_loop())
        self.state.write(
            status="Starting", pid=os.getpid(), started_at=started,
            last_heartbeat=started, current_collection=[])
        self._configured, initialization = self.collect_engine._configured()
        if any(value["status"] == "failed" for value in initialization):
            raise RuntimeError(
                "daemon startup failed: connector initialization is invalid")
        scheduler_options = {}
        if "state_path" in inspect.signature(
                self.scheduler_factory).parameters:
            scheduler_options["state_path"] = (
                self.runtime_dir / "scheduler/state.json")
        if "now_fn" in inspect.signature(
                self.scheduler_factory).parameters:
            scheduler_options["now_fn"] = self.now
        scheduler = self.scheduler_factory(
            [value[1] for value in self._configured], **scheduler_options)
        self.state.write(status="Running", pid=os.getpid())
        if once:
            outcomes = await scheduler.execute_once("collect") \
                if self._configured else ()
            result = self.collect_engine.record(
                self._configured, outcomes, initialization,
                started=started, completed=_utc(self.now()))
            successful = [
                value for value in result["connectors"]
                if value["status"] == "success"]
            self.state.write(
                last_heartbeat=_utc(self.now()),
                last_successful_collection=(
                    result["pipeline_run"]["completed_at"]
                    if successful else self.state.read().get(
                        "last_successful_collection")))
            try:
                status = OperatorStatusEngine(
                    self.root, self.config,
                    registry=self.collect_engine.registry,
                    runtime_dir=self.runtime_dir, now_fn=self.now).run()
                self.notifications.evaluate(status)
            except Exception as exc:
                logging.getLogger("operator.daemon").error(
                    "notification_evaluation=failed exception_type=%s",
                    type(exc).__name__)
            return result
        heartbeat = asyncio.create_task(self._heartbeat())
        try:
            await scheduler.run_continuous(
                stop_event=self.stop_event, on_start=self._on_start,
                on_outcome=self._on_outcome, include_discovery=True)
        finally:
            self.stop_event.set()
            await heartbeat
            if self._notification_task:
                await self._notification_task
        return None

    def run(self, *, once=False):
        with DaemonLock(self.state.lock_path):
            try:
                return asyncio.run(self._run(once))
            finally:
                self._current.clear()
                self.state.write(
                    status="Stopped", pid=None,
                    last_heartbeat=_utc(self.now()), current_collection=[])


def start_background(script, runtime_dir, output=None):
    """Start the foreground daemon as a detached child process."""
    state = DaemonStateStore(runtime_dir)
    snapshot = state.snapshot()
    if snapshot["status"] in {"Running", "Starting"}:
        raise DaemonAlreadyRunningError(
            f"ITP daemon is already {snapshot['status'].lower()}")
    state.write(
        status="Starting", pid=None, started_at=_utc(),
        last_heartbeat=_utc(), current_collection=[])
    log_path = state.root / "daemon.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("a")
    arguments = [sys.executable, str(script), "daemon", "--foreground"]
    options = {
        "cwd": str(Path(script).resolve().parents[1]),
        "stdin": subprocess.DEVNULL, "stdout": log, "stderr": log,
        "close_fds": True,
    }
    if os.name == "nt":
        options["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS)
    else:
        options["start_new_session"] = True
    process = subprocess.Popen(arguments, **options)
    log.close()
    state.write(pid=process.pid)
    if output:
        output(f"ITP daemon starting (PID {process.pid}); log: {log_path}")
    return process.pid
