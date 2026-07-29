import os
import subprocess
from dataclasses import dataclass

import pytest
import yaml

from analysis.runtime_deployment import (
    RuntimeDeploymentError,
    RuntimeDeploymentManager,
    normalize_onboarding_value,
    slugify,
)
from collectors.connector_registry import ConnectorMetadataRegistry


@dataclass
class Connector:
    id: str
    display_name: str
    credential_fields: tuple = ()
    configuration_fields: tuple = ()

    def to_dict(self):
        return {"id": self.id, "display_name": self.display_name}


class Registry:
    def __init__(self):
        self.values = (
            Connector("snmp", "SNMP", ({
                "id": "community", "env": "NETWORK_SNMP_COMMUNITY",
                "required": True, "secret": True},)),
            Connector("mist", "Mist"),
        )

    def all(self):
        return self.values

    def get(self, name):
        return next(value for value in self.values if value.id == name)


def manager(tmp_path, **kwargs):
    return RuntimeDeploymentManager(
        tmp_path, registry=Registry(), output_fn=lambda _value: None,
        port_fn=lambda _address, _port: True,
        **kwargs)


def test_slug_and_non_interactive_first_deployment(tmp_path):
    deployment = manager(tmp_path).create(
        name="Example School", non_interactive=True,
        collectors=["snmp"])
    assert deployment.deployment_id == "example-school"
    assert deployment.manifest.is_file()
    assert deployment.collectors.is_file()
    assert deployment.dashboards.is_file()
    assert deployment.secrets_dir.is_dir()
    assert deployment.generated.is_dir()
    assert (deployment.path / "logs").is_dir()
    assert (deployment.path / "evidence").is_dir()
    assert (deployment.path / "state").is_dir()
    assert yaml.safe_load(deployment.collectors.read_text())[
        "collectors"]["snmp"]["enabled"] is True
    assert oct(deployment.env_file.stat().st_mode & 0o777) == "0o600"
    assert slugify(" Example Corporate ") == "example-corporate"
    environment = dict(
        line.split("=", 1)
        for line in deployment.env_file.read_text().splitlines())
    assert environment["ITP_SITES_CONFIG"] == str(
        deployment.generated / "sites.yml")
    assert environment["INFLUXDB_HOST"] == "influxdb3-core"
    assert environment["INFLUXDB_TOKEN"] == ""
    assert yaml.safe_load(
        (deployment.generated / "sites.yml").read_text())["sites"][0]["id"] == (
            "site:example-school")
    config = yaml.safe_load(deployment.collectors.read_text())
    assert config["customer"] == config["customer_id"] == "example-school"
    assert config["collectors"]["snmp"]["enabled"] is True
    assert config["networks"] == [{
        "cidr": "192.0.2.0/32", "purpose": "disabled"}]


def test_rerun_preserves_configuration_and_secrets(tmp_path):
    runtime = manager(tmp_path)
    deployment = runtime.create(
        name="Example School", non_interactive=True)
    original = deployment.env_file.read_text()
    deployment.env_file.write_text(original + "PRESERVED=value\n")
    repeated = runtime.create(
        name="Example School", non_interactive=True)
    assert repeated.path == deployment.path
    assert repeated.env_file.read_text().endswith("PRESERVED=value\n")


def test_forced_regeneration_preserves_generated_credentials(tmp_path):
    runtime = manager(tmp_path)
    deployment = runtime.create(
        name="Example School", non_interactive=True)
    original = deployment.env_file.read_text().replace(
        "INFLUXDB_TOKEN=\n", "INFLUXDB_TOKEN=fictional-token\n")
    deployment.env_file.write_text(original)
    repeated = runtime.create(
        name="Example School", non_interactive=True, force=True)
    environment = dict(
        line.split("=", 1)
        for line in repeated.env_file.read_text().splitlines())
    assert environment["INFLUXDB_TOKEN"] == "fictional-token"


def test_force_accepts_ports_owned_by_same_deployment(tmp_path):
    runtime = manager(tmp_path)
    runtime.create(name="Example", non_interactive=True)
    runtime.port_available = lambda _address, _port: False
    repeated = runtime.create(
        name="Example", non_interactive=True, force=True)
    assert repeated.load()["network"]["grafana_port"] == 3000


def test_collector_add_and_remove_write_only_runtime(tmp_path):
    answers = iter(["private-community"])
    runtime = manager(
        tmp_path, input_fn=lambda _prompt: "",
        secret_input=lambda _prompt: next(answers))
    deployment = runtime.create(
        name="Example School", non_interactive=True)
    runtime.add_collector(deployment, "snmp")
    secret = deployment.secrets_dir / "snmp.env"
    assert secret.read_text() == "NETWORK_SNMP_COMMUNITY=private-community\n"
    assert oct(secret.stat().st_mode & 0o777) == "0o600"
    runtime.remove_collector(deployment, "snmp")
    assert yaml.safe_load(deployment.collectors.read_text())[
        "collectors"]["snmp"]["enabled"] is False
    assert not (tmp_path / ".env").exists()


def test_generated_compose_command_handles_paths_with_spaces(tmp_path):
    root = tmp_path / "Repository With Spaces"
    deployment = manager(root).create(
        name="Example Site", non_interactive=True)
    command = deployment.compose_command("config", "--quiet")
    assert str(deployment.env_file) in command
    assert str(deployment.compose_override) in command
    assert command[0:2] == ["docker", "compose"]


def test_quiet_compose_capture_is_bounded_and_preserves_failure_code(
        tmp_path, monkeypatch):
    deployment = manager(tmp_path).create(
        name="Example Site", non_interactive=True)

    def failed(_command, **kwargs):
        kwargs["stdout"].write("x" * 70000 + "\nactionable failure\n")
        return subprocess.CompletedProcess([], 17)

    monkeypatch.setattr(subprocess, "run", failed)
    with pytest.raises(subprocess.CalledProcessError) as failure:
        deployment.run_compose("build", capture=True)
    assert failure.value.returncode == 17
    assert len(failure.value.output) <= 65536
    assert failure.value.output.endswith("actionable failure\n")


def test_active_deployment_and_listing_are_deterministic(tmp_path):
    runtime = manager(tmp_path)
    runtime.create(name="Zulu Site", non_interactive=True)
    runtime.create(name="Alpha Site", non_interactive=True)
    assert [item["id"] for item in runtime.list()] == [
        "alpha-site", "zulu-site"]
    assert runtime.active_id() == "alpha-site"
    assert os.path.samefile(
        runtime.select("zulu-site").path,
        tmp_path / "runtime/deployments/zulu-site")


def test_interactive_deployment_id_and_listen_guidance(tmp_path):
    answers = iter(["Friendly Site", "friendly-id", "UTC", "", "3100", "8282", ""])
    output = []
    runtime = RuntimeDeploymentManager(
        tmp_path, registry=Registry(), input_fn=lambda _prompt: next(answers),
        output_fn=output.append, port_fn=lambda _address, _port: True)
    deployment = runtime.create()
    assert deployment.deployment_id == "friendly-id"
    assert any("127.0.0.1 = available only" in line for line in output)
    assert deployment.load()["network"]["listen_address"] == "127.0.0.1"


@pytest.mark.parametrize(("normalizer", "value", "expected"), [
    ("https-host", "firewall.example.invalid:8443",
     "https://firewall.example.invalid:8443"),
    ("https-host", "https://firewall.example.invalid/",
     "https://firewall.example.invalid"),
    ("https-origin", "https://api.ac2.mist.com/",
     "https://api.ac2.mist.com"),
    ("papercut-health-origin", "print.example.invalid:9192/api/health/",
     "https://print.example.invalid:9192"),
    ("papercut-health-origin", "https://print.example.invalid/",
     "https://print.example.invalid"),
])
def test_connector_endpoint_normalization(normalizer, value, expected):
    assert normalize_onboarding_value(value, normalizer) == expected


@pytest.mark.parametrize(("normalizer", "value"), [
    ("https-host", "http://firewall.example.invalid"),
    ("https-host", "https://firewall.example.invalid/api/v2"),
    ("https-origin", "api.mist.com"),
    ("https-origin", "https://api.mist.com/api/v1"),
    ("papercut-health-origin", "https://print.example.invalid/other"),
])
def test_connector_endpoint_rejections(normalizer, value):
    with pytest.raises(RuntimeDeploymentError):
        normalize_onboarding_value(value, normalizer)


def test_fortigate_canonical_host_is_prompted_once_and_propagated(tmp_path):
    registry = ConnectorMetadataRegistry.load()
    prompts = []
    secrets = []
    answers = iter(["firewall.example.invalid:8443", "", ""])

    def entered(prompt):
        prompts.append(prompt)
        return next(answers)

    runtime = RuntimeDeploymentManager(
        tmp_path, registry=registry, input_fn=entered,
        secret_input=lambda prompt: secrets.append(prompt) or "token-value",
        output_fn=lambda _value: None,
        port_fn=lambda _address, _port: True)
    deployment = runtime.create(
        name="Example", non_interactive=True)
    runtime.add_collector(deployment, "fortigate")
    config = yaml.safe_load(deployment.collectors.read_text())
    assert config["collectors"]["fortigate"]["host"] == (
        "https://firewall.example.invalid:8443")
    env = (deployment.secrets_dir / "fortigate.env").read_text()
    assert "FORTIGATE_HOST=https://firewall.example.invalid:8443" in env
    assert "FORTIGATE_API_TOKEN=token-value" in env
    assert sum("FortiGate host" in prompt for prompt in prompts) == 1
    assert len(secrets) == 1


def test_credentials_and_readiness_are_runtime_derived(tmp_path):
    runtime = manager(tmp_path)
    deployment = runtime.create(
        name="Example", non_interactive=True, collectors=["snmp"])
    credentials = runtime.grafana_credentials(deployment)
    assert credentials["username"] == "admin"
    assert credentials["password"]
    states = {item["id"]: item for item in runtime.collector_readiness(deployment)}
    assert states["snmp"]["state"] == "pending credentials"
    assert states["mist"]["state"] == "disabled"
    (deployment.secrets_dir / "snmp.env").write_text(
        "NETWORK_SNMP_COMMUNITY=fictional\n")
    states = {item["id"]: item for item in runtime.collector_readiness(deployment)}
    assert states["snmp"]["state"] == "configured"


def test_credentials_fail_clearly_when_generated_environment_is_missing(tmp_path):
    runtime = manager(tmp_path)
    deployment = runtime.create(name="Example", non_interactive=True)
    deployment.env_file.unlink()
    with pytest.raises(RuntimeDeploymentError, match="credentials are unavailable"):
        runtime.grafana_credentials(deployment)


def test_credentials_fail_clearly_when_no_deployment_exists(tmp_path):
    with pytest.raises(RuntimeDeploymentError, match="no deployment selected"):
        manager(tmp_path).select()


def test_credentials_cli_uses_active_and_explicit_deployment(
        tmp_path, monkeypatch, capsys):
    import scripts.itp as cli

    registry = ConnectorMetadataRegistry.load()
    runtime = RuntimeDeploymentManager(
        tmp_path, registry=registry, output_fn=lambda _value: None,
        port_fn=lambda _address, _port: True)
    first = runtime.create(
        name="First", deployment_id="first", non_interactive=True)
    second = runtime.create(
        name="Second", deployment_id="second", non_interactive=True)
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(
        cli.ConnectorMetadataRegistry, "load",
        classmethod(lambda cls, *_args, **_kwargs: registry))

    monkeypatch.setattr(
        "sys.argv", ["itp", "credentials", "grafana"])
    cli.main()
    assert f"Deployment: {second.deployment_id}" in capsys.readouterr().out

    monkeypatch.setattr(
        "sys.argv",
        ["itp", "credentials", "--deployment", first.deployment_id, "grafana"])
    cli.main()
    assert f"Deployment: {first.deployment_id}" in capsys.readouterr().out


def test_deploy_verbose_and_doctor_prompt_modes_are_deterministic():
    from scripts.itp import deployment_doctor_requested, deployment_verbose

    assert not deployment_verbose(False, {})
    assert deployment_verbose(True, {})
    assert deployment_verbose(False, {"ITP_VERBOSE": "1"})
    assert not deployment_doctor_requested(
        non_interactive=True,
        input_fn=lambda _prompt: pytest.fail("must not prompt"))
    assert deployment_doctor_requested(
        non_interactive=True, explicit=True,
        input_fn=lambda _prompt: pytest.fail("must not prompt"))
    assert deployment_doctor_requested(
        non_interactive=False, input_fn=lambda _prompt: "")
    assert not deployment_doctor_requested(
        non_interactive=False, input_fn=lambda _prompt: "n")
