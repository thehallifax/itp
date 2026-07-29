from datetime import datetime, timezone
from pathlib import Path
import shutil

import pytest

from analysis.demo import DemoEngine, DemoError, DemoTelemetry


END = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)
REPOSITORY = Path(__file__).resolve().parents[2]


class RecordingWriter:
    def __init__(self):
        self.points = []

    def write(self, points):
        self.points.extend(points)
        return len(points)


class RecordingLifecycle:
    def __init__(self):
        self.started = 0

    def start(self):
        self.started += 1
        return {"action": "start"}


def demo_root(tmp_path):
    for name in ("dashboards", "collectors", "config"):
        shutil.copytree(REPOSITORY / name, tmp_path / name)
    return tmp_path


def test_telemetry_is_repeatable_for_fixed_seed_and_time():
    first = DemoTelemetry(seed=42, days=2, end_at=END).points()
    second = DemoTelemetry(seed=42, days=2, end_at=END).points()
    assert first == second
    assert first != DemoTelemetry(seed=43, days=2, end_at=END).points()


def test_telemetry_covers_required_sources_and_operational_states():
    points = DemoTelemetry(seed=1001, days=30, end_at=END).points()
    measurements = {point["measurement"] for point in points}
    assert {
        "cpu", "mem", "system", "device", "availability", "performance",
        "fortigate_system", "fortigate_performance", "fortigate_interfaces",
        "infrastructure_device", "wireless_access_point", "collector_health",
    } <= measurements
    health = [point for point in points
              if point["measurement"] == "collector_health"]
    assert {point["fields"]["success"] for point in health} == {True, False}
    assert {point["fields"]["partial"] for point in health} == {True, False}
    timestamps = sorted({point["timestamp"] for point in points})
    assert timestamps[-1] - timestamps[0] == 30 * 24 * 3600 * 1_000_000_000


def test_history_range_validation():
    with pytest.raises(DemoError, match="between 1 and 90"):
        DemoTelemetry(days=0)
    with pytest.raises(DemoError, match="between 1 and 90"):
        DemoTelemetry(days=91)


def test_demo_run_isolated_and_seeds_all_runtime_outputs(tmp_path):
    root = demo_root(tmp_path)
    writer = RecordingWriter()
    lifecycle = RecordingLifecycle()
    production = root / "runtime/production-sentinel"
    production.parent.mkdir()
    production.write_text("untouched")
    engine = DemoEngine(
        root, seed=1001, days=2, end_at=END,
        writer=writer, lifecycle=lifecycle)

    result = engine.run()

    assert lifecycle.started == 1
    assert result["database"] == "itp_demo"
    assert result["compose_project"] == "itp-demo"
    assert result["points_written"] == len(writer.points) > 0
    assert result["pipeline_runs"] == 3
    assert result["notifications"] == 4
    assert production.read_text() == "untouched"
    assert (root / "runtime/demo/demo.json").is_file()
    assert (root / "runtime/demo/pipeline-runs/latest.json").is_file()
    assert (root / "runtime/demo/notifications/state.json").is_file()
    assert (root / "runtime/demo/dashboard/managed/registry.json").is_file()
    infrastructure = (
        root / "runtime/demo/dashboard/managed/infrastructure/"
        "itp-infrastructure-overview.json")
    dashboard = __import__("json").loads(infrastructure.read_text())
    variable = next(value for value in dashboard["templating"]["list"]
                    if value["name"] == "site")
    assert [(value["text"], value["value"]) for value in variable["options"]] == [
        ("All Sites", "all"),
        ("example-school Reference Site", "site:example-school"),
        ("Northwind College", "site:example-corporate"),
    ]


def test_demo_rerun_is_deterministic_and_does_not_remove_user_files(tmp_path):
    root = demo_root(tmp_path)
    writer = RecordingWriter()
    engine = DemoEngine(root, seed=7, days=1, end_at=END, writer=writer)
    first = engine.run()
    user_file = root / "runtime/demo/user-created-dashboard.json"
    user_file.write_text("{}")
    writer.points.clear()

    second = engine.run()

    assert first == second
    assert user_file.read_text() == "{}"


def test_isolation_guard_rejects_production_identity(tmp_path):
    engine = DemoEngine(tmp_path, writer=RecordingWriter())
    environment = engine.environment()
    environment["INFLUXDB_BUCKET"] = "local_system"
    with pytest.raises(DemoError, match="isolation identity"):
        engine._guard(environment)


def test_demo_cli_reports_machine_readable_result(monkeypatch, tmp_path, capsys):
    import scripts.itp as cli

    expected = {
        "points_written": 123, "days": 30, "pipeline_runs": 31,
        "notifications": 4, "dashboard_packs": [],
    }

    class FakeDemo:
        def __init__(self, root, seed, days):
            assert seed == 1001
            assert days == 30
            self.runtime = tmp_path / "runtime/demo"
            self.env_path = self.runtime / "config/demo.env"
            self.lifecycle = None

        def environment(self):
            return {
                "COMPOSE_PROJECT_NAME": "itp-demo",
                "ITP_DEPLOYMENT_ID": "demo",
                "INFLUXDB_BUCKET": "itp_demo",
            }

        def prepare(self):
            return {"collectors": {}}, {}

        def run(self):
            assert self.lifecycle is not None
            return expected

    monkeypatch.setattr(cli, "DemoEngine", FakeDemo)
    monkeypatch.setattr(cli, "load_root_env", lambda: None)
    monkeypatch.setattr(
        cli.sys, "argv", ["itp", "demo", "--json"])
    cli.main()
    assert '"points_written": 123' in capsys.readouterr().out
