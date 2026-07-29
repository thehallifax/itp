"""Registry-driven, read-only local deployment doctor."""
from __future__ import annotations

import importlib.util
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from collectors.configuration import ConfigurationResolver
from collectors.connector_registry import ConnectorMetadataRegistry
from itp_profiles.settings import SettingsError, resolve_settings

from .models import DiagnosticCheck, DoctorReport


EXPECTED_SERVICES = (
    "collector", "discovery", "grafana", "influxdb3-core", "telegraf")
REQUIRED_LAYOUT = (
    "collectors", "analysis", "discovery", "docs", "grafana", "secrets",
    "docker-compose.yml")
REQUIRED_TEMPLATES = (
    ".env.example", "discovery/config.example.yml",
    "secrets/mist.env.example", "secrets/fortigate.env.example",
    "secrets/paloalto.env.example", "secrets/snmp.env.example")


class DoctorUsageError(ValueError):
    pass


class DoctorFatalError(RuntimeError):
    pass


class Redactor:
    def __init__(self, registry):
        names = {
            name for connector in registry.all()
            for field in connector.credential_fields
            if field.get("secret")
            for name in (field.get("env"), *field.get("env_aliases", []))
            if name}
        names.update(name for name in os.environ if any(
            word in name.upper() for word in
            ("TOKEN", "PASSWORD", "SECRET", "COMMUNITY", "PRIVATE_KEY")))
        self.values = tuple(sorted(
            {os.getenv(name, "") for name in names if len(os.getenv(name, "")) >= 4},
            key=len, reverse=True))

    def __call__(self, value):
        text = str(value or "")
        for secret in self.values:
            text = text.replace(secret, "[REDACTED]")
        text = re.sub(
            r"(?i)(token|password|secret|community|api[_-]?key)"
            r"(\s*[=:]\s*)([^,\s;]+)",
            r"\1\2[REDACTED]", text)
        return text


class DoctorEngine:
    def __init__(self, root, *, offline=False, platform_only=False,
                 connectors_only=False, connector=None, runner=subprocess.run,
                 which_fn=shutil.which, http_fn=None, now_fn=None,
                 validation_adapters=None, timeout=3, registry=None,
                 env_path=None, config_path=None, runtime_deployment=None):
        self.root = Path(root).resolve()
        self.offline = bool(offline)
        self.platform_only = bool(platform_only)
        self.connectors_only = bool(connectors_only)
        self.connector_selector = connector
        self.runner = runner
        self.which = which_fn
        self.http = http_fn or self._http
        self.now = now_fn or (
            lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
        self.validation_adapters = validation_adapters or {}
        self.timeout = max(0.1, float(timeout))
        self.env_path = Path(env_path or self.root / ".env")
        self.config_path = Path(
            config_path or self.root / "discovery/config.yml")
        self.runtime_deployment = runtime_deployment
        self.port_in_use = lambda port: self._port_in_use(port)
        try:
            self.registry = registry or ConnectorMetadataRegistry.load(self.root)
        except Exception as exc:
            raise DoctorFatalError(
                f"connector registry could not be loaded: {type(exc).__name__}") from exc
        self.redact = Redactor(self.registry)
        self.checks = []
        self.errors = []
        self.raw_config = None
        self.env_values = {}
        if connector:
            try:
                self.selected_connector = self.registry.get(connector)
            except KeyError as exc:
                raise DoctorUsageError(f"unknown connector: {connector}") from exc
        else:
            self.selected_connector = None

    def _result(self, check_id, category, subject, status, summary, *,
                detail="", remediation="", command="", metadata=None,
                duration=0, exception_type=""):
        if status == "pass":
            remediation = command = ""
        severity = "error" if status == "fail" else (
            "warning" if status in {"warn", "unavailable"} else "info")
        self.checks.append(DiagnosticCheck(
            check_id, category, subject, status, severity,
            self.redact(summary), self.redact(detail),
            self.redact(remediation), self.redact(command),
            metadata or {}, int(duration * 1000), exception_type))

    def _isolated(self, check_id, category, subject, function):
        started = time.monotonic()
        try:
            function()
        except Exception as exc:
            self._result(
                check_id, category, subject, "fail", "Check failed",
                detail=self.redact(str(exc)),
                remediation="Review local configuration and rerun doctor.",
                duration=time.monotonic() - started,
                exception_type=type(exc).__name__)
            self.errors.append({
                "check_id": check_id, "exception_type": type(exc).__name__,
                "detail": self.redact(str(exc))})

    def _command(self, command):
        return self.runner(
            command, cwd=self.root, check=True, text=True,
            encoding="utf-8", errors="replace",
            capture_output=True, timeout=self.timeout)

    def _compose_command(self, *arguments):
        if self.runtime_deployment is not None:
            return self.runtime_deployment.compose_command(*arguments)
        return ["docker", "compose", *arguments]

    @staticmethod
    def _read_env(path):
        values = {}
        if not path.is_file():
            return values
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("'\"")
        return values

    def _platform_checks(self):
        version_ok = sys.version_info >= (3, 9)
        self._result(
            "platform.python", "Platform", "Python",
            "pass" if version_ok else "fail",
            f"Python {platform.python_version()} is "
            + ("supported" if version_ok else "unsupported"),
            remediation="" if version_ok else "Install Python 3.9 or later.")

        missing = [value for value in REQUIRED_LAYOUT
                   if not (self.root / value).exists()]
        self._result(
            "platform.layout", "Platform", "Repository layout",
            "pass" if not missing else "fail",
            "Required repository paths are present" if not missing
            else "Required repository paths are missing",
            detail=", ".join(missing),
            remediation="Restore missing tracked files with Git.")

        templates = [value for value in REQUIRED_TEMPLATES
                     if not (self.root / value).is_file()]
        self._result(
            "platform.templates", "Platform", "Tracked templates",
            "pass" if not templates else "fail",
            "Required templates are present" if not templates
            else "Required templates are missing",
            detail=", ".join(templates),
            remediation="Restore tracked templates with Git.")

        env_path = self.env_path
        self.env_values = self._read_env(env_path)
        self._result(
            "platform.env", "Platform", ".env",
            "pass" if env_path.is_file() and os.access(env_path, os.R_OK) else "warn",
            "Root environment file is readable" if env_path.is_file()
            else "Root environment file is absent",
            remediation="Run ./itp setup" if not env_path.is_file() else "")
        settings = None
        settings_warnings = []
        if env_path.is_file():
            try:
                settings = resolve_settings(
                    self.env_values, warnings=settings_warnings)
                self._result(
                    "platform.settings", "Platform",
                    "Required deployment settings", "pass",
                    "Required deployment settings are complete",
                    metadata={
                        "database": settings.database,
                        "organization": settings.organization,
                    })
            except SettingsError as exc:
                self._result(
                    "platform.settings", "Platform",
                    "Required deployment settings", "fail",
                    "Required deployment settings are invalid",
                    detail=str(exc),
                    remediation="Rerun setup to repair blank or conflicting values.",
                    command="./itp setup --force")
        else:
            self._result(
                "platform.settings", "Platform",
                "Required deployment settings", "warn",
                "Deployment settings have not been generated",
                remediation="Run setup.", command="./itp setup")
        for index, warning in enumerate(settings_warnings):
            self._result(
                f"platform.settings.warning.{index}", "Platform",
                "Deprecated deployment setting", "warn", warning,
                command="./itp setup --force")

        deployment_id = (
            settings.deployment_id if settings else
            self.env_values.get("ITP_DEPLOYMENT_ID", "").strip())
        self._result(
            "platform.deployment_identity", "Platform", "Deployment identity",
            "pass" if deployment_id else "fail",
            "Stable deployment identity is configured" if deployment_id
            else "Deployment identity is missing",
            detail=deployment_id,
            command="./itp setup --force")

        timezone_name = self.env_values.get("TZ", "").strip()
        try:
            if not timezone_name:
                raise ValueError("TZ is blank")
            ZoneInfo(timezone_name)
            timezone_status, timezone_summary = (
                "pass", f"Timezone {timezone_name} is valid")
        except (ZoneInfoNotFoundError, ValueError):
            timezone_status, timezone_summary = (
                "fail", "Timezone is missing or invalid")
        self._result(
            "platform.timezone", "Platform", "Timezone",
            timezone_status, timezone_summary,
            remediation="Set TZ to an IANA timezone with ./itp setup --force.")

        datasource_path = (
            self.root / "grafana/provisioning/datasources/influxdb.yml")
        try:
            datasource = yaml.safe_load(datasource_path.read_text())
            influx = next(
                item for item in datasource["datasources"]
                if item.get("uid") == "ffsu5ap2kr5dse")
            configured = influx["jsonData"]["dbName"]
            datasource_ok = configured == "${INFLUXDB_BUCKET}"
            datasource_detail = str(configured)
        except (OSError, KeyError, StopIteration, TypeError, yaml.YAMLError) as exc:
            datasource_ok = False
            datasource_detail = str(exc)
        self._result(
            "platform.grafana_datasource", "Platform",
            "Grafana InfluxDB datasource",
            "pass" if datasource_ok else "fail",
            "Grafana datasource follows the configured database"
            if datasource_ok else "Grafana datasource database may disagree",
            detail=datasource_detail,
            remediation="Restore the managed datasource template.")

        try:
            from collectors.__main__ import build_parser
            build_parser()
            parser_ok, parser_detail = True, ""
        except Exception as exc:
            parser_ok, parser_detail = False, str(exc)
        self._result(
            "platform.collector_parser", "Platform", "Collector CLI parser",
            "pass" if parser_ok else "fail",
            "Collector CLI parser constructs successfully" if parser_ok
            else "Collector CLI parser cannot start",
            detail=parser_detail,
            remediation="Restore the collector CLI parser definitions.")

        config_path = self.config_path
        if not config_path.is_file():
            self._result(
                "platform.config", "Platform", "Discovery configuration",
                "warn", "discovery/config.yml is absent",
                remediation="Run ./itp setup", command="./itp setup")
        else:
            try:
                value = yaml.safe_load(config_path.read_text())
                if not isinstance(value, dict):
                    raise ValueError("configuration must be a mapping")
                self.raw_config = value
                valid = value.get("schema_version") == 1 and isinstance(
                    value.get("collectors"), dict)
                self._result(
                    "platform.config", "Platform", "Discovery configuration",
                    "pass" if valid else "fail",
                    "Configuration schema is valid" if valid
                    else "Configuration schema is invalid",
                    remediation="Restore schema_version 1 and a collectors mapping.")
            except (OSError, yaml.YAMLError, ValueError) as exc:
                self._result(
                    "platform.config", "Platform", "Discovery configuration",
                    "fail", "Configuration cannot be parsed",
                    detail=str(exc), remediation="Correct discovery/config.yml.")

        compose_path = self.root / "docker-compose.yml"
        try:
            compose = yaml.safe_load(compose_path.read_text())
            valid = isinstance(compose, dict) and isinstance(
                compose.get("services"), dict)
            self._result(
                "platform.compose_file", "Platform", "Docker Compose file",
                "pass" if valid else "fail",
                "Compose YAML is parseable" if valid else "Compose services are invalid")
        except Exception as exc:
            self._result(
                "platform.compose_file", "Platform", "Docker Compose file",
                "fail", "Compose YAML cannot be parsed", detail=str(exc))

        try:
            count = len(self.registry.all())
            self._result(
                "platform.registry", "Platform", "Connector registry",
                "pass", f"{count} connector definitions are valid")
        except Exception as exc:
            self._result(
                "platform.registry", "Platform", "Connector registry",
                "fail", "Registry validation failed", detail=str(exc))

        required_dirs = ("collectors", "analysis", "discovery", "docs", "secrets")
        writable = [value for value in required_dirs
                    if not os.access(self.root / value, os.R_OK)]
        self._result(
            "platform.directories", "Platform", "Required directories",
            "pass" if not writable else "fail",
            "Required directories are readable" if not writable
            else "Required directories are inaccessible",
            detail=", ".join(writable))

        runtime = Path(os.getenv("ITP_RUNTIME_DIR", self.root / "runtime"))
        runtime_ok = runtime.is_dir() and os.access(runtime, os.W_OK)
        self._result(
            "platform.runtime", "Platform", "Runtime directories",
            "pass" if runtime_ok else "warn",
            "Runtime directory is writable" if runtime_ok
            else "Runtime directory is missing or not writable",
            detail=str(runtime), remediation="Run ./itp setup")
        demo_state = self.root / "runtime/demo/demo.json"
        self._result(
            "platform.demo", "Platform", "Demonstration environment",
            "warn" if demo_state.is_file() else "pass",
            "Demonstration environment is present"
            if demo_state.is_file() else "Demonstration environment is not loaded",
            detail=str(demo_state) if demo_state.is_file() else "",
            command="./itp demo reset" if demo_state.is_file() else "")
        provisioning = runtime / "provisioning/state.json"
        if self.runtime_deployment is not None:
            generated = self.runtime_deployment.generated
            complete = (
                self.runtime_deployment.env_file.is_file()
                and (generated / "dashboard/provisioning/dashboards.yml").is_file()
            )
            summary = (
                "Runtime deployment provisioning is complete" if complete
                else "Runtime deployment provisioning is incomplete")
            detail = "" if complete else str(generated)
        else:
            try:
                state = json.loads(provisioning.read_text())
                complete = state.get("status") == "complete"
                summary = (
                    "Provisioning is complete" if complete
                    else "Provisioning is incomplete")
                detail = ", ".join(state.get("missing") or [])
            except (OSError, json.JSONDecodeError):
                complete, summary, detail = (
                    False, "Provisioning has not completed", str(provisioning))
        self._result(
            "platform.provisioning", "Platform", "Automatic provisioning",
            "pass" if complete else "warn", summary, detail=detail,
            remediation="Run ./itp start", command="./itp start")

        ports = []
        for key, default in (("GRAFANA_PORT", 3000), ("INFLUXDB_PORT", 8181)):
            try:
                ports.append((key, int(self.env_values.get(key, default))))
            except ValueError:
                ports.append((key, default))
        conflicts = []
        for key, port in ports:
            if self.port_in_use(port):
                conflicts.append(f"{key}:{port}")
        self._result(
            "platform.ports", "Platform", "Required ports",
            "pass" if self.runtime_deployment is not None or not conflicts
            else "warn",
            "Configured service ports are listening"
            if conflicts and self.runtime_deployment is not None
            else "Configured ports are already in use" if conflicts
            else "Configured ports are available",
            detail=", ".join(conflicts),
            remediation="Stop the conflicting service or choose another port.")

        self._profile_checks()

    @staticmethod
    def _port_in_use(port):
        with socket.socket() as connection:
            connection.settimeout(0.1)
            return connection.connect_ex(("127.0.0.1", port)) == 0

    def _profile_checks(self):
        profile_id = os.getenv("ITP_PROFILE", "").strip()
        if not profile_id:
            self._result(
                "platform.profile", "Platform", "Deployment profile",
                "skip", "No deployment profile is selected")
            return
        if self.runtime_deployment is not None:
            valid = self.runtime_deployment.manifest.is_file()
            self._result(
                "platform.profile", "Platform", "Runtime deployment",
                "pass" if valid else "fail",
                f"Runtime deployment {profile_id} is locally valid"
                if valid else "Runtime deployment manifest is missing",
                detail=str(self.runtime_deployment.manifest))
            return
        try:
            from itp_profiles import DeploymentProfile
            value = DeploymentProfile.load(profile_id, self.root)
            self._result(
                "platform.profile", "Platform", "Deployment profile",
                "pass", f"Profile {value.id} is locally valid")
        except Exception as exc:
            self._result(
                "platform.profile", "Platform", "Deployment profile",
                "fail", "Selected profile is invalid", detail=str(exc),
                command=f"./itp profile validate {profile_id}")

    def _service_checks(self):
        if self.offline:
            for check_id, subject in (
                    ("services.daemon", "Docker daemon"),
                    ("services.compose", "Compose services"),
                    ("services.containers", "Container state"),
                    ("services.ports", "Published service ports"),
                    ("services.http", "HTTP health endpoints")):
                self._result(
                    check_id, "Services", subject, "skip",
                    "Skipped in offline mode")
            return
        docker = self.which("docker")
        if not docker:
            self._result(
                "services.docker", "Services", "Docker CLI", "fail",
                "Docker is unavailable", remediation="Install Docker.")
            return
        try:
            self._command(["docker", "--version"])
            self._result(
                "services.docker", "Services", "Docker CLI", "pass",
                "Docker CLI is available")
        except Exception as exc:
            self._result(
                "services.docker", "Services", "Docker CLI", "fail",
                "Docker CLI failed", detail=str(exc))
            return
        try:
            self._command(["docker", "compose", "version"])
            self._result(
                "services.compose_v2", "Services", "Docker Compose v2",
                "pass", "Docker Compose v2 is available")
        except Exception as exc:
            self._result(
                "services.compose_v2", "Services", "Docker Compose v2",
                "fail", "Docker Compose v2 is unavailable", detail=str(exc))
            return
        try:
            self._command(["docker", "info"])
            self._result(
                "services.daemon", "Services", "Docker daemon", "pass",
                "Docker daemon is reachable")
        except Exception as exc:
            self._result(
                "services.daemon", "Services", "Docker daemon", "fail",
                "Docker daemon is unavailable", detail=str(exc))
            return
        try:
            output = self._command(
                self._compose_command("config", "--services")).stdout
            services = tuple(sorted(line.strip() for line in output.splitlines()
                                    if line.strip()))
            missing = sorted(set(EXPECTED_SERVICES) - set(services))
            self._result(
                "services.compose", "Services", "Compose services",
                "pass" if not missing else "fail",
                "Expected Compose services are declared" if not missing
                else "Expected Compose services are missing",
                detail=", ".join(missing))
        except Exception as exc:
            self._result(
                "services.compose", "Services", "Compose services",
                "fail", "Compose services cannot be resolved", detail=str(exc))
        self._container_checks()
        published = [
            port for port in (
                int(self.env_values.get("GRAFANA_PORT", 3000)),
                int(self.env_values.get("INFLUXDB_PORT", 8181)))
            if self.port_in_use(port)]
        self._result(
            "services.published_ports", "Services", "Published service ports",
            "pass" if len(published) == 2 else "warn",
            "Grafana and InfluxDB ports are reachable"
            if len(published) == 2 else "One or more published ports are absent",
            detail=", ".join(str(value) for value in published),
            command="docker compose ps")
        self._http_checks()

    def _container_checks(self):
        try:
            text = self._command(
                self._compose_command("ps", "--format", "json")).stdout.strip()
            if not text:
                rows = []
            else:
                try:
                    value = json.loads(text)
                    rows = value if isinstance(value, list) else [value]
                except json.JSONDecodeError:
                    rows = [json.loads(line) for line in text.splitlines()]
            states = {
                str(row.get("Service") or row.get("Name")): row for row in rows}
            for service in EXPECTED_SERVICES:
                row = states.get(service)
                if row is None:
                    self._result(
                        f"services.container.{service}", "Services", service,
                        "warn", "Container does not exist",
                        command="docker compose up -d")
                    continue
                state = str(row.get("State") or "").casefold()
                health = str(row.get("Health") or "").casefold()
                status = "pass"
                summary = "Container is running"
                if state != "running":
                    status, summary = "fail", f"Container state is {state or 'unknown'}"
                elif health == "unhealthy":
                    status, summary = "fail", "Container is unhealthy"
                elif health == "starting":
                    status, summary = "warn", "Container health is starting"
                self._result(
                    f"services.container.{service}", "Services", service,
                    status, summary, command=f"docker compose logs {service}")
        except Exception as exc:
            self._result(
                "services.containers", "Services", "Container state",
                "fail", "Container state could not be read", detail=str(exc))

    def _http(self, url, timeout):
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status

    def _http_checks(self):
        influx_port = int(self.env_values.get(
            "INFLUXDB_PORT", os.getenv("INFLUXDB_PORT", 8181)))
        grafana_port = int(self.env_values.get(
            "GRAFANA_PORT", os.getenv("GRAFANA_PORT", 3000)))
        endpoints = (
            ("influxdb", f"http://127.0.0.1:{influx_port}/health"),
            ("grafana", f"http://127.0.0.1:{grafana_port}/api/health"))
        for name, url in endpoints:
            try:
                status = self.http(url, self.timeout)
                self._result(
                    f"services.http.{name}", "Services", f"{name} HTTP",
                    "pass" if status < 400 else "fail",
                    f"HTTP endpoint returned {status}",
                    metadata={"endpoint": url})
            except Exception as exc:
                self._result(
                    f"services.http.{name}", "Services", f"{name} HTTP",
                    "warn", "HTTP endpoint is unreachable",
                    detail=str(exc), metadata={"endpoint": url})

    @staticmethod
    def _path(value, dotted):
        current = value
        for part in dotted.split("."):
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        return current

    def _connector_resolution(self):
        profile_values = {}
        profile = os.getenv("ITP_PROFILE", "").strip()
        if profile:
            profile_env = self.root / "profiles" / profile / ".env"
            profile_values.update(self._read_env(profile_env))
            secret_root = self.root / "secrets" / profile
            for path in sorted(secret_root.glob("*.env")) \
                    if secret_root.exists() else ():
                profile_values.update(self._read_env(path))
        root_values = dict(self.env_values)
        secret_root = self.root / "secrets"
        for path in sorted(secret_root.glob("*.env")) \
                if secret_root.exists() else ():
            root_values.update(self._read_env(path))
        return ConfigurationResolver(
            self.registry, self.raw_config or {},
            process_environment=os.environ,
            profile_environment=profile_values,
            root_environment=root_values).evaluate()

    def _connector_checks(self):
        configured = (self.raw_config or {}).get("collectors", {})
        resolved = {
            item["connector"]: item
            for item in self._connector_resolution()["connectors"]}
        if self.selected_connector:
            connectors = (self.selected_connector,)
        else:
            connectors = tuple(
                connector for connector in self.registry.all()
                if connector.id in configured)
        if not connectors:
            self._result(
                "connectors.none", "Connectors", "Connector configuration",
                "skip", "No configured connectors were found")
            return
        for connector in connectors:
            settings = configured.get(connector.id)
            explicitly_selected = self.selected_connector is not None
            if settings is None:
                self._result(
                    f"connector.{connector.id}.configured", "Connectors",
                    connector.display_name, "warn",
                    "Connector is not present in local configuration",
                    command=f"python -m collectors connectors inspect {connector.id}")
                continue
            enabled = isinstance(settings, dict) and settings.get("enabled") is True
            if not enabled and not explicitly_selected:
                self._result(
                    f"connector.{connector.id}.enabled", "Connectors",
                    connector.display_name, "skip", "Connector is disabled")
                continue
            missing_config = [
                path for path in connector.configuration_fields
                if self._path(self.raw_config or {}, path) in (None, "", [])]
            self._result(
                f"connector.{connector.id}.configuration", "Connectors",
                connector.display_name,
                "warn" if missing_config else "pass",
                "Required local configuration is incomplete" if missing_config
                else "Required local configuration is present",
                detail=", ".join(missing_config),
                remediation="Complete the documented connector configuration.",
                command=connector.remediation_command)
            resolved_settings = {
                item["name"].rsplit(".", 1)[-1]: item
                for item in resolved[connector.id]["settings"]}
            missing_credentials = [
                field["env"] for field in connector.credential_fields
                if field.get("required") and resolved_settings[
                    field["id"]]["status"] == "missing"]
            self._result(
                f"connector.{connector.id}.credentials", "Connectors",
                connector.display_name,
                "warn" if missing_credentials else "pass",
                "Required credentials are missing" if missing_credentials
                else "Required credential references are present",
                detail=", ".join(missing_credentials),
                remediation="Add required values to the connector's ignored secret file.",
                command=connector.remediation_command)
            if not connector.capabilities["doctor"]:
                self._result(
                    f"connector.{connector.id}.doctor", "Connectors",
                    connector.display_name, "unavailable",
                    "No connector doctor adapter is declared",
                    detail=connector.notes)
            complete = not missing_config and not missing_credentials
            adapter = self.validation_adapters.get(connector.id)
            if self.offline:
                self._result(
                    f"connector.{connector.id}.validation", "Connectors",
                    connector.display_name, "skip",
                    "Live validation skipped in offline mode")
            elif not connector.capabilities["validation"]:
                self._result(
                    f"connector.{connector.id}.validation", "Connectors",
                    connector.display_name, "unavailable",
                    "Live validation is not supported")
            elif not complete:
                self._result(
                    f"connector.{connector.id}.validation", "Connectors",
                    connector.display_name, "skip",
                    "Live validation skipped because configuration is incomplete")
            elif adapter is None:
                self._result(
                    f"connector.{connector.id}.validation", "Connectors",
                    connector.display_name, "unavailable",
                    "No safe doctor validation adapter is registered")
            else:
                started = time.monotonic()
                try:
                    adapter(self.raw_config, timeout=self.timeout)
                    self._result(
                        f"connector.{connector.id}.validation", "Connectors",
                        connector.display_name, "pass",
                        "Live validation succeeded",
                        duration=time.monotonic() - started)
                except TimeoutError as exc:
                    self._result(
                        f"connector.{connector.id}.validation", "Connectors",
                        connector.display_name, "fail",
                        "Live validation timed out", detail=str(exc),
                        duration=time.monotonic() - started,
                        exception_type=type(exc).__name__)
                except Exception as exc:
                    self._result(
                        f"connector.{connector.id}.validation", "Connectors",
                        connector.display_name, "fail",
                        "Live validation failed", detail=str(exc),
                        duration=time.monotonic() - started,
                        exception_type=type(exc).__name__)

    def _state_history_checks(self):
        try:
            importable = importlib.util.find_spec(
                "analysis.state_history.pipeline") is not None
        except Exception:
            importable = False
        self._result(
            "state_history.module", "State History", "Pipeline module",
            "pass" if importable else "fail",
            "State-history pipeline is importable" if importable
            else "State-history pipeline is unavailable")
        value = (self.raw_config or {}).get("state_history")
        if value is None:
            self._result(
                "state_history.configuration", "State History", "Configuration",
                "skip", "State history is not configured")
            return
        if not isinstance(value, dict):
            self._result(
                "state_history.configuration", "State History", "Configuration",
                "fail", "State-history configuration must be a mapping")
            return
        enabled = value.get("enabled", False)
        self._result(
            "state_history.configuration", "State History", "Configuration",
            "pass" if isinstance(enabled, bool) else "fail",
            "State history is enabled" if enabled is True
            else "State history is disabled" if enabled is False
            else "State-history enabled flag is invalid")
        if enabled is not True:
            return
        path = Path(value.get("store_path", self.root / "runtime/state-history"))
        if not path.is_absolute():
            path = self.root / path
        status = "pass" if (path.exists() and os.access(path, os.R_OK)) else "warn"
        self._result(
            "state_history.store", "State History", "Filesystem store",
            status, "State-history store is readable" if status == "pass"
            else "State-history store does not exist yet",
            metadata={"path": str(path)})
        latest = path / "latest"
        invalid = []
        if latest.exists():
            for pointer in sorted(latest.glob("*.json")):
                try:
                    reference = json.loads(pointer.read_text())
                    snapshot = path / "snapshots" / (
                        str(reference["snapshot_id"]) + ".json")
                    if not snapshot.is_file():
                        invalid.append(pointer.name)
                except Exception:
                    invalid.append(pointer.name)
        self._result(
            "state_history.latest", "State History", "Latest pointers",
            "fail" if invalid else "pass",
            "Latest pointers are inconsistent" if invalid
            else "Existing latest pointers are consistent",
            detail=", ".join(invalid))

    def _scheduler_checks(self):
        runtime = Path(os.getenv("ITP_RUNTIME_DIR", self.root / "runtime"))
        path = runtime / "scheduler/state.json"
        if not path.is_file():
            self._result(
                "scheduler.state", "Scheduler", "Runtime state", "skip",
                "Scheduler has not written runtime state")
            return
        try:
            state = json.loads(path.read_text())
            if not isinstance(state, dict):
                raise ValueError("state is not a mapping")
            updated = datetime.fromisoformat(
                str(state.get("updated_at") or "").replace("Z", "+00:00")) \
                if state.get("updated_at") else None
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            self._result(
                "scheduler.state", "Scheduler", "Runtime state", "fail",
                "Scheduler runtime state is invalid",
                exception_type=type(exc).__name__)
            return
        lifecycle = state.get("lifecycle_state", "unknown")
        generated = datetime.fromisoformat(
            str(self.now()).replace("Z", "+00:00"))
        age = (
            generated.astimezone(timezone.utc)
            - updated.astimezone(timezone.utc)
        ).total_seconds() if updated else None
        stale = lifecycle in {
            "ready", "degraded", "starting",
            "initial_discovery", "initial_collection",
        } and (age is None or age > 900)
        status = (
            "warn" if stale or lifecycle == "degraded"
            else "pass" if lifecycle == "ready"
            else "skip" if lifecycle == "stopped"
            else "warn")
        self._result(
            "scheduler.state", "Scheduler", "Runtime state", status,
            "Scheduler runtime state is stale" if stale
            else f"Scheduler lifecycle is {lifecycle}",
            detail=(
                "initial_discovery="
                f"{(state.get('initial_discovery') or {}).get('outcome', 'unknown')} "
                "initial_collection="
                f"{(state.get('initial_collection') or {}).get('outcome', 'unknown')} "
                "discovery_failures="
                f"{state.get('consecutive_discovery_failures', 0)} "
                "collection_failures="
                f"{state.get('consecutive_collection_failures', 0)} "
                f"last_skip_reason={state.get('last_skip_reason') or 'none'}"),
            metadata={
                "lifecycle_state": lifecycle,
                "last_successful_discovery":
                    state.get("last_successful_discovery"),
                "last_successful_collection":
                    state.get("last_successful_collection"),
                "last_skip_reason": state.get("last_skip_reason"),
            })

    def _operations_checks(self):
        module = importlib.util.find_spec("analysis.operations.engine") is not None
        definitions = importlib.util.find_spec(
            "analysis.services.evaluators") is not None
        self._result(
            "operations.module", "Operations Engine", "Operations module",
            "pass" if module else "fail",
            "Operations Engine is importable" if module
            else "Operations Engine is unavailable")
        self._result(
            "operations.services", "Operations Engine", "Service definitions",
            "pass" if definitions else "fail",
            "Service definitions are available" if definitions
            else "Service definitions are unavailable")
        value = (self.raw_config or {}).get("operations")
        self._result(
            "operations.configuration", "Operations Engine", "Configuration",
            "pass" if value is None or isinstance(value, dict) else "fail",
            "Operations configuration is parseable"
            if value is None or isinstance(value, dict)
            else "Operations configuration must be a mapping")

    def _telemetry_contract_checks(self):
        config = self.raw_config or {}
        deployment_id = str(
            config.get("deployment_id")
            or self.env_values.get("ITP_DEPLOYMENT_ID") or "").strip()
        customer_id = str(config.get("customer_id") or "").strip()
        site_id = str(config.get("site_id") or "").strip()
        missing = [
            name for name, value in (
                ("deployment_id", deployment_id),
                ("customer_id", customer_id),
                ("site_id", site_id)) if not value]
        self._result(
            "telemetry.deployment_identity", "Telemetry",
            "Canonical deployment identity",
            "pass" if not missing else "warn",
            "Canonical deployment identity is configured"
            if not missing else "Canonical deployment identity is incomplete",
            detail="missing=" + (",".join(missing) or "none"),
            remediation=(
                "Regenerate deployment configuration with ./itp deploy."
                if missing else ""))
        canonical_site = bool(site_id.startswith("site:"))
        self._result(
            "telemetry.site_identity", "Telemetry", "Canonical site ID",
            "pass" if canonical_site else "warn",
            f"Canonical site ID is {site_id}" if canonical_site
            else "Canonical site ID is unavailable or legacy",
            remediation=(
                "Configure a stable site_id using the site:<id> format."
                if not canonical_site else ""))

        runtime = str(
            self.env_values.get("ITP_RUNTIME_MODE")
            or os.getenv("ITP_RUNTIME_MODE", "central")).casefold()
        mismatches = []
        for connector in self.registry.all():
            settings = (config.get("collectors") or {}).get(
                connector.id) or {}
            if not settings.get("enabled"):
                continue
            execution = str(settings.get("execution") or runtime).casefold()
            if execution not in connector.runtime_modes:
                mismatches.append(
                    f"{connector.id}:{execution} not in "
                    f"{','.join(connector.runtime_modes)}")
        self._result(
            "telemetry.runtime_capabilities", "Telemetry",
            "Collector runtime capabilities",
            "pass" if not mismatches else "warn",
            "Enabled collectors match declared runtime capabilities"
            if not mismatches else "Collector runtime capability mismatch",
            detail="; ".join(mismatches),
            remediation=(
                "Move the collector to a supported runtime or update its "
                "explicit execution placement." if mismatches else ""))
        try:
            from telemetry.schema import MEASUREMENTS, SCHEMA_VERSION
            schema_ok = SCHEMA_VERSION == 1 and "collector_health" in MEASUREMENTS
        except (ImportError, AttributeError):
            schema_ok = False
        self._result(
            "telemetry.schema", "Telemetry", "Canonical schema",
            "pass" if schema_ok else "fail",
            "Canonical telemetry schema is available" if schema_ok
            else "Canonical telemetry schema is unavailable",
            remediation=(
                "Restore the tracked telemetry schema modules."
                if not schema_ok else ""))

    def run(self):
        try:
            if not self.connectors_only:
                self._platform_checks()
                if not self.platform_only:
                    self._service_checks()
            if not self.platform_only:
                if self.raw_config is None:
                    config_path = self.config_path
                    if config_path.is_file():
                        try:
                            value = yaml.safe_load(config_path.read_text())
                            self.raw_config = value if isinstance(value, dict) else None
                        except Exception:
                            pass
                self._connector_checks()
                if not self.connectors_only:
                    self._state_history_checks()
                    self._scheduler_checks()
                    self._operations_checks()
                    self._telemetry_contract_checks()
            checks = tuple(sorted(self.checks, key=lambda value: value.check_id))
            identity = str(
                (self.raw_config or {}).get("deployment", {}).get("name")
                if isinstance((self.raw_config or {}).get("deployment"), dict)
                else "") or os.getenv("ITP_PROFILE", "") or "root"
            return DoctorReport(
                self.now(), identity,
                {"offline": self.offline,
                 "platform_only": self.platform_only,
                 "connectors_only": self.connectors_only,
                 "connector": self.selected_connector.id
                 if self.selected_connector else ""},
                checks, tuple(sorted(self.errors, key=lambda value: value["check_id"])))
        except DoctorUsageError:
            raise
        except Exception as exc:
            raise DoctorFatalError(
                f"doctor could not produce a report: {type(exc).__name__}") from exc
