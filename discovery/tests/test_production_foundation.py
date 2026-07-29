import json
from pathlib import Path

import yaml

from collectors import CollectorRegistry
from telemetry.schema import MEASUREMENTS, SCHEMA_VERSION


ROOT = Path(__file__).resolve().parents[2]


def test_platform_and_schema_metadata_are_stable():
    assert (ROOT / "VERSION").read_text().strip() == "0.2.0"
    assert (ROOT / "SCHEMA_VERSION").read_text().strip() == str(SCHEMA_VERSION) == "1"
    assert set(MEASUREMENTS) == {
        "device", "availability", "performance", "interface", "wireless",
        "firewall", "collector_health", "license", "content_package",
        "virtualisation_platform", "virtualisation_cluster", "virtualisation_host",
        "virtualisation_workload", "virtualisation_storage",
        "virtualisation_snapshot", "virtualisation_finding",
        "virtualisation_collection",
    }
    assert all(CollectorRegistry.get(name).schema_version == 1
               for name in CollectorRegistry.names())


def test_canonical_schema_documents_exist():
    expected = {"device", "availability", "performance", "interface", "wireless",
                    "firewall", "server", "inventory", "relationship", "collector_health",
                    "license", "content_package", "virtualisation"}
    assert {path.stem for path in (ROOT / "docs/schema").glob("*.md")} == expected
    for name in expected:
        text = (ROOT / f"docs/schema/{name}.md").read_text().lower()
        assert "purpose" in text and "tags" in text and "fields" in text
        assert "example" in text and "collector" in text


def test_dashboard_folders_and_uids_are_fixed():
    folders = {"Operations", "Infrastructure", "Security", "Wireless", "Printing",
               "Compute", "Identity", "Virtualisation", "Vendor"}
    provision = yaml.safe_load((ROOT / "grafana/provisioning/dashboards/dashboards.yml").read_text())
    providers = provision["providers"]
    assert {provider["folder"] for provider in providers} == folders
    folder_uids = [provider["folderUid"] for provider in providers]
    assert len(folder_uids) == len(set(folder_uids)) == len(folders)
    dashboard_uids = [json.loads(path.read_text())["uid"]
                      for path in (ROOT / "dashboards").rglob("*.json")]
    assert len(dashboard_uids) == len(set(dashboard_uids))
    assert set(dashboard_uids) == {
        "itp-infrastructure-overview", "mist-infrastructure-overview",
        "fortigate-infrastructure-overview", "paloalto-operational-overview",
        "itp-operations-wallboard", "itp-collector-health",
        "itp-virtualisation-overview", "itp-snmp-overview",
        "papercut-operational-overview",
    }


def test_flightsql_datasource_is_provisioned_with_dashboard_uid():
    provision = yaml.safe_load(
        (ROOT / "grafana/provisioning/datasources/influxdb.yml").read_text()
    )
    datasource = next(value for value in provision["datasources"]
                      if value["uid"] == "ffsu5ap2kr5dse")
    assert datasource["uid"] == "ffsu5ap2kr5dse"
    assert datasource["type"] == "influxdb"
    assert datasource["jsonData"] == {
        "version": "SQL",
        "dbName": "${INFLUXDB_BUCKET}",
        "httpMode": "POST",
        "insecureGrpc": True,
    }
    assert datasource["secureJsonData"]["token"] == "${INFLUXDB_TOKEN}"
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    assert "INFLUXDB_BUCKET=${INFLUXDB_BUCKET}" in \
        compose["services"]["grafana"]["environment"]


def test_deployment_scripts_and_templates_exist():
    for path in ("scripts/install.sh", "scripts/update.sh", "scripts/Install-ITP.ps1",
                 "scripts/Update-ITP.ps1", ".env.example", "discovery/config.example.yml",
                 "secrets/mist.env.example", "secrets/fortigate.env.example",
                 "secrets/papercut.env.example", "secrets/aruba.env.example"):
        assert (ROOT / path).is_file()
    gitignore = (ROOT / ".gitignore").read_text().splitlines()
    assert ".env" in gitignore and "discovery/config.yml" in gitignore
