import subprocess
import sys
import uuid

import pytest
import yaml

from itp_profiles.settings import (
    SettingsError,
    resolve_influx_port,
    resolve_settings,
)
from itp_profiles.setup import BootstrapWizard, SetupError, SetupOptions


def complete_settings(**overrides):
    values = {
        "INFLUXDB_BUCKET": "local_system",
        "INFLUXDB_ORG": "itp",
        "INFLUXDB_PORT": "8181",
        "GRAFANA_PORT": "3000",
        "TZ": "UTC",
        "TELEGRAF_COLLECTION_INTERVAL": "60s",
        "ITP_DEPLOYMENT_ID": "00000000-0000-4000-8000-000000000001",
        "INFLUXDB_NODE_ID": "itp-test",
    }
    values.update(overrides)
    return values


def test_required_settings_reject_blanks_and_port_conflicts():
    with pytest.raises(SettingsError, match="INFLUXDB_BUCKET is blank"):
        resolve_settings(complete_settings(INFLUXDB_BUCKET=""))
    with pytest.raises(SettingsError, match="must differ"):
        resolve_settings(complete_settings(GRAFANA_PORT="8181"))


def test_legacy_influx_port_warns_and_conflicts_fail():
    warnings = []
    values = complete_settings()
    values.pop("INFLUXDB_PORT")
    values["INFLUXDB_HTTP_PORT"] = "8281"
    assert resolve_influx_port(values, warnings=warnings) == 8281
    assert "deprecated" in warnings[0]
    values["INFLUXDB_PORT"] = "8381"
    with pytest.raises(SettingsError, match="disagree"):
        resolve_influx_port(values)


def test_timezone_mapping_validation_and_fallback(monkeypatch):
    assert BootstrapWizard.detect_timezone(
        environment={}, windows_id="W. Australia Standard Time"
    ) == "Australia/Perth"
    monkeypatch.setattr(
        "itp_profiles.setup.datetime",
        type("Clock", (), {
            "now": staticmethod(lambda: type("Now", (), {
                "astimezone": lambda self: type(
                    "Aware", (), {"tzinfo": object()})()
            })())
        }))
    assert BootstrapWizard.detect_timezone(
        environment={}, localtime="/does/not/exist") == "UTC"
    with pytest.raises(SetupError, match="IANA"):
        BootstrapWizard.validate_timezone("Not/A-Timezone")


def test_collection_intervals_are_normalized_and_bounded():
    assert BootstrapWizard.validate_interval("5m") == "300s"
    assert BootstrapWizard.validate_interval("90s") == "90s"
    with pytest.raises(SetupError, match="between"):
        BootstrapWizard.validate_interval("2s")


def test_captured_subprocess_output_uses_utf8_replacement(tmp_path):
    wizard = BootstrapWizard(
        tmp_path, connector_registry=type(
            "Registry", (), {"all": lambda self: ()})())
    result = wizard._run([
        sys.executable, "-c",
        "import sys; sys.stdout.buffer.write(b'caf\\xc3\\xa9:\\xff')",
    ])
    assert result.stdout == "café:\ufffd"


def test_generated_files_share_one_deployment_contract(
        tmp_path, monkeypatch):
    root = tmp_path
    (root / "discovery").mkdir()
    repository = BootstrapWizard.__module__
    source = __import__(repository, fromlist=["x"])
    project = source.Path(source.__file__).resolve().parents[1]
    (root / ".env.example").write_text(
        (project / ".env.example").read_text())
    (root / "discovery/config.example.yml").write_text(
        (project / "discovery/config.example.yml").read_text())
    monkeypatch.setattr("itp_profiles.setup.shutil.which", lambda _: "/docker")
    monkeypatch.setattr(
        BootstrapWizard, "_port_available", staticmethod(lambda _: True))
    runner = lambda command, **kwargs: subprocess.CompletedProcess(
        command, 0, "", "")
    result = BootstrapWizard(root, runner=runner).run(SetupOptions(
        non_interactive=True,
        deployment_name="Contract Test",
        deployment_type="Business",
        grafana_port=3400,
        influxdb_port=8481,
        timezone="Australia/Perth",
        collection_interval="5m",
    ))
    env = BootstrapWizard._env_values((root / ".env").read_text())
    config = yaml.safe_load((root / "discovery/config.yml").read_text())
    settings = resolve_settings(env)
    assert settings.grafana_port == 3400
    assert settings.influx_port == 8481
    assert settings.database == "local_system"
    assert settings.organization == "itp"
    assert settings.timezone == "Australia/Perth"
    assert settings.collection_interval == "300s"
    assert uuid.UUID(settings.deployment_id)
    assert config["deployment_id"] == result.deployment_id
    assert all(not value["enabled"]
               for value in config["collectors"].values())
