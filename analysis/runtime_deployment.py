"""Sanitised, runtime-only deployment creation and management."""
from __future__ import annotations

import getpass
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from collectors.configuration import ConfigurationResolver, parse_bool_default
from collectors.connector_registry import ConnectorMetadataRegistry
from collectors.registry import CollectorRegistry
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


def retry_command(*arguments: str, system: str | None = None) -> str:
    """Return an execution-policy-safe retry command for the current host."""
    suffix = " ".join(str(value) for value in arguments)
    if (system or platform.system()).casefold() == "windows":
        command = (
            r"powershell.exe -NoProfile -ExecutionPolicy Bypass "
            r"-File .\itp.ps1")
    else:
        command = "./itp"
    return f"{command} {suffix}".rstrip()


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
    def ca_dir(self) -> Path:
        return self.secrets_dir / "ca"

    @property
    def ca_bundle(self) -> Path:
        return self.ca_dir / "ca-bundle.pem"

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
                 registry=None, port_fn=_port_available,
                 urlopen=urllib.request.urlopen, clock=time.monotonic,
                 sleep=time.sleep):
        self.root = Path(root).resolve()
        self.runtime = self.root / "runtime"
        self.deployments = self.runtime / "deployments"
        self.shared = self.runtime / "shared"
        self.input = input_fn
        self.output = output_fn
        self.secret_input = secret_input
        self.runner = runner
        self.port_available = port_fn
        self.urlopen = urlopen
        self.clock = clock
        self.sleep = sleep
        self.registry = registry or ConnectorMetadataRegistry.load(self.root)
        self._new_grafana_passwords = {}

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
            deployments = [value["id"] for value in self.list()]
            if len(deployments) > 1:
                available = "\n".join(f"- {value}" for value in deployments)
                raise RuntimeDeploymentError(
                    "multiple deployments exist and no active deployment is "
                    f"selected:\n\n{available}\n\nSpecify one with:\n\n"
                    f"--deployment {deployments[0]}\n\nor select it with:\n\n"
                    f"./itp deployment select {deployments[0]}")
            raise RuntimeDeploymentError(
                "no deployment selected; run ./itp deploy or pass --deployment")
        deployment = RuntimeDeployment(self.root, slugify(selected))
        if not deployment.manifest.is_file():
            raise RuntimeDeploymentError(f"deployment does not exist: {selected}")
        self._migrate_site_alias(deployment)
        return deployment

    def activate(self, deployment_id: str) -> RuntimeDeployment:
        deployment = self.select(deployment_id)
        self.shared.mkdir(parents=True, exist_ok=True)
        atomic_write(
            self.shared / "active-deployment",
            deployment.deployment_id + "\n")
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

    def _grafana_password(self, deployment_id, existing, non_interactive):
        if existing:
            return existing
        if non_interactive:
            password = _secret()
        else:
            self.output("Grafana administrator password")
            self.output("")
            self.output(
                "1. Generate a secure password automatically [recommended]")
            self.output("2. Enter a password")
            selection = self.input("Selection [1]: ").strip() or "1"
            if selection == "1":
                password = _secret()
            elif selection == "2":
                password = self.secret_input(
                    "Grafana administrator password: ")
                confirmation = self.secret_input("Confirm password: ")
                if not password:
                    raise RuntimeDeploymentError(
                        "Grafana administrator password cannot be blank")
                if len(password) < 12:
                    raise RuntimeDeploymentError(
                        "Grafana administrator password must contain at least "
                        "12 characters")
                if password != confirmation:
                    raise RuntimeDeploymentError(
                        "Grafana administrator password confirmation does not match")
            else:
                raise RuntimeDeploymentError(
                    "Grafana password selection must be 1 or 2")
        self._new_grafana_passwords[deployment_id] = password
        return password

    def take_new_grafana_password(self, deployment):
        """Return a password created by this process once, then forget it."""
        return self._new_grafana_passwords.pop(
            deployment.deployment_id, None)

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
            raise RuntimeDeploymentError(
                "timezone must be a valid IANA name, for example Australia/Perth") from exc
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
            deployment.ca_dir,
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
            "ITP_CA_DIR": str(deployment.ca_dir),
            "ITP_CA_BUNDLE": "",
            "ITP_ENV_FILE": str(deployment.env_file),
            "TZ": timezone,
            "GRAFANA_ADDRESS": listen_address,
            "GRAFANA_PORT": str(grafana_port),
            "INFLUXDB_ADDRESS": listen_address,
            "INFLUXDB_PORT": str(influxdb_port),
            "GRAFANA_ADMIN_USER": "admin",
            "GRAFANA_ADMIN_PASSWORD": self._grafana_password(
                identifier,
                existing_environment.get("GRAFANA_ADMIN_PASSWORD", ""),
                non_interactive),
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

    def _migrate_site_alias(self, deployment):
        """Persist the canonical single-site ID in older generated runtimes."""
        try:
            value = yaml.safe_load(deployment.collectors.read_text()) or {}
        except (OSError, yaml.YAMLError):
            return
        canonical = str(value.get("site_id") or
                        f"site:{deployment.deployment_id}")
        if not canonical.startswith("site:"):
            canonical = f"site:{deployment.deployment_id}"
        changed = False
        legacy = str(value.get("site") or "")
        if legacy and legacy != canonical:
            value["site"] = canonical
            changed = True
        if value.get("site_id") != canonical:
            value["site_id"] = canonical
            changed = True
        for settings in (value.get("collectors") or {}).values():
            if isinstance(settings, dict) and settings.get("site") and \
                    settings["site"] != canonical:
                settings["site"] = canonical
                changed = True
            if isinstance(settings, dict) and "site_id" in settings and \
                    settings["site_id"] != canonical:
                settings["site_id"] = canonical
                changed = True
        if changed:
            atomic_write(deployment.collectors, yaml.safe_dump(
                value, sort_keys=False))
            self.output(
                f"Migrated deployment {deployment.deployment_id} to canonical "
                f"site identity {canonical}.")

    @staticmethod
    def _pem_certificates(data):
        text = data.decode("ascii")
        pattern = re.compile(
            r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
            re.DOTALL)
        certificates = pattern.findall(text)
        remainder = pattern.sub("", text)
        remainder = "\n".join(
            line for line in remainder.splitlines()
            if line.strip() and not line.lstrip().startswith("#"))
        if not certificates or remainder.strip():
            raise RuntimeDeploymentError(
                "CA input must contain only PEM-encoded X.509 certificates")
        result = []
        for certificate in certificates:
            try:
                der = ssl.PEM_cert_to_DER_cert(certificate)
            except ValueError as exc:
                raise RuntimeDeploymentError(
                    "CA input contains an invalid PEM certificate") from exc
            result.append((
                certificate.strip() + "\n",
                hashlib.sha256(der).hexdigest()))
        return result

    def _write_ca_bundle(self, deployment):
        certificates = sorted(
            path for path in deployment.ca_dir.glob("*.pem")
            if path.name != deployment.ca_bundle.name)
        environment = self._read_env(deployment.env_file)
        if certificates:
            atomic_write(
                deployment.ca_bundle,
                "".join(path.read_text() for path in certificates))
            os.chmod(deployment.ca_bundle, 0o600)
            environment["ITP_CA_BUNDLE"] = "/app/trust/ca-bundle.pem"
        else:
            deployment.ca_bundle.unlink(missing_ok=True)
            environment["ITP_CA_BUNDLE"] = ""
        environment["ITP_CA_DIR"] = str(deployment.ca_dir)
        atomic_write(
            deployment.env_file,
            "".join(f"{key}={value}\n" for key, value in environment.items()))
        os.chmod(deployment.env_file, 0o600)

    def ca_add(self, deployment, certificate_file):
        source = Path(certificate_file).expanduser()
        try:
            certificates = self._pem_certificates(source.read_bytes())
        except (OSError, UnicodeDecodeError) as exc:
            raise RuntimeDeploymentError(
                "unable to read the CA certificate file") from exc
        deployment.ca_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(deployment.ca_dir, 0o700)
        added = []
        base = slugify(source.stem)
        for index, (certificate, fingerprint) in enumerate(certificates, 1):
            suffix = f"-{index}" if len(certificates) > 1 else ""
            target = deployment.ca_dir / (
                f"{base}{suffix}--{fingerprint[:16]}.pem")
            atomic_write(target, certificate)
            os.chmod(target, 0o600)
            added.append({
                "name": f"{base}{suffix}", "fingerprint": fingerprint})
        self._write_ca_bundle(deployment)
        return added

    def ca_list(self, deployment):
        result = []
        if not deployment.ca_dir.is_dir():
            return result
        for path in sorted(deployment.ca_dir.glob("*.pem")):
            if path.name == deployment.ca_bundle.name:
                continue
            for _, fingerprint in self._pem_certificates(path.read_bytes()):
                result.append({
                    "name": path.name.split("--", 1)[0],
                    "fingerprint": fingerprint})
        return result

    def ca_remove(self, deployment, identifier):
        matches = [
            item for item in self.ca_list(deployment)
            if item["name"] == identifier or
            item["fingerprint"].startswith(identifier.casefold())]
        if not matches:
            raise RuntimeDeploymentError(
                f"deployment CA certificate not found: {identifier}")
        if len(matches) > 1:
            raise RuntimeDeploymentError(
                "deployment CA identifier is ambiguous; use a longer fingerprint")
        selected = matches[0]
        for path in deployment.ca_dir.glob(
                f"{selected['name']}--{selected['fingerprint'][:16]}.pem"):
            path.unlink()
        self._write_ca_bundle(deployment)
        return selected

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

    @staticmethod
    def _valid_token(token):
        return (
            isinstance(token, str)
            and len(token) >= 20
            and not any(character.isspace() for character in token)
            and all(32 < ord(character) < 127 for character in token)
        )

    @classmethod
    def _parse_admin_token(cls, body, content_type=""):
        """Accept current plain-text and older JSON admin-token responses."""
        try:
            text = bytes(body).decode("utf-8-sig").strip()
        except (TypeError, UnicodeDecodeError) as exc:
            raise RuntimeDeploymentError(
                "InfluxDB returned a non-UTF-8 administrator token response") from exc
        token = ""
        response_type = "plain text"
        if text.startswith("{"):
            response_type = "JSON"
            try:
                payload = json.loads(text)
            except (TypeError, json.JSONDecodeError) as exc:
                raise RuntimeDeploymentError(
                    "InfluxDB returned malformed JSON during administrator "
                    "token creation") from exc
            if not isinstance(payload, dict):
                raise RuntimeDeploymentError(
                    "InfluxDB returned an unexpected JSON administrator token response")
            token = str(payload.get("token") or "")
        else:
            token = text
        if not cls._valid_token(token):
            raise RuntimeDeploymentError(
                f"InfluxDB returned a blank or malformed {response_type} "
                "administrator token response")
        return token, response_type

    @staticmethod
    def _prepare_token_destination(deployment):
        deployment.path.mkdir(parents=True, exist_ok=True)
        deployment.generated.mkdir(parents=True, exist_ok=True)
        if not deployment.env_file.is_file():
            raise RuntimeDeploymentError(
                "generated deployment environment is missing before InfluxDB "
                f"bootstrap: {deployment.env_file}")
        try:
            with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", prefix=".token-preflight.",
                    dir=deployment.generated, delete=True) as handle:
                handle.write("writable\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise RuntimeDeploymentError(
                "InfluxDB token destination is not writable: "
                f"{deployment.generated}") from exc

    @classmethod
    def _persist_admin_token(cls, deployment, environment, token):
        if not cls._valid_token(token):
            raise RuntimeDeploymentError(
                "refusing to persist an invalid InfluxDB administrator token")
        updated = dict(environment)
        updated["INFLUXDB_TOKEN"] = token
        try:
            atomic_write(
                deployment.env_file,
                "".join(f"{key}={value}\n" for key, value in updated.items()))
            os.chmod(deployment.env_file, 0o600)
            persisted = cls._read_env(deployment.env_file).get(
                "INFLUXDB_TOKEN", "")
        except OSError as exc:
            raise RuntimeDeploymentError(
                "InfluxDB created the administrator token, but atomic "
                "persistence failed; the token may now require recovery") from exc
        if persisted != token:
            raise RuntimeDeploymentError(
                "InfluxDB created the administrator token, but persisted-token "
                "verification failed; the token may now require recovery")
        return updated

    @staticmethod
    def _already_exists_error(error):
        try:
            body = error.read().decode("utf-8", errors="replace")
        except (AttributeError, KeyError, OSError, ValueError):
            body = ""
        return error.code == 409 or "already exists" in body.casefold()

    @staticmethod
    def _influx_context(deployment, endpoint, *, status="unknown",
                        response_type="unknown", admin_exists=False,
                        persisted=False):
        container = f"itp-{deployment.deployment_id}-influxdb3-core-1"
        container_state = "unknown"
        health_state = "unknown"
        try:
            result = deployment.run_compose(
                "ps", "influxdb3-core", "--format", "json",
                check=False, capture=True)
            text = (result.stdout or "").strip()
            if text:
                row = json.loads(text.splitlines()[0])
                container = str(row.get("Name") or container)
                container_state = str(row.get("State") or "unknown")
                health_state = str(row.get("Health") or "unknown")
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        return (
            f"deployment={deployment.deployment_id}; runtime={deployment.path}; "
            f"container={container}; state={container_state}; "
            f"health={health_state}; endpoint={endpoint}; HTTP={status}; "
            f"response_type={response_type}; admin_exists={str(admin_exists).lower()}; "
            f"token_destination_exists={str(deployment.env_file.exists()).lower()}; "
            f"token_persisted={str(persisted).lower()}")

    def _existing_token_error(self, deployment, endpoint, *,
                              non_interactive=False):
        retry = retry_command("deploy", "--force", "--verbose")
        reset = retry_command(
            "deploy", "--force", "--reset-influx", "--verbose")
        mode = (
            "Non-interactive recovery cannot reset data."
            if non_interactive else
            f"For a confirmed disposable deployment, rerun with: {reset}")
        return RuntimeDeploymentError(
            "InfluxDB reports that operator token _admin already exists, but "
            f"no token is persisted at {deployment.env_file}. An earlier "
            "bootstrap created the token without saving it. ITP will not delete "
            "the existing volume automatically. "
            f"{mode} For an established deployment, recover or regenerate the "
            "operator token using the supported InfluxDB administrator-token "
            f"workflow, update the deployment environment, then retry with: {retry}. "
            + self._influx_context(
                deployment, endpoint, status=409,
                response_type="error", admin_exists=True, persisted=False))

    def bootstrap_influx(self, deployment, timeout=90, capture=False,
                         non_interactive=False):
        """Provision the real InfluxDB token and deployment database."""
        self._prepare_token_destination(deployment)
        environment = self._read_env(deployment.env_file)
        if "INFLUXDB_PORT" not in environment:
            raise RuntimeDeploymentError(
                "generated deployment environment does not define INFLUXDB_PORT")
        deployment.run_compose(
            "up", "-d", "influxdb3-core", capture=capture)
        port = int(environment["INFLUXDB_PORT"])
        endpoint = (
            f"http://127.0.0.1:{port}/api/v3/configure/token/admin")
        deadline = self.clock() + timeout
        while self.clock() < deadline:
            try:
                with self.urlopen(
                        f"http://127.0.0.1:{port}/health", timeout=2) as response:
                    if response.status == 200:
                        break
            except (OSError, URLError):
                self.sleep(1)
        else:
            raise RuntimeDeploymentError(
                "InfluxDB did not become healthy during credential bootstrap. "
                + self._influx_context(deployment, endpoint))

        token = environment.get("INFLUXDB_TOKEN", "")
        if token and not self._valid_token(token):
            raise RuntimeDeploymentError(
                "persisted InfluxDB administrator token is malformed; refusing "
                "to request a replacement token")
        if not token:
            request = urllib.request.Request(
                endpoint,
                data=b"", method="POST",
                headers={"Accept": "application/json",
                         "Content-Type": "application/json"})
            last_error = None
            while self.clock() < deadline:
                try:
                    with self.urlopen(request, timeout=5) as response:
                        status = int(getattr(response, "status", 0))
                        if not 200 <= status < 300:
                            raise RuntimeDeploymentError(
                                "InfluxDB administrator-token endpoint returned "
                                f"HTTP {status}")
                        content_type = response.headers.get(
                            "Content-Type", "") if response.headers else ""
                        try:
                            token, response_type = self._parse_admin_token(
                                response.read(), content_type)
                        except RuntimeDeploymentError as exc:
                            raise RuntimeDeploymentError(
                                f"{exc}. The token may have been created but not "
                                "persisted. " + self._influx_context(
                                    deployment, endpoint, status=status,
                                    response_type=content_type or "unknown",
                                    persisted=False)) from exc
                    try:
                        environment = self._persist_admin_token(
                            deployment, environment, token)
                    except RuntimeDeploymentError as exc:
                        raise RuntimeDeploymentError(
                            f"{exc}. " + self._influx_context(
                                deployment, endpoint, status=status,
                                response_type=response_type,
                                persisted=False)) from exc
                    break
                except HTTPError as exc:
                    if self._already_exists_error(exc):
                        raise self._existing_token_error(
                            deployment, endpoint,
                            non_interactive=non_interactive) from exc
                    if 500 <= exc.code < 600:
                        last_error = f"HTTP {exc.code}"
                        self.sleep(1)
                        continue
                    raise RuntimeDeploymentError(
                        "InfluxDB administrator-token endpoint returned "
                        f"HTTP {exc.code}. " + self._influx_context(
                            deployment, endpoint, status=exc.code,
                            response_type="error")) from exc
                except (URLError, TimeoutError, ConnectionError, OSError) as exc:
                    last_error = type(exc).__name__
                    self.sleep(1)
            else:
                raise RuntimeDeploymentError(
                    "InfluxDB administrator-token endpoint did not become ready "
                    f"within {timeout} seconds (last result: {last_error or 'no response'})")

        result = deployment.run_compose(
            "exec", "-T", "influxdb3-core", "influxdb3", "create", "database",
            "--host", "http://localhost:8181", "--token", token,
            environment["INFLUXDB_BUCKET"], check=False, capture=True)
        if result.returncode != 0 and "already exists" not in (
                (result.stderr or "") + (result.stdout or "")).casefold():
            raise RuntimeDeploymentError(
                "InfluxDB database initialisation failed")

    def reset_influx(self, deployment, *, non_interactive=False,
                     confirmation=""):
        """Reset only the profile InfluxDB volume after explicit confirmation."""
        if non_interactive:
            raise RuntimeDeploymentError(
                "InfluxDB reset is unavailable in non-interactive mode")
        expected = f"RESET {deployment.deployment_id}"
        entered = confirmation or self.input(
            "This permanently deletes this deployment's InfluxDB telemetry. "
            f"Type {expected} to continue: ").strip()
        if entered != expected:
            raise RuntimeDeploymentError(
                "InfluxDB reset was not confirmed; no volume was deleted")
        deployment.run_compose(
            "stop", "influxdb3-core", check=False, capture=True)
        deployment.run_compose(
            "rm", "-f", "-s", "influxdb3-core",
            check=False, capture=True)
        volume = f"itp-{deployment.deployment_id}_influxdb_data"
        result = self.runner(
            ["docker", "volume", "rm", volume],
            cwd=self.root, check=False, text=True, encoding="utf-8",
            errors="replace", capture_output=True)
        if result.returncode and "no such volume" not in (
                (result.stdout or "") + (result.stderr or "")).casefold():
            raise RuntimeDeploymentError(
                f"unable to remove disposable InfluxDB volume {volume}")
        environment = self._read_env(deployment.env_file)
        environment["INFLUXDB_TOKEN"] = ""
        atomic_write(
            deployment.env_file,
            "".join(f"{key}={value}\n" for key, value in environment.items()))
        os.chmod(deployment.env_file, 0o600)

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
        runtime_mode = self._read_env(deployment.env_file).get(
            "ITP_RUNTIME_MODE", "central").strip().casefold()
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
                    field["env"] for field in connector.credential_fields
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
                eligible, execution = CollectorRegistry.execution_eligible(
                    connector.id,
                    (config.get("collectors") or {}).get(connector.id) or {},
                    runtime_mode)
                if state == "configured" and not eligible:
                    state = "execution mode mismatch"
            result.append({
                "id": connector.id, "display_name": connector.display_name,
                "state": state, "missing": missing,
                "execution_mode": (
                    execution if item["enabled"] else None),
                "runtime_mode": runtime_mode,
                "tls_verification": (
                    parse_bool_default(
                        (config.get("collectors", {}).get("papercut") or {}).get(
                            "verify_tls"), True)
                    if connector.id == "papercut" else None),
                "next_action": (
                    f"./itp collector add {connector.id} --deployment "
                    f"{deployment.deployment_id}"
                    if state.startswith("pending") else ""),
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
