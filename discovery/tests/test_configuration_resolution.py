import json
import os
import ast
import subprocess
from pathlib import Path

import pytest
import yaml

from collectors.config import load_config
from collectors.configuration import (
    ConfigurationError, ConfigurationResolver, parse_bool, parse_int,
    materialize_runtime_configuration, resolve_environment_value)
from collectors.connector_registry import ConnectorMetadataRegistry
from collectors.papercut.collector import validate_settings
from collectors.paloalto.collector import validate_settings as validate_paloalto
from collectors.writer import InfluxWriter
from itp_profiles import DeploymentProfile


ROOT = Path(__file__).resolve().parents[2]


def registry():
    return ConnectorMetadataRegistry.load(ROOT)


def connector(result, name):
    return next(value for value in result["connectors"]
                if value["connector"] == name)


def setting(result, connector_name, suffix):
    return next(value for value in connector(
        result, connector_name)["settings"] if value["name"].endswith(suffix))


def test_process_environment_precedes_profile_and_root_sources():
    resolver = ConfigurationResolver(
        registry(), {"collectors": {"mist": {"enabled": True}}},
        process_environment={"MIST_ORG_ID": "process-org",
                             "MIST_API_TOKEN": "process-token"},
        profile_environment={"MIST_ORG_ID": "profile-org",
                             "MIST_API_TOKEN": "profile-token"},
        root_environment={"MIST_ORG_ID": "root-org",
                          "MIST_API_TOKEN": "root-token"})
    result = resolver.evaluate()
    assert result["ready"] is True
    assert setting(result, "mist", "organization_id")["source"] == \
        "process environment"
    assert setting(result, "mist", "api_token")["source"] == \
        "process environment"
    assert "process-token" not in json.dumps(result)


def test_profile_environment_precedes_root_and_local_overrides_tracked():
    resolver = ConfigurationResolver(
        registry(), {"collectors": {"fortigate": {
            "enabled": True, "host": "https://tracked.example.invalid",
            "site": "site:example"}}},
        local_config={"collectors": {"fortigate": {
            "host": "https://local.example.invalid"}}},
        profile_environment={"FORTIGATE_HOST": "https://profile.example.invalid",
                             "FORTIGATE_API_TOKEN": "profile-token"},
        root_environment={"FORTIGATE_HOST": "https://root.example.invalid",
                          "FORTIGATE_API_TOKEN": "root-token"})
    result = resolver.evaluate()
    assert result["ready"] is True
    assert setting(result, "fortigate", "host")["source"] == \
        "profile environment"
    site = next(value for value in connector(result, "fortigate")["settings"]
                if value["name"] == "collectors.fortigate.site")
    assert site["source"] == "tracked deployment configuration"


def test_enabled_requires_mandatory_credentials_but_disabled_does_not():
    disabled = ConfigurationResolver(
        registry(), {"collectors": {"mist": {"enabled": False}}}).evaluate()
    assert disabled["ready"] is True
    assert connector(disabled, "mist")["ready"] is True
    enabled = ConfigurationResolver(
        registry(), {"collectors": {"mist": {"enabled": True}}}).evaluate()
    assert enabled["ready"] is False
    assert {value["status"] for value in connector(
        enabled, "mist")["settings"] if value["secret"]} == {"missing"}


def test_typed_parsing_is_strict_and_bounded():
    assert parse_bool("yes") is True
    assert parse_bool("OFF") is False
    assert parse_int("60", minimum=1, maximum=3600) == 60
    with pytest.raises(ConfigurationError, match="boolean"):
        parse_bool("sometimes")
    with pytest.raises(ConfigurationError, match="at most"):
        parse_int("3601", maximum=3600)


def test_runtime_materialization_uses_registry_aliases_and_custom_pointers():
    value = materialize_runtime_configuration({
        "collectors": {
            "paloalto": {"api_key_env": "SITE_PAN_KEY"},
            "papercut": {},
        },
        "virtualisation": {"endpoints": [
            {"provider": "vmware"},
            {"provider": "proxmox", "token_secret_env": "SITE_PVE_SECRET"},
        ]},
    }, registry(), {
        "SITE_PAN_KEY": "pan-secret",
        "PAPERCUT_AUTHORIZATION_KEY": "paper-secret",
        "VMWARE_USERNAME": "reader",
        "VMWARE_PASSWORD": "vm-secret",
        "PROXMOX_TOKEN_ID": "itp@pve!reader",
        "SITE_PVE_SECRET": "pve-secret",
    })
    assert value["collectors"]["paloalto"]["api_key"] == "pan-secret"
    assert value["collectors"]["papercut"]["authorization_key"] == \
        "paper-secret"
    vmware, proxmox = value["virtualisation"]["endpoints"]
    assert (vmware["username"], vmware["password"]) == \
        ("reader", "vm-secret")
    assert (proxmox["token_id"], proxmox["token_secret"]) == \
        ("itp@pve!reader", "pve-secret")


def test_collectors_accept_injected_credentials_and_writer_configuration():
    paloalto = {
        "collectors": {"paloalto": {
            "base_url": "https://192.0.2.10",
            "site": "site:example",
            "api_key": "injected-secret",
        }}}
    assert validate_paloalto(paloalto).api_key == "injected-secret"
    writer = InfluxWriter.from_config({
        "deployment_id": "deployment:test",
        "writer": {
            "url": "influxdb3-core",
            "token": "writer-secret",
            "database": "test",
        }})
    assert writer.url == "http://influxdb3-core:8181"
    assert writer.token == "writer-secret"
    assert writer.database == "test"
    assert writer.deployment_id == "deployment:test"


def test_connector_metadata_describes_complete_configuration_contract():
    for item in registry().all():
        assert item.configuration_namespace
        assert isinstance(item.validation_requirements, tuple)
        assert set(item.validation_requirements) <= {
            field["id"] for field in item.credential_fields}
        for field in item.credential_fields:
            assert set(field) >= {"id", "env", "required", "secret"}
            assert isinstance(field.get("env_aliases", []), list)


def test_connector_business_modules_do_not_read_process_environment_directly():
    approved = {
        ROOT / "collectors/__main__.py",
        ROOT / "collectors/config.py",
        ROOT / "collectors/configuration.py",
    }
    violations = []
    for path in sorted((ROOT / "collectors").rglob("*.py")):
        if path in approved:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                owner = node.func.value
                if (isinstance(owner, ast.Name) and owner.id == "os"
                        and node.func.attr == "getenv"):
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}")
            if isinstance(node, ast.Attribute) and isinstance(
                    node.value, ast.Name) and node.value.id == "os" \
                    and node.attr == "environ":
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert violations == []


def test_papercut_canonical_and_legacy_environment_names(monkeypatch):
    with pytest.warns(DeprecationWarning, match="PAPERCUT_AUTHORIZATION"):
        value, source, deprecated = resolve_environment_value(
            {"PAPERCUT_AUTHORIZATION": "legacy-value"},
            "PAPERCUT_AUTHORIZATION_KEY", ("PAPERCUT_AUTHORIZATION",))
    assert value == "legacy-value"
    assert source == "PAPERCUT_AUTHORIZATION"
    assert deprecated is True
    with pytest.warns(DeprecationWarning):
        resolved = materialize_runtime_configuration(
            {"collectors": {"papercut": {
                "base_url": "https://print.example.invalid",
                "site": "site:example"}}},
            registry(), {"PAPERCUT_AUTHORIZATION": "legacy-value"})
    settings = validate_settings(resolved)
    assert settings.authorization_key == "legacy-value"


def test_connector_local_overlay_is_deterministic(tmp_path, monkeypatch):
    discovery = tmp_path / "discovery"
    config_dir = tmp_path / "config"
    discovery.mkdir()
    config_dir.mkdir()
    (discovery / "config.yml").write_text(yaml.safe_dump({
        "collectors": {"mist": {"enabled": False,
                                "collection_interval_seconds": 120}}}))
    (config_dir / "connectors.local.yml").write_text(yaml.safe_dump({
        "collectors": {"mist": {"enabled": True,
                                "collection_interval_seconds": 60}}}))
    monkeypatch.delenv("ITP_PROFILE", raising=False)
    monkeypatch.setenv(
        "ITP_CONNECTORS_CONFIG",
        str(config_dir / "connectors.local.yml"))
    with pytest.warns(DeprecationWarning):
        value = load_config(discovery / "config.yml")
    assert value["collectors"]["mist"] == {
        "enabled": True, "collection_interval_seconds": 60}


def test_profile_local_configuration_has_explicit_provenance(tmp_path):
    profile = tmp_path / "profiles/example"
    secret_dir = tmp_path / "secrets/example"
    profile.mkdir(parents=True)
    secret_dir.mkdir(parents=True)
    for name, value in (
        ("discovery.yml", {"schema_version": 1, "collectors": {
            "mist": {"enabled": True, "base_url":
                     "https://tracked.example.invalid"}}}),
        ("sites.yml", {"sites": [{"id": "example",
                                  "display_name": "Example"}]}),
        ("dashboards.yml", {"enabled": True}),
    ):
        (profile / name).write_text(yaml.safe_dump(value))
    (profile / "connectors.local.yml").write_text(yaml.safe_dump({
        "collectors": {"mist": {
            "base_url": "https://local.example.invalid"}}}))
    (secret_dir / "mist.env").write_text(
        "MIST_ORG_ID=example-org\nMIST_API_TOKEN=example-token\n")
    (profile / "profile.yml").write_text(yaml.safe_dump({
        "profile": {"id": "example", "name": "Example",
                    "environment": "production",
                    "timezone": "UTC", "runtime_mode": "central"},
        "paths": {"discovery_config": "profiles/example/discovery.yml",
                  "sites_config": "profiles/example/sites.yml",
                  "dashboards_config": "profiles/example/dashboards.yml",
                  "secrets_dir": "secrets/example",
                  "runtime_dir": "runtime/example"},
        "telemetry": {"deployment_id": "example",
                      "influx_bucket": "itp_example", "influx_org": "itp"},
        "grafana": {"folder_prefix": "EXAMPLE",
                    "provisioning_namespace": "example"},
        "ports": {"grafana": 3900, "influxdb": 8900}}))
    deployment = DeploymentProfile.load("example", tmp_path)
    result = ConfigurationResolver.profile(
        deployment, registry(), process_environment={}).evaluate()
    assert result["ready"] is True
    assert setting(result, "mist", "api_token")["source"] == \
        "profile environment"
    base = next(value for value in connector(result, "mist")["settings"]
                if value["name"] == "collectors.mist.base_url")
    assert base["source"] == "deployment local configuration"
    assert "example-token" not in json.dumps(result)


def test_tracked_configuration_has_no_local_secret_files_or_obvious_values():
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, text=True,
        capture_output=True, check=True).stdout.splitlines()
    assert not any(
        path == ".env" or path.endswith(".local.yml")
        or path.startswith("secrets/") and path.endswith(".env")
        for path in tracked)
    scan = subprocess.run(
        ["git", "grep", "-I", "-n", "-E",
         r"(password|token|authorization_key):[[:space:]]+[^$<[:space:]]+"],
        cwd=ROOT, text=True, capture_output=True)
    assert scan.returncode in {0, 1}
    assert "example-token" not in scan.stdout
    dockerignore = (ROOT / ".dockerignore").read_text().splitlines()
    assert {
        ".env", ".env.*", "secrets/", "config/*.local.yml",
        "profiles/*/*.local.yml", "profiles/*/.env",
    } <= set(dockerignore)


def test_compose_connector_environment_sources_are_explicit():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    files = compose["services"]["collector"]["env_file"]
    paths = [value if isinstance(value, str) else value["path"]
             for value in files]
    assert paths[0] == "${ITP_ENV_FILE:-.env.example}"
    assert "${ITP_SECRETS_DIR:-./secrets}/collector.env" in paths
    assert "${ITP_SECRETS_DIR:-./secrets}/papercut.env" in paths
