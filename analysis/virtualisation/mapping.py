"""Map sanitized provider contracts into canonical dictionaries."""
from __future__ import annotations

from datetime import datetime, timezone

from .models import canonical_id
from .normalise import percentage, power_state


AGENTS = {
    "vmware": "VMware Tools",
    "hyperv": "Hyper-V Integration Services",
    "proxmox": "QEMU Guest Agent",
}


def _base(provider, endpoint, deployment_id, site_id, kind, native, observed_at):
    source_id = str(native["id"])
    return {
        "kind": kind, "deployment_id": deployment_id, "site_id": site_id,
        "provider": provider, "source_id": source_id,
        "canonical_id": canonical_id(provider, kind, endpoint, source_id),
        "name": str(native.get("name") or source_id),
        "display_name": str(native.get("name") or source_id),
        "source_endpoint": endpoint, "collected_at": observed_at,
        "observed_at": str(native.get("observed_at") or observed_at),
        "confidence": str(native.get("confidence") or "high"),
        "evidence": {"native_state": native.get("state"),
                     "native": native.get("metadata", {})},
        "tags": sorted(set(native.get("tags") or [])),
    }


def map_contract(contract, endpoint, deployment_id, site_id, observed_at=None):
    provider = str(contract["provider"])
    observed_at = observed_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    objects = []
    manager = contract.get("manager")
    if manager:
        value = _base(provider, endpoint, deployment_id, site_id, "manager", manager, observed_at)
        value.update({"version": manager.get("version"), "build": manager.get("build"),
                      "reachable": manager.get("reachable")})
        objects.append(value)
    for native in contract.get("clusters", []):
        value = _base(provider, endpoint, deployment_id, site_id, "cluster", native, observed_at)
        value.update({key: native.get(key) for key in (
            "manager_id", "total_host_count", "enabled_host_count",
            "degraded_host_count", "maintenance_host_count", "ha_status",
            "admission_control_status", "failover_capacity", "cpu_total_mhz",
            "cpu_used_mhz", "memory_total_bytes", "memory_used_bytes", "health")})
        objects.append(value)
    for native in contract.get("hosts", []):
        value = _base(provider, endpoint, deployment_id, site_id, "host", native, observed_at)
        value.update({key: native.get(key) for key in (
            "manager_id", "cluster_id", "fqdn", "management_address",
            "platform_version", "build", "hardware_manufacturer", "hardware_model",
            "serial_number", "connection_state", "maintenance", "uptime_seconds",
            "cpu_model", "physical_sockets", "core_count", "logical_processor_count",
            "cpu_total_mhz", "cpu_used_mhz", "memory_total_bytes",
            "memory_used_bytes", "vm_count", "running_vm_count",
            "ha_participation", "health")})
        objects.append(value)
    snapshots_by_workload = {}
    now = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    mapped_snapshots = []
    for native in contract.get("snapshots", []):
        value = _base(provider, endpoint, deployment_id, site_id, "snapshot", native, observed_at)
        created = native.get("created_at")
        age = native.get("age_days")
        if age is None and created:
            try:
                age = max(0, (now - datetime.fromisoformat(
                    str(created).replace("Z", "+00:00"))).days)
            except ValueError:
                age = None
        value.update({"workload_id": native.get("workload_id"),
            "snapshot_type": native.get("snapshot_type", "snapshot"),
            "created_at": created, "age_days": age,
            "description": native.get("description"), "parent_id": native.get("parent_id"),
            "accessible": native.get("accessible", True)})
        snapshots_by_workload.setdefault(str(native.get("workload_id")), []).append(value)
        mapped_snapshots.append(value)
    for collection, kind in ((contract.get("vms", []), "vm"),
                             (contract.get("containers", []), "container")):
        for native in collection:
            value = _base(provider, endpoint, deployment_id, site_id, kind, native, observed_at)
            snapshots = snapshots_by_workload.get(str(native["id"]), [])
            ages = [item["age_days"] for item in snapshots if item["age_days"] is not None]
            value.update({key: native.get(key) for key in (
                "manager_id", "cluster_id", "host_id", "guest_hostname",
                "guest_operating_system", "vcpu", "memory_bytes",
                "provisioned_storage_bytes", "consumed_storage_bytes",
                "creation_time", "uptime_seconds", "replication_state",
                "ha_protected", "template")})
            value.update({"power_state": power_state(native.get("state")),
                "guest_agent_type": AGENTS[provider],
                "guest_agent_state": native.get("guest_agent_state"),
                "ip_addresses": list(native.get("ip_addresses") or []),
                "mac_addresses": list(native.get("mac_addresses") or []),
                "network_ids": list(native.get("network_ids") or []),
                "storage_ids": list(native.get("storage_ids") or []),
                "snapshot_count": len(snapshots),
                "oldest_snapshot_age_days": max(ages) if ages else None})
            objects.append(value)
    for native in contract.get("storage", []):
        value = _base(provider, endpoint, deployment_id, site_id, "storage", native, observed_at)
        used, capacity = native.get("used_bytes"), native.get("capacity_bytes")
        value.update({key: native.get(key) for key in (
            "manager_id", "cluster_id", "host_id", "storage_type", "scope",
            "capacity_bytes", "used_bytes", "free_bytes", "accessible",
            "shared", "thin_provisioned", "alarm_state")})
        value["utilisation_percent"] = native.get(
            "utilisation_percent", percentage(used, capacity))
        objects.append(value)
    for native in contract.get("networks", []):
        value = _base(provider, endpoint, deployment_id, site_id, "network", native, observed_at)
        value.update({key: native.get(key) for key in (
            "manager_id", "cluster_id", "host_id", "network_type", "vlan_id",
            "connected_workload_count", "uplink_evidence")})
        objects.append(value)
    objects.extend(mapped_snapshots)
    return sorted(objects, key=lambda value: (value["kind"], value["canonical_id"]))
