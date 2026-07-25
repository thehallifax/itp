#!/usr/bin/env python3
"""Standard-library-only self-bootstrap for the ITP command."""
from __future__ import annotations

import hashlib
import json
import os
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


class BootstrapError(RuntimeError):
    pass


def command_requires_runtime(arguments):
    arguments = list(arguments)
    if not arguments or any(value in {"-h", "--help"} for value in arguments):
        return False
    if arguments[0] in WINDOWS_RUNTIME_COMMANDS:
        return True
    return len(arguments) > 1 and arguments[0] == "profile" and \
        arguments[1] in {"up", "down", "restart", "status", "logs"}


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
            [docker, "info"], text=True, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, check=False)
    except OSError as exc:
        raise BootstrapError(
            "Docker Desktop could not be contacted. Start Docker Desktop and "
            "rerun the ITP command") from exc
    if daemon.returncode != 0:
        raise BootstrapError(
            "Docker is installed but Docker Desktop is not running. Start "
            "Docker Desktop, wait until it is ready, and rerun the ITP command")
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


def marker_payload(digest, version=None):
    version = version or sys.version_info
    return {
        "bootstrap_schema": BOOTSTRAP_SCHEMA,
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


def dependencies_current(environment, digest, *, run=subprocess.run):
    python = environment_python(environment)
    expected = marker_payload(digest)
    if not python.is_file() or read_marker(environment) != expected:
        return False
    check = run(
        [str(python), "-c", "import httpx, yaml, pysnmp"],
        text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        check=False)
    return check.returncode == 0


def _safe_remove_environment(environment, root):
    environment = Path(environment).resolve()
    expected = (Path(root).resolve() / ".venv").resolve()
    if environment != expected:
        raise BootstrapError("refusing to replace a virtual environment outside .venv")
    shutil.rmtree(environment)


def ensure_environment(root, *, run=subprocess.run, output=None):
    root = Path(root).resolve()
    dependency = root / DEPENDENCY_FILE
    script = root / "scripts/itp.py"
    environment = root / ".venv"
    output = output or (lambda message: print(message, file=sys.stderr))

    if sys.version_info < MINIMUM_PYTHON:
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

    if not dependencies_current(environment, digest, run=run):
        output("ITP bootstrap: installing dependencies")
        try:
            result = run(
                [str(python), "-m", "pip", "install", str(root)],
                cwd=root, text=True, stdout=sys.stderr, check=False)
        except OSError as exc:
            raise BootstrapError(
                "could not start pip from .venv; remove .venv and rerun the "
                "ITP command") from exc
        if result.returncode != 0:
            raise BootstrapError(
                "dependency installation failed. Check network or package-index "
                "access, then rerun the ITP command. For offline use, make the "
                "required packages available in pip's cache or an internal index")
        try:
            marker_path(environment).write_text(
                json.dumps(
                    marker_payload(digest), indent=2, sort_keys=True) + "\n")
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


def launch(root, arguments, *, run=subprocess.run, output=None):
    output = output or (lambda message: print(message, file=sys.stderr))
    show_progress = os.getenv("ITP_BOOTSTRAP_SHOW_PROGRESS") == "1"
    if show_progress and command_requires_runtime(arguments):
        output("ITP bootstrap: checking prerequisites")
    if show_progress and os.getenv("ITP_BOOTSTRAP_PYTHON_VERSION"):
        output(
            "ITP bootstrap: using Python "
            + os.environ["ITP_BOOTSTRAP_PYTHON_VERSION"]
            + " via "
            + os.getenv("ITP_BOOTSTRAP_PYTHON_LABEL", "python"))
    if os.name == "nt":
        check_windows_prerequisites(arguments, run=run)
    python, script = ensure_environment(root, run=run, output=output)
    try:
        result = run([str(python), str(script), *arguments], check=False)
    except OSError as exc:
        raise BootstrapError(
            "could not launch ITP from .venv; remove .venv and rerun the command"
        ) from exc
    return result.returncode


def main(arguments=None):
    try:
        return launch(repository_root(), list(arguments or sys.argv[1:]))
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
