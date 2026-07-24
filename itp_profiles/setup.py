"""Cross-platform first-run bootstrap for the root ITP deployment."""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import yaml
from collectors.connector_registry import ConnectorMetadataRegistry


DEPLOYMENT_TYPES = ("Home Lab", "School", "Business", "MSP", "Enterprise")
REQUIRED_SERVICES = frozenset(
    {"influxdb3-core", "telegraf", "discovery", "collector", "grafana"})


class SetupError(ValueError):
    pass


@dataclass(frozen=True)
class SetupOptions:
    non_interactive: bool = False
    deployment_name: str | None = None
    deployment_type: str | None = None
    grafana_port: int | None = None
    start: bool | None = None
    force: bool = False
    health_timeout: int = 180


@dataclass(frozen=True)
class SetupResult:
    first_run: bool
    created: tuple[str, ...]
    updated: tuple[str, ...]
    started: bool
    dashboard_url: str


class BootstrapWizard:
    """Prepare the legacy root Compose deployment without touching secrets."""

    def __init__(self, root, *, runner=subprocess.run, input_fn=input,
                 output_fn=print, sleep_fn=time.sleep, connector_registry=None):
        self.root = Path(root).resolve()
        self.runner = runner
        self.input = input_fn
        self.output = output_fn
        self.sleep = sleep_fn
        self.connector_registry = connector_registry or \
            ConnectorMetadataRegistry.load(Path(__file__).resolve().parents[1])
        self.env_path = self.root / ".env"
        self.config_path = self.root / "discovery/config.yml"
        self.env_template = self.root / ".env.example"
        self.config_template = self.root / "discovery/config.example.yml"

    def _run(self, command, *, check=True, capture=True):
        return self.runner(command, cwd=self.root, check=check, text=True,
                           capture_output=capture)

    def verify_docker(self):
        if not shutil.which("docker"):
            raise SetupError(
                "Docker is not installed or is not on PATH. Install Docker "
                "Desktop or Docker Engine before running setup.")
        try:
            self._run(["docker", "--version"])
            self._run(["docker", "compose", "version"])
        except (OSError, subprocess.CalledProcessError) as exc:
            raise SetupError(
                "Docker Compose v2 is unavailable. Verify `docker compose version`.") from exc

    @staticmethod
    def _port_available(port):
        with socket.socket() as connection:
            connection.settimeout(0.25)
            return connection.connect_ex(("127.0.0.1", port)) != 0

    def _project_running(self):
        try:
            return bool(self._run(
                ["docker", "compose", "ps", "-q"]).stdout.strip())
        except (OSError, subprocess.CalledProcessError):
            return False

    @staticmethod
    def _answer(value):
        return str(value).strip().casefold() in {"y", "yes"}

    def _prompt(self, label, default):
        value = self.input(f"{label} [{default}]: ").strip()
        return value or default

    def _choices(self, options):
        default_name = options.deployment_name or "ITP Deployment"
        default_type = options.deployment_type or "Home Lab"
        default_port = options.grafana_port or 3000
        if options.non_interactive:
            name, kind, port = default_name, default_type, default_port
        else:
            name = self._prompt("Deployment name", default_name)
            self.output("Deployment types: " + ", ".join(DEPLOYMENT_TYPES))
            kind = self._prompt("Deployment type", default_type)
            port_text = self._prompt("Grafana port", str(default_port))
            try:
                port = int(port_text)
            except ValueError as exc:
                raise SetupError("Grafana port must be a number") from exc
        name = str(name).strip()
        if not name:
            raise SetupError("Deployment name is required")
        if kind not in DEPLOYMENT_TYPES:
            raise SetupError(
                "Deployment type must be one of: " + ", ".join(DEPLOYMENT_TYPES))
        if not 1 <= int(port) <= 65535 or int(port) == 8181:
            raise SetupError(
                "Grafana port must be between 1 and 65535 and differ from 8181")
        return name, kind, int(port)

    @staticmethod
    def _atomic_write(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _set_env(content, key, value):
        line = f"{key}={value}"
        pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
        if pattern.search(content):
            return pattern.sub(line, content)
        return content.rstrip() + "\n" + line + "\n"

    @staticmethod
    def _slug(value):
        slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
        return slug or "itp-deployment"

    def _write_configuration(self, name, kind, port, *, update_existing):
        for template in (self.env_template, self.config_template):
            if not template.is_file():
                raise SetupError(f"required setup template is missing: {template}")
        created, updated = [], []
        if not self.env_path.exists():
            env = self.env_template.read_text()
            env = self._set_env(env, "GRAFANA_PORT", port)
            self._atomic_write(self.env_path, env)
            created.append(str(self.env_path.relative_to(self.root)))
        elif update_existing:
            env = self._set_env(self.env_path.read_text(), "GRAFANA_PORT", port)
            self._atomic_write(self.env_path, env)
            updated.append(str(self.env_path.relative_to(self.root)))

        if not self.config_path.exists():
            config = yaml.safe_load(self.config_template.read_text())
            config["deployment"] = {"name": name, "type": kind}
            config["customer"] = config["site"] = self._slug(name)
            self._atomic_write(
                self.config_path, yaml.safe_dump(config, sort_keys=False))
            created.append(str(self.config_path.relative_to(self.root)))
        elif update_existing:
            try:
                config = yaml.safe_load(self.config_path.read_text())
            except yaml.YAMLError as exc:
                raise SetupError(
                    f"existing configuration is invalid: {exc}") from exc
            if not isinstance(config, dict):
                raise SetupError("existing discovery/config.yml must be a mapping")
            config["deployment"] = {"name": name, "type": kind}
            config["customer"] = config["site"] = self._slug(name)
            self._atomic_write(
                self.config_path, yaml.safe_dump(config, sort_keys=False))
            updated.append(str(self.config_path.relative_to(self.root)))
        return tuple(created), tuple(updated)

    def _preserved_choices(self, name, kind, port, *, update_existing):
        if update_existing:
            return name, kind, port
        if self.env_path.exists():
            match = re.search(
                r"^GRAFANA_PORT=(\d+)\s*$", self.env_path.read_text(),
                re.MULTILINE)
            if match:
                port = int(match.group(1))
        if self.config_path.exists():
            try:
                config = yaml.safe_load(self.config_path.read_text())
            except yaml.YAMLError:
                config = None
            deployment = config.get("deployment", {}) if isinstance(config, dict) else {}
            name = str(deployment.get("name") or name)
            kind = str(deployment.get("type") or kind)
        return name, kind, port

    def validate_configuration(self):
        try:
            config = yaml.safe_load(self.config_path.read_text())
        except (OSError, yaml.YAMLError) as exc:
            raise SetupError(f"invalid discovery configuration: {exc}") from exc
        if not isinstance(config, dict) or config.get("schema_version") != 1:
            raise SetupError("discovery configuration must use schema_version 1")
        if not isinstance(config.get("collectors"), dict):
            raise SetupError("discovery configuration requires collectors mapping")
        try:
            self._run(["docker", "compose", "config", "--quiet"])
        except (OSError, subprocess.CalledProcessError) as exc:
            detail = getattr(exc, "stderr", "") or str(exc)
            raise SetupError(f"Docker Compose configuration validation failed: {detail}") from exc

    def _container_states(self):
        completed = self._run(
            ["docker", "compose", "ps", "--format", "json"])
        text = completed.stdout.strip()
        if not text:
            return {}
        try:
            payload = json.loads(text)
            rows = payload if isinstance(payload, list) else [payload]
        except json.JSONDecodeError:
            try:
                rows = [json.loads(line) for line in text.splitlines() if line.strip()]
            except json.JSONDecodeError as exc:
                raise SetupError("unable to read Docker Compose service health") from exc
        return {
            str(item.get("Service") or item.get("Name")): {
                "state": str(item.get("State") or "").casefold(),
                "health": str(item.get("Health") or "").casefold(),
            }
            for item in rows
        }

    def wait_healthy(self, timeout):
        deadline = time.monotonic() + max(1, timeout)
        while time.monotonic() < deadline:
            states = self._container_states()
            if REQUIRED_SERVICES <= set(states):
                ready = all(
                    states[service]["state"] == "running"
                    and states[service]["health"] not in {
                        "starting", "unhealthy"}
                    for service in REQUIRED_SERVICES)
                if ready:
                    return
            self.sleep(2)
        states = self._container_states()
        detail = ", ".join(
            f"{name}={value['state']}/{value['health'] or 'no-healthcheck'}"
            for name, value in sorted(states.items())) or "no containers reported"
        raise SetupError(
            f"services did not become healthy within {timeout}s: {detail}")

    def run(self, options):
        self.verify_docker()
        first_run = not self.env_path.exists() or not self.config_path.exists()
        existing = self.env_path.exists() or self.config_path.exists()
        update_existing = options.force
        if existing and not options.force and not options.non_interactive:
            update_existing = self._answer(self.input(
                "Existing configuration detected. Update wizard-managed values? [y/N]: "))
        name, kind, port = self._choices(options)
        name, kind, port = self._preserved_choices(
            name, kind, port, update_existing=update_existing)
        running = self._project_running()
        occupied = [
            candidate for candidate in (port, 8181)
            if not self._port_available(candidate)]
        if occupied and not running:
            raise SetupError(
                "required port(s) already in use: " +
                ", ".join(str(value) for value in occupied))
        created, updated = self._write_configuration(
            name, kind, port, update_existing=update_existing)
        self.validate_configuration()
        should_start = options.start
        if should_start is None:
            should_start = False if options.non_interactive else self._answer(
                self.input("Start ITP services now? [y/N]: "))
        if should_start:
            self._run(["docker", "compose", "up", "-d", "--build"])
            self.wait_healthy(options.health_timeout)
        url = f"http://localhost:{port}"
        self.output("ITP setup complete.")
        self.output(f"Dashboard: {url}")
        if not should_start:
            self.output("Next: docker compose up -d --build")
        self.output(
            "Next: configure collector credentials under secrets/ and enable "
            "collectors in discovery/config.yml.")
        self.output(
            f"Connector catalogue: {len(self.connector_registry.all())} "
            "registered; guided credential onboarding is not enabled yet.")
        return SetupResult(first_run, created, updated, bool(should_start), url)
