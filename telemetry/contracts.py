"""Framework-owned deployment identity and telemetry validation contracts."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone


class TelemetryValidationError(ValueError):
    """A deterministic, non-secret telemetry contract failure."""

    def __init__(self, measurement, field, expected, received, collector,
                 point_number):
        self.measurement = measurement
        self.field = field
        self.expected = expected
        self.received = received
        self.collector = collector or "unknown"
        self.point_number = point_number
        super().__init__(
            f"Measurement: {measurement}; Field: {field}; "
            f"Expected: {expected}; Received: {received}; "
            f"Connector: {self.collector}; Line: {point_number}")


@dataclass(frozen=True)
class DeploymentMetadata:
    deployment_id: str = ""
    customer_id: str = ""
    site_id: str = ""
    customer_name: str = ""
    site_name: str = ""
    display_name: str = ""
    timezone: str = "UTC"
    currency: str = ""
    region: str = ""

    @classmethod
    def from_config(cls, config):
        identity = config.get("identity") or {}
        deployment = config.get("deployment") or {}
        writer = config.get("writer") or {}
        return cls(
            deployment_id=str(
                writer.get("deployment_id")
                or config.get("deployment_id") or "").strip(),
            customer_id=str(
                writer.get("customer_id")
                or config.get("customer_id") or "").strip(),
            site_id=str(
                writer.get("site_id")
                or config.get("site_id") or "").strip(),
            customer_name=str(
                writer.get("customer_name")
                or identity.get("customer_name") or "").strip(),
            site_name=str(
                writer.get("site_name")
                or config.get("site_name")
                or identity.get("site_name") or "").strip(),
            display_name=str(
                deployment.get("display_name")
                or deployment.get("name")
                or identity.get("display_name") or "").strip(),
            timezone=str(
                deployment.get("timezone")
                or config.get("timezone") or "UTC").strip(),
            currency=str(deployment.get("currency") or "").strip(),
            region=str(deployment.get("region") or "").strip(),
        )

    def apply(self, tags):
        """Replace connector identity while retaining source attribution."""
        result = dict(tags or {})
        source_site_id = result.get("source_site_id") or result.get("site_id")
        source_site_name = (
            result.get("source_site_name") or result.get("site_name")
            or result.get("site"))
        source_customer_id = (
            result.get("source_customer_id") or result.get("customer_id")
            or result.get("customer"))
        if self.site_id and source_site_id and source_site_id != self.site_id:
            result["source_site_id"] = str(source_site_id)
        if self.site_id and source_site_name and source_site_name not in {
                self.site_id, self.site_name}:
            result["source_site_name"] = str(source_site_name)
        if (self.customer_id and source_customer_id
                and source_customer_id != self.customer_id):
            result["source_customer_id"] = str(source_customer_id)
        if self.deployment_id:
            result["deployment_id"] = self.deployment_id
        if self.customer_id:
            result["customer_id"] = self.customer_id
            result["customer"] = self.customer_id
        if self.site_id:
            result["site_id"] = self.site_id
            result["site"] = self.site_id
        if self.customer_name:
            result["customer_name"] = self.customer_name
        if self.site_name:
            result["site_name"] = self.site_name
        return result


# Fields omitted here remain valid and retain their native scalar type. The
# registry controls fields whose Influx type must be stable across collectors.
FIELD_TYPES = {
    "availability": {
        "total": "integer",
        "offline": "integer",
    },
    "collector_health": {
        "duration_ms": "integer",
        "points_generated": "integer",
        "points_written": "integer",
        "retry_count": "integer",
        "api_latency_ms": "integer",
        "api_requests": "integer",
        "error_count": "integer",
    },
    "device": {
        "uptime_seconds": "integer",
        "cpu_count": "integer",
    },
    "performance": {
        "cpu_percent": "integer",
        "active_sessions": "integer",
        "max_sessions": "integer",
        "printer_count": "integer",
        "held_jobs": "integer",
        "error_count": "integer",
    },
}


def coerce_integer(value):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return round(value)
    if isinstance(value, str):
        return round(float(value.strip()))
    raise TypeError


def coerce_float(value):
    if isinstance(value, bool):
        return float(int(value))
    result = float(value)
    if not math.isfinite(result):
        raise TypeError
    return result


def coerce_boolean_integer(value):
    if isinstance(value, bool):
        return int(value)
    text = str(value).strip().casefold()
    if text in {"1", "true", "yes", "on"}:
        return 1
    if text in {"0", "false", "no", "off"}:
        return 0
    raise ValueError("value is not boolean")


def timestamp_ns(value):
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise TypeError("boolean is not a timestamp")
    if isinstance(value, (int, float)):
        return int(value)
    parsed = datetime.fromisoformat(
        str(value).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1_000_000_000)


def normalize_point(point, metadata, point_number=1):
    measurement = str(point.get("measurement") or "").strip()
    if not measurement:
        raise TelemetryValidationError(
            "", "measurement", "non-empty text", "empty", "unknown",
            point_number)
    tags = metadata.apply(point.get("tags", {}))
    collector = str(tags.get("collector") or "unknown")
    fields = {}
    for field, value in sorted((point.get("fields") or {}).items()):
        expected = FIELD_TYPES.get(measurement, {}).get(field)
        if expected is None:
            fields[field] = value
            continue
        try:
            fields[field] = (
                coerce_integer(value)
                if expected == "integer" else coerce_float(value))
        except (TypeError, ValueError, OverflowError):
            raise TelemetryValidationError(
                measurement, field, expected, type(value).__name__,
                collector, point_number) from None
    try:
        timestamp = timestamp_ns(point.get("timestamp"))
    except (TypeError, ValueError, OverflowError):
        raise TelemetryValidationError(
            measurement, "timestamp", "nanosecond or ISO-8601 timestamp",
            type(point.get("timestamp")).__name__, collector,
            point_number) from None
    result = {**point, "measurement": measurement, "tags": tags,
              "fields": fields}
    if timestamp is None:
        result.pop("timestamp", None)
    else:
        result["timestamp"] = timestamp
    return result
