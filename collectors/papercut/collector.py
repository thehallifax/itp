"""Read-only PaperCut MF System Health collector."""
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
from .client import PaperCutClient
from .models import PaperCutConfig, PaperCutError
from .normalizer import normalize


LOG = logging.getLogger("collector.papercut")


def _utcnow():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_settings(config):
    raw = config.get("collectors", {}).get("papercut", config)
    base_url = str(raw.get("base_url") or "").strip()
    site = str(raw.get("site") or config.get("site") or "").strip()
    key_env = str(
        raw.get("authorization_key_env") or
        "PAPERCUT_AUTHORIZATION_KEY").strip()
    key = str(raw.get("authorization_key") or "")
    if not base_url:
        raise ValueError("collectors.papercut.base_url is required")
    if not site:
        raise ValueError(
            "collectors.papercut.site must reference a canonical site")
    PaperCutClient.normalize_url(base_url)
    timeout = float(raw.get("timeout_seconds", 20))
    if timeout <= 0 or timeout > 120:
        raise ValueError(
            "PaperCut timeout_seconds must be between 1 and 120")
    discovery_interval = parse_int(
        raw.get("discovery_interval_seconds", 21600), minimum=1)
    collection_interval = parse_int(
        raw.get("collection_interval_seconds", 60), minimum=1)
    retries = parse_int(raw.get("max_retries", 2), minimum=0, maximum=10)
    disk_threshold = float(raw.get("disk_warning_percent", 85))
    jvm_threshold = float(raw.get("jvm_warning_percent", 85))
    held_threshold = int(raw.get("held_jobs_warning", 25))
    assurance_threshold = int(
        raw.get("upgrade_assurance_warning_days", 90))
    uptime_threshold = int(raw.get("uptime_advisory_days", 180))
    if discovery_interval <= 0 or collection_interval <= 0:
        raise ValueError("PaperCut collection intervals must be positive")
    if retries < 0 or retries > 10:
        raise ValueError("PaperCut max_retries must be between 0 and 10")
    if not 0 <= disk_threshold <= 100 or \
            not 0 <= jvm_threshold <= 100:
        raise ValueError(
            "PaperCut utilisation thresholds must be between 0 and 100")
    if min(held_threshold, assurance_threshold, uptime_threshold) < 0:
        raise ValueError("PaperCut advisory thresholds cannot be negative")
    return PaperCutConfig(
        base_url=base_url, authorization_key=key,
        customer=str(raw.get("customer") or
                     config.get("customer") or "unknown"),
        site=site, verify_tls=parse_bool_default(raw.get("verify_tls"), True),
        timeout_seconds=timeout,
        discovery_interval_seconds=discovery_interval,
        collection_interval_seconds=collection_interval,
        max_retries=retries, disk_warning_percent=disk_threshold,
        jvm_warning_percent=jvm_threshold,
        held_jobs_warning=held_threshold,
        upgrade_assurance_warning_days=assurance_threshold,
        uptime_advisory_days=uptime_threshold,
        customer_name=str(raw.get("customer_name") or
                          (config.get("identity") or {}).get(
                              "customer_name") or ""),
        site_name=str(raw.get("site_name") or config.get("site_name") or ""),
        deployment_id=str(config.get("deployment_id") or ""))


@CollectorRegistry.register
class PaperCutCollector(BaseCollector):
    name = "papercut"
    execution = "central"

    def __init__(self, config,
                 inventory_path="/app/runtime/inventory/devices.json", *,
                 client=None, writer=None):
        self.settings = validate_settings(config)
        self.discovery_interval = self.settings.discovery_interval_seconds
        self.collection_interval = self.settings.collection_interval_seconds
        self.inventory = InventoryManager(
            inventory_path, config.get("inventory"))
        self.client = client or PaperCutClient(
            self.settings.base_url, self.settings.authorization_key,
            self.settings.timeout_seconds,
            verify_tls=self.settings.verify_tls,
            max_retries=self.settings.max_retries)
        self.writer = writer or InfluxWriter.from_config(config)

    async def _normalized(self):
        snapshot = await self.client.snapshot()
        records, points, conditions = normalize(
            snapshot, self.settings, _utcnow())
        return snapshot, records, points, conditions

    async def discover(self):
        run_id = self.inventory.engine.begin_source_run(
            "papercut", self.name, _utcnow())
        try:
            snapshot, records, _, _ = await self._normalized()
            result = self.inventory.update_source(
                records, "papercut", self.settings.customer,
                self.settings.site, _utcnow(), source_run_id=run_id)
            self.inventory.engine.complete_source_run(
                "papercut", run_id, success=True,
                records_returned=len(records), completed_at=_utcnow(),
                error_category="partial" if snapshot["partial"] else None)
            return result
        except Exception as exc:
            self.inventory.engine.complete_source_run(
                "papercut", run_id, success=False,
                completed_at=_utcnow(),
                error_category=getattr(
                    exc, "category", type(exc).__name__))
            raise

    async def collect(self):
        started = time.monotonic()
        requests = self.client.api_requests
        retries = self.client.retry_count
        success = False
        partial = False
        written = 0
        records = []
        category = "success"
        try:
            snapshot, records, points, conditions = \
                await self._normalized()
            partial = bool(snapshot["partial"])
            category = "partial" if partial else "success"
            written = await asyncio.to_thread(self.writer.write, points)
            success = True
            return {
                "status": category, "points_written": written,
                "assets_returned": len(records),
                "findings": len(conditions), "partial": partial}
        except Exception as exc:
            category = getattr(
                exc, "category",
                "write_failure" if not isinstance(exc, PaperCutError)
                else "invalid_response")
            raise
        finally:
            health = {
                "measurement": "collector_health",
                "tags": {
                    "collector": "papercut",
                    "deployment_id": self.settings.deployment_id,
                    "customer": self.settings.customer,
                    "customer_id": self.settings.customer,
                    "site": self.settings.site,
                    "site_id": self.settings.site,
                    "customer_name": self.settings.customer_name,
                    "site_name": self.settings.site_name,
                    "diagnostic_category": category},
                "fields": {
                    "success": success, "partial": partial,
                    "duration_ms": int(
                        (time.monotonic() - started) * 1000),
                    "api_requests": self.client.api_requests - requests,
                    "retry_count": self.client.retry_count - retries,
                    "error_count": 0 if success and not partial else 1,
                    "devices_returned": len(records),
                    "points_written": written}}
            try:
                await asyncio.to_thread(self.writer.write, [health])
            except Exception:
                LOG.error(
                    "collector=papercut phase=health result=write_failed")

    async def inspect(self):
        snapshot, records, points, conditions = await self._normalized()
        return {
            "enabled": True,
            "api_hostname": urlsplit(self.client.base_url).hostname or "",
            "organization_id": "not-applicable",
            "site_count": 1, "device_count": len(records),
            "device_types": {
                "server": 1, "printer": max(0, len(records) - 1)},
            "measurements": {},
            "points_produced": len(points),
            "always_empty_fields": [],
            "high_cardinality_warnings": [],
            "influx_write_completed": False,
            "points_written": 0,
            "partial": bool(snapshot["partial"]),
            "diagnostics": {
                "endpoint_reachable": True,
                "authentication_successful": True,
                "valid_json": True,
                "partial_collection": bool(snapshot["partial"])},
            "findings": len(conditions)}

    async def close(self):
        await self.client.close()
