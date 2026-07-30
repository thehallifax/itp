import json
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from analysis.operator import (
    OperatorCollectEngine,
    OperatorStatusEngine,
    render_collect,
    render_status,
)
from analysis.operator.engine import PipelineRunStore
from collectors.scheduler import Scheduler as RuntimeScheduler


NOW = datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc)


class Registry:
    def all(self):
        return (
            SimpleNamespace(
                id="alpha", display_name="Alpha", domains=("switching",)),
            SimpleNamespace(
                id="beta", display_name="Beta", domains=("firewall",)),
            SimpleNamespace(
                id="gamma", display_name="Gamma", domains=("wireless",)),
        )


class Collector:
    def __init__(self, name):
        self.name = name


class Scheduler:
    def __init__(self, collectors):
        self.collectors = collectors

    async def execute_once(self, phase):
        assert phase == "collect"
        return tuple({
            "connector": value.name,
            "status": "failed" if value.name == "beta" else "success",
            "duration_ms": 12,
            "value": (
                {"points_written": 4, "api_token": "must-not-leak"}
                if value.name == "alpha" else None),
            "exception_type": "RuntimeError" if value.name == "beta" else "",
            "reason": "collector execution failed" if value.name == "beta" else "",
        } for value in self.collectors)


def config(alpha=True, beta=True, gamma=False):
    return {
        "customer": "test-estate",
        "site": "site-a",
        "collectors": {
            "alpha": {
                "enabled": alpha, "collection_interval_seconds": 60},
            "beta": {
                "enabled": beta, "collection_interval_seconds": 60},
            "gamma": {
                "enabled": gamma, "collection_interval_seconds": 60},
        },
    }


def factory(name, _config, _inventory):
    return Collector(name)


def test_collect_records_pipeline_run_and_redacts_results(tmp_path):
    values = iter((NOW, NOW + timedelta(milliseconds=25)))
    result = OperatorCollectEngine(
        tmp_path, config(), registry=Registry(), collector_factory=factory,
        scheduler_factory=Scheduler, runtime_dir=tmp_path / "runtime",
        now_fn=lambda: next(values)).run()

    assert result["summary"] == {
        "successful": 1, "failed": 1, "skipped": 1,
        "duration_ms": 25, "overall": "partial"}
    assert [value["connector"] for value in result["connectors"]] == [
        "alpha", "beta", "gamma"]
    assert result["connectors"][0]["summary"] == {"points_written": 4}
    assert "must-not-leak" not in json.dumps(result)
    assert result["pipeline_run"]["status"] == "partial"
    assert result["pipeline_run"]["site_coverage"] == ["site-a"]
    persisted = PipelineRunStore(
        tmp_path / "runtime/pipeline-runs").latest()
    assert persisted == result
    assert "Alpha" in render_collect(result)


def write_run(store, connector, status, completed, *, reason="", points=0):
    store.write({
        "schema_version": 1,
        "deployment_identity": "test-estate",
        "pipeline_run": {
            "schema_version": 1, "run_id": f"{connector}-{completed}",
            "started_at": completed, "completed_at": completed,
            "status": "failed" if status == "failed" else "success",
            "canonical_output": "", "source_coverage": [],
            "provider_coverage": [], "site_coverage": [],
            "domain_coverage": [], "expected_scopes": [],
            "observed_scopes": [], "failed_scopes": [],
            "skipped_scopes": [], "scopes": [], "warning_details": []},
        "connectors": [{
            "connector": connector, "display_name": connector,
            "status": status, "duration_ms": 1,
            "summary": {"points_written": points},
            "exception_type": "", "reason": reason}],
        "summary": {},
    })


def test_status_reports_service_health_success_and_freshness(tmp_path):
    runtime = tmp_path / "runtime"
    store = PipelineRunStore(runtime / "pipeline-runs")
    write_run(store, "alpha", "success", "2026-07-24T07:59:00Z")
    services = runtime / "services/service-health.json"
    services.parent.mkdir(parents=True)
    services.write_text(json.dumps({"estate": {"services": [
        {"service": "Wireless", "status": "Warning"},
        {"service": "Internet", "status": "Healthy"},
    ]}}))

    result = OperatorStatusEngine(
        tmp_path, config(beta=False), registry=Registry(),
        runtime_dir=runtime, now_fn=lambda: NOW,
        readiness=(
            {"id": "alpha", "state": "configured", "missing": []},
            {"id": "beta", "state": "disabled", "missing": []},
            {"id": "gamma", "state": "disabled", "missing": []},
        )).run()
    states = {value["connector"]: value["freshness"]
              for value in result["connectors"]}
    assert states == {
        "alpha": "Fresh", "beta": "Disabled", "gamma": "Disabled"}
    assert result["connectors"][0]["health"] == "Healthy"
    assert result["connectors"][0]["last_successful_collection"] == \
        "2026-07-24T07:59:00Z"
    assert result["connectors"][0]["configuration_state"] == "configured"
    assert result["connectors"][0]["last_run"] == "2026-07-24T07:59:00Z"
    assert result["connectors"][0]["last_failure"] is None
    assert result["connectors"][0]["records_collected"] == 0
    assert result["service_health"] == [
        {"service": "Internet", "status": "Healthy"},
        {"service": "Wireless", "status": "Warning"},
    ]
    assert "Latest PipelineRun:" in render_status(result)


def test_status_freshness_states_are_deterministic(tmp_path):
    scenarios = (
        ("success", NOW - timedelta(seconds=150), "Aging"),
        ("success", NOW - timedelta(seconds=181), "Stale"),
        ("failed", NOW - timedelta(seconds=1), "Fresh"),
        ("skipped", NOW - timedelta(seconds=1), "Fresh"),
    )
    for index, (run_status, at, expected) in enumerate(scenarios):
        runtime = tmp_path / str(index)
        write_run(PipelineRunStore(runtime / "pipeline-runs"), "alpha",
                  run_status, at.isoformat().replace("+00:00", "Z"))
        result = OperatorStatusEngine(
            tmp_path, config(beta=False), registry=Registry(),
            runtime_dir=runtime, now_fn=lambda: NOW).run()
        assert result["connectors"][0]["freshness"] == expected

    empty = OperatorStatusEngine(
        tmp_path, config(beta=False), registry=Registry(),
        runtime_dir=tmp_path / "empty", now_fn=lambda: NOW).run()
    assert empty["connectors"][0]["freshness"] == "Never Run"
    assert empty["latest_pipeline_run"] is None


def test_status_recovery_uses_latest_result_and_preserves_failure_history(
        tmp_path):
    runtime = tmp_path / "runtime"
    store = PipelineRunStore(runtime / "pipeline-runs")
    write_run(
        store, "alpha", "failed", "2026-07-24T07:55:00Z",
        reason="connection unreachable")
    write_run(
        store, "alpha", "success", "2026-07-24T07:59:00Z", points=33)

    result = OperatorStatusEngine(
        tmp_path, config(beta=False), registry=Registry(),
        runtime_dir=runtime, now_fn=lambda: NOW).run()
    alpha = result["connectors"][0]

    assert alpha["status"] == "successful"
    assert alpha["health"] == "Healthy"
    assert alpha["freshness"] == "Fresh"
    assert alpha["records_collected"] == 33
    assert alpha["last_successful_collection"] == "2026-07-24T07:59:00Z"
    assert alpha["last_failure"] == "2026-07-24T07:55:00Z"
    assert alpha["last_error_summary"] == "connection unreachable"


def test_freshness_is_independent_from_current_health(tmp_path):
    runtime = tmp_path / "runtime"
    store = PipelineRunStore(runtime / "pipeline-runs")
    write_run(
        store, "alpha", "failed", "2026-07-24T07:59:00Z",
        reason="current failure")
    result = OperatorStatusEngine(
        tmp_path, config(beta=False), registry=Registry(),
        runtime_dir=runtime, now_fn=lambda: NOW).run()
    alpha = result["connectors"][0]
    assert alpha["freshness"] == "Fresh"
    assert alpha["health"] == "Failed"

    older = tmp_path / "older"
    write_run(
        PipelineRunStore(older / "pipeline-runs"), "alpha", "success",
        "2026-07-24T07:55:00Z")
    stale = OperatorStatusEngine(
        tmp_path, config(beta=False), registry=Registry(),
        runtime_dir=older, now_fn=lambda: NOW).run()["connectors"][0]
    assert stale["freshness"] == "Stale"
    assert stale["health"] == "Healthy"


def test_status_mixed_connectors_project_only_their_latest_results(tmp_path):
    runtime = tmp_path / "runtime"
    store = PipelineRunStore(runtime / "pipeline-runs")
    write_run(store, "alpha", "failed", "2026-07-24T07:55:00Z",
              reason="historical PaperCut failure")
    write_run(store, "alpha", "success", "2026-07-24T07:59:00Z", points=34)
    write_run(store, "beta", "failed", "2026-07-24T07:59:30Z",
              reason="current Palo Alto failure")
    result = OperatorStatusEngine(
        tmp_path, config(gamma=False), registry=Registry(),
        runtime_dir=runtime, now_fn=lambda: NOW).run()
    values = {value["connector"]: value for value in result["connectors"]}
    assert values["alpha"]["health"] == "Healthy"
    assert values["alpha"]["status"] == "successful"
    assert values["beta"]["health"] == "Failed"
    assert values["beta"]["status"] == "failed"


def test_status_json_contains_no_configuration_or_secrets(tmp_path):
    result = OperatorStatusEngine(
        tmp_path, {
            **config(beta=False),
            "collectors": {
                **config(beta=False)["collectors"],
                "alpha": {
                    "enabled": True, "api_token": "super-secret"},
            },
        }, registry=Registry(), runtime_dir=tmp_path / "runtime",
        now_fn=lambda: NOW).run()
    rendered = json.dumps(result, sort_keys=True)
    assert "super-secret" not in rendered
    assert json.loads(rendered)["deployment_identity"] == "test-estate"


def test_status_reports_missing_identifiers_without_secret_values(tmp_path):
    result = OperatorStatusEngine(
        tmp_path, config(beta=False), registry=Registry(),
        runtime_dir=tmp_path / "runtime", now_fn=lambda: NOW,
        readiness=(
            {"id": "alpha", "state": "pending credentials",
             "missing": ["ALPHA_API_TOKEN"]},
            {"id": "beta", "state": "disabled", "missing": []},
            {"id": "gamma", "state": "disabled", "missing": []},
        )).run()
    rendered = render_status(result)
    assert "pending credentials" in rendered
    assert "Missing: ALPHA_API_TOKEN" in rendered
    assert "secret-value" not in rendered
    assert result["connectors"][0]["status"] == "pending credentials"
    assert result["connectors"][1]["status"] == "disabled"


def test_status_surfaces_papercut_tls_policy(tmp_path):
    result = OperatorStatusEngine(
        tmp_path, config(beta=False), registry=Registry(),
        runtime_dir=tmp_path / "runtime", now_fn=lambda: NOW,
        readiness=(
            {"id": "alpha", "state": "configured", "missing": [],
             "tls_verification": False},
            {"id": "beta", "state": "disabled", "missing": []},
            {"id": "gamma", "state": "disabled", "missing": []},
        )).run()
    assert result["connectors"][0]["tls_verification"] is False
    assert "TLS certificate verification is disabled" in render_status(result)


def test_status_distinguishes_execution_mode_mismatch(tmp_path):
    result = OperatorStatusEngine(
        tmp_path, config(beta=False), registry=Registry(),
        runtime_dir=tmp_path / "runtime", now_fn=lambda: NOW,
        readiness=(
            {"id": "alpha", "state": "execution mode mismatch", "missing": [],
             "execution_mode": "edge", "runtime_mode": "central"},
            {"id": "beta", "state": "disabled", "missing": []},
            {"id": "gamma", "state": "disabled", "missing": []},
        )).run()
    alpha = result["connectors"][0]
    assert alpha["status"] == "skipped execution mode mismatch"
    assert alpha["execution_mode"] == "edge"
    assert alpha["runtime_mode"] == "central"
    assert "skipped execution mode mismatch" in render_status(result)


def test_scheduler_one_shot_isolates_connector_failures(caplog):
    class Good:
        name = "good"

        async def collect(self):
            return {"points_written": 2}

    class Bad:
        name = "bad"

        def collect(self):
            raise RuntimeError("credential=must-not-be-returned")

    outcomes = asyncio.run(
        RuntimeScheduler([Good(), Bad()]).execute_once("collect"))
    assert [value["connector"] for value in outcomes] == ["bad", "good"]
    assert outcomes[0]["status"] == "failed"
    assert outcomes[0]["exception_type"] == "RuntimeError"
    assert outcomes[0]["reason"] == "collector execution failed"
    assert "must-not-be-returned" not in json.dumps(outcomes)
    assert "must-not-be-returned" not in caplog.text
    assert outcomes[1]["value"] == {"points_written": 2}
