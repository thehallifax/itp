"""Interval scheduler for independently paced collectors."""
import asyncio
import inspect
import time
import logging
from pathlib import Path
from datetime import datetime, timezone


async def _resolve(value):
    return await value if inspect.isawaitable(value) else value


class Scheduler:
    def __init__(self, collectors, health_path=None, *, inventory_engine=None,
                 lifecycle_interval=3600, infrastructure_engine=None,
                 operations_engine=None, service_health_engine=None,
                 wallboard_engine=None, dashboard_registry=None,
                 state_history_capture=None,
                 operations_interval=300):
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
        self.state_history_capture = state_history_capture
        self.operations_interval = max(1, int(operations_interval))
        self._operations_lock = None

    async def run_once(self):
        return [await _resolve(collector.discover()) for collector in self.collectors]

    async def _execute(self, collector, phase):
        outcome = await self._execute_detailed(
            collector, phase, log_exception=True)
        return outcome["value"] if outcome["status"] == "success" else None

    async def _execute_detailed(self, collector, phase, *, log_exception=False):
        """Execute one phase with structured timing for operator commands."""
        started = time.monotonic()
        lock = self._locks[collector]
        if lock is None:
            lock = self._locks[collector] = asyncio.Lock()
        if lock.locked():
            logging.getLogger("collector.scheduler").warning(
                "collector=%s phase=%s result=skipped_overlap", collector.name, phase)
            return {"connector": collector.name, "status": "skipped",
                    "duration_ms": 0, "value": None,
                    "exception_type": "", "reason": "overlapping execution"}
        async with lock:
            try:
                result = await _resolve(getattr(collector, phase)())
                if self.health_path:
                    self.health_path.touch()
                return {"connector": collector.name, "status": "success",
                        "duration_ms": int((time.monotonic() - started) * 1000),
                        "value": result, "exception_type": "", "reason": ""}
            except Exception as exc:
                logger = logging.getLogger("collector.scheduler")
                if log_exception:
                    logger.exception(
                        "collector=%s phase=%s result=failed",
                        collector.name, phase)
                else:
                    logger.error(
                        "collector=%s phase=%s result=failed exception_type=%s",
                        collector.name, phase, type(exc).__name__)
                return {"connector": collector.name, "status": "failed",
                        "duration_ms": int((time.monotonic() - started) * 1000),
                        "value": None, "exception_type": type(exc).__name__,
                        "reason": "collector execution failed"}

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
        while not stop_event.is_set():
            started = time.monotonic()
            if on_start:
                await _resolve(on_start(collector.name, phase))
            outcome = await self._execute_detailed(collector, phase)
            if on_outcome:
                await _resolve(on_outcome(outcome, phase))
            delay = max(0.1, interval - (time.monotonic() - started))
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def run_continuous(self, *, stop_event=None, on_start=None,
                             on_outcome=None, include_discovery=True):
        """Continuously execute configured collectors using their intervals."""
        stop_event = stop_event or asyncio.Event()
        tasks = []
        for collector in self.collectors:
            if include_discovery:
                tasks.append(asyncio.create_task(self._continuous_loop(
                    collector, "discover", collector.discovery_interval,
                    stop_event, on_start, on_outcome)))
            tasks.append(asyncio.create_task(self._continuous_loop(
                collector, "collect", collector.collection_interval,
                stop_event, on_start, on_outcome)))
        if not tasks:
            await stop_event.wait()
            return
        await asyncio.gather(*tasks)

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
            if self.infrastructure_engine:
                infrastructure = await asyncio.to_thread(
                    self.infrastructure_engine.run)
            result = await asyncio.to_thread(self.operations_engine.run)
            if self.service_health_engine:
                await asyncio.to_thread(self.service_health_engine.run)
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
        except Exception:
            logger.exception("operations result=failed")
            return None

    async def run(self, discovery_only=False):
        tasks = [asyncio.create_task(self._loop(c, "discover", c.discovery_interval))
                 for c in self.collectors]
        if not discovery_only:
            tasks.extend(asyncio.create_task(self._loop(c, "collect", c.collection_interval))
                         for c in self.collectors)
            if self.inventory_engine:
                tasks.append(asyncio.create_task(self._lifecycle_loop()))
            if self.operations_engine:
                tasks.append(asyncio.create_task(self._operations_loop()))
        await asyncio.gather(*tasks)
