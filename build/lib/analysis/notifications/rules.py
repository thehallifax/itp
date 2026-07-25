"""Deterministic notification condition adapters over canonical state."""
from __future__ import annotations

from datetime import datetime, timezone

from .models import (
    NotificationFingerprint, NotificationRule, NotificationSeverity)


RULES = (
    NotificationRule("connector.collection_failed", "Connector collection failed",
                     NotificationSeverity.CRITICAL, "pipeline"),
    NotificationRule("connector.stale", "Connector telemetry is stale",
                     NotificationSeverity.WARNING, "freshness"),
    NotificationRule("daemon.stopped", "ITP daemon is stopped",
                     NotificationSeverity.CRITICAL, "daemon"),
    NotificationRule("daemon.heartbeat_stale", "ITP daemon heartbeat is stale",
                     NotificationSeverity.CRITICAL, "daemon"),
    NotificationRule("doctor.failed", "ITP Doctor check failed",
                     NotificationSeverity.CRITICAL, "doctor"),
    NotificationRule("pipeline.partial", "PipelineRun partially failed",
                     NotificationSeverity.WARNING, "pipeline"),
    NotificationRule("pipeline.failed", "PipelineRun failed",
                     NotificationSeverity.CRITICAL, "pipeline"),
)


def _condition(rule_id, subject, summary, *, scope=""):
    rule = next(value for value in RULES if value.id == rule_id)
    return {
        "fingerprint": NotificationFingerprint(
            rule_id, subject, scope).value,
        "rule_id": rule.id, "severity": rule.severity.value,
        "title": rule.title, "summary": summary, "source": rule.source,
        "subject": subject,
    }


def evaluate_conditions(status, doctor=None, *, now=None,
                        heartbeat_stale_seconds=30):
    """Return sorted active conditions without performing health checks."""
    now = now or datetime.now(timezone.utc)
    conditions = []
    latest = status.get("latest_pipeline_run") or {}
    latest_results = status.get("latest_connector_results") or []
    for value in latest_results:
        if value.get("status") == "failed":
            connector = str(value.get("connector") or "unknown")
            display_name = str(value.get("display_name") or connector)
            conditions.append(_condition(
                "connector.collection_failed", connector,
                f"{display_name} collection failed."))
    for value in status.get("connectors", []):
        if value.get("enabled") and value.get("freshness") == "Stale":
            connector = str(value.get("connector") or "unknown")
            display_name = str(value.get("display_name") or connector)
            conditions.append(_condition(
                "connector.stale", connector,
                f"{display_name} has exceeded its configured freshness threshold."))
    daemon = status.get("daemon") or {}
    daemon_status = daemon.get("status", "Stopped")
    if daemon_status == "Stopped":
        conditions.append(_condition(
            "daemon.stopped", "operator-daemon",
            "The ITP continuous collection daemon is stopped."))
    elif daemon_status == "Running":
        heartbeat = daemon.get("last_heartbeat")
        try:
            observed = datetime.fromisoformat(
                str(heartbeat).replace("Z", "+00:00"))
            age = (now.astimezone(timezone.utc)
                   - observed.astimezone(timezone.utc)).total_seconds()
        except (TypeError, ValueError):
            age = heartbeat_stale_seconds + 1
        if age > heartbeat_stale_seconds:
            conditions.append(_condition(
                "daemon.heartbeat_stale", "operator-daemon",
                "The ITP daemon heartbeat is overdue."))
    if doctor:
        checks = doctor.to_dict().get("checks", []) \
            if hasattr(doctor, "to_dict") else doctor.get("checks", [])
        for check in checks:
            if check.get("status") == "fail":
                check_id = str(check.get("check_id") or "unknown")
                conditions.append(_condition(
                    "doctor.failed", check_id,
                    f"Doctor check failed: {check.get('subject') or check_id}."))
    if latest.get("status") == "partial":
        conditions.append(_condition(
            "pipeline.partial", "latest-pipeline",
            "The latest PipelineRun completed with partial source coverage."))
    elif latest.get("status") == "failed":
        conditions.append(_condition(
            "pipeline.failed", "latest-pipeline",
            "The latest PipelineRun failed."))
    return sorted(conditions, key=lambda value: value["fingerprint"])
