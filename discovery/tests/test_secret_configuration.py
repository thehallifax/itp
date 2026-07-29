from pathlib import Path

import pytest
import yaml

from collectors.config import load_config
from collectors.mist.collector import MistCollector

ROOT = Path(__file__).resolve().parents[2]


def test_mist_secret_is_optional_and_collector_only():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    collector = compose["services"]["collector"]
    assert "profiles" not in collector
    assert collector["env_file"] == [
        "${ITP_ENV_FILE:-.env.example}",
        {"path": "${ITP_SECRETS_DIR:-./secrets}/influxdb.env", "required": False},
        {"path": "${ITP_SECRETS_DIR:-./secrets}/collector.env", "required": False},
        {"path": "${ITP_SECRETS_DIR:-./secrets}/mist.env", "required": False},
        {"path": "${ITP_SECRETS_DIR:-./secrets}/fortigate.env", "required": False},
        {"path": "${ITP_SECRETS_DIR:-./secrets}/paloalto.env", "required": False},
        {"path": "${ITP_SECRETS_DIR:-./secrets}/papercut.env", "required": False},
        {"path": "${ITP_SECRETS_DIR:-./secrets}/aruba.env", "required": False},
        {"path": "${ITP_SECRETS_DIR:-./secrets}/snmp.env", "required": False},
        {"path": "${ITP_SECRETS_DIR:-./secrets}/vmware.env", "required": False},
        {"path": "${ITP_SECRETS_DIR:-./secrets}/proxmox.env", "required": False},
    ]
    environment = "\n".join(collector["environment"])
    assert "MIST_ORG_ID" not in environment and "MIST_API_TOKEN" not in environment
    assert all(name in environment for name in ("INFLUXDB_HOST", "INFLUXDB_BUCKET"))
    assert "INFLUXDB_TOKEN" not in environment
    assert (
        "COLLECTOR_HEALTH_PATH=/app/runtime/"
        "${ITP_PROFILE:-legacy}/collector-health" in environment)


def test_secret_files_are_excluded_from_git_and_docker_context():
    gitignore = (ROOT / ".gitignore").read_text()
    dockerignore = (ROOT / ".dockerignore").read_text().splitlines()
    assert "secrets/**/*.env" in gitignore and "!secrets/**/*.env.example" in gitignore
    assert "secrets/" in dockerignore and ".env" in dockerignore and ".env.*" in dockerignore
    assert (ROOT / "secrets/mist.env.example").read_text() == "MIST_ORG_ID=\nMIST_API_TOKEN=\n"
    fortigate = (ROOT / "secrets/fortigate.env.example").read_text()
    assert "FORTIGATE_API_TOKEN=\n" in fortigate
    assert "FORTIGATE_VERIFY_TLS=true\n" in fortigate
    assert (ROOT / "secrets/paloalto.env.example").read_text() == "PALOALTO_API_KEY=\n"
    assert (ROOT / "secrets/papercut.env.example").read_text() == \
        "PAPERCUT_AUTHORIZATION_KEY=\n"
    assert (ROOT / "secrets/aruba.env.example").read_text() == (
        "ARUBA_CENTRAL_CLIENT_ID=\n"
        "ARUBA_CENTRAL_CLIENT_SECRET=\n"
        "ARUBA_CENTRAL_REFRESH_TOKEN=\n"
        "ARUBA_CENTRAL_ACCESS_TOKEN=\n")
    assert "MIST_" not in (ROOT / ".env.example").read_text()


def test_missing_environment_placeholders_fail_without_secret_values(tmp_path, monkeypatch):
    monkeypatch.delenv("MIST_ORG_ID", raising=False)
    monkeypatch.delenv("MIST_API_TOKEN", raising=False)
    path = tmp_path / "config.yml"
    path.write_text("collectors:\n  mist:\n    enabled: true\n    organization_id: ${MIST_ORG_ID}\n    api_token: ${MIST_API_TOKEN}\n")
    config = load_config(path)
    assert config["collectors"]["mist"]["organization_id"] == ""
    assert config["collectors"]["mist"]["api_token"] == ""
    with pytest.raises(ValueError, match="MIST_ORG_ID and MIST_API_TOKEN are required") as caught:
        MistCollector(config)
    assert "${" not in str(caught.value)
