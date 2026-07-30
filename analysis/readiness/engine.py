"""Deterministic readiness evaluation from existing platform contracts."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from collectors.writer import atomic_write

from .models import ReadinessState

READINESS_PRECEDENCE = {
    "healthy": 0,
    "not_configured": 1,
    "waiting_first_collection": 2,
    "warning": 3,
    "unavailable": 4,
    "critical": 5,
}


def _time(value):
    try:
        return datetime.fromisoformat(
            str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _state(state, reason, *, configured, enabled, first_run=False,
           last_success=None, stale=False, label, action):
    return ReadinessState(
        state, reason, configured, enabled, first_run, last_success,
        stale, label, action).to_dict()


def aggregate_readiness(*states):
    """Return the highest-precedence canonical readiness state."""
    if not states:
        raise ValueError("at least one readiness state is required")
    return dict(max(
        states, key=lambda value: READINESS_PRECEDENCE[value["state"]]))


def credentials_ready(config, registry, environment):
    """Return credential completeness without retaining names or values."""
    enabled = [
        name for name, value in (config.get("collectors") or {}).items()
        if isinstance(value, dict) and value.get("enabled") is True]
    if not enabled:
        return False
    for name in enabled:
        try:
            metadata = registry.get(name)
        except KeyError:
            return False
        for field in metadata.credential_fields:
            if field.get("required") and not str(
                    environment.get(field.get("env"), "")).strip():
                return False
    return True


def evaluate_readiness(*, enabled_collectors=(), collector_records=(),
                       capability_manifest=None,
                       capabilities=(), assets=(), operations_generated=False,
                       deployment_configured=True, platform_running=False,
                       credentials_configured=False, demo=False, now=None,
                       stale_seconds=900):
    """Return one deterministic readiness document without sensitive input."""
    now = now or datetime.now(timezone.utc)
    enabled = tuple(sorted({str(value) for value in enabled_collectors}))
    if capability_manifest:
        manifest_records = []
        for name in enabled:
            value = capability_manifest.get("collectors", {}).get(name, {})
            state = value.get("execution", {}).get("state")
            if not state:
                state = value.get("state")
            collection = value.get("last_collection", {})
            if not isinstance(collection, dict):
                collection = {
                    "observed_at": collection,
                    "last_success": value.get("last_successful_collection"),
                }
            status = {
                "collected": "healthy", "partial": "warning",
                "failed": "failed", "unavailable": "warning",
                "not_yet_collected": "unknown",
            }.get(state, "unknown")
            manifest_records.append({
                "collector": name, "status": status,
                "last_run": collection.get("observed_at"),
                "last_successful_run": collection.get("last_success"),
                "points_written": collection.get("points_written"),
                "skip_reason": collection.get("skip_reason"),
            })
        collector_records = manifest_records
    records = {
        str(value.get("collector")): value for value in collector_records
        if value.get("collector") in enabled}
    collector_states = []
    for name in enabled:
        record = records.get(name)
        if not record:
            value = _state(
                "waiting_first_collection", "no_run_record",
                configured=True, enabled=True, label="Waiting for first run",
                action="Run collection and review Collector Health.")
        else:
            last_success = record.get("last_successful_run")
            last_run = record.get("last_run")
            observed = _time(last_run)
            stale = bool(observed and
                         (now - observed).total_seconds() > stale_seconds)
            status = str(record.get("status") or "unknown").casefold()
            points_written = record.get("points_written")
            if status == "failed":
                value = _state(
                    "unavailable", "collection_failed",
                    configured=True, enabled=True,
                    first_run=bool(last_run), last_success=last_success,
                    stale=False, label="Collection failed",
                    action="Run Doctor and inspect Collector Health.")
            elif stale or status == "stale":
                value = _state(
                    "unavailable", "collection_stale",
                    configured=True, enabled=True,
                    first_run=bool(last_run), last_success=last_success,
                    stale=True, label="Collection stale",
                    action="Check daemon health and collector connectivity.")
            elif status == "skipped":
                value = _state(
                    "unavailable", "runtime_or_scheduler_skip",
                    configured=True, enabled=True,
                    first_run=bool(last_run), last_success=last_success,
                    label="Collector skipped",
                    action=(
                        "Review runtime placement and Collector Health: "
                        f"{record.get('skip_reason') or 'skip reason unavailable'}."))
            elif status == "warning":
                value = _state(
                    "warning", "collection_partial",
                    configured=True, enabled=True,
                    first_run=bool(last_run), last_success=last_success,
                    label="Collection warning",
                    action="Review Collector Health for partial results.")
            elif (status in {"healthy", "success"} or last_success) \
                    and points_written == 0:
                value = _state(
                    "waiting_first_collection", "healthy_no_telemetry",
                    configured=True, enabled=True, first_run=True,
                    last_success=last_success or last_run,
                    label="No telemetry received",
                    action=(
                        "Collector healthy; waiting for supported data. "
                        "Review points written and capability availability."))
            elif status in {"healthy", "success"} or last_success:
                value = _state(
                    "healthy", "collection_current",
                    configured=True, enabled=True, first_run=True,
                    last_success=last_success or last_run,
                    label="Healthy", action="No action required.")
            else:
                value = _state(
                    "waiting_first_collection", "no_successful_run",
                    configured=True, enabled=True,
                    first_run=bool(last_run), last_success=last_success,
                    label="Waiting for first success",
                    action="Run collection and review Collector Health.")
        collector_states.append({"collector": name, **value})

    if demo:
        for value in collector_states:
            if value["state"] == "waiting_first_collection":
                value.update(_state(
                    "healthy", "demo_data_active", configured=True,
                    enabled=True, first_run=True,
                    label="Demo data active", action="No action required."))

    states = {value["state"] for value in collector_states}
    first_success = any(value["last_success"] or value["state"] == "healthy"
                        for value in collector_states)
    latest_success = max(
        (str(value["last_success"]) for value in collector_states
         if value["last_success"]), default=None)
    if demo and not enabled:
        observability = _state(
            "healthy", "demo_data_active",
            configured=True, enabled=False, first_run=True,
            label="Demo data active", action="No action required.")
    elif not enabled:
        observability = _state(
            "not_configured", "no_collectors_enabled",
            configured=False, enabled=False, label="Monitoring not started",
            action="Configure credentials and enable a collector.")
    elif "unavailable" in states and states <= {"unavailable"}:
        observability = _state(
            "unavailable", "all_collectors_unavailable",
            configured=True, enabled=True, first_run=True,
            last_success=latest_success,
            stale=any(value["stale"] for value in collector_states),
            label="Collectors unavailable",
            action="Run Doctor and inspect Collector Health.")
    elif "unavailable" in states or "warning" in states:
        observability = _state(
            "warning", "collector_degradation",
            configured=True, enabled=True, first_run=first_success,
            last_success=latest_success,
            stale=any(value["stale"] for value in collector_states),
            label="Warning",
            action="Review Collector Health.")
    elif not first_success:
        observability = _state(
            "waiting_first_collection", "no_successful_collection",
            configured=True, enabled=True, label="Awaiting first collection",
            action="Run collection and wait for the first successful result.")
    else:
        observability = _state(
            "healthy", "collectors_current",
            configured=True, enabled=True, first_run=True,
            last_success=latest_success, label="Healthy",
            action="No action required.")

    if demo and not enabled:
        infrastructure = _state(
            "healthy", "demo_data_active",
            configured=True, enabled=False, first_run=True,
            label="Demo data active", action="No action required.")
    elif observability["state"] == "not_configured":
        infrastructure = _state(
            "not_configured", "discovery_not_configured",
            configured=False, enabled=False, label="Discovery not configured",
            action="Enable a collector to begin discovery.")
    elif observability["state"] in {"waiting_first_collection", "unavailable"}:
        infrastructure = _state(
            observability["state"], "inventory_unavailable",
            configured=True, enabled=True,
            first_run=observability["first_run_completed"],
            last_success=observability["last_success"],
            stale=observability["stale"],
            label=("Waiting for inventory"
                   if observability["state"] == "waiting_first_collection"
                   else "Inventory unavailable"),
            action=observability["operator_action"])
    elif not assets:
        infrastructure = _state(
            "waiting_first_collection", "inventory_empty",
            configured=True, enabled=True, first_run=first_success,
            last_success=latest_success, label="Waiting for inventory",
            action="Run discovery and confirm the collector returns assets.")
    else:
        infrastructure = _state(
            "healthy", "inventory_available",
            configured=True, enabled=True, first_run=True,
            last_success=latest_success, label="Healthy",
            action="Review any operational findings.")

    overall = aggregate_readiness(observability, infrastructure)
    steps = (
        ("platform_services", "Platform services running", platform_running,
         "Run ./itp status; if services are stopped, run ./itp start."),
        ("deployment", "Deployment configured", deployment_configured,
         "Run ./itp setup to complete deployment configuration."),
        ("credentials", "Collector credentials configured",
         demo or bool(enabled) and credentials_configured,
         "Add credentials to the ignored connector secret file."),
        ("collector_enabled", "At least one collector enabled",
         bool(enabled) or demo,
         "Enable one configured collector in discovery/config.yml."),
        ("first_collection", "First successful collection completed",
         first_success or demo, "Run ./itp collect and review Collector Health."),
        ("inventory", "Infrastructure inventory available", bool(assets) or demo,
         "Run discovery and confirm inventory output."),
        ("operations", "Operational analysis available",
         operations_generated or demo,
         "Run ./itp operations after infrastructure state is available."),
    )
    onboarding = [{
        "id": identifier, "step": index, "label": label,
        "complete": bool(complete),
        "state": "complete" if complete else "incomplete",
        "operator_action": "Complete" if complete else action,
    } for index, (identifier, label, complete, action) in enumerate(steps, 1)]
    return {
        "schema_version": 1,
        "generated_at": now.astimezone(timezone.utc).isoformat().replace(
            "+00:00", "Z"),
        "demo": bool(demo),
        "enabled_collectors": list(enabled),
        "capabilities": sorted(set(capabilities)),
        "overall": overall,
        "infrastructure": infrastructure,
        "observability": observability,
        "collectors": collector_states,
        "onboarding": onboarding,
    }


class ReadinessEngine:
    def __init__(self, output_path):
        self.output_path = Path(output_path)

    def write(self, **inputs):
        result = evaluate_readiness(**inputs)
        atomic_write(
            self.output_path,
            json.dumps(result, indent=2, sort_keys=True) + "\n")
        return result


def empty_infrastructure_summary(readiness):
    """Return the deterministic zero-asset dashboard contract."""
    infrastructure = readiness["infrastructure"]["display_label"]
    observability = readiness["observability"]["display_label"]
    scope = {
        "scope": "all", "display_name": "All Sites",
        "sites": 0, "healthy_sites": 0, "warning_sites": 0,
        "critical_sites": 0, "devices": 0, "devices_online": 0,
        "devices_offline": 0, "actionable_warnings": 0,
        "data_quality_findings": 0,
        "infrastructure_health": infrastructure,
        "observability_health": observability,
        "collectors_healthy": 0, "collectors_failed": 0,
        "switches_total": 0, "switches_online": 0, "switches_offline": 0,
        "aps_total": 0, "aps_online": 0, "aps_offline": 0,
        "firewalls_total": 0, "firewalls_healthy": 0, "firewalls_offline": 0,
        "servers_total": 0, "servers_healthy": 0, "servers_offline": 0,
        "printers_total": 0, "printers_healthy": 0, "printers_offline": 0,
    }
    return {
        "generated_at": readiness["generated_at"],
        "readiness": readiness, "site_options": [], "scopes": [scope],
        "infrastructure_health": infrastructure,
        "observability_health": observability,
        "sites": 0, "healthy_sites": 0, "warning_sites": 0,
        "critical_sites": 0, "devices": 0, "devices_online": 0,
        "devices_offline": 0, "warnings": 0, "actionable_warnings": 0,
        "data_quality_findings": 0, "critical": 0,
        "collectors_healthy": 0, "collectors_failed": 0,
        "switches_total": 0, "switches_online": 0, "switches_offline": 0,
        "aps_total": 0, "aps_online": 0, "aps_offline": 0,
        "firewalls_total": 0, "firewalls_healthy": 0, "firewalls_offline": 0,
        "servers_total": 0, "servers_healthy": 0, "servers_offline": 0,
        "printers_total": 0, "printers_healthy": 0, "printers_offline": 0,
    }
