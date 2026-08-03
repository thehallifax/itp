import ast
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_PACKAGES = ("analysis", "collectors", "telemetry", "itp_profiles")


def test_runtime_packages_do_not_import_repository_cli_modules():
    violations = []
    for package in RUNTIME_PACKAGES[:3]:
        for path in (ROOT / package).rglob("*.py"):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [item.name for item in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                if any(name == "scripts" or name.startswith("scripts.")
                       for name in names):
                    violations.append(str(path.relative_to(ROOT)))
    assert violations == []


def test_collector_image_runtime_imports_without_scripts_package(tmp_path):
    for package in RUNTIME_PACKAGES:
        shutil.copytree(ROOT / package, tmp_path / package)
    command = (
        "import sys; "
        f"sys.path.insert(0, {str(tmp_path)!r}); "
        "import analysis.doctor, analysis.prerequisites, "
        "analysis.runtime_deployment, collectors.scheduler, telemetry"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-c", command], text=True,
        capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr
    assert not (tmp_path / "scripts").exists()


def test_collector_dockerfile_copies_runtime_engine_not_scripts():
    dockerfile = (ROOT / "discovery/Dockerfile").read_text()
    assert "COPY analysis /app/analysis" in dockerfile
    assert "COPY scripts " not in dockerfile
