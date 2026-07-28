"""Framework configuration loading with strict environment expansion."""
import os
import re
import warnings
from pathlib import Path

import yaml
from .configuration import (
    apply_connector_overlay,
    materialize_runtime_configuration,
)

ENVIRONMENT = re.compile(r"^\$\{([A-Z][A-Z0-9_]*)\}$")
EXECUTIONS = {"central", "edge", "either"}


def _expand(value):
    if isinstance(value, dict): return {key: _expand(item) for key, item in value.items()}
    if isinstance(value, list): return [_expand(item) for item in value]
    if isinstance(value, str) and (match := ENVIRONMENT.match(value)):
        name = match.group(1)
        return os.getenv(name, "")
    return value


def load_config(path):
    candidate = Path(path)
    if not os.getenv("ITP_PROFILE") and candidate.name == "config.yml" and candidate.parent.name == "discovery":
        warnings.warn("discovery/config.yml is deprecated; select a deployment profile",
                      DeprecationWarning, stacklevel=2)
    try: value = yaml.safe_load(Path(path).read_text())
    except (OSError, yaml.YAMLError) as exc: raise ValueError(f"invalid configuration {path}: {exc}") from exc
    if not isinstance(value, dict): raise ValueError("configuration must be a YAML mapping")
    value, _ = apply_connector_overlay(
        value, candidate, root=Path(__file__).resolve().parents[1])
    value = _expand(value)
    from .connector_registry import ConnectorMetadataRegistry
    value = materialize_runtime_configuration(
        value,
        ConnectorMetadataRegistry.load(
            Path(__file__).resolve().parents[1], validation_mode="runtime"),
        os.environ)
    if os.getenv("ITP_DEPLOYMENT_ID"):
        value["deployment_id"] = os.environ["ITP_DEPLOYMENT_ID"]
    writer = value.setdefault("writer", {})
    for name, environment_name in (
            ("url", "INFLUXDB_HOST"),
            ("token", "INFLUXDB_TOKEN"),
            ("database", "INFLUXDB_BUCKET"),
            ("deployment_id", "ITP_DEPLOYMENT_ID")):
        environment_value = os.getenv(environment_name, "")
        if environment_value:
            writer[name] = environment_value
    collectors = value.get("collectors", {})
    if not isinstance(collectors, dict): raise ValueError("collectors configuration must be a mapping")
    for name, settings in collectors.items():
        if not isinstance(settings, dict): raise ValueError(f"collector {name} configuration must be a mapping")
        execution = settings.get("execution", "either")
        if execution not in EXECUTIONS:
            raise ValueError(f"collector {name} has unsupported execution placement: {execution}")
    runtime = os.getenv("ITP_RUNTIME_DIR")
    sites = os.getenv("SITES_CONFIG")
    if runtime:
        base = Path(runtime)
        defaults = {
            "inventory": {},
            "infrastructure": {
                "inventory_path": str(base / "inventory"),
                "operations_path": str(base / "operations"),
                "output_path": str(base / "infrastructure"),
                "dashboard_path": str(base / "dashboard"),
                "sites_output": str(base / "sites"),
            },
            "operations": {
                "inventory_path": str(base / "inventory"),
                "output_path": str(base / "operations"),
                "dashboard_output": str(base / "dashboard/grafana/infrastructure-overview.json"),
                "capability_registry": str(base / "dashboard/managed/registry.json"),
            },
            "services": {
                "infrastructure_state": str(base / "infrastructure/state.json"),
                "operations_state": str(base / "operations/operations.json"),
                "capability_registry": str(base / "dashboard/managed/registry.json"),
                "output_path": str(base / "services"),
            },
            "wallboard": {
                "infrastructure_state": str(base / "infrastructure/state.json"),
                "operations_state": str(base / "operations/operations.json"),
                "sites_state": str(base / "sites/sites.json"),
                "summary_output": str(base / "dashboard/wallboard-summary.json"),
                "dashboard_output": str(base / "dashboard/operations/operations-wallboard.json"),
                "capability_registry": str(base / "dashboard/managed/registry.json"),
                "service_health": str(base / "services/service-health.json"),
            },
            "state_history": {
                "store_path": str(base / "state-history"),
            },
        }
        for section, settings in defaults.items():
            target = value.setdefault(section, {})
            for key, setting in settings.items():
                target.setdefault(key, setting)
        if sites:
            value["infrastructure"].setdefault("sites_config", sites)
            value["services"].setdefault("sites_config", sites)
    return value
