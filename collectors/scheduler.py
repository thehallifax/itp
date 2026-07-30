"""Interval scheduler for independently paced collectors."""
import asyncio
import inspect
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from telemetry import CollectorHealth

from .base import CollectorSkipped
from .writer import atomic_write


async def _resolve(value):
    return await value if inspect.isawaitable(value) else value


def _utc(value=None):
    value = value or datetime.now(timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class SchedulerLifecycle(str, Enum):
    STARTING = "starting"
    INITIAL_DISCOVERY = "initial_discovery"
    INITIAL_COLLECTION = "initial_collection"
    READY = "ready"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    STOPPED = "stopped"


class SchedulerStateStore:
    """Atomic machine-readable scheduler lifecycle state."""

    def __init__(self, path):
        self.path = Path(path) if path else None
        self.value = self.defaults()
        if self.path:
            try:
                existing = json.loads(self.path.read_text())
                if isinstance(existing, dict):
                    self.value.update(existing)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                pass

    @staticmethod
    def defaults():
        return {
            "schema_version": 1,
            "lifecycle_state": SchedulerLifecycle.STOPPED.value,
            "started_at": None,
            "ready_at": None,
            "initial_discovery": {"outcome": "not_run", "duration_ms": None},
            "initial_collection": {"outcome": "not_run", "duration_ms": None},
            "last_discovery_attempt": None,
            "last_successful_discovery": None,
            "last_discovery_outcome": "never_run",
            "last_discovery_duration_ms": None,
            "next_discovery_run": None,
            "last_collection_attempt": None,
            "last_successful_collection": None,
            "last_collection_outcome": "never_run",
            "last_collection_duration_ms": None,
            "next_collection_run": None,
            "next_runs": {"discovery": {}, "collection": {}},
            "last_skipped_run": None,
            "last_skip_reason": None,
            "last_failure": None,
            "consecutive_discovery_failures": 0,
            "consecutive_collection_failures": 0,
            "active_phase": None,
            "current_run_id": None,
            "last_error_class": None,
            "last_safe_error_summary": None,
            "connectors": {},
            "updated_at": None,
        }

    def write(self, **changes):
        self.value = {**self.value, **changes}
        if self.path:
            atomic_write(
                self.path,
                json.dumps(self.value, indent=2, sort_keys=True) + "\n")
        return dict(self.value)


class Scheduler:
    def __init__(self, collectors, health_path=None, *, inventory_engine=None,
                 lifecycle_interval=3600, infrastructure_engine=None,
                 operations_engine=None, service_health_engine=None,
                 wallboard_engine=None, dashboard_registry=None,
                 state_history_capture=None,
                 operations_interval=300, state_path=None, now_fn=None,
                 monotonic_fn=None, capability_engine=None,
                 health_writer=None, runtime_mode="central"):
        self.collectors = list(collectors)
        self.health_path = Path(health_path) if health_path else None
        # Locks are created lazily inside the active event loop. Python 3.9
        # otherwise attempts to bind them during construction and fails when
        # the scheduler is instantiated before asyncio.run().
        self._locks = {collector: None for collector in self.collectors}
        self.inventory_engine = inventory_engine
        self.lifecycle_interval = max(1, int(lifecycle_interval))
        self._lifecycle_lock = None
        self.operations_engine = operations_engine
        self.infrastructure_engine = infrastructure_engine
        self.service_health_engine = service_health_engine
        self.wallboard_engine = wallboard_engine
        self.dashboard_registry = dashboard_registry
        self.capability_engine = capability_engine
        self.state_history_capture = state_history_capture
        self.operations_interval = max(1, int(operations_interval))
        self._operations_lock = None
        self.now = now_fn or (lambda: datetime.now(timezone.utc))
        self.monotonic = monotonic_fn or time.monotonic
        self.state = SchedulerStateStore(state_path)
        self._active = {}
        self._shutdown = False
        self._initial_discovery_success = set()
        self.logger = logging.getLogger("collector.scheduler")
        self.health_writer = health_writer
        self.runtime_mode = runtime_mode

    def _log(self, event, **fields):
        suffix = " ".join(
            f"{key}={value}" for key, value in sorted(fields.items())
            if value not in (None, ""))
        self.logger.info("%s%s", event, f" {suffix}" if suffix else "")

    def _phase_state(self, phase, outcome, attempted_at):
        phase = "discovery" if phase == "discover" else "collection" \
            if phase == "collect" else phase
        now = _utc(self.now())
        status = outcome["status"]
        changes = {
            f"last_{phase}_attempt": attempted_at,
            f"last_{phase}_outcome": status,
            f"last_{phase}_duration_ms": outcome["duration_ms"],
            "updated_at": now,
        }
        connector = outcome["connector"]
        connector_states = {
            name: dict(value) for name, value in
            self.state.value["connectors"].items()}
        connector_state = {
            "consecutive_discovery_failures": 0,
            "consecutive_collection_failures": 0,
            **connector_states.get(connector, {}),
        }
        failures = f"consecutive_{phase}_failures"
        if status == "success":
            changes[f"last_successful_{phase}"] = now
            connector_state[failures] = 0
            connector_state[f"last_{phase}_error_class"] = None
            connector_state[f"last_{phase}_safe_error_summary"] = None
            connector_state[f"current_{phase}_skip_reason"] = None
        elif status == "failed":
            connector_state[failures] += 1
            connector_state[f"last_{phase}_error_class"] = (
                outcome["exception_type"] or None)
            connector_state[f"last_{phase}_safe_error_summary"] = (
                outcome["reason"] or None)
            connector_state[f"current_{phase}_skip_reason"] = None
            connector_state["last_failure"] = now
            changes["last_failure"] = now
        elif status == "skipped":
            changes["last_skipped_run"] = now
            connector_state[f"current_{phase}_skip_reason"] = outcome["reason"]
            connector_state[f"last_{phase}_error_class"] = None
            connector_state[f"last_{phase}_safe_error_summary"] = None
        connector_state.update({
            f"last_{phase}_attempt": attempted_at,
            f"last_{phase}_outcome": status,
            f"last_{phase}_duration_ms": outcome["duration_ms"],
        })
        # Persist only bounded, non-sensitive collection evidence needed by
        # capability/readiness projections. Never persist raw responses.
        result = outcome.get("value")
        if isinstance(result, dict):
            safe_keys = {
                "points_written", "records_returned", "devices_returned",
                "assets_returned", "partial", "status",
            }
            connector_state[f"last_{phase}_result"] = {
                key: result[key] for key in sorted(safe_keys & result.keys())
                if isinstance(result[key], (str, int, float, bool, type(None)))
            }
            capability_states = result.get("capability_states")
            if isinstance(capability_states, dict):
                allowed_states = {
                    "collected", "not_yet_collected", "disabled",
                    "unavailable", "failed", "partial", "not_applicable"}
                connector_state[f"last_{phase}_result"][
                    "capability_states"] = {
                        str(key): str(value)
                        for key, value in sorted(capability_states.items())
                        if str(value) in allowed_states}
            capability_resources = result.get("capability_resources")
            if isinstance(capability_resources, dict):
                connector_state[f"last_{phase}_result"][
                    "capability_resources"] = {
                        str(key): max(0, int(value))
                        for key, value in sorted(capability_resources.items())
                        if isinstance(value, int) and not isinstance(value, bool)
                    }
            endpoint_states = result.get("endpoint_states")
            if isinstance(endpoint_states, dict):
                allowed_endpoint_states = {
                    "collected", "endpoint_disabled", "invalid_response",
                    "insufficient_permissions", "api_unavailable",
                    "unsupported_endpoint",
                }
                connector_state[f"last_{phase}_result"]["endpoint_states"] = {
                    str(key): {
                        "state": str(value.get("state")),
                        "resource_count": max(
                            0, int(value.get("resource_count") or 0)),
                    }
                    for key, value in sorted(endpoint_states.items())
                    if isinstance(value, dict)
                    and str(value.get("state")) in allowed_endpoint_states
                }
        if status == "success":
            connector_state[f"last_successful_{phase}"] = now
        phase_names = ("discovery", "collection")
        active_errors = [
            (name, connector_state.get(f"last_{name}_error_class"),
             connector_state.get(f"last_{name}_safe_error_summary"))
            for name in phase_names
            if connector_state.get(f"last_{name}_outcome") == "failed"]
        connector_state["last_error_class"] = (
            active_errors[0][1] if active_errors else None)
        connector_state["last_safe_error_summary"] = (
            active_errors[0][2] if active_errors else None)
        connector_states[connector] = connector_state
        changes["connectors"] = connector_states
        changes[failures] = sum(
            value.get(failures, 0) for value in connector_states.values())
        root_errors = sorted(
            (name, phase_name,
             value.get(f"last_{phase_name}_error_class"),
             value.get(f"last_{phase_name}_safe_error_summary"))
            for name, value in connector_states.items()
            for phase_name in phase_names
            if value.get(f"last_{phase_name}_outcome") == "failed")
        root_skips = sorted(
            (name, phase_name,
             value.get(f"current_{phase_name}_skip_reason"))
            for name, value in connector_states.items()
            for phase_name in phase_names
            if value.get(f"last_{phase_name}_outcome") == "skipped")
        changes["last_error_class"] = (
            root_errors[0][2] if root_errors else None)
        changes["last_safe_error_summary"] = (
            root_errors[0][3] if root_errors else None)
        changes["last_skip_reason"] = (
            root_skips[0][2] if root_skips else None)
        self.state.write(**changes)
        if status == "success" and self.state.value[
                "lifecycle_state"] == SchedulerLifecycle.DEGRADED.value \
                and self.state.value["last_successful_discovery"] \
                and self.state.value["last_successful_collection"] \
                and self.state.value["consecutive_discovery_failures"] == 0 \
                and self.state.value["consecutive_collection_failures"] == 0:
            self.state.write(
                lifecycle_state=SchedulerLifecycle.READY.value,
                ready_at=now, updated_at=now)
            self._log("scheduler.ready", actual_start_time=now,
                      outcome="recovered")

    async def run_once(self):
        return [await _resolve(collector.discover()) for collector in self.collectors]

    async def _execute(self, collector, phase):
        outcome = await self._execute_detailed(
            collector, phase, log_exception=True)
        return outcome["value"] if outcome["status"] == "success" else None

    async def _execute_detailed(self, collector, phase, *, log_exception=False):
        """Execute one phase with structured timing for operator commands."""
        started = self.monotonic()
        attempted_at = _utc(self.now())
        run_id = uuid.uuid4().hex
        lock = self._locks[collector]
        if lock is None:
            lock = self._locks[collector] = asyncio.Lock()
        if self._shutdown:
            reason = "shutdown_in_progress"
        elif lock.locked():
            active = self._active.get(collector, "collect")
            reason = "active_discovery" if active == "discover" \
                else "active_collection"
        else:
            reason = ""
        if reason:
            self.logger.warning(
                "scheduler.run.skipped collector=%s phase=%s outcome=skipped "
                "skip_reason=%s", collector.name, phase, reason)
            outcome = {"connector": collector.name, "status": "skipped",
                       "duration_ms": 0, "value": None,
                       "exception_type": "", "reason": reason,
                       "run_id": run_id}
            self._phase_state(phase, outcome, attempted_at)
            await self._record_health(collector, phase, outcome)
            return outcome
        async with lock:
            self._active[collector] = phase
            self.state.write(
                active_phase=phase, current_run_id=run_id,
                updated_at=attempted_at)
            self._log(
                f"scheduler.{phase}.begin", collector=collector.name,
                run_id=run_id, actual_start_time=attempted_at)
            try:
                result = await _resolve(getattr(collector, phase)())
                if self.health_path:
                    self.health_path.touch()
                outcome = {
                    "connector": collector.name, "status": "success",
                    "duration_ms": int((self.monotonic() - started) * 1000),
                    "value": result, "exception_type": "", "reason": "",
                    "run_id": run_id}
            except CollectorSkipped as exc:
                outcome = {
                    "connector": collector.name, "status": "skipped",
                    "duration_ms": int((self.monotonic() - started) * 1000),
                    "value": None, "exception_type": "",
                    "reason": exc.reason, "run_id": run_id}
            except Exception as exc:
                if log_exception:
                    self.logger.exception(
                        "collector=%s phase=%s result=failed",
                        collector.name, phase)
                else:
                    self.logger.error(
                        "collector=%s phase=%s result=failed exception_type=%s",
                        collector.name, phase, type(exc).__name__)
                outcome = {
                    "connector": collector.name, "status": "failed",
                    "duration_ms": int((self.monotonic() - started) * 1000),
                    "value": None, "exception_type": type(exc).__name__,
                    "reason": "collector execution failed", "run_id": run_id}
            finally:
                self._active.pop(collector, None)
                self.state.write(
                    active_phase=None, current_run_id=None,
                    updated_at=_utc(self.now()))
            self._phase_state(phase, outcome, attempted_at)
            await self._record_health(collector, phase, outcome)
            if phase == "discover" and outcome["status"] == "success":
                self._initial_discovery_success.add(collector.name)
            self._log(
                f"scheduler.{phase}.complete", collector=collector.name,
                run_id=run_id, duration_ms=outcome["duration_ms"],
                outcome=outcome["status"],
                error_class=outcome["exception_type"])
            return outcome

    async def _record_health(self, collector, phase, outcome):
        if self.health_writer is None:
            return
        health = CollectorHealth.from_outcome(
            outcome, runtime=self.runtime_mode,
            execution_mode=getattr(collector, "execution", "either"),
            phase=phase)
        try:
            await asyncio.to_thread(self.health_writer.write, [health.point()])
        except Exception as exc:
            self.logger.error(
                "collector=%s phase=health result=write_failed "
                "exception_type=%s",
                collector.name, type(exc).__name__)

    async def execute_once(self, phase="collect"):
        """Execute a phase once for every configured scheduler collector."""
        outcomes = await asyncio.gather(*(
            self._execute_detailed(collector, phase)
            for collector in self.collectors))
        return tuple(sorted(outcomes, key=lambda value: value["connector"]))

    async def _loop(self, collector, phase, interval):
        while True:
            started = time.monotonic()
            await self._execute(collector, phase)
            await asyncio.sleep(max(0.1, interval - (time.monotonic() - started)))

    async def _continuous_loop(self, collector, phase, interval, stop_event,
                               on_start=None, on_outcome=None):
        """Run one collector phase at its configured interval until stopped."""
        deadline = self.now().timestamp() + interval
        while not stop_event.is_set():
            state_phase = "discovery" if phase == "discover" else "collection"
            next_run = _utc(datetime.fromtimestamp(deadline, timezone.utc))
            next_runs = {
                name: dict(values) for name, values in
                self.state.value["next_runs"].items()}
            next_runs[state_phase][collector.name] = next_run
            earliest = min(next_runs[state_phase].values())
            self.state.write(
                **{f"next_{state_phase}_run": earliest},
                next_runs=next_runs,
                updated_at=_utc(self.now()))
            delay = max(0, deadline - self.now().timestamp())
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
                break
            except asyncio.TimeoutError:
                pass
            if self._shutdown or stop_event.is_set():
                break
            if on_start:
                await _resolve(on_start(collector.name, phase))
            if phase == "collect" and collector.name not in \
                    self._initial_discovery_success:
                outcome = {
                    "connector": collector.name, "status": "skipped",
                    "duration_ms": 0, "value": None,
                    "exception_type": "",
                    "reason": "prerequisite_unavailable",
                    "run_id": uuid.uuid4().hex,
                }
                self._phase_state(phase, outcome, _utc(self.now()))
                self.logger.warning(
                    "scheduler.run.skipped collector=%s phase=%s "
                    "outcome=skipped skip_reason=prerequisite_unavailable",
                    collector.name, phase)
                if on_outcome:
                    await _resolve(on_outcome(outcome, phase))
                deadline = self.now().timestamp() + interval
                continue
            self._log(
                f"scheduler.{state_phase}.tick", collector=collector.name,
                scheduled_time=next_run, actual_start_time=_utc(self.now()))
            outcome = await self._execute_detailed(collector, phase)
            if on_outcome:
                await _resolve(on_outcome(outcome, phase))
            deadline = self.now().timestamp() + interval

    async def startup(self, *, on_start=None, on_outcome=None):
        """Run discovery then collection sequentially before recurring work."""
        async def execute_initial(collector, phase):
            if on_start:
                await _resolve(on_start(collector.name, phase))
            outcome = await self._execute_detailed(collector, phase)
            if on_outcome:
                await _resolve(on_outcome(outcome, phase))
            return outcome

        started = _utc(self.now())
        self._shutdown = False
        self.state.write(
            lifecycle_state=SchedulerLifecycle.STARTING.value,
            started_at=started, ready_at=None, updated_at=started)
        self._log("scheduler.starting", actual_start_time=started)

        discovery_started = self.monotonic()
        self.state.write(
            lifecycle_state=SchedulerLifecycle.INITIAL_DISCOVERY.value,
            active_phase="initial_discovery", updated_at=_utc(self.now()))
        self._log(
            "scheduler.initial_discovery.begin",
            actual_start_time=_utc(self.now()))
        discovery = tuple(sorted(await asyncio.gather(*(
            execute_initial(collector, "discover")
            for collector in self.collectors)),
            key=lambda item: item["connector"]))
        self._initial_discovery_success = {
            item["connector"] for item in discovery
            if item["status"] == "success"}
        discovery_outcome = (
            "success" if all(item["status"] == "success" for item in discovery)
            else "failed")
        discovery_duration = int(
            (self.monotonic() - discovery_started) * 1000)
        self.state.write(
            initial_discovery={
                "outcome": discovery_outcome,
                "duration_ms": discovery_duration},
            active_phase=None, updated_at=_utc(self.now()))
        self._log(
            "scheduler.initial_discovery.complete"
            if discovery_outcome == "success"
            else "scheduler.initial_discovery.failed",
            duration_ms=discovery_duration, outcome=discovery_outcome)

        eligible = [
            collector for collector in self.collectors
            if collector.name in self._initial_discovery_success]
        collection_started = self.monotonic()
        self.state.write(
            lifecycle_state=SchedulerLifecycle.INITIAL_COLLECTION.value,
            active_phase="initial_collection", updated_at=_utc(self.now()))
        self._log(
            "scheduler.initial_collection.begin",
            actual_start_time=_utc(self.now()))
        collection = tuple(sorted(await asyncio.gather(*(
            execute_initial(collector, "collect")
            for collector in eligible)), key=lambda item: item["connector"]))
        unavailable = sorted(
            {collector.name for collector in self.collectors}
            - self._initial_discovery_success)
        collection_outcome = (
            "skipped_prerequisite" if unavailable and not eligible
            else "failed" if unavailable or any(
                item["status"] != "success" for item in collection)
            else "success")
        collection_duration = int(
            (self.monotonic() - collection_started) * 1000)
        self.state.write(
            initial_collection={
                "outcome": collection_outcome,
                "duration_ms": collection_duration},
            active_phase=None, updated_at=_utc(self.now()))
        self._log(
            "scheduler.initial_collection.complete"
            if collection_outcome == "success"
            else "scheduler.initial_collection.failed",
            duration_ms=collection_duration, outcome=collection_outcome,
            skip_reason="prerequisite_unavailable" if unavailable else None)

        degraded = discovery_outcome != "success" or \
            collection_outcome != "success"
        lifecycle = (
            SchedulerLifecycle.DEGRADED if degraded
            else SchedulerLifecycle.READY)
        ready_at = _utc(self.now())
        self.state.write(
            lifecycle_state=lifecycle.value, ready_at=ready_at,
            updated_at=ready_at,
            last_skip_reason=(
                "prerequisite_unavailable" if unavailable
                else self.state.value["last_skip_reason"]))
        self._log(
            "scheduler.degraded" if degraded else "scheduler.ready",
            actual_start_time=ready_at, outcome=lifecycle.value)
        return {"discovery": discovery, "collection": collection,
                "lifecycle_state": lifecycle.value}

    async def run_continuous(self, *, stop_event=None, on_start=None,
                             on_outcome=None, include_discovery=True,
                             on_ready=None, startup_completed=False):
        """Continuously execute configured collectors using their intervals."""
        stop_event = stop_event or asyncio.Event()
        tasks = []
        try:
            startup = (
                {"lifecycle_state": self.state.value["lifecycle_state"]}
                if startup_completed else await self.startup(
                    on_start=on_start, on_outcome=on_outcome))
            if on_ready:
                await _resolve(on_ready(startup))
            for collector in self.collectors:
                if include_discovery:
                    tasks.append(asyncio.create_task(self._continuous_loop(
                        collector, "discover",
                        collector.discovery_interval,
                        stop_event, on_start, on_outcome)))
                tasks.append(asyncio.create_task(self._continuous_loop(
                    collector, "collect",
                    collector.collection_interval,
                    stop_event, on_start, on_outcome)))
            if not tasks:
                await stop_event.wait()
            else:
                await asyncio.gather(*tasks)
        finally:
            self._shutdown = True
            self.state.write(
                lifecycle_state=SchedulerLifecycle.STOPPING.value,
                active_phase=None, current_run_id=None,
                updated_at=_utc(self.now()))
            self._log("scheduler.stopping", outcome="stopping")
            stop_event.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.state.write(
                lifecycle_state=SchedulerLifecycle.STOPPED.value,
                next_discovery_run=None, next_collection_run=None,
                next_runs={"discovery": {}, "collection": {}},
                updated_at=_utc(self.now()))
            self._log("scheduler.stopped", outcome="stopped")

    async def _execute_lifecycle(self):
        logger = logging.getLogger("collector.inventory")
        if self._lifecycle_lock is None:
            self._lifecycle_lock = asyncio.Lock()
        if self._lifecycle_lock.locked():
            logger.warning("collector=inventory phase=lifecycle result=skipped_overlap")
            return None
        async with self._lifecycle_lock:
            started = time.monotonic()
            try:
                result = await asyncio.to_thread(self.inventory_engine.update_lifecycle)
                summary = result["lifecycle_summary"]
                if self.health_path: self.health_path.touch()
                logger.info("collector=inventory phase=lifecycle result=success "
                    "assets_evaluated=%d transitions=%d stale=%d missing=%d duration_ms=%d",
                    summary["assets_evaluated"], summary["transitions"], summary["stale"],
                    summary["missing"], (time.monotonic() - started) * 1000)
                return result
            except Exception:
                logger.exception("collector=inventory phase=lifecycle result=failed")
                return None

    async def _lifecycle_loop(self):
        while True:
            started = time.monotonic()
            await self._execute_lifecycle()
            await asyncio.sleep(max(1, self.lifecycle_interval - (time.monotonic() - started)))

    async def _operations_loop(self):
        logger = logging.getLogger("collector.operations")
        if self._operations_lock is None:
            self._operations_lock = asyncio.Lock()
        while True:
            started = time.monotonic()
            async with self._operations_lock:
                await self._execute_operations(started)
            await asyncio.sleep(max(1, self.operations_interval - (time.monotonic() - started)))

    async def _execute_operations(self, started=None):
        """Run one canonical pipeline; history cannot invalidate its outputs."""
        logger = logging.getLogger("collector.operations")
        started = time.monotonic() if started is None else started
        try:
            infrastructure = None
            if self.capability_engine:
                await asyncio.to_thread(self.capability_engine.generate)
            if self.infrastructure_engine:
                infrastructure = await asyncio.to_thread(
                    self.infrastructure_engine.run)
            result = await asyncio.to_thread(self.operations_engine.run)
            if self.service_health_engine:
                await asyncio.to_thread(self.service_health_engine.run)
            if self.dashboard_registry and hasattr(
                    self.dashboard_registry, "refresh_state_derived"):
                dashboard_started = time.monotonic()
                deployment_id = str(
                    getattr(self.dashboard_registry, "config", {}).get(
                        "deployment_id") or "root")
                output_path = (
                    self.dashboard_registry.output_root /
                    "operations/itp-operations-wallboard.json")
                logger.info(
                    "dashboard.render.begin deployment_id=%s "
                    "dashboard=operations-wallboard",
                    deployment_id)
                try:
                    await asyncio.to_thread(
                        self.dashboard_registry.refresh_state_derived)
                except Exception as exc:
                    logger.error(
                        "dashboard.render.failed deployment_id=%s "
                        "dashboard=operations-wallboard error_class=%s "
                        "safe_error_summary=state-derived dashboard "
                        "render failed",
                        deployment_id, type(exc).__name__)
                    raise
                logger.info(
                    "dashboard.render.complete deployment_id=%s "
                    "dashboard=operations-wallboard output_path=%s "
                    "duration_ms=%d",
                    deployment_id, output_path,
                    int((time.monotonic() - dashboard_started) * 1000))
            else:
                if self.wallboard_engine:
                    await asyncio.to_thread(self.wallboard_engine.run)
                if self.dashboard_registry:
                    await asyncio.to_thread(self.dashboard_registry.generate)
            if self.state_history_capture and infrastructure is not None:
                try:
                    capture = await asyncio.to_thread(
                        self.state_history_capture.capture_generated,
                        infrastructure, started_at=datetime.fromtimestamp(
                            time.time() - (time.monotonic() - started),
                            timezone.utc),
                        canonical_output="runtime/infrastructure/state.json")
                    if capture:
                        logger.info("state_history result=%s run_id=%s",
                                    capture.status, capture.run_id)
                except Exception:
                    logger.exception("state_history result=degraded")
            logger.info(
                "operations result=success issues=%d risks=%d recommendations=%d",
                len(result["issues"]), len(result["risks"]),
                len(result["recommendations"]))
            if self.health_path:
                self.health_path.touch()
            return result
        except Exception as exc:
            logger.error(
                "operations result=failed error_class=%s",
                type(exc).__name__)
            return None

    async def run(self, discovery_only=False):
        if discovery_only:
            stop_event = asyncio.Event()
            tasks = []
            try:
                await self.execute_once("discover")
                tasks = [
                    asyncio.create_task(self._continuous_loop(
                        collector, "discover",
                        collector.discovery_interval, stop_event))
                    for collector in self.collectors
                ]
                await asyncio.gather(*tasks)
            finally:
                self._shutdown = True
                stop_event.set()
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            return
        stop_event = asyncio.Event()
        tasks = []
        try:
            await self.startup()
            tasks.append(asyncio.create_task(self.run_continuous(
                stop_event=stop_event, include_discovery=True,
                startup_completed=True)))
            if self.inventory_engine:
                tasks.append(asyncio.create_task(self._lifecycle_loop()))
            if self.operations_engine:
                tasks.append(asyncio.create_task(self._operations_loop()))
            await asyncio.gather(*tasks)
        finally:
            self._shutdown = True
            stop_event.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if not tasks:
                self.state.write(
                    lifecycle_state=SchedulerLifecycle.STOPPED.value,
                    active_phase=None, current_run_id=None,
                    updated_at=_utc(self.now()))
