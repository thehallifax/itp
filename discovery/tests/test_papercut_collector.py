import asyncio
import json
import ssl
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from analysis.operations.models import OperationalContext
from analysis.operations.rules import PaperCutHealthRule
from collectors.papercut.client import PaperCutClient
from collectors.papercut.collector import PaperCutCollector
from collectors.papercut.models import (
    PaperCutAuthenticationError, PaperCutAuthorizationError,
    PaperCutCertificateExpiredError, PaperCutConfig,
    PaperCutConnectionError, PaperCutHostnameMismatchError,
    PaperCutInvalidRequestError, PaperCutMalformedResponseError,
    PaperCutRedirectError,
    PaperCutUnknownIssuerError, PaperCutWrongEndpointError,
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


def test_disabled_tls_policy_is_warned_and_exposed(caplog, tmp_path):
    class Client:
        base_url = "https://print.example.invalid"
        api_requests = 0
        retry_count = 0

    collector = PaperCutCollector({
        "deployment_id": "example",
        "customer": "example",
        "site": "site:example",
        "collectors": {"papercut": {
            "enabled": True,
            "base_url": "https://print.example.invalid",
            "site": "site:example",
            "verify_tls": False,
        }},
    }, tmp_path / "inventory/devices.json", client=Client(), writer=object())
    assert collector.settings.verify_tls is False
    assert caplog.messages.count(
        "PaperCut TLS certificate verification is disabled for this "
        "deployment.") == 1


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
    assert requests[0].url.params.get_list("Authorization") == [
        "secret-value"]
    assert "Authorization" not in requests[0].headers
    assert "secret-value" not in str(caught.value)
    asyncio.run(client.client.aclose())


@pytest.mark.parametrize("base_url", [
    "https://print.example.invalid:9192",
    "https://print.example.invalid:9192/",
    "https://print.example.invalid:9192///",
    "https://print.example.invalid:9192/api/health",
])
def test_system_health_request_contract_is_exact(base_url):
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(200, json=fixture("healthy.json")["health"])

    client = PaperCutClient(
        base_url,
        "health-key", max_retries=0,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    asyncio.run(client.get())
    request = requests[0]
    assert request.method == "GET"
    assert request.url.path == "/api/health"
    assert request.url.params.get_list("Authorization") == ["health-key"]
    assert str(request.url).count("Authorization=") == 1
    assert "/api/health?" in str(request.url)
    assert "/api/health/?" not in str(request.url)
    assert "Authorization" not in request.headers
    assert request.headers["Accept"] == "application/json"
    assert "Content-Type" not in request.headers
    asyncio.run(client.client.aclose())


@pytest.mark.parametrize("content_type,body,expected", [
    ("text/plain", "Missing required auth parameter",
     "Missing required auth parameter"),
    ("application/json",
     '{"error":"bad request","authorization":"secret-value"}',
     '{"authorization":"[REDACTED]","error":"bad request"}'),
])
def test_http_400_returns_bounded_sanitized_diagnostic(
        content_type, body, expected):
    async def handler(request):
        return httpx.Response(
            400, text=body, headers={"Content-Type": content_type},
            request=request)

    client = PaperCutClient(
        "https://print.example.invalid", "secret-value", max_retries=0,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(PaperCutInvalidRequestError) as caught:
        asyncio.run(client.get())
    diagnostic = caught.value.diagnostic_payload()
    assert diagnostic == {
        "category": "invalid_request",
        "message": "PaperCut System Health rejected the request (HTTP 400)",
        "http_status": 400,
        "method": "GET",
        "path": "/api/health",
        "content_type": content_type,
        "response": expected,
    }
    assert "secret-value" not in json.dumps(diagnostic)
    asyncio.run(client.client.aclose())


def test_authorization_key_trims_edges_and_rejects_internal_controls():
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(401, request=request)

    client = PaperCutClient(
        "https://print.example.invalid", " \t\r\n valid key \r\n",
        max_retries=0,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(PaperCutAuthenticationError):
        asyncio.run(client.get())
    assert requests[0].url.params["Authorization"] == "valid key"
    assert "valid key" not in str(PaperCutAuthenticationError(
        "PaperCut System Health authentication failed (HTTP 401)"))
    asyncio.run(client.client.aclose())

    with pytest.raises(ValueError, match="control character"):
        PaperCutClient(
            "https://print.example.invalid", "invalid\x16key")


def test_http_400_html_is_stripped_truncated_and_redacted():
    body = (
        "<html><body>Authorization=secret-value "
        + ("failure " * 200) + "</body></html>")

    async def handler(request):
        return httpx.Response(
            400, text=body, headers={"Content-Type": "text/html"},
            request=request)

    client = PaperCutClient(
        "https://print.example.invalid", "secret-value", max_retries=0,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(PaperCutInvalidRequestError) as caught:
        asyncio.run(client.get())
    response = caught.value.diagnostic_payload()["response"]
    assert len(response) <= PaperCutClient.MAX_DIAGNOSTIC_BODY
    assert response.endswith("...")
    assert "<html>" not in response
    assert "secret-value" not in response
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
    with pytest.raises(PaperCutConnectionError) as caught:
        asyncio.run(client.get())
    assert "print.example.invalid" not in str(caught.value)
    asyncio.run(client.client.aclose())


@pytest.mark.parametrize("message,error", [
    ("certificate has expired", PaperCutCertificateExpiredError),
    ("hostname mismatch", PaperCutHostnameMismatchError),
    ("certificate verify failed: unable to get local issuer certificate",
     PaperCutUnknownIssuerError),
])
def test_tls_failures_have_actionable_categories(message, error):
    request = httpx.Request("GET", "https://print.example.invalid")
    failure = httpx.ConnectError(
        "connect failed", request=request)
    failure.__cause__ = ssl.SSLCertVerificationError(message)
    classified = PaperCutClient._connection_error(failure)
    assert isinstance(classified, error)
    assert "print.example.invalid" not in str(classified)
    if error is PaperCutUnknownIssuerError:
        assert "credentials ca add" in str(classified)


@pytest.mark.parametrize("status,error", [
    (401, PaperCutAuthenticationError),
    (403, PaperCutAuthorizationError),
    (404, PaperCutWrongEndpointError),
    (302, PaperCutRedirectError),
])
def test_http_responses_are_classified_not_reported_unreachable(status, error):
    async def handler(request):
        return httpx.Response(
            status, headers={"location": "/users"}, request=request)

    client = PaperCutClient(
        "https://print.example.invalid", max_retries=0,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(error):
        asyncio.run(client.get())
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
