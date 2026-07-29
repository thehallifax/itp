"""Canonical root-deployment settings and legacy environment migration."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_INFLUX_DATABASE = "local_system"
DEFAULT_INFLUX_ORG = "itp"
DEFAULT_INFLUX_PORT = 8181
DEFAULT_GRAFANA_PORT = 3000
DEFAULT_TIMEZONE = "UTC"
DEFAULT_COLLECTION_INTERVAL = "60s"
LEGACY_INFLUX_PORT = "INFLUXDB_HTTP_PORT"
CANONICAL_INFLUX_PORT = "INFLUXDB_PORT"


class SettingsError(ValueError):
    pass


@dataclass(frozen=True)
class DeploymentSettings:
    database: str
    organization: str
    influx_port: int
    grafana_port: int
    timezone: str
    collection_interval: str
    deployment_id: str
    node_id: str


def load_env_file(path):
    result = {}
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return result
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip("'\"")
    return result


def _required(values, key):
    value = str(values.get(key) or "").strip()
    if not value:
        raise SettingsError(
            f"{key} is blank. Run `./itp setup --force` to repair deployment "
            "configuration.")
    return value


def resolve_influx_port(values, *, warnings=None):
    warnings = warnings if warnings is not None else []
    canonical = str(values.get(CANONICAL_INFLUX_PORT) or "").strip()
    legacy = str(values.get(LEGACY_INFLUX_PORT) or "").strip()
    if canonical and legacy and canonical != legacy:
        raise SettingsError(
            f"{CANONICAL_INFLUX_PORT} and deprecated {LEGACY_INFLUX_PORT} "
            "disagree; keep only INFLUXDB_PORT.")
    if not canonical and legacy:
        warnings.append(
            "INFLUXDB_HTTP_PORT is deprecated; rerun setup to write "
            "INFLUXDB_PORT.")
        canonical = legacy
    if not canonical:
        raise SettingsError(
            "INFLUXDB_PORT is blank. Run `./itp setup --force` to repair "
            "deployment configuration.")
    try:
        result = int(canonical)
    except ValueError as exc:
        raise SettingsError("INFLUXDB_PORT must be a numeric TCP port.") from exc
    if not 1 <= result <= 65535:
        raise SettingsError("INFLUXDB_PORT must be between 1 and 65535.")
    return result


def resolve_settings(values, *, warnings=None):
    warnings = warnings if warnings is not None else []
    influx_port = resolve_influx_port(values, warnings=warnings)
    try:
        grafana_port = int(_required(values, "GRAFANA_PORT"))
    except ValueError as exc:
        raise SettingsError("GRAFANA_PORT must be a numeric TCP port.") from exc
    if not 1 <= grafana_port <= 65535:
        raise SettingsError("GRAFANA_PORT must be between 1 and 65535.")
    if grafana_port == influx_port:
        raise SettingsError("GRAFANA_PORT and INFLUXDB_PORT must differ.")
    return DeploymentSettings(
        database=_required(values, "INFLUXDB_BUCKET"),
        organization=_required(values, "INFLUXDB_ORG"),
        influx_port=influx_port,
        grafana_port=grafana_port,
        timezone=_required(values, "TZ"),
        collection_interval=_required(
            values, "TELEGRAF_COLLECTION_INTERVAL"),
        deployment_id=_required(values, "ITP_DEPLOYMENT_ID"),
        node_id=_required(values, "INFLUXDB_NODE_ID"),
    )


def fresh_defaults():
    return {
        "INFLUXDB_BUCKET": DEFAULT_INFLUX_DATABASE,
        "INFLUXDB_ORG": DEFAULT_INFLUX_ORG,
        "INFLUXDB_PORT": str(DEFAULT_INFLUX_PORT),
        "GRAFANA_PORT": str(DEFAULT_GRAFANA_PORT),
        "TZ": DEFAULT_TIMEZONE,
        "TELEGRAF_COLLECTION_INTERVAL": DEFAULT_COLLECTION_INTERVAL,
    }
