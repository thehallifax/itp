import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import bootstrap


def _python_path(environment):
    return environment / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python")


def _current_environment(tmp_path):
    environment = tmp_path / ".venv"
    python = _python_path(environment)
    python.parent.mkdir(parents=True)
    python.write_text("")
    digest = bootstrap.dependency_hash(tmp_path / "pyproject.toml")
    bootstrap.marker_path(environment).write_text(json.dumps(
        bootstrap.marker_payload(digest)))
    return environment, digest


def test_repository_root_resolves_from_helper_location(tmp_path):
    helper = tmp_path / "repository with spaces/scripts/bootstrap.py"
    assert bootstrap.repository_root(helper) == tmp_path / "repository with spaces"


def test_dependency_hash_is_deterministic_and_content_sensitive(tmp_path):
    dependency = tmp_path / "pyproject.toml"
    dependency.write_text("[project]\ndependencies=[]\n")
    first = bootstrap.dependency_hash(dependency)
    assert first == bootstrap.dependency_hash(dependency)
    dependency.write_text("[project]\ndependencies=['PyYAML']\n")
    assert first != bootstrap.dependency_hash(dependency)


def test_missing_dependency_definition_has_actionable_error(tmp_path):
    with pytest.raises(bootstrap.BootstrapError, match="missing pyproject.toml"):
        bootstrap.dependency_hash(tmp_path / "pyproject.toml")


def test_fresh_environment_requires_dependency_sync(tmp_path):
    dependency = tmp_path / "pyproject.toml"
    dependency.write_text("[project]\n")
    assert not bootstrap.dependencies_current(
        tmp_path / ".venv", bootstrap.dependency_hash(dependency))


def test_existing_current_environment_skips_sync(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    environment, digest = _current_environment(tmp_path)
    calls = []

    def runner(*args, **kwargs):
        calls.append(args[0])
        return SimpleNamespace(returncode=0)

    assert bootstrap.dependencies_current(environment, digest, run=runner)
    assert len(calls) == 1


def test_changed_dependencies_require_sync(tmp_path):
    dependency = tmp_path / "pyproject.toml"
    dependency.write_text("[project]\n")
    environment, digest = _current_environment(tmp_path)
    dependency.write_text("[project]\ndependencies=['PyYAML']\n")
    changed = bootstrap.dependency_hash(dependency)
    assert changed != digest
    assert not bootstrap.dependencies_current(environment, changed)


def test_corrupt_environment_is_recovered_and_installed(
        tmp_path, monkeypatch):
    root = tmp_path / "repository with spaces"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts/itp.py").write_text("")
    (root / "pyproject.toml").write_text("[project]\n")
    (root / ".venv").mkdir()
    (root / ".venv/orphan").write_text("incomplete")
    progress = []
    commands = []

    class Builder:
        def __init__(self, **kwargs):
            pass

        def create(self, environment):
            python = _python_path(Path(environment))
            python.parent.mkdir(parents=True)
            python.write_text("")

    def runner(command, **kwargs):
        commands.append(command)
        # Import probe before installation fails; pip installation succeeds.
        return SimpleNamespace(returncode=0 if "pip" in command else 1)

    monkeypatch.setattr(bootstrap.venv, "EnvBuilder", Builder)
    python, script = bootstrap.ensure_environment(
        root, run=runner, output=progress.append)

    assert python == _python_path(root / ".venv")
    assert script == root / "scripts/itp.py"
    assert not (root / ".venv/orphan").exists()
    assert any("recovering" in value for value in progress)
    assert any("creating" in value for value in progress)
    assert any("installing" in value for value in progress)
    assert bootstrap.marker_path(root / ".venv").is_file()


def test_missing_venv_support_has_actionable_error(tmp_path, monkeypatch):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/itp.py").write_text("")
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    monkeypatch.setattr(bootstrap, "venv", None)
    with pytest.raises(bootstrap.BootstrapError, match="venv support"):
        bootstrap.ensure_environment(tmp_path, output=lambda value: None)


def test_launch_forwards_arguments_and_exit_code(tmp_path, monkeypatch):
    python = tmp_path / ".venv/bin/python"
    script = tmp_path / "scripts/itp.py"
    calls = []
    monkeypatch.setattr(
        bootstrap, "ensure_environment",
        lambda *args, **kwargs: (python, script))

    def runner(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=17)

    assert bootstrap.launch(
        tmp_path, ["status", "--json"], run=runner) == 17
    assert calls == [[str(python), str(script), "status", "--json"]]


@pytest.mark.parametrize("arguments,expected", [
    (["help"], False),
    (["demo", "--help"], False),
    (["status", "--json"], True),
    (["demo"], True),
    (["setup"], True),
    (["start"], True),
    (["profile", "status", "customer"], True),
    (["connectors", "list"], False),
])
def test_runtime_prerequisite_command_classification(arguments, expected):
    assert bootstrap.command_requires_runtime(arguments) is expected


def test_windows_prerequisites_report_git_without_blocking():
    commands = []

    def which(name):
        return {"git": None, "docker": "C:/Docker/docker.exe"}.get(name)

    def runner(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0)

    result = bootstrap.check_windows_prerequisites(
        ["demo"], which=which, run=runner)
    assert result == {
        "git": False, "docker": True, "compose": True, "daemon": True}
    assert commands == [
        ["C:/Docker/docker.exe", "compose", "version"],
        ["C:/Docker/docker.exe", "info"],
    ]


def test_windows_prerequisites_reject_missing_docker():
    with pytest.raises(bootstrap.BootstrapError, match="Docker was not found"):
        bootstrap.check_windows_prerequisites(
            ["demo"], which=lambda name: None)


def test_windows_prerequisites_reject_missing_compose_plugin():
    def runner(command, **kwargs):
        return SimpleNamespace(returncode=1)

    with pytest.raises(bootstrap.BootstrapError, match="Compose v2"):
        bootstrap.check_windows_prerequisites(
            ["setup"], which=lambda name: "docker.exe"
            if name == "docker" else None, run=runner)


def test_windows_prerequisites_reject_stopped_daemon():
    def runner(command, **kwargs):
        return SimpleNamespace(
            returncode=0 if command[1:3] == ["compose", "version"] else 1)

    with pytest.raises(bootstrap.BootstrapError, match="not running"):
        bootstrap.check_windows_prerequisites(
            ["status"], which=lambda name: "docker.exe"
            if name == "docker" else None, run=runner)


def test_help_does_not_require_docker():
    result = bootstrap.check_windows_prerequisites(
        ["demo", "--help"], which=lambda name: None)
    assert result["docker"] is False


def test_progress_uses_stderr_and_does_not_contaminate_json_stdout(
        tmp_path, monkeypatch, capsys):
    python = tmp_path / ".venv/bin/python"
    script = tmp_path / "scripts/itp.py"

    def ensure(*args, **kwargs):
        kwargs["output"]("ITP bootstrap: ready")
        return python, script

    monkeypatch.setattr(bootstrap, "ensure_environment", ensure)

    def runner(command, **kwargs):
        print('{"status": "ok"}')
        return SimpleNamespace(returncode=0)

    assert bootstrap.launch(
        tmp_path, ["status", "--json"], run=runner,
        output=lambda value: print(value, file=__import__("sys").stderr)) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"status": "ok"}
    assert "ITP bootstrap: ready" in captured.err


def test_windows_first_run_progress_identifies_selected_python(
        tmp_path, monkeypatch):
    progress = []
    monkeypatch.setenv("ITP_BOOTSTRAP_SHOW_PROGRESS", "1")
    monkeypatch.setenv("ITP_BOOTSTRAP_PYTHON_VERSION", "3.12")
    monkeypatch.setenv("ITP_BOOTSTRAP_PYTHON_LABEL", "py -3")
    monkeypatch.setattr(
        bootstrap, "check_windows_prerequisites",
        lambda *args, **kwargs: {})
    monkeypatch.setattr(
        bootstrap, "ensure_environment",
        lambda *args, **kwargs: (
            tmp_path / ".venv/Scripts/python.exe",
            tmp_path / "scripts/itp.py"))
    assert bootstrap.launch(
        tmp_path, ["demo"], output=progress.append,
        run=lambda command, **kwargs: SimpleNamespace(returncode=0)) == 0
    assert progress[:2] == [
        "ITP bootstrap: checking prerequisites",
        "ITP bootstrap: using Python 3.12 via py -3",
    ]


def test_windows_launcher_is_thin_and_preserves_exit_code():
    text = (Path(__file__).resolve().parents[2] / "itp.ps1").read_text()
    assert "$PSScriptRoot" in text
    assert 'Join-Path $Root "scripts\\bootstrap.py"' in text
    assert text.index('Name = "py"') < text.index('Name = "python"')
    assert text.index('Name = "python"') < text.index('Name = "python3"')
    assert 'Prefix = @("-3")' in text
    assert "System.Diagnostics.ProcessStartInfo" in text
    assert "$StartInfo.RedirectStandardError = $true" in text
    assert "$Process.ExitCode -ne 0" in text
    assert "sys.version_info >= (3, 9)" in text
    assert "catch {" in text
    assert "continue" in text
    assert "@args" in text
    assert "$BootstrapExitCode = $LASTEXITCODE" in text
    assert "exit $BootstrapExitCode" in text
    assert "pip install" not in text
    assert "ITP prerequisite check failed: Python was not found." in text
    assert "Windows App Execution Aliases" in text
    assert "Python $MinimumPython or later" in text
    missing_start = text.index(
        'if ($null -eq $Selected) {')
    missing_end = text.index(
        '$env:ITP_BOOTSTRAP_PYTHON_LABEL')
    assert "exit 1" in text[missing_start:missing_end]


def test_unix_launcher_is_location_relative_and_forwards_arguments():
    text = (Path(__file__).resolve().parents[2] / "itp").read_text()
    assert 'dirname -- "$0"' in text
    assert '"$BOOTSTRAP" "$@"' in text
    assert "set -eu" in text
