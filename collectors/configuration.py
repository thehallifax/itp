"""Deterministic connector configuration resolution and safe diagnostics."""
from __future__ import annotations

import os
import warnings
from dataclasses import dataclass
from pathlib import Path

import yaml

from itp_profiles.settings import load_env_file


class ConfigurationError(ValueError):
    pass


def deep_merge(base, override):
    result = dict(base or {})
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def read_yaml(path):
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        value = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ConfigurationError(
            f"invalid local connector configuration: {path.name}") from exc
    if not isinstance(value, dict):
        raise ConfigurationError(
            f"local connector configuration must be a mapping: {path.name}")
    return value


def connector_overlay_path(config_path, root=None, environment=None):
    """Resolve one explicit local overlay; never search arbitrary directories."""
    environment = os.environ if environment is None else environment
    explicit = str(environment.get("ITP_CONNECTORS_CONFIG") or "").strip()
    if explicit:
        return Path(explicit)
    config_path = Path(config_path)
    if config_path.parent.parent.name == "profiles":
        return config_path.parent / "connectors.local.yml"
    root = Path(root or config_path.resolve().parents[1])
    return root / "config/connectors.local.yml"


def apply_connector_overlay(config, config_path, *, root=None,
                            environment=None):
    path = connector_overlay_path(
        config_path, root=root, environment=environment)
    return deep_merge(config, read_yaml(path)), path


def parse_bool(value):
    if isinstance(value, bool):
        return value
    text = str(value).strip().casefold()
    if text in {"true", "1", "yes", "on"}:
        return True
    if text in {"false", "0", "no", "off"}:
        return False
    raise ConfigurationError("value must be a boolean")


def parse_bool_default(value, default=True):
    """Parse an optional boolean while preserving an explicit default."""
    if value in (None, ""):
        return default
    return parse_bool(value)


def parse_int(value, *, minimum=None, maximum=None):
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError("value must be an integer") from exc
    if minimum is not None and result < minimum:
        raise ConfigurationError(f"value must be at least {minimum}")
    if maximum is not None and result > maximum:
        raise ConfigurationError(f"value must be at most {maximum}")
    return result


def resolve_environment_value(environment, canonical, aliases=()):
    """Return a canonical/legacy value without exposing it in warnings."""
    if str(environment.get(canonical) or "").strip():
        return environment[canonical], canonical, False
    for alias in aliases:
        if str(environment.get(alias) or "").strip():
            warnings.warn(
                f"{alias} is deprecated; use {canonical}.",
                DeprecationWarning, stacklevel=2)
            return environment[alias], alias, True
    return "", "", False


def materialize_runtime_configuration(config, registry, environment):
    """Inject resolved runtime values at the process/configuration boundary.

    The returned mapping is an in-memory copy. Secret values are never written
    back to tracked or local configuration files.
    """
    result = deep_merge({}, config)
    connectors = result.setdefault("collectors", {})
    for connector in registry.all():
        settings = connectors.get(connector.id)
        if settings is None:
            continue
        if not isinstance(settings, dict):
            continue
        for field in connector.credential_fields:
            if _present(settings.get(field["id"])):
                continue
            pointer = str(settings.get(f"{field['id']}_env") or "").strip()
            canonical = pointer or field["env"]
            aliases = field.get("env_aliases", []) if not pointer else []
            value, _, _ = resolve_environment_value(
                environment, canonical, aliases)
            if _present(value):
                settings[field["id"]] = value

    endpoints = (result.get("virtualisation") or {}).get("endpoints") or []
    metadata = {item.id: item for item in registry.all()}
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            continue
        connector = metadata.get(str(endpoint.get("provider") or "").casefold())
        if not connector:
            continue
        for field in connector.credential_fields:
            if _present(endpoint.get(field["id"])):
                continue
            pointer = str(endpoint.get(f"{field['id']}_env") or "").strip()
            canonical = pointer or field["env"]
            aliases = field.get("env_aliases", []) if not pointer else []
            value, _, _ = resolve_environment_value(
                environment, canonical, aliases)
            if _present(value):
                endpoint[field["id"]] = value
    return result


def _path(value, dotted):
    current = value
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _present(value):
    if value in (None, ""):
        return False
    if isinstance(value, str) and value.strip().startswith("${") \
            and value.strip().endswith("}"):
        return False
    return True


@dataclass(frozen=True)
class ResolvedSetting:
    name: str
    configured: bool
    source: str
    secret: bool
    deprecated_alias: str = ""

    def to_dict(self):
        return {
            "name": self.name,
            "status": "configured" if self.configured else "missing",
            "source": self.source or "none",
            "secret": self.secret,
            "deprecated_alias": self.deprecated_alias,
        }


class ConfigurationResolver:
    """Resolve connector readiness using an explicit, testable source order."""

    def __init__(self, registry, tracked_config, *, local_config=None,
                 process_environment=None, profile_environment=None,
                 root_environment=None, defaults=None):
        self.registry = registry
        self.tracked = tracked_config or {}
        self.local = local_config or {}
        self.process = dict(process_environment or {})
        self.profile_env = dict(profile_environment or {})
        self.root_env = dict(root_environment or {})
        self.defaults = defaults or {}

    @classmethod
    def root(cls, root, registry, *, process_environment=None,
             config_path=None):
        root = Path(root)
        config_path = Path(config_path or root / "discovery/config.yml")
        tracked = read_yaml(
            root / "discovery/config.example.yml")
        deployment = read_yaml(config_path)
        root_env = load_env_file(root / ".env")
        for path in sorted((root / "secrets").glob("*.env")):
            root_env.update(load_env_file(path))
        local_path = connector_overlay_path(
            config_path, root=root,
            environment={**root_env, **(process_environment or {})})
        return cls(
            registry, deep_merge(tracked, deployment),
            local_config=read_yaml(local_path),
            process_environment=process_environment or {},
            root_environment=root_env)

    @classmethod
    def profile(cls, profile, registry, *, process_environment=None):
        tracked = read_yaml(profile.paths.discovery)
        local_path = profile.paths.discovery.parent / "connectors.local.yml"
        profile_env = load_env_file(
            profile.paths.profile_root / ".env")
        for path in sorted(profile.paths.secrets.glob("*.env")):
            profile_env.update(load_env_file(path))
        root_env = load_env_file(profile.paths.root / ".env")
        return cls(
            registry, tracked, local_config=read_yaml(local_path),
            process_environment=process_environment or {},
            profile_environment=profile_env,
            root_environment=root_env)

    def _environment(self, canonical, aliases):
        for values, source in (
                (self.process, "process environment"),
                (self.profile_env, "profile environment"),
                (self.root_env, "root environment")):
            value, name, deprecated = resolve_environment_value(
                values, canonical, aliases)
            if name:
                return value, source, name if deprecated else ""
        return None, "", ""

    def _configured_value(self, connector, field):
        canonical = field["env"]
        aliases = tuple(field.get("env_aliases") or ())
        value, source, deprecated = self._environment(canonical, aliases)
        if value not in (None, ""):
            return value, source, deprecated
        candidates = (
            (self.local, "deployment local configuration"),
            (self.tracked, "tracked deployment configuration"),
            (self.defaults, "connector default"),
        )
        for values, label in candidates:
            value = _path(values, f"collectors.{connector.id}.{field['id']}")
            if _present(value):
                return value, label, ""
        return None, "", ""

    def evaluate(self):
        combined = deep_merge(self.tracked, self.local)
        results = []
        for connector in self.registry.all():
            settings = _path(combined, f"collectors.{connector.id}") or {}
            enabled = settings.get("enabled") is True
            fields = []
            ready = True
            for field in connector.credential_fields:
                value, source, deprecated = self._configured_value(
                    connector, field)
                configured = _present(value)
                if enabled and field["required"] and not configured:
                    ready = False
                fields.append(ResolvedSetting(
                    f"{connector.id}.{field['id']}", configured, source,
                    field["secret"], deprecated).to_dict())
            nonsecret = []
            for name in connector.configuration_fields:
                value = _path(self.local, name)
                source = "deployment local configuration"
                if value is None:
                    value = _path(self.tracked, name)
                    source = "tracked deployment configuration"
                nonsecret.append(ResolvedSetting(
                    name, _present(value), source if _present(value)
                    else "", False).to_dict())
            results.append({
                "connector": connector.id,
                "display_name": connector.display_name,
                "enabled": enabled,
                "ready": ready if enabled else True,
                "settings": fields + nonsecret,
                "tls_verification": settings.get("verify_tls", "default"),
                "site": settings.get("site") or combined.get("site") or "",
            })
        enabled = [value for value in results if value["enabled"]]
        return {
            "ready": all(value["ready"] for value in enabled),
            "enabled_connectors": [value["connector"] for value in enabled],
            "connectors": results,
        }
