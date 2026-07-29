"""SNMP collector implementation."""
import asyncio
import logging

from collectors.base import BaseCollector
from collectors.inventory import InventoryManager
from collectors.registry import CollectorRegistry
from .discovery import enumerate_targets, snmp_get, utcnow
from .generator import generate_configs

LOG = logging.getLogger("snmp-discovery")


@CollectorRegistry.register
class SNMPCollector(BaseCollector):
    name = "snmp"
    execution = "edge"

    def __init__(self, config, inventory_path, generated_dir):
        self.config = config
        self.inventory = InventoryManager(inventory_path, config.get("inventory"))
        self.generated_dir = generated_dir
        self.discovery_interval = int(config["discovery"].get("interval_seconds", 3600))
        self.collection_interval = 30

    async def discover(self):
        run_id = self.inventory.engine.begin_source_run("snmp", self.name, utcnow())
        try:
            targets = enumerate_targets(self.config)
            options = self.config["discovery"]
            LOG.info("scanning %d addresses in approved CIDRs", len(targets))
            semaphore = asyncio.Semaphore(int(options.get("concurrency", 40)))
            tasks = [snmp_get(
                ip, self.config["snmp"]["communities"],
                float(options.get("timeout_seconds", 1.5)),
                int(options.get("retries", 1)), semaphore,
            ) for ip, _ in targets]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            discoveries = []
            failures = 0
            for (ip, purpose), result in zip(targets, results):
                if isinstance(result, Exception):
                    failures += 1
                    LOG.warning("SNMP query failed for %s: %s", ip, result)
                elif result:
                    discoveries.append((ip, result[0], result[1], purpose))
            inventory = self.inventory.update(self.config, discoveries, source_run_id=run_id,
                                              partial=failures > 0)
            self.inventory.engine.complete_source_run("snmp", run_id, success=True,
                records_returned=len(discoveries), completed_at=utcnow(), partial=failures > 0)
        except Exception as exc:
            self.inventory.engine.complete_source_run("snmp", run_id, success=False,
                completed_at=utcnow(), error_category=type(exc).__name__)
            raise
        generate_configs(inventory, self.config["snmp"]["communities"], self.generated_dir)
        retained = sum(1 for item in inventory["devices"]
                       if item.get("source") in (None, "snmp") and item.get("status") != "active")
        LOG.info("discovery complete: %d active, %d retained unreachable",
                 len(discoveries), retained)
        return inventory

    def collect(self):
        inventory = self.inventory.read()
        generate_configs(inventory, self.config["snmp"]["communities"], self.generated_dir)
        return inventory
