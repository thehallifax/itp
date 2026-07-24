"""Declarative, side-effect-free connector metadata registry."""
from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml


DOMAINS = frozenset({
    "wireless", "switching", "firewall", "virtualisation", "printing",
    "internet", "servers", "power", "environmental", "identity", "operations",
})
DEPLOYMENT_TYPES = frozenset(
    {"Home Lab", "School", "Business", "MSP", "Enterprise"})
IMPLEMENTATION_STATUSES = frozenset(
    {"supported", "profile-only", "experimental", "incomplete"})
CONFIGURATION_MODES = frozenset(
    {"manual", "profile-manual", "guided", "internal"})
CAPABILITIES = ("validation", "doctor", "status", "demo_fixture")


@dataclass(frozen=True)
class ConnectorMetadata:
    id: str
    display_name: str
    vendor: str
    domains: tuple[str, ...]
    deployment_types: tuple[str, ...]
    implementation_status: str
    guided_setup: bool
    configuration_mode: str
    credential_fields: tuple[dict, ...]
    configuration_fields: tuple[str, ...]
    secret_handling: dict
    capabilities: dict
    documentation: str
    implementation: str
    aliases: tuple[str, ...]
    notes: str

    @property
    def manual_only(self):
        return not self.guided_setup and self.configuration_mode in {
            "manual", "profile-manual"}

    def to_dict(self):
        value = asdict(self)
        value["domains"] = list(self.domains)
        value["deployment_types"] = list(self.deployment_types)
        value["credential_fields"] = list(self.credential_fields)
        value["configuration_fields"] = list(self.configuration_fields)
        value["aliases"] = list(self.aliases)
        value["manual_only"] = self.manual_only
        return value


class ConnectorMetadataRegistry:
    """Validated connector catalogue; loading performs no runtime imports."""

    def __init__(self, root, connectors):
        self.root = Path(root).resolve()
        self._connectors = tuple(sorted(connectors, key=lambda value: value.id))
        self._validate()
        self._lookup = {
            key: connector for connector in self._connectors
            for key in (connector.id, *connector.aliases)}

    @classmethod
    def load(cls, root=None, path=None):
        root = Path(root or Path(__file__).resolve().parents[1]).resolve()
        path = Path(path or root / "collectors/connector-registry.yml")
        try:
            payload = yaml.safe_load(path.read_text())
        except (OSError, yaml.YAMLError) as exc:
            raise ValueError(f"invalid connector registry {path}: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError("connector registry must use schema_version 1")
        values = payload.get("connectors")
        if not isinstance(values, list):
            raise ValueError("connector registry connectors must be a list")
        connectors = []
        for raw in values:
            if not isinstance(raw, dict):
                raise ValueError("connector registry entries must be mappings")
            try:
                connectors.append(ConnectorMetadata(
                    id=str(raw["id"]),
                    display_name=str(raw["display_name"]),
                    vendor=str(raw["vendor"]),
                    domains=tuple(sorted(set(raw.get("domains") or []))),
                    deployment_types=tuple(sorted(set(
                        raw.get("deployment_types") or []))),
                    implementation_status=str(raw["implementation_status"]),
                    guided_setup=bool(raw.get("guided_setup", False)),
                    configuration_mode=str(raw["configuration_mode"]),
                    credential_fields=tuple(raw.get("credential_fields") or []),
                    configuration_fields=tuple(sorted(set(
                        raw.get("configuration_fields") or []))),
                    secret_handling=dict(raw.get("secret_handling") or {}),
                    capabilities=dict(raw.get("capabilities") or {}),
                    documentation=str(raw["documentation"]),
                    implementation=str(raw.get("implementation") or ""),
                    aliases=tuple(sorted(set(raw.get("aliases") or []))),
                    notes=str(raw.get("notes") or ""),
                ))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("invalid connector registry entry") from exc
        return cls(root, connectors)

    def _validate_reference(self, connector):
        if not connector.implementation or ":" not in connector.implementation:
            raise ValueError(
                f"connector {connector.id} requires path:symbol implementation")
        relative, symbol = connector.implementation.split(":", 1)
        path = self.root / relative
        if not path.is_file():
            raise ValueError(
                f"connector {connector.id} implementation does not exist: {relative}")
        try:
            tree = ast.parse(path.read_text())
        except (OSError, SyntaxError) as exc:
            raise ValueError(
                f"connector {connector.id} implementation is invalid") from exc
        declared = {
            node.name for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))}
        if symbol not in declared:
            raise ValueError(
                f"connector {connector.id} implementation symbol is missing: {symbol}")

    def _validate(self):
        ids = [value.id for value in self._connectors]
        if any(not value or value != value.casefold() for value in ids):
            raise ValueError("connector IDs must be non-empty lowercase values")
        if len(ids) != len(set(ids)):
            raise ValueError("connector registry contains duplicate IDs")
        aliases = [alias for value in self._connectors for alias in value.aliases]
        if any(not alias or alias != alias.casefold() for alias in aliases):
            raise ValueError("connector aliases must be non-empty lowercase values")
        if len(aliases) != len(set(aliases)):
            raise ValueError("connector registry contains duplicate aliases")
        overlap = set(ids) & set(aliases)
        if overlap:
            raise ValueError(
                "connector aliases conflict with IDs: " + ", ".join(sorted(overlap)))
        for connector in self._connectors:
            unknown_domains = set(connector.domains) - DOMAINS
            if unknown_domains:
                raise ValueError(
                    f"connector {connector.id} has invalid domains: " +
                    ", ".join(sorted(unknown_domains)))
            unknown_types = set(connector.deployment_types) - DEPLOYMENT_TYPES
            if unknown_types or not connector.deployment_types:
                raise ValueError(
                    f"connector {connector.id} has invalid deployment types")
            if connector.implementation_status not in IMPLEMENTATION_STATUSES:
                raise ValueError(
                    f"connector {connector.id} has invalid implementation status")
            if connector.configuration_mode not in CONFIGURATION_MODES:
                raise ValueError(
                    f"connector {connector.id} has invalid configuration mode")
            if set(connector.capabilities) != set(CAPABILITIES) or any(
                    not isinstance(value, bool)
                    for value in connector.capabilities.values()):
                raise ValueError(
                    f"connector {connector.id} has invalid capability metadata")
            credential_ids = []
            for field in connector.credential_fields:
                if (not isinstance(field, dict) or not field.get("id")
                        or not field.get("env")
                        or not isinstance(field.get("required"), bool)
                        or not isinstance(field.get("secret"), bool)):
                    raise ValueError(
                        f"connector {connector.id} has invalid credential metadata")
                credential_ids.append(field["id"])
            if len(credential_ids) != len(set(credential_ids)):
                raise ValueError(
                    f"connector {connector.id} has duplicate credential fields")
            if connector.guided_setup != (
                    connector.configuration_mode == "guided"):
                raise ValueError(
                    f"connector {connector.id} guided setup metadata conflicts")
            if connector.secret_handling.get("scope") not in {
                    "root", "profile", "root-or-profile", "none"}:
                raise ValueError(
                    f"connector {connector.id} has invalid secret handling scope")
            if not (self.root / connector.documentation).is_file():
                raise ValueError(
                    f"connector {connector.id} documentation does not exist: "
                    f"{connector.documentation}")
            for template in connector.secret_handling.get("templates", []):
                if not (self.root / template).is_file():
                    raise ValueError(
                        f"connector {connector.id} secret template does not exist: "
                        f"{template}")
            self._validate_reference(connector)

    def all(self):
        return self._connectors

    def get(self, connector_id):
        try:
            return self._lookup[str(connector_id).casefold()]
        except KeyError as exc:
            raise KeyError(f"unknown connector: {connector_id}") from exc

    def filter(self, *, domain=None, deployment_type=None, guided_setup=None):
        values = self._connectors
        if domain is not None:
            if domain not in DOMAINS:
                raise ValueError(f"unknown infrastructure domain: {domain}")
            values = tuple(value for value in values if domain in value.domains)
        if deployment_type is not None:
            if deployment_type not in DEPLOYMENT_TYPES:
                raise ValueError(f"unknown deployment type: {deployment_type}")
            values = tuple(
                value for value in values
                if deployment_type in value.deployment_types)
        if guided_setup is not None:
            values = tuple(
                value for value in values
                if value.guided_setup is bool(guided_setup))
        return tuple(values)

    def manual_only(self):
        return tuple(value for value in self._connectors if value.manual_only)

    def to_dict(self):
        return {
            "schema_version": 1,
            "connectors": [value.to_dict() for value in self._connectors],
        }
