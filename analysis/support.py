"""Sanitised, deployment-scoped support bundle generation."""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

from analysis.operator_ux import SENSITIVE, SafeRedactor

SAFE_RUNTIME_FILES = (
    "deployment.yml", "collectors.yml", "dashboards.yml",
    "scheduler/state.json", "state/daemon.json", "dashboard/readiness.json",
    "dashboard/managed/registry.json", "collector-health",
    "infrastructure/state.json", "operations/operations.json",
    "services/service-health.json", "services/estate-health.json",
    "state-history/latest.json",
)
LOG_SERVICES = ("collector", "discovery", "grafana", "influxdb3-core", "telegraf")


def _read_env(path):
    result = {}
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return result
    for line in lines:
        if line and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip().strip("'\"")
    return result


class SupportBundleBuilder:
    def __init__(self, root, deployment, *, runner=subprocess.run,
                 now=None, privacy="standard"):
        self.root = Path(root).resolve()
        self.deployment = deployment
        self.runner = runner
        self.now = now or datetime.now(timezone.utc)
        secret_values = [value for key, value in
                         _read_env(deployment.env_file).items()
                         if SENSITIVE.search(key)]
        for path in deployment.secrets_dir.glob("*.env"):
            secret_values.extend(_read_env(path).values())
        self.known_secrets = tuple(value for value in secret_values if value)
        self.redactor = SafeRedactor(self.known_secrets, privacy=privacy)
        self.privacy = privacy

    def _command(self, command):
        try:
            value = self.runner(
                command, cwd=self.root, check=False, text=True,
                encoding="utf-8", errors="replace", capture_output=True,
                timeout=20)
            return self.redactor.text(
                (value.stdout or "") + ("\n" + value.stderr if value.stderr else ""))[-65536:]
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"Unavailable: {type(exc).__name__}"

    def _json_bytes(self, value):
        return (json.dumps(self.redactor.value(value), indent=2,
                           sort_keys=True) + "\n").encode()

    def build(self, output_dir=None, *, extra=None):
        output_dir = Path(output_dir or self.root / "support")
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = self.now.strftime("%Y%m%dT%H%M%SZ")
        final = output_dir / (
            f"itp-support-{self.deployment.deployment_id}-{stamp}.zip")
        temporary = final.with_suffix(".zip.incomplete")
        included = []
        excluded = [
            "deployment secret environment files", "private keys",
            "certificate private-key material", "telemetry databases",
            "unrelated deployments and Docker resources",
        ]
        try:
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
                system = {
                    "version": (self.root / "VERSION").read_text().strip()
                    if (self.root / "VERSION").is_file() else "unknown",
                    "git_commit": self._command(["git", "rev-parse", "HEAD"]).strip(),
                    "os": platform.platform(), "architecture": platform.machine(),
                    "python": sys.version.split()[0], "privacy": self.privacy,
                }
                archive.writestr("platform.json", self._json_bytes(system))
                included.append("platform.json")
                for name, value in sorted((extra or {}).items()):
                    target = f"diagnostics/{name}.json"
                    archive.writestr(target, self._json_bytes(value))
                    included.append(target)
                commands = {
                    "docker-version.txt": ["docker", "--version"],
                    "compose-version.txt": ["docker", "compose", "version"],
                    "docker-info.txt": ["docker", "info"],
                }
                for name, command in commands.items():
                    archive.writestr(name, self._command(command))
                    included.append(name)
                for relative in SAFE_RUNTIME_FILES:
                    source = self.deployment.path / relative
                    if not source.is_file():
                        continue
                    try:
                        if source.suffix in {".yml", ".yaml"}:
                            value = yaml.safe_load(source.read_text())
                        elif source.suffix == ".json":
                            value = json.loads(source.read_text())
                        else:
                            value = source.read_text()
                    except (OSError, ValueError, yaml.YAMLError) as exc:
                        value = {"unavailable": type(exc).__name__}
                    target = f"deployment/{relative}"
                    archive.writestr(target, self._json_bytes(value))
                    included.append(target)
                for source in sorted((self.deployment.path / "capabilities").glob("*.json")):
                    target = f"deployment/capabilities/{source.name}"
                    try:
                        value = json.loads(source.read_text())
                    except (OSError, json.JSONDecodeError) as exc:
                        value = {"unavailable": type(exc).__name__}
                    archive.writestr(target, self._json_bytes(value))
                    included.append(target)
                dashboard_root = self.deployment.path / "generated/dashboard"
                dashboard_files = []
                if dashboard_root.is_dir():
                    for source in sorted(path for path in dashboard_root.rglob("*")
                                         if path.is_file()):
                        dashboard_files.append({
                            "path": str(source.relative_to(self.deployment.path)),
                            "mode": oct(source.stat().st_mode & 0o777),
                            "size_bytes": source.stat().st_size,
                        })
                archive.writestr(
                    "deployment/dashboard-publication.json",
                    self._json_bytes({"files": dashboard_files}))
                included.append("deployment/dashboard-publication.json")
                compose = self.deployment.compose_command("config")
                archive.writestr(
                    "deployment/compose-config.txt", self._command(compose))
                included.append("deployment/compose-config.txt")
                archive.writestr(
                    "deployment/containers.json",
                    self._command(self.deployment.compose_command(
                        "ps", "--format", "json")))
                included.append("deployment/containers.json")
                for service in LOG_SERVICES:
                    name = f"logs/{service}.log"
                    archive.writestr(name, self._command(
                        self.deployment.compose_command(
                            "logs", "--no-color", "--tail", "300", service)))
                    included.append(name)
                manifest = {
                    "schema_version": 1,
                    "deployment_id": self.deployment.deployment_id,
                    "privacy": self.privacy,
                    "included": sorted(included + ["manifest.json", "redaction.json"]),
                    "excluded": excluded,
                    "warnings": [
                        "Infrastructure metadata may include hostnames and IP addresses."
                        if self.privacy != "high" else
                        "High privacy pseudonymises common identity fields."],
                }
                archive.writestr("redaction.json", self._json_bytes({
                    "known_secret_values": len(self.known_secrets),
                    "policy": "structural keys, known values, authorization and URL credentials",
                }))
                archive.writestr("manifest.json", self._json_bytes(manifest))
            with zipfile.ZipFile(temporary) as archive:
                for name in archive.namelist():
                    content = archive.read(name)
                    for secret in self.known_secrets:
                        if len(secret) >= 4 and secret.encode() in content:
                            raise ValueError(
                                f"known credential remained in support archive item {name}")
            os.chmod(temporary, 0o600)
            temporary.replace(final)
            return {"path": str(final), "size_bytes": final.stat().st_size,
                    "included": sorted(included), "excluded": excluded,
                    "privacy": self.privacy}
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
