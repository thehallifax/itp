import json
import os
import subprocess
import sys
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
import certifi

from analysis.runtime_deployment import (
    RuntimeDeployment,
    RuntimeDeploymentError,
    RuntimeDeploymentManager,
    normalize_onboarding_value,
    retry_command,
    slugify,
)
from collectors.connector_registry import ConnectorMetadataRegistry
from scripts.itp import runtime_stack_action

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_start_and_restart_rebuild_current_source_image():
    calls = []
    deployment = SimpleNamespace(
        run_compose=lambda *arguments: calls.append(arguments))

    runtime_stack_action(deployment, "start")
    assert calls == [("up", "-d", "--build", "--remove-orphans")]

    calls.clear()
    runtime_stack_action(deployment, "restart")
    assert calls == [
        ("down",),
        ("up", "-d", "--build", "--remove-orphans")]


def test_collector_writes_managed_dashboards_to_grafana_provisioned_tree():
    compose = (ROOT / "docker-compose.yml").read_text()
    assert (
        "DASHBOARD_MANAGED_OUTPUT=/app/runtime/"
        "${ITP_PROFILE:-legacy}/generated/dashboard/managed"
    ) in compose
    assert (
        "DASHBOARD_PROVISIONING=/app/runtime/"
        "${ITP_PROFILE:-legacy}/generated/dashboard/provisioning/"
        "dashboards.yml"
    ) in compose


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


class HttpResponse:
    def __init__(self, body=b"", *, status=200, content_type="text/plain"):
        self.status = status
        self.body = body
        self.headers = {"Content-Type": content_type}

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def install_compose_mock(monkeypatch, calls):
    def run_compose(_deployment, *arguments, **_kwargs):
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(RuntimeDeployment, "run_compose", run_compose)


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


def test_grafana_password_generation_is_secure_persisted_and_one_time(tmp_path):
    runtime = manager(tmp_path)
    deployment = runtime.create(
        name="Example School", non_interactive=True)
    environment = runtime._read_env(deployment.env_file)
    password = environment["GRAFANA_ADMIN_PASSWORD"]
    assert len(password) >= 32
    assert runtime.take_new_grafana_password(deployment) == password
    assert runtime.take_new_grafana_password(deployment) is None
    repeated = runtime.create(
        name="Example School", non_interactive=True, force=True)
    assert runtime._read_env(repeated.env_file)[
        "GRAFANA_ADMIN_PASSWORD"] == password
    assert runtime.take_new_grafana_password(repeated) is None


def test_custom_grafana_password_uses_masked_confirmation(tmp_path):
    secrets = iter(["correct horse battery", "correct horse battery"])
    runtime = manager(
        tmp_path, input_fn=lambda _prompt: "2",
        secret_input=lambda _prompt: next(secrets))
    password = runtime._grafana_password("example", "", False)
    assert password == "correct horse battery"
    assert runtime._new_grafana_passwords["example"] == password


def test_default_interactive_grafana_password_is_generated(tmp_path):
    runtime = manager(tmp_path, input_fn=lambda _prompt: "")
    password = runtime._grafana_password("example", "", False)
    assert len(password) >= 32
    assert runtime._new_grafana_passwords["example"] == password


@pytest.mark.parametrize(("values", "message"), [
    (("", ""), "cannot be blank"),
    (("short", "short"), "at least 12"),
    (("long-enough-password", "different-password"), "does not match"),
])
def test_custom_grafana_password_rejects_invalid_values(
        tmp_path, values, message):
    secrets = iter(values)
    runtime = manager(
        tmp_path, input_fn=lambda _prompt: "2",
        secret_input=lambda _prompt: next(secrets))
    with pytest.raises(RuntimeDeploymentError, match=message):
        runtime._grafana_password("example", "", False)
    assert runtime._new_grafana_passwords == {}


def test_invalid_password_validation_has_no_persistent_side_effects(tmp_path):
    secrets = iter(["short", "short"])
    runtime = manager(
        tmp_path, input_fn=lambda prompt: "2" if prompt.startswith(
            "Selection") else "",
        secret_input=lambda _prompt: next(secrets))
    with pytest.raises(RuntimeDeploymentError, match="at least 12"):
        runtime.create(
            name="Invalid Password", deployment_id="invalid-password",
            timezone="UTC", non_interactive=False,
            collectors=[], confirm=False)
    assert not (tmp_path / "runtime/deployments/invalid-password").exists()


def test_canonical_site_defaults_and_custom_site_are_created_idempotently(tmp_path):
    runtime = manager(tmp_path)
    default = runtime.create(name="Default Site", non_interactive=True)
    assert runtime._site_id(default) == "site:default-site"
    custom = runtime.create(
        name="Campus Deployment", deployment_id="campus",
        site_id="north-campus", site_name="North Campus",
        non_interactive=True)
    sites = yaml.safe_load((custom.generated / "sites.yml").read_text())
    assert sites["sites"] == [{
        "id": "site:north-campus", "display_name": "North Campus",
        "aliases": ["campus", "north-campus"], "enabled": True}]
    repeated = runtime.create(
        name="Campus Deployment", deployment_id="campus",
        site_id="north-campus", non_interactive=True)
    assert repeated.path == custom.path
    assert len(yaml.safe_load((custom.generated / "sites.yml").read_text())[
        "sites"]) == 1


def test_deployment_inventory_and_cleanup_use_exact_compose_labels(tmp_path):
    outputs = {
        ("docker", "ps", "-a", "--filter", "label=com.docker.compose.project",
         "--format", "{{.Label \"com.docker.compose.project\"}}"): "itp-orphan\n",
    }
    def runner(command, **_kwargs):
        key = tuple(command)
        output = outputs.get(key, "")
        if any(value == "label=com.docker.compose.project=itp-orphan"
               for value in command):
            if command[1:3] == ["ps", "-a"]:
                output = '{"Names":"itp-orphan-collector-1","State":"exited"}\n'
            elif command[1:3] == ["network", "ls"]:
                output = '{"Name":"itp-orphan_default"}\n'
            elif command[1:3] == ["volume", "ls"]:
                output = '{"Name":"itp-orphan_influxdb_data"}\n'
        return subprocess.CompletedProcess(command, 0, output, "")
    runtime = manager(tmp_path, runner=runner)
    inventory = runtime.deployment_inventory()
    assert inventory[0]["deployment_id"] == "orphan"
    assert inventory[0]["status"] == "orphaned"
    audit = runtime.cleanup()
    assert audit["dry_run"] is True
    assert audit["candidates"][0]["volumes"] == [
        "itp-orphan_influxdb_data"]
    assert audit["removed"] == []


def test_reset_preserves_telemetry_by_default(tmp_path, monkeypatch):
    runtime = manager(tmp_path)
    deployment = runtime.create(name="Reset Site", non_interactive=True)
    runtime.runner = lambda command, **_kwargs: subprocess.CompletedProcess(
        command, 0, "", "")
    calls = []
    install_compose_mock(monkeypatch, calls)
    result = runtime.reset(deployment.deployment_id, yes=True)
    assert result["telemetry_preserved"] is True
    assert calls == [("down", "--remove-orphans")]
    assert deployment.env_file.is_file()


def test_remove_requires_confirmation_and_preserves_volume_by_default(
        tmp_path, monkeypatch):
    runtime = manager(tmp_path, input_fn=lambda _prompt: "no")
    deployment = runtime.create(name="Remove Site", non_interactive=True)
    with pytest.raises(RuntimeDeploymentError, match="not confirmed"):
        runtime.remove(deployment.deployment_id)
    assert deployment.path.exists()
    calls = []
    runtime.runner = lambda command, **_kwargs: subprocess.CompletedProcess(
        command, 0, "", "")
    install_compose_mock(monkeypatch, calls)
    result = runtime.remove(deployment.deployment_id, yes=True)
    assert result["telemetry_preserved"] is True
    assert calls == [("down", "--remove-orphans")]
    assert not deployment.path.exists()


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


def test_deployment_selection_is_explicit_when_multiple_are_ambiguous(tmp_path):
    runtime = manager(tmp_path)
    runtime.create(
        name="Alpha Site", deployment_id="alpha", non_interactive=True)
    runtime.create(
        name="Beta Site", deployment_id="beta", non_interactive=True)
    (runtime.shared / "active-deployment").unlink()

    with pytest.raises(RuntimeDeploymentError) as failure:
        runtime.select()
    assert "multiple deployments exist" in str(failure.value)
    assert "--deployment alpha" in str(failure.value)
    assert "deployment select alpha" in str(failure.value)

    selected = runtime.activate("beta")
    assert selected.deployment_id == "beta"
    assert runtime.active_id() == "beta"


def test_exactly_one_deployment_is_inferred_without_active_marker(tmp_path):
    runtime = manager(tmp_path)
    deployment = runtime.create(
        name="Only Site", deployment_id="only", non_interactive=True)
    (runtime.shared / "active-deployment").unlink()
    assert runtime.select().path == deployment.path


def test_unknown_deployment_fails_clearly(tmp_path):
    with pytest.raises(
            RuntimeDeploymentError,
            match="deployment does not exist: missing"):
        manager(tmp_path).select("missing")


def test_interactive_deployment_id_and_listen_guidance(tmp_path):
    answers = iter([
        "Friendly Site", "friendly-id", "UTC", "", "3100", "8282",
        "", "", "", "", ""])
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
    assert states["snmp"]["state"] == "execution mode mismatch"
    assert states["snmp"]["execution_mode"] == "edge"
    assert states["snmp"]["runtime_mode"] == "central"


def test_runtime_readiness_reports_incomplete_connectors_without_values(
        tmp_path):
    registry = ConnectorMetadataRegistry.load()
    runtime = RuntimeDeploymentManager(
        tmp_path, registry=registry, output_fn=lambda _value: None,
        port_fn=lambda _address, _port: True)
    deployment = runtime.create(
        name="Example", non_interactive=True)
    config = yaml.safe_load(deployment.collectors.read_text())
    config["collectors"]["paloalto"].update({
        "enabled": True,
        "base_url": "https://firewall.example.invalid",
        "site": "site:example",
    })
    config["collectors"]["papercut"]["enabled"] = True
    deployment.collectors.write_text(yaml.safe_dump(config, sort_keys=False))

    states = {
        item["id"]: item
        for item in runtime.collector_readiness(deployment)}

    assert states["paloalto"]["state"] == "pending credentials"
    assert states["paloalto"]["missing"] == ["PALOALTO_API_KEY"]
    assert states["papercut"]["state"] == "pending configuration"
    assert set(states["papercut"]["missing"]) == {"base_url", "site"}
    rendered = json.dumps(states)
    assert "password" not in rendered.casefold()
    assert "authorization" not in rendered.casefold()


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


def test_credentials_cli_json_is_machine_readable(tmp_path, monkeypatch, capsys):
    import json
    import scripts.itp as cli

    registry = ConnectorMetadataRegistry.load()
    runtime = RuntimeDeploymentManager(
        tmp_path, registry=registry, output_fn=lambda _value: None,
        port_fn=lambda _address, _port: True)
    deployment = runtime.create(
        name="Example", deployment_id="example", non_interactive=True)
    expected = runtime.grafana_credentials(deployment)
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(
        cli.ConnectorMetadataRegistry, "load",
        classmethod(lambda cls, *_args, **_kwargs: registry))
    monkeypatch.setattr(
        "sys.argv",
        ["itp", "credentials", "grafana", "--deployment", "example", "--json"])

    cli.main()

    result = json.loads(capsys.readouterr().out)
    assert result == {
        key: expected[key] for key in (
            "deployment_id", "url", "username", "password")
    }
    assert "source" not in result


def test_grafana_onboarding_displays_new_password_once(tmp_path):
    from scripts.itp import grafana_onboarding_summary

    runtime = manager(tmp_path)
    deployment = runtime.create(
        name="Example", deployment_id="example", non_interactive=True)
    password = runtime._read_env(deployment.env_file)[
        "GRAFANA_ADMIN_PASSWORD"]

    interactive = "\n".join(grafana_onboarding_summary(
        runtime, deployment, "http://127.0.0.1:3000"))
    repeated = "\n".join(grafana_onboarding_summary(
        runtime, deployment, "http://127.0.0.1:3000"))

    assert interactive.count(password) == 1
    assert "Store this password securely." in interactive
    assert "credentials grafana --deployment example" in interactive
    assert password not in repeated
    assert "Password: securely stored" in repeated


def test_non_interactive_onboarding_never_displays_generated_password(tmp_path):
    from scripts.itp import grafana_onboarding_summary

    runtime = manager(tmp_path)
    deployment = runtime.create(
        name="Example", deployment_id="example", non_interactive=True)
    password = runtime._read_env(deployment.env_file)[
        "GRAFANA_ADMIN_PASSWORD"]

    output = "\n".join(grafana_onboarding_summary(
        runtime, deployment, "http://127.0.0.1:3000",
        non_interactive=True))

    assert password not in output
    assert "Password: securely stored" in output


def test_runtime_collection_skips_only_incomplete_connector(
        tmp_path, monkeypatch):
    import scripts.itp as cli

    deployment_id = "m" + "lc"
    registry = SimpleNamespace(all=lambda: (
        SimpleNamespace(
            id="paloalto", display_name="Palo Alto",
            domains=("firewall",)),
        SimpleNamespace(
            id="papercut", display_name="PaperCut",
            domains=("printing",)),
    ))

    runtime = SimpleNamespace(
        registry=registry,
        collector_readiness=lambda _deployment: (
            {"id": "paloalto", "display_name": "Palo Alto",
             "state": "pending credentials",
             "missing": ["PALOALTO_API_KEY"]},
            {"id": "papercut", "display_name": "PaperCut",
             "state": "configured", "missing": []},
        ))

    calls = []

    deployment = SimpleNamespace(
        deployment_id=deployment_id,
        path=tmp_path / "runtime/deployments" / deployment_id)

    def run_compose(*arguments, **_kwargs):
        calls.append(arguments)
        return subprocess.CompletedProcess(
            arguments, 0,
            'INFO collector=papercut phase=collect\n'
            '{"points_written": 17, "authorization_key": "must-not-leak"}',
            "")

    deployment.run_compose = run_compose

    config = {
        "deployment": {"name": deployment_id},
        "site": "site:reference",
        "collectors": {
            "paloalto": {"enabled": True},
            "papercut": {"enabled": True},
        },
    }
    monkeypatch.setattr(cli, "ROOT", tmp_path)

    result = cli.runtime_collection(runtime, deployment, config)

    by_id = {value["connector"]: value for value in result["connectors"]}
    assert by_id["paloalto"]["status"] == "skipped"
    assert by_id["paloalto"]["reason"] == (
        "pending credentials: PALOALTO_API_KEY")
    assert by_id["papercut"]["status"] == "success"
    assert by_id["papercut"]["summary"] == {"points_written": 17}
    assert result["summary"]["overall"] == "partial"
    assert len(calls) == 1
    assert calls[0][-3:] == ("collect", "papercut", "--json")
    assert "must-not-leak" not in str(result)


def test_runtime_cli_help_accepts_canonical_deployment_placement():
    deployment_id = "m" + "lc"
    commands = (
        ("doctor", "--deployment", deployment_id, "--help"),
        ("collect", "--deployment", deployment_id, "--help"),
        ("status", "--deployment", deployment_id, "--help"),
        ("collector", "run", "paloalto", "--deployment", deployment_id,
         "--help"),
        ("collector", "run", "papercut", "--deployment", deployment_id,
         "--help"),
        ("dashboard", "generate", "--deployment", deployment_id, "--help"),
        ("credentials", "grafana", "--deployment", deployment_id, "--help"),
        ("logs", "collector", "--deployment", deployment_id, "--help"),
    )
    for arguments in commands:
        result = subprocess.run(
            [sys.executable, "scripts/itp.py", *arguments],
            cwd=os.fspath(ROOT), text=True, capture_output=True)
        assert result.returncode == 0, (arguments, result.stderr)
        assert "unrecognized arguments" not in result.stderr


def test_shared_collector_service_state_uses_compose_service_name():
    from scripts.itp import compose_service_state

    payload = json.dumps([
        {"Name": "itp-example-collector-1", "Service": "collector",
         "State": "running"},
        {"Name": "itp-example-discovery-1", "Service": "discovery",
         "State": "exited"},
    ])
    assert compose_service_state(payload, "collector") == "running"
    assert compose_service_state(payload, "discovery") == "exited"
    assert compose_service_state(payload, "paloalto") == "stopped"


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


@pytest.mark.parametrize(("body", "content_type"), [
    (b"apiv3_012345678901234567890123456789\r\n", "text/plain"),
    (b"\xef\xbb\xbfapiv3_012345678901234567890123456789\r\n",
     "application/json"),
    (b'{"token":"apiv3_012345678901234567890123456789"}',
     "application/json"),
])
def test_influx_bootstrap_parses_windows_and_supported_response_formats(
        tmp_path, monkeypatch, body, content_type):
    calls = []
    requests = []

    def urlopen(request, timeout):
        requests.append(request)
        if isinstance(request, str):
            return HttpResponse(status=200)
        return HttpResponse(body, status=201, content_type=content_type)

    runtime = manager(tmp_path, urlopen=urlopen, sleep=lambda _value: None)
    deployment = runtime.create(name="Example", non_interactive=True)
    install_compose_mock(monkeypatch, calls)
    runtime.bootstrap_influx(deployment)
    environment = runtime._read_env(deployment.env_file)
    assert environment["INFLUXDB_TOKEN"].startswith("apiv3_")
    assert any("/api/v3/configure/token/admin" in request.full_url
               for request in requests if not isinstance(request, str))
    assert any(arguments[:4] == (
        "exec", "-T", "influxdb3-core", "influxdb3") for arguments in calls)


@pytest.mark.parametrize(("body", "content_type", "message"), [
    (b"", "text/plain", "blank or malformed"),
    (b"{broken", "application/json", "malformed JSON"),
    (b'{"unexpected":true}', "application/json", "blank or malformed"),
])
def test_influx_bootstrap_rejects_blank_and_malformed_responses(
        tmp_path, monkeypatch, body, content_type, message):
    def urlopen(request, timeout):
        if isinstance(request, str):
            return HttpResponse(status=200)
        return HttpResponse(body, status=201, content_type=content_type)

    runtime = manager(tmp_path, urlopen=urlopen)
    deployment = runtime.create(name="Example", non_interactive=True)
    install_compose_mock(monkeypatch, [])
    with pytest.raises(RuntimeDeploymentError, match=message):
        runtime.bootstrap_influx(deployment)
    assert runtime._read_env(deployment.env_file)["INFLUXDB_TOKEN"] == ""


def test_influx_bootstrap_preflights_directory_and_writability(
        tmp_path, monkeypatch):
    runtime = manager(tmp_path, urlopen=lambda *_args, **_kwargs:
                      pytest.fail("must not request a token"))
    deployment = runtime.create(name="Example", non_interactive=True)
    deployment.env_file.unlink()
    with pytest.raises(RuntimeDeploymentError, match="environment is missing"):
        runtime.bootstrap_influx(deployment)

    deployment = runtime.create(
        name="Example", non_interactive=True, force=True)
    monkeypatch.setattr(
        "analysis.runtime_deployment.tempfile.NamedTemporaryFile",
        lambda **_kwargs: (_ for _ in ()).throw(PermissionError("denied")))
    with pytest.raises(RuntimeDeploymentError, match="not writable"):
        runtime.bootstrap_influx(deployment)


def test_influx_token_returned_but_atomic_persistence_fails_clearly(
        tmp_path, monkeypatch):
    def urlopen(request, timeout):
        return (HttpResponse(status=200) if isinstance(request, str) else
                HttpResponse(
                    b"apiv3_012345678901234567890123456789", status=201))

    runtime = manager(tmp_path, urlopen=urlopen)
    deployment = runtime.create(name="Example", non_interactive=True)
    install_compose_mock(monkeypatch, [])
    monkeypatch.setattr(
        "analysis.runtime_deployment.atomic_write",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PermissionError("denied")))
    with pytest.raises(RuntimeDeploymentError, match="atomic persistence failed"):
        runtime.bootstrap_influx(deployment)


def test_influx_endpoint_delay_and_http_failures_are_bounded(
        tmp_path, monkeypatch):
    calls = {"count": 0}

    def delayed(request, timeout):
        calls["count"] += 1
        if isinstance(request, str):
            return HttpResponse(status=200)
        if calls["count"] == 2:
            raise urllib.error.HTTPError(
                request.full_url, 503, "Starting", {},
                __import__("io").BytesIO(b"service unavailable"))
        return HttpResponse(
            b"apiv3_012345678901234567890123456789", status=201)

    runtime = manager(tmp_path, urlopen=delayed, sleep=lambda _value: None)
    deployment = runtime.create(name="Example", non_interactive=True)
    install_compose_mock(monkeypatch, [])
    runtime.bootstrap_influx(deployment)
    assert calls["count"] == 3

    def forbidden(request, timeout):
        if isinstance(request, str):
            return HttpResponse(status=200)
        raise urllib.error.HTTPError(
            request.full_url, 403, "Forbidden", {}, None)

    runtime.urlopen = forbidden
    deployment.env_file.write_text(
        deployment.env_file.read_text().replace(
            "INFLUXDB_TOKEN=apiv3_012345678901234567890123456789",
            "INFLUXDB_TOKEN="))
    with pytest.raises(RuntimeDeploymentError, match="HTTP 403"):
        runtime.bootstrap_influx(deployment)


def test_existing_influx_token_is_reused_without_creation(
        tmp_path, monkeypatch):
    requests = []

    def urlopen(request, timeout):
        requests.append(request)
        return HttpResponse(status=200)

    runtime = manager(tmp_path, urlopen=urlopen)
    deployment = runtime.create(name="Example", non_interactive=True)
    deployment.env_file.write_text(
        deployment.env_file.read_text().replace(
            "INFLUXDB_TOKEN=\n",
            "INFLUXDB_TOKEN=apiv3_012345678901234567890123456789\n"))
    install_compose_mock(monkeypatch, [])
    runtime.bootstrap_influx(deployment)
    runtime.bootstrap_influx(deployment)
    assert all(isinstance(request, str) for request in requests)


@pytest.mark.parametrize("non_interactive", [False, True])
def test_existing_unpersisted_influx_token_has_safe_recovery(
        tmp_path, monkeypatch, non_interactive):
    def urlopen(request, timeout):
        if isinstance(request, str):
            return HttpResponse(status=200)
        raise urllib.error.HTTPError(
            request.full_url, 409, "Conflict", {},
            __import__("io").BytesIO(b"token name already exists, _admin"))

    runtime = manager(tmp_path, urlopen=urlopen)
    deployment = runtime.create(name="Example", non_interactive=True)
    install_compose_mock(monkeypatch, [])
    with pytest.raises(RuntimeDeploymentError) as failure:
        runtime.bootstrap_influx(
            deployment, non_interactive=non_interactive)
    text = str(failure.value)
    assert "_admin already exists" in text
    assert "will not delete" in text
    assert ("Non-interactive recovery" in text) is non_interactive


def test_influx_reset_requires_explicit_interactive_confirmation(
        tmp_path, monkeypatch):
    runner_calls = []

    def runner(command, **_kwargs):
        runner_calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    runtime = manager(tmp_path, runner=runner)
    deployment = runtime.create(name="Example", non_interactive=True)
    compose_calls = []
    install_compose_mock(monkeypatch, compose_calls)
    with pytest.raises(RuntimeDeploymentError, match="not confirmed"):
        runtime.reset_influx(deployment, confirmation="yes")
    assert runner_calls == []
    with pytest.raises(RuntimeDeploymentError, match="non-interactive"):
        runtime.reset_influx(
            deployment, non_interactive=True,
            confirmation=f"RESET {deployment.deployment_id}")
    assert runner_calls == []
    runtime.reset_influx(
        deployment, confirmation=f"RESET {deployment.deployment_id}")
    assert ("rm", "-f", "-s", "influxdb3-core") in compose_calls
    assert runner_calls == [[
        "docker", "volume", "rm",
        f"itp-{deployment.deployment_id}_influxdb_data"]]


def test_retry_commands_are_platform_appropriate():
    assert retry_command(
        "deploy", "--force", "--verbose", system="Windows") == (
        r"powershell.exe -NoProfile -ExecutionPolicy Bypass "
        r"-File .\itp.ps1 deploy --force --verbose")
    assert retry_command(
        "deploy", "--force", "--verbose", system="Darwin") == (
        "./itp deploy --force --verbose")
    assert retry_command(
        "deploy", "--force", "--verbose", system="Linux") == (
        "./itp deploy --force --verbose")


def test_runtime_timezone_validation_is_iana_and_actionable(tmp_path):
    deployment = manager(tmp_path).create(
        name="Perth", timezone="Australia/Perth", non_interactive=True)
    assert deployment.load()["timezone"] == "Australia/Perth"
    with pytest.raises(RuntimeDeploymentError, match="Australia/Perth"):
        manager(tmp_path / "invalid").create(
            name="Invalid", timezone="Not/AZone", non_interactive=True)


def test_select_durably_migrates_legacy_site_alias(tmp_path):
    runtime = manager(tmp_path)
    deployment = runtime.create(name="Example", non_interactive=True)
    value = yaml.safe_load(deployment.collectors.read_text())
    value["site"] = "example"
    value["collectors"]["papercut"] = {
        "enabled": True, "site": "example"}
    deployment.collectors.write_text(yaml.safe_dump(value, sort_keys=False))
    selected = runtime.select("example")
    migrated = yaml.safe_load(selected.collectors.read_text())
    assert migrated["site"] == "site:example"
    assert migrated["collectors"]["papercut"]["site"] == "site:example"
    before = selected.collectors.read_text()
    runtime.select("example")
    assert selected.collectors.read_text() == before


def test_deployment_ca_store_builds_bundle_without_printing_contents(tmp_path):
    runtime = manager(tmp_path)
    deployment = runtime.create(name="Example", non_interactive=True)
    source = tmp_path / "private-root.pem"
    certificates = Path(certifi.where()).read_text()
    source.write_text(
        certificates.split("-----END CERTIFICATE-----", 1)[0]
        + "-----END CERTIFICATE-----\n")
    added = runtime.ca_add(deployment, source)
    assert len(added) == 1
    assert len(added[0]["fingerprint"]) == 64
    assert runtime.ca_list(deployment) == added
    assert "BEGIN CERTIFICATE" not in str(added)
    assert runtime._read_env(deployment.env_file)["ITP_CA_BUNDLE"] == \
        "/app/trust/ca-bundle.pem"
    removed = runtime.ca_remove(deployment, added[0]["fingerprint"][:20])
    assert removed == added[0]
    assert runtime.ca_list(deployment) == []
    assert runtime._read_env(deployment.env_file)["ITP_CA_BUNDLE"] == ""
