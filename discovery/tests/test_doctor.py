import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from analysis.doctor import (
    DoctorEngine,
    DoctorFatalError,
    DoctorUsageError,
    render_human,
    render_json,
)
from collectors.connector_registry import ConnectorMetadataRegistry


ROOT = Path(__file__).resolve().parents[2]
NOW = "2026-07-24T00:00:00Z"


@pytest.fixture(autouse=True)
def isolated_deployment_environment(monkeypatch):
    monkeypatch.delenv("ITP_PROFILE", raising=False)
    monkeypatch.delenv("ITP_RUNTIME_DIR", raising=False)


def repository(tmp_path, *, config=True, env=True):
    for directory in ("collectors", "analysis", "discovery", "docs",
                      "grafana", "secrets"):
        (tmp_path / directory).mkdir(exist_ok=True)
    for template in (
            ".env.example", "discovery/config.example.yml",
            "secrets/mist.env.example", "secrets/fortigate.env.example",
            "secrets/paloalto.env.example", "secrets/snmp.env.example"):
        target = tmp_path / template
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("template\n")
    (tmp_path / "docker-compose.yml").write_text(yaml.safe_dump({
        "services": {name: {"image": "example"} for name in
                     ("collector", "discovery", "grafana",
                      "influxdb3-core", "telegraf")}}))
    if env:
        (tmp_path / ".env").write_text(
            "GRAFANA_PORT=39001\n"
            "INFLUXDB_PORT=39002\n"
            "INFLUXDB_BUCKET=test_database\n"
            "INFLUXDB_ORG=test_org\n"
            "INFLUXDB_NODE_ID=test-node\n"
            "ITP_DEPLOYMENT_ID=00000000-0000-4000-8000-000000000001\n"
            "TZ=UTC\n"
            "TELEGRAF_COLLECTION_INTERVAL=60s\n")
    datasource = tmp_path / "grafana/provisioning/datasources/influxdb.yml"
    datasource.parent.mkdir(parents=True, exist_ok=True)
    datasource.write_text(yaml.safe_dump({
        "apiVersion": 1,
        "datasources": [{
            "uid": "ffsu5ap2kr5dse",
            "jsonData": {"dbName": "${INFLUXDB_BUCKET}"},
        }],
    }))
    if config:
        (tmp_path / "discovery/config.yml").write_text(yaml.safe_dump({
            "schema_version": 1,
            "collectors": {"snmp": {"enabled": False}},
            "state_history": {"enabled": False},
            "operations": {"enabled": True},
        }))
    return tmp_path


class Runner:
    def __init__(self, *, compose=False, info=False, rows=None):
        self.commands = []
        self.fail_compose = compose
        self.fail_info = info
        self.rows = rows if rows is not None else [
            {"Service": name, "State": "running", "Health": "healthy"}
            for name in ("collector", "discovery", "grafana",
                         "influxdb3-core", "telegraf")]

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        if command == ["docker", "compose", "version"] and self.fail_compose:
            raise subprocess.CalledProcessError(1, command, stderr="missing compose")
        if command == ["docker", "info"] and self.fail_info:
            raise subprocess.CalledProcessError(1, command, stderr="daemon down")
        if command[-3:] == ["config", "--services"]:
            return SimpleNamespace(stdout="\n".join(
                ("collector", "discovery", "grafana",
                 "influxdb3-core", "telegraf")), stderr="")
        if command[-4:] == ["compose", "ps", "--format", "json"]:
            return SimpleNamespace(stdout=json.dumps(self.rows), stderr="")
        return SimpleNamespace(stdout="ok", stderr="")


def engine(root, **kwargs):
    value = DoctorEngine(
        root, registry=ConnectorMetadataRegistry.load(ROOT),
        now_fn=lambda: NOW, **kwargs)
    value.port_in_use = lambda port: False
    return value


def check(report, identifier):
    return next(value for value in report.checks if value.check_id == identifier)


def test_clean_offline_report_is_ordered_and_skips_live_checks(tmp_path):
    report = engine(repository(tmp_path), offline=True).run()
    assert [value.check_id for value in report.checks] == sorted(
        value.check_id for value in report.checks)
    assert check(report, "platform.config").status == "pass"
    assert check(report, "platform.runtime").status == "warn"
    assert check(report, "platform.provisioning").status == "warn"
    assert check(report, "services.daemon").status == "skip"
    assert check(report, "state_history.configuration").summary == \
        "State history is disabled"
    assert report.generated_at == NOW


@pytest.mark.parametrize("missing,check_id", [
    (".env", "platform.env"),
    ("discovery/config.yml", "platform.config"),
])
def test_missing_local_runtime_files_still_produce_report(
        tmp_path, missing, check_id):
    root = repository(tmp_path)
    (root / missing).unlink()
    report = engine(root, offline=True).run()
    assert check(report, check_id).status == "warn"
    assert report.exit_code() == (1 if missing == ".env" else 0)


def test_malformed_yaml_and_state_history_configuration(tmp_path):
    root = repository(tmp_path)
    (root / "discovery/config.yml").write_text("broken: [")
    report = engine(root, offline=True).run()
    assert check(report, "platform.config").status == "fail"
    assert report.exit_code() == 1
    (root / "discovery/config.yml").write_text(yaml.safe_dump({
        "schema_version": 1, "collectors": {}, "state_history": "bad"}))
    report = engine(root, offline=True).run()
    assert check(report, "state_history.configuration").status == "fail"


def test_invalid_registry_prevents_report(tmp_path):
    with pytest.raises(ValueError, match="documentation does not exist"):
        ConnectorMetadataRegistry(repository(tmp_path))


def test_doctor_loads_registry_with_strict_validation(monkeypatch):
    registry = ConnectorMetadataRegistry.load(ROOT)
    calls = []

    def load(root, path=None, *, validation_mode="strict"):
        calls.append(validation_mode)
        return registry

    monkeypatch.setattr(
        "analysis.doctor.engine.ConnectorMetadataRegistry.load", load)
    DoctorEngine(ROOT, offline=True)
    assert calls == ["strict"]


def test_docker_and_compose_unavailable_are_isolated(tmp_path):
    report = engine(repository(tmp_path), which_fn=lambda _: None).run()
    assert check(report, "services.docker").status == "fail"
    report = engine(
        repository(tmp_path), which_fn=lambda _: "/docker",
        runner=Runner(compose=True)).run()
    assert check(report, "services.compose_v2").status == "fail"


def test_daemon_service_stopped_and_unhealthy(tmp_path):
    report = engine(
        repository(tmp_path), which_fn=lambda _: "/docker",
        runner=Runner(info=True)).run()
    assert check(report, "services.daemon").status == "fail"
    rows = [
        {"Service": "collector", "State": "exited"},
        {"Service": "discovery", "State": "running"},
        {"Service": "grafana", "State": "running", "Health": "unhealthy"},
        {"Service": "influxdb3-core", "State": "running"},
        {"Service": "telegraf", "State": "running"},
    ]
    report = engine(
        repository(tmp_path), which_fn=lambda _: "/docker",
        runner=Runner(rows=rows), http_fn=lambda url, timeout: 200).run()
    assert check(report, "services.container.collector").status == "fail"
    assert check(report, "services.container.grafana").status == "fail"


def test_port_conflict_warns_and_strict_fails(tmp_path):
    value = engine(repository(tmp_path), offline=True)
    value.port_in_use = lambda port: True
    report = value.run()
    assert check(report, "platform.ports").status == "warn"
    assert report.exit_code() == 0
    assert report.exit_code(strict=True) == 1


def test_connector_alias_manual_profile_and_unknown(tmp_path):
    root = repository(tmp_path)
    report = engine(root, offline=True, connector="telegraf-snmp").run()
    assert check(report, "connector.snmp.doctor").status == "unavailable"
    report = engine(root, offline=True, connector="vcenter").run()
    assert "profile" in check(
        report, "connector.vmware.configured").summary.casefold() or \
        check(report, "connector.vmware.configured").status == "warn"
    with pytest.raises(DoctorUsageError, match="unknown connector"):
        engine(root, offline=True, connector="unknown")


def test_incomplete_connector_and_missing_secret_names_only(tmp_path):
    root = repository(tmp_path)
    (root / "discovery/config.yml").write_text(yaml.safe_dump({
        "schema_version": 1,
        "collectors": {"mist": {"enabled": True, "base_url": ""}},
    }))
    report = engine(root, offline=True, connector="mist").run()
    assert check(report, "connector.mist.configuration").status == "warn"
    credential = check(report, "connector.mist.credentials")
    assert credential.status == "warn"
    assert "MIST_API_TOKEN" in credential.detail


def test_validation_exception_timeout_and_secret_redaction(tmp_path, monkeypatch):
    root = repository(tmp_path)
    secret = "DOCTOR-SECRET-8675309"
    monkeypatch.setenv("MIST_ORG_ID", "org")
    monkeypatch.setenv("MIST_API_TOKEN", secret)
    (root / "discovery/config.yml").write_text(yaml.safe_dump({
        "schema_version": 1,
        "collectors": {"mist": {"enabled": True,
                                "base_url": "https://api.mist.com"}},
    }))

    def failure(config, timeout):
        raise RuntimeError("token=" + secret)
    report = engine(
        root, connector="mist", validation_adapters={"mist": failure},
        which_fn=lambda _: None).run()
    rendered = render_human(report) + render_json(report)
    assert secret not in rendered
    assert "[REDACTED]" in rendered
    assert check(report, "connector.mist.validation").status == "fail"

    def timeout(config, timeout):
        raise TimeoutError("bounded timeout")
    report = engine(
        root, connector="mist", validation_adapters={"mist": timeout},
        which_fn=lambda _: None).run()
    assert check(report, "connector.mist.validation").exception_type == "TimeoutError"


def test_json_and_human_rendering_contract(tmp_path):
    report = engine(repository(tmp_path), offline=True).run()
    payload = json.loads(render_json(report))
    assert payload["schema_version"] == 1
    assert payload["exit_code_meaning"]["2"].startswith("invalid usage")
    assert payload["checks"] == sorted(
        payload["checks"], key=lambda value: value["check_id"])
    human = render_human(report)
    assert "Infrastructure Telemetry Platform Doctor" in human
    assert "State History" in human and "[SKIP]" in human
    assert "Scheduler" in human


def test_doctor_interprets_scheduler_state_and_failure_streaks(
        tmp_path, monkeypatch):
    root = repository(tmp_path)
    runtime = tmp_path / "runtime"
    state = runtime / "scheduler/state.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({
        "schema_version": 1,
        "lifecycle_state": "degraded",
        "updated_at": NOW,
        "initial_discovery": {"outcome": "success", "duration_ms": 4},
        "initial_collection": {"outcome": "failed", "duration_ms": 5},
        "last_successful_discovery": NOW,
        "last_successful_collection": None,
        "consecutive_discovery_failures": 0,
        "consecutive_collection_failures": 2,
        "last_skip_reason": "active_collection",
    }))
    monkeypatch.setenv("ITP_RUNTIME_DIR", str(runtime))
    report = engine(root, offline=True).run()
    scheduler = check(report, "scheduler.state")
    assert scheduler.status == "warn"
    assert scheduler.metadata["lifecycle_state"] == "degraded"
    assert "collection_failures=2" in scheduler.detail
    assert "active_collection" in scheduler.detail


def test_doctor_reports_missing_and_malformed_scheduler_state(
        tmp_path, monkeypatch):
    root = repository(tmp_path)
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("ITP_RUNTIME_DIR", str(runtime))
    assert check(
        engine(root, offline=True).run(), "scheduler.state").status == "skip"
    state = runtime / "scheduler/state.json"
    state.parent.mkdir(parents=True)
    state.write_text("{broken")
    assert check(
        engine(root, offline=True).run(), "scheduler.state").status == "fail"


def test_doctor_warns_when_wallboard_is_older_than_runtime_state(
        tmp_path, monkeypatch):
    root = repository(tmp_path)
    runtime = tmp_path / "runtime"
    dashboard = runtime / (
        "dashboard/managed/operations/itp-operations-wallboard.json")
    dashboard.parent.mkdir(parents=True)
    dashboard.write_text('{"panels":[]}')
    source = runtime / "operations/operations.json"
    source.parent.mkdir(parents=True)
    source.write_text('{"generated_at":"2026-07-30T01:00:00Z"}')
    os.utime(dashboard, ns=(1_000_000_000, 1_000_000_000))
    os.utime(source, ns=(2_000_000_000, 2_000_000_000))
    monkeypatch.setenv("ITP_RUNTIME_DIR", str(runtime))

    result = check(
        engine(root, offline=True).run(),
        "operations.wallboard_freshness")

    assert result.status == "warn"
    assert "older than authoritative runtime state" in result.summary
    assert "operations/operations.json" in result.detail


def test_doctor_detects_active_bootstrap_payload_but_ignores_no_value(
        tmp_path, monkeypatch):
    root = repository(tmp_path)
    runtime = tmp_path / "runtime"
    source = runtime / "inventory/source_runs.json"
    source.parent.mkdir(parents=True)
    source.write_text('{"sources":{"example":{"last_run":{"success":true}}}}')
    dashboard = runtime / (
        "dashboard/managed/operations/itp-operations-wallboard.json")
    dashboard.parent.mkdir(parents=True)
    dashboard.write_text(json.dumps({"panels": [{
        "fieldConfig": {"defaults": {"noValue": "Not Yet Collected"}},
        "targets": [{"csvContent":
                     "scope,value\\r\\nall,Waiting for first collection"}]}]}))
    os.utime(source, ns=(1_000_000_000, 1_000_000_000))
    os.utime(dashboard, ns=(2_000_000_000, 2_000_000_000))
    monkeypatch.setenv("ITP_RUNTIME_DIR", str(runtime))

    result = check(
        engine(root, offline=True).run(),
        "operations.wallboard_freshness")
    assert result.status == "warn"
    assert result.metadata == {}
    assert "bootstrap" in result.summary

    dashboard.write_text(json.dumps({"panels": [{
        "fieldConfig": {"defaults": {"noValue": "Not Yet Collected"}},
        "targets": [{"csvContent": "scope,value\\r\\nall,Healthy"}]}]}))
    os.utime(dashboard, ns=(3_000_000_000, 3_000_000_000))
    result = check(
        engine(root, offline=True).run(),
        "operations.wallboard_freshness")
    assert result.status == "pass"


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes are not authoritative")
def test_doctor_reports_unreadable_grafana_publication(tmp_path, monkeypatch):
    root = repository(tmp_path)
    runtime = tmp_path / "runtime"
    provisioning = runtime / "generated/dashboard/provisioning/dashboards.yml"
    managed = runtime / (
        "generated/dashboard/managed/operations/"
        "itp-operations-wallboard.json")
    provisioning.parent.mkdir(parents=True)
    managed.parent.mkdir(parents=True)
    provisioning.write_text("apiVersion: 1\n")
    managed.write_text('{"panels":[]}')
    provisioning.chmod(0o600)
    managed.chmod(0o600)
    monkeypatch.setenv("ITP_RUNTIME_DIR", str(runtime))

    result = check(
        engine(root, offline=True).run(),
        "operations.dashboard_publication")
    assert result.status == "warn"
    assert "mode=0600" in result.detail
    assert result.command == "./itp restart"


def test_cli_exit_codes_json_alias_and_no_runtime_dependency(
        tmp_path, monkeypatch, capsys, caplog):
    import collectors.__main__ as cli

    root = repository(tmp_path)
    registry = ConnectorMetadataRegistry.load(ROOT)
    monkeypatch.setattr(
        "analysis.doctor.engine.ConnectorMetadataRegistry.load",
        lambda _root: registry)
    monkeypatch.setattr(cli, "ROOT", root)
    monkeypatch.setattr(
        sys, "argv", ["collectors", "doctor", "--connector", "unknown",
                     "--offline"])
    with pytest.raises(SystemExit) as unknown:
        cli.main()
    assert unknown.value.code == 2
    assert "unknown connector" in caplog.text

    monkeypatch.setattr(
        sys, "argv", ["collectors", "doctor", "--connector",
                     "telegraf-snmp", "--offline", "--json"])
    with pytest.raises(SystemExit) as selected:
        cli.main()
    assert selected.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"]["connector"] == "snmp"


def test_platform_and_connector_scope_modes(tmp_path):
    root = repository(tmp_path)
    platform_report = engine(root, offline=True, platform_only=True).run()
    assert {value.category for value in platform_report.checks} == {"Platform"}
    connectors_report = engine(
        root, offline=True, connectors_only=True).run()
    assert {value.category for value in connectors_report.checks} == {"Connectors"}
