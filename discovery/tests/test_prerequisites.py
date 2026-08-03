from types import SimpleNamespace

from analysis.prerequisites import evaluate_prerequisites, render_prerequisites


def result(report, key):
    return next(value for value in report.checks if value.key == key)


def runner_for(*, compose=0, daemon=0, linux="linux", pip=0, volume=0,
               lsof=""):
    def run(command, **_kwargs):
        text = ""
        code = 0
        if command[1:3] == ["-m", "pip"]:
            code = pip
        elif command[1:3] == ["compose", "version"]:
            code = compose
        elif command[1:] == ["info"]:
            code = daemon
        elif command[1:3] == ["info", "--format"]:
            text = linux
        elif command[1:3] == ["volume", "create"]:
            code = volume
        elif command[0] == "lsof":
            text = lsof
        return SimpleNamespace(returncode=code, stdout=text)
    return run


def kwargs(tmp_path, **values):
    defaults = {
        "system": "Linux", "version": (3, 12, 1), "machine": "x86_64",
        "which": lambda name: f"/usr/bin/{name}" if name in {"git", "docker"} else None,
        "runner": runner_for(), "port_in_use": lambda _port: False,
        "disk_usage": lambda _path: SimpleNamespace(free=20 * 1024 ** 3),
        "memory_fn": lambda _system: 16 * 1024 ** 3,
        "exists": lambda _path: False,
        "venv_fn": lambda: True,
    }
    defaults.update(values)
    return evaluate_prerequisites(tmp_path, **defaults)


def test_git_detection_and_missing_git_are_actionable(tmp_path):
    present = kwargs(tmp_path)
    assert result(present, "git").status == "pass"
    missing = kwargs(tmp_path, which=lambda name: "/usr/bin/docker"
                     if name == "docker" else None)
    check = result(missing, "git")
    assert check.status == "fail"
    assert "https://git-scm.com/downloads" in check.remediation


def test_common_windows_git_location_is_detected(tmp_path, monkeypatch):
    monkeypatch.setenv("ProgramFiles", "C:/Program Files")
    report = kwargs(
        tmp_path, system="Windows", which=lambda name: {
            "docker": "C:/Docker/docker.exe",
            "powershell.exe": "C:/Windows/powershell.exe",
        }.get(name), exists=lambda path: str(path).endswith("Git/cmd/git.exe"),
        runner=runner_for(linux="linux"))
    assert result(report, "git").status == "pass"


def test_missing_docker_and_stopped_daemon_are_distinct(tmp_path):
    missing = kwargs(tmp_path, which=lambda name: "/usr/bin/git"
                     if name == "git" else None)
    assert result(missing, "docker").summary == "Docker is not installed"
    stopped = kwargs(tmp_path, runner=runner_for(daemon=1))
    assert result(stopped, "docker").status == "pass"
    assert result(stopped, "docker_daemon").summary == (
        "Docker is installed but its daemon is not running")


def test_unsupported_python_is_blocking(tmp_path):
    report = kwargs(tmp_path, version=(3, 8, 18))
    assert result(report, "python").status == "fail"
    assert not report.ready


def test_port_collision_includes_process_and_pid(tmp_path):
    report = kwargs(
        tmp_path, port_in_use=lambda port: port == 3000,
        runner=runner_for(lsof="p4184\ncgrafana-server\n"))
    check = result(report, "ports")
    assert check.status == "fail"
    assert "port 3000: grafana-server (PID 4184)" in check.detail


def test_runtime_permission_failure_is_blocking(tmp_path):
    report = kwargs(tmp_path, access=lambda _path, _mode: False)
    assert result(report, "permissions").status == "fail"
    assert not report.ready


def test_low_memory_and_disk_warn_without_blocking(tmp_path):
    report = kwargs(
        tmp_path,
        disk_usage=lambda _path: SimpleNamespace(free=2 * 1024 ** 3),
        memory_fn=lambda _system: 4 * 1024 ** 3)
    assert result(report, "disk").status == "warn"
    assert result(report, "memory").status == "warn"
    assert report.ready
    rendered = render_prerequisites(report)
    assert "[WARNING] Available disk space" in rendered
    assert "All prerequisites satisfied" in rendered
