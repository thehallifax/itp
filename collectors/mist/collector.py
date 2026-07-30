"""Native read-only Juniper Mist collector."""
import asyncio
import logging
import time
from collections import Counter, defaultdict
from urllib.parse import urlsplit
from datetime import datetime, timezone

from collectors.base import BaseCollector
from collectors.inventory import InventoryManager
from collectors.registry import CollectorRegistry
from collectors.writer import InfluxWriter
from collectors.configuration import parse_bool_default
from collectors.tls import deployment_ca_bundle
from .client import MistClient
from .models import MistConfig
from .normalizer import METRIC_FIELDS, metric_points, normalize_device

LOG = logging.getLogger("collector.mist")


def _utcnow():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@CollectorRegistry.register
class MistCollector(BaseCollector):
    name = "mist"
    execution = "central"

    def __init__(self, config, inventory_path="/app/runtime/inventory/devices.json", *, client=None, writer=None):
        settings = config.get("collectors", {}).get("mist", config)
        self.settings = MistConfig(
            base_url=settings.get("base_url", "https://api.mist.com"),
            organization_id=settings.get("organization_id", ""), api_token=settings.get("api_token", ""),
            discovery_interval_seconds=int(settings.get("discovery_interval_seconds", 21600)),
            collection_interval_seconds=int(settings.get("collection_interval_seconds", 120)),
            timeout_seconds=float(settings.get("timeout_seconds", 20)),
            verify_tls=parse_bool_default(settings.get("verify_tls"), True),
            ca_bundle=(
                str(settings.get("ca_bundle") or "").strip()
                or deployment_ca_bundle(config)),
        )
        if not self.settings.organization_id or not self.settings.api_token:
            raise ValueError("MIST_ORG_ID and MIST_API_TOKEN are required")
        self.customer = config.get("customer", "unknown")
        self.default_site = config.get("site", "unknown")
        self.discovery_interval = self.settings.discovery_interval_seconds
        self.collection_interval = self.settings.collection_interval_seconds
        self.inventory = InventoryManager(inventory_path, config.get("inventory"))
        self.client = client or MistClient(self.settings.base_url, self.settings.organization_id,
            self.settings.api_token, self.settings.timeout_seconds,
            verify_tls=self.settings.verify_tls,
            ca_bundle=self.settings.ca_bundle)
        self.writer = writer or InfluxWriter.from_config(config)

    @staticmethod
    def _stat_key(value):
        return value.get("id") or value.get("device_id") or value.get("serial") or value.get("mac")

    @staticmethod
    def _stats_index(stats):
        result = {}
        for item in stats:
            for key in (item.get("id"), item.get("device_id"), item.get("serial"), item.get("mac")):
                if key: result[key] = item
        return result

    async def _fetch(self):
        sites, inventory, stats = await asyncio.gather(
            self.client.sites(), self.client.inventory(), self.client.device_stats())
        site_names = {item.get("id"): item.get("name") for item in sites if item.get("id")}
        stats_by_key = self._stats_index(stats)
        records = []
        for device in inventory:
            stat = next((stats_by_key[key] for key in (device.get("id"), device.get("device_id"), device.get("serial"), device.get("mac")) if key in stats_by_key), {})
            records.append(normalize_device(device, stat, site_names, self.settings.organization_id,
                                            self.customer, self.default_site))
        return records, stats_by_key

    async def discover(self):
        started = time.monotonic(); requests = self.client.api_requests
        run_started = _utcnow()
        run_id = self.inventory.engine.begin_source_run("mist", self.name, run_started)
        try:
            records, _ = await self._fetch()
            result = self.inventory.update_source(records, "mist", self.customer, self.default_site,
                                                  _utcnow(), source_run_id=run_id)
            self.inventory.engine.complete_source_run("mist", run_id, success=True,
                                                       records_returned=len(records), completed_at=_utcnow())
        except Exception as exc:
            self.inventory.engine.complete_source_run("mist", run_id, success=False,
                completed_at=_utcnow(), error_category=type(exc).__name__)
            raise
        LOG.info("collector=mist phase=discover duration_ms=%d result_count=%d api_requests=%d",
                 (time.monotonic() - started) * 1000, len(records), self.client.api_requests - requests)
        return result

    async def collect(self):
        started = time.monotonic(); requests = self.client.api_requests; retries = self.client.retry_count
        points = []; devices_returned = 0; error = None; written = 0
        try:
            stats = await self.client.device_stats()
            stats_by_key = self._stats_index(stats)
            records = [item for item in self.inventory.read().get("devices", [])
                       if item.get("source") == "mist" and item.get("status") == "active"]
            if not records:
                records, stats_by_key = await self._fetch()
            devices_returned = len(records)
            for record in records:
                key = record.get("external_device_id") or record.get("serial") or record.get("mac")
                points.extend(metric_points(record, stats_by_key.get(key, {})))
            written = await asyncio.to_thread(self.writer.write, points)
            return written
        except Exception as exc:
            error = exc
            raise
        finally:
            duration = int((time.monotonic() - started) * 1000)
            health = {"measurement": "collector_health",
                "tags": {"collector": "mist", "customer": self.customer, "site": self.default_site},
                "fields": {"success": error is None, "duration_ms": duration,
                    "devices_returned": devices_returned, "points_written": written,
                    "api_requests": self.client.api_requests - requests,
                    "retry_count": self.client.retry_count - retries,
                    "error_count": 0 if error is None else 1}}
            if self.client.rate_limit_remaining is not None:
                health["fields"]["rate_limit_remaining"] = self.client.rate_limit_remaining
            try: await asyncio.to_thread(self.writer.write, [health])
            except Exception: LOG.error("collector=mist phase=health result=write_failed")
            LOG.info("collector=mist phase=collect duration_ms=%d result_count=%d points_written=%d api_requests=%d",
                     duration, devices_returned, written, self.client.api_requests - requests)

    async def inspect(self):
        sites, inventory, stats = await asyncio.gather(
            self.client.sites(), self.client.inventory(), self.client.device_stats())
        site_names = {item.get("id"): item.get("name") for item in sites if item.get("id")}
        stats_by_key = self._stats_index(stats)
        records = []
        points = []
        for device in inventory:
            stat = next((stats_by_key[key] for key in
                (device.get("id"), device.get("device_id"), device.get("serial"), device.get("mac"))
                if key in stats_by_key), {})
            record = normalize_device(device, stat, site_names, self.settings.organization_id,
                                      self.customer, self.default_site)
            records.append(record)
            points.extend(metric_points(record, stat))
        measurements = defaultdict(lambda: {"tags": set(), "fields": set(),
            "field_counts": Counter(), "tag_values": defaultdict(set), "points": 0})
        for point in points:
            summary = measurements[point["measurement"]]
            summary["points"] += 1
            summary["tags"].update(key for key, value in point.get("tags", {}).items() if value not in (None, ""))
            summary["fields"].update(key for key, value in point["fields"].items() if value not in (None, ""))
            summary["field_counts"].update(key for key, value in point["fields"].items() if value not in (None, ""))
            for key, value in point.get("tags", {}).items():
                if value not in (None, ""): summary["tag_values"][key].add(str(value))
        supported = set(METRIC_FIELDS)
        always_empty = sorted(key for key in supported if any(key in stat for stat in stats)
                              and all(stat.get(key) in (None, "") for stat in stats))
        cardinality = []
        for measurement, summary in measurements.items():
            for key, values in summary["tag_values"].items():
                if len(values) > 100 and len(values) / max(1, len(points)) > 0.3:
                    cardinality.append(f"{measurement}.{key}={len(values)}")
        measurements["collector_health"]["tags"].update(("collector", "customer", "site"))
        measurements["collector_health"]["fields"].update(("success", "duration_ms", "devices_returned",
            "points_written", "api_requests", "retry_count", "error_count"))
        measurements["collector_health"]["field_counts"].update({key: 1 for key in measurements["collector_health"]["fields"]})
        measurements["collector_health"]["points"] = 1
        written = await asyncio.to_thread(self.writer.write, points)
        return {
            "enabled": True, "api_hostname": urlsplit(self.settings.base_url).hostname or "",
            "organization_id": self.settings.organization_id, "site_count": len(sites),
            "device_count": len(records), "device_types": dict(sorted(Counter(r["platform"] for r in records).items())),
            "measurements": {name: {"tags": sorted(value["tags"]), "fields": sorted(value["fields"]),
                                             "field_counts": dict(sorted(value["field_counts"].items())),
                                             "points": value["points"]}
                             for name, value in sorted(measurements.items())},
            "points_produced": len(points), "always_empty_fields": always_empty,
            "high_cardinality_warnings": sorted(cardinality), "influx_write_completed": written == len(points),
            "points_written": written,
        }

    async def close(self):
        await self.client.close()
