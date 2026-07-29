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
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

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
        return subprocess.run(
            self.compose_command(*arguments), cwd=self.root, check=check,
            text=True, encoding="utf-8", errors="replace",
            capture_output=capture)


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
        identifier = slugify(deployment_id or display_name)
        deployment = RuntimeDeployment(self.root, identifier)
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
            listen_address = self._prompt(
                "Listening address", listen_address)
            grafana_port = int(self._prompt("Grafana port", str(grafana_port)))
            influxdb_port = int(self._prompt("InfluxDB port", str(influxdb_port)))
        if grafana_port == influxdb_port:
            raise RuntimeDeploymentError("Grafana and InfluxDB ports must differ")
        for label, port in (("Grafana", grafana_port), ("InfluxDB", influxdb_port)):
            if not 1 <= int(port) <= 65535:
                raise RuntimeDeploymentError(f"{label} port is invalid")
            if not self.port_available(listen_address, port):
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
                "id": identifier,
                "display_name": display_name,
                "aliases": [],
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

    def bootstrap_influx(self, deployment, timeout=90):
        """Provision the real InfluxDB token and deployment database."""
        environment = self._read_env(deployment.env_file)
        deployment.run_compose("up", "-d", "influxdb3-core")
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
        for field_name in connector.configuration_fields:
            if not field_name.startswith(prefix):
                continue
            key = field_name[len(prefix):]
            if settings.get(key) not in (None, ""):
                continue
            entered = self.input(
                f"{connector.display_name} {key} "
                "(blank to configure later): ").strip()
            if entered:
                if key.endswith(("verify_tls", "enabled")):
                    settings[key] = entered.casefold() in {
                        "1", "true", "yes", "on"}
                else:
                    settings[key] = entered
        atomic_write(deployment.collectors, yaml.safe_dump(
            value, sort_keys=False))
        secret_path = deployment.secrets_dir / f"{connector.id}.env"
        if not secret_path.exists():
            lines = []
            for field in connector.credential_fields:
                if field.get("secret"):
                    entered = self.secret_input(
                        f"{connector.display_name} {field['id']} "
                        "(blank to configure later): ")
                else:
                    entered = self.input(
                        f"{connector.display_name} {field['id']} "
                        "(blank to configure later): ")
                if entered:
                    lines.append(f"{field['env']}={entered}")
            atomic_write(secret_path, "\n".join(lines) + ("\n" if lines else ""))
            os.chmod(secret_path, 0o600)
        return connector

    def remove_collector(self, deployment, name):
        connector = self.registry.get(name)
        value = yaml.safe_load(deployment.collectors.read_text()) or {}
        value.setdefault("collectors", {}).setdefault(
            connector.id, {})["enabled"] = False
        atomic_write(deployment.collectors, yaml.safe_dump(
            value, sort_keys=False))
        return connector
