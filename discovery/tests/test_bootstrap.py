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


def test_supported_python_minimum_is_deliberate():
    assert bootstrap.supported_python((3, 9, 0))
    assert bootstrap.supported_python((3, 13, 1))
    assert not bootstrap.supported_python((3, 8, 20))


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


def test_development_environment_requires_pytest_and_satisfies_runtime(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    environment, digest = _current_environment(tmp_path)

    def runner(command, **kwargs):
        return SimpleNamespace(returncode=0)

    assert not bootstrap.dependencies_current(
        environment, digest, development=True, run=runner)
    bootstrap.marker_path(environment).write_text(json.dumps(
        bootstrap.marker_payload(digest, development=True)))
    assert bootstrap.dependencies_current(
        environment, digest, development=True, run=runner)
    assert bootstrap.dependencies_current(environment, digest, run=runner)


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
    pip_commands = [command for command in commands if "pip" in command]
    assert len(pip_commands) == 2
    assert all("--disable-pip-version-check" in command
               for command in pip_commands)
    assert all("--quiet" in command for command in pip_commands)


def test_developer_bootstrap_installs_declared_dev_extra(tmp_path, monkeypatch):
    root = tmp_path / "repository"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts/itp.py").write_text("")
    (root / "pyproject.toml").write_text("[project]\n")
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
        return SimpleNamespace(returncode=0 if "pip" in command else 1)

    monkeypatch.setattr(bootstrap.venv, "EnvBuilder", Builder)
    bootstrap.ensure_environment(
        root, development=True, run=runner, output=lambda value: None)

    install = [
        command for command in commands
        if "pip" in command and "--upgrade" not in command][0]
    assert install[-1] == f"{root}[dev]"
    marker = bootstrap.read_marker(root / ".venv")
    assert marker["dependency_group"] == "development"


def test_missing_venv_support_has_actionable_error(tmp_path, monkeypatch):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/itp.py").write_text("")
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    monkeypatch.setattr(bootstrap, "venv", None)
    with pytest.raises(bootstrap.BootstrapError, match="venv support"):
        bootstrap.ensure_environment(tmp_path, output=lambda value: None)


def test_dependency_failure_prints_captured_pip_output(
        tmp_path, monkeypatch, capsys):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/itp.py").write_text("")
    (tmp_path / "pyproject.toml").write_text("[project]\n")

    class Builder:
        def __init__(self, **kwargs):
            pass

        def create(self, environment):
            python = _python_path(Path(environment))
            python.parent.mkdir(parents=True)
            python.write_text("")

    def runner(command, **kwargs):
        if "-c" in command:
            return SimpleNamespace(returncode=1, stdout="")
        if "--upgrade" in command:
            return SimpleNamespace(returncode=0, stdout="toolchain detail\n")
        return SimpleNamespace(
            returncode=1, stdout="actionable package-index failure\n")

    monkeypatch.setattr(bootstrap.venv, "EnvBuilder", Builder)
    with pytest.raises(
            bootstrap.BootstrapError, match="dependency installation failed"):
        bootstrap.ensure_environment(
            tmp_path, run=runner, output=lambda value: None)
    error = capsys.readouterr().err
    assert "toolchain detail" in error
    assert "actionable package-index failure" in error


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
        tmp_path, ["status", "--json"], run=runner,
        prerequisite_fn=lambda *args, **kwargs: {}) == 17
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

    with pytest.raises(bootstrap.BootstrapError, match="daemon is unavailable"):
        bootstrap.check_windows_prerequisites(
            ["status"], which=lambda name: "docker.exe"
            if name == "docker" else None, run=runner)


def test_windows_virtualization_diagnostic_parsing():
    system = bootstrap.parse_windows_systeminfo(
        "Hyper-V Requirements:\n"
        "    Virtualization Enabled In Firmware: No\n")
    assert system["virtualization_enabled"] is False
    assert system["hypervisor_detected"] is False
    features = bootstrap.parse_windows_optional_features(json.dumps([
        {"FeatureName": "VirtualMachinePlatform", "State": "Disabled"},
        {"FeatureName": "HypervisorPlatform", "State": "Enabled"},
    ]))
    assert features == {
        "VirtualMachinePlatform": "Disabled",
        "HypervisorPlatform": "Enabled",
    }
    guidance = bootstrap.windows_docker_guidance({
        **system, "features": features, "hypervisor_launch": "off",
        "reboot_pending": True,
    })
    assert "AMD SVM / AMD-V or Intel VT-x" in guidance
    assert "Virtual Machine Platform" in guidance
    assert "boot configuration" in guidance
    assert "pending reboot" in guidance


def test_windows_optional_diagnostics_tolerate_unavailable_commands():
    result = bootstrap.windows_virtualization_diagnostics(
        which=lambda name: None,
        run=lambda *args, **kwargs: pytest.fail("runner should not be called"))
    assert result == {
        "virtualization_enabled": None, "hypervisor_detected": False,
        "features": {}, "hypervisor_launch": None, "reboot_pending": None,
    }


def test_windows_diagnostics_collect_available_read_only_evidence():
    paths = {
        "systeminfo": "systeminfo.exe",
        "powershell.exe": "powershell.exe",
        "bcdedit": "bcdedit.exe",
        "reg": "reg.exe",
    }

    def runner(command, **kwargs):
        if command[0] == "systeminfo.exe":
            return SimpleNamespace(
                returncode=0,
                stdout="Virtualization Enabled In Firmware: No")
        if command[0] == "powershell.exe":
            return SimpleNamespace(returncode=0, stdout=json.dumps({
                "FeatureName": "VirtualMachinePlatform",
                "State": "Disabled",
            }))
        if command[0] == "bcdedit.exe":
            return SimpleNamespace(
                returncode=0, stdout="hypervisorlaunchtype    Off")
        return SimpleNamespace(returncode=0, stdout="")

    result = bootstrap.windows_virtualization_diagnostics(
        which=paths.get, run=runner)
    assert result["virtualization_enabled"] is False
    assert result["features"]["VirtualMachinePlatform"] == "Disabled"
    assert result["hypervisor_launch"] == "off"
    assert result["reboot_pending"] is True


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
        output=lambda value: print(value, file=__import__("sys").stderr),
        prerequisite_fn=lambda *args, **kwargs: {}) == 0
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
        run=lambda command, **kwargs: SimpleNamespace(returncode=0),
        prerequisite_fn=lambda *args, **kwargs: {
            "docker": True, "compose": True}) == 0
    assert progress[:2] == [
        "ITP bootstrap: checking prerequisites",
        "✓ Python 3.12 (py -3)",
    ]
    assert progress[2:4] == ["✓ Docker", "✓ Docker Compose"]


def test_main_strips_bootstrap_verbose_before_launch(monkeypatch):
    captured = {}

    def launcher(root, arguments, verbose=False):
        captured.update(arguments=arguments, verbose=verbose)
        return 0

    monkeypatch.setattr(bootstrap, "launch", launcher)
    assert bootstrap.main(["--verbose", "demo", "--help"]) == 0
    assert captured == {
        "arguments": ["demo", "--help"], "verbose": True}


def test_windows_launcher_is_thin_and_preserves_exit_code():
    root = Path(__file__).resolve().parents[2]
    text = (root / "itp.ps1").read_text()
    helper = (root / "scripts/windows-bootstrap.ps1").read_text()
    assert "$PSScriptRoot" in text
    assert 'Join-Path $Root "scripts\\bootstrap.py"' in text
    assert 'Join-Path $Root "scripts\\windows-bootstrap.ps1"' in text
    assert text.index(". $WindowsBootstrap") < text.index("Initialize-ITPPython")
    assert "@args" in text
    assert "$BootstrapExitCode = $LASTEXITCODE" in text
    assert "exit $BootstrapExitCode" in text
    assert "pip install" not in text
    assert helper.index('Name = "py"') < helper.index('Name = "python"')
    assert helper.index('Name = "python"') < helper.index('Name = "python3"')
    assert 'Prefix = @("-3")' in helper
    assert "System.Diagnostics.ProcessStartInfo" in helper
    assert "$StartInfo.RedirectStandardError = $true" in helper
    assert "$Process.ExitCode -ne 0" in helper
    assert "Python.Python.3.12" in helper
    assert '"--exact", "--source", "winget"' in helper
    assert '"--accept-package-agreements", "--accept-source-agreements"' in helper
    assert 'GetEnvironmentVariable("Path", $Scope)' in helper
    assert "IsInputRedirected" in helper
    assert "python-3.12.10-amd64.exe" in helper
    assert "python-3.12.10-arm64.exe" in helper
    assert "67b5635e80ea51072b87941312d00ec8927c4db9ba18938f7ad2d27b328b95fb" in helper
    assert "377ac8fd478987940088e879441e702a71b53164d2a1e6f1d51ff77a7e470258" in helper
    assert "Get-AuthenticodeSignature" in helper
    assert "Python Software Foundation" in helper
    assert "Invoke-Expression" not in helper
    assert "InstallAllUsers=0" in helper
    assert "Include_pip=1" in helper
    assert "MaximumRedirection" in helper
    assert "www.python.org" in helper
    assert "Invoke-ITPPrerequisiteDiagnostics" in text
    assert text.index("Invoke-ITPPrerequisiteDiagnostics") < \
        text.index("Initialize-ITPPython")
    assert "Locations checked" in helper


def test_windows_bootstrap_has_cross_platform_reviewable_harness():
    root = Path(__file__).resolve().parents[2]
    harness = (root / "scripts/Test-WindowsBootstrap.ps1").read_text()
    for scenario in (
            "py must take precedence",
            "python fallback must be selected",
            "unsupported Python must not be selected",
            "declined direct installation must stop",
            "direct provider fallback",
            "non-interactive execution must not prompt or install",
            "PATH refresh scopes",
            "cross-host redirect must be rejected",
            "invalid installer verification must block",
            "success cleanup",
            "installer exit handling",
            "launcher must forward all arguments",
            "missing Git and Docker must block"):
        assert scenario in harness


def test_production_powershell_sources_are_windows_51_ascii_safe():
    root = Path(__file__).resolve().parents[2]
    paths = (
        root / "itp.ps1",
        root / "scripts/windows-bootstrap.ps1",
        root / "scripts/Install-ITP.ps1",
        root / "scripts/Update-ITP.ps1",
        root / "collectors/hyperv/Collect-ITPHyperV.ps1",
    )
    for path in paths:
        data = path.read_bytes()
        assert data.isascii(), (
            f"{path.relative_to(root)} contains non-ASCII source bytes")
        assert data.decode("ascii") == data.decode("cp1252")


def test_unix_launcher_is_location_relative_and_forwards_arguments():
    text = (Path(__file__).resolve().parents[2] / "itp").read_text()
    assert 'dirname -- "$0"' in text
    assert '"$BOOTSTRAP" "$@"' in text
    assert "set -eu" in text
    assert "scripts/itp.py" not in text
    assert "ITP_BOOTSTRAP_SHOW_PROGRESS" in text
    assert "ITP_BOOTSTRAP_PYTHON_VERSION" in text
