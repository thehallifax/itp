"""Deterministic registry-driven collection and deployment status."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from analysis.state_history import (
    ObservationCompleteness,
    ObservationScope,
    PipelineRun,
)
from collectors import CollectorRegistry
from collectors.connector_registry import ConnectorMetadataRegistry
from collectors.scheduler import Scheduler, SchedulerStateStore
from collectors.writer import atomic_write


class Freshness(str, Enum):
    FRESH = "Fresh"
    AGING = "Aging"
    STALE = "Stale"
    UNKNOWN = "Unknown"
    NEVER_RUN = "Never Run"
    DISABLED = "Disabled"


def _utc(value=None):
    value = value or datetime.now(timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _identity(config):
    deployment = config.get("deployment")
    if isinstance(deployment, dict) and deployment.get("name"):
        return str(deployment["name"])
    return str(config.get("customer") or os.getenv("ITP_PROFILE") or "root")


def _deployment_type(config):
    deployment = config.get("deployment")
    return str(deployment.get("type") or "") \
        if isinstance(deployment, dict) else ""


def _safe_result(value):
    if not isinstance(value, dict):
        return {}
    allowed = (
        "points_written", "points_produced", "device_count", "site_count",
        "influx_write_completed")
    return {key: value[key] for key in allowed if key in value}


class PipelineRunStore:
    def __init__(self, root):
        self.root = Path(root)
        self.runs = self.root / "runs"
        self.latest_path = self.root / "latest.json"

    def write(self, payload):
        run_id = payload["pipeline_run"]["run_id"]
        destination = self.runs / (
            hashlib.sha256(run_id.encode()).hexdigest()[:24] + ".json")
        content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        atomic_write(destination, content)
        atomic_write(self.latest_path, content)

    def latest(self):
        try:
            return json.loads(self.latest_path.read_text())
        except (OSError, json.JSONDecodeError):
            return None

    def history(self):
        values = []
        for path in sorted(self.runs.glob("*.json")):
            try:
                values.append(json.loads(path.read_text()))
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(values, key=lambda value: (
            value.get("pipeline_run", {}).get("completed_at", ""),
            value.get("pipeline_run", {}).get("run_id", "")), reverse=True)


class OperatorCollectEngine:
    def __init__(self, root, config, *, registry=None, collector_factory=None,
                 scheduler_factory=Scheduler, runtime_dir=None, now_fn=None):
        self.root = Path(root)
        self.config = config
        self.registry = registry or ConnectorMetadataRegistry.load(self.root)
        self.collector_factory = collector_factory
        self.scheduler_factory = scheduler_factory
        self.runtime_dir = Path(runtime_dir or os.getenv(
            "ITP_RUNTIME_DIR", self.root / "runtime"))
        self.store = PipelineRunStore(self.runtime_dir / "pipeline-runs")
        self.now = now_fn or (lambda: datetime.now(timezone.utc))

    def _configured(self):
        settings = self.config.get("collectors", {})
        enabled, skipped = [], []
        for metadata in self.registry.all():
            value = settings.get(metadata.id)
            if not isinstance(value, dict) or value.get("enabled") is not True:
                skipped.append({
                    "connector": metadata.id, "display_name": metadata.display_name,
                    "status": "skipped", "duration_ms": 0, "summary": {},
                    "exception_type": "", "reason": "disabled"})
                continue
            try:
                if not self.collector_factory:
                    runtime_mode = os.getenv(
                        "ITP_RUNTIME_MODE", "central").strip().lower()
                    eligible, execution = CollectorRegistry.execution_eligible(
                        metadata.id, value, runtime_mode)
                    if not eligible:
                        skipped.append({
                            "connector": metadata.id,
                            "display_name": metadata.display_name,
                            "status": "skipped", "duration_ms": 0, "summary": {},
                            "exception_type": "",
                            "reason": (
                                f"execution placement {execution} does not match "
                                f"{runtime_mode} runtime")})
                        continue
                inventory_path = os.getenv(
                    "INVENTORY_PATH",
                    str(self.runtime_dir / "inventory/devices.json"))
                generated_dir = os.getenv(
                    "TELEGRAF_GENERATED_DIR",
                    str(self.runtime_dir / "telegraf"))
                if self.collector_factory:
                    collector = self.collector_factory(
                        metadata.id, self.config, inventory_path)
                else:
                    collector = CollectorRegistry.create_configured(
                        metadata.id, self.config, inventory_path, generated_dir)
                enabled.append((metadata, collector))
            except KeyError:
                skipped.append({
                    "connector": metadata.id, "display_name": metadata.display_name,
                    "status": "skipped", "duration_ms": 0, "summary": {},
                    "exception_type": "",
                    "reason": "runtime implementation unavailable"})
            except (TypeError, ValueError) as exc:
                skipped.append({
                    "connector": metadata.id, "display_name": metadata.display_name,
                    "status": "failed", "duration_ms": 0, "summary": {},
                    "exception_type": type(exc).__name__,
                    "reason": "connector initialization failed"})
        return enabled, skipped

    def run(self):
        started = _utc(self.now())
        configured, skipped = self._configured()
        scheduler = self.scheduler_factory([value[1] for value in configured])
        outcomes = asyncio.run(scheduler.execute_once("collect")) \
            if configured else ()
        completed = _utc(self.now())
        return self.record(configured, outcomes, skipped, started, completed)

    def record(self, configured, outcomes, additional_results=(),
               started=None, completed=None, scope_metadata=None):
        """Persist structured scheduler outcomes as one canonical PipelineRun."""
        started = _utc(started or self.now())
        completed = _utc(completed or self.now())
        metadata = {value.id: value for value, _ in configured}
        connector_results = []
        for outcome in outcomes:
            connector_results.append({
                "connector": outcome["connector"],
                "display_name": metadata[outcome["connector"]].display_name,
                "status": outcome["status"],
                "duration_ms": outcome["duration_ms"],
                "summary": _safe_result(outcome["value"]),
                "exception_type": outcome["exception_type"],
                "reason": outcome["reason"],
            })
        connector_results.extend(additional_results)
        connector_results.sort(key=lambda value: value["connector"])

        observed = {value["connector"] for value in connector_results
                    if value["status"] == "success"}
        failed = {value["connector"] for value in connector_results
                  if value["status"] == "failed"}
        skipped_ids = {value["connector"] for value in connector_results
                       if value["status"] == "skipped"
                       and value["reason"] != "disabled"}
        enabled_metadata = tuple(scope_metadata or (
            value for value in self.registry.all()
            if isinstance(self.config.get("collectors", {}).get(value.id), dict)
            and self.config["collectors"][value.id].get("enabled") is True))
        enabled_ids = {value.id for value in enabled_metadata}
        site_id = str(self.config.get("site") or "unassigned")
        scopes = []
        authorities = sorted({
            (site_id, domain) for value in enabled_metadata
            for domain in value.domains})
        for site, domain in authorities:
            expected = tuple(sorted(
                value.id for value in enabled_metadata if domain in value.domains))
            scope_observed = tuple(sorted(set(expected) & observed))
            scope_failed = tuple(sorted(set(expected) & failed))
            scope_skipped = tuple(sorted(set(expected) & skipped_ids))
            if scope_failed and not scope_observed:
                completeness = ObservationCompleteness.FAILED.value
            elif scope_skipped and not scope_observed:
                completeness = ObservationCompleteness.SKIPPED.value
            elif scope_failed or scope_skipped:
                completeness = ObservationCompleteness.PARTIAL.value
            else:
                completeness = ObservationCompleteness.COMPLETE.value
            scopes.append(ObservationScope(
                site, domain, completeness, expected_sources=expected,
                observed_sources=scope_observed, failed_sources=scope_failed,
                skipped_sources=scope_skipped))
        if failed and not observed:
            status = "failed"
        elif (failed or skipped_ids) and observed:
            status = "partial"
        elif skipped_ids:
            status = "skipped"
        elif not enabled_ids:
            status = "skipped"
        else:
            status = "success"
        material = {
            "started_at": started, "completed_at": completed,
            "deployment": _identity(self.config),
            "connectors": connector_results,
        }
        run_id = "collect:" + hashlib.sha256(json.dumps(
            material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]
        run = PipelineRun(
            run_id, started, completed, status, tuple(scopes),
            canonical_output="collector telemetry and inventory")
        payload = {
            "schema_version": 1,
            "deployment_identity": _identity(self.config),
            "deployment_type": _deployment_type(self.config),
            "pipeline_run": run.to_dict(),
            "connectors": connector_results,
            "summary": {
                "successful": len(observed), "failed": len(failed),
                "skipped": sum(value["status"] == "skipped"
                               for value in connector_results),
                "duration_ms": max(0, int((
                    _parse(completed) - _parse(started)).total_seconds() * 1000)),
                "overall": status,
            },
        }
        self.store.write(payload)
        return payload


class OperatorStatusEngine:
    def __init__(self, root, config, *, registry=None, runtime_dir=None,
                 now_fn=None, readiness=None):
        self.root = Path(root)
        self.config = config
        self.registry = registry or ConnectorMetadataRegistry.load(self.root)
        self.runtime_dir = Path(runtime_dir or os.getenv(
            "ITP_RUNTIME_DIR", self.root / "runtime"))
        self.store = PipelineRunStore(self.runtime_dir / "pipeline-runs")
        self.now = now_fn or (lambda: datetime.now(timezone.utc))
        self.readiness = {
            value["id"]: value for value in (readiness or ())}

    def _freshness(self, enabled, connector_id):
        """Describe observation age only; execution health is projected separately."""
        if not enabled:
            return Freshness.DISABLED.value
        records = [
            (run, item) for run in self.store.history()
            for item in run.get("connectors", [])
            if item.get("connector") == connector_id]
        if not records:
            return Freshness.NEVER_RUN.value
        current_run, _ = records[0]
        timestamp = _parse(current_run.get(
            "pipeline_run", {}).get("completed_at"))
        if timestamp is None:
            return Freshness.UNKNOWN.value
        age = (self.now().astimezone(timezone.utc) -
               timestamp.astimezone(timezone.utc)).total_seconds()
        settings = self.config.get("collectors", {}).get(connector_id, {})
        interval = max(60, int(settings.get(
            "collection_interval_seconds", 300)))
        if age <= interval * 2:
            return Freshness.FRESH.value
        if age <= interval * 3:
            return Freshness.AGING.value
        return Freshness.STALE.value

    def run(self):
        latest = self.store.latest()
        settings = self.config.get("collectors", {})
        connectors = []
        history = self.store.history()
        for metadata in self.registry.all():
            enabled = isinstance(settings.get(metadata.id), dict) and \
                settings[metadata.id].get("enabled") is True
            successes = [
                run for run in history if any(
                    item.get("connector") == metadata.id
                    and item.get("status") == "success"
                    for item in run.get("connectors", []))]
            records = [
                (run, item) for run in history
                for item in run.get("connectors", [])
                if item.get("connector") == metadata.id]
            failures = [
                (run, item) for run, item in records
                if item.get("status") == "failed"]
            latest_record = records[0] if records else (None, None)
            readiness = self.readiness.get(metadata.id, {})
            latest_summary = (
                latest_record[1].get("summary") or {}
                if latest_record[1] else {})
            configuration_state = readiness.get(
                "state", "configured" if enabled else "disabled")
            current_status = (
                latest_record[1].get("status") if latest_record[1] else None)
            current_reason = (
                latest_record[1].get("reason") if latest_record[1] else None)
            if configuration_state == "disabled":
                operational_status = "disabled"
                health = "Disabled"
            elif configuration_state == "execution mode mismatch":
                operational_status = "skipped execution mode mismatch"
                health = "Warning"
            elif configuration_state in {
                    "pending configuration", "pending credentials"}:
                operational_status = configuration_state
                health = "Warning"
            elif current_status == "skipped":
                skip_reason = str(
                    latest_record[1].get("reason") or "").casefold()
                if "execution mode" in skip_reason or \
                        latest_record[1].get(
                            "exception_type") == "execution_mode_mismatch":
                    operational_status = "skipped execution mode mismatch"
                elif "runtime policy" in skip_reason or \
                        "placement" in skip_reason:
                    operational_status = "skipped runtime policy"
                else:
                    operational_status = "skipped prerequisite"
                health = "Warning"
            elif current_status == "failed":
                lowered = str(current_reason or "").casefold()
                if "not trusted" in lowered or "certificate" in lowered:
                    operational_status = "TLS trust failure"
                elif "authentication" in lowered or "http 401" in lowered:
                    operational_status = "authentication failure"
                elif any(value in lowered for value in (
                        "dns", "connection", "unreachable", "timed out")):
                    operational_status = "unreachable"
                else:
                    operational_status = "failed"
                health = "Failed"
            elif current_status == "success":
                operational_status = "successful"
                health = "Healthy"
            else:
                operational_status = "not yet collected"
                health = "Warning" if enabled else "Disabled"
            connectors.append({
                "connector": metadata.id,
                "display_name": metadata.display_name,
                "enabled": enabled,
                "configuration_state": configuration_state,
                "status": operational_status,
                "health": health,
                "missing": list(readiness.get("missing") or ()),
                "execution_mode": readiness.get("execution_mode"),
                "runtime_mode": readiness.get("runtime_mode"),
                "tls_verification": readiness.get("tls_verification"),
                "freshness": self._freshness(enabled, metadata.id),
                "last_run": (
                    latest_record[0]["pipeline_run"]["completed_at"]
                    if latest_record[0] else None),
                "last_successful_collection": (
                    successes[0]["pipeline_run"]["completed_at"]
                    if successes else None),
                "last_failure": (
                    failures[0][0]["pipeline_run"]["completed_at"]
                    if failures else None),
                "last_error_summary": (
                    failures[0][1].get("reason") if failures else None),
                "records_collected": (
                    latest_summary.get("points_written")
                    or latest_summary.get("points_produced")
                    or latest_summary.get("device_count")
                    or 0),
            })
        services_path = self.runtime_dir / "services/service-health.json"
        try:
            service_payload = json.loads(services_path.read_text())
            services = service_payload.get("estate", {}).get("services", [])
            service_health = [{
                "service": str(value.get("service") or value.get("name") or ""),
                "status": str(value.get("status") or value.get("state") or "Unknown")}
                for value in services]
        except (OSError, json.JSONDecodeError):
            service_health = []
        service_health.sort(key=lambda value: value["service"])
        from .daemon import DaemonStateStore
        from analysis.notifications import NotificationEngine
        return {
            "schema_version": 1,
            "generated_at": _utc(self.now()),
            "deployment_identity": _identity(self.config),
            "deployment_type": _deployment_type(self.config),
            "connectors": connectors,
            "service_health": service_health,
            "daemon": DaemonStateStore(self.runtime_dir).snapshot(self.now()),
            "scheduler": SchedulerStateStore(
                self.runtime_dir / "scheduler/state.json").value
            if not (self.runtime_dir / "scheduler/state.json").is_file()
            else self._read_scheduler_state(),
            "notifications": NotificationEngine(
                self.runtime_dir, self.config.get("notifications")).summary(),
            "latest_pipeline_run": (
                latest.get("pipeline_run") if latest else None),
            "latest_connector_results": (
                latest.get("connectors", []) if latest else []),
        }

    def _read_scheduler_state(self):
        try:
            value = json.loads(
                (self.runtime_dir / "scheduler/state.json").read_text())
            return value if isinstance(value, dict) else \
                SchedulerStateStore.defaults()
        except (OSError, json.JSONDecodeError):
            return SchedulerStateStore.defaults()


def render_collect(payload):
    lines = [
        f"Collection: {payload['summary']['overall']}",
        f"Deployment: {payload['deployment_identity']}"
        + (f" ({payload['deployment_type']})"
           if payload.get("deployment_type") else "")]
    for value in payload["connectors"]:
        duration = f" ({value.get('duration_ms', 0)} ms)" \
            if value["status"] != "skipped" else ""
        reason = f" — {value['reason']}" if value.get("reason") else ""
        lines.append(
            f"[{value['status'].upper()}] {value['display_name']}{duration}{reason}")
    summary = payload["summary"]
    lines.append(
        f"Summary: successful={summary['successful']} failed={summary['failed']} "
        f"skipped={summary['skipped']} duration={summary['duration_ms']} ms")
    return "\n".join(lines)


def render_status(payload):
    lines = [
        f"Deployment: {payload['deployment_identity']}"
        + (f" ({payload['deployment_type']})"
           if payload.get("deployment_type") else "")]
    daemon = payload["daemon"]
    scheduler = payload["scheduler"]
    current = ", ".join(daemon["current_collection"]) or "none"
    lines.extend((
        f"Daemon: {daemon['status']}",
        f"  Last heartbeat: {daemon.get('last_heartbeat') or 'Never'}",
        f"  Last successful collection: "
        f"{daemon.get('last_successful_collection') or 'Never'}",
        f"  Current collection: {current}",
        f"  Uptime: {daemon['uptime_seconds']} seconds",
        f"Scheduler: {scheduler['lifecycle_state']}",
        "  Initial discovery: "
        f"{scheduler['initial_discovery']['outcome']}",
        "  Initial collection: "
        f"{scheduler['initial_collection']['outcome']}",
        "  Last discovery success: "
        f"{scheduler.get('last_successful_discovery') or 'Never'}",
        "  Last collection success: "
        f"{scheduler.get('last_successful_collection') or 'Never'}",
        "  Last skip reason: "
        f"{scheduler.get('last_skip_reason') or 'none'}",
        "Connectors:",
    ))
    for value in payload["connectors"]:
        lines.append(
            f"  {value['display_name']}: "
            f"{value.get('status', value.get('configuration_state', 'configured'))}; "
            f"health={value.get('health', 'Warning')}; "
            f"freshness={value['freshness']}"
            + (f" — last success {value['last_successful_collection']}"
               if value["last_successful_collection"] else ""))
        if value.get("missing"):
            lines.append(
                "    Missing: " + ", ".join(value["missing"]))
        if value.get("tls_verification") is False:
            lines.append(
                "    WARNING: PaperCut TLS certificate verification is "
                "disabled for this deployment.")
        if value.get("last_run"):
            lines.append(f"    Last run: {value['last_run']}")
        if value.get("last_failure"):
            lines.append(
                f"    Last failure: {value['last_failure']} — "
                f"{value.get('last_error_summary') or 'collection failed'}")
        lines.append(
            f"    Records collected: {value.get('records_collected', 0)}")
    lines.append("Service health:")
    lines.extend(
        f"  {value['service']}: {value['status']}"
        for value in payload["service_health"])
    if not payload["service_health"]:
        lines.append("  Unknown")
    latest = payload["latest_pipeline_run"]
    lines.append("Latest PipelineRun: " + (
        f"{latest['run_id']} ({latest['status']})"
        if latest else "Never Run"))
    notifications = payload["notifications"]
    lines.extend((
        f"Active notifications: {notifications['active_count']}",
        "Highest active severity: "
        f"{notifications['highest_active_severity'] or 'none'}",
        "Failed notification deliveries: "
        f"{notifications['failed_delivery_count']}",
    ))
    return "\n".join(lines)
