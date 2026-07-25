"""Deterministic, explainable virtualisation health findings."""
from __future__ import annotations

import hashlib
import json

from .normalise import percentage


def _finding(rule, severity, obj, reason, action, evidence, confidence="high"):
    material = json.dumps([rule, obj["canonical_id"], evidence], sort_keys=True, default=str)
    return {
        "id": "virt:" + hashlib.sha256(material.encode()).hexdigest()[:20],
        "rule_id": rule, "provider": obj["provider"],
        "deployment_id": obj["deployment_id"], "site_id": obj["site_id"],
        "canonical_id": obj["canonical_id"], "object_name": obj["display_name"],
        "severity": severity, "confidence": confidence,
        "reason": reason, "recommended_operator_check": action,
        "evidence": evidence, "first_observed": obj.get("observed_at"),
        "last_observed": obj.get("observed_at"),
    }


def evaluate(collection, thresholds):
    values = []
    cpu_warning = float(thresholds.get("host_cpu_warning_percent", 85))
    memory_warning = float(thresholds.get("host_memory_warning_percent", 85))
    storage_warning = float(thresholds.get("storage_warning_percent", 80))
    storage_critical = float(thresholds.get("storage_critical_percent", 90))
    snapshot_days = int(thresholds.get("snapshot_age_warning_days", 30))
    snapshot_count = int(thresholds.get("snapshot_count_warning", 3))
    maintenance_expected = set(thresholds.get("expected_maintenance_host_ids", []))
    for obj in collection:
        kind = obj.get("kind")
        if kind == "manager" and obj.get("reachable") is False:
            values.append(_finding("virtualisation.manager_unreachable", "Critical", obj,
                "The management endpoint is unreachable.",
                "Verify endpoint reachability, TLS trust and delegated credentials.",
                {"reachable": False}))
        if kind == "cluster" and obj.get("health") in {"warning", "critical", "degraded"}:
            values.append(_finding("virtualisation.cluster_degraded",
                "Critical" if obj.get("health") == "critical" else "High", obj,
                "The cluster reports degraded health.",
                "Review cluster alarms, host quorum and remaining failover capacity.",
                {"health": obj.get("health"), "degraded_hosts": obj.get("degraded_host_count")}))
        if kind == "host":
            connection = str(obj.get("connection_state", "")).casefold()
            if connection in {"disconnected", "not_responding", "notresponding"}:
                values.append(_finding("virtualisation.host_disconnected", "Critical", obj,
                    f"Host connection state is {connection}.",
                    "Confirm host management connectivity and cluster membership.",
                    {"connection_state": connection}))
            if obj.get("maintenance") is True and obj["source_id"] not in maintenance_expected:
                values.append(_finding("virtualisation.unexpected_maintenance", "Medium", obj,
                    "Host is in maintenance mode without a configured expectation.",
                    "Confirm the maintenance window and evacuation status.",
                    {"maintenance": True}))
            for rule, label, used, total, warning in (
                ("host_cpu_high", "CPU", obj.get("cpu_used_mhz"), obj.get("cpu_total_mhz"), cpu_warning),
                ("host_memory_high", "memory", obj.get("memory_used_bytes"),
                 obj.get("memory_total_bytes"), memory_warning)):
                utilisation = percentage(used, total)
                if utilisation is not None and utilisation >= warning:
                    values.append(_finding("virtualisation." + rule, "Medium", obj,
                        f"Host {label} utilisation is {utilisation}%.",
                        f"Review workload demand and available {label} capacity.",
                        {"used": used, "total": total, "utilisation_percent": utilisation,
                         "threshold_percent": warning}))
        if kind == "storage":
            utilisation = obj.get("utilisation_percent")
            if obj.get("accessible") is False:
                values.append(_finding("virtualisation.storage_inaccessible", "Critical", obj,
                    "Virtualisation storage is inaccessible.",
                    "Verify storage paths, cluster access and backing storage health.",
                    {"accessible": False}))
            elif utilisation is not None and utilisation >= storage_critical:
                values.append(_finding("virtualisation.storage_capacity_critical", "Critical", obj,
                    f"Storage utilisation is {utilisation}%.", "Free or extend storage capacity.",
                    {"utilisation_percent": utilisation, "threshold_percent": storage_critical}))
            elif utilisation is not None and utilisation >= storage_warning:
                values.append(_finding("virtualisation.storage_capacity_warning", "Medium", obj,
                    f"Storage utilisation is {utilisation}%.", "Review storage growth and capacity.",
                    {"utilisation_percent": utilisation, "threshold_percent": storage_warning}))
        if kind in {"vm", "container"}:
            if obj.get("power_state") == "unknown":
                values.append(_finding("virtualisation.workload_state_unknown", "Low", obj,
                    "Workload power state could not be normalized.",
                    "Check provider permissions and workload inventory evidence.",
                    {"native_state": obj.get("evidence", {}).get("native_state")}, "medium"))
            agent = str(obj.get("guest_agent_state") or "").casefold()
            if obj.get("power_state") == "running" and agent in {"missing", "not_running", "unhealthy"}:
                values.append(_finding("virtualisation.guest_agent_unhealthy", "Low", obj,
                    "The running workload guest integration agent is unavailable.",
                    "Verify the provider guest tools or integration service.",
                    {"agent_type": obj.get("guest_agent_type"), "agent_state": agent}))
            if obj.get("snapshot_count", 0) > snapshot_count:
                values.append(_finding("virtualisation.snapshot_count_excessive", "Medium", obj,
                    f"Workload has {obj['snapshot_count']} snapshots/checkpoints.",
                    "Confirm retention requirements and remove only through approved change control.",
                    {"count": obj["snapshot_count"], "threshold": snapshot_count}))
        if kind == "snapshot":
            if obj.get("accessible") is False:
                values.append(_finding("virtualisation.snapshot_inaccessible", "Low", obj,
                    "Snapshot evidence could not be fully read.",
                    "Review provider permissions and snapshot metadata.",
                    {"accessible": False}, "medium"))
            elif obj.get("age_days") is None:
                values.append(_finding("virtualisation.snapshot_age_unknown", "Info", obj,
                    "Snapshot age is unknown.", "Review snapshot creation metadata.",
                    {"created_at": obj.get("created_at")}, "low"))
            elif obj["age_days"] > snapshot_days:
                values.append(_finding("virtualisation.snapshot_stale", "Medium", obj,
                    f"Snapshot/checkpoint is {obj['age_days']} days old.",
                    "Confirm whether the snapshot is still required under change policy.",
                    {"age_days": obj["age_days"], "threshold_days": snapshot_days}))
    return sorted(values, key=lambda value: (
        value["provider"], value["site_id"], value["rule_id"], value["canonical_id"]))
