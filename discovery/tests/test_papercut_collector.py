import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from analysis.operations.models import OperationalContext
from analysis.operations.rules import PaperCutHealthRule
from collectors.papercut.client import PaperCutClient
from collectors.papercut.models import (
    PaperCutAuthenticationError, PaperCutConfig,
    PaperCutMalformedResponseError, PaperCutUnreachableError,
)
from collectors.papercut.normalizer import normalize


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "collectors/papercut/fixtures"


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def settings():
    return PaperCutConfig(
        base_url="https://print.example.invalid:9192",
        authorization_key="", customer="example", site="main-campus")


def test_healthy_fixture_normalizes_inventory_and_canonical_telemetry():
    records, points, conditions = normalize(
        fixture("healthy.json"), settings(), "2026-01-01T00:00:00Z")
    assert conditions == []
    assert [value["hostname"] for value in records] == [
        "print.example.invalid", "Library MFD", "Office MFD"]
    assert {value["measurement"] for value in points} == {
        "device", "availability", "performance", "license"}
    app = next(value for value in points
               if value["measurement"] == "performance"
               and value["tags"]["component"] == "application")
    assert app["fields"]["cpu_percent"] == 18.5
    assert app["fields"]["jvm_memory_used_percent"] == 37.5
    licence = next(value for value in points
                   if value["measurement"] == "license")
    assert licence["fields"]["user_utilisation_percent"] == 65
    assert licence["fields"]["upgrade_assurance_remaining_days"] == 180


def test_device_errors_and_offline_services_generate_explainable_conditions():
    records, _, conditions = normalize(
        fixture("device-errors.json"), settings(), "2026-01-01T00:00:00Z")
    assert records[1]["online"] is False
    assert {value["code"] for value in conditions} == {
        "embedded_device_errors"}
    _, _, offline = normalize(
        fixture("offline-services.json"), settings(), "2026-01-01T00:00:00Z")
    assert {value["code"] for value in offline} == {
        "print_provider_offline"}


def test_conditions_are_promoted_to_canonical_operational_findings():
    snapshot = fixture("device-errors.json")
    records, _, _ = normalize(
        snapshot, settings(), "2026-01-01T00:00:00Z")
    records[0]["canonical_id"] = records[0]["id"]
    items = PaperCutHealthRule().evaluate(OperationalContext(
        now=datetime(2026, 1, 1, tzinfo=timezone.utc), assets=records))
    assert [(value.kind, value.category, value.severity) for value in items] == [
        ("issue", "Printing", "Medium")]
    assert items[0].evidence["code"] == "embedded_device_errors"


def test_client_uses_optional_authorization_without_exposing_it():
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(401)

    client = PaperCutClient(
        "https://print.example.invalid", "secret-value", max_retries=0,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(PaperCutAuthenticationError) as caught:
        asyncio.run(client.get())
    assert requests[0].headers["Authorization"] == "secret-value"
    assert "secret-value" not in str(caught.value)
    asyncio.run(client.client.aclose())


def test_client_rejects_malformed_response_and_reports_partial_detail():
    malformed = fixture("malformed.json")["health"]

    async def malformed_handler(request):
        return httpx.Response(200, json=malformed)

    client = PaperCutClient(
        "https://print.example.invalid", max_retries=0,
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(malformed_handler)))
    with pytest.raises(PaperCutMalformedResponseError):
        asyncio.run(client.snapshot())
    asyncio.run(client.client.aclose())

    healthy = fixture("healthy.json")["health"]

    async def partial_handler(request):
        if request.url.path.endswith("/devices"):
            return httpx.Response(500)
        return httpx.Response(200, json=healthy)

    partial = PaperCutClient(
        "https://print.example.invalid", max_retries=0,
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(partial_handler)))
    assert asyncio.run(partial.snapshot())["partial"] is True
    asyncio.run(partial.client.aclose())


def test_invalid_endpoint_and_connectivity_fail_safely():
    with pytest.raises(ValueError, match="HTTPS"):
        PaperCutClient("http://print.example.invalid")

    async def unavailable(request):
        raise httpx.ConnectError("connection refused", request=request)

    client = PaperCutClient(
        "https://print.example.invalid", max_retries=0,
        client=httpx.AsyncClient(transport=httpx.MockTransport(unavailable)))
    with pytest.raises(PaperCutUnreachableError) as caught:
        asyncio.run(client.get())
    assert "print.example.invalid" not in str(caught.value)
    asyncio.run(client.client.aclose())


def test_dashboard_is_classic_managed_and_uses_canonical_measurements():
    dashboard = json.loads(
        (ROOT / "dashboards/Printing/papercut-overview.json").read_text())
    assert dashboard["uid"] == "papercut-operational-overview"
    assert isinstance(dashboard["panels"], list) and len(dashboard["panels"]) >= 10
    assert "elements" not in dashboard and "layout" not in dashboard
    sql = "\n".join(target["rawSql"] for panel in dashboard["panels"]
                    for target in panel.get("targets", []))
    assert {"device", "availability", "performance", "license"} <= {
        name for name in ("device", "availability", "performance", "license")
        if name in sql}
    assert "papercut_" not in sql
    for panel in dashboard["panels"]:
        for target in panel.get("targets", []):
            assert target["rawQuery"] is True
            assert target["format"] == "table"
