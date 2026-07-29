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
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from collectors.connector_registry import ConnectorMetadataRegistry
from .settings import (
    DEFAULT_COLLECTION_INTERVAL,
    DEFAULT_GRAFANA_PORT,
    DEFAULT_INFLUX_DATABASE,
    DEFAULT_INFLUX_ORG,
    DEFAULT_INFLUX_PORT,
    DEFAULT_TIMEZONE,
    SettingsError,
    resolve_settings,
)


DEPLOYMENT_TYPES = ("Home Lab", "School", "Business", "MSP", "Enterprise")
REQUIRED_SERVICES = frozenset(
    {"influxdb3-core", "telegraf", "discovery", "collector", "grafana"})
WINDOWS_TIMEZONES = {
    "W. Australia Standard Time": "Australia/Perth",
    "AUS Eastern Standard Time": "Australia/Sydney",
    "E. Australia Standard Time": "Australia/Brisbane",
    "Tasmania Standard Time": "Australia/Hobart",
    "Cen. Australia Standard Time": "Australia/Adelaide",
    "UTC": "UTC",
    "GMT Standard Time": "Europe/London",
    "Eastern Standard Time": "America/New_York",
    "Central Standard Time": "America/Chicago",
    "Mountain Standard Time": "America/Denver",
    "Pacific Standard Time": "America/Los_Angeles",
}
COLLECTION_PRESETS = {
    "1": "10s", "2": "30s", "3": "60s", "4": "300s",
}


class SetupError(ValueError):
    pass


@dataclass(frozen=True)
class SetupOptions:
    non_interactive: bool = False
    deployment_name: str | None = None
    deployment_type: str | None = None
    grafana_port: int | None = None
    influxdb_port: int | None = None
    timezone: str | None = None
    collection_interval: str | None = None
    demo: bool | None = None
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
    deployment_id: str = ""
    timezone: str = DEFAULT_TIMEZONE
    influxdb_url: str = ""
    collection_interval: str = DEFAULT_COLLECTION_INTERVAL
    demo_loaded: bool = False


class BootstrapWizard:
    """Prepare the legacy root Compose deployment without touching secrets."""

    def __init__(self, root, *, runner=subprocess.run, input_fn=input,
                 output_fn=print, sleep_fn=time.sleep, connector_registry=None,
                 provision_fn=None, start_fn=None, demo_fn=None,
                 timezone_runner=subprocess.run):
        self.root = Path(root).resolve()
        self.runner = runner
        self.input = input_fn
        self.output = output_fn
        self.sleep = sleep_fn
        self.connector_registry = connector_registry or \
            ConnectorMetadataRegistry.load(Path(__file__).resolve().parents[1])
        self.provision_fn = provision_fn
        self.start_fn = start_fn
        self.demo_fn = demo_fn
        self.timezone_runner = timezone_runner
        self.env_path = self.root / ".env"
        self.config_path = self.root / "discovery/config.yml"
        self.env_template = self.root / ".env.example"
        self.config_template = self.root / "discovery/config.example.yml"

    def _run(self, command, *, check=True, capture=True):
        return self.runner(command, cwd=self.root, check=check, text=True,
                           encoding="utf-8", errors="replace",
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

    @staticmethod
    def validate_timezone(value):
        value = str(value or "").strip()
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise SetupError(
                "Timezone must be a valid IANA name such as Australia/Perth."
            ) from exc
        return value

    @classmethod
    def detect_timezone(cls, *, environment=None, localtime=None,
                        windows_id=None):
        environment = os.environ if environment is None else environment
        candidate = str(environment.get("TZ") or "").strip()
        if candidate:
            try:
                return cls.validate_timezone(candidate)
            except SetupError:
                pass
        if windows_id in WINDOWS_TIMEZONES:
            return WINDOWS_TIMEZONES[windows_id]
        localtime = Path(localtime or "/etc/localtime")
        try:
            resolved = str(localtime.resolve())
            marker = "/zoneinfo/"
            if marker in resolved:
                return cls.validate_timezone(resolved.split(marker, 1)[1])
        except OSError:
            pass
        key = getattr(datetime.now().astimezone().tzinfo, "key", None)
        try:
            return cls.validate_timezone(key) if key else DEFAULT_TIMEZONE
        except SetupError:
            return DEFAULT_TIMEZONE

    def _detected_timezone(self):
        windows_id = None
        if os.name == "nt":
            try:
                result = self.timezone_runner(
                    ["tzutil", "/g"], text=True, encoding="utf-8",
                    errors="replace", capture_output=True, check=False)
                if result.returncode == 0:
                    windows_id = result.stdout.strip()
            except OSError:
                pass
        return self.detect_timezone(windows_id=windows_id)

    def recommend_port(self, preferred, *, step=100, excluded=()):
        candidate = int(preferred)
        excluded = set(int(value) for value in excluded)
        for _ in range(100):
            if candidate not in excluded and self._port_available(candidate):
                return candidate
            candidate += step
            if candidate > 65535:
                break
        raise SetupError("No available host port could be recommended.")

    @staticmethod
    def validate_interval(value):
        text = str(value or "").strip().lower()
        match = re.fullmatch(r"(\d+)(s|m)", text)
        if not match:
            raise SetupError(
                "Collection interval must use seconds or minutes, for example "
                "60s or 5m.")
        seconds = int(match.group(1)) * (60 if match.group(2) == "m" else 1)
        if not 5 <= seconds <= 3600:
            raise SetupError(
                "Collection interval must be between 5 seconds and 60 minutes.")
        return f"{seconds}s"

    def _choices(self, options):
        default_name = options.deployment_name or "ITP Deployment"
        default_type = options.deployment_type or "Home Lab"
        detected_timezone = options.timezone or self._detected_timezone()
        default_grafana = (
            options.grafana_port if options.grafana_port is not None
            else self.recommend_port(DEFAULT_GRAFANA_PORT))
        default_influx = (
            options.influxdb_port if options.influxdb_port is not None
            else self.recommend_port(
                DEFAULT_INFLUX_PORT, excluded=(default_grafana,)))
        default_interval = options.collection_interval or (
            "10s" if default_type == "Home Lab" else DEFAULT_COLLECTION_INTERVAL)
        if options.non_interactive:
            name, kind = default_name, default_type
            grafana_port, influx_port = default_grafana, default_influx
            timezone = detected_timezone
            interval = self.validate_interval(default_interval)
            demo = bool(options.demo)
        else:
            name = self._prompt("Deployment name", default_name)
            self.output("Deployment types: " + ", ".join(DEPLOYMENT_TYPES))
            kind = self._prompt("Deployment type", default_type)
            timezone = self.validate_timezone(
                self._prompt("Timezone", detected_timezone))
            grafana_text = self._prompt(
                "Grafana web port", str(default_grafana))
            influx_text = self._prompt(
                "InfluxDB API port", str(default_influx))
            try:
                grafana_port = int(grafana_text)
                influx_port = int(influx_text)
            except ValueError as exc:
                raise SetupError("Service ports must be numbers.") from exc
            self.output(
                "Collection intervals: 1=10 seconds, 2=30 seconds, "
                "3=60 seconds, 4=5 minutes, 5=Custom")
            selection = self._prompt("Collection interval", "3")
            if selection == "5":
                selection = self._prompt(
                    "Custom interval (for example 90s or 5m)", "60s")
            interval = self.validate_interval(
                COLLECTION_PRESETS.get(selection, selection))
            demo = bool(options.demo) if options.demo is not None else \
                self._answer(self.input("Load demonstration data? [y/N]: "))
        name = str(name).strip()
        if not name:
            raise SetupError("Deployment name is required")
        if kind not in DEPLOYMENT_TYPES:
            raise SetupError(
                "Deployment type must be one of: " + ", ".join(DEPLOYMENT_TYPES))
        for label, port in (
                ("Grafana", grafana_port), ("InfluxDB", influx_port)):
            if not 1 <= int(port) <= 65535:
                raise SetupError(f"{label} port must be between 1 and 65535.")
        if int(grafana_port) == int(influx_port):
            raise SetupError("Grafana and InfluxDB host ports must differ.")
        return (
            name, kind, timezone, int(grafana_port), int(influx_port),
            interval, demo)

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

    def _write_configuration(
            self, name, kind, timezone, grafana_port, influx_port, interval,
            deployment_id, *, update_existing):
        for template in (self.env_template, self.config_template):
            if not template.is_file():
                raise SetupError(f"required setup template is missing: {template}")
        created, updated = [], []
        if not self.env_path.exists():
            env = self.env_template.read_text()
            values = {
                "GRAFANA_PORT": grafana_port,
                "INFLUXDB_PORT": influx_port,
                "INFLUXDB_BUCKET": DEFAULT_INFLUX_DATABASE,
                "INFLUXDB_ORG": DEFAULT_INFLUX_ORG,
                "TELEGRAF_COLLECTION_INTERVAL": interval,
                "TZ": timezone,
                "ITP_DEPLOYMENT_ID": deployment_id,
                "INFLUXDB_NODE_ID": f"itp-{deployment_id[:12]}",
            }
            for key, value in values.items():
                env = self._set_env(env, key, value)
            env = re.sub(
                r"^INFLUXDB_HTTP_PORT=.*\n?", "", env,
                flags=re.MULTILINE)
            self._atomic_write(self.env_path, env)
            created.append(str(self.env_path.relative_to(self.root)))
        elif update_existing:
            env = self.env_path.read_text()
            values = {
                "GRAFANA_PORT": grafana_port,
                "INFLUXDB_PORT": influx_port,
                "TELEGRAF_COLLECTION_INTERVAL": interval,
                "TZ": timezone,
                "ITP_DEPLOYMENT_ID": deployment_id,
            }
            if not re.search(r"^INFLUXDB_BUCKET=.+$", env, re.MULTILINE):
                values["INFLUXDB_BUCKET"] = DEFAULT_INFLUX_DATABASE
            if not re.search(r"^INFLUXDB_ORG=.+$", env, re.MULTILINE):
                values["INFLUXDB_ORG"] = DEFAULT_INFLUX_ORG
            if not re.search(r"^INFLUXDB_NODE_ID=.+$", env, re.MULTILINE):
                values["INFLUXDB_NODE_ID"] = f"itp-{deployment_id[:12]}"
            for key, value in values.items():
                env = self._set_env(env, key, value)
            env = re.sub(
                r"^INFLUXDB_HTTP_PORT=.*\n?", "", env,
                flags=re.MULTILINE)
            self._atomic_write(self.env_path, env)
            updated.append(str(self.env_path.relative_to(self.root)))

        if not self.config_path.exists():
            config = yaml.safe_load(self.config_template.read_text())
            config["deployment"] = {"name": name, "type": kind}
            config["deployment_id"] = deployment_id
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
            config["deployment_id"] = deployment_id
            config["customer"] = config["site"] = self._slug(name)
            self._atomic_write(
                self.config_path, yaml.safe_dump(config, sort_keys=False))
            updated.append(str(self.config_path.relative_to(self.root)))
        return tuple(created), tuple(updated)

    @staticmethod
    def _env_values(text):
        return {
            key.strip(): value.strip().strip("'\"")
            for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
            and "=" in line
            for key, value in (line.split("=", 1),)
        }

    def _preserved_choices(
            self, name, kind, timezone, grafana_port, influx_port, interval,
            deployment_id, *, update_existing):
        if update_existing:
            return (
                name, kind, timezone, grafana_port, influx_port, interval,
                deployment_id)
        if self.env_path.exists():
            values = self._env_values(self.env_path.read_text())
            grafana_port = int(values.get("GRAFANA_PORT") or grafana_port)
            influx_port = int(
                values.get("INFLUXDB_PORT")
                or values.get("INFLUXDB_HTTP_PORT") or influx_port)
            timezone = values.get("TZ") or timezone
            interval = values.get("TELEGRAF_COLLECTION_INTERVAL") or interval
            deployment_id = values.get("ITP_DEPLOYMENT_ID") or deployment_id
        if self.config_path.exists():
            try:
                config = yaml.safe_load(self.config_path.read_text())
            except yaml.YAMLError:
                config = None
            deployment = config.get("deployment", {}) if isinstance(config, dict) else {}
            name = str(deployment.get("name") or name)
            kind = str(deployment.get("type") or kind)
        return (
            name, kind, timezone, grafana_port, influx_port, interval,
            deployment_id)

    def _existing_defaults(self, options):
        values = self._env_values(
            self.env_path.read_text()) if self.env_path.is_file() else {}
        deployment = {}
        if self.config_path.is_file():
            try:
                config = yaml.safe_load(self.config_path.read_text())
                if isinstance(config, dict) and isinstance(
                        config.get("deployment"), dict):
                    deployment = config["deployment"]
            except yaml.YAMLError:
                pass

        def integer(value):
            try:
                return int(value) if value not in (None, "") else None
            except ValueError:
                return None

        return replace(
            options,
            deployment_name=options.deployment_name
            if options.deployment_name is not None else deployment.get("name"),
            deployment_type=options.deployment_type
            if options.deployment_type is not None else deployment.get("type"),
            grafana_port=options.grafana_port
            if options.grafana_port is not None else integer(
                values.get("GRAFANA_PORT")),
            influxdb_port=options.influxdb_port
            if options.influxdb_port is not None else integer(
                values.get("INFLUXDB_PORT")
                or values.get("INFLUXDB_HTTP_PORT")),
            timezone=options.timezone
            if options.timezone is not None else values.get("TZ"),
            collection_interval=options.collection_interval
            if options.collection_interval is not None else values.get(
                "TELEGRAF_COLLECTION_INTERVAL"),
        )

    def validate_configuration(self, *, require_inert=False):
        try:
            config = yaml.safe_load(self.config_path.read_text())
        except (OSError, yaml.YAMLError) as exc:
            raise SetupError(f"invalid discovery configuration: {exc}") from exc
        if not isinstance(config, dict) or config.get("schema_version") != 1:
            raise SetupError("discovery configuration must use schema_version 1")
        if not isinstance(config.get("collectors"), dict):
            raise SetupError("discovery configuration requires collectors mapping")
        for name, settings in config["collectors"].items():
            if (require_inert and isinstance(settings, dict)
                    and settings.get("enabled") is True):
                raise SetupError(
                    f"External collector {name} is enabled. Fresh setup remains "
                    "inert; configure credentials and enable it explicitly "
                    "after setup.")
            if not (isinstance(settings, dict)
                    and settings.get("enabled") is True):
                continue
            try:
                metadata = self.connector_registry.get(name)
            except KeyError as exc:
                raise SetupError(
                    f"Enabled collector {name} is not registered.") from exc
            missing_config = []
            for dotted in metadata.configuration_fields:
                current = config
                for part in dotted.split("."):
                    if not isinstance(current, dict) or part not in current:
                        current = None
                        break
                    current = current[part]
                if current in (None, "", []):
                    missing_config.append(dotted)
            secret_values = {}
            secrets = self.root / "secrets"
            if secrets.is_symlink():
                raise SetupError(
                    "secrets/ must not be a symlink during root deployment setup.")
            for path in sorted(secrets.glob("*.env")) if secrets.is_dir() else ():
                if path.is_symlink():
                    raise SetupError(
                        f"Secret file must not be a symlink: {path.name}")
                secret_values.update(self._env_values(path.read_text()))
            missing_credentials = [
                field["env"] for field in metadata.credential_fields
                if field.get("required")
                and not secret_values.get(field["env"], "").strip()]
            if missing_config or missing_credentials:
                detail = []
                if missing_config:
                    detail.append("configuration: " + ", ".join(missing_config))
                if missing_credentials:
                    detail.append(
                        "credentials: " + ", ".join(missing_credentials))
                raise SetupError(
                    f"Enabled collector {name} is incomplete (" +
                    "; ".join(detail) + "). Disable it or complete its local "
                    "configuration before starting ITP.")
        values = self._env_values(self.env_path.read_text())
        try:
            warnings = []
            resolved = resolve_settings(values, warnings=warnings)
            self.validate_timezone(resolved.timezone)
            self.validate_interval(resolved.collection_interval)
            for warning in warnings:
                self.output("Warning: " + warning)
        except (SettingsError, SetupError) as exc:
            raise SetupError(str(exc)) from exc
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
        if existing:
            options = self._existing_defaults(options)
        choices = self._choices(options)
        existing_id = ""
        if self.env_path.exists():
            existing_id = self._env_values(
                self.env_path.read_text()).get("ITP_DEPLOYMENT_ID", "")
        deployment_id = existing_id or str(uuid.uuid4())
        choices = (*choices[:-1], deployment_id, choices[-1])
        name, kind, timezone, grafana_port, influx_port, interval, \
            deployment_id, demo = self._preserved_choices(
                *choices[:-1], update_existing=update_existing) + (choices[-1],)
        running = self._project_running()
        occupied = [
            candidate for candidate in (grafana_port, influx_port)
            if not self._port_available(candidate)]
        if occupied and not running:
            raise SetupError(
                "required port(s) already in use: " +
                ", ".join(str(value) for value in occupied))
        previous_env = self.env_path.read_text() if self.env_path.is_file() else None
        previous_config = (
            self.config_path.read_text() if self.config_path.is_file() else None)
        try:
            created, updated = self._write_configuration(
                name, kind, timezone, grafana_port, influx_port, interval,
                deployment_id, update_existing=update_existing)
            self.validate_configuration(require_inert=first_run)
        except Exception:
            if update_existing:
                if previous_env is not None:
                    self._atomic_write(self.env_path, previous_env)
                if previous_config is not None:
                    self._atomic_write(self.config_path, previous_config)
            raise
        if self.provision_fn:
            self.provision_fn()
        should_start = options.start
        if should_start is None:
            should_start = False if options.non_interactive else not \
                str(self.input("Start ITP services now? [Y/n]: ")).strip() \
                .casefold() in {"n", "no"}
        if should_start:
            if self.start_fn:
                self.start_fn()
                self.wait_healthy(options.health_timeout)
            else:
                self._run(["docker", "compose", "up", "-d", "--build"])
                self.wait_healthy(options.health_timeout)
        if demo and self.demo_fn:
            self.demo_fn()
        url = f"http://localhost:{grafana_port}"
        influx_url = f"http://localhost:{influx_port}"
        self.output("Deployment created successfully")
        self.output(f"Deployment:\n  Name: {name}\n  Type: {kind}")
        self.output(f"  Timezone: {timezone}\n  Deployment ID: {deployment_id}")
        self.output(
            f"Services:\n  Grafana: {url}\n  InfluxDB: {influx_url}")
        self.output(f"Collection interval:\n  {interval}")
        self.output("External connectors:\n  None enabled")
        if not should_start:
            self.output("Next: ./itp start")
        self.output(
            "Next: configure collector credentials under secrets/ and enable "
            "collectors in discovery/config.yml.")
        self.output(
            f"Connector catalogue: {len(self.connector_registry.all())} "
            "registered; guided credential onboarding is not enabled yet.")
        return SetupResult(
            first_run, created, updated, bool(should_start), url,
            deployment_id, timezone, influx_url, interval, bool(demo))
