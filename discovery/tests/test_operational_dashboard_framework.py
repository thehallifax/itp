import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
OVERVIEW = ROOT / "dashboards/Infrastructure Overview/infrastructure-overview.json"


def test_infrastructure_overview_is_classic_json_with_stable_uid():
    dashboard = json.loads(OVERVIEW.read_text())
    assert dashboard["title"] == "Infrastructure Overview"
    assert dashboard["uid"] == "itp-infrastructure-overview"
    assert isinstance(dashboard["panels"], list)
    assert "elements" not in dashboard
    assert "layout" not in dashboard
    assert len({panel["id"] for panel in dashboard["panels"]}) == 33


def test_overview_contains_required_operational_panels_and_honest_placeholders():
    dashboard = json.loads(OVERVIEW.read_text())
    panels = {panel["title"]: panel for panel in dashboard["panels"]}
    required = {
        "Infrastructure Health", "Devices Online", "Devices Offline",
        "Observability Health", "Data Quality Findings", "Collectors Healthy", "Switches",
        "Access Points", "Firewalls", "Servers", "Printers", "WAN Latency",
        "WAN Packet Loss", "WAN Bandwidth", "DNS", "DHCP", "Active Directory",
        "PaperCut", "Certificates", "Active Issues", "Operational Risks",
        "Recommendations",
        "Sites", "Healthy Sites", "Warning Sites", "Critical Sites",
    }
    assert required <= panels.keys()
    operations_generated = {"Active Issues", "Operational Risks", "Recommendations"}
    state_generated = {
        "Infrastructure Health", "Devices Online", "Devices Offline",
        "Observability Health", "Data Quality Findings", "Collectors Healthy",
        "Switches", "Access Points", "Firewalls", "Servers", "Printers",
        "Sites", "Healthy Sites", "Warning Sites", "Critical Sites",
    }
    for title in required - operations_generated - state_generated:
        panel = panels[title]
        assert panel.get("description", "").startswith("TODO:")
        assert panel.get("targets", []) == []
    for title in operations_generated:
        assert "operations.json" in panels[title]["description"]
    for title in state_generated:
        assert "infrastructure-summary.json" in panels[title]["description"]


def test_vendor_dashboards_retain_titles_uids_and_classic_schema():
    expected = {
        "vendor/mist-infrastructure-overview.json":
            ("Mist Infrastructure Overview", "mist-infrastructure-overview"),
        "vendor/fortigate-overview.json":
            ("FortiGate Infrastructure Overview", "fortigate-infrastructure-overview"),
        "vendor/paloalto-overview.json":
            ("Palo Alto Operational Overview", "paloalto-operational-overview"),
    }
    for relative, (title, uid) in expected.items():
        dashboard = json.loads((ROOT / "dashboards" / relative).read_text())
        assert dashboard["title"] == title
        assert dashboard["uid"] == uid
        assert isinstance(dashboard["panels"], list)
        assert "elements" not in dashboard and "layout" not in dashboard


def test_provisioning_creates_fixed_operational_folders():
    config = yaml.safe_load(
        (ROOT / "grafana/provisioning/dashboards/dashboards.yml").read_text()
    )
    providers = config["providers"]
    expected = {"Operations", "Infrastructure", "Security", "Wireless", "Printing",
                "Compute", "Identity", "Virtualisation", "Vendor"}
    assert {provider["folder"] for provider in providers} == expected
    assert all(provider["folderUid"].startswith("itp-folder-") for provider in providers)
