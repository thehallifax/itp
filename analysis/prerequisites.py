"""Standard-library host prerequisite checks shared by bootstrap and Doctor."""
from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

MINIMUM_PYTHON = (3, 9)
RECOMMENDED_MEMORY_BYTES = 8 * 1024 ** 3
RECOMMENDED_DISK_BYTES = 10 * 1024 ** 3


@dataclass(frozen=True)
class PrerequisiteCheck:
    key: str
    label: str
    status: str
    summary: str
    detail: str = ""
    remediation: str = ""
    metadata: dict = field(default_factory=dict)

    @property
    def blocking(self):
        return self.status == "fail"


@dataclass(frozen=True)
class PrerequisiteReport:
    checks: tuple[PrerequisiteCheck, ...]

    @property
    def ready(self):
        return not any(value.blocking for value in self.checks)


def _find_executable(name, system, *, which=shutil.which, exists=Path.is_file):
    found = which(name)
    if found:
        return str(found)
    candidates = []
    if system == "Windows":
        roots = [os.getenv("ProgramFiles"), os.getenv("ProgramFiles(x86)"),
                 os.getenv("LocalAppData")]
        if name == "git":
            candidates.extend(Path(root) / "Git/cmd/git.exe"
                              for root in roots if root)
        elif name == "docker":
            candidates.extend(Path(root) / "Docker/Docker/resources/bin/docker.exe"
                              for root in roots if root)
    elif system == "Darwin":
        candidates.extend(Path(value) for value in (
            f"/usr/bin/{name}", f"/usr/local/bin/{name}",
            f"/opt/homebrew/bin/{name}"))
    else:
        candidates.extend(Path(value) for value in (
            f"/usr/bin/{name}", f"/usr/local/bin/{name}", f"/snap/bin/{name}"))
    return next((str(value) for value in candidates if exists(value)), None)


def _run(command, runner):
    try:
        return runner(command, text=True, encoding="utf-8", errors="replace",
                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                      check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _memory_bytes(system, runner):
    if system == "Windows":
        command = [
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory",
        ]
        value = _run(command, runner)
        try:
            return int((value.stdout or "").strip()) \
                if value and value.returncode == 0 else None
        except ValueError:
            return None
    if system == "Darwin":
        value = _run(["sysctl", "-n", "hw.memsize"], runner)
        try:
            return int((value.stdout or "").strip()) \
                if value and value.returncode == 0 else None
        except ValueError:
            return None


def _venv_usable():
    try:
        import venv
        with tempfile.TemporaryDirectory(prefix="itp-venv-check-") as directory:
            venv.EnvBuilder(with_pip=False).create(Path(directory) / "venv")
        return True
    except (ImportError, OSError, subprocess.SubprocessError):
        return False
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, OSError, ValueError):
        return None


def _port_owner(port, system, runner):
    if system == "Windows":
        command = [
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            (f"$c=Get-NetTCPConnection -State Listen -LocalPort {int(port)} "
             "-ErrorAction SilentlyContinue | Select-Object -First 1; "
             "if($c){$p=Get-Process -Id $c.OwningProcess "
             "-ErrorAction SilentlyContinue; Write-Output "
             "($c.OwningProcess.ToString()+'|'+$p.ProcessName)}"),
        ]
    else:
        command = ["lsof", "-nP", f"-iTCP:{int(port)}", "-sTCP:LISTEN", "-Fpct"]
    result = _run(command, runner)
    text = (getattr(result, "stdout", "") or "").strip() if result else ""
    if system == "Windows" and "|" in text:
        pid, process = text.split("|", 1)
        return process or "unknown", pid or "unknown"
    if system != "Windows" and text:
        pid = process = "unknown"
        for line in text.splitlines():
            if line.startswith("p"): pid = line[1:]
            if line.startswith("c"): process = line[1:]
        return process, pid
    return "unknown", "unknown"


def _port_in_use(port):
    with socket.socket() as connection:
        connection.settimeout(0.2)
        return connection.connect_ex(("127.0.0.1", int(port))) == 0


def evaluate_prerequisites(root, *, ports=(3000, 8181), system=None,
                           version=None, machine=None, which=shutil.which,
                           runner=subprocess.run, port_in_use=_port_in_use,
                           disk_usage=shutil.disk_usage, memory_fn=None,
                           exists=Path.is_file, check_docker_volume=True,
                           check_services=True, check_ports=True,
                           access=os.access, venv_fn=_venv_usable):
    """Return deterministic host readiness without writing deployment files."""
    root = Path(root).resolve()
    system = system or platform.system()
    version = version or sys.version_info
    machine = machine or platform.machine()
    checks = []

    supported_os = system in {"Windows", "Darwin", "Linux"}
    checks.append(PrerequisiteCheck(
        "os", "Operating System", "pass" if supported_os else "fail",
        f"{system} is supported" if supported_os else f"{system} is unsupported",
        remediation="Use Windows 11, a supported macOS release, or Linux."))

    python_ok = tuple(version[:2]) >= MINIMUM_PYTHON
    python_label = ".".join(str(value) for value in version[:3])
    checks.append(PrerequisiteCheck(
        "python", "Supported Python", "pass" if python_ok else "fail",
        f"Python {python_label} ({machine}) is usable" if python_ok else
        f"Python {python_label} is unsupported",
        remediation="Install Python 3.9 or later from https://www.python.org/downloads/."))
    venv_ok = bool(venv_fn())
    checks.append(PrerequisiteCheck(
        "venv", "Python virtual environment", "pass" if venv_ok else "fail",
        "Python venv support is available" if venv_ok else
        "Python venv support is unavailable",
        remediation="Install the venv component for the selected Python release."))
    pip = _run([sys.executable, "-m", "pip", "--version"], runner)
    pip_ok = bool(pip and pip.returncode == 0)
    checks.append(PrerequisiteCheck(
        "pip", "Python package installer", "pass" if pip_ok else "fail",
        "pip is usable" if pip_ok else "pip could not run",
        remediation="Repair pip for the selected Python interpreter."))

    git = _find_executable("git", system, which=which, exists=exists)
    checks.append(PrerequisiteCheck(
        "git", "Git", "pass" if git else "fail",
        "Git is available" if git else
        ("Git for Windows is not installed" if system == "Windows" else
         "Git is not installed"),
        detail=git or "", remediation=(
            "Git is required for updates, version reporting and deployment "
            "lifecycle management. Install it from https://git-scm.com/downloads, "
            "then rerun the ITP deploy command.")))

    docker = _find_executable("docker", system, which=which, exists=exists)
    compose_ok = daemon_ok = linux_mode = False
    if docker and check_services:
        compose = _run([docker, "compose", "version"], runner)
        compose_ok = bool(compose and compose.returncode == 0)
        daemon = _run([docker, "info"], runner)
        daemon_ok = bool(daemon and daemon.returncode == 0)
        if daemon_ok and system == "Windows":
            mode = _run([docker, "info", "--format", "{{.OSType}}"], runner)
            linux_mode = bool(mode and mode.returncode == 0 and
                              (mode.stdout or "").strip().casefold() == "linux")
        else:
            linux_mode = daemon_ok
    if check_services:
        checks.append(PrerequisiteCheck(
            "docker", "Docker CLI", "pass" if docker else "fail",
            "Docker CLI is available" if docker else "Docker is not installed",
            detail=docker or "", remediation=(
                "Install Docker Desktop from https://www.docker.com/products/docker-desktop/.")))
        checks.append(PrerequisiteCheck(
            "docker_daemon", "Docker daemon", "pass" if daemon_ok else "fail",
            "Docker daemon is running" if daemon_ok else
            "Docker is installed but its daemon is not running",
            remediation="Start Docker Desktop or the Docker service, then retry."))
        checks.append(PrerequisiteCheck(
            "compose", "Docker Compose v2", "pass" if compose_ok else "fail",
            "Docker Compose v2 is available" if compose_ok else
            "Docker Compose v2 is unavailable",
            remediation="Install or update Docker Compose v2, then verify `docker compose version`."))
    if system == "Windows" and check_services:
        checks.append(PrerequisiteCheck(
            "linux_containers", "Linux container mode",
            "pass" if linux_mode else "fail",
            "Docker is using Linux containers" if linux_mode else
            "Docker is running Windows containers or its mode is unavailable",
            remediation="Switch Docker Desktop to Linux containers, then retry."))
        windows_ps = _find_executable("powershell.exe", system, which=which,
                                      exists=exists)
        pwsh = _find_executable("pwsh", system, which=which, exists=exists)
        checks.append(PrerequisiteCheck(
            "powershell", "Windows PowerShell", "pass" if windows_ps else "fail",
            "Windows PowerShell is available" if windows_ps else
            "Windows PowerShell is unavailable",
            detail=("PowerShell 7 also detected" if pwsh else
                    "PowerShell 7 is optional"),
            remediation="Enable Windows PowerShell; PowerShell 7 is optional."))

    runtime = root / "runtime"
    permission_targets = [root, runtime if runtime.exists() else root]
    permission_ok = all(access(value, os.W_OK) for value in permission_targets)
    checks.append(PrerequisiteCheck(
        "permissions", "Runtime write permissions",
        "pass" if permission_ok else "fail",
        "Runtime and deployment locations are writable" if permission_ok else
        "Runtime or deployment location is not writable",
        detail=", ".join(str(value) for value in permission_targets),
        remediation="Grant the current user write access to the repository runtime directory."))

    volume_ok = daemon_ok
    if daemon_ok and check_docker_volume and check_services:
        volume = f"itp-prerequisite-{os.getpid()}"
        created = _run([docker, "volume", "create", volume], runner)
        volume_ok = bool(created and created.returncode == 0)
        if volume_ok:
            _run([docker, "volume", "rm", volume], runner)
    if check_services:
        checks.append(PrerequisiteCheck(
            "docker_volume", "Docker volume access",
            "pass" if volume_ok else "fail",
            "Docker volumes can be created" if volume_ok else
            "Docker volume creation is unavailable",
            remediation="Check Docker Desktop permissions and available storage."))

    available_disk = disk_usage(root).free
    checks.append(PrerequisiteCheck(
        "disk", "Available disk space",
        "pass" if available_disk >= RECOMMENDED_DISK_BYTES else "warn",
        f"{available_disk / 1024 ** 3:.1f} GiB available",
        remediation="Free at least 10 GiB for images, volumes and telemetry."))
    memory = memory_fn(system) if memory_fn else _memory_bytes(system, runner)
    checks.append(PrerequisiteCheck(
        "memory", "Available memory",
        "pass" if memory is None or memory >= RECOMMENDED_MEMORY_BYTES else "warn",
        "Memory could not be measured" if memory is None else
        f"{memory / 1024 ** 3:.1f} GiB installed",
        remediation="8 GiB or more is recommended for the local platform stack."))

    if check_ports:
        collisions = []
        for port in ports:
            if port_in_use(int(port)):
                process, pid = _port_owner(port, system, runner)
                collisions.append((int(port), process, pid))
        checks.append(PrerequisiteCheck(
            "ports", "Required ports", "fail" if collisions else "pass",
            "Required ports are available" if not collisions else
            "One or more required ports are already in use",
            detail="; ".join(f"port {port}: {process} (PID {pid})"
                             for port, process, pid in collisions),
            remediation="Stop the conflicting process or choose another port."))
    return PrerequisiteReport(tuple(checks))


def render_prerequisites(report):
    lines = ["=" * 49, "Infrastructure Telemetry Platform", "",
             "Checking prerequisites...", ""]
    labels = {"pass": "PASS", "warn": "WARNING", "fail": "FAIL"}
    for check in report.checks:
        lines.append(f"[{labels[check.status]}] {check.label} - {check.summary}")
        if check.detail and check.status != "pass":
            lines.append(f"       {check.detail}")
        if check.status != "pass" and check.remediation:
            lines.append(f"       Action: {check.remediation}")
    lines.extend(("", "All prerequisites satisfied." if report.ready else
                  "Deployment cannot continue until failed prerequisites are corrected.",
                  "=" * 49))
    return "\n".join(lines)
