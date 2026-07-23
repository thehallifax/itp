"""Production-safe, read-only Palo Alto Networks PAN-OS collector."""
import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from collectors.base import BaseCollector
from collectors.inventory import InventoryManager
from collectors.registry import CollectorRegistry
from collectors.writer import InfluxWriter
from .api import PaloAltoClient
from .mapper import map_snapshot
from .models import PaloAltoConfig, PaloAltoError, Snapshot
from . import parser

LOG = logging.getLogger("collector.paloalto")


def _utcnow():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _bool(value, default=True):
    if isinstance(value, bool): return value
    if value in (None, ""): return default
    lowered = str(value).strip().lower()
    if lowered in {"true", "1", "yes", "on"}: return True
    if lowered in {"false", "0", "no", "off"}: return False
    raise ValueError("Palo Alto boolean configuration must be true or false")


def validate_settings(config, *, require_key=True):
    raw = config.get("collectors", {}).get("paloalto", config)
    base_url = str(raw.get("base_url") or "").strip()
    site = str(raw.get("site") or "").strip()
    api_key_env = str(raw.get("api_key_env") or "PALOALTO_API_KEY").strip()
    api_key = os.getenv(api_key_env, "")
    allow_http = _bool(raw.get("allow_insecure_http", False), False)
    if not base_url: raise ValueError("collectors.paloalto.base_url is required")
    if not site: raise ValueError("collectors.paloalto.site must reference a canonical site ID")
    if not api_key_env: raise ValueError("collectors.paloalto.api_key_env is required")
    if require_key and not api_key:
        raise ValueError(f"Palo Alto API key environment variable {api_key_env} is required")
    PaloAltoClient.normalize_url(base_url, allow_http=allow_http)
    timeout = float(raw.get("timeout_seconds", 20))
    if timeout <= 0 or timeout > 120: raise ValueError("Palo Alto timeout_seconds must be between 1 and 120")
    ca_bundle = str(raw.get("ca_bundle") or "").strip() or None
    if ca_bundle and not os.path.isfile(ca_bundle):
        raise ValueError("Palo Alto custom CA bundle does not exist")
    return PaloAltoConfig(base_url=base_url, api_key=api_key, api_key_env=api_key_env,
        customer=str(raw.get("customer") or config.get("customer", "unknown")),
        site=site, verify_tls=_bool(raw.get("verify_tls", True)),
        ca_bundle=ca_bundle, timeout_seconds=timeout,
        discovery_interval_seconds=int(raw.get("discovery_interval_seconds", 21600)),
        collection_interval_seconds=int(raw.get("collection_interval_seconds", 60)),
        max_retries=int(raw.get("max_retries", 2)),
        expected_interfaces=tuple(sorted(set(raw.get("expected_interfaces") or []))),
        collect_interfaces=_bool(raw.get("collect_interfaces", True)),
        collect_ha=_bool(raw.get("collect_ha", True)),
        collect_system_resources=_bool(raw.get("collect_system_resources", True)),
        collect_licenses=_bool(raw.get("collect_licenses", True)),
        collect_content_versions=_bool(raw.get("collect_content_versions", True)),
        licence_expiry_days=int(raw.get("licence_expiry_days", 30)))


@CollectorRegistry.register
class PaloAltoCollector(BaseCollector):
    name = "paloalto"
    execution = "edge"

    def __init__(self, config, inventory_path="/app/runtime/inventory/devices.json", *,
                 client=None, writer=None):
        self.settings = validate_settings(config)
        raw = config.get("collectors", {}).get("paloalto", config)
        self.discovery_interval = self.settings.discovery_interval_seconds
        self.collection_interval = self.settings.collection_interval_seconds
        self.inventory = InventoryManager(inventory_path, config.get("inventory"))
        self.client = client or PaloAltoClient(self.settings.base_url, self.settings.api_key,
            self.settings.timeout_seconds, verify_tls=self.settings.verify_tls,
            ca_bundle=self.settings.ca_bundle,
            allow_http=_bool(raw.get("allow_insecure_http", False), False),
            max_retries=self.settings.max_retries)
        self.writer = writer or InfluxWriter()

    async def _snapshot(self):
        system_result = await self.client.op("system")
        capabilities = {"system": type("Result", (), {"available": True, "data": system_result,
                                                       "category": "success", "message": ""})()}
        enabled = {
            "ha": self.settings.collect_ha,
            "interfaces": self.settings.collect_interfaces,
            "resources": self.settings.collect_system_resources,
            "licenses": self.settings.collect_licenses,
        }
        for name, collect in enabled.items():
            if collect: capabilities[name] = await self.client.capability(name)
        return Snapshot(capabilities)

    def _parse(self, snapshot):
        values = {"system": parser.system(snapshot.capabilities["system"].data)}
        parsers = {"ha": parser.ha, "interfaces": parser.interfaces,
                   "resources": parser.resources, "licenses": parser.licenses}
        for name, function in parsers.items():
            capability = snapshot.capabilities.get(name)
            if capability and capability.available:
                try: values[name] = function(capability.data)
                except Exception:
                    LOG.warning("collector=paloalto operation=%s category=parse message=capability unavailable",
                                name)
        return values

    async def discover(self):
        run_id = self.inventory.engine.begin_source_run("paloalto", self.name, _utcnow())
        try:
            snapshot = await self._snapshot()
            parsed = self._parse(snapshot)
            record, _ = map_snapshot(parsed, self.settings, _utcnow())
            result = self.inventory.update_source([record], "paloalto", self.settings.customer,
                self.settings.site, _utcnow(), source_run_id=run_id)
            self.inventory.engine.complete_source_run("paloalto", run_id, success=True,
                records_returned=1, completed_at=_utcnow(),
                error_category="partial" if snapshot.partial else None)
            return result
        except Exception as exc:
            self.inventory.engine.complete_source_run("paloalto", run_id, success=False,
                completed_at=_utcnow(), error_category=getattr(exc, "category", type(exc).__name__))
            raise

    async def collect(self):
        started = time.monotonic(); requests = self.client.api_requests
        retries = self.client.retry_count; success = False; partial = False
        category = "success"; written = 0; unavailable = 0
        try:
            snapshot = await self._snapshot(); partial = snapshot.partial
            diagnostics = [value for value in snapshot.capabilities.values() if not value.available]
            unavailable = len(diagnostics); category = "partial" if partial else "success"
            record, points = map_snapshot(self._parse(snapshot), self.settings, _utcnow())
            written = await asyncio.to_thread(self.writer.write, points)
            success = True
            return {"status": category, "points_written": written, "asset_id": record["id"],
                    "capabilities_unavailable": sorted(value.name for value in diagnostics)}
        except Exception as exc:
            category = getattr(exc, "category",
                               "write_failure" if not isinstance(exc, PaloAltoError) else "invalid_response")
            raise
        finally:
            health = {"measurement": "collector_health",
                "tags": {"collector": "paloalto", "customer": self.settings.customer,
                         "site": self.settings.site, "diagnostic_category": category},
                "fields": {"success": success, "partial": partial,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "api_requests": self.client.api_requests - requests,
                    "retry_count": self.client.retry_count - retries,
                    "error_count": unavailable if success else max(1, unavailable),
                    "devices_returned": 1 if success else 0, "points_written": written}}
            try: await asyncio.to_thread(self.writer.write, [health])
            except Exception: LOG.error("collector=paloalto phase=health result=write_failed")

    async def inspect(self):
        snapshot = await self._snapshot(); parsed = self._parse(snapshot)
        record, points = map_snapshot(parsed, self.settings, _utcnow())
        unavailable = sorted(name for name, value in snapshot.capabilities.items()
                             if not value.available)
        return {"enabled": True, "api_hostname": urlsplit(self.client.base_url).hostname or "",
            "organization_id": "not-applicable", "site_count": 1, "device_count": 1,
            "device_types": {"firewall": 1}, "measurements": {},
            "points_produced": len(points), "always_empty_fields": [],
            "high_cardinality_warnings": [], "influx_write_completed": False,
            "points_written": 0, "partial": snapshot.partial,
            "diagnostics": unavailable, "device_id": record["id"],
            "model": parsed["system"].get("model"),
            "software_version": parsed["system"].get("software_version"),
            "ha_status": (parsed.get("ha") or {}).get("status", "unavailable"),
            "interface_count": len(parsed.get("interfaces", [])),
            "licence_count": len(parsed.get("licenses", []))}

    async def close(self):
        await self.client.close()
