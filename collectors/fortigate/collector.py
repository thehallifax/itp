"""Native read-only FortiGate HTTPS API collector."""
import asyncio
import copy
import logging
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from collectors.base import BaseCollector
from collectors.inventory import InventoryManager
from collectors.registry import CollectorRegistry
from collectors.writer import InfluxWriter
from collectors.configuration import parse_bool_default, parse_int
from collectors.tls import deployment_ca_bundle
from .client import FortiGateClient
from .models import FortiGateConfig, FortiGateError, WanInterface
from .normalizer import normalize, payload, pick

LOG = logging.getLogger("collector.fortigate")
WAN_ROLES = {"primary", "secondary", "backup", "cellular", "mpls", "other"}


def _utcnow():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _add_wan_rate_samples(record, previous):
    current = record.get("extensions", {}).get("wan_interfaces", [])
    prior = {(value.get("interface_name") or value.get("name")): value
             for value in (previous or {}).get("extensions", {}).get(
                 "wan_interfaces", [])}
    for value in current:
        old = prior.get(value.get("interface_name"))
        if not old:
            continue
        current_time = _parse_time(value.get("observed_at"))
        previous_time = _parse_time(old.get("observed_at"))
        elapsed = ((current_time - previous_time).total_seconds()
                   if current_time and previous_time else 0)
        if elapsed <= 0:
            continue
        sample = {"time": value.get("observed_at")}
        for counter, rate in (("rx_bytes_total", "rx_bps"),
                              ("tx_bytes_total", "tx_bps")):
            before, after = old.get(counter), value.get(counter)
            if (isinstance(before, (int, float))
                    and isinstance(after, (int, float)) and after >= before):
                sample[rate] = round((after - before) * 8 / elapsed, 3)
        if len(sample) > 1:
            history = [item for item in old.get("samples", [])
                       if isinstance(item, dict) and item.get("time")]
            value["samples"] = (history + [sample])[-120:]
    return record


def _publish_wan_rates(points, record):
    samples = {}
    for value in record.get("extensions", {}).get("wan_interfaces", []):
        history = value.get("samples") or []
        if history:
            samples[value.get("interface_name")] = history[-1]
    for point in points:
        if point.get("measurement") not in {"interface", "network_interface"}:
            continue
        sample = samples.get((point.get("tags") or {}).get("interface_name"))
        if sample:
            for field in ("rx_bps", "tx_bps"):
                if sample.get(field) is not None:
                    point["fields"][field] = sample[field]
    return points


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
    # The read-only FortiOS HTTPS API has no edge-local dependency. It may run
    # centrally when the management endpoint is reachable, or at an edge.
    execution = "either"

    def __init__(self, config, inventory_path="/app/runtime/inventory/devices.json", *, client=None, writer=None):
        settings = config.get("collectors", {}).get("fortigate", config)
        configured_wan = settings.get("wan_interfaces") or []
        if not isinstance(configured_wan, list):
            raise ValueError("collectors.fortigate.wan_interfaces must be a list")
        wan = []
        for index, value in enumerate(configured_wan):
            if not isinstance(value, dict):
                raise ValueError(
                    f"FortiGate WAN interface entry {index + 1} must be a mapping")
            name = str(value.get("name") or "").strip()
            role = str(value.get("role") or "").strip().casefold()
            display_name = str(value.get("display_name") or name).strip()
            if not name or role not in WAN_ROLES or not display_name:
                raise ValueError(
                    f"FortiGate WAN interface entry {index + 1} requires name, "
                    f"display_name, and role in {sorted(WAN_ROLES)}")
            wan.append(WanInterface(name, role, display_name))
        names = [value.name for value in wan]
        if len(names) != len(set(names)):
            raise ValueError("FortiGate wan_interfaces contains duplicate interface names")
        if wan and sum(value.role == "primary" for value in wan) != 1:
            raise ValueError("FortiGate wan_interfaces requires exactly one primary interface")
        self.settings = FortiGateConfig(
            base_url=settings.get("host", ""), api_token=settings.get("api_token", ""),
            customer=settings.get("customer") or config.get("customer", "unknown"),
            site=settings.get("site") or config.get("site", "unknown"),
            verify_tls=parse_bool_default(settings.get("verify_tls"), True),
            ca_bundle=(
                str(settings.get("ca_bundle") or "").strip()
                or deployment_ca_bundle(config)),
            timeout_seconds=float(settings.get("timeout_seconds", 20)),
            discovery_interval_seconds=parse_int(
                settings.get("discovery_interval_seconds", 21600), minimum=1),
            collection_interval_seconds=parse_int(
                settings.get("collection_interval_seconds", 60), minimum=1),
            max_retries=parse_int(
                settings.get("max_retries", 2), minimum=0, maximum=10),
            wan_interfaces=tuple(wan))
        if not self.settings.base_url or not self.settings.api_token:
            raise ValueError("FORTIGATE_HOST and FORTIGATE_API_TOKEN are required")
        self.discovery_interval = self.settings.discovery_interval_seconds
        self.collection_interval = self.settings.collection_interval_seconds
        self.inventory = InventoryManager(inventory_path, config.get("inventory"))
        self.client = client or FortiGateClient(self.settings.base_url, self.settings.api_token,
            self.settings.timeout_seconds, verify_tls=self.settings.verify_tls,
            ca_bundle=self.settings.ca_bundle,
            max_retries=self.settings.max_retries)
        self.writer = writer or InfluxWriter.from_config(config)
        self._wan_baseline = None

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
            record, _ = normalize({"system": system}, self.settings, _utcnow())
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
            observed_at = _utcnow()
            record, points = normalize(endpoints, self.settings, observed_at)
            previous = self._wan_baseline
            if previous is None:
                assets = self.inventory.engine.load().get("assets", [])
                previous = next((value for value in assets
                                 if value.get("id") == record["id"]
                                 or value.get("source_record_id") == record["id"]), None)
            _add_wan_rate_samples(record, previous)
            _publish_wan_rates(points, record)
            points.extend(_legacy_points(record, points))
            written = await asyncio.to_thread(self.writer.write, points)
            self.inventory.update_source([record], "fortigate",
                self.settings.customer, self.settings.site, observed_at)
            self._wan_baseline = copy.deepcopy(record)
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
        record, points = normalize(endpoints, self.settings, _utcnow())
        raw_interfaces = payload(endpoints.get("interfaces"))
        if isinstance(raw_interfaces, dict):
            raw_interfaces = [({"name": name, **value}
                if isinstance(value, dict) else {"name": name})
                for name, value in raw_interfaces.items()]
        raw_by_name = {str(pick(value, "name", "interface_name", "interface")): value
                       for value in raw_interfaces or []
                       if isinstance(value, dict)}
        interfaces = [{
            "interface_name": point["tags"].get("interface_name"),
            "alias": point["tags"].get("interface_description", ""),
            "role": point["tags"].get("interface_role", ""),
            "operational_status": point["fields"].get("operational_status"),
            "speed": point["fields"].get("speed_bps"),
            "sdwan_member": parse_bool_default(pick(raw_by_name.get(
                point["tags"].get("interface_name"), {}),
                "sdwan_member", "sd_wan_member", default=False), False),
            "ip_address": str(pick(raw_by_name.get(
                point["tags"].get("interface_name"), {}),
                "ip_address", "ip", default="")),
            "zone": str(pick(raw_by_name.get(
                point["tags"].get("interface_name"), {}),
                "zone", default="")),
            "default_route": parse_bool_default(pick(raw_by_name.get(
                point["tags"].get("interface_name"), {}),
                "default_route", default=False), False),
        } for point in points if point.get("measurement") == "network_interface"]
        return {"enabled": True, "api_hostname": urlsplit(self.client.base_url).hostname or "",
            "organization_id": "not-applicable", "site_count": 1, "device_count": 1,
            "device_types": {"fortigate": 1}, "measurements": {}, "points_produced": len(points),
            "always_empty_fields": [], "high_cardinality_warnings": [],
            "influx_write_completed": False, "points_written": 0,
            "partial": bool(diagnostics), "diagnostics": [item.category for item in diagnostics],
            "device_id": record["id"], "interface_count": len(interfaces),
            "interfaces": interfaces,
            "wan_configuration": {
                "configured": bool(self.settings.wan_interfaces),
                "mappings": [{"name": value.name, "role": value.role,
                              "display_name": value.display_name}
                             for value in self.settings.wan_interfaces],
                "missing": record.get("extensions", {}).get(
                    "wan_validation", {}).get("missing", []),
            }}

    async def close(self):
        await self.client.close()
