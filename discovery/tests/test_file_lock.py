import ast
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from collectors.file_lock import exclusive_file_lock, lock_file, unlock_file
from collectors.file_permissions import restrict_owner_access
from collectors.writer import atomic_write


ROOT = Path(__file__).resolve().parents[2]


def test_posix_lock_and_unlock_use_exclusive_flock(tmp_path, monkeypatch):
    calls = []
    backend = SimpleNamespace(
        LOCK_EX=2, LOCK_UN=8,
        flock=lambda handle, operation: calls.append(operation))
    monkeypatch.setitem(sys.modules, "fcntl", backend)
    with (tmp_path / "lock").open("a+") as handle:
        lock_file(handle, platform="posix")
        unlock_file(handle, platform="posix")
    assert calls == [backend.LOCK_EX, backend.LOCK_UN]


def test_windows_lock_reserves_one_byte_and_unlocks(tmp_path, monkeypatch):
    calls = []
    backend = SimpleNamespace(
        LK_NBLCK=1, LK_UNLCK=2,
        locking=lambda descriptor, operation, length:
        calls.append((operation, length)))
    monkeypatch.setitem(sys.modules, "msvcrt", backend)
    path = tmp_path / "lock"
    with path.open("a+") as handle:
        with exclusive_file_lock(handle, platform="nt"):
            assert path.read_bytes() == b"\0"
    assert calls == [(backend.LK_NBLCK, 1), (backend.LK_UNLCK, 1)]


def test_lock_context_releases_after_exception(tmp_path, monkeypatch):
    calls = []
    backend = SimpleNamespace(
        LOCK_EX=2, LOCK_UN=8,
        flock=lambda handle, operation: calls.append(operation))
    monkeypatch.setitem(sys.modules, "fcntl", backend)
    with pytest.raises(RuntimeError):
        with (tmp_path / "lock").open("a+") as handle:
            with exclusive_file_lock(handle, platform="posix"):
                raise RuntimeError("test")
    assert calls == [backend.LOCK_EX, backend.LOCK_UN]


def test_application_has_no_direct_fcntl_import():
    for path in (
            ROOT / "collectors/inventory.py",
            ROOT / "collectors/file_lock.py"):
        tree = ast.parse(path.read_text())
        imports = [
            alias.name for node in ast.walk(tree)
            if isinstance(node, ast.Import) for alias in node.names]
        imports.extend(
            node.module for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom))
        assert "fcntl" not in imports


def test_secret_permissions_are_posix_only(tmp_path):
    path = tmp_path / "secret.env"
    path.write_text("value")
    path.chmod(0o644)
    restrict_owner_access(path, platform="nt")
    assert path.stat().st_mode & 0o777 == 0o644
    restrict_owner_access(path, platform="posix")
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(sys.platform == "win32",
                    reason="POSIX modes are not authoritative")
def test_atomic_write_defaults_remain_restrictive_for_secret_artifacts(tmp_path):
    path = tmp_path / "secret.env"
    atomic_write(path, "TOKEN=redacted\n")
    assert path.stat().st_mode & 0o777 == 0o600
    atomic_write(path, "TOKEN=still-redacted\n")
    assert path.stat().st_mode & 0o777 == 0o600


def test_cli_import_does_not_require_fcntl():
    code = (
        "import builtins\n"
        "original = builtins.__import__\n"
        "def guarded(name, *args, **kwargs):\n"
        "    if name == 'fcntl': raise ModuleNotFoundError('blocked fcntl')\n"
        "    return original(name, *args, **kwargs)\n"
        "builtins.__import__ = guarded\n"
        "import scripts.itp\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, text=True,
        capture_output=True, check=False)
    assert result.returncode == 0, result.stderr


def test_host_runtime_has_no_hardcoded_tmp_or_shell_subprocess():
    application_files = list((ROOT / "analysis").rglob("*.py"))
    application_files += list((ROOT / "collectors").rglob("*.py"))
    application_files += list((ROOT / "scripts").rglob("*.py"))
    for path in application_files:
        text = path.read_text()
        assert '"/tmp/' not in text
        assert "'/tmp/" not in text
        assert "shell=True" not in text.replace(" ", "")
