import json
from pathlib import Path


def test_mist_dashboard_is_valid_and_uses_available_metrics():
    root = Path(__file__).resolve().parents[2]
    dashboard = json.loads((root / "dashboards/vendor/mist-infrastructure-overview.json").read_text())
    assert dashboard["title"] == "Mist Infrastructure Overview"
    titles = {panel["title"] for panel in dashboard["panels"]}
    assert {"Total Devices Reporting", "Online Devices", "Offline / Disconnected",
            "Recently Offline Devices", "Access Point Usage", "Device Health", "Collector Health"} <= titles
    variables = {item["name"] for item in dashboard["templating"]["list"]}
    assert variables == {"site", "device_type", "device", "status"}
    encoded = json.dumps(dashboard).lower()
    assert "temperature_celsius" not in encoded
    assert "radio" not in encoded and "ssid" not in encoded and "poe" not in encoded
    assert "mist_api_token" not in encoded and "authorization" not in encoded
