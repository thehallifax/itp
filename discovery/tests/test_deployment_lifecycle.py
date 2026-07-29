import json
import os
import subprocess
from pathlib import Path

import pytest

from analysis.deployment import (
    DeploymentError, DockerCompose, Provisioner, StackLifecycle)


ROOT = Path(__file__).resolve().parents[2]


class Runner:
    def __init__(self, services=()):
        self.services = list(services)
        self.commands = []
        self.fail = None

    def __call__(self, command, **_kwargs):
        self.commands.append(command)
        if self.fail and self.fail(command):
            raise subprocess.CalledProcessError(1, command)
        output = ""
        if command[:4] == ["docker", "compose", "ps", "--format"]:
            output = json.dumps([
                {"Service": value, "State": "running", "Health": "healthy"}
                for value in self.services])
        return subprocess.CompletedProcess(command, 0, output, "")


def deployment(tmp_path, runner, monkeypatch, *, token=True):
    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ITP_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("INFLUXDB_BUCKET", "local_system")
    if token:
        monkeypatch.setenv("INFLUXDB_TOKEN", "opaque-secret-token")
    else:
        monkeypatch.delenv("INFLUXDB_TOKEN", raising=False)
    env = tmp_path / ".env"
    env.write_text("\n".join((
        "INFLUXDB_BUCKET=local_system",
        "INFLUXDB_ORG=itp",
        "INFLUXDB_PORT=8181",
        "GRAFANA_PORT=3000",
        "TZ=UTC",
        "TELEGRAF_COLLECTION_INTERVAL=60s",
        "ITP_DEPLOYMENT_ID=00000000-0000-4000-8000-000000000001",
        "INFLUXDB_NODE_ID=itp-test",
        "INFLUXDB_TOKEN=opaque-secret-token" if token else "INFLUXDB_TOKEN=",
        "",
    )))
    compose = DockerCompose(
        ROOT, runner=runner, which_fn=lambda name: f"/usr/bin/{name}",
        environment=lambda: {})
    config = {"schema_version": 1, "collectors": {}}
    provisioner = Provisioner(
        ROOT, config, tmp_path / "runtime", compose, env_path=env)
    return compose, provisioner, StackLifecycle(
        compose, provisioner, http_fn=lambda _url: True,
        port_fn=lambda _port: True, sleep_fn=lambda _: None)


def test_start_stopped_and_start_already_running(tmp_path, monkeypatch):
    runner = Runner()
    _, _, lifecycle = deployment(tmp_path, runner, monkeypatch)
    result = lifecycle.start()
    assert result["changed"] is True
    assert any(command[2:4] == ["up", "-d"] for command in runner.commands)

    runner.services = ["grafana", "influxdb3-core"]
    runner.commands.clear()
    result = lifecycle.start()
    assert result["changed"] is False
    assert not any("up" in command for command in runner.commands)


def test_stop_running_and_already_stopped(tmp_path, monkeypatch):
    runner = Runner(["grafana"])
    _, _, lifecycle = deployment(tmp_path, runner, monkeypatch)
    assert lifecycle.stop()["changed"] is True
    assert any(len(command) > 2 and command[2] == "down"
               for command in runner.commands)
    runner.services = []
    runner.commands.clear()
    assert lifecycle.stop()["changed"] is False
    assert not any("down" in command for command in runner.commands)


def test_restart_and_logs_command_construction(tmp_path, monkeypatch):
    runner = Runner(["grafana"])
    _, _, lifecycle = deployment(tmp_path, runner, monkeypatch)
    assert lifecycle.restart()["action"] == "restart"
    assert any(len(command) > 2 and command[2] == "restart"
               for command in runner.commands)
    lifecycle.logs(follow=True, service="grafana", tail=42)
    assert runner.commands[-1] == [
        "docker", "compose", "logs", "--tail", "42", "--follow", "grafana"]


def test_docker_unavailable_daemon_unavailable_and_compose_failure(
        tmp_path, monkeypatch):
    runner = Runner()
    compose = DockerCompose(
        ROOT, runner=runner, which_fn=lambda _name: None)
    with pytest.raises(DeploymentError, match="Docker is unavailable"):
        compose.verify()
    compose = DockerCompose(
        ROOT, runner=runner, which_fn=lambda _name: "/docker")
    runner.fail = lambda command: command == ["docker", "info"]
    with pytest.raises(DeploymentError, match="daemon or Compose"):
        compose.verify()
    runner.fail = lambda command: command[2:3] == ["up"]
    _, _, lifecycle = deployment(tmp_path, runner, monkeypatch)
    with pytest.raises(DeploymentError, match="could not start"):
        lifecycle.start()


def test_provisioning_first_repeat_credentials_and_partial_recovery(
        tmp_path, monkeypatch):
    runner = Runner()
    _, provisioner, _ = deployment(tmp_path, runner, monkeypatch)
    original = provisioner.env_path.read_text()
    first = provisioner.provision()
    second = provisioner.provision()
    assert first["status"] == second["status"] == "complete"
    assert provisioner.env_path.read_text() == original
    assert (tmp_path / "runtime/daemon").is_dir()
    assert (tmp_path / "runtime/notifications").is_dir()
    assert (tmp_path / "runtime/dashboard/managed/registry.json").is_file()
    assert "opaque-secret-token" not in json.dumps(second)

    partial_root = tmp_path / "partial"
    _, partial, _ = deployment(
        partial_root, Runner(), monkeypatch, token=False)
    assert partial.provision()["status"] == "partial"
    partial.env_path.write_text(
        partial.env_path.read_text().replace(
            "INFLUXDB_TOKEN=", "INFLUXDB_TOKEN=preserved-token"))
    recovered = partial.provision()
    assert recovered["status"] == "complete"
    assert recovered["last_successful_provisioning"]


def test_status_reports_services_endpoints_and_provisioning(
        tmp_path, monkeypatch):
    runner = Runner(["grafana", "influxdb3-core"])
    _, provisioner, lifecycle = deployment(tmp_path, runner, monkeypatch)
    provisioner.provision()
    result = lifecycle.status()
    assert result["docker_available"] is True
    assert result["compose_project_state"] == "running"
    assert result["influxdb"] == result["grafana"] == "reachable"
    assert result["provisioning"]["provisioning_version"] == 1
    assert {value["id"] for value in result["dashboard_packs"]} == {"platform"}


def test_first_service_provisioning_bootstraps_token_once(
        tmp_path, monkeypatch):
    runner = Runner(["influxdb3-core"])
    compose, provisioner, _ = deployment(
        tmp_path, runner, monkeypatch, token=False)
    calls = []
    provisioner.token_fn = lambda: (
        calls.append("token") or "new-opaque-bootstrap-token")
    result = provisioner.provision(services_running=True)
    assert result["credentials"] == "created"
    assert calls == ["token"]
    assert "new-opaque-bootstrap-token" in provisioner.env_path.read_text()
    provisioner.provision(services_running=True)
    assert calls == ["token"]
    assert "new-opaque-bootstrap-token" not in json.dumps(result)
