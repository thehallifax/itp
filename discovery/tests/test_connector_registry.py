import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from collectors.connector_registry import (
    ConnectorMetadataRegistry,
    DOMAINS,
)
from itp_profiles.setup import BootstrapWizard, SetupOptions


ROOT = Path(__file__).resolve().parents[2]
EXPECTED = {
    "snmp", "mist", "fortigate", "paloalto",
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


def test_alias_lookup_and_manual_only_inventory(registry):
    assert registry.get("pan-os").id == "paloalto"
    assert registry.get("vcenter").id == "vmware"
    assert {value.id for value in registry.manual_only()} == EXPECTED
    assert registry.filter(guided_setup=True) == ()
    assert {value.id for value in registry.filter(guided_setup=False)} == EXPECTED


def test_domain_and_deployment_filters(registry):
    assert {value.id for value in registry.filter(domain="virtualisation")} == {
        "vmware", "hyperv", "proxmox"}
    assert {value.id for value in registry.filter(domain="printing")} == {"snmp"}
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
