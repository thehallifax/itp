"""Generate and install a deterministic ITP demonstration estate."""
from __future__ import annotations

import hashlib
import json
import os
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from analysis.dashboards import DashboardRegistry
from analysis.notifications import NotificationStore
from analysis.operator.engine import PipelineRunStore
from analysis.readiness import empty_infrastructure_summary, evaluate_readiness
from analysis.sites import SiteRegistry
from collectors.writer import InfluxWriter, atomic_write


DEMO_PROJECT = "itp-demo"
DEMO_DATABASE = "itp_demo"
DEMO_DEPLOYMENT = "demo"
DEFAULT_SEED = 1001


class DemoError(ValueError):
    pass


def _iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _ns(value):
    return int(value.timestamp() * 1_000_000_000)


class DemoTelemetry:
    """Pure deterministic telemetry generator; no Docker or network access."""

    def __init__(self, *, seed=DEFAULT_SEED, days=30, end_at=None):
        if not 1 <= int(days) <= 90:
            raise DemoError("demo history must be between 1 and 90 days")
        self.seed = int(seed)
        self.days = int(days)
        self.end_at = (end_at or datetime.now(timezone.utc)).astimezone(
            timezone.utc).replace(minute=0, second=0, microsecond=0)
        self.start_at = self.end_at - timedelta(days=self.days)
        self.random = random.Random(self.seed)

    def _common(self, collector, hostname, site="example-school Reference Site"):
        return {
            "collector": collector, "customer": "ITP Demo",
            "site": site, "hostname": hostname,
        }

    def _point(self, measurement, timestamp, tags, fields):
        return {
            "measurement": measurement, "timestamp": _ns(timestamp),
            "tags": tags, "fields": fields,
        }

    def points(self):
        points = []
        counters = {
            "port1": [4_000_000_000, 2_000_000_000],
            "internal": [9_000_000_000, 7_000_000_000],
        }
        hours = self.days * 24
        for offset in range(hours + 1):
            at = self.start_at + timedelta(hours=offset)
            phase = offset / max(1, hours)
            warning = 0.38 <= phase < 0.44
            failed = 0.72 <= phase < 0.75
            jitter = self.random.uniform(-3, 3)

            # Standard Telegraf-style host metrics.
            host_tags = {"host": "itp-demo-host", "customer": "ITP Demo",
                         "site": "example-school Reference Site"}
            cpu = max(2.0, min(98.0, 32 + jitter + (45 if warning else 0)))
            memory = max(10.0, min(98.0, 55 + jitter + (30 if warning else 0)))
            points.extend((
                self._point("cpu", at, {**host_tags, "cpu": "cpu-total"},
                            {"usage_user": cpu * .7, "usage_system": cpu * .3,
                             "usage_idle": 100 - cpu}),
                self._point("mem", at, host_tags, {"used_percent": memory}),
                self._point("system", at, host_tags,
                            {"uptime": int((offset + 48) * 3600)}),
            ))

            # Canonical SNMP devices, including a repeatable outage.
            for index, (hostname, platform, vendor) in enumerate((
                    ("core-switch-01", "switch", "Cisco"),
                    ("access-switch-01", "switch", "Aruba"),
                    ("printer-library", "printer", "HP"))):
                available = not (failed and index == 1)
                tags = {
                    **self._common("snmp", hostname),
                    "device_id": f"snmp-{index + 1}",
                    "device_ip": f"192.0.2.{10 + index}",
                    "platform": platform, "vendor": vendor,
                }
                points.extend((
                    self._point("device", at, tags, {
                        "description": f"Demo {vendor} {platform}",
                        "online": available, "uptime_seconds": offset * 3600}),
                    self._point("availability", at, tags,
                                {"available": available}),
                    self._point("performance", at, tags, {
                        "cpu_percent": 25 + index * 6 + jitter,
                        "memory_used_percent": 48 + index * 4 + jitter}),
                ))

            # Mist AP and switch records used by the vendor dashboard.
            for index, platform in enumerate(("ap", "ap", "switch")):
                hostname = f"mist-{platform}-{index + 1:02d}"
                online = not (warning and index == 1)
                tags = {
                    **self._common("mist", hostname, "Northwind College"),
                    "device_id": f"mist-{index + 1}", "platform": platform,
                    "model": "AP45" if platform == "ap" else "EX4400",
                    "vendor": "Juniper",
                }
                fields = {
                    "online": online, "uptime_seconds": offset * 3600,
                    "cpu_percent": 30 + jitter,
                    "memory_used_percent": 52 + jitter,
                }
                points.append(self._point(
                    "infrastructure_device", at, tags, fields))
                if platform == "ap":
                    points.append(self._point(
                        "wireless_access_point", at, tags, {
                            **fields, "client_count": 18 + index * 7,
                            "rx_bps": 8_000_000 + index * 1_000_000,
                            "tx_bps": 3_000_000 + index * 500_000}))

            # FortiGate legacy telemetry retained for its engineering dashboard.
            common = {
                **self._common("fortigate", "edge-fw-01"),
                "device_ip": "192.0.2.1", "vendor": "fortinet",
                "platform": "fortigate", "device_role": "firewall",
            }
            points.extend((
                self._point("fortigate_system", at, common,
                            {"uptime_ticks": (offset + 72) * 360_000}),
                self._point("fortigate_performance", at,
                            {**common, "firmware": "FortiOS 7.4.4"}, {
                                "cpu_percent": 28 + jitter + (
                                    42 if warning else 0),
                                "memory_percent": 61 + jitter,
                                "current_sessions": 3200 + offset % 400}),
            ))
            for name, description, speed in (
                    ("port1", "Primary Internet", 1_000_000_000),
                    ("internal", "Campus LAN", 10_000_000_000)):
                counters[name][0] += int(90_000_000 + self.random.random() * 9_000_000)
                counters[name][1] += int(45_000_000 + self.random.random() * 6_000_000)
                points.append(self._point(
                    "fortigate_interfaces", at,
                    {**common, "interface_name": name,
                     "interface_description": description}, {
                        "admin_status": 1, "operational_status": 1,
                        "interface_speed_bps": speed,
                        "in_octets": counters[name][0],
                        "out_octets": counters[name][1],
                        "in_errors": 0, "out_errors": 0,
                        "in_discards": 0, "out_discards": 0}))

            # Health alternates through healthy, warning and failed periods.
            for collector in ("snmp", "mist", "fortigate"):
                success = not (failed and collector in {"mist", "fortigate"})
                points.append(self._point(
                    "collector_health", at,
                    {"collector": collector, "customer": "ITP Demo",
                     "site": ("Northwind College" if collector == "mist"
                              else "example-school Reference Site"),
                     "diagnostic_category": "success" if success else "api_failure"},
                    {"success": success, "partial": warning,
                     "duration_ms": 450 + int(self.random.random() * 500),
                     "api_requests": 4, "points_written": 0 if not success else 24,
                     "retry_count": 2 if not success else 0,
                     "error_count": 0 if success else 1,
                     "devices_returned": 0 if not success else 3}))
        return points


class DemoEngine:
    """Provision and seed a Compose project that cannot target production."""

    def __init__(self, root, *, seed=DEFAULT_SEED, days=30, end_at=None,
                 writer=None, lifecycle=None):
        self.root = Path(root).resolve()
        self.runtime = self.root / "runtime/demo"
        self.config_path = self.runtime / "config/discovery.yml"
        self.sites_path = self.runtime / "config/sites.yml"
        self.env_path = self.runtime / "config/demo.env"
        self.seed = int(seed)
        self.days = int(days)
        self.end_at = end_at
        self.writer = writer
        self.lifecycle = lifecycle

    def _guard(self, environment):
        expected = {
            "COMPOSE_PROJECT_NAME": DEMO_PROJECT,
            "ITP_DEPLOYMENT_ID": DEMO_DEPLOYMENT,
            "INFLUXDB_BUCKET": DEMO_DATABASE,
        }
        if any(environment.get(key) != value for key, value in expected.items()):
            raise DemoError("refusing demo setup: isolation identity is invalid")
        try:
            self.runtime.resolve().relative_to((self.root / "runtime/demo").resolve())
            self.config_path.resolve().relative_to(self.runtime.resolve())
        except ValueError as exc:
            raise DemoError(
                "refusing demo setup outside runtime/demo") from exc

    def environment(self):
        token = ""
        persisted = {}
        if self.env_path.is_file():
            for line in self.env_path.read_text().splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    key, item = line.split("=", 1)
                    persisted[key.strip()] = item.strip()
            token = persisted.get("INFLUXDB_TOKEN", "")
        value = {
            **os.environ,
            "COMPOSE_PROJECT_NAME": DEMO_PROJECT,
            "ITP_PROFILE": DEMO_DEPLOYMENT,
            "ITP_DEPLOYMENT_ID": DEMO_DEPLOYMENT,
            "ITP_RUNTIME_MODE": "central",
            "ITP_RUNTIME_DIR": str(self.runtime),
            "ITP_DISCOVERY_CONFIG": str(self.config_path),
            "ITP_SITES_CONFIG": str(self.sites_path),
            "ITP_SECRETS_DIR": str(self.runtime / "secrets"),
            "INFLUXDB_BUCKET": DEMO_DATABASE,
            "INFLUXDB_ORG": "itp-demo",
            "INFLUXDB_HOST": "http://influxdb3-core:8181",
            "INFLUXDB_PORT": "8281",
            "GRAFANA_PORT": "3300",
            "TZ": "UTC",
            "TELEGRAF_COLLECTION_INTERVAL": "60s",
            "INFLUXDB_NODE_ID": "itp-demo-node",
            "INFLUXDB_TOKEN": token,
        }
        self._guard(value)
        return value

    def _config(self):
        return {
            "schema_version": 1, "deployment_id": DEMO_DEPLOYMENT,
            "customer": "ITP Demo", "site": "example-school Reference Site",
            "deployment": {"name": "ITP Demonstration", "type": "Home Lab"},
            "discovery": {"interval_seconds": 3600, "concurrency": 5,
                          "timeout_seconds": 1, "retries": 0},
            "networks": [], "exclusions": [],
            "collectors": {
                "snmp": {"enabled": True, "execution": "edge"},
                "mist": {"enabled": True, "execution": "central"},
                "fortigate": {"enabled": True, "execution": "edge"},
                "paloalto": {"enabled": False, "execution": "edge"},
            },
        }

    def prepare(self):
        environment = self.environment()
        self._guard(environment)
        self.runtime.mkdir(parents=True, exist_ok=True)
        config = self._config()
        atomic_write(self.config_path, yaml.safe_dump(config, sort_keys=False))
        atomic_write(
            self.sites_path,
            (self.root / "config/sites.yml").read_text())
        registry = SiteRegistry.load(self.sites_path)
        generated_at = "1970-01-01T00:00:00Z"
        state = {
            "generated_at": generated_at, "deployment_id": DEMO_DEPLOYMENT,
            "assets": [], "summary": {"observability_health": "Demo data active"},
        }
        registry.write(
            self.runtime / "sites", self.runtime / "dashboard", state)
        readiness = evaluate_readiness(
            demo=True, deployment_configured=True, platform_running=True,
            now=datetime(1970, 1, 1, tzinfo=timezone.utc))
        summary = empty_infrastructure_summary(readiness)
        summary["site_options"] = [
            {"site_id": site.site_id, "display_name": site.display_name}
            for site in registry.sites]
        summary["scopes"] = [
            {"scope": "all", "display_name": "All Sites"},
            *({"scope": site.site_id, "display_name": site.display_name}
              for site in registry.sites),
        ]
        atomic_write(
            self.runtime / "dashboard/infrastructure-summary.json",
            json.dumps(summary, indent=2, sort_keys=True) + "\n")
        atomic_write(self.env_path, "\n".join((
            f"ITP_DEPLOYMENT_ID={DEMO_DEPLOYMENT}",
            "INFLUXDB_NODE_ID=itp-demo-node",
            f"INFLUXDB_BUCKET={DEMO_DATABASE}",
            "INFLUXDB_ORG=itp-demo",
            "INFLUXDB_PORT=8281",
            "GRAFANA_PORT=3300",
            "TZ=UTC",
            "TELEGRAF_COLLECTION_INTERVAL=60s",
            f"INFLUXDB_TOKEN={environment['INFLUXDB_TOKEN']}",
            "",
        )))
        dashboards = DashboardRegistry(
            self.root, config, self.runtime / "dashboard/managed",
            self.runtime / "dashboard/provisioning/dashboards.yml").generate()
        return config, dashboards

    def _seed_pipeline_runs(self, telemetry):
        store = PipelineRunStore(self.runtime / "pipeline-runs")
        statuses = ("success", "partial", "failed", "success")
        count = 0
        for day in range(self.days + 1):
            completed = telemetry.start_at + timedelta(days=day)
            status = statuses[(day // 7) % len(statuses)]
            connectors = []
            for connector in ("fortigate", "mist", "snmp"):
                result = "failed" if status == "failed" and connector != "snmp" \
                    else "success"
                connectors.append({
                    "connector": connector, "display_name": connector.title(),
                    "status": result, "duration_ms": 900,
                    "summary": {"points_written": 24 if result == "success" else 0},
                    "exception_type": "" if result == "success" else "DemoFailure",
                    "reason": "" if result == "success" else "simulated demo outage",
                })
            run_id = "demo:" + hashlib.sha256(
                f"{self.seed}|{_iso(completed)}".encode()).hexdigest()[:24]
            payload = {
                "schema_version": 1, "deployment_identity": "ITP Demonstration",
                "deployment_type": "Home Lab",
                "pipeline_run": {
                    "schema_version": 1, "run_id": run_id,
                    "started_at": _iso(completed - timedelta(seconds=4)),
                    "completed_at": _iso(completed), "status": status,
                    "canonical_output": "demo telemetry and inventory",
                    "source_coverage": sorted(
                        item["connector"] for item in connectors
                        if item["status"] == "success"),
                    "provider_coverage": [],
                    "site_coverage": [
                        "site:example-school", "site:example-corporate"],
                    "scopes": [], "warning_details": [],
                },
                "connectors": connectors,
                "summary": {
                    "successful": sum(x["status"] == "success" for x in connectors),
                    "failed": sum(x["status"] == "failed" for x in connectors),
                    "skipped": 0, "duration_ms": 4000, "overall": status,
                },
            }
            store.write(payload)
            count += 1
        return count

    def _seed_notifications(self, telemetry):
        store = NotificationStore(self.runtime)
        events = []
        definitions = (
            ("warning", "Connector becoming stale", "mist", 8, False),
            ("critical", "Firewall collection failed", "fortigate", 7, True),
            ("recovery", "Mist connector recovered", "mist", 6, False),
            ("info", "Demo environment ready", "platform", 0, False),
        )
        active = {}
        for index, (severity, title, subject, age, recovered) in enumerate(definitions):
            at = _iso(telemetry.end_at - timedelta(days=age))
            fingerprint = hashlib.sha256(
                f"demo|{title}|{subject}".encode()).hexdigest()
            event = {
                "schema_version": 1, "id": f"demo-notification-{index + 1}",
                "fingerprint": fingerprint, "rule_id": f"demo.{subject}",
                "severity": severity, "title": title,
                "summary": "Deterministic demonstration event.",
                "source": "demo", "subject": subject,
                "first_seen": at, "last_seen": at, "occurrence_count": index + 1,
                "active": recovered, "acknowledged": False,
                "recovery_of": "demo-notification-1" if severity == "recovery" else "",
                "test": False,
            }
            events.append(event)
            if event["active"]:
                active[fingerprint] = event
        store.write({"schema_version": 1, "active": active,
                     "events": events, "deliveries": []})
        return len(events)

    def run(self):
        config, dashboards = self.prepare()
        if self.lifecycle is not None:
            self.lifecycle.start()
        end_at = self.end_at
        manifest_path = self.runtime / "demo.json"
        if end_at is None and manifest_path.is_file():
            try:
                previous = json.loads(manifest_path.read_text())
                if (previous.get("seed") == self.seed
                        and previous.get("days") == self.days):
                    end_at = datetime.fromisoformat(
                        previous["end_at"].replace("Z", "+00:00"))
            except (OSError, KeyError, ValueError, json.JSONDecodeError):
                end_at = None
        telemetry = DemoTelemetry(
            seed=self.seed, days=self.days, end_at=end_at)
        points = telemetry.points()
        writer = self.writer
        if writer is None:
            environment = self.environment()
            writer = InfluxWriter(
                url=f"http://127.0.0.1:{environment['INFLUXDB_PORT']}",
                token=environment["INFLUXDB_TOKEN"],
                database=DEMO_DATABASE)
        written = writer.write(points)
        pipeline_runs = self._seed_pipeline_runs(telemetry)
        notifications = self._seed_notifications(telemetry)
        manifest = {
            "schema_version": 1, "mode": "demo", "seed": self.seed,
            "days": self.days, "start_at": _iso(telemetry.start_at),
            "end_at": _iso(telemetry.end_at), "database": DEMO_DATABASE,
            "compose_project": DEMO_PROJECT, "points_written": written,
            "pipeline_runs": pipeline_runs, "notifications": notifications,
            "dashboard_packs": dashboards["packs"],
        }
        atomic_write(self.runtime / "demo.json",
                     json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        return manifest
