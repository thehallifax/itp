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
from collectors.configuration import parse_bool_default, parse_int
from .api import PaloAltoClient
from .mapper import map_snapshot
from .models import PaloAltoConfig, PaloAltoError, Snapshot, WanInterface
from . import parser

LOG = logging.getLogger("collector.paloalto")
WAN_ROLES = {"primary", "secondary", "tertiary", "backup", "cellular",
             "mpls", "internet", "other"}


def _utcnow():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _add_wan_rate_samples(record, previous):
    """Derive bounded WAN rates from successive inventory observations.

    PAN-OS exposes cumulative octet counters. Counter resets and incomplete
    observations are intentionally omitted instead of producing misleading
    negative or zero traffic.
    """
    current_values = record.get("extensions", {}).get("wan_interfaces", [])
    previous_values = (previous or {}).get("extensions", {}).get("wan_interfaces", [])
    by_interface = {value.get("interface_name"): value for value in previous_values}
    for current in current_values:
        old = by_interface.get(current.get("interface_name"))
        if not old:
            continue
        current_time = _parse_time(current.get("observed_at"))
        previous_time = _parse_time(old.get("observed_at"))
        elapsed = ((current_time - previous_time).total_seconds()
                   if current_time and previous_time else 0)
        if elapsed <= 0:
            continue
        sample = {"time": current.get("observed_at")}
        for counter, rate in (("rx_bytes_total", "rx_bps"), ("tx_bytes_total", "tx_bps")):
            before, after = old.get(counter), current.get(counter)
            if isinstance(before, (int, float)) and isinstance(after, (int, float)) and after >= before:
                sample[rate] = round((after - before) * 8 / elapsed, 3)
        if len(sample) == 1:
            continue
        history = [value for value in old.get("samples", [])
                   if isinstance(value, dict) and value.get("time")]
        current["samples"] = (history + [sample])[-120:]
    return record


def validate_settings(config, *, require_key=True):
    raw = config.get("collectors", {}).get("paloalto", config)
    base_url = str(raw.get("base_url") or "").strip()
    site = str(raw.get("site") or "").strip()
    api_key_env = str(raw.get("api_key_env") or "PALOALTO_API_KEY").strip()
    api_key = str(raw.get("api_key") or "")
    allow_http = parse_bool_default(
        raw.get("allow_insecure_http"), False)
    if not base_url: raise ValueError("collectors.paloalto.base_url is required")
    if not site: raise ValueError("collectors.paloalto.site must reference a canonical site ID")
    if not api_key_env: raise ValueError("collectors.paloalto.api_key_env is required")
    if require_key and not api_key:
        raise ValueError(
            f"Palo Alto API key {api_key_env} is required in resolved configuration")
    PaloAltoClient.normalize_url(base_url, allow_http=allow_http)
    timeout = float(raw.get("timeout_seconds", 20))
    if timeout <= 0 or timeout > 120: raise ValueError("Palo Alto timeout_seconds must be between 1 and 120")
    ca_bundle = str(raw.get("ca_bundle") or "").strip() or None
    if ca_bundle and not os.path.isfile(ca_bundle):
        raise ValueError("Palo Alto custom CA bundle does not exist")
    configured_wan = raw.get("wan_interfaces") or []
    if not isinstance(configured_wan, list):
        raise ValueError("collectors.paloalto.wan_interfaces must be a list")
    wan = []
    for index, value in enumerate(configured_wan):
        if not isinstance(value, dict):
            raise ValueError(f"Palo Alto WAN interface entry {index + 1} must be a mapping")
        name = str(value.get("name") or "").strip()
        role = str(value.get("role") or "").strip().lower()
        display = str(value.get("display_name") or name).strip()
        if not name or role not in WAN_ROLES or not display:
            raise ValueError(
                f"Palo Alto WAN interface entry {index + 1} requires name, display_name, "
                f"and role in {sorted(WAN_ROLES)}")
        wan.append(WanInterface(name, role, display))
    names = [value.name for value in wan]
    if len(names) != len(set(names)):
        raise ValueError("Palo Alto wan_interfaces contains duplicate interface names")
    if sum(value.role == "primary" for value in wan) > 1:
        raise ValueError("Palo Alto wan_interfaces supports only one primary interface")
    return PaloAltoConfig(base_url=base_url, api_key=api_key, api_key_env=api_key_env,
        customer=str(raw.get("customer") or config.get("customer", "unknown")),
        site=site, verify_tls=parse_bool_default(raw.get("verify_tls"), True),
        ca_bundle=ca_bundle, timeout_seconds=timeout,
        discovery_interval_seconds=parse_int(
            raw.get("discovery_interval_seconds", 21600), minimum=1),
        collection_interval_seconds=parse_int(
            raw.get("collection_interval_seconds", 60), minimum=1),
        max_retries=parse_int(raw.get("max_retries", 2), minimum=0, maximum=10),
        expected_interfaces=tuple(sorted(set(raw.get("expected_interfaces") or []))),
        collect_interfaces=parse_bool_default(raw.get("collect_interfaces"), True),
        collect_ha=parse_bool_default(raw.get("collect_ha"), True),
        collect_system_resources=parse_bool_default(
            raw.get("collect_system_resources"), True),
        collect_licenses=parse_bool_default(raw.get("collect_licenses"), True),
        collect_content_versions=parse_bool_default(
            raw.get("collect_content_versions"), True),
        licence_expiry_days=parse_int(
            raw.get("licence_expiry_days", 30), minimum=0),
        wan_interfaces=tuple(wan),
        content_warning_days=parse_int(
            raw.get("content_warning_days", 30), minimum=0),
        content_critical_days=parse_int(
            raw.get("content_critical_days", 90), minimum=0),
        customer_name=str(raw.get("customer_name") or
                          (config.get("identity") or {}).get(
                              "customer_name") or ""),
        site_name=str(raw.get("site_name") or config.get("site_name") or ""),
        deployment_id=str(config.get("deployment_id") or ""))


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
            allow_http=parse_bool_default(
                raw.get("allow_insecure_http"), False),
            max_retries=self.settings.max_retries)
        self.writer = writer or InfluxWriter.from_config(config)

    async def _snapshot(self):
        system_result = await self.client.op("system")
        capabilities = {"system": type("Result", (), {"available": True, "data": system_result,
                                                       "category": "success", "message": ""})()}
        enabled = {
            "ha": self.settings.collect_ha,
            "interfaces": self.settings.collect_interfaces,
            "interface_counters": self.settings.collect_interfaces,
            "resources": self.settings.collect_system_resources,
            "resource_monitor": self.settings.collect_system_resources,
            "sessions": self.settings.collect_system_resources,
            "licenses": self.settings.collect_licenses,
        }
        for name, collect in enabled.items():
            if collect: capabilities[name] = await self.client.capability(name)
        return Snapshot(capabilities)

    def _parse(self, snapshot):
        values = {"system": parser.system(snapshot.capabilities["system"].data)}
        parsers = {"ha": parser.ha, "interfaces": parser.interfaces,
                   "interface_counters": parser.interface_counters,
                   "resources": parser.resources,
                   "resource_monitor": parser.resource_monitor,
                   "sessions": parser.sessions, "licenses": parser.licenses}
        for name, function in parsers.items():
            capability = snapshot.capabilities.get(name)
            if capability and capability.available:
                try: values[name] = function(capability.data)
                except Exception:
                    LOG.warning("collector=paloalto operation=%s category=parse message=capability unavailable",
                                name)
        by_name = {value["name"]: value for value in values.get("interfaces", [])}
        for counter in values.get("interface_counters", []):
            if counter["name"] in by_name:
                by_name[counter["name"]].update(
                    {key: value for key, value in counter.items() if key != "name"})
        values["interfaces"] = [by_name[name] for name in sorted(by_name)]
        discovered = set(by_name)
        values["wan_validation"] = {
            "configured": bool(self.settings.wan_interfaces),
            "missing": sorted(value.name for value in self.settings.wan_interfaces
                              if value.name not in discovered),
        }
        values["content_packages"] = parser.content_packages(values["system"])
        return values

    async def discover(self):
        run_id = self.inventory.engine.begin_source_run("paloalto", self.name, _utcnow())
        try:
            snapshot = await self._snapshot()
            parsed = self._parse(snapshot)
            record, _ = map_snapshot(parsed, self.settings, _utcnow())
            existing = self.inventory.engine.load().get("assets", [])
            previous = next((value for value in existing
                             if value.get("id") == record["id"]
                             or value.get("asset_id") == record["id"]
                             or value.get("source_record_id") == record["id"]), None)
            _add_wan_rate_samples(record, previous)
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
            diagnostics_before = len(self.client.command_diagnostics)
            snapshot = await self._snapshot(); partial = snapshot.partial
            diagnostics = [value for value in snapshot.capabilities.values() if not value.available]
            unavailable = len(diagnostics); category = "partial" if partial else "success"
            record, points = map_snapshot(self._parse(snapshot), self.settings, _utcnow())
            written = await asyncio.to_thread(self.writer.write, points)
            success = True
            return {"status": category, "points_written": written, "asset_id": record["id"],
                    "capabilities_unavailable": sorted(value.name for value in diagnostics),
                    "configuration_diagnostics": record["extensions"].get(
                        "wan_validation", {}).get("missing", [])}
        except Exception as exc:
            category = getattr(exc, "category",
                               "write_failure" if not isinstance(exc, PaloAltoError) else "invalid_response")
            raise
        finally:
            command_diagnostics = self.client.command_diagnostics[
                locals().get("diagnostics_before", len(self.client.command_diagnostics)):]
            durations = [value["duration_ms"] for value in command_diagnostics]
            health = {"measurement": "collector_health",
                "tags": {"collector": "paloalto",
                         "deployment_id": self.settings.deployment_id,
                         "customer": self.settings.customer,
                         "customer_id": self.settings.customer,
                         "site": self.settings.site,
                         "site_id": self.settings.site,
                         "customer_name": self.settings.customer_name,
                         "site_name": self.settings.site_name,
                         "diagnostic_category": category},
                "fields": {"success": success, "partial": partial,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "api_requests": self.client.api_requests - requests,
                    "api_duration_ms_total": sum(durations),
                    "api_duration_ms_max": max(durations, default=0),
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
