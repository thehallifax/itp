"""Sanitised, runtime-only deployment creation and management."""
from __future__ import annotations

import getpass
import json
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from collectors.configuration import ConfigurationResolver
from collectors.connector_registry import ConnectorMetadataRegistry
from collectors.writer import atomic_write


class RuntimeDeploymentError(ValueError):
    """Actionable deployment configuration failure."""


def slugify(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", str(value).casefold()).strip("-")
    if not value:
        raise RuntimeDeploymentError("deployment name must contain letters or numbers")
    return value[:63]


def _port_available(address: str, port: int) -> bool:
    host = "127.0.0.1" if address in {"", "0.0.0.0", "::"} else address
    with socket.socket() as connection:
        connection.settimeout(0.2)
        return connection.connect_ex((host, int(port))) != 0


def _secret() -> str:
    return secrets.token_urlsafe(36)


def normalize_onboarding_value(value: str, normalizer: str = "") -> str:
    """Normalize a registry-described prompt without network side effects."""
    value = str(value or "").strip()
    if not value or not normalizer:
        return value
    if normalizer == "https-origin" and "://" not in value:
        raise RuntimeDeploymentError("enter a complete HTTPS URL, including https://")
    if "://" not in value:
        value = "https://" + value
    parsed = urlsplit(value)
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        raise RuntimeDeploymentError("the endpoint must use HTTPS and include a hostname")
    try:
        parsed.port
    except ValueError as exc:
        raise RuntimeDeploymentError(
            "the endpoint contains an invalid port") from exc
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeDeploymentError(
            "the endpoint must not contain credentials, a query, or a fragment")
    path = parsed.path.rstrip("/")
    if normalizer == "papercut-health-origin" and path == "/api/health":
        path = ""
    elif path:
        raise RuntimeDeploymentError(
            "enter the service origin only; do not include an API path")
    return urlunsplit(("https", parsed.netloc, "", "", ""))


@dataclass(frozen=True)
class RuntimeDeployment:
    root: Path
    deployment_id: str

    @property
    def path(self) -> Path:
        return self.root / "runtime/deployments" / self.deployment_id

    @property
    def manifest(self) -> Path:
        return self.path / "deployment.yml"

    @property
    def collectors(self) -> Path:
        return self.path / "collectors.yml"

    @property
    def dashboards(self) -> Path:
        return self.path / "dashboards.yml"

    @property
    def secrets_dir(self) -> Path:
        return self.path / "secrets"

    @property
    def generated(self) -> Path:
        return self.path / "generated"

    @property
    def env_file(self) -> Path:
        return self.generated / "deployment.env"

    @property
    def compose_override(self) -> Path:
        return self.generated / "compose.override.yml"

    def load(self) -> dict:
        try:
            value = yaml.safe_load(self.manifest.read_text())
        except (OSError, yaml.YAMLError) as exc:
            raise RuntimeDeploymentError(
                f"unable to load deployment {self.deployment_id}: {exc}") from exc
        if not isinstance(value, dict):
            raise RuntimeDeploymentError("deployment manifest must be a mapping")
        return value

    def compose_command(self, *arguments: str) -> list[str]:
        return [
            "docker", "compose",
            "--project-name", f"itp-{self.deployment_id}",
            "--env-file", str(self.env_file),
            "-f", str(self.root / "docker-compose.yml"),
            "-f", str(self.compose_override),
            *arguments,
        ]

    def run_compose(self, *arguments: str, check=True, capture=False):
        command = self.compose_command(*arguments)
        if capture:
            with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as output:
                completed = subprocess.run(
                    command, cwd=self.root, check=False, text=True,
                    encoding="utf-8", errors="replace",
                    stdout=output, stderr=subprocess.STDOUT)
                output.seek(0, os.SEEK_END)
                size = output.tell()
                output.seek(max(0, size - 65536))
                captured = output.read()
            result = subprocess.CompletedProcess(
                command, completed.returncode, captured, "")
            if check and result.returncode:
                raise subprocess.CalledProcessError(
                    result.returncode, command, output=captured, stderr="")
            return result
        return subprocess.run(
            command, cwd=self.root, check=check,
            text=True, encoding="utf-8", errors="replace",
            capture_output=False)


class RuntimeDeploymentManager:
    """Create deployments without mutating tracked source or templates."""

    def __init__(self, root: Path, *, input_fn=input, output_fn=print,
                 secret_input=getpass.getpass, runner=subprocess.run,
                 registry=None, port_fn=_port_available):
        self.root = Path(root).resolve()
        self.runtime = self.root / "runtime"
        self.deployments = self.runtime / "deployments"
        self.shared = self.runtime / "shared"
        self.input = input_fn
        self.output = output_fn
        self.secret_input = secret_input
        self.runner = runner
        self.port_available = port_fn
        self.registry = registry or ConnectorMetadataRegistry.load(self.root)

    def list(self) -> list[dict]:
        result = []
        if not self.deployments.is_dir():
            return result
        for manifest in sorted(self.deployments.glob("*/deployment.yml")):
            try:
                value = yaml.safe_load(manifest.read_text()) or {}
                result.append({
                    "id": manifest.parent.name,
                    "name": value.get("display_name", manifest.parent.name),
                    "platform": value.get("platform"),
                    "path": str(manifest.parent),
                })
            except (OSError, yaml.YAMLError):
                continue
        return result

    def active_id(self) -> str | None:
        try:
            value = (self.shared / "active-deployment").read_text().strip()
            return value or None
        except OSError:
            deployments = self.list()
            return deployments[0]["id"] if len(deployments) == 1 else None

    def select(self, deployment_id: str | None = None) -> RuntimeDeployment:
        selected = deployment_id or self.active_id()
        if not selected:
            raise RuntimeDeploymentError(
                "no deployment selected; run ./itp deploy or pass --deployment")
        deployment = RuntimeDeployment(self.root, slugify(selected))
        if not deployment.manifest.is_file():
            raise RuntimeDeploymentError(f"deployment does not exist: {selected}")
        return deployment

    def verify_docker(self):
        if not shutil.which("docker"):
            raise RuntimeDeploymentError(
                "Docker is not installed. Install Docker Desktop or Docker Engine.")
        for command in (
            ["docker", "--version"],
            ["docker", "compose", "version"],
            ["docker", "info"],
        ):
            try:
                self.runner(command, check=True, capture_output=True, text=True)
            except (OSError, subprocess.CalledProcessError) as exc:
                raise RuntimeDeploymentError(
                    "Docker daemon and Compose v2 must be available.") from exc

    @staticmethod
    def _timezone(default="UTC"):
        value = getattr(datetime.now().astimezone().tzinfo, "key", None) or default
        try:
            ZoneInfo(value)
            return value
        except (ValueError, ZoneInfoNotFoundError):
            return default

    def _prompt(self, label, default):
        value = self.input(f"{label} [{default}]: ").strip()
        return value or default

    def _collector_selection(self):
        names = [value.id for value in self.registry.all()]
        self.output("Available collectors: " + ", ".join(names))
        selected = self.input(
            "Collectors to enable (comma-separated, blank for none): ").strip()
        if not selected:
            return []
        values = sorted({slugify(value) for value in selected.split(",")})
        unknown = sorted(set(values) - set(names))
        if unknown:
            raise RuntimeDeploymentError(
                "unknown collectors: " + ", ".join(unknown))
        return values

    def create(self, *, name=None, deployment_id=None, timezone=None,
               grafana_port=3000, influxdb_port=8181,
               listen_address="127.0.0.1", collectors=None,
               non_interactive=False, force=False) -> RuntimeDeployment:
        display_name = name or (
            "ITP Deployment" if non_interactive
            else self._prompt("Deployment display name", "ITP Deployment"))
        derived_id = slugify(deployment_id or display_name)
        identifier = derived_id if non_interactive or deployment_id else slugify(
            self._prompt("Deployment ID", derived_id))
        deployment = RuntimeDeployment(self.root, identifier)
        existing_manifest = (
            deployment.load() if deployment.manifest.is_file() else {})
        existing_environment = self._read_env(deployment.env_file)
        if deployment.manifest.exists() and not force:
            self.output(f"Deployment {identifier} already exists; preserving configuration.")
            return deployment
        timezone = timezone or (
            self._timezone() if non_interactive
            else self._prompt("Timezone", self._timezone()))
        try:
            ZoneInfo(timezone)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise RuntimeDeploymentError("timezone must be a valid IANA name") from exc
        if not non_interactive:
            self.output("Dashboard access:")
            self.output("127.0.0.1 = available only on this machine")
            self.output(
                "0.0.0.0   = available to other devices, subject to firewall rules")
            listen_address = self._prompt(
                "Listening address", listen_address)
            grafana_port = int(self._prompt("Grafana port", str(grafana_port)))
            influxdb_port = int(self._prompt("InfluxDB port", str(influxdb_port)))
        if grafana_port == influxdb_port:
            raise RuntimeDeploymentError("Grafana and InfluxDB ports must differ")
        for label, port in (("Grafana", grafana_port), ("InfluxDB", influxdb_port)):
            if not 1 <= int(port) <= 65535:
                raise RuntimeDeploymentError(f"{label} port is invalid")
            existing_network = existing_manifest.get("network", {})
            owns_port = (
                force
                and existing_network.get("listen_address") == listen_address
                and int(existing_network.get(
                    f"{label.casefold()}_port", -1)) == int(port))
            if not owns_port and not self.port_available(listen_address, port):
                raise RuntimeDeploymentError(
                    f"{label} port {port} is already in use")
        enabled = sorted(collectors if collectors is not None else (
            [] if non_interactive else self._collector_selection()))
        deployment.path.mkdir(parents=True, exist_ok=True)
        for directory in (
            deployment.secrets_dir, deployment.generated,
            deployment.path / "logs", deployment.path / "evidence",
            deployment.path / "state", deployment.path / "generated/dashboard",
            deployment.path / "generated/telegraf",
        ):
            directory.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": 1,
            "deployment_id": identifier,
            "customer_id": identifier,
            "display_name": display_name,
            "timezone": timezone,
            "region": "",
            "currency": "",
            "platform": platform.system().casefold(),
            "deployment": {"mode": "standalone"},
            "network": {
                "listen_address": listen_address,
                "grafana_port": int(grafana_port),
                "influxdb_port": int(influxdb_port),
            },
        }
        collector_config = {
            "schema_version": 1,
            "deployment_id": identifier,
            "customer_id": identifier,
            "customer": identifier,
            "site_id": f"site:{identifier}",
            "site": f"site:{identifier}",
            "site_name": display_name,
            "identity": {
                "customer_name": display_name,
                "site_name": display_name,
            },
            "discovery": {
                "interval_seconds": 3600,
                "concurrency": 1,
                "timeout_seconds": 1,
                "retries": 0,
            },
            "snmp": {
                "version": 2,
                "communities": ["disabled"],
            },
            "networks": [{
                "cidr": "192.0.2.0/32",
                "purpose": "disabled",
            }],
            "exclusions": [],
            "collectors": {
                value.id: {"enabled": value.id in enabled}
                for value in self.registry.all()
            },
            "inventory": {},
            "writer": {},
        }
        dashboards = {
            "schema_version": 1,
            "managed": True,
            "enabled_collectors": enabled,
        }
        atomic_write(deployment.manifest, yaml.safe_dump(
            manifest, sort_keys=False))
        atomic_write(deployment.collectors, yaml.safe_dump(
            collector_config, sort_keys=False))
        atomic_write(deployment.dashboards, yaml.safe_dump(
            dashboards, sort_keys=False))
        sites = {
            "deployment_model": "standalone",
            "sites": [{
                "id": f"site:{identifier}",
                "display_name": display_name,
                "aliases": [identifier],
                "enabled": True,
            }],
        }
        atomic_write(
            deployment.generated / "sites.yml",
            yaml.safe_dump(sites, sort_keys=False))
        env = {
            "ITP_DEPLOYMENT_ID": identifier,
            "ITP_CUSTOMER_ID": identifier,
            "ITP_PROFILE": identifier,
            "ITP_RUNTIME_DIR": str(deployment.path),
            "ITP_DASHBOARD_DIR": str(deployment.generated / "dashboard"),
            "ITP_TELEGRAF_DIR": str(deployment.generated / "telegraf"),
            "ITP_DISCOVERY_CONFIG": str(deployment.collectors),
            "ITP_CONNECTORS_CONFIG": str(deployment.collectors),
            "ITP_SITES_CONFIG": str(deployment.generated / "sites.yml"),
            "ITP_SECRETS_DIR": str(deployment.secrets_dir),
            "ITP_ENV_FILE": str(deployment.env_file),
            "TZ": timezone,
            "GRAFANA_ADDRESS": listen_address,
            "GRAFANA_PORT": str(grafana_port),
            "INFLUXDB_ADDRESS": listen_address,
            "INFLUXDB_PORT": str(influxdb_port),
            "GRAFANA_ADMIN_USER": "admin",
            "GRAFANA_ADMIN_PASSWORD": existing_environment.get(
                "GRAFANA_ADMIN_PASSWORD") or _secret(),
            "INFLUXDB_TOKEN": existing_environment.get("INFLUXDB_TOKEN", ""),
            "INFLUXDB_NODE_ID": f"{identifier}-node",
            "INFLUXDB_HOST": "influxdb3-core",
            "INFLUXDB_BUCKET": f"itp_{identifier.replace('-', '_')}",
            "INFLUXDB_ORG": "local_org",
            "TELEGRAF_COLLECTION_INTERVAL": "30s",
        }
        atomic_write(
            deployment.env_file,
            "".join(f"{key}={value}\n" for key, value in env.items()))
        os.chmod(deployment.env_file, 0o600)
        override = {
            "x-itp-deployment": {
                "id": identifier,
                "generated": True,
            },
            "services": {},
        }
        atomic_write(deployment.compose_override, yaml.safe_dump(
            override, sort_keys=False))
        self.shared.mkdir(parents=True, exist_ok=True)
        atomic_write(self.shared / "active-deployment", identifier + "\n")
        return deployment

    @staticmethod
    def _read_env(path):
        values = {}
        try:
            lines = Path(path).read_text().splitlines()
        except OSError:
            return values
        for line in lines:
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key] = value
        return values

    def bootstrap_influx(self, deployment, timeout=90, capture=False):
        """Provision the real InfluxDB token and deployment database."""
        environment = self._read_env(deployment.env_file)
        deployment.run_compose(
            "up", "-d", "influxdb3-core", capture=capture)
        port = int(environment["INFLUXDB_PORT"])
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/health", timeout=2) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(1)
        else:
            raise RuntimeDeploymentError(
                "InfluxDB did not become healthy during credential bootstrap")

        token = environment.get("INFLUXDB_TOKEN", "")
        if not token:
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/v3/configure/token/admin",
                data=b"", method="POST",
                headers={"Accept": "application/json",
                         "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    token = str(json.loads(response.read()).get("token") or "")
            except (OSError, ValueError) as exc:
                raise RuntimeDeploymentError(
                    "InfluxDB administrator token bootstrap failed") from exc
            if len(token) < 20 or any(character.isspace() for character in token):
                raise RuntimeDeploymentError(
                    "InfluxDB returned an invalid administrator token")
            environment["INFLUXDB_TOKEN"] = token
            atomic_write(
                deployment.env_file,
                "".join(f"{key}={value}\n" for key, value in environment.items()))
            os.chmod(deployment.env_file, 0o600)

        result = deployment.run_compose(
            "exec", "-T", "influxdb3-core", "influxdb3", "create", "database",
            "--host", "http://localhost:8181", "--token", token,
            environment["INFLUXDB_BUCKET"], check=False, capture=True)
        if result.returncode != 0 and "already exists" not in (
                (result.stderr or "") + (result.stdout or "")).casefold():
            raise RuntimeDeploymentError(
                "InfluxDB database initialisation failed")

    def add_collector(self, deployment, name):
        connector = self.registry.get(name)
        value = yaml.safe_load(deployment.collectors.read_text()) or {}
        settings = value.setdefault("collectors", {}).setdefault(
            connector.id, {})
        settings["enabled"] = True
        prefix = f"collectors.{connector.id}."
        prompt_by_field = {
            item["field"]: item
            for item in getattr(connector, "configuration_prompts", ())
        }
        canonical_values = {}
        for field_name in connector.configuration_fields:
            if not field_name.startswith(prefix):
                continue
            key = field_name[len(prefix):]
            if settings.get(key) not in (None, ""):
                continue
            prompt = prompt_by_field.get(field_name, {})
            label = prompt.get("label") or f"{connector.display_name} {key}"
            default = prompt.get("default", "")
            if prompt.get("help"):
                self.output(prompt["help"])
            suffix = f" [{default}]" if default else " (blank to configure later)"
            entered = self.input(f"{label}{suffix}: ").strip() or default
            if entered:
                entered = normalize_onboarding_value(
                    entered, prompt.get("normalizer", ""))
                if key.endswith(("verify_tls", "enabled")):
                    settings[key] = entered.casefold() in {
                        "1", "true", "yes", "on"}
                else:
                    settings[key] = entered
                canonical_values[field_name] = entered
        atomic_write(deployment.collectors, yaml.safe_dump(
            value, sort_keys=False))
        secret_path = deployment.secrets_dir / f"{connector.id}.env"
        if not secret_path.exists():
            lines = []
            for field in connector.credential_fields:
                canonical = field.get("configuration_field", "")
                if canonical and (
                        canonical in canonical_values
                        or settings.get(canonical.rsplit(".", 1)[-1])):
                    entered = canonical_values.get(
                        canonical, settings[canonical.rsplit(".", 1)[-1]])
                else:
                    prompt = field.get("prompt", {})
                    label = prompt.get("label") or (
                        f"{connector.display_name} {field['id']}")
                    default = prompt.get("default", "")
                    if prompt.get("help"):
                        self.output(prompt["help"])
                    suffix = (
                        f" [{default}]" if default
                        else " (blank to configure later)")
                    reader = self.secret_input if (
                        field.get("secret") or prompt.get("sensitive")
                    ) else self.input
                    entered = reader(f"{label}{suffix}: ").strip() or default
                    if (entered and prompt.get("value_type") == "uuid"
                            and not re.fullmatch(
                                r"[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}",
                                entered)):
                        self.output(
                            f"Advisory: {label} does not look like a UUID; "
                            "verify it before collection.")
                if entered:
                    lines.append(f"{field['env']}={entered}")
            atomic_write(secret_path, "\n".join(lines) + ("\n" if lines else ""))
            os.chmod(secret_path, 0o600)
        return connector

    def grafana_credentials(self, deployment):
        environment = self._read_env(deployment.env_file)
        required = ("GRAFANA_ADMIN_USER", "GRAFANA_ADMIN_PASSWORD")
        missing = [key for key in required if not environment.get(key)]
        if missing:
            raise RuntimeDeploymentError(
                "Grafana credentials are unavailable; rerun ./itp deploy "
                f"--deployment-id {deployment.deployment_id} --force")
        network = deployment.load().get("network", {})
        address = network.get("listen_address", "127.0.0.1")
        if address in {"0.0.0.0", "::"}:
            address = "127.0.0.1"
        return {
            "deployment_id": deployment.deployment_id,
            "url": f"http://{address}:{network.get('grafana_port', 3000)}",
            "username": environment["GRAFANA_ADMIN_USER"],
            "password": environment["GRAFANA_ADMIN_PASSWORD"],
            "source": str(deployment.env_file),
        }

    def collector_readiness(self, deployment):
        config = yaml.safe_load(deployment.collectors.read_text()) or {}
        environment = {}
        for path in sorted(deployment.secrets_dir.glob("*.env")):
            environment.update(self._read_env(path))
        resolution = ConfigurationResolver(
            self.registry, config, root_environment=environment).evaluate()
        resolved = {
            item["connector"]: item for item in resolution["connectors"]}
        result = []
        for connector in self.registry.all():
            item = resolved[connector.id]
            if not item["enabled"]:
                state, missing = "disabled", []
            else:
                settings = {
                    field["name"]: field for field in item["settings"]}
                missing_config = [
                    field.rsplit(".", 1)[-1]
                    for field in connector.configuration_fields
                    if field.startswith(f"collectors.{connector.id}.")
                    and settings[field]["status"] != "configured"
                ]
                missing_credentials = [
                    field["id"] for field in connector.credential_fields
                    if field.get("required")
                    and settings[
                        f"{connector.id}.{field['id']}"]["status"]
                    != "configured"
                ]
                if missing_config:
                    state, missing = "pending configuration", missing_config
                elif missing_credentials:
                    state, missing = "pending credentials", missing_credentials
                else:
                    state, missing = "configured", []
            result.append({
                "id": connector.id, "display_name": connector.display_name,
                "state": state, "missing": missing,
                "next_action": (
                    f"./itp collector --deployment {deployment.deployment_id} "
                    f"add {connector.id}" if state.startswith("pending") else ""),
            })
        return result

    def remove_collector(self, deployment, name):
        connector = self.registry.get(name)
        value = yaml.safe_load(deployment.collectors.read_text()) or {}
        value.setdefault("collectors", {}).setdefault(
            connector.id, {})["enabled"] = False
        atomic_write(deployment.collectors, yaml.safe_dump(
            value, sort_keys=False))
        return connector
