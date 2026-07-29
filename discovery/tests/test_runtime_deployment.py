import os
from dataclasses import dataclass

import yaml

from analysis.runtime_deployment import RuntimeDeploymentManager, slugify


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
            "example-school")
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
