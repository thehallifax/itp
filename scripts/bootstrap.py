#!/usr/bin/env python3
"""Standard-library-only self-bootstrap for the ITP command."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import venv
except ImportError:  # pragma: no cover - platform packaging failure
    venv = None


MINIMUM_PYTHON = (3, 9)
BOOTSTRAP_SCHEMA = 1
MARKER_NAME = ".itp-dependencies.json"
DEPENDENCY_FILE = "pyproject.toml"
WINDOWS_RUNTIME_COMMANDS = frozenset({
    "demo", "setup", "provision", "start", "stop", "restart", "status", "logs",
})
WINDOWS_FEATURES = (
    "VirtualMachinePlatform",
    "Microsoft-Hyper-V-All",
    "HypervisorPlatform",
)


class BootstrapError(RuntimeError):
    pass


def supported_python(version):
    return tuple(version[:2]) >= MINIMUM_PYTHON


def command_requires_runtime(arguments):
    arguments = list(arguments)
    if not arguments or any(value in {"-h", "--help"} for value in arguments):
        return False
    if arguments[0] in WINDOWS_RUNTIME_COMMANDS:
        return True
    return len(arguments) > 1 and arguments[0] == "profile" and \
        arguments[1] in {"up", "down", "restart", "status", "logs"}


def parse_windows_systeminfo(text):
    text = str(text or "").casefold()
    return {
        "virtualization_enabled": (
            False if "virtualization enabled in firmware:" in text
            and "virtualization enabled in firmware: no" in text
            else True if "virtualization enabled in firmware: yes" in text
            else None),
        "hypervisor_detected": "a hypervisor has been detected" in text,
    }


def parse_windows_optional_features(text):
    try:
        value = json.loads(str(text or ""))
    except (TypeError, json.JSONDecodeError):
        return {}
    values = value if isinstance(value, list) else [value]
    return {
        str(item.get("FeatureName")): str(item.get("State"))
        for item in values if isinstance(item, dict) and item.get("FeatureName")
    }


def windows_virtualization_diagnostics(*, which=shutil.which, run=subprocess.run):
    """Best-effort, read-only diagnostics for a stopped Docker Desktop."""
    result = {
        "virtualization_enabled": None, "hypervisor_detected": False,
        "features": {}, "hypervisor_launch": None, "reboot_pending": None,
    }

    def captured(command):
        try:
            value = run(
                command, text=True, encoding="utf-8", errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, check=False)
            return getattr(value, "stdout", "") if value.returncode == 0 else ""
        except OSError:
            return ""

    systeminfo = which("systeminfo")
    if systeminfo:
        result.update(parse_windows_systeminfo(captured([systeminfo])))
    powershell = which("powershell.exe") or which("powershell")
    if powershell:
        names = ",".join(f"'{name}'" for name in WINDOWS_FEATURES)
        script = (
            f"@({names}) | ForEach-Object {{ "
            "Get-WindowsOptionalFeature -Online -FeatureName $_ "
            "-ErrorAction SilentlyContinue } | "
            "Select-Object FeatureName,State | ConvertTo-Json -Compress")
        result["features"] = parse_windows_optional_features(captured([
            powershell, "-NoProfile", "-NonInteractive", "-Command", script]))
    bcdedit = which("bcdedit")
    if bcdedit:
        boot = captured([bcdedit, "/enum", "{current}"]).casefold()
        if "hypervisorlaunchtype" in boot:
            result["hypervisor_launch"] = (
                "off" if re.search(
                    r"hypervisorlaunchtype\s+off", boot) else "enabled")
    reg = which("reg")
    if reg:
        try:
            pending = run([
                reg, "query",
                r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion"
                r"\Component Based Servicing\RebootPending",
            ], text=True, encoding="utf-8", errors="replace",
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False)
            result["reboot_pending"] = pending.returncode == 0
        except OSError:
            pass
    return result


def windows_docker_guidance(diagnostics):
    lines = []
    if diagnostics.get("virtualization_enabled") is False:
        lines.extend((
            "Docker Desktop cannot start because hardware virtualization is disabled.",
            "Enable AMD SVM / AMD-V or Intel VT-x in firmware, then reboot.",
        ))
    disabled = [
        name for name, state in diagnostics.get("features", {}).items()
        if state.casefold() in {"disabled", "disable pending"}]
    if disabled:
        labels = {
            "VirtualMachinePlatform": "Virtual Machine Platform",
            "Microsoft-Hyper-V-All": "Hyper-V (where supported)",
            "HypervisorPlatform": "Windows Hypervisor Platform",
        }
        lines.append(
            "Enable the required Windows feature(s): "
            + ", ".join(labels.get(name, name) for name in sorted(disabled))
            + ".")
    if diagnostics.get("hypervisor_launch") == "off":
        lines.append(
            "The Windows hypervisor is disabled in boot configuration; review "
            "`bcdedit /enum {current}` with an administrator.")
    if diagnostics.get("reboot_pending"):
        lines.append(
            "Windows reports a pending reboot; restart Windows before retrying.")
    if not lines:
        lines.append(
            "Start Docker Desktop, wait until it reports that the engine is "
            "running, then retry.")
    return " ".join(lines)


def check_windows_prerequisites(
        arguments, *, which=shutil.which, run=subprocess.run):
    """Check stack prerequisites without importing any third-party package."""
    result = {
        "git": bool(which("git")), "docker": False,
        "compose": False, "daemon": False,
    }
    if not command_requires_runtime(arguments):
        return result
    docker = which("docker")
    if not docker:
        raise BootstrapError(
            "Docker was not found. Install Docker Desktop for Windows from "
            "https://www.docker.com/products/docker-desktop/, then open a new "
            "PowerShell window and rerun the ITP command")
    result["docker"] = True
    try:
        compose = run(
            [docker, "compose", "version"], text=True,
            encoding="utf-8", errors="replace",
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    except OSError as exc:
        raise BootstrapError(
            "Docker could not be started. Repair Docker Desktop and rerun the "
            "ITP command") from exc
    if compose.returncode != 0:
        raise BootstrapError(
            "Docker Compose v2 is unavailable. Update Docker Desktop and verify "
            "`docker compose version`, then rerun the ITP command")
    result["compose"] = True
    try:
        daemon = run(
            [docker, "info"], text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, check=False)
    except OSError as exc:
        raise BootstrapError(
            "Docker Desktop could not be contacted. Start Docker Desktop and "
            "rerun the ITP command") from exc
    if daemon.returncode != 0:
        diagnostics = windows_virtualization_diagnostics(
            which=which, run=run)
        raise BootstrapError(
            "Docker is installed but its daemon is unavailable. "
            + windows_docker_guidance(diagnostics))
    result["daemon"] = True
    return result


def check_runtime_prerequisites(
        arguments, *, platform=None, which=shutil.which, run=subprocess.run):
    platform = platform or sys.platform
    if platform == "win32":
        return check_windows_prerequisites(
            arguments, which=which, run=run)
    result = {
        "git": bool(which("git")), "docker": False,
        "compose": False, "daemon": False,
    }
    if not command_requires_runtime(arguments):
        return result
    docker = which("docker")
    if not docker:
        raise BootstrapError(
            "Docker was not found. Install Docker Desktop on macOS or Docker "
            "Engine on Linux, then rerun the ITP command")
    result["docker"] = True
    compose = run(
        [docker, "compose", "version"], text=True,
        encoding="utf-8", errors="replace",
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    if compose.returncode != 0:
        raise BootstrapError(
            "Docker Compose v2 is unavailable; install the Compose plugin and "
            "verify `docker compose version`")
    result["compose"] = True
    daemon = run(
        [docker, "info"], text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, check=False)
    if daemon.returncode != 0:
        raise BootstrapError(
            "Docker is installed but its daemon is unavailable. Start Docker "
            "Desktop on macOS or the Docker service on Linux, then retry")
    result["daemon"] = True
    return result


def repository_root(source=__file__):
    return Path(source).resolve().parents[1]


def dependency_hash(path):
    path = Path(path)
    if not path.is_file():
        raise BootstrapError(
            f"missing {path.name}; restore the repository and rerun the ITP command")
    digest = hashlib.sha256()
    digest.update(f"itp-bootstrap-schema:{BOOTSTRAP_SCHEMA}\0".encode())
    try:
        digest.update(path.read_bytes())
    except OSError as exc:
        raise BootstrapError(
            f"could not read {path.name}; correct its permissions and rerun "
            "the ITP command") from exc
    return digest.hexdigest()


def environment_python(environment):
    environment = Path(environment)
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def marker_path(environment):
    return Path(environment) / MARKER_NAME


def marker_payload(digest, version=None, *, development=False):
    version = version or sys.version_info
    return {
        "bootstrap_schema": BOOTSTRAP_SCHEMA,
        "dependency_group": "development" if development else "runtime",
        "dependency_file": DEPENDENCY_FILE,
        "dependency_hash": digest,
        "python": f"{version.major}.{version.minor}",
    }


def read_marker(environment):
    try:
        value = json.loads(marker_path(environment).read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def dependencies_current(
        environment, digest, *, development=False, run=subprocess.run):
    python = environment_python(environment)
    actual = read_marker(environment)
    expected = marker_payload(digest, development=development)
    compatible_groups = (
        {"development"} if development else {"runtime", "development"})
    if not python.is_file() or any((
            actual.get("bootstrap_schema") != expected["bootstrap_schema"],
            actual.get("dependency_file") != expected["dependency_file"],
            actual.get("dependency_hash") != expected["dependency_hash"],
            actual.get("python") != expected["python"],
            actual.get("dependency_group") not in compatible_groups)):
        return False
    imports = "import httpx, yaml, pysnmp"
    if development:
        imports += ", pytest"
    check = run(
        [str(python), "-c", imports],
        text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        check=False)
    return check.returncode == 0


def _safe_remove_environment(environment, root):
    environment = Path(environment).resolve()
    expected = (Path(root).resolve() / ".venv").resolve()
    if environment != expected:
        raise BootstrapError("refusing to replace a virtual environment outside .venv")
    shutil.rmtree(environment)


def ensure_environment(
        root, *, development=False, run=subprocess.run, output=None,
        verbose=False):
    root = Path(root).resolve()
    dependency = root / DEPENDENCY_FILE
    script = root / "scripts/itp.py"
    environment = root / ".venv"
    output = output or (lambda message: print(message, file=sys.stderr))

    if not supported_python(sys.version_info):
        raise BootstrapError(
            "Python 3.9 or later is required; install a supported release")
    if not script.is_file():
        raise BootstrapError(
            "missing scripts/itp.py; restore the repository and rerun the ITP command")
    digest = dependency_hash(dependency)
    python = environment_python(environment)

    if environment.exists() and not python.is_file():
        output("ITP bootstrap: recovering incomplete Python environment")
        try:
            _safe_remove_environment(environment, root)
        except OSError as exc:
            raise BootstrapError(
                "could not remove the incomplete .venv; close processes using it "
                "and rerun the ITP command") from exc

    created = False
    if not environment.exists():
        output("ITP bootstrap: creating Python environment")
        if venv is None:
            raise BootstrapError(
                "Python venv support is unavailable; install the venv package "
                "for this Python release and rerun the ITP command")
        try:
            venv.EnvBuilder(with_pip=True, clear=False).create(environment)
        except Exception as exc:
            raise BootstrapError(
                "could not create .venv. Install Python with venv support, remove "
                "any incomplete .venv, and rerun the ITP command") from exc
        created = True

    if not python.is_file():
        raise BootstrapError(
            "the virtual environment is incomplete; remove .venv and rerun the "
            "ITP command")

    if not dependencies_current(
            environment, digest, development=development, run=run):
        dependency_label = (
            "development dependencies" if development else "dependencies")
        output(f"ITP bootstrap: installing {dependency_label}")
        environment_variables = {
            **os.environ,
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_PROGRESS_BAR": "off",
        }
        toolchain_output = ""
        if created:
            toolchain = run([
                str(python), "-m", "pip", "install",
                "--disable-pip-version-check", "--quiet", "--upgrade",
                "pip>=23.1", "setuptools>=68", "wheel",
            ], cwd=root, env=environment_variables, text=True,
                encoding="utf-8", errors="replace",
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
            toolchain_output = getattr(toolchain, "stdout", "") or ""
        try:
            install_arguments = [
                str(python), "-m", "pip", "install",
                "--disable-pip-version-check",
            ]
            if not verbose:
                install_arguments.append("--quiet")
            project_requirement = str(root)
            if development:
                project_requirement += "[dev]"
            install_arguments.append(project_requirement)
            result = run(
                install_arguments, cwd=root, env=environment_variables,
                text=True, encoding="utf-8", errors="replace",
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                check=False)
        except OSError as exc:
            raise BootstrapError(
                "could not start pip from .venv; remove .venv and rerun the "
                "ITP command") from exc
        if result.returncode != 0:
            captured = toolchain_output + (getattr(result, "stdout", "") or "")
            if captured:
                print(captured.rstrip(), file=sys.stderr)
            raise BootstrapError(
                "dependency installation failed. Check network or package-index "
                "access, then rerun the ITP command. For offline use, make the "
                "required packages available in pip's cache or an internal index")
        if verbose:
            captured = toolchain_output + (getattr(result, "stdout", "") or "")
            if captured:
                print(captured.rstrip(), file=sys.stderr)
        try:
            marker_path(environment).write_text(
                json.dumps(
                    marker_payload(digest, development=development),
                    indent=2, sort_keys=True) + "\n")
        except OSError as exc:
            raise BootstrapError(
                "dependencies installed but bootstrap state could not be written "
                "inside .venv; correct repository permissions and rerun the "
                "ITP command") from exc
        output("ITP bootstrap: ready")
    elif created:
        # Defensive only: a freshly created environment cannot normally have a
        # current marker, but retain the expected first-run completion message.
        output("ITP bootstrap: ready")
    return python, script


def launch(
        root, arguments, *, run=subprocess.run, output=None, verbose=False,
        prerequisite_fn=None):
    output = output or (lambda message: print(message, file=sys.stderr))
    show_progress = os.getenv("ITP_BOOTSTRAP_SHOW_PROGRESS") == "1"
    prerequisite = None
    if command_requires_runtime(arguments):
        if show_progress:
            output("ITP bootstrap: checking prerequisites")
        prerequisite = (prerequisite_fn or check_runtime_prerequisites)(
            arguments, run=run)
    if show_progress and os.getenv("ITP_BOOTSTRAP_PYTHON_VERSION"):
        output(
            "✓ Python "
            + os.environ["ITP_BOOTSTRAP_PYTHON_VERSION"]
            + " ("
            + os.getenv("ITP_BOOTSTRAP_PYTHON_LABEL", "python")
            + ")")
    if show_progress and prerequisite:
        output("✓ Docker")
        output("✓ Docker Compose")
    python, script = ensure_environment(
        root, run=run, output=output, verbose=verbose)
    try:
        result = run([str(python), str(script), *arguments], check=False)
    except OSError as exc:
        raise BootstrapError(
            "could not launch ITP from .venv; remove .venv and rerun the command"
        ) from exc
    return result.returncode


def main(arguments=None):
    arguments = list(arguments or sys.argv[1:])
    verbose = False
    if arguments[:1] == ["--verbose"]:
        verbose = True
        arguments = arguments[1:]
    try:
        return launch(repository_root(), arguments, verbose=verbose)
    except BootstrapError as exc:
        print(f"ITP bootstrap error: {exc}.", file=sys.stderr)
        return 1
    except OSError as exc:
        print(
            "ITP bootstrap error: a local file or process could not be accessed "
            f"({type(exc).__name__}). Correct repository permissions and rerun "
            "the ITP command.",
            file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
