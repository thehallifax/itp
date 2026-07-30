"""Read-only Aruba Central inventory collector."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from collectors.base import BaseCollector
from collectors.configuration import parse_bool_default, parse_int
from collectors.inventory import InventoryManager
from collectors.registry import CollectorRegistry
from collectors.writer import InfluxWriter
from collectors.tls import deployment_ca_bundle

from .client import ArubaCentralClient, ArubaOAuthTokenManager
from .models import ArubaCentralConfig
from .normalizer import _items, normalize

LOG = logging.getLogger("collector.aruba")


def _utcnow():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


DEVICE_CLASSES = {
    "access_points": "access_point_inventory",
    "switches": "switch_inventory",
    "gateways": "gateway_inventory",
}


def _class_items(snapshot, device_class):
    payload = (snapshot.get("device_classes") or {}).get(device_class)
    return _items(
        payload,
        ("devices", "items", "data", "aps", "switches", "gateways"),
    )


def _capability_projection(snapshot, records):
    diagnostics = snapshot.get("diagnostics") or {}
    capability_states = {
        "account": "collected",
        "groups": "collected",
        "sites": "collected",
        "inventory": "collected",
        "device_health": "collected",
        "firmware": "collected",
        "alerts": (
            "collected"
            if diagnostics.get("alerts", {}).get("state") == "collected"
            else "unavailable"
        ),
        "client_counts": (
            "collected"
            if any(
                (value.get("extensions") or {})
                .get("aruba_central", {})
                .get("client_count") is not None
                for value in records
            )
            else "unavailable"
        ),
        "collector_diagnostics": "collected",
    }
    capability_resources = {
        "inventory": len(records),
        "device_health": len(records),
        "firmware": sum(bool(value.get("firmware_version")) for value in records),
        "alerts": len(_items(snapshot.get("alerts"), ("alerts", "items", "data"))),
    }
    endpoint_states = {}
    for device_class, capability in DEVICE_CLASSES.items():
        items = _class_items(snapshot, device_class)
        diagnostic = diagnostics.get(device_class) or {"state": "collected"}
        state = str(diagnostic.get("state") or "collected")
        capability_states[capability] = (
            "collected" if state == "collected" else "unavailable"
        )
        capability_resources[capability] = len(items)
        endpoint_states[device_class] = {
            "state": state,
            "resource_count": len(items),
        }
    return capability_states, capability_resources, endpoint_states


def validate_settings(config):
    raw = config.get("collectors", {}).get("aruba", config)
    base_url = str(raw.get("base_url") or "").strip()
    auth_mode = str(raw.get("auth_mode") or "refresh_token").strip()
    token_url = str(raw.get("token_url") or (
        base_url.rstrip("/") + "/oauth2/token"
        if auth_mode == "refresh_token"
        else "https://sso.common.cloud.hpe.com/as/token.oauth2")).strip()
    client_id = str(raw.get("client_id") or "").strip()
    client_secret = str(raw.get("client_secret") or "")
    refresh_token = str(raw.get("refresh_token") or "")
    access_token = str(raw.get("access_token") or "")
    site = str(raw.get("site") or config.get("site") or "").strip()
    if not base_url:
        raise ValueError("collectors.aruba.base_url is required")
    ArubaCentralClient.normalize_url(base_url)
    ArubaCentralClient.normalize_url(token_url)
    if not client_id or not client_secret:
        raise ValueError(
            "Aruba Central client_id and client_secret are required")
    if auth_mode not in {"client_credentials", "refresh_token"}:
        raise ValueError(
            "Aruba Central auth_mode must be client_credentials or refresh_token")
    if auth_mode == "refresh_token" and not refresh_token:
        raise ValueError(
            "Aruba Central refresh_token is required for refresh_token authentication")
    if not site:
        raise ValueError(
            "collectors.aruba.site must reference a canonical site")
    timeout = float(raw.get("timeout_seconds", 20))
    if timeout <= 0 or timeout > 120:
        raise ValueError("Aruba Central timeout_seconds must be between 1 and 120")
    endpoint_config = raw.get("endpoints") or {}
    if not isinstance(endpoint_config, dict):
        raise TypeError("collectors.aruba.endpoints must be a mapping")
    allowed_endpoints = {
        "groups", "sites", "access_points", "switches", "gateways", "alerts",
    }
    unknown_endpoints = sorted(set(endpoint_config) - allowed_endpoints)
    if unknown_endpoints:
        raise ValueError(
            "Unknown Aruba Central endpoints: " + ", ".join(unknown_endpoints)
        )
    if endpoint_config.get("access_points") == "":
        raise ValueError("Aruba Central access_points endpoint cannot be disabled")
    return ArubaCentralConfig(
        base_url=base_url, token_url=token_url, client_id=client_id,
        client_secret=client_secret, refresh_token=refresh_token,
        access_token=access_token, auth_mode=auth_mode,
        account_id=str(raw.get("account_id") or "").strip(),
        customer=str(raw.get("customer_id") or raw.get("customer") or
                     config.get("customer_id") or config.get("customer") or ""),
        site=site,
        deployment_id=str(config.get("deployment_id") or ""),
        customer_name=str(raw.get("customer_name") or
                          (config.get("identity") or {}).get("customer_name") or ""),
        site_name=str(raw.get("site_name") or config.get("site_name") or ""),
        verify_tls=parse_bool_default(raw.get("verify_tls"), True),
        ca_bundle=(
            str(raw.get("ca_bundle") or "").strip()
            or deployment_ca_bundle(config)),
        timeout_seconds=timeout,
        discovery_interval_seconds=parse_int(
            raw.get("discovery_interval_seconds", 21600), minimum=1),
        collection_interval_seconds=parse_int(
            raw.get("collection_interval_seconds", 120), minimum=1),
        max_retries=parse_int(
            raw.get("max_retries", 2), minimum=0, maximum=5),
        endpoints={
            str(key): str(value)
            for key, value in sorted(endpoint_config.items())
        },
    )


@CollectorRegistry.register
class ArubaCentralCollector(BaseCollector):
    name = "aruba"
    execution = "central"

    def __init__(self, config,
                 inventory_path="/app/runtime/inventory/devices.json", *,
                 client=None, writer=None, token_manager=None):
        self.settings = validate_settings(config)
        self.discovery_interval = self.settings.discovery_interval_seconds
        self.collection_interval = self.settings.collection_interval_seconds
        self.inventory = InventoryManager(
            inventory_path, config.get("inventory"))
        if client is not None:
            self.client = client
        else:
            manager = token_manager or ArubaOAuthTokenManager(
                self.settings.token_url, self.settings.client_id,
                self.settings.client_secret,
                refresh_token=self.settings.refresh_token,
                access_token=self.settings.access_token,
                auth_mode=self.settings.auth_mode,
                timeout=self.settings.timeout_seconds,
                verify_tls=self.settings.verify_tls,
                ca_bundle=self.settings.ca_bundle)
            self.client = ArubaCentralClient(
                self.settings.base_url, manager,
                self.settings.timeout_seconds,
                verify_tls=self.settings.verify_tls,
                ca_bundle=self.settings.ca_bundle,
                max_retries=self.settings.max_retries,
                endpoints=self.settings.endpoints)
        self.writer = writer or InfluxWriter.from_config(config)

    async def _snapshot(self):
        snapshot = await self.client.snapshot()
        records, points = normalize(snapshot, self.settings, _utcnow())
        return snapshot, records, points

    async def discover(self):
        run_id = self.inventory.engine.begin_source_run(
            "aruba", self.name, _utcnow())
        try:
            snapshot, records, _ = await self._snapshot()
            result = self.inventory.update_source(
                records, "aruba", self.settings.customer,
                self.settings.site, _utcnow(), source_run_id=run_id)
            self.inventory.engine.complete_source_run(
                "aruba", run_id, success=True,
                records_returned=len(records), completed_at=_utcnow(),
                error_category=(
                    "no_devices" if not records
                    else "partial" if snapshot.get("partial") else None))
            return result
        except Exception as exc:
            self.inventory.engine.complete_source_run(
                "aruba", run_id, success=False, completed_at=_utcnow(),
                error_category=getattr(exc, "category", type(exc).__name__))
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
            snapshot, records, points = await self._snapshot()
            partial = bool(snapshot.get("partial"))
            category = (
                "no_devices" if not records else
                "partial" if partial else "success")
            written = await asyncio.to_thread(self.writer.write, points)
            success = True
            capability_states, capability_resources, endpoint_states = (
                _capability_projection(snapshot, records)
            )
            return {
                "status": category, "points_written": written,
                "assets_returned": len(records), "partial": partial,
                "capability_states": capability_states,
                "capability_resources": capability_resources,
                "endpoint_states": endpoint_states}
        except Exception as exc:
            category = getattr(exc, "category", type(exc).__name__)
            raise
        finally:
            health = {
                "measurement": "collector_health",
                "tags": {
                    "collector": "aruba",
                    "deployment_id": self.settings.deployment_id,
                    "customer_id": self.settings.customer,
                    "customer": self.settings.customer,
                    "customer_name": self.settings.customer_name,
                    "site_id": self.settings.site,
                    "site": self.settings.site,
                    "site_name": self.settings.site_name,
                    "diagnostic_category": category,
                },
                "fields": {
                    "success": success, "partial": partial,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "api_requests": self.client.api_requests - requests,
                    "retry_count": self.client.retry_count - retries,
                    "error_count": 0 if success and not partial else 1,
                    "devices_returned": len(records),
                    "points_written": written,
                },
            }
            try:
                await asyncio.to_thread(self.writer.write, [health])
            # Health-write failure must never replace the primary collection
            # result or stop the scheduler.
            except Exception:  # noqa: BLE001
                LOG.error("collector=aruba phase=health result=write_failed")

    async def inspect(self):
        snapshot, records, points = await self._snapshot()
        groups = _items(snapshot.get("groups"), ("groups", "items", "data"))
        sites = _items(snapshot.get("sites"), ("sites", "items", "data"))
        capability_states, capability_resources, endpoint_states = (
            _capability_projection(snapshot, records)
        )
        types = {}
        for record in records:
            kind = record["device_type"]
            types[kind] = types.get(kind, 0) + 1
        return {
            "enabled": True,
            "api_base_url": self.settings.base_url,
            "api_hostname": urlsplit(self.settings.base_url).hostname or "",
            "authentication_result": "successful",
            "account_discovery": (
                "configured" if self.settings.account_id else "not_configured"
            ),
            "organization_id": self.settings.account_id or "not-configured",
            "deployment_id": self.settings.deployment_id,
            "customer_id": self.settings.customer,
            "site_id": self.settings.site,
            "account_id": self.settings.account_id,
            "group_count": len(groups), "site_count": len(sites),
            "device_count": len(records), "device_types": dict(sorted(types.items())),
            "access_point_count": capability_resources["access_point_inventory"],
            "switch_count": capability_resources["switch_inventory"],
            "gateway_count": capability_resources["gateway_inventory"],
            "capability_states": capability_states,
            "capability_resources": capability_resources,
            "endpoint_states": endpoint_states,
            "measurements": {}, "points_produced": len(points),
            "always_empty_fields": [],
            "high_cardinality_warnings": [],
            "influx_write_completed": False, "points_written": 0,
            "partial": bool(snapshot.get("partial")),
            "diagnostics": {
                "endpoint_reachable": True,
                "authentication_successful": True,
                "valid_json": True,
                "permissions_sufficient": True,
                "devices_discovered": bool(records),
                "category": (
                    "no_devices" if not records
                    else "partial" if snapshot.get("partial") else "healthy"),
            },
        }

    async def close(self):
        await self.client.close()
