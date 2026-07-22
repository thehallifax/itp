import asyncio
import inspect

from collectors import BaseCollector, CollectorRegistry, SNMPCollector
from collectors.inventory import InventoryManager
from collectors.scheduler import Scheduler
from collectors.writer import InfluxWriter


def test_snmp_collector_is_registered_automatically():
    assert CollectorRegistry.get("snmp") is SNMPCollector
    assert issubclass(SNMPCollector, BaseCollector)
    assert inspect.isabstract(BaseCollector)


def test_inventory_manager_round_trip(tmp_path):
    manager = InventoryManager(tmp_path / "devices.json")
    expected = {"schema_version": 1, "devices": []}
    assert manager.write(expected) == expected
    assert manager.read() == expected


def test_scheduler_honours_collector_interface():
    class ExampleCollector(BaseCollector):
        name = "example"

        def __init__(self):
            self.calls = 0

        async def discover(self):
            self.calls += 1
            return self.calls

        def collect(self):
            return None

    collector = ExampleCollector()
    assert asyncio.run(Scheduler([collector]).run_once()) == [1]
    assert collector.calls == 1


def test_influx_writer_wraps_existing_delegate():
    writer = InfluxWriter(lambda payload: ("written", payload))
    assert writer.write("metric") == ("written", "metric")
