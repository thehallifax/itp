"""Versioned, deterministic collector capability manifests."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .writer import atomic_write

SCHEMA_VERSION = 1
SUPPORT_STATES = {"supported", "unsupported", "conditional", "unknown"}
COLLECTION_STATES = {
    "collected", "not_yet_collected", "disabled", "unavailable", "failed",
    "partial", "not_applicable",
}


@dataclass(frozen=True)
class Capability:
    id: str
    label: str
    support: str
    measurements: tuple[str, ...] = ()
    fields: tuple[str, ...] = ()
    services: tuple[str, ...] = ()
    panels: tuple[str, ...] = ()
    condition: str = ""
    reason: str = ""
    phase: str = "collection"
    health_impact: bool = True

    def __post_init__(self):
        if self.support not in SUPPORT_STATES:
            raise ValueError(f"invalid support state for {self.id}: {self.support}")
        if self.support == "conditional" and not self.condition:
            raise ValueError(f"conditional capability {self.id} requires a condition")
        if self.support == "unsupported" and not self.reason:
            raise ValueError(f"unsupported capability {self.id} requires a reason")


def _cap(identifier, label, support="supported", **kwargs):
    return Capability(identifier, label, support, **kwargs)


from .aruba.capabilities import CAPABILITIES as ARUBA_CAPABILITIES


MANIFESTS = {
    "aruba": ARUBA_CAPABILITIES,
    "paloalto": (
        _cap("device_inventory", "Device inventory", measurements=("device",),
             services=("Security",), panels=("Overview", "Inventory")),
        _cap("availability", "Device availability", measurements=("availability",),
             services=("Security",), panels=("Overall Health",)),
        _cap("firewall_health", "Firewall health", measurements=("firewall",),
             services=("Security",), panels=("Firewall Health",)),
        _cap("ha_status", "HA status", measurements=("firewall",),
             fields=("ha_state",), services=("Security",), panels=("HA Status",)),
        _cap("performance", "Resource performance",
             measurements=("performance",),
             fields=("management_cpu_percent", "dataplane_cpu_percent",
                     "memory_used_percent"), panels=("Resources",)),
        _cap("active_sessions", "Active sessions",
             measurements=("performance",),
             fields=("active_sessions", "max_sessions", "session_utilisation_percent"),
             panels=("Sessions",)),
        _cap("interfaces", "Interface inventory",
             measurements=("interface",), services=("Internet",),
             panels=("Interfaces",)),
        _cap("wan_classification", "Authoritative WAN classification", "conditional",
             measurements=("interface",), fields=("is_wan",),
             services=("Internet",), panels=("WAN",),
             condition="At least one interface is authoritatively classified as WAN."),
        _cap("wan_throughput", "WAN throughput", "conditional",
             measurements=("interface",), fields=("rx_bps", "tx_bps"),
             services=("Internet",), panels=("WAN Throughput",),
             condition="A classified WAN interface exposes traffic counters."),
        _cap("interface_fault_counters", "Interface fault counters",
             measurements=("interface",), fields=("rx_errors", "tx_errors"),
             panels=("Interface Errors",)),
        _cap("subscriptions", "Subscriptions", measurements=("license",),
             services=("Security",), panels=("Licensing",)),
        _cap("content_application", "Application content version",
             measurements=("content_package",), services=("Security",),
             panels=("Content Updates",)),
        _cap("content_threat", "Threat content version",
             measurements=("content_package",), services=("Security",),
             panels=("Content Updates",)),
        _cap("content_antivirus", "Antivirus content version",
             measurements=("content_package",), services=("Security",),
             panels=("Content Updates",)),
        _cap("content_wildfire", "WildFire content version",
             measurements=("content_package",), services=("Security",),
             panels=("Content Updates",)),
        _cap("content_url_filtering", "URL filtering content version",
             measurements=("content_package",), services=("Security",),
             panels=("Content Updates",)),
        _cap("device_certificate_status", "Device certificate status",
             measurements=("firewall",), fields=("device_certificate_status",),
             services=("Security",), panels=("Device Certificate",)),
        _cap("collector_diagnostics", "Collector diagnostics",
             measurements=("collector_health",), services=("Monitoring",),
             panels=("Collector Diagnostics",)),
        _cap("certificate_expiry", "Certificate expiry", "unsupported",
             services=("Security",), panels=("Certificate Expiry",),
             reason="The current read-only PAN-OS collection does not retrieve certificate expiry."),
        _cap("configuration_commits", "Configuration commits", "unsupported",
             panels=("Recent Configuration Commits",),
             reason="The current collector does not retrieve configuration audit history."),
    ),
    "papercut": (
        _cap("server_inventory", "Application server inventory",
             measurements=("device",), services=("Printing",), panels=("Application",)),
        _cap("server_availability", "Application availability",
             measurements=("availability",), services=("Printing",),
             panels=("Overall Health",)),
        _cap("server_performance", "Application performance",
             measurements=("performance",),
             fields=("cpu_percent", "jvm_memory_used_percent", "disk_used_percent"),
             services=("Printing",), panels=("Application",)),
        _cap("database_health", "Database health", measurements=("performance",),
             fields=("status", "query_latency_ms", "connection_latency_ms"),
             services=("Printing",), panels=("Database",)),
        _cap("printer_inventory", "Printer inventory", "conditional",
             measurements=("performance",), fields=("printer_count",),
             services=("Printing",), panels=("Printers",),
             condition="The API returns an aggregate printer count; individual printer records are not exposed."),
        _cap("printer_status", "Embedded printer/device status", "conditional",
             measurements=("device",), services=("Printing",), panels=("Devices",),
             condition="The System Health API returns embedded-device records."),
        _cap("held_jobs", "Held jobs", measurements=("performance",),
             fields=("held_jobs",), services=("Printing",), panels=("Printers",)),
        _cap("print_services", "Print service availability",
             measurements=("availability",), services=("Printing",),
             panels=("Services",)),
        _cap("license_status", "Licence status",
             measurements=("license",), services=("Printing",), panels=("Licensing",)),
        _cap("upgrade_assurance", "Upgrade Assurance",
             measurements=("license",),
             fields=("upgrade_assurance_remaining_days",),
             services=("Printing",), panels=("Licensing",)),
        _cap("printer_consumables", "Printer consumables", "unsupported",
             services=("Printing",), panels=("Consumables",),
             reason="PaperCut System Health does not expose toner or consumable levels."),
        _cap("collector_diagnostics", "Collector diagnostics",
             measurements=("collector_health",), services=("Monitoring",),
             panels=("Collector Diagnostics",)),
    ),
    "snmp": (
        _cap("discovery", "SNMP discovery", phase="discovery",
             measurements=("device",), services=("Switching",)),
        _cap("device_polling", "SNMP device polling", "conditional",
             measurements=("device", "availability"),
             condition="At least one responsive SNMP target is discovered.",
             services=("Switching",)),
        _cap("interface_metrics", "Interface metrics", "conditional",
             measurements=("interface",),
             condition="A responsive target exposes standard interface MIB data.",
             services=("Switching",)),
        _cap("printer_metrics", "Printer metrics", "conditional",
             condition="A responsive printer exposes supported Printer-MIB data.",
             services=("Printing",)),
        _cap("wireless_metrics", "Wireless metrics", "conditional",
             condition="A responsive wireless device exposes a supported vendor MIB.",
             services=("Wireless",)),
        _cap("collector_diagnostics", "Collector diagnostics",
             measurements=("collector_health",), services=("Monitoring",)),
    ),
    "framework": (
        _cap("scheduler_health", "Scheduler health", services=("Monitoring",)),
        _cap("collector_diagnostics", "Collector diagnostics",
             measurements=("collector_health",), services=("Monitoring",)),
    ),
}


def validate_manifest(payload):
    """Reject unknown schemas and controlled-vocabulary violations."""
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"unsupported capability schema version: {payload.get('schema_version')}")
    for collector in payload.get("collectors", {}).values():
        for capability in collector.get("capabilities", []):
            if capability.get("support") not in SUPPORT_STATES:
                raise ValueError("invalid capability support state")
            if capability.get("collection") not in COLLECTION_STATES:
                raise ValueError("invalid capability collection state")
            if capability["support"] == "unsupported" and \
                    capability["collection"] != "not_applicable":
                raise ValueError("unsupported capability must be not_applicable")
    return payload


def _read(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


class CapabilityManifestEngine:
    """Fuse declarations with scheduler and inventory evidence."""

    def __init__(self, config, runtime_dir):
        self.config = config
        self.runtime_dir = Path(runtime_dir)

    def _enabled(self, name):
        if name == "framework":
            return any(self._enabled(value) for value in MANIFESTS if value != "framework")
        return bool(self.config.get("collectors", {}).get(name, {}).get("enabled", False))

    def _runtime(self, name):
        scheduler = _read(self.runtime_dir / "scheduler/state.json", {})
        if name == "framework":
            lifecycle = scheduler.get("lifecycle_state")
            return ({
                "last_collection_outcome": (
                    "success" if lifecycle == "ready"
                    else "failed" if lifecycle == "degraded" else None),
                "last_collection_attempt": scheduler.get("updated_at"),
                "last_successful_collection": scheduler.get("ready_at"),
                "last_error_class": scheduler.get("last_error_class"),
            }, {})
        source_runs = _read(self.runtime_dir / "inventory/source_runs.json", {})
        connector = scheduler.get("connectors", {}).get(name, {})
        source = source_runs.get("sources", {}).get(name, {})
        last_run = source.get("last_run", {})
        return connector, last_run

    def _collection_state(self, declaration, enabled, connector, source):
        if declaration.support == "unsupported":
            return "not_applicable", declaration.reason
        if not enabled:
            return "disabled", "Collector is not enabled."
        runtime_capabilities = (
            connector.get("last_collection_result") or {}).get(
                "capability_states", {})
        explicit = runtime_capabilities.get(declaration.id)
        if explicit in COLLECTION_STATES:
            explanations = {
                "collected": "Authoritative capability evidence was collected.",
                "unavailable": declaration.condition or
                    "The capability is currently unavailable.",
                "failed": "The latest capability collection failed.",
                "partial": "Capability evidence is incomplete.",
            }
            return explicit, explanations.get(
                explicit, "Runtime capability state is explicit.")
        phase = declaration.phase
        outcome = connector.get(f"last_{phase}_outcome")
        if not outcome and source:
            outcome = "success" if source.get("success") else "failed"
        if not outcome or outcome in {"never_run", "not_run"}:
            return "not_yet_collected", "No completed run is recorded."
        if outcome == "failed":
            return "failed", "The latest collector run failed."
        if source.get("partial"):
            return "partial", "The latest collector run completed partially."
        if declaration.support == "conditional" and int(source.get("records_returned") or 0) == 0:
            return "unavailable", declaration.condition
        if outcome == "success":
            return "collected", "Current runtime evidence is available."
        return "unavailable", "Current runtime evidence is unavailable."

    def build(self):
        collectors = {}
        for name in sorted(MANIFESTS):
            enabled = self._enabled(name)
            connector, source = self._runtime(name)
            items = []
            for declaration in sorted(MANIFESTS[name], key=lambda value: value.id):
                state, explanation = self._collection_state(
                    declaration, enabled, connector, source)
                value = asdict(declaration)
                value.update({
                    "measurements": list(declaration.measurements),
                    "fields": list(declaration.fields),
                    "services": list(declaration.services),
                    "panels": list(declaration.panels),
                    "collection": state,
                    "configured": enabled,
                    "available": state in {"collected", "partial"},
                    "collectable": (
                        enabled and declaration.support in {
                            "supported", "conditional"}
                        and state not in {
                            "disabled", "unavailable", "failed",
                            "not_applicable"}),
                    "resource_count": (
                        (connector.get("last_collection_result") or {})
                        .get("capability_resources", {})
                        .get(declaration.id)
                    ),
                    "explanation": explanation,
                })
                items.append(value)
            counts = {state: sum(item["collection"] == state for item in items)
                      for state in sorted(COLLECTION_STATES)}
            collectors[name] = {
                "schema_version": SCHEMA_VERSION,
                "collector": {
                    "id": name,
                    "display_name": {
                        "aruba": "HPE Aruba Networking Central",
                        "paloalto": "Palo Alto Networks",
                        "papercut": "PaperCut MF",
                        "snmp": "SNMP",
                        "framework": "ITP Collector Framework",
                    }[name],
                    "version": "1.0.0",
                },
                "identity": {
                    "deployment_id": str(self.config.get("deployment_id") or ""),
                    "customer_id": str(self.config.get("customer_id") or
                                       self.config.get("customer") or ""),
                    "site_id": str(self.config.get("site_id") or
                                   self.config.get("site") or ""),
                },
                "execution": {
                    "configured": name == "framework" or name in
                                  self.config.get("collectors", {}),
                    "enabled": enabled,
                    "mode": str(self.config.get("collectors", {}).get(
                        name, {}).get("execution") or
                        self.config.get("runtime_mode") or "central"),
                    "state": (
                    "disabled" if not enabled else
                    "failed" if any(item["collection"] == "failed" for item in items) else
                    "partial" if any(
                        item["health_impact"]
                        and item["collection"] in {"partial", "unavailable"}
                        for item in items) else
                    "not_yet_collected" if any(item["collection"] == "not_yet_collected"
                                               for item in items) else "collected"),
                },
                "last_discovery": {
                    "status": connector.get("last_discovery_outcome", "not_run"),
                    "observed_at": connector.get("last_discovery_attempt"),
                    "duration_ms": connector.get("last_discovery_duration_ms"),
                },
                "last_collection": {
                    "status": connector.get("last_collection_outcome") or (
                        "success" if source.get("success") is True
                        else "failed" if source.get("success") is False
                        else "not_run"),
                    "observed_at": connector.get("last_collection_attempt")
                                   or source.get("completed_at"),
                    "last_success": connector.get("last_successful_collection")
                                    or (source.get("completed_at")
                                        if source.get("success") is True else None),
                    "duration_ms": connector.get("last_collection_duration_ms"),
                    "points_written": (connector.get(
                        "last_collection_result") or {}).get("points_written"),
                    "safe_error_class": (
                        str(connector.get("last_error_class") or "")[:80] or None),
                },
                "capability_counts": counts,
                "capabilities": items,
            }
        health = []
        for name, value in sorted(collectors.items()):
            counts = value["capability_counts"]
            health.append({
                "collector": name,
                "collector_state": value["execution"]["state"],
                "discovery_state": value["last_discovery"]["status"],
                "collection_state": value["last_collection"]["status"],
                "capabilities_supported": sum(
                    item["support"] in {"supported", "conditional"}
                    for item in value["capabilities"]),
                "capabilities_collected": counts["collected"],
                "capabilities_failed": counts["failed"],
                "capabilities_partial": counts["partial"],
                "capabilities_unavailable": counts["unavailable"],
                "capabilities_not_applicable": counts["not_applicable"],
                "last_success": value["last_collection"]["last_success"],
                "last_failure": (
                    value["last_collection"]["observed_at"]
                    if value["last_collection"]["status"] == "failed" else None),
                "safe_error_class": value["last_collection"]["safe_error_class"],
            })
        return {
            "schema_version": SCHEMA_VERSION,
            "profile": str(self.config.get("deployment_id") or
                           self.config.get("deployment", {}).get("id") or
                           self.config.get("profile") or ""),
            "collectors": collectors,
            "collector_health": health,
        }

    def generate(self):
        payload = validate_manifest(self.build())
        output = self.runtime_dir / "capabilities"
        output.mkdir(parents=True, exist_ok=True)
        for name, value in payload["collectors"].items():
            if name == "framework":
                continue
            atomic_write(output / f"{name}.json",
                         json.dumps(value, indent=2, sort_keys=True) + "\n")
        atomic_write(output / "collectors.json",
                     json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return payload
