import json
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from analysis.dashboards import DashboardRegistry
from collectors.connector_registry import (
    DOMAINS,
    ConnectorMetadataRegistry,
)
from itp_profiles.setup import BootstrapWizard, SetupOptions

ROOT = Path(__file__).resolve().parents[2]
EXPECTED = {
    "snmp", "mist", "fortigate", "paloalto", "papercut", "aruba",
    "vmware", "hyperv", "proxmox",
}


@pytest.fixture
def registry():
    return ConnectorMetadataRegistry.load(ROOT)


def test_complete_inventory_stable_ids_and_deterministic_order(registry):
    values = registry.all()
    assert {value.id for value in values} == EXPECTED
    assert [value.id for value in values] == sorted(EXPECTED)
    assert all(value.id == value.id.casefold() for value in values)
    assert registry.to_dict() == registry.to_dict()
    assert ConnectorMetadataRegistry(ROOT).to_dict() == registry.to_dict()
    assert ConnectorMetadataRegistry(registry.all()).to_dict() == registry.to_dict()
    assert all(value.remediation_command for value in registry.all())


def test_alias_lookup_and_manual_only_inventory(registry):
    assert registry.get("pan-os").id == "paloalto"
    assert registry.get("vcenter").id == "vmware"
    assert {value.id for value in registry.manual_only()} == EXPECTED
    assert registry.filter(guided_setup=True) == ()
    assert {value.id for value in registry.filter(guided_setup=False)} == EXPECTED


def test_domain_and_deployment_filters(registry):
    assert {value.id for value in registry.filter(domain="virtualisation")} == {
        "vmware", "hyperv", "proxmox"}
    assert {value.id for value in registry.filter(domain="printing")} == {
        "papercut", "snmp"}
    assert {value.id for value in registry.filter(
        deployment_type="Home Lab")} == EXPECTED
    assert {value.id for value in registry.filter(deployment_type="School")} == EXPECTED
    with pytest.raises(ValueError, match="unknown infrastructure domain"):
        registry.filter(domain="made-up")


def test_duplicate_id_and_alias_rejection(registry):
    values = list(registry.all())
    with pytest.raises(ValueError, match="duplicate IDs"):
        ConnectorMetadataRegistry(ROOT, values + [values[0]])
    duplicate = replace(values[1], aliases=(values[0].aliases[0],))
    with pytest.raises(ValueError, match="duplicate aliases"):
        ConnectorMetadataRegistry(ROOT, [values[0], duplicate, *values[2:]])


def test_invalid_domain_and_documentation_rejection(registry):
    values = list(registry.all())
    assert set(value for item in values for value in item.domains) <= DOMAINS
    with pytest.raises(ValueError, match="invalid domains"):
        ConnectorMetadataRegistry(
            ROOT, [replace(values[0], domains=("not-a-domain",)), *values[1:]])
    with pytest.raises(ValueError, match="documentation does not exist"):
        ConnectorMetadataRegistry(
            ROOT, [replace(values[0], documentation="docs/missing.md"), *values[1:]])


def test_prompt_metadata_is_optional_validated_and_deterministic(registry):
    fortigate = registry.get("fortigate")
    assert fortigate.configuration_prompts[0]["value_type"] == "host"
    assert fortigate.credential_fields[0]["configuration_field"] == (
        "collectors.fortigate.host")
    values = list(registry.all())
    target = values.index(fortigate)
    invalid = replace(
        fortigate,
        configuration_prompts=({
            "field": "collectors.fortigate.host",
            "value_type": "password-ish",
        },))
    with pytest.raises(ValueError, match="invalid prompt value type"):
        ConnectorMetadataRegistry(
            ROOT, [*values[:target], invalid, *values[target + 1:]])
    legacy = replace(fortigate, configuration_prompts=())
    loaded = ConnectorMetadataRegistry(
        ROOT, [*values[:target], legacy, *values[target + 1:]])
    assert loaded.get("fortigate").configuration_prompts == ()


def test_runtime_mode_skips_only_repository_artifact_references(registry):
    values = list(registry.all())
    missing_template = replace(
        values[0],
        secret_handling={
            **values[0].secret_handling,
            "templates": ["secrets/not-in-runtime.env.example"],
        })
    changed = [missing_template, *values[1:]]
    with pytest.raises(ValueError, match="secret template does not exist"):
        ConnectorMetadataRegistry(ROOT, changed)
    runtime_metadata = replace(
        missing_template,
        documentation="docs/not-in-runtime.md",
        dashboard_manifest="collectors/not-in-runtime.yml")
    runtime = ConnectorMetadataRegistry(
        ROOT, [runtime_metadata, *values[1:]], validation_mode="runtime")
    assert runtime.get(runtime_metadata.id) == runtime_metadata


def test_runtime_mode_retains_schema_and_implementation_validation(registry):
    values = list(registry.all())
    with pytest.raises(ValueError, match="invalid domains"):
        ConnectorMetadataRegistry(
            ROOT, [replace(values[0], domains=("invalid",)), *values[1:]],
            validation_mode="runtime")
    with pytest.raises(ValueError, match="implementation does not exist"):
        ConnectorMetadataRegistry(
            ROOT, [
                replace(values[0],
                        implementation="collectors/missing.py:Collector"),
                *values[1:],
            ], validation_mode="runtime")


def test_runtime_dashboard_generation_does_not_require_secret_templates(
        tmp_path):
    root = tmp_path / "image"
    shutil.copytree(ROOT / "collectors", root / "collectors")
    shutil.copytree(ROOT / "dashboards", root / "dashboards")
    output = root / "runtime/dashboard/managed"
    result = DashboardRegistry(
        root, {"deployment_id": "runtime-test", "collectors": {}},
        output, root / "runtime/dashboard/provisioning/dashboards.yml",
        registry_validation_mode="runtime").generate()
    assert result["enabled_collectors"] == []
    assert not (root / "secrets").exists()
    assert (output / "operations/itp-collector-health.json").is_file()


def test_registry_load_has_no_config_secret_or_runtime_dependency(
        tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in tuple(os.environ):
        if name.endswith(("TOKEN", "PASSWORD", "COMMUNITY")):
            monkeypatch.delenv(name, raising=False)
    registry = ConnectorMetadataRegistry.load(ROOT)
    assert {value.id for value in registry.all()} == EXPECTED
    assert not (tmp_path / "runtime").exists()
    completed = subprocess.run(
        [sys.executable, "-c",
         "import sys; from collectors.connector_registry import "
         "ConnectorMetadataRegistry; ConnectorMetadataRegistry.load(); "
         "assert 'collectors.mist.client' not in sys.modules; "
         "assert 'collectors.paloalto.api' not in sys.modules"],
        cwd=ROOT, text=True, capture_output=True)
    assert completed.returncode == 0, completed.stderr


def run_cli(*arguments):
    return subprocess.run(
        [sys.executable, "-m", "collectors", "connectors", *arguments],
        cwd=ROOT, text=True, capture_output=True,
        env={key: value for key, value in os.environ.items()
             if key not in {"COLLECTOR_CONFIG", "ITP_PROFILE"}})


def test_json_and_human_cli_listing_are_deterministic():
    first = run_cli("list", "--json")
    second = run_cli("list", "--json")
    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout
    payload = json.loads(first.stdout)
    assert [value["id"] for value in payload["connectors"]] == sorted(EXPECTED)
    human = run_cli("list")
    assert human.returncode == 0
    assert "fortigate\tFortiGate" in human.stdout
    assert "domains=firewall,internet,switching" in human.stdout


def test_collector_cli_uses_runtime_registry_validation(
        monkeypatch, capsys, registry):
    import collectors.__main__ as cli

    calls = []

    def load(root, path=None, *, validation_mode="strict"):
        calls.append(validation_mode)
        return registry

    monkeypatch.setattr(cli.ConnectorMetadataRegistry, "load", load)
    monkeypatch.setattr(
        sys, "argv", ["collectors", "connectors", "list", "--json"])
    cli.main()
    assert calls == ["runtime"]
    assert json.loads(capsys.readouterr().out)["schema_version"] == 1


def test_connector_inspection_and_unknown_id():
    inspected = run_cli("inspect", "pan-os", "--json")
    assert inspected.returncode == 0
    value = json.loads(inspected.stdout)
    assert value["id"] == "paloalto"
    assert value["capabilities"]["validation"] is True
    human = run_cli("inspect", "vmware")
    assert "Support: profile-only" in human.stdout
    assert "Setup: profile-manual" in human.stdout
    unknown = run_cli("inspect", "missing")
    assert unknown.returncode != 0
    assert "unknown connector: missing" in unknown.stderr


def test_oobe_consumes_injected_registry_contract(
        tmp_path, monkeypatch):
    class Registry:
        def __init__(self):
            self.calls = 0
        def all(self):
            self.calls += 1
            return ("one", "two")

    source = ROOT / "discovery/config.example.yml"
    (tmp_path / "discovery").mkdir()
    (tmp_path / ".env.example").write_text((ROOT / ".env.example").read_text())
    (tmp_path / "discovery/config.example.yml").write_text(source.read_text())
    fake = Registry()
    messages = []

    def runner(command, **kwargs):
        if command[-3:] == ["compose", "ps", "-q"]:
            return type("Result", (), {"stdout": ""})()
        return type("Result", (), {"stdout": "ok", "stderr": ""})()

    monkeypatch.setattr("itp_profiles.setup.shutil.which", lambda _: "/docker")
    monkeypatch.setattr(
        BootstrapWizard, "_port_available", staticmethod(lambda _: True))
    BootstrapWizard(
        tmp_path, runner=runner, output_fn=messages.append,
        connector_registry=fake).run(SetupOptions(non_interactive=True))
    assert fake.calls == 1
    assert any("Connector catalogue: 2 registered" in value for value in messages)
