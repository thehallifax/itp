"""Docker Compose lifecycle and idempotent local provisioning."""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from analysis.dashboards import DashboardRegistry
from collectors.writer import atomic_write
from itp_profiles.settings import (
    SettingsError, load_env_file, resolve_settings)


PROVISIONING_VERSION = 1
SERVICES = ("collector", "discovery", "grafana", "influxdb3-core", "telegraf")


def _utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class DeploymentError(ValueError):
    pass


class DockerCompose:
    def __init__(self, root, *, runner=subprocess.run, which_fn=shutil.which,
                 environment=None):
        self.root = Path(root)
        self.runner = runner
        self.which = which_fn
        self.environment = environment or (lambda: dict(os.environ))

    def run(self, *arguments, check=True, capture=True):
        return self.runner(
            ["docker", "compose", *arguments], cwd=self.root,
            env=self.environment(), check=check, text=True,
            encoding="utf-8", errors="replace",
            capture_output=capture)

    def verify(self, daemon=True):
        if not self.which("docker"):
            raise DeploymentError(
                "Docker is unavailable. Install Docker Desktop or Docker Engine.")
        try:
            self.runner(["docker", "--version"], cwd=self.root,
                        check=True, text=True, encoding="utf-8",
                        errors="replace", capture_output=True)
            self.runner(["docker", "compose", "version"], cwd=self.root,
                        check=True, text=True, encoding="utf-8",
                        errors="replace", capture_output=True)
            if daemon:
                self.runner(["docker", "info"], cwd=self.root,
                            check=True, text=True, encoding="utf-8",
                            errors="replace", capture_output=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise DeploymentError(
                "Docker daemon or Compose v2 is unavailable. Start Docker and "
                "verify `docker compose version`.") from exc

    def services(self):
        try:
            text = self.run("ps", "--format", "json").stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return []
        if not text:
            return []
        try:
            value = json.loads(text)
            rows = value if isinstance(value, list) else [value]
        except json.JSONDecodeError:
            rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        return sorted(({
            "service": str(row.get("Service") or row.get("Name") or ""),
            "state": str(row.get("State") or "").lower(),
            "health": str(row.get("Health") or "").lower(),
        } for row in rows), key=lambda value: value["service"])

    def daemon_available(self):
        if not self.which("docker"):
            return False
        try:
            self.runner(["docker", "info"], cwd=self.root, check=True,
                        text=True, encoding="utf-8", errors="replace",
                        capture_output=True)
            return True
        except (OSError, subprocess.CalledProcessError):
            return False


class Provisioner:
    def __init__(self, root, config, runtime_dir, compose, *, env_path=None,
                 token_fn=None):
        self.root = Path(root)
        self.config = config
        self.runtime = Path(runtime_dir)
        self.compose = compose
        self.env_path = Path(env_path or self.root / ".env")
        self.state_path = self.runtime / "provisioning/state.json"
        self.token_fn = token_fn or self._bootstrap_token

    def settings(self):
        try:
            return resolve_settings(load_env_file(self.env_path))
        except SettingsError as exc:
            raise DeploymentError(str(exc)) from exc

    def _state(self):
        try:
            return json.loads(self.state_path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def _token_value(self):
        if os.getenv("INFLUXDB_TOKEN", "").strip():
            return os.getenv("INFLUXDB_TOKEN", "").strip()
        try:
            for line in self.env_path.read_text().splitlines():
                if line.startswith("INFLUXDB_TOKEN="):
                    return line.split("=", 1)[1].strip().strip("'\"")
        except OSError:
            pass
        return ""

    def _bootstrap_token(self):
        port = self.settings().influx_port
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/v3/configure/token/admin",
            data=b"", method="POST",
            headers={"Accept": "application/json",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=5) as response:
            value = json.loads(response.read())
        token = str(value.get("token") or "")
        if len(token) < 20 or any(character.isspace() for character in token):
            raise DeploymentError(
                "InfluxDB did not return a valid bootstrap token")
        return token

    def _preserve_token(self, token):
        lines = self.env_path.read_text().splitlines() \
            if self.env_path.is_file() else []
        replaced = False
        output = []
        for line in lines:
            if line.startswith("INFLUXDB_TOKEN="):
                output.append("INFLUXDB_TOKEN=" + token)
                replaced = True
            else:
                output.append(line)
        if not replaced:
            output.append("INFLUXDB_TOKEN=" + token)
        atomic_write(self.env_path, "\n".join(output) + "\n")
        os.environ["INFLUXDB_TOKEN"] = token

    def provision(self, *, services_running=False):
        directories = (
            "inventory", "operations", "infrastructure", "services", "sites",
            "dashboard/managed", "dashboard/provisioning", "daemon",
            "notifications", "provisioning", "telegraf-generated")
        for value in directories:
            (self.runtime / value).mkdir(parents=True, exist_ok=True)
        dashboard = DashboardRegistry(
            self.root, self.config,
            self.runtime / "dashboard/managed",
            self.runtime / "dashboard/provisioning/dashboards.yml").generate()
        token = self._token_value()
        credential_state = "preserved" if token else "required"
        if services_running and not token:
            try:
                token = self.token_fn()
                self._preserve_token(token)
                credential_state = "created"
            except Exception:
                token = ""
        token_present = bool(token)
        settings = self.settings()
        database = settings.database
        database_state = "deferred"
        if services_running and token_present:
            result = self.compose.run(
                "exec", "-T", "influxdb3-core", "influxdb3", "create",
                "database", "--host", "http://localhost:8181",
                "--token", token, database,
                check=False)
            database_state = (
                "available" if result.returncode == 0
                or "already exists" in (result.stderr or "").lower()
                else "failed")
        missing = []
        if not self.env_path.is_file():
            missing.append("environment configuration")
        if not token_present:
            missing.append("InfluxDB token")
        if database_state == "failed":
            missing.append("InfluxDB database")
        now = _utc()
        completed = not missing and (
            database_state == "available" or not services_running)
        previous = self._state()
        state = {
            "schema_version": 1,
            "provisioning_version": PROVISIONING_VERSION,
            "status": "complete" if completed else "partial",
            "last_attempt": now,
            "last_successful_provisioning": (
                now if completed else previous.get(
                    "last_successful_provisioning")),
            "credentials": credential_state if token_present else "required",
            "database": database_state,
            "configured_database": settings.database,
            "configured_organization": settings.organization,
            "dashboard_count": len(dashboard["dashboards"]),
            "dashboard_packs": dashboard.get("packs", []),
            "missing": sorted(missing),
        }
        atomic_write(
            self.state_path, json.dumps(state, indent=2, sort_keys=True) + "\n")
        return state

    def status(self):
        state = self._state()
        return state or {
            "schema_version": 1,
            "provisioning_version": PROVISIONING_VERSION,
            "status": "not provisioned",
            "last_attempt": None,
            "last_successful_provisioning": None,
            "credentials": "unknown", "database": "unknown",
            "dashboard_count": 0, "dashboard_packs": [], "missing": [],
        }


class StackLifecycle:
    def __init__(self, compose, provisioner, *, http_fn=None, port_fn=None,
                 sleep_fn=time.sleep, bootstrap_timeout=90):
        self.compose = compose
        self.provisioner = provisioner
        self.http = http_fn or self._http
        self.port_available = port_fn or self._port_available
        self.sleep = sleep_fn
        self.bootstrap_timeout = max(1, int(bootstrap_timeout))

    @staticmethod
    def _http(url):
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                return response.status < 400
        except OSError:
            return False

    @staticmethod
    def _port_available(port):
        with socket.socket() as connection:
            connection.settimeout(0.2)
            return connection.connect_ex(("127.0.0.1", int(port))) != 0

    def status(self, *, online=True):
        docker_available = bool(self.compose.which("docker"))
        services = self.compose.services() if docker_available else []
        running = [value for value in services if value["state"] == "running"]
        project = "running" if running else (
            "stopped" if services else "not created")
        influx_port = os.getenv("INFLUXDB_PORT", "8181")
        grafana_port = os.getenv("GRAFANA_PORT", "3000")
        provisioning = self.provisioner.status()
        return {
            "docker_available": docker_available,
            "docker_daemon_available": (
                self.compose.daemon_available() if docker_available else False),
            "compose_project_state": project,
            "services": services,
            "influxdb": (
                "reachable" if online and self.http(
                    f"http://127.0.0.1:{influx_port}/health")
                else "unavailable"),
            "grafana": (
                "reachable" if online and self.http(
                    f"http://127.0.0.1:{grafana_port}/api/health")
                else "unavailable"),
            "provisioning": provisioning,
            "dashboard_packs": provisioning.get("dashboard_packs", []),
        }

    def start(self):
        self.compose.verify()
        settings = self.provisioner.settings()
        before = self.status(online=False)
        self.provisioner.provision(services_running=False)
        if before["compose_project_state"] != "running":
            occupied = [
                value for value in (
                    settings.grafana_port,
                    settings.influx_port)
                if not self.port_available(value)]
            if occupied:
                raise DeploymentError(
                    "Stack cannot start because required host port(s) are in "
                    "use: " + ", ".join(str(value) for value in occupied)
                    + ". Stop the conflicting service or change .env.")
            try:
                self.compose.run(
                    "up", "-d", "--build", "influxdb3-core")
                deadline = time.monotonic() + self.bootstrap_timeout
                health_url = (
                    f"http://127.0.0.1:{settings.influx_port}/health")
                while not self.http(health_url):
                    if time.monotonic() >= deadline:
                        raise DeploymentError(
                            "InfluxDB bootstrap did not become healthy within "
                            f"{self.bootstrap_timeout}s. Inspect "
                            "`./itp logs --service influxdb3-core`.")
                    self.sleep(1)
            except (OSError, subprocess.CalledProcessError) as exc:
                raise DeploymentError(
                    "Docker Compose could not start the InfluxDB bootstrap "
                    "service. Run "
                    "`./itp logs --tail 200` and verify configured ports.") from exc
            changed = True
        else:
            changed = False
        provisioning = self.provisioner.provision(services_running=True)
        if provisioning["status"] != "complete":
            if changed:
                self.compose.run("stop", "influxdb3-core", check=False)
            raise DeploymentError(
                "ITP provisioning is incomplete: "
                + ", ".join(provisioning.get("missing") or ["unknown stage"])
                + ". Configuration was preserved; correct the issue and "
                "rerun `./itp start`.")
        if changed or provisioning["credentials"] == "created":
            try:
                self.compose.run(
                    "up", "-d", "--build", "--remove-orphans")
            except (OSError, subprocess.CalledProcessError) as exc:
                raise DeploymentError(
                    "Bootstrap provisioning succeeded, but remaining ITP "
                    "services could not start. Inspect `./itp logs`.") from exc
        return {"action": "start", "changed": changed,
                "stack": self.status(), "provisioning": provisioning}

    def stop(self):
        self.compose.verify()
        before = self.status(online=False)
        changed = before["compose_project_state"] == "running"
        if changed:
            try:
                self.compose.run("down")
            except (OSError, subprocess.CalledProcessError) as exc:
                raise DeploymentError(
                    "Docker Compose could not stop ITP. Run `docker compose ps`."
                ) from exc
        return {"action": "stop", "changed": changed,
                "stack": self.status(online=False)}

    def restart(self):
        self.compose.verify()
        self.provisioner.settings()
        before = self.status(online=False)
        if before["compose_project_state"] == "running":
            try:
                self.compose.run("restart")
            except (OSError, subprocess.CalledProcessError) as exc:
                raise DeploymentError(
                    "Docker Compose could not restart ITP. Inspect `./itp logs`."
                ) from exc
        else:
            try:
                self.compose.run("up", "-d", "--build", "--remove-orphans")
            except (OSError, subprocess.CalledProcessError) as exc:
                raise DeploymentError(
                    "Docker Compose could not start ITP during restart.") from exc
        provisioning = self.provisioner.provision(services_running=True)
        return {"action": "restart", "changed": True,
                "stack": self.status(), "provisioning": provisioning}

    def logs(self, *, follow=False, service=None, tail=200):
        self.compose.verify()
        arguments = ["logs", "--tail", str(max(0, int(tail)))]
        if follow:
            arguments.append("--follow")
        if service:
            arguments.append(service)
        return self.compose.run(*arguments, capture=False)
