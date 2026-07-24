"""Typed canonical virtualisation records."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


PROVIDERS = frozenset({"vmware", "hyperv", "proxmox"})


class PowerState(str, Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    PAUSED = "paused"
    SUSPENDED = "suspended"
    SAVED = "saved"
    UNKNOWN = "unknown"


class HealthState(str, Enum):
    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


def canonical_id(provider: str, kind: str, source_endpoint: str, source_id: str) -> str:
    if provider not in PROVIDERS:
        raise ValueError(f"unsupported virtualisation provider: {provider}")
    material = json.dumps([provider, kind, source_endpoint.casefold(), str(source_id)],
                          separators=(",", ":"), ensure_ascii=True)
    return f"virt:{kind}:{hashlib.sha256(material.encode()).hexdigest()[:24]}"


@dataclass(frozen=True)
class CanonicalVirtualisationObject:
    deployment_id: str
    site_id: str
    provider: str
    source_id: str
    canonical_id: str
    name: str
    display_name: str
    source_endpoint: str
    collected_at: str
    observed_at: str
    confidence: str = "high"
    evidence: dict[str, Any] = field(default_factory=dict)
    tags: tuple[str, ...] = ()

    def __post_init__(self):
        if self.provider not in PROVIDERS:
            raise ValueError(f"unsupported virtualisation provider: {self.provider}")
        if not self.source_id or not self.canonical_id:
            raise ValueError("source_id and canonical_id are required")

    def to_dict(self):
        value = asdict(self)
        value["tags"] = list(self.tags)
        return value


@dataclass(frozen=True)
class VirtualisationPlatform(CanonicalVirtualisationObject):
    version: str | None = None
    build: str | None = None
    status: str = "unknown"


@dataclass(frozen=True)
class VirtualisationManager(CanonicalVirtualisationObject):
    version: str | None = None
    build: str | None = None
    reachable: bool | None = None


@dataclass(frozen=True)
class VirtualisationCluster(CanonicalVirtualisationObject):
    manager_id: str | None = None
    total_host_count: int | None = None
    enabled_host_count: int | None = None
    degraded_host_count: int | None = None
    maintenance_host_count: int | None = None
    ha_status: str | None = None
    admission_control_status: str | None = None
    failover_capacity: float | None = None
    cpu_total_mhz: float | None = None
    cpu_used_mhz: float | None = None
    memory_total_bytes: int | None = None
    memory_used_bytes: int | None = None
    health: str = "unknown"


@dataclass(frozen=True)
class VirtualisationHost(CanonicalVirtualisationObject):
    manager_id: str | None = None
    cluster_id: str | None = None
    fqdn: str | None = None
    management_address: str | None = None
    platform_version: str | None = None
    build: str | None = None
    hardware_manufacturer: str | None = None
    hardware_model: str | None = None
    serial_number: str | None = None
    connection_state: str = "unknown"
    maintenance: bool | None = None
    uptime_seconds: int | None = None
    cpu_model: str | None = None
    physical_sockets: int | None = None
    core_count: int | None = None
    logical_processor_count: int | None = None
    cpu_total_mhz: float | None = None
    cpu_used_mhz: float | None = None
    memory_total_bytes: int | None = None
    memory_used_bytes: int | None = None
    vm_count: int | None = None
    running_vm_count: int | None = None
    ha_participation: bool | None = None
    health: str = "unknown"


@dataclass(frozen=True)
class VirtualMachine(CanonicalVirtualisationObject):
    manager_id: str | None = None
    cluster_id: str | None = None
    host_id: str | None = None
    guest_hostname: str | None = None
    guest_operating_system: str | None = None
    vcpu: int | None = None
    memory_bytes: int | None = None
    provisioned_storage_bytes: int | None = None
    consumed_storage_bytes: int | None = None
    power_state: str = "unknown"
    guest_agent_type: str | None = None
    guest_agent_state: str | None = None
    ip_addresses: tuple[str, ...] = ()
    mac_addresses: tuple[str, ...] = ()
    network_ids: tuple[str, ...] = ()
    storage_ids: tuple[str, ...] = ()
    creation_time: str | None = None
    uptime_seconds: int | None = None
    snapshot_count: int = 0
    oldest_snapshot_age_days: int | None = None
    replication_state: str | None = None
    ha_protected: bool | None = None


@dataclass(frozen=True)
class VirtualContainer(VirtualMachine):
    template: bool | None = None


@dataclass(frozen=True)
class VirtualStorage(CanonicalVirtualisationObject):
    manager_id: str | None = None
    cluster_id: str | None = None
    host_id: str | None = None
    storage_type: str | None = None
    scope: str | None = None
    capacity_bytes: int | None = None
    used_bytes: int | None = None
    free_bytes: int | None = None
    utilisation_percent: float | None = None
    accessible: bool | None = None
    shared: bool | None = None
    thin_provisioned: bool | None = None
    alarm_state: str | None = None


@dataclass(frozen=True)
class VirtualNetwork(CanonicalVirtualisationObject):
    manager_id: str | None = None
    cluster_id: str | None = None
    host_id: str | None = None
    network_type: str | None = None
    vlan_id: int | None = None
    connected_workload_count: int | None = None
    uplink_evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class VirtualSnapshot(CanonicalVirtualisationObject):
    workload_id: str = ""
    snapshot_type: str = "snapshot"
    created_at: str | None = None
    age_days: int | None = None
    description: str | None = None
    parent_id: str | None = None
    accessible: bool | None = True


@dataclass(frozen=True)
class VirtualisationAlarm(CanonicalVirtualisationObject):
    object_id: str = ""
    severity: str = "Info"
    state: str = "unknown"
    message: str = ""


@dataclass(frozen=True)
class VirtualisationCapacity:
    cpu_total_mhz: float | None = None
    cpu_used_mhz: float | None = None
    memory_total_bytes: int | None = None
    memory_used_bytes: int | None = None
    storage_total_bytes: int | None = None
    storage_used_bytes: int | None = None


@dataclass(frozen=True)
class VirtualisationCollectionResult:
    provider: str
    endpoint_id: str
    deployment_id: str
    site_id: str
    started_at: str
    completed_at: str
    success: bool
    partial: bool = False
    diagnostic_category: str = "success"
    diagnostics: tuple[dict, ...] = ()
    objects: tuple[CanonicalVirtualisationObject, ...] = ()

    def to_dict(self):
        return {**asdict(self),
            "diagnostics": list(self.diagnostics),
            "objects": [value.to_dict() for value in self.objects]}
