"""Interval scheduler for independently paced collectors."""
import asyncio
import inspect
import time
import logging
from pathlib import Path


async def _resolve(value):
    return await value if inspect.isawaitable(value) else value


class Scheduler:
    def __init__(self, collectors, health_path=None, *, inventory_engine=None,
                 lifecycle_interval=3600, infrastructure_engine=None,
                 operations_engine=None, wallboard_engine=None, operations_interval=300):
        self.collectors = list(collectors)
        self.health_path = Path(health_path) if health_path else None
        self._locks = {collector: asyncio.Lock() for collector in self.collectors}
        self.inventory_engine = inventory_engine
        self.lifecycle_interval = max(1, int(lifecycle_interval))
        self._lifecycle_lock = asyncio.Lock()
        self.operations_engine = operations_engine
        self.infrastructure_engine = infrastructure_engine
        self.wallboard_engine = wallboard_engine
        self.operations_interval = max(1, int(operations_interval))
        self._operations_lock = asyncio.Lock()

    async def run_once(self):
        return [await _resolve(collector.discover()) for collector in self.collectors]

    async def _execute(self, collector, phase):
        lock = self._locks[collector]
        if lock.locked():
            logging.getLogger("collector.scheduler").warning(
                "collector=%s phase=%s result=skipped_overlap", collector.name, phase)
            return None
        async with lock:
            try:
                result = await _resolve(getattr(collector, phase)())
                if self.health_path:
                    self.health_path.touch()
                return result
            except Exception:
                logging.getLogger("collector.scheduler").exception(
                    "collector=%s phase=%s result=failed", collector.name, phase)
                return None

    async def _loop(self, collector, phase, interval):
        while True:
            started = time.monotonic()
            await self._execute(collector, phase)
            await asyncio.sleep(max(0.1, interval - (time.monotonic() - started)))

    async def _execute_lifecycle(self):
        logger = logging.getLogger("collector.inventory")
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
        while True:
            started = time.monotonic()
            async with self._operations_lock:
                try:
                    if self.infrastructure_engine:
                        await asyncio.to_thread(self.infrastructure_engine.run)
                    result = await asyncio.to_thread(self.operations_engine.run)
                    if self.wallboard_engine:
                        await asyncio.to_thread(self.wallboard_engine.run)
                    logger.info("operations result=success issues=%d risks=%d recommendations=%d",
                        len(result["issues"]), len(result["risks"]), len(result["recommendations"]))
                    if self.health_path: self.health_path.touch()
                except Exception:
                    logger.exception("operations result=failed")
            await asyncio.sleep(max(1, self.operations_interval - (time.monotonic() - started)))

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
