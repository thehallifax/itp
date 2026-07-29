import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from itp_profiles.setup import (
    BootstrapWizard,
    SetupError,
    SetupOptions,
)


ROOT = Path(__file__).resolve().parents[2]


class DockerRunner:
    def __init__(self, *, compose_error=False, running=False, states=None):
        self.commands = []
        self.compose_error = compose_error
        self.running = running
        self.states = states or (
            '[{"Service":"influxdb3-core","State":"running","Health":"healthy"},'
            '{"Service":"telegraf","State":"running"},'
            '{"Service":"discovery","State":"running"},'
            '{"Service":"collector","State":"running","Health":"healthy"},'
            '{"Service":"grafana","State":"running"}]'
        )

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        if command[-2:] == ["config", "--quiet"] and self.compose_error:
            raise subprocess.CalledProcessError(
                1, command, stderr="invalid compose configuration")
        if command[-3:] == ["compose", "ps", "-q"]:
            return SimpleNamespace(stdout="container\n" if self.running else "")
        if command[-4:] == ["compose", "ps", "--format", "json"]:
            return SimpleNamespace(stdout=self.states)
        return SimpleNamespace(stdout="Docker version 27\n", stderr="")


def tree(tmp_path):
    discovery = tmp_path / "discovery"
    discovery.mkdir()
    (tmp_path / ".env.example").write_text((ROOT / ".env.example").read_text())
    (discovery / "config.example.yml").write_text(
        (ROOT / "discovery/config.example.yml").read_text())
    return tmp_path


@pytest.fixture
def docker_available(monkeypatch):
    monkeypatch.setattr("itp_profiles.setup.shutil.which",
                        lambda command: "/usr/bin/docker")
    monkeypatch.setattr(BootstrapWizard, "_port_available",
                        staticmethod(lambda port: True))


def test_first_run_copies_templates_and_generates_selected_values(
        tmp_path, docker_available):
    runner = DockerRunner()
    messages = []
    wizard = BootstrapWizard(
        tree(tmp_path), runner=runner, output_fn=messages.append)
    result = wizard.run(SetupOptions(
        non_interactive=True, deployment_name="North Campus",
        deployment_type="School", grafana_port=3100))

    assert result.first_run is True
    assert result.created == (".env", "discovery/config.yml")
    assert result.started is False
    assert "GRAFANA_PORT=3100" in (tmp_path / ".env").read_text()
    assert "INFLUXDB_BUCKET=local_system" in (tmp_path / ".env").read_text()
    assert "INFLUXDB_ORG=itp" in (tmp_path / ".env").read_text()
    assert uuid.UUID(result.deployment_id)
    config = yaml.safe_load((tmp_path / "discovery/config.yml").read_text())
    assert config["deployment"] == {"name": "North Campus", "type": "School"}
    assert config["customer"] == config["site"] == "north-campus"
    assert config["schema_version"] == 1
    assert ["docker", "compose", "config", "--quiet"] in runner.commands
    assert any("Grafana: http://localhost:3100" in message
               for message in messages)
    assert "Next: ./itp start" in messages


def test_setup_separates_provisioning_and_stack_start(
        tmp_path, docker_available):
    calls = []
    wizard = BootstrapWizard(
        tree(tmp_path), runner=DockerRunner(),
        output_fn=lambda _: None, sleep_fn=lambda _: None,
        provision_fn=lambda: calls.append("provision"),
        start_fn=lambda: calls.append("start"))
    result = wizard.run(SetupOptions(
        non_interactive=True, start=True, health_timeout=2))
    assert result.started is True
    assert calls == ["provision", "start"]


def test_interactive_first_run_prompts_and_can_start(
        tmp_path, docker_available):
    answers = iter((
        "Campus Lab", "Home Lab", "Australia/Perth", "3400", "8281",
        "1", "no", "yes"))
    runner = DockerRunner()
    result = BootstrapWizard(
        tree(tmp_path), runner=runner, input_fn=lambda _: next(answers),
        output_fn=lambda _: None, sleep_fn=lambda _: None).run(SetupOptions())
    assert result.started is True
    assert result.dashboard_url == "http://localhost:3400"
    config = yaml.safe_load((tmp_path / "discovery/config.yml").read_text())
    assert config["deployment"]["name"] == "Campus Lab"


def test_existing_configuration_is_preserved_without_confirmation(
        tmp_path, docker_available):
    root = tree(tmp_path)
    env = root / ".env"
    config = root / "discovery/config.yml"
    env.write_text((root / ".env.example").read_text().replace(
        "GRAFANA_PORT=3000", "GRAFANA_PORT=4444").replace(
        "INFLUXDB_NODE_ID=", "INFLUXDB_NODE_ID=existing-node").replace(
        "ITP_DEPLOYMENT_ID=",
        "ITP_DEPLOYMENT_ID=00000000-0000-4000-8000-000000000001")
        + "CUSTOM=value\n")
    config.write_text("schema_version: 1\ncollectors: {}\ncustom: retained\n")
    wizard = BootstrapWizard(root, runner=DockerRunner(), output_fn=lambda _: None)
    result = wizard.run(SetupOptions(
        non_interactive=True, deployment_name="Ignored",
        deployment_type="Business", grafana_port=3200))
    assert result.first_run is False
    assert result.created == result.updated == ()
    assert result.dashboard_url == "http://localhost:4444"
    assert "GRAFANA_PORT=4444" in env.read_text()
    assert "CUSTOM=value" in env.read_text()
    assert yaml.safe_load(config.read_text())["custom"] == "retained"


def test_force_updates_only_wizard_owned_existing_values(
        tmp_path, docker_available):
    root = tree(tmp_path)
    (root / ".env").write_text((root / ".env.example").read_text().replace(
        "INFLUXDB_NODE_ID=", "INFLUXDB_NODE_ID=existing-node").replace(
        "ITP_DEPLOYMENT_ID=",
        "ITP_DEPLOYMENT_ID=00000000-0000-4000-8000-000000000001").replace(
        "INFLUXDB_BUCKET=local_system", "INFLUXDB_BUCKET=custom_database").replace(
        "INFLUXDB_ORG=itp", "INFLUXDB_ORG=custom_org")
        + "CUSTOM=retained\n")
    (root / "discovery/config.yml").write_text(
        "schema_version: 1\ncollectors: {}\ncustom: retained\n")
    result = BootstrapWizard(
        root, runner=DockerRunner(), output_fn=lambda _: None).run(
            SetupOptions(non_interactive=True, force=True,
                         deployment_name="Acme", deployment_type="Enterprise",
                         grafana_port=3300))
    assert result.updated == (".env", "discovery/config.yml")
    assert "CUSTOM=retained" in (root / ".env").read_text()
    assert "INFLUXDB_BUCKET=custom_database" in (root / ".env").read_text()
    assert "INFLUXDB_ORG=custom_org" in (root / ".env").read_text()
    assert "INFLUXDB_PORT=8181" in (root / ".env").read_text()
    config = yaml.safe_load((root / "discovery/config.yml").read_text())
    assert config["custom"] == "retained"
    assert config["deployment"] == {"name": "Acme", "type": "Enterprise"}
    assert config["customer"] == config["site"] == "acme"


def test_missing_docker_fails_before_writing(tmp_path, monkeypatch):
    root = tree(tmp_path)
    monkeypatch.setattr("itp_profiles.setup.shutil.which", lambda command: None)
    with pytest.raises(SetupError, match="Docker is not installed"):
        BootstrapWizard(root, runner=DockerRunner()).run(
            SetupOptions(non_interactive=True))
    assert not (root / ".env").exists()
    assert not (root / "discovery/config.yml").exists()


def test_missing_compose_v2_fails_before_writing(
        tmp_path, docker_available):
    root = tree(tmp_path)

    def runner(command, **kwargs):
        if command == ["docker", "compose", "version"]:
            raise subprocess.CalledProcessError(1, command)
        return SimpleNamespace(stdout="Docker version 27\n", stderr="")

    with pytest.raises(SetupError, match="Compose v2 is unavailable"):
        BootstrapWizard(root, runner=runner).run(
            SetupOptions(non_interactive=True))
    assert not (root / ".env").exists()


def test_configuration_validation_failure_is_actionable(
        tmp_path, docker_available):
    root = tree(tmp_path)
    with pytest.raises(SetupError, match="Compose configuration validation failed"):
        BootstrapWizard(root, runner=DockerRunner(compose_error=True)).run(
            SetupOptions(non_interactive=True))


def test_invalid_existing_yaml_fails_only_when_update_confirmed(
        tmp_path, docker_available):
    root = tree(tmp_path)
    (root / ".env").write_text("GRAFANA_PORT=3000\n")
    (root / "discovery/config.yml").write_text("invalid: [")
    before = (root / ".env").read_text()
    with pytest.raises(SetupError, match="existing configuration is invalid"):
        BootstrapWizard(root, runner=DockerRunner()).run(
            SetupOptions(non_interactive=True, force=True))
    assert (root / ".env").read_text() == before


def test_non_interactive_start_waits_for_all_services(
        tmp_path, docker_available):
    runner = DockerRunner()
    result = BootstrapWizard(
        tree(tmp_path), runner=runner, output_fn=lambda _: None,
        sleep_fn=lambda _: None).run(
            SetupOptions(non_interactive=True, start=True, health_timeout=2))
    assert result.started is True
    assert ["docker", "compose", "up", "-d", "--build"] in runner.commands
    assert any(command[-4:] == ["compose", "ps", "--format", "json"]
               for command in runner.commands)


def test_occupied_default_port_is_recommended_automatically(
        tmp_path, docker_available, monkeypatch):
    root = tree(tmp_path)
    monkeypatch.setattr(
        BootstrapWizard, "_port_available",
        staticmethod(lambda port: port != 3000))
    result = BootstrapWizard(root, runner=DockerRunner()).run(
        SetupOptions(non_interactive=True))
    assert result.dashboard_url == "http://localhost:3100"


def test_invalid_non_interactive_options_are_rejected(
        tmp_path, docker_available):
    wizard = BootstrapWizard(tree(tmp_path), runner=DockerRunner())
    with pytest.raises(SetupError, match="Deployment type"):
        wizard.run(SetupOptions(
            non_interactive=True, deployment_type="Spaceship"))


def test_force_repairs_blanks_migrates_legacy_and_preserves_identity(
        tmp_path, docker_available):
    root = tree(tmp_path)
    deployment_id = "00000000-0000-4000-8000-000000000099"
    (root / ".env").write_text(
        "INFLUXDB_HTTP_PORT=8281\n"
        "INFLUXDB_BUCKET=\n"
        "INFLUXDB_ORG=\n"
        "GRAFANA_PORT=3300\n"
        "TZ=UTC\n"
        "TELEGRAF_COLLECTION_INTERVAL=60s\n"
        f"ITP_DEPLOYMENT_ID={deployment_id}\n"
        "INFLUXDB_NODE_ID=existing-node\n")
    (root / "discovery/config.yml").write_text(
        "schema_version: 1\ncollectors: {}\n")
    result = BootstrapWizard(
        root, runner=DockerRunner(), output_fn=lambda _: None).run(
            SetupOptions(non_interactive=True, force=True,
                         influxdb_port=8381))
    text = (root / ".env").read_text()
    assert "INFLUXDB_HTTP_PORT" not in text
    assert "INFLUXDB_PORT=8381" in text
    assert "INFLUXDB_BUCKET=local_system" in text
    assert "INFLUXDB_ORG=itp" in text
    assert result.deployment_id == deployment_id


def test_explicit_occupied_port_is_rejected_without_writing(
        tmp_path, docker_available, monkeypatch):
    root = tree(tmp_path)
    monkeypatch.setattr(
        BootstrapWizard, "_port_available",
        staticmethod(lambda port: port != 3456))
    with pytest.raises(SetupError, match="already in use.*3456"):
        BootstrapWizard(root, runner=DockerRunner()).run(
            SetupOptions(non_interactive=True, grafana_port=3456))
    assert not (root / ".env").exists()


def test_enabled_connector_requires_local_credentials_not_shell_environment(
        tmp_path, docker_available, monkeypatch):
    root = tree(tmp_path)
    wizard = BootstrapWizard(
        root, runner=DockerRunner(), output_fn=lambda _: None)
    wizard.run(SetupOptions(non_interactive=True))
    config_path = root / "discovery/config.yml"
    config = yaml.safe_load(config_path.read_text())
    config["collectors"]["mist"]["enabled"] = True
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    monkeypatch.setenv("MIST_ORG_ID", "must-not-be-inherited")
    monkeypatch.setenv("MIST_API_TOKEN", "must-not-be-inherited")
    with pytest.raises(SetupError, match="Enabled collector mist.*credentials"):
        wizard.validate_configuration()
