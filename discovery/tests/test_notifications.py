import json
import sys
from datetime import datetime, timedelta, timezone

from analysis.notifications import (
    NotificationChannelRegistry,
    NotificationEngine,
    NotificationFingerprint,
    NotificationStore,
)
from analysis.notifications.rules import evaluate_conditions


NOW = datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc)


def config(**changes):
    value = {
        "enabled": True,
        "minimum_severity": "warning",
        "repeat_suppression_seconds": 3600,
        "daemon_heartbeat_stale_seconds": 30,
        "channels": {"console": {"enabled": False}},
    }
    value.update(changes)
    return value


def status(*, freshness="Fresh", daemon="Running", heartbeat=None,
           pipeline="success", connector_result="success"):
    heartbeat = heartbeat or "2026-07-24T07:59:59Z"
    return {
        "connectors": [{
            "connector": "alpha", "display_name": "Alpha",
            "enabled": True, "freshness": freshness,
            "last_successful_collection": "2026-07-24T07:59:00Z"}],
        "daemon": {
            "status": daemon, "last_heartbeat": heartbeat,
            "current_collection": [], "uptime_seconds": 60},
        "latest_pipeline_run": {
            "run_id": "run-1", "status": pipeline},
        "latest_connector_results": [{
            "connector": "alpha", "status": connector_result,
            "exception_type": "RuntimeError",
            "reason": "collector execution failed"}],
    }


def test_fingerprints_and_condition_generation_are_deterministic():
    left = NotificationFingerprint(
        "connector.stale", "alpha", "site-a").value
    right = NotificationFingerprint(
        "connector.stale", "alpha", "site-a").value
    assert left == right
    values = evaluate_conditions(status(
        freshness="Stale", daemon="Stopped", pipeline="partial",
        connector_result="failed"), now=NOW)
    assert {value["rule_id"] for value in values} == {
        "connector.collection_failed", "connector.stale", "daemon.stopped",
        "pipeline.partial"}
    assert values == sorted(values, key=lambda value: value["fingerprint"])


def test_doctor_failure_and_recovery_conditions():
    doctor = {"checks": [
        {"check_id": "platform.config", "subject": "Configuration",
         "status": "fail"}]}
    failed = evaluate_conditions(status(), doctor, now=NOW)
    assert any(value["rule_id"] == "doctor.failed" for value in failed)
    recovered = evaluate_conditions(
        status(), {"checks": [
            {"check_id": "platform.config", "subject": "Configuration",
             "status": "pass"}]}, now=NOW)
    assert not any(value["rule_id"] == "doctor.failed" for value in recovered)


def test_duplicate_suppression_occurrences_and_recovery(tmp_path):
    messages = []
    channels = NotificationChannelRegistry(output=messages.append)
    settings = config(
        channels={"console": {"enabled": True}})
    engine = NotificationEngine(
        tmp_path, settings, channel_registry=channels,
        now_fn=lambda: NOW)
    failure = status(connector_result="failed")
    first = engine.evaluate(failure)
    second = engine.evaluate(failure)
    assert len(first["new_events"]) == 1
    assert second["new_events"] == []
    active = list(NotificationStore(tmp_path).read()["active"].values())
    connector = next(
        value for value in active
        if value["rule_id"] == "connector.collection_failed")
    assert connector["occurrence_count"] == 2
    assert any(value["status"] == "suppressed"
               for value in second["deliveries"])

    recovery = engine.evaluate(status())
    assert any(value["rule_id"] == "connector.collection_failed.recovery"
               for value in recovery["recoveries"])
    assert all(value["rule_id"] != "connector.collection_failed"
               for value in NotificationStore(tmp_path).read()["active"].values())


def test_minimum_severity_and_disabled_configuration(tmp_path):
    critical_only = NotificationEngine(
        tmp_path, config(minimum_severity="critical"), now_fn=lambda: NOW)
    result = critical_only.evaluate(status(
        freshness="Stale", pipeline="partial"))
    assert all(value["severity"] == "critical"
               for value in NotificationStore(tmp_path).read()["active"].values())
    disabled_root = tmp_path / "disabled"
    disabled = NotificationEngine(disabled_root, {})
    assert disabled.evaluate(status(freshness="Stale"))["enabled"] is False
    assert not (disabled_root / "notifications/state.json").exists()


def test_webhook_success_payload_and_static_headers(tmp_path):
    requests = []

    def opener(request, timeout):
        requests.append((request, timeout))
        return type("Response", (), {"status": 204})()

    settings = config(channels={"webhook": {
        "enabled": True, "url": "https://hooks.example.invalid/secret-path",
        "timeout_seconds": 2, "headers": {
            "Authorization": "Bearer super-secret"}}})
    engine = NotificationEngine(
        tmp_path, settings,
        channel_registry=NotificationChannelRegistry(webhook_opener=opener),
        now_fn=lambda: NOW)
    result = engine.test()
    request, timeout = requests[0]
    payload = json.loads(request.data)
    assert timeout == 2
    assert request.headers["Authorization"] == "Bearer super-secret"
    assert payload["event"]["test"] is True
    assert result["deliveries"][0]["status"] == "delivered"
    assert "secret-path" not in json.dumps(result)
    assert "super-secret" not in json.dumps(result)


def test_webhook_failure_is_safe_and_does_not_raise(tmp_path):
    def opener(*_args, **_kwargs):
        raise RuntimeError(
            "https://token@example.invalid?api_key=super-secret")

    settings = config(channels={"webhook": {
        "enabled": True, "url": "https://example.invalid/private",
        "headers": {"X-Token": "super-secret"}}})
    engine = NotificationEngine(
        tmp_path, settings,
        channel_registry=NotificationChannelRegistry(webhook_opener=opener),
        now_fn=lambda: NOW)
    result = engine.test()
    assert result["deliveries"][0]["status"] == "failed"
    assert result["deliveries"][0]["exception_type"] == "RuntimeError"
    assert result["deliveries"][0]["detail"] == "delivery failed"
    assert "super-secret" not in json.dumps(result)


def test_acknowledgement_and_summary(tmp_path):
    engine = NotificationEngine(tmp_path, config(), now_fn=lambda: NOW)
    event = engine.evaluate(status(connector_result="failed"))["new_events"][0]
    acknowledged = NotificationStore(tmp_path).acknowledge(
        event["id"], "2026-07-24T08:01:00Z")
    assert acknowledged["acknowledged"] is True
    summary = engine.summary()
    assert summary["active_count"] >= 1
    state = NotificationStore(tmp_path).read()
    assert any(value["status"] == "acknowledged"
               for value in state["deliveries"])


def test_cli_list_and_test_human_and_json(tmp_path, monkeypatch, capsys):
    import scripts.itp as cli

    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setenv("ITP_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setattr(cli, "load_config", lambda _path: {
        "customer": "test", "collectors": {},
        "notifications": {"enabled": False}})

    monkeypatch.setattr(sys, "argv", ["./itp", "notifications", "list"])
    cli.main()
    assert "Notifications: 0 active" in capsys.readouterr().out

    monkeypatch.setattr(
        sys, "argv", ["./itp", "notifications", "list", "--json"])
    cli.main()
    assert json.loads(capsys.readouterr().out)["active"] == []

    monkeypatch.setattr(
        sys, "argv", ["./itp", "notifications", "test", "--json"])
    cli.main()
    value = json.loads(capsys.readouterr().out)
    assert value["event"]["test"] is True
    assert value["deliveries"] == []


def test_missing_notification_config_is_backwards_compatible(tmp_path):
    engine = NotificationEngine(tmp_path, None, now_fn=lambda: NOW)
    assert engine.enabled is False
    assert engine.summary() == {
        "enabled": False, "active_count": 0,
        "highest_active_severity": None,
        "most_recent_notification": None,
        "most_recent_successful_delivery": None,
        "failed_delivery_count": 0,
    }
