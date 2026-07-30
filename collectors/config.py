"""Framework configuration loading with strict environment expansion."""
import os
import re
import warnings
from pathlib import Path

import yaml
from itp_profiles.identity import IdentityError, IdentityResolver
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
    deployment_id = str(
        os.getenv("ITP_DEPLOYMENT_ID") or value.get("deployment_id")
        or value.get("customer") or "legacy").strip()
    customer_id = str(
        os.getenv("ITP_CUSTOMER_ID") or value.get("customer_id")
        or value.get("customer") or deployment_id).strip()
    configured_sites = os.getenv("SITES_CONFIG") or \
        os.getenv("ITP_SITES_CONFIG")
    profile_sites = candidate.parent / "sites.yml" \
        if candidate.parent.parent.name == "profiles" else None
    profile_local_sites = candidate.parent / "sites.local.yml" \
        if profile_sites else None
    active_profile_sites = (
        Path(configured_sites)
        if configured_sites and
        os.getenv("ITP_PROFILE") == candidate.parent.name
        else profile_local_sites
        if profile_local_sites and profile_local_sites.is_file()
        else profile_sites)
    sites_path = Path(
        active_profile_sites or
        (profile_sites if profile_sites and profile_sites.is_file()
         else Path(__file__).resolve().parents[1] / "config/sites.yml"))
    try:
        identity = IdentityResolver.from_sites_file(
            deployment_id, customer_id, sites_path,
            customer_name=os.getenv("ITP_CUSTOMER_NAME", ""))
    except (IdentityError, OSError, yaml.YAMLError) as exc:
        raise ValueError(f"invalid canonical identity configuration: {exc}") \
            from exc
    value["deployment_id"] = identity.deployment_id
    value["customer_id"] = identity.customer_id
    value["customer"] = identity.customer_id
    value["identity"] = {
        "deployment_id": identity.deployment_id,
        "customer_id": identity.customer_id,
        "customer_name": identity.customer_name,
    }
    if value.get("site"):
        configured_site = str(value["site"])
        default_site = identity.resolve_site(value["site"])
        if configured_site != default_site.site_id:
            warnings.warn(
                f"legacy site alias {configured_site!r} was normalized to "
                f"{default_site.site_id!r}; update the profile configuration",
                DeprecationWarning, stacklevel=2)
        value["site_id"] = default_site.site_id
        value["site"] = default_site.site_id
        value["site_name"] = default_site.site_name
    for settings in (value.get("collectors") or {}).values():
        if not isinstance(settings, dict) or not settings.get("enabled") \
                or not settings.get("site"):
            continue
        configured_site = str(settings["site"])
        resolved = identity.resolve_site(settings["site"])
        if configured_site != resolved.site_id:
            warnings.warn(
                f"legacy connector site alias {configured_site!r} was "
                f"normalized to {resolved.site_id!r}; update the profile "
                "configuration", DeprecationWarning, stacklevel=2)
        settings.update({
            "customer_id": identity.customer_id,
            "customer": identity.customer_id,
            "site_id": resolved.site_id,
            "site": resolved.site_id,
            "site_name": resolved.site_name,
            "customer_name": identity.customer_name,
        })
    from .connector_registry import ConnectorMetadataRegistry
    value = materialize_runtime_configuration(
        value,
        ConnectorMetadataRegistry.load(
            Path(__file__).resolve().parents[1], validation_mode="runtime"),
        os.environ)
    writer = value.setdefault("writer", {})
    for name, environment_name in (
            ("url", "INFLUXDB_HOST"),
            ("token", "INFLUXDB_TOKEN"),
            ("database", "INFLUXDB_BUCKET"),
            ("deployment_id", "ITP_DEPLOYMENT_ID")):
        environment_value = os.getenv(environment_name, "")
        if environment_value:
            writer[name] = environment_value
    writer.setdefault("deployment_id", identity.deployment_id)
    writer.setdefault("customer_id", identity.customer_id)
    writer.setdefault("customer_name", identity.customer_name)
    if value.get("site_id"):
        writer.setdefault("site_id", value["site_id"])
        writer.setdefault("site_name", value.get("site_name", ""))
    ca_bundle = os.getenv("ITP_CA_BUNDLE", "").strip()
    if ca_bundle:
        value.setdefault("tls", {})["ca_bundle"] = ca_bundle
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
        dashboard = base / "generated/dashboard"
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
                "dashboard_output": str(dashboard / "grafana/infrastructure-overview.json"),
                "capability_registry": str(dashboard / "managed/registry.json"),
            },
            "services": {
                "infrastructure_state": str(base / "infrastructure/state.json"),
                "operations_state": str(base / "operations/operations.json"),
                "capability_registry": str(dashboard / "managed/registry.json"),
                "output_path": str(base / "services"),
            },
            "wallboard": {
                "infrastructure_state": str(base / "infrastructure/state.json"),
                "operations_state": str(base / "operations/operations.json"),
                "sites_state": str(base / "sites/sites.json"),
                "summary_output": str(dashboard / "wallboard-summary.json"),
                "dashboard_output": str(dashboard / "operations/operations-wallboard.json"),
                "capability_registry": str(dashboard / "managed/registry.json"),
                "service_health": str(base / "services/service-health.json"),
            },
            "state_history": {
                "store_path": str(base / "state-history"),
            },
        }
        for section, settings in defaults.items():
            target = value.setdefault(section, {})
            for key, setting in settings.items():
                current = target.get(key)
                # Tracked templates use /app/runtime as a portable container
                # placeholder. Rebase only those defaults to the selected
                # deployment root; preserve genuinely custom paths.
                if isinstance(current, str) and current.startswith(
                        "/app/runtime/dashboard/"):
                    target[key] = str(
                        dashboard /
                        current.removeprefix("/app/runtime/dashboard/"))
                elif isinstance(current, str) and current.startswith(
                        "/app/runtime/"):
                    target[key] = str(
                        base / current.removeprefix("/app/runtime/"))
                else:
                    target.setdefault(key, setting)
        if sites:
            value["infrastructure"].setdefault("sites_config", sites)
            value["services"].setdefault("sites_config", sites)
    return value
