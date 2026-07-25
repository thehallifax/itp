"""Low-cardinality canonical telemetry mapping."""
from datetime import datetime
from .normalise import percentage


def _timestamp(value):
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
                   * 1_000_000_000)
    except (TypeError, ValueError):
        return None


MEASUREMENT_BY_KIND = {
    "manager": "virtualisation_platform",
    "cluster": "virtualisation_cluster",
    "host": "virtualisation_host",
    "vm": "virtualisation_workload",
    "container": "virtualisation_workload",
    "storage": "virtualisation_storage",
    "snapshot": "virtualisation_snapshot",
}


def points(state):
    result = []
    for value in state["objects"]:
        measurement = MEASUREMENT_BY_KIND.get(value["kind"])
        if not measurement:
            continue
        tags = {
            "deployment_id": value["deployment_id"],
            "site_id": value["site_id"],
            "provider": value["provider"],
        }
        for key in ("manager_id", "cluster_id", "host_id"):
            if value.get(key):
                tags[key] = str(value[key])
        if value["kind"] in {"vm", "container"}:
            tags["workload_type"] = value["kind"]
            tags["state"] = value["power_state"]
        fields = {"canonical_id": value["canonical_id"], "name": value["name"]}
        for key in (
            "reachable", "health", "ha_status", "connection_state", "maintenance",
            "cpu_total_mhz", "cpu_used_mhz", "memory_total_bytes", "memory_used_bytes",
            "vm_count", "running_vm_count", "vcpu", "memory_bytes",
            "guest_agent_state", "snapshot_count", "oldest_snapshot_age_days",
            "capacity_bytes", "used_bytes", "free_bytes", "utilisation_percent",
            "accessible", "shared", "age_days",
        ):
            if value.get(key) is not None:
                fields[key] = value[key]
        if value["kind"] in {"cluster", "host"}:
            cpu = percentage(value.get("cpu_used_mhz"), value.get("cpu_total_mhz"))
            memory = percentage(value.get("memory_used_bytes"),
                                value.get("memory_total_bytes"))
            if cpu is not None:
                fields["cpu_utilisation_percent"] = cpu
            if memory is not None:
                fields["memory_utilisation_percent"] = memory
        if value["kind"] == "host" and value.get("vm_count") is not None:
            fields["workload_count"] = value["vm_count"]
        if value["kind"] == "snapshot":
            fields["workload_id"] = value.get("workload_id", "")
            fields["snapshot_type"] = value.get("snapshot_type", "snapshot")
        result.append({"measurement": measurement, "tags": tags,
                       "fields": fields, "timestamp": _timestamp(value["observed_at"])})
    for value in state["findings"]:
        result.append({"measurement": "virtualisation_finding",
            "tags": {"deployment_id": value["deployment_id"], "site_id": value["site_id"],
                     "provider": value["provider"], "severity": value["severity"]},
            "fields": {"finding_id": value["id"], "rule_id": value["rule_id"],
                       "canonical_id": value["canonical_id"], "reason": value["reason"],
                       "recommended_operator_check": value["recommended_operator_check"],
                       "confidence": value["confidence"]},
            "timestamp": _timestamp(value["last_observed"])})
    for value in state["collections"]:
        result.append({"measurement": "virtualisation_collection",
            "tags": {"deployment_id": state["deployment_id"],
                     "provider": value["provider"]},
            "fields": {"endpoint_id": value["endpoint"], "result": value["result"],
                       "partial": value["partial"], "duration_ms": value["duration_ms"],
                       "diagnostic_category": value["diagnostic_category"]},
            "timestamp": _timestamp(value["last_attempt"])})
    return result
