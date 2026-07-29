import ast
import asyncio
from pathlib import Path

import pytest

from collectors import CollectorRegistry
from collectors.__main__ import _run_idle
from discovery.discover import main_async

ROOT = Path(__file__).resolve().parents[2]


def test_supported_collectors_register_automatically():
    assert set(CollectorRegistry.names()) == {
        "aruba", "fortigate", "mist", "paloalto", "papercut", "snmp"}


def test_collectors_do_not_import_grafana_or_each_other():
    for path in (ROOT / "collectors").rglob("*.py"):
        tree = ast.parse(path.read_text())
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        assert not any(name.startswith("grafana") for name in imports), path
        if path.parent.name in {
                "aruba", "fortigate", "mist", "paloalto", "papercut", "snmp"}:
            others = {
                "aruba", "fortigate", "mist", "paloalto", "papercut", "snmp"
            } - {path.parent.name}
            assert not any(name.startswith(f"collectors.{other}")
                           for other in others for name in imports), path


def test_disabled_native_collectors_run_healthy_idle(tmp_path, monkeypatch):
    health = tmp_path / "collector-health"
    monkeypatch.setenv("COLLECTOR_HEALTH_PATH", str(health))

    async def stop_after_first_sleep(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", stop_after_first_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_run_idle())
    assert health.is_file()


def test_snmp_enable_flag_is_enforced(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text(
        "customer: test\nsite: test\n"
        "discovery:\n  interval_seconds: 3600\n"
        "snmp:\n  version: 2\n  communities: [public]\n"
        "networks:\n  - cidr: 192.0.2.0/30\n"
        "collectors:\n  snmp:\n    enabled: false\n"
    )
    args = type("Args", (), {"config": str(path), "command": "once",
                              "inventory": None, "generated": None})()
    with pytest.raises(ValueError, match="collector snmp is not enabled"):
        asyncio.run(main_async(args))
