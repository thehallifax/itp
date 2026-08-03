import json
import os
import shlex
import socket
import ssl
import zipfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from analysis.onboarding import inspect_tls, validate_wan_selection, wan_candidate
from analysis.doctor.models import DiagnosticCheck, DoctorReport
from analysis.operator_ux import SafeRedactor
from analysis.runtime_deployment import (
    RuntimeDeployment,
    RuntimeDeploymentManager,
    container_runtime_condition,
    deployment_runtime_state,
)
from analysis.runtime_deployment import RuntimeDeploymentError
from analysis.support import SupportBundleBuilder
from scripts.itp import (
    deployment_edit_should_prompt,
    deployment_verification_failure,
    load_deployment_environment,
)


@dataclass
class Connector:
    id: str
    display_name: str
    credential_fields: tuple = ()
    configuration_fields: tuple = ()


class Registry:
    def all(self):
        return (Connector("paloalto", "Palo Alto"),
                Connector("fortigate", "FortiGate"),
                Connector("papercut", "PaperCut"))

    def get(self, name):
        return next(value for value in self.all() if value.id == name)


def manager(tmp_path, **kwargs):
    return RuntimeDeploymentManager(
        tmp_path, registry=Registry(), output_fn=lambda _value: None,
        port_fn=lambda _address, _port: True, **kwargs)


def test_deployment_edit_dry_run_cancel_and_atomic_backup(tmp_path):
    runtime = manager(tmp_path, input_fn=lambda _prompt: "n")
    deployment = runtime.create(
        name="Example", non_interactive=True, collectors=["paloalto"])
    original = deployment.manifest.read_text()
    preview = runtime.edit(deployment, {"display_name": "Changed"}, dry_run=True)
    assert preview["valid"] and not preview.get("applied")
    assert deployment.manifest.read_text() == original
    cancelled = runtime.edit(deployment, {"display_name": "Changed"})
    assert cancelled["cancelled"] and deployment.manifest.read_text() == original
    applied = runtime.edit(
        deployment, {"display_name": "Changed", "grafana_port": 3300}, yes=True)
    assert applied["applied"] and applied["restart_required"]
    assert deployment.manifest.with_name("deployment.yml.rollback").is_file()
    assert deployment.load()["display_name"] == "Changed"
    assert runtime._read_env(deployment.env_file)["GRAFANA_PORT"] == "3300"


def test_dry_run_without_changes_is_noninteractive_and_does_not_write(tmp_path):
    runtime = manager(tmp_path, input_fn=lambda _prompt: (_ for _ in ()).throw(
        AssertionError("dry-run prompted")))
    deployment = runtime.create(name="Example", non_interactive=True)
    before = {path: path.read_bytes() for path in (
        deployment.manifest, deployment.collectors, deployment.dashboards,
        deployment.env_file)}
    result = runtime.edit(deployment, {}, dry_run=True)
    assert result["dry_run"] is True
    assert result["applied"] is False
    assert result["valid"] is True
    assert result["changes"] == []
    assert before == {path: path.read_bytes() for path in before}
    assert not any(deployment.path.rglob("*.rollback"))
    assert not deployment_edit_should_prompt(
        supplied=False, dry_run=True, json_output=False)
    assert not deployment_edit_should_prompt(
        supplied=False, dry_run=False, json_output=True)
    assert deployment_edit_should_prompt(
        supplied=False, dry_run=False, json_output=False)


def test_interrupted_edit_does_not_write_or_create_rollback(tmp_path):
    runtime = manager(tmp_path, input_fn=lambda _prompt: (_ for _ in ()).throw(
        KeyboardInterrupt()))
    deployment = runtime.create(name="Example", non_interactive=True)
    before = {path: path.read_bytes() for path in (
        deployment.manifest, deployment.collectors, deployment.dashboards,
        deployment.env_file)}
    try:
        runtime.edit(deployment, {"display_name": "Changed"})
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("edit did not propagate cancellation to CLI boundary")
    assert before == {path: path.read_bytes() for path in before}
    assert not any(deployment.path.rglob("*.rollback"))


def test_selected_deployment_rebases_only_owned_runtime_paths(tmp_path, monkeypatch):
    runtime = manager(tmp_path)
    deployment = runtime.create(name="Example", non_interactive=True)
    original = deployment.env_file.read_text()
    stale = Path("/old/repository/runtime/deployments") / deployment.deployment_id
    deployment.env_file.write_text(original + "".join((
        f"ITP_RUNTIME_DIR={stale}\n",
        f"ITP_DASHBOARD_DIR={stale}/generated/dashboard\n",
        f"ITP_SITES_CONFIG={stale}/generated/sites.yml\n",
        "ITP_CA_BUNDLE=/external/pki/deployment-ca.pem\n",
        "EXTERNAL_CONFIG=/external/config.yml\n",
        "CONTAINER_CONFIG=/app/runtime/example/config.yml\n",
    )))
    persisted = deployment.env_file.read_bytes()
    values = deployment.environment()
    assert values["ITP_RUNTIME_DIR"] == str(deployment.path.resolve())
    assert values["ITP_DASHBOARD_DIR"] == str(
        (deployment.path / "generated/dashboard").resolve())
    assert values["ITP_SITES_CONFIG"] == str(
        (deployment.path / "generated/sites.yml").resolve())
    assert values["ITP_CA_BUNDLE"] == "/external/pki/deployment-ca.pem"
    assert values["EXTERNAL_CONFIG"] == "/external/config.yml"
    assert values["CONTAINER_CONFIG"] == "/app/runtime/example/config.yml"
    assert deployment.env_file.read_bytes() == persisted
    monkeypatch.setattr(os, "environ", {})
    load_deployment_environment(deployment)
    assert os.environ["ITP_SITES_CONFIG"] == str(
        (deployment.path / "generated/sites.yml").resolve())
    assert deployment.env_file.read_bytes() == persisted


def test_windows_owned_path_rebases_and_traversal_is_rejected(tmp_path):
    runtime = manager(tmp_path)
    deployment = runtime.create(name="Example", non_interactive=True)
    deployment.env_file.write_text(
        "ITP_SITES_CONFIG=C:\\Old\\runtime\\deployments\\example\\generated\\sites.yml\n")
    assert deployment.environment()["ITP_SITES_CONFIG"] == str(
        (deployment.path / "generated/sites.yml").resolve())
    deployment.env_file.write_text(
        "ITP_SITES_CONFIG=/old/runtime/deployments/example/../other/sites.yml\n")
    try:
        deployment.environment()
    except RuntimeDeploymentError as exc:
        assert "unsafe deployment-owned path" in str(exc)
    else:
        raise AssertionError("traversal-like deployment path was accepted")


def test_wan_recommendation_validation_and_identity_separation():
    candidate = wan_candidate({
        "interface_name": "ethernet1/6", "alias": "Backup ISP",
        "zone": "L3-Internet", "ip_address": "203.0.113.10",
        "operational_status": "up"}, "paloalto")
    assert candidate["likely_wan"]
    assert candidate["suggested_display_name"] == "Backup ISP"
    selected = validate_wan_selection([
        {"name": "ethernet1/5", "role": "primary", "display_name": "WAN 1"},
        {"name": "ethernet1/6", "role": "secondary", "display_name": "WAN 2"},
    ], [{"interface_name": "ethernet1/5"}, {"interface_name": "ethernet1/6"}])
    assert selected["mappings"][1] == {
        "name": "ethernet1/6", "role": "secondary", "display_name": "WAN 2"}


def test_fortigate_sdwan_is_recommended_and_missing_manual_mapping_warns():
    candidate = wan_candidate({"interface_name": "port9", "alias": "Fibre",
                               "sdwan_member": True}, "fortigate")
    assert candidate["likely_wan"] and "SD-WAN" in candidate["reason"]
    result = validate_wan_selection([
        {"name": "wan2", "role": "primary", "display_name": "Backup"}],
        [{"interface_name": "wan1"}])
    assert result["missing"] == ["wan2"]


def test_tls_diagnostics_distinguish_dns_tcp_and_private_trust():
    def dns_failure(_host):
        raise socket.gaierror

    assert inspect_tls("https://example.invalid", resolver=dns_failure)[
        "category"] == "dns_failure"

    def tcp_failure(*_args, **_kwargs):
        raise ConnectionRefusedError

    assert inspect_tls(
        "https://example.invalid", resolver=lambda _host: "192.0.2.1",
        connection_fn=tcp_failure)["category"] == "tcp_failure"

    class Raw:
        def __enter__(self): return self
        def __exit__(self, *_args): return False

    class Context:
        def wrap_socket(self, *_args, **_kwargs):
            raise ssl.SSLCertVerificationError("private CA unable to verify")

    result = inspect_tls(
        "https://example.invalid", resolver=lambda _host: "192.0.2.1",
        connection_fn=lambda *_args, **_kwargs: Raw(), context=Context())
    assert result["category"] == "tls_trust_failure"
    assert "credentials ca add" in result["guidance"]


def test_redactor_removes_structural_url_and_log_secrets_with_stable_privacy():
    redactor = SafeRedactor(["fictional-secret"], privacy="high")
    value = redactor.value({
        "api_key": "fictional-secret",
        "endpoint": "https://user:fictional-secret@example.invalid/api?Authorization=fictional-secret",
        "hostname": "firewall.example.invalid",
        "log": "Authorization: fictional-secret",
    })
    text = json.dumps(value)
    assert "fictional-secret" not in text and "user:" not in text
    assert value["hostname"] == redactor.value(
        {"hostname": "firewall.example.invalid"})["hostname"]
    assert "192.0.2.10" not in redactor.text(
        "peer firewall.example.invalid at 192.0.2.10")


def test_support_bundle_is_scoped_redacted_readable_and_deterministic(tmp_path):
    runtime = manager(tmp_path)
    deployment = runtime.create(name="Example", non_interactive=True)
    secret = "fictional-known-secret"
    deployment.env_file.write_text(
        deployment.env_file.read_text() + f"EXAMPLE_TOKEN={secret}\n")
    (deployment.path / "state/scheduler.json").write_text(json.dumps({
        "authorization": secret, "status": "ready"}))

    def runner(command, **_kwargs):
        return SimpleNamespace(returncode=0,
                               stdout=f"safe output token={secret}", stderr="")

    result = SupportBundleBuilder(
        tmp_path, deployment, runner=runner, privacy="high").build(
            tmp_path / "support")
    with zipfile.ZipFile(result["path"]) as archive:
        assert archive.testzip() is None
        assert "manifest.json" in archive.namelist()
        assert "deployment/dashboard-publication.json" in archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["included"] == sorted(manifest["included"])
        assert all(secret.encode() not in archive.read(name)
                   for name in archive.namelist())
        assert not any("secrets/" in name or name.endswith("deployment.env")
                       for name in archive.namelist())
    if os.name != "nt":
        assert oct(Path(result["path"]).stat().st_mode & 0o777) == "0o600"


def test_recovery_plan_never_defaults_to_destructive_action(tmp_path):
    runtime = manager(tmp_path)
    deployment = runtime.create(name="Example", non_interactive=True)
    plan = runtime.recovery_plan(deployment)
    destructive = [item for item in plan["actions"] if item.get("destructive")]
    assert destructive and plan["actions"][0].get("destructive") is not True
    assert any(item["id"] == "support" for item in plan["actions"])


def _empty_docker(command, **_kwargs):
    return SimpleNamespace(returncode=0, stdout="", stderr="")


def _assert_recovery_commands_target(plan, deployment_id):
    for action in plan["actions"]:
        arguments = shlex.split(action["command"])
        selector = "--deployment-id" if "--deployment-id" in arguments \
            else "--deployment"
        assert arguments[arguments.index(selector) + 1] == deployment_id


def test_recovery_exactly_selects_requested_similarly_named_stopped_deployment(
        tmp_path):
    runtime = manager(tmp_path, runner=_empty_docker)
    runtime.create(name="Campus", deployment_id="campus", non_interactive=True)
    requested = runtime.create(
        name="Campus Test", deployment_id="campus-test", non_interactive=True)
    plan = runtime.recovery_plan(requested)
    assert plan["deployment_id"] == "campus-test"
    assert plan["state"] == "stopped"
    assert plan["detected"]["deployment_id"] == "campus-test"
    assert plan["detected"]["compose_project"] == "itp-campus-test"
    assert plan["detected"]["runtime_path"] == str(requested.path)
    _assert_recovery_commands_target(plan, "campus-test")


def test_recovery_exactly_selects_requested_partial_deployment(tmp_path):
    runtime = manager(tmp_path, runner=_empty_docker)
    runtime.create(name="Alpha", deployment_id="alpha", non_interactive=True)
    requested = runtime.create(
        name="Zulu", deployment_id="zulu", non_interactive=True)
    requested.env_file.unlink()
    plan = runtime.recovery_plan(requested)
    assert plan["state"] == "partial"
    assert plan["detected"]["deployment_id"] == "zulu"
    _assert_recovery_commands_target(plan, "zulu")


def test_recovery_missing_deployment_fails_before_generating_actions(tmp_path):
    runtime = manager(tmp_path, runner=_empty_docker)
    missing = RuntimeDeployment(tmp_path, "missing")
    try:
        runtime.recovery_plan(missing)
    except RuntimeDeploymentError as exc:
        assert str(exc) == "deployment inventory is unavailable: missing"
    else:
        raise AssertionError("unresolved deployment produced recovery actions")


def test_recovery_state_classification_never_calls_restart_loop_running():
    restarting = {"Service": "collector", "State": "restarting",
                  "Status": "Restarting (1) 2 seconds ago"}
    running = {"State": "running", "Health": "healthy"}
    healthy_stack = [{**running, "Service": service} for service in (
        "collector", "discovery", "grafana", "influxdb3-core", "telegraf")]
    assert container_runtime_condition(restarting) == (
        "failed", "Container is restarting")
    assert deployment_runtime_state(
        configured=True, generated=True, containers=[restarting]) == "failed"
    assert deployment_runtime_state(
        configured=True, generated=True,
        containers=[running, restarting]) == "degraded"
    assert deployment_runtime_state(
        configured=True, generated=True, containers=healthy_stack) == "running"
    assert deployment_runtime_state(
        configured=True, generated=True,
        containers=[{"Service": "influxdb3-core", **running}]) == "degraded"
    assert deployment_runtime_state(
        configured=True, generated=True, containers=[]) == "stopped"
    assert deployment_runtime_state(
        configured=True, generated=False, containers=[]) == "partial"


def test_deployment_verification_surfaces_failing_service_and_safe_action():
    check = DiagnosticCheck(
        "services.container.collector", "Services", "collector", "fail",
        "error", "Container is restarting", detail="Runtime import failure")
    report = DoctorReport(
        "2026-08-03T00:00:00Z", "example", {}, (check,))
    rendered = deployment_verification_failure(report, "example")
    assert "Deployment verification failed." in rendered
    assert "collector" in rendered
    assert "Restarting" in rendered
    assert "Runtime import failure" in rendered
    assert "./itp logs collector --deployment example" in rendered
    assert "./itp recover --deployment example" in rendered
    assert "Traceback" not in rendered
