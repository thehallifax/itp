import json
from pathlib import Path

import yaml

from collectors import CollectorRegistry
from telemetry.schema import MEASUREMENTS, SCHEMA_VERSION


ROOT = Path(__file__).resolve().parents[2]


def test_platform_and_schema_metadata_are_stable():
    assert (ROOT / "VERSION").read_text().strip() == "0.1.0"
    assert (ROOT / "SCHEMA_VERSION").read_text().strip() == str(SCHEMA_VERSION) == "1"
    assert set(MEASUREMENTS) == {
        "device", "availability", "performance", "interface", "wireless",
        "firewall", "collector_health",
    }
    assert all(CollectorRegistry.get(name).schema_version == 1
               for name in CollectorRegistry.names())


def test_canonical_schema_documents_exist():
    expected = {"device", "availability", "performance", "interface", "wireless",
                "firewall", "server", "inventory", "relationship", "collector_health"}
    assert {path.stem for path in (ROOT / "docs/schema").glob("*.md")} == expected
    for name in expected:
        text = (ROOT / f"docs/schema/{name}.md").read_text().lower()
        assert "purpose" in text and "tags" in text and "fields" in text
        assert "example" in text and "collector" in text


def test_dashboard_folders_and_uids_are_fixed():
    folders = {"overview", "network", "wireless", "security", "compute", "vendor", "health"}
    assert folders <= {path.name for path in (ROOT / "dashboards").iterdir() if path.is_dir()}
    provision = yaml.safe_load((ROOT / "grafana/provisioning/dashboards/dashboards.yml").read_text())
    providers = provision["providers"]
    assert {item["folder"].lower() for item in providers} == folders
    folder_uids = [item["folderUid"] for item in providers]
    assert len(folder_uids) == len(set(folder_uids)) == 7
    dashboard_uids = [json.loads(path.read_text())["uid"]
                      for path in (ROOT / "dashboards").rglob("*.json")]
    assert len(dashboard_uids) == len(set(dashboard_uids))
    assert set(dashboard_uids) == {"mist-infrastructure-overview",
                                   "fortigate-infrastructure-overview"}


def test_flightsql_datasource_is_provisioned_with_dashboard_uid():
    provision = yaml.safe_load(
        (ROOT / "grafana/provisioning/datasources/influxdb.yml").read_text()
    )
    datasource = provision["datasources"][0]
    assert datasource["uid"] == "ffsu5ap2kr5dse"
    assert datasource["type"] == "influxdb"
    assert datasource["jsonData"] == {
        "version": "SQL",
        "dbName": "local_system",
        "httpMode": "POST",
        "insecureGrpc": True,
    }
    assert datasource["secureJsonData"]["token"] == "${INFLUXDB_TOKEN}"


def test_deployment_scripts_and_templates_exist():
    for path in ("scripts/install.sh", "scripts/update.sh", "scripts/Install-ITP.ps1",
                 "scripts/Update-ITP.ps1", ".env.example", "discovery/config.example.yml",
                 "secrets/mist.env.example", "secrets/fortigate.env.example"):
        assert (ROOT / path).is_file()
    gitignore = (ROOT / ".gitignore").read_text().splitlines()
    assert ".env" in gitignore and "discovery/config.yml" in gitignore
