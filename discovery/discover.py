#!/usr/bin/env python3
"""Compatibility CLI for SNMP discovery and Telegraf target generation."""
import argparse
import asyncio
import logging

from collectors.scheduler import Scheduler
from collectors.snmp.collector import SNMPCollector
from collectors.snmp.discovery import (
    AP_MODEL_MARKERS, ENTERPRISES, JUNIPER_SWITCH_OBJECT_IDS, OIDS,
    WIRELESS_ENTERPRISES, classify, enumerate_addresses, enumerate_targets,
    load_config, merge_inventory, snmp_get, utcnow,
)
from collectors.snmp.generator import generate_configs, group_devices, render_group, toml_string
from collectors.writer import atomic_remove, atomic_write


async def discover_once(config, inventory_path, generated_dir):
    """Preserve the original one-shot programmatic API."""
    return await SNMPCollector(config, inventory_path, generated_dir).discover()


async def main_async(args):
    config = load_config(args.config)
    if not config.get("collectors", {}).get("snmp", {}).get("enabled", True):
        if args.command == "once":
            raise ValueError("collector snmp is not enabled")
        LOG = logging.getLogger("snmp-discovery")
        LOG.info("collector=snmp phase=run result=idle enabled=false")
        while True:
            await asyncio.sleep(3600)
    collector = SNMPCollector(
        config,
        args.inventory or "/app/runtime/inventory/devices.json",
        args.generated or "/app/generated",
    )
    scheduler = Scheduler([collector])
    if args.command == "once":
        await scheduler.run_once()
    else:
        await scheduler.run(discovery_only=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("once", "run"))
    parser.add_argument("--config", required=True)
    parser.add_argument("--inventory")
    parser.add_argument("--generated")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(main_async(args))
    except (ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
