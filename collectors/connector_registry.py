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
PROMPT_VALUE_TYPES = frozenset({"host", "url", "uuid", "text", "secret"})
PROMPT_NORMALIZERS = frozenset({
    "", "https-host", "https-origin", "papercut-health-origin",
})
RUNTIME_MODES = frozenset({"central", "edge", "cloud"})


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
    validation_adapter: str = ""
    configuration_checker: str = ""
    health_adapter: str = ""
    remediation_command: str = ""
    dashboard_manifest: str = ""
    configuration_prompts: tuple[dict, ...] = ()
    runtime_modes: tuple[str, ...] = ("central", "edge")

    @property
    def configuration_namespace(self):
        if self.id in {"vmware", "hyperv", "proxmox"}:
            return "virtualisation.endpoints"
        return f"collectors.{self.id}"

    @property
    def validation_requirements(self):
        return tuple(
            field["id"] for field in self.credential_fields
            if field["required"])

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
        value["configuration_prompts"] = list(self.configuration_prompts)
        value["runtime_modes"] = list(self.runtime_modes)
        value["aliases"] = list(self.aliases)
        value["manual_only"] = self.manual_only
        value["configuration_namespace"] = self.configuration_namespace
        value["validation_requirements"] = list(
            self.validation_requirements)
        return value


class ConnectorMetadataRegistry:
    """Validated connector catalogue; loading performs no runtime imports."""

    def __init__(self, root, connectors=None, *, validation_mode="strict"):
        if connectors is None:
            if isinstance(root, (str, Path)):
                # Compatibility with the original root-only constructor.
                # Metadata still comes from the authoritative tracked manifest,
                # while documentation and implementation references are
                # validated relative to the supplied repository root.
                supplied_manifest = (
                    Path(root) / "collectors/connector-registry.yml")
                connectors = self._read_connectors(
                    supplied_manifest if supplied_manifest.is_file()
                    else Path(__file__).resolve().parents[1]
                    / "collectors/connector-registry.yml")
            else:
                # Compatibility with callers that supplied only an iterable of
                # metadata and relied on the package repository as the root.
                connectors, root = root, Path(__file__).resolve().parents[1]
        self.root = Path(root).resolve()
        if validation_mode not in {"strict", "runtime"}:
            raise ValueError(
                f"unsupported connector registry validation mode: "
                f"{validation_mode}")
        self.validation_mode = validation_mode
        self._connectors = tuple(sorted(connectors, key=lambda value: value.id))
        self._validate()
        self._lookup = {
            key: connector for connector in self._connectors
            for key in (connector.id, *connector.aliases)}

    @classmethod
    def _read_connectors(cls, path):
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
                    configuration_prompts=tuple(
                        raw.get("configuration_prompts") or []),
                    secret_handling=dict(raw.get("secret_handling") or {}),
                    capabilities=dict(raw.get("capabilities") or {}),
                    documentation=str(raw["documentation"]),
                    implementation=str(raw.get("implementation") or ""),
                    aliases=tuple(sorted(set(raw.get("aliases") or []))),
                    notes=str(raw.get("notes") or ""),
                    validation_adapter=str(
                        raw.get("validation_adapter") or ""),
                    configuration_checker=str(
                        raw.get("configuration_checker") or ""),
                    health_adapter=str(raw.get("health_adapter") or ""),
                    remediation_command=str(
                        raw.get("remediation_command")
                        or "python -m collectors connectors inspect "
                        + str(raw["id"])),
                    dashboard_manifest=str(
                        raw.get("dashboard_manifest") or ""),
                    runtime_modes=tuple(sorted(set(
                        raw.get("runtime_modes") or ("central", "edge")))),
                ))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("invalid connector registry entry") from exc
        return connectors

    @classmethod
    def load(cls, root=None, path=None, *, validation_mode="strict"):
        root = Path(root or Path(__file__).resolve().parents[1]).resolve()
        path = Path(path or root / "collectors/connector-registry.yml")
        return cls(
            root, cls._read_connectors(path),
            validation_mode=validation_mode)

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
            if (not connector.runtime_modes
                    or set(connector.runtime_modes) - RUNTIME_MODES):
                raise ValueError(
                    f"connector {connector.id} has invalid runtime modes")
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
                        or not isinstance(field.get("secret"), bool)
                        or not isinstance(field.get("env_aliases", []), list)
                        or any(not isinstance(alias, str) or not alias
                               for alias in field.get("env_aliases", []))):
                    raise ValueError(
                        f"connector {connector.id} has invalid credential metadata")
                self._validate_prompt(
                    connector.id, field.get("prompt", {}))
                canonical = field.get("configuration_field", "")
                if canonical and canonical not in connector.configuration_fields:
                    raise ValueError(
                        f"connector {connector.id} credential references an "
                        f"unknown configuration field: {canonical}")
                credential_ids.append(field["id"])
            if len(credential_ids) != len(set(credential_ids)):
                raise ValueError(
                    f"connector {connector.id} has duplicate credential fields")
            prompt_fields = []
            for field in connector.configuration_prompts:
                if not isinstance(field, dict) or (
                        field.get("field") not in connector.configuration_fields):
                    raise ValueError(
                        f"connector {connector.id} has invalid configuration "
                        "prompt metadata")
                self._validate_prompt(connector.id, field)
                prompt_fields.append(field["field"])
            if len(prompt_fields) != len(set(prompt_fields)):
                raise ValueError(
                    f"connector {connector.id} has duplicate configuration prompts")
            if connector.guided_setup != (
                    connector.configuration_mode == "guided"):
                raise ValueError(
                    f"connector {connector.id} guided setup metadata conflicts")
            if connector.secret_handling.get("scope") not in {
                    "root", "profile", "root-or-profile", "none"}:
                raise ValueError(
                    f"connector {connector.id} has invalid secret handling scope")
            if self.validation_mode == "strict":
                if not (self.root / connector.documentation).is_file():
                    raise ValueError(
                        f"connector {connector.id} documentation does not exist: "
                        f"{connector.documentation}")
                if connector.dashboard_manifest and not (
                        self.root / connector.dashboard_manifest).is_file():
                    raise ValueError(
                        f"connector {connector.id} dashboard manifest does not "
                        f"exist: {connector.dashboard_manifest}")
                for template in connector.secret_handling.get(
                        "templates", []):
                    if not (self.root / template).is_file():
                        raise ValueError(
                            f"connector {connector.id} secret template does not "
                            f"exist: {template}")
            self._validate_reference(connector)

    @staticmethod
    def _validate_prompt(connector_id, prompt):
        if not prompt:
            return
        if not isinstance(prompt, dict):
            raise ValueError(
                f"connector {connector_id} has invalid prompt metadata")
        for key in ("label", "example", "help", "default"):
            if key in prompt and not isinstance(prompt[key], str):
                raise ValueError(
                    f"connector {connector_id} has invalid prompt metadata")
        value_type = prompt.get("value_type", "text")
        if value_type not in PROMPT_VALUE_TYPES:
            raise ValueError(
                f"connector {connector_id} has invalid prompt value type")
        if "sensitive" in prompt and not isinstance(
                prompt["sensitive"], bool):
            raise ValueError(
                f"connector {connector_id} has invalid prompt sensitivity")
        if prompt.get("normalizer", "") not in PROMPT_NORMALIZERS:
            raise ValueError(
                f"connector {connector_id} has invalid prompt normalizer")

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
