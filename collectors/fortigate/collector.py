"""Native read-only FortiGate HTTPS API collector."""
import asyncio
import logging
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from collectors.base import BaseCollector
from collectors.inventory import InventoryManager
from collectors.registry import CollectorRegistry
from collectors.writer import InfluxWriter
from collectors.configuration import parse_bool_default, parse_int
from .client import FortiGateClient
from .models import FortiGateConfig, FortiGateError
from .normalizer import normalize

LOG = logging.getLogger("collector.fortigate")


def _utcnow():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _legacy_points(record, points):
    common = {"collector": "fortigate", "customer": record["customer"], "site": record["site"],
              "vendor": "fortinet", "platform": "fortigate", "device_role": "firewall",
              "hostname": record["hostname"], "device_ip": record.get("ip", "")}
    device = next((point for point in points if point["measurement"] == "infrastructure_device"), None)
    security = next((point for point in points if point["measurement"] == "security_appliance"), None)
    result = []
    if device:
        system_fields = {"uptime_ticks": int(device["fields"].get("uptime_seconds", 0) * 100)}
        result.append({"measurement": "fortigate_system", "tags": common, "fields": system_fields})
        performance = {}
        if "cpu_percent" in device["fields"]: performance["cpu_percent"] = device["fields"]["cpu_percent"]
        if "memory_used_percent" in device["fields"]: performance["memory_percent"] = device["fields"]["memory_used_percent"]
        if security and "session_count" in security["fields"]: performance["current_sessions"] = security["fields"]["session_count"]
        performance_tags = dict(common)
        if record.get("firmware"): performance_tags["firmware"] = record["firmware"]
        if performance: result.append({"measurement": "fortigate_performance", "tags": performance_tags, "fields": performance})
    for point in (item for item in points if item["measurement"] == "network_interface"):
        tags = {**common, "interface_name": point["tags"]["interface_name"]}
        if point["tags"].get("interface_description"): tags["interface_description"] = point["tags"]["interface_description"]
        aliases = {"admin_status": "admin_status", "operational_status": "operational_status",
            "speed_bps": "interface_speed_bps", "rx_bytes": "in_octets", "tx_bytes": "out_octets",
            "rx_errors": "in_errors", "tx_errors": "out_errors", "rx_discards": "in_discards", "tx_discards": "out_discards"}
        fields = {aliases[key]: value for key, value in point["fields"].items() if key in aliases}
        status_values = {"up": 1, "down": 2, "testing": 3, "unknown": 4, "dormant": 5,
                         "not-present": 6, "lower-layer-down": 7}
        for key in ("admin_status", "operational_status"):
            if key in fields and isinstance(fields[key], str):
                fields[key] = status_values.get(fields[key].lower(), 4)
        if fields: result.append({"measurement": "fortigate_interfaces", "tags": tags, "fields": fields})
    return result


@CollectorRegistry.register
class FortiGateCollector(BaseCollector):
    name = "fortigate"
    execution = "edge"

    def __init__(self, config, inventory_path="/app/runtime/inventory/devices.json", *, client=None, writer=None):
        settings = config.get("collectors", {}).get("fortigate", config)
        self.settings = FortiGateConfig(
            base_url=settings.get("host", ""), api_token=settings.get("api_token", ""),
            customer=settings.get("customer") or config.get("customer", "unknown"),
            site=settings.get("site") or config.get("site", "unknown"),
            verify_tls=parse_bool_default(settings.get("verify_tls"), True),
            timeout_seconds=float(settings.get("timeout_seconds", 20)),
            discovery_interval_seconds=parse_int(
                settings.get("discovery_interval_seconds", 21600), minimum=1),
            collection_interval_seconds=parse_int(
                settings.get("collection_interval_seconds", 60), minimum=1),
            max_retries=parse_int(
                settings.get("max_retries", 2), minimum=0, maximum=10))
        if not self.settings.base_url or not self.settings.api_token:
            raise ValueError("FORTIGATE_HOST and FORTIGATE_API_TOKEN are required")
        self.discovery_interval = self.settings.discovery_interval_seconds
        self.collection_interval = self.settings.collection_interval_seconds
        self.inventory = InventoryManager(inventory_path, config.get("inventory"))
        self.client = client or FortiGateClient(self.settings.base_url, self.settings.api_token,
            self.settings.timeout_seconds, verify_tls=self.settings.verify_tls,
            max_retries=self.settings.max_retries)
        self.writer = writer or InfluxWriter.from_config(config)

    async def _snapshot(self):
        results = {"system": (await self.client.endpoint("system")).data}
        diagnostics = []
        for name in ("resources", "interfaces", "ha"):
            result = await self.client.endpoint(name, optional=True)
            results[name] = result.data
            if not result.available: diagnostics.append(result)
        return results, diagnostics

    async def discover(self):
        run_id = self.inventory.engine.begin_source_run("fortigate", self.name, _utcnow())
        try:
            system = (await self.client.endpoint("system")).data
            record, _ = normalize({"system": system}, self.settings)
            result = self.inventory.update_source([record], "fortigate", self.settings.customer,
                self.settings.site, _utcnow(), source_run_id=run_id)
            self.inventory.engine.complete_source_run("fortigate", run_id, success=True,
                records_returned=1, completed_at=_utcnow())
            return result
        except Exception as exc:
            self.inventory.engine.complete_source_run("fortigate", run_id, success=False,
                completed_at=_utcnow(), error_category=getattr(exc, "category", type(exc).__name__))
            raise

    async def collect(self):
        started = time.monotonic(); requests = self.client.api_requests; retries = self.client.retry_count
        success = False; partial = False; category = "success"; written = 0; devices = 0; error_count = 0
        try:
            endpoints, diagnostics = await self._snapshot()
            partial = bool(diagnostics); category = "partial" if partial else "success"; error_count = len(diagnostics)
            record, points = normalize(endpoints, self.settings); devices = 1
            points.extend(_legacy_points(record, points))
            written = await asyncio.to_thread(self.writer.write, points)
            success = True
            return written
        except Exception as exc:
            category = getattr(exc, "category", "write_failure" if not isinstance(exc, FortiGateError) else "invalid_response")
            error_count = max(1, error_count)
            raise
        finally:
            health = {"measurement": "collector_health",
                "tags": {"collector": "fortigate", "customer": self.settings.customer,
                         "site": self.settings.site, "diagnostic_category": category},
                "fields": {"success": success, "partial": partial,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "api_requests": self.client.api_requests - requests,
                    "retry_count": self.client.retry_count - retries, "error_count": error_count,
                    "devices_returned": devices, "points_written": written}}
            try: await asyncio.to_thread(self.writer.write, [health])
            except Exception: LOG.error("collector=fortigate phase=health result=write_failed")

    async def inspect(self):
        endpoints, diagnostics = await self._snapshot()
        record, points = normalize(endpoints, self.settings)
        return {"enabled": True, "api_hostname": urlsplit(self.client.base_url).hostname or "",
            "organization_id": "not-applicable", "site_count": 1, "device_count": 1,
            "device_types": {"fortigate": 1}, "measurements": {}, "points_produced": len(points),
            "always_empty_fields": [], "high_cardinality_warnings": [],
            "influx_write_completed": False, "points_written": 0,
            "partial": bool(diagnostics), "diagnostics": [item.category for item in diagnostics],
            "device_id": record["id"]}

    async def close(self):
        await self.client.close()
