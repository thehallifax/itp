#!/usr/bin/env python3
"""ITP multi-customer deployment operator."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import warnings
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analysis.dashboards import DashboardRegistry
from analysis.demo import DemoEngine
from analysis.deployment import (
    DeploymentError,
    DockerCompose,
    Provisioner,
    StackLifecycle,
)
from analysis.doctor import (
    DoctorEngine,
    DoctorFatalError,
    DoctorUsageError,
    render_human,
    render_json,
)
from analysis.notifications import (
    NotificationChannelRegistry,
    NotificationEngine,
    NotificationStore,
)
from analysis.operator import (
    DaemonAlreadyRunningError,
    OperatorCollectEngine,
    OperatorDaemon,
    OperatorStatusEngine,
    render_collect,
    render_status,
    start_background,
)
from analysis.runtime_deployment import (
    RuntimeDeploymentError,
    RuntimeDeploymentManager,
    retry_command,
)
from analysis.sites import SiteRegistry
from analysis.virtualisation import VirtualisationEngine
from analysis.virtualisation.config import validate_virtualisation
from analysis.virtualisation.renderer import render as render_virtualisation
from analysis.virtualisation.telemetry import points as virtualisation_points
from collectors.config import load_config
from collectors.base import ExecutionModeMismatch
from collectors.configuration import ConfigurationResolver
from collectors.connector_registry import ConnectorMetadataRegistry
from collectors.file_permissions import restrict_owner_access
from collectors.hyperv.runner import LocalPowerShellRunner
from collectors.proxmox.client import ProxmoxClient
from collectors.vmware.client import VMwareClient
from collectors.writer import InfluxWriter
from itp_profiles import DeploymentProfile, ProfileError, discover_profiles
from itp_profiles.profiles import PLACEHOLDERS, PROFILE_ID
from itp_profiles.setup import BootstrapWizard, SetupError, SetupOptions


def load_root_env():
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if " #" in value and not value.startswith(("'", '"')):
            value = value.split(" #", 1)[0].rstrip()
        os.environ.setdefault(key.strip(), value.strip("'\""))


def load_runtime_env(path):
    for line in Path(path).read_text().splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key] = value


def load_deployment_environment(deployment):
    """Load one deployment's protected environment without rendering values."""
    load_runtime_env(deployment.env_file)
    for path in sorted(deployment.secrets_dir.glob("*.env")):
        load_runtime_env(path)


def deployment_verbose(explicit=False, environment=None):
    environment = os.environ if environment is None else environment
    return bool(explicit) or str(environment.get(
        "ITP_VERBOSE", "")).casefold() in {"1", "true", "yes", "on"}


def deployment_doctor_requested(
        *, non_interactive, explicit=False, input_fn=input):
    if explicit:
        return True
    if non_interactive:
        return False
    return input_fn(
        "Run deployment health checks now? [Y/n]: "
    ).strip().casefold() not in {"n", "no"}


def profile(value, *, secrets=True):
    explicit = set(os.environ)
    load_root_env()
    result = DeploymentProfile.load(value, ROOT)
    result.activate(
        load_secrets=secrets, protected_environment=explicit)
    return result


def describe(value):
    print(f"Profile: {value.id} ({value.name})")
    print(f"Customer: {value.customer_id}")
    sites = SiteRegistry.load(value.paths.sites)
    if len(sites.sites) == 1:
        site = sites.sites[0]
        print(f"Site: {site.site_id} ({site.display_name})")
    else:
        print(f"Sites: {len(sites.sites)} canonical sites")
    print(f"Deployment mode: {value.deployment_mode}")
    print(f"Configuration: {value.paths.discovery}")
    print(f"Sites: {value.paths.sites}")
    print(f"Secrets: {value.paths.secrets}")
    print(f"Runtime: {value.paths.runtime}")
    print(f"Compose project: {value.compose_project}")


def compose(value, *arguments, capture=False):
    environment = {**os.environ, **value.env()}
    return subprocess.run(["docker", "compose", *arguments], cwd=ROOT, env=environment,
                          check=True, text=True, encoding="utf-8",
                          errors="replace", capture_output=capture)


def generate_profile_dashboards(value):
    """Materialize managed folders before Grafana starts watching them."""
    config = load_config(value.paths.discovery)
    raw_config = yaml.safe_load(value.paths.discovery.read_text()) or {}
    legacy_sites = []
    for label, settings in [
            ("profile", raw_config),
            *((name, settings) for name, settings in
              (raw_config.get("collectors") or {}).items()
              if isinstance(settings, dict) and settings.get("enabled"))]:
        configured_site = str(settings.get("site") or "")
        if configured_site and not configured_site.startswith("site:"):
            legacy_sites.append(f"{label}={configured_site}")
    if legacy_sites:
        print("[WARN] Identity compatibility: legacy site aliases will be "
              "normalised; update " + ", ".join(legacy_sites))
    return DashboardRegistry(
        ROOT, config, value.paths.managed_dashboards,
        value.paths.dashboard_runtime / "provisioning/dashboards.yml",
        registry_validation_mode="runtime").generate()


def port_available(port):
    with socket.socket() as connection:
        connection.settimeout(0.25)
        return connection.connect_ex(("127.0.0.1", port)) != 0


def required_secrets(config):
    required = {"INFLUXDB_TOKEN"}
    registry = ConnectorMetadataRegistry.load(ROOT)
    for name, settings in config.get("collectors", {}).items():
        if not isinstance(settings, dict) or not settings.get("enabled"):
            continue
        try:
            connector = registry.get(name)
        except KeyError:
            connector = None
        if connector:
            required.update(
                field["env"] for field in connector.credential_fields
                if field.get("required") and field.get("env"))
        for key in ("api_key_env", "token_env"):
            if settings.get(key):
                required.add(str(settings[key]))
    return sorted(required)


def validate(value, *, process_environment=None):
    describe(value)
    checks = []

    def check(label, passed, detail):
        checks.append(passed)
        print(f"[{'PASS' if passed else 'FAIL'}] {label}: {detail}")

    config = load_config(value.paths.discovery)
    sites = SiteRegistry.load(value.paths.sites)
    blocking_types = {
        "duplicate_alias", "ambiguous_alias", "duplicate_site_id",
        "unknown_parent", "self_parent", "circular_hierarchy",
        "invalid_site_type", "invalid_rollup_group", "disabled_parent",
        "excessive_hierarchy_depth", "duplicate_display_order",
        "invalid_service_dependency", "unknown_dependency_site",
    }
    findings = [item for item in sites.validation() if item["type"] in blocking_types]
    check("Manifest", True, f"deployment_id={value.deployment_id} "
          f"customer_id={value.customer_id} mode={value.deployment_mode} "
          f"timezone={value.timezone}")
    check("Configuration", config.get("schema_version") == 1, "schema version 1")
    check("Sites", bool(sites.sites) and not findings,
          f"{len(sites.sites)} canonical site(s), model={sites.deployment_model}, "
          f"{len(findings)} conflict(s)")
    try:
        virtualisation = validate_virtualisation(config, value.paths.sites, ROOT)
        check("Virtualisation", True,
              f"{len(virtualisation)} enabled endpoint(s)" if virtualisation else "not enabled")
    except ValueError as exc:
        check("Virtualisation", False, str(exc))
    enabled = sorted(name for name, settings in config.get("collectors", {}).items()
                     if isinstance(settings, dict) and settings.get("enabled"))
    check("Collectors", bool(enabled), ", ".join(enabled) or "none enabled")
    resolution = ConfigurationResolver.profile(
        value, ConnectorMetadataRegistry.load(ROOT),
        process_environment=process_environment or {}).evaluate()
    check("Connector configuration", resolution["ready"],
          "ready" if resolution["ready"] else "required settings missing")
    missing, placeholders = [], []
    for name in required_secrets(config):
        raw = os.getenv(name, "").strip()
        if not raw:
            missing.append(name)
        elif raw.lower() in PLACEHOLDERS or "change_me" in raw.lower():
            placeholders.append(name)
    check("Secrets", not missing and not placeholders,
          "valid" if not missing and not placeholders else
          "missing=" + ",".join(missing) + " placeholders=" + ",".join(placeholders))
    registry = DashboardRegistry(ROOT, config, value.paths.managed_dashboards,
        value.paths.dashboard_runtime / "provisioning/dashboards.yml")
    resolved = registry.resolve()
    check("Dashboards", True, f"{len(resolved['dashboards'])} selected")
    value.paths.create_runtime()
    check("Runtime", os.access(value.paths.runtime, os.W_OK), f"writable: {value.paths.runtime}")
    check("Telemetry", value.deployment_id == value.id and bool(value.influx_bucket),
          f"deployment_id={value.deployment_id} database={value.influx_bucket}")
    check("Grafana isolation", value.provisioning_namespace == value.id,
          f"separate instance namespace={value.provisioning_namespace}")
    try:
        compose(value, "config", "--quiet", capture=True)
        compose_valid, compose_detail = True, value.compose_project
    except (OSError, subprocess.CalledProcessError) as exc:
        compose_valid, compose_detail = False, str(exc)
    check("Compose", compose_valid, compose_detail)
    if value.deployment_mode == "standalone":
        occupied = [str(port) for port in
                    (value.grafana_port, value.influxdb_port)
                    if not port_available(port)]
        check("Ports", not occupied or _project_running(value),
              "available" if not occupied else "in use: " + ", ".join(occupied))
    else:
        check("Shared services", bool(
            value.shared_grafana_url and value.shared_influxdb_url),
            f"cluster={value.cluster_id} grafana={value.shared_grafana_url} "
            f"influxdb={value.shared_influxdb_url}")
    if not all(checks):
        raise ProfileError(f"profile {value.id} validation failed")
    print(f"Status: valid ({len(checks)} checks)")


def _project_running(value):
    try:
        return bool(compose(value, "ps", "-q", capture=True).stdout.strip())
    except Exception:
        return False


def _port_owner(port):
    """Return the Compose project/container publishing a local port."""
    try:
        output = subprocess.run([
            "docker", "ps", "--format",
            '{{.Names}}\\t{{.Ports}}\\t{{.Label "com.docker.compose.project"}}'],
            check=True, text=True, capture_output=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    marker = f":{port}->"
    for line in output.splitlines():
        name, ports, *project = line.split("\t")
        if marker in ports:
            return {"container": name, "project": project[0] if project else ""}
    return None


def preflight_start(value):
    value.paths.create_runtime()
    if value.deployment_mode == "cluster_member":
        for label, endpoint in (
                ("Grafana", value.shared_grafana_url),
                ("InfluxDB", value.shared_influxdb_url)):
            try:
                with urllib.request.urlopen(endpoint, timeout=3):
                    pass
            except urllib.error.HTTPError as exc:
                if exc.code < 500:
                    continue
                raise ProfileError(
                    f"cluster-member profile {value.id} cannot reach shared "
                    f"{label} at {endpoint}") from exc
            except OSError as exc:
                raise ProfileError(
                    f"cluster-member profile {value.id} cannot reach shared "
                    f"{label} at {endpoint}") from exc
        return
    if _project_running(value):
        return
    for label, port in (
            ("Grafana", value.grafana_port),
            ("InfluxDB", value.influxdb_port)):
        if port_available(port):
            continue
        owner = _port_owner(port) or {}
        detail = owner.get("project") or owner.get("container") or \
            "an unknown local process"
        raise ProfileError(
            f"Cannot start standalone profile {value.id}. {label} port "
            f"{port} is already used by {detail}. Stop the conflicting "
            "deployment, configure alternate standalone ports, or use "
            "cluster-member deployment mode.")


def status(value):
    describe(value)
    try:
        payload = compose(value, "ps", "--format", "json", capture=True).stdout.strip()
        containers = [json.loads(line) for line in payload.splitlines() if line.strip()]
    except Exception:
        containers = []
    print("Containers: " + (", ".join(
        f"{item.get('Service')}={item.get('State')}" for item in containers) or "not running"))
    config = load_config(value.paths.discovery)
    enabled = sorted(name for name, settings in config.get("collectors", {}).items()
                     if isinstance(settings, dict) and settings.get("enabled"))
    print("Enabled collectors: " + (", ".join(enabled) or "none"))
    sites = SiteRegistry.load(value.paths.sites)
    print(f"Deployment model: {value.deployment_mode}")
    print(f"Enabled sites: {len(sites.sites)}")
    assets_path = value.paths.inventory / "assets.json"
    try:
        assets = len(json.loads(assets_path.read_text()).get("assets", []))
    except Exception:
        assets = 0
    print(f"Assets: {assets}")
    services_path = value.paths.services / "service-health.json"
    try:
        services = json.loads(services_path.read_text()).get("estate", {}).get("services", [])
        print("Services: " + ", ".join(f"{item['service']}={item['status']}" for item in services))
    except Exception:
        print("Services: no generated state")
    registry_path = value.paths.managed_dashboards / "registry.json"
    try:
        count = len(json.loads(registry_path.read_text())["dashboards"])
    except Exception:
        count = 0
    print(f"Managed dashboards: {count}")
    if value.deployment_mode == "cluster_member":
        print(f"Shared cluster: {value.cluster_id}")
        print(f"InfluxDB: {value.shared_influxdb_url}")
        print(f"Grafana: {value.shared_grafana_url}")
    elif containers:
        print(f"InfluxDB: http://localhost:{value.influxdb_port}")
        print(f"Grafana: http://localhost:{value.grafana_port}")
    else:
        print("InfluxDB: stopped")
        print("Grafana: stopped")


def sites_status(value):
    describe(value)
    registry = SiteRegistry.load(value.paths.sites)
    blocking = [item for item in registry.validation()
                if item["type"] not in {"unused_alias", "unknown_site"}]
    print(f"Deployment model: {registry.deployment_model}")
    print(f"Enabled sites: {len(registry.sites)}")
    print(f"Root sites: {len(registry.roots)}")
    print(f"Child sites: {len(registry.children)}")
    print(f"Disabled sites: {len(registry.disabled_sites)}")
    print("Estate rollups: " + ("enabled" if registry.estate_enabled else "single-site"))
    for site in registry.sites:
        relationship = f" -> {site.canonical_parent_id}" if site.parent_id else ""
        print(f"  {site.site_id}\t{site.display_name}\t{site.type}{relationship}")
    print("Hierarchy validation: " + ("valid" if not blocking
          else f"{len(blocking)} blocking finding(s)"))
    if blocking:
        for finding in blocking:
            print(f"  {finding['type']}: {finding.get('site_id') or finding.get('alias')}")
        raise ProfileError(f"profile {value.id} site hierarchy is invalid")
    print("Status: valid")


def virtualisation(value, *, fixture=None, provider_name=None):
    config = load_config(value.paths.discovery)
    sites = SiteRegistry.load(value.paths.sites)
    if not sites.sites:
        raise ProfileError("virtualisation requires at least one enabled canonical site")
    selected = fixture or provider_name
    if selected not in {"vmware", "hyperv", "proxmox"}:
        raise ProfileError("select --fixture or --provider: vmware, hyperv, or proxmox")
    if not fixture:
        endpoints = [item for item in validate_virtualisation(
            config, value.paths.sites, ROOT) if item["provider"] == selected]
        if not endpoints:
            raise ProfileError(f"no enabled {selected} endpoint is configured")
        contracts = []
        for endpoint in endpoints:
            if selected == "vmware":
                username = str(endpoint.get("username") or "")
                password = str(endpoint.get("password") or "")
                if not username or not password:
                    raise ProfileError("VMware read-only credentials are unavailable")
                verify = endpoint.get("ca_bundle") or endpoint.get("verify_tls", True)
                contract = VMwareClient(endpoint["endpoint"], username, password,
                    verify=verify, timeout=float(endpoint.get("timeout_seconds", 20))).collect()
            elif selected == "proxmox":
                token_id = str(endpoint.get("token_id") or "")
                token_secret = str(endpoint.get("token_secret") or "")
                if not token_id or not token_secret:
                    raise ProfileError("Proxmox read-only API token is unavailable")
                verify = endpoint.get("ca_bundle") or endpoint.get("verify_tls", True)
                contract = ProxmoxClient(endpoint["endpoint"], token_id, token_secret,
                    verify=verify, timeout=float(endpoint.get("timeout_seconds", 20))).collect()
            else:
                if endpoint.get("transport") != "local_powershell":
                    raise ProfileError(
                        "Hyper-V live collection must run on an authorised Windows "
                        "management host using local_powershell")
                contract = LocalPowerShellRunner(
                    ROOT / "collectors/hyperv/Collect-ITPHyperV.ps1").run()
            contracts.append((selected, endpoint["endpoint"], contract,
                              endpoint["site_id"]))
    output = value.paths.runtime / "virtualisation"
    if fixture:
        output = output / "fixtures" / selected
    engine = VirtualisationEngine(
        ROOT, output, value.deployment_id,
        sites.sites[0].site_id,
        (config.get("virtualisation") or {}).get("thresholds"))
    if fixture:
        result = engine.run_fixture(selected)
    else:
        result = render_virtualisation(output, engine.evaluate(contracts))
        written = InfluxWriter.from_config(config).write(
            virtualisation_points(result))
        print(f"Telemetry points written: {written}")
    print(f"Profile: {value.id}")
    print(f"Provider: {selected}")
    print("Mode: " + ("sanitized fixture" if fixture else "live read-only"))
    print(f"Runtime: {output}")
    print("Objects: " + ", ".join(
        f"{key}={result['summary'][key]}" for key in
        ("managers", "clusters", "hosts", "vms", "containers")))
    print(f"Findings: warnings={result['summary']['warnings']} "
          f"critical={result['summary']['critical_findings']}")


def virtualisation_status(value):
    path = value.paths.runtime / "virtualisation"
    try:
        summary = json.loads((path / "summary.json").read_text())
        collection = json.loads((path / "collection-status.json").read_text())
    except (OSError, json.JSONDecodeError):
        fixtures = sorted((path / "fixtures").glob("*/summary.json"))
        if not fixtures:
            raise ProfileError("no generated virtualisation state; run virtualisation collection")
        print(f"Profile: {value.id}")
        print("Live virtualisation state: not generated")
        for fixture_path in fixtures:
            summary = json.loads(fixture_path.read_text())
            print(f"Fixture {fixture_path.parent.name}: "
                  f"hosts={summary.get('hosts', 0)} vms={summary.get('vms', 0)} "
                  f"containers={summary.get('containers', 0)}")
        return
    print(f"Profile: {value.id}")
    print(f"Runtime: {path}")
    print(f"Providers: {summary.get('providers', 0)}")
    print(f"Hosts: {summary.get('hosts', 0)}")
    print(f"VMs: {summary.get('vms', 0)}")
    print(f"Containers: {summary.get('containers', 0)}")
    for item in collection.get("collections", []):
        print(f"{item['provider']}\t{item['result']}\t{item['last_attempt']}")


def init_secrets(value):
    value.paths.secrets.mkdir(parents=True, exist_ok=True)
    created = []
    examples = sorted(value.paths.secrets.glob("*.env.example"))
    for source in examples:
        target = source.with_suffix("")
        if target.exists():
            continue
        shutil.copyfile(source, target)
        restrict_owner_access(target)
        created.append(target)
    print("Created: " + (", ".join(str(path) for path in created) or "none; existing files preserved"))


def bootstrap_influx(value):
    """Create the first profile-local InfluxDB admin token without exposing it."""
    if value.deployment_mode != "standalone":
        return
    token_file = value.paths.secrets / "influxdb.env"
    if token_file.exists() and any(line.startswith("INFLUXDB_TOKEN=") and line.split("=", 1)[1].strip()
                                   for line in token_file.read_text().splitlines()):
        return
    print(f"Bootstrapping isolated InfluxDB credentials for profile {value.id}")
    compose(value, "down")
    name = f"{value.compose_project}-influx-bootstrap"
    subprocess.run(["docker", "rm", "-f", name], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    environment = {**os.environ, **value.env()}
    subprocess.run(["docker", "compose", "run", "-d", "--name", name,
        "--service-ports", "--no-deps", "influxdb3-core",
        "influxdb3", "serve", "--node-id", os.getenv("INFLUXDB_NODE_ID", f"{value.id}-node"),
        "--object-store=file", "--data-dir=/var/lib/influxdb3"],
        cwd=ROOT, env=environment, check=True, text=True, encoding="utf-8",
        errors="replace", stdout=subprocess.DEVNULL)
    try:
        for _ in range(60):
            with socket.socket() as connection:
                connection.settimeout(1)
                if connection.connect_ex(("127.0.0.1", value.influxdb_port)) == 0:
                    break
            time.sleep(0.5)
        else:
            raise ProfileError("temporary InfluxDB bootstrap service did not become ready")
        request = urllib.request.Request(
            f"http://127.0.0.1:{value.influxdb_port}/api/v3/configure/token/admin",
            data=b"", method="POST",
            headers={"Accept": "application/json", "Content-Type": "application/json"})
        for _ in range(30):
            try:
                with urllib.request.urlopen(request, timeout=3) as result:
                    response = json.loads(result.read())
                break
            except (OSError, ValueError):
                time.sleep(0.5)
        else:
            raise ProfileError("InfluxDB operator-token endpoint did not become ready")
        token = str(response.get("token") or "")
        if len(token) < 40 or any(character.isspace() for character in token):
            raise ProfileError("InfluxDB did not return a valid opaque admin token")
        value.paths.secrets.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write("INFLUXDB_TOKEN=" + token + "\n")
        # Allow the catalog/WAL snapshot containing the operator token to
        # reach the profile volume before stopping the bootstrap process.
        time.sleep(3)
    finally:
        subprocess.run(["docker", "stop", "--time", "30", name], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["docker", "rm", name], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(20):
            if port_available(value.influxdb_port):
                break
            time.sleep(0.25)
    value.load_secrets()
    print("InfluxDB profile credential created")


def create(profile_id):
    if not PROFILE_ID.fullmatch(profile_id):
        raise ProfileError("profile ID must be lowercase and filesystem-safe")
    target = ROOT / "profiles" / profile_id
    if target.exists():
        raise ProfileError(f"profile already exists: {profile_id}")
    target.mkdir(parents=True)
    manifest = {
        "profile": {"id": profile_id, "name": "CHANGE ME", "environment": "production",
                    "timezone": "Australia/Perth", "runtime_mode": "central"},
        "paths": {
            "discovery_config": f"profiles/{profile_id}/discovery.yml",
            "sites_config": f"profiles/{profile_id}/sites.yml",
            "dashboards_config": f"profiles/{profile_id}/dashboards.yml",
            "secrets_dir": f"secrets/{profile_id}",
            "runtime_dir": f"runtime/{profile_id}",
        },
        "telemetry": {"deployment_id": profile_id, "influx_bucket": f"itp_{profile_id}",
                      "influx_org": "local_org"},
        "grafana": {"folder_prefix": profile_id.upper(),
                    "provisioning_namespace": profile_id},
        "ports": {"grafana": 3000, "influxdb": 8181},
    }
    (target / "profile.yml").write_text(yaml.safe_dump(manifest, sort_keys=False))
    baseline = {"schema_version": 1, "customer": profile_id, "site": profile_id,
        "discovery": {"interval_seconds": 3600, "concurrency": 20,
                      "timeout_seconds": 2, "retries": 1},
        "snmp": {"version": 2, "communities": ["${SNMP_COMMUNITY}"]},
        "networks": [], "exclusions": [],
        "collectors": {name: {"enabled": False} for name in
                       ("snmp", "mist", "fortigate", "paloalto")}}
    (target / "discovery.yml").write_text(yaml.safe_dump(baseline, sort_keys=False))
    (target / "sites.yml").write_text(yaml.safe_dump({"sites": [{
        "id": profile_id, "display_name": "CHANGE ME", "aliases": [profile_id]}]},
        sort_keys=False))
    (target / "dashboards.yml").write_text("enabled: true\nfolder_prefix: CHANGE_ME\n")
    (target / "env.example").write_text(
        "GRAFANA_ADMIN_USER=admin\nGRAFANA_ADMIN_PASSWORD=CHANGE_ME\n"
        "INFLUXDB_TOKEN=CHANGE_ME\nINFLUXDB_NODE_ID=CHANGE_ME\n")
    secret_dir = ROOT / "secrets" / profile_id
    secret_dir.mkdir(parents=True)
    (secret_dir / "collector.env.example").write_text(
        "INFLUXDB_TOKEN=\nSNMP_COMMUNITY=\n")
    print(f"Created disabled-collector profile: {profile_id}")
    print(f"Next: ./itp profile validate {profile_id}")


def run_demo(seed=1001, days=30):
    load_root_env()
    engine = DemoEngine(ROOT, seed=seed, days=days)
    os.environ.update(engine.environment())
    config, _ = engine.prepare()
    compose_runtime = DockerCompose(ROOT, environment=engine.environment)
    engine.lifecycle = StackLifecycle(
        compose_runtime,
        Provisioner(
            ROOT, config, engine.runtime, compose_runtime,
            env_path=engine.env_path))
    return engine.run()


def grafana_onboarding_summary(runtime_manager, deployment, url, *,
                               non_interactive=False):
    password = runtime_manager.take_new_grafana_password(deployment)
    lines = [
        "Grafana:",
        f"  URL: {url}",
        "  Username: admin",
    ]
    if password and not non_interactive:
        lines.extend((
            f"  Password: {password}",
            "  Store this password securely.",
        ))
    else:
        lines.append("  Password: securely stored")
    lines.extend((
        "  Retrieve it later with:",
        "    " + retry_command(
            "credentials", "grafana", "--deployment",
            deployment.deployment_id),
    ))
    return lines


def add_deployment_selector(parser):
    """Add the canonical runtime deployment selector to a command parser."""
    parser.add_argument(
        "--deployment", metavar="DEPLOYMENT_ID",
        help="runtime deployment ID (defaults to the active deployment)")
    return parser


def add_local_deployment_selector(parser):
    """Allow canonical selector placement after a nested subcommand."""
    parser.add_argument(
        "--deployment", metavar="DEPLOYMENT_ID",
        default=argparse.SUPPRESS,
        help="runtime deployment ID (defaults to the active deployment)")
    return parser


def resolve_runtime_deployment(runtime_manager, requested=None):
    """Resolve an explicit, active, or sole runtime deployment.

    Return None only when no runtime deployment exists, preserving the legacy
    root-stack command path.
    """
    if requested or runtime_manager.active_id() or runtime_manager.list():
        return runtime_manager.select(requested)
    return None


def last_json_object(output):
    """Return the final JSON object from combined container output."""
    decoder = json.JSONDecoder()
    candidates = []
    for index, character in enumerate(output):
        if character != "{":
            continue
        try:
            value, end = decoder.raw_decode(output[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidates.append((index + end, value))
    return max(candidates, default=(0, {}), key=lambda item: item[0])[1]


def compose_service_state(output, service):
    """Return a deterministic Compose service state from JSON output."""
    values = []
    try:
        parsed = json.loads(output)
        values = parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        for line in output.splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                values.append(value)
    for value in values:
        if str(value.get("Service") or value.get("service") or "") == service:
            return str(
                value.get("State") or value.get("state") or "unknown"
            ).casefold()
    return "stopped"


def runtime_collection(runtime_manager, deployment, config, connector=None):
    """Collect configured connectors independently in the shared service."""
    readiness = {
        value["id"]: value
        for value in runtime_manager.collector_readiness(deployment)}
    configured = []
    enabled_metadata = []
    outcomes = []
    additional = []
    settings = config.get("collectors") or {}
    for metadata in runtime_manager.registry.all():
        enabled = (
            isinstance(settings.get(metadata.id), dict)
            and settings[metadata.id].get("enabled") is True)
        if connector and metadata.id != connector:
            continue
        if connector and not enabled:
            raise RuntimeDeploymentError(
                f"collector {connector} is not enabled in deployment "
                f"{deployment.deployment_id}")
        if not enabled:
            if not connector:
                additional.append({
                    "connector": metadata.id,
                    "display_name": metadata.display_name,
                    "status": "skipped", "duration_ms": 0, "summary": {},
                    "exception_type": "", "reason": "disabled"})
            continue
        enabled_metadata.append(metadata)
        state = readiness[metadata.id]
        if state["state"] != "configured":
            diagnostic = {}
            if state["state"] == "execution mode mismatch":
                mismatch = ExecutionModeMismatch(
                    metadata.id, state["execution_mode"],
                    state["runtime_mode"])
                reason = str(mismatch)
                diagnostic = mismatch.diagnostic_payload()
            else:
                reason = state["state"]
            if state["missing"]:
                reason += ": " + ", ".join(state["missing"])
            additional.append({
                "connector": metadata.id,
                "display_name": metadata.display_name,
                "status": "skipped", "duration_ms": 0,
                "summary": (
                    {"diagnostic": diagnostic} if diagnostic else {}),
                "exception_type": (
                    "execution_mode_mismatch" if diagnostic else ""),
                "reason": reason})
            continue
        configured.append((metadata, None))
        started = time.monotonic()
        completed = deployment.run_compose(
            "exec", "-T", "collector", "python", "-m", "collectors",
            "--config", "/app/config.yml", "collect", metadata.id, "--json",
            capture=True, check=False)
        duration_ms = int((time.monotonic() - started) * 1000)
        output = (completed.stdout or "").strip()
        payload = last_json_object(output) if output else {}
        if completed.returncode == 0 and output:
            payload = last_json_object(output)
        diagnostic = (
            payload.get("diagnostic", {})
            if isinstance(payload, dict) else {})
        outcomes.append({
            "connector": metadata.id,
            "status": "success" if completed.returncode == 0 else "failed",
            "duration_ms": duration_ms,
            "value": payload,
            "exception_type": (
                "" if completed.returncode == 0 else
                str(diagnostic.get("category") or "CollectorCommandError")),
            "reason": (
                "" if completed.returncode == 0
                else str(diagnostic.get("message") or
                         "collector execution failed; inspect shared collector logs")),
        })
    if connector and not any(
            value.id == connector for value in runtime_manager.registry.all()):
        raise RuntimeDeploymentError(f"unknown collector: {connector}")
    engine = OperatorCollectEngine(
        ROOT, config, registry=runtime_manager.registry,
        runtime_dir=deployment.path)
    return engine.record(
        configured, outcomes, additional,
        scope_metadata=tuple(enabled_metadata))


def runtime_stack_action(deployment, action):
    """Apply one runtime lifecycle action using the current source image."""
    if action == "stop":
        deployment.run_compose("down")
    elif action in {"start", "restart"}:
        if action == "restart":
            deployment.run_compose("down")
        deployment.run_compose(
            "up", "-d", "--build", "--remove-orphans")
    else:
        raise ValueError(f"unsupported runtime stack action: {action}")


def main():
    parser = argparse.ArgumentParser(prog="./itp", description=__doc__)
    commands = parser.add_subparsers(dest="group", required=True)
    commands.add_parser("help")
    for deployment_command in ("deploy", "init"):
        runtime_setup = commands.add_parser(
            deployment_command,
            help="create a runtime-only deployment" + (
                " and start it" if deployment_command == "deploy" else ""))
        runtime_setup.add_argument("--deployment-name")
        runtime_setup.add_argument("--deployment-id")
        runtime_setup.add_argument("--timezone")
        runtime_setup.add_argument("--grafana-port", type=int, default=3000)
        runtime_setup.add_argument("--influxdb-port", type=int, default=8181)
        runtime_setup.add_argument("--listen-address", default="127.0.0.1")
        runtime_setup.add_argument("--collector", action="append", default=None)
        runtime_setup.add_argument("--site-id")
        runtime_setup.add_argument("--site-name")
        runtime_setup.add_argument("--non-interactive", action="store_true")
        runtime_setup.add_argument("--force", action="store_true")
        runtime_setup.add_argument("--reset-influx", action="store_true")
        runtime_setup.add_argument("--no-start", action="store_true")
        runtime_setup.add_argument("--verbose", action="store_true")
        runtime_setup.add_argument("--doctor", action="store_true")
    deployment_parser = commands.add_parser(
        "deployment", help="inspect runtime deployments")
    deployment_actions = deployment_parser.add_subparsers(
        dest="deployment_action", required=True)
    deployment_actions.add_parser("list").add_argument(
        "--json", action="store_true")
    deployment_show = deployment_actions.add_parser("show")
    deployment_show.add_argument("deployment_id", nargs="?")
    deployment_show.add_argument("--json", action="store_true")
    deployment_select = deployment_actions.add_parser(
        "select", help="set the active runtime deployment")
    deployment_select.add_argument("deployment_id")
    deployment_select.add_argument("--json", action="store_true")
    reset_runtime = commands.add_parser(
        "reset", help="reset disposable generated deployment state")
    reset_runtime.add_argument("--deployment", required=True)
    reset_runtime.add_argument("--reset-influx", action="store_true",
                               help="also permanently remove telemetry")
    reset_runtime.add_argument("--yes", action="store_true")
    reset_runtime.add_argument("--json", action="store_true")
    remove_runtime = commands.add_parser(
        "remove", help="remove a runtime deployment")
    remove_runtime.add_argument("--deployment", required=True)
    remove_runtime.add_argument("--remove-telemetry", action="store_true")
    remove_runtime.add_argument("--yes", action="store_true")
    remove_runtime.add_argument("--json", action="store_true")
    cleanup_runtime = commands.add_parser(
        "cleanup", help="audit or remove confidently owned orphan resources")
    cleanup_runtime.add_argument("--deployment")
    cleanup_runtime.add_argument("--yes", action="store_true")
    cleanup_runtime.add_argument("--json", action="store_true")
    credentials = add_deployment_selector(commands.add_parser(
        "credentials", help="show how to retrieve deployment credentials")
    )
    credential_actions = credentials.add_subparsers(dest="credential_action")
    grafana_credentials = add_local_deployment_selector(
        credential_actions.add_parser(
            "grafana",
            help="display generated Grafana administrator credentials"))
    grafana_credentials.add_argument("--json", action="store_true")
    ca_credentials = credential_actions.add_parser(
        "ca", help="manage deployment-specific trusted CA certificates")
    ca_actions = ca_credentials.add_subparsers(
        dest="ca_action", required=True)
    ca_add = add_local_deployment_selector(ca_actions.add_parser("add"))
    ca_add.add_argument("certificate_file")
    ca_add.add_argument("--json", action="store_true")
    ca_list = add_local_deployment_selector(ca_actions.add_parser("list"))
    ca_list.add_argument("--json", action="store_true")
    ca_remove = add_local_deployment_selector(ca_actions.add_parser("remove"))
    ca_remove.add_argument("identifier")
    ca_remove.add_argument("--json", action="store_true")
    collector_runtime = add_deployment_selector(commands.add_parser(
        "collector", help="manage collectors in a runtime deployment"))
    collector_actions = collector_runtime.add_subparsers(
        dest="runtime_collector_action", required=True)
    collector_list_runtime = add_local_deployment_selector(
        collector_actions.add_parser("list"))
    collector_list_runtime.add_argument(
        "--json", action="store_true")
    for action in ("add", "test", "run", "remove"):
        item = add_local_deployment_selector(
            collector_actions.add_parser(action))
        item.add_argument("collector")
        item.add_argument("--json", action="store_true")
    dashboard_runtime = add_deployment_selector(commands.add_parser(
        "dashboard", help="manage runtime dashboards"))
    dashboard_generate = add_local_deployment_selector(
        dashboard_runtime.add_subparsers(
            dest="dashboard_action", required=True).add_parser("generate"))
    update_runtime = add_deployment_selector(commands.add_parser(
        "update", help="fast-forward source and refresh a runtime deployment"))
    connector_parser = commands.add_parser(
        "connectors", help="inspect the connector metadata registry")
    connector_actions = connector_parser.add_subparsers(
        dest="connector_action", required=True)
    connector_list = connector_actions.add_parser("list")
    connector_list.add_argument("--json", action="store_true")
    connector_inspect = connector_actions.add_parser("inspect")
    connector_inspect.add_argument("connector")
    connector_inspect.add_argument("--json", action="store_true")
    doctor = add_deployment_selector(commands.add_parser(
        "doctor", help="run read-only deployment diagnostics"))
    doctor.add_argument("--json", action="store_true")
    doctor_scope = doctor.add_mutually_exclusive_group()
    doctor_scope.add_argument("--platform-only", action="store_true")
    doctor_scope.add_argument("--connectors-only", action="store_true")
    doctor.add_argument("--connector")
    doctor.add_argument("--offline", action="store_true")
    doctor.add_argument("--strict", action="store_true")
    collect_root = add_deployment_selector(commands.add_parser(
        "collect", help="run every enabled deployment connector once"))
    collect_root.add_argument("--json", action="store_true")
    status_root = add_deployment_selector(commands.add_parser(
        "status", help="show deployment collection and service status"))
    status_root.add_argument("--json", action="store_true")
    daemon = add_deployment_selector(commands.add_parser(
        "daemon", help="run enabled deployment connectors continuously"))
    daemon_mode = daemon.add_mutually_exclusive_group()
    daemon_mode.add_argument("--foreground", action="store_true")
    daemon_mode.add_argument("--once", action="store_true")
    for lifecycle_name in ("start", "stop", "restart"):
        lifecycle = add_deployment_selector(commands.add_parser(
            lifecycle_name, help=f"{lifecycle_name} a deployment stack"))
        lifecycle.add_argument("--json", action="store_true")
    logs_root = add_deployment_selector(
        commands.add_parser("logs", help="show deployment service logs"))
    logs_root.add_argument("service_positional", nargs="?")
    logs_root.add_argument("--follow", action="store_true")
    logs_root.add_argument("--service")
    logs_root.add_argument("--tail", type=int, default=200)
    notifications = add_deployment_selector(commands.add_parser(
        "notifications", help="evaluate and inspect operational notifications"))
    notification_actions = notifications.add_subparsers(
        dest="notification_action", required=True)
    for name in ("evaluate", "list", "test"):
        item = add_local_deployment_selector(
            notification_actions.add_parser(name))
        item.add_argument("--json", action="store_true")
    notification_inspect = add_local_deployment_selector(
        notification_actions.add_parser("inspect"))
    notification_inspect.add_argument("notification_id")
    notification_inspect.add_argument("--json", action="store_true")
    notification_ack = add_local_deployment_selector(
        notification_actions.add_parser("acknowledge"))
    notification_ack.add_argument("notification_id")
    notification_ack.add_argument("--json", action="store_true")
    setup_parser = commands.add_parser(
        "setup", help="prepare a new root Docker Compose deployment")
    setup_parser.add_argument("--non-interactive", action="store_true")
    setup_parser.add_argument("--deployment-name")
    setup_parser.add_argument("--deployment-type",
                              choices=("Home Lab", "School", "Business",
                                       "MSP", "Enterprise"))
    setup_parser.add_argument("--grafana-port", type=int)
    setup_parser.add_argument("--influxdb-port", type=int)
    setup_parser.add_argument("--timezone")
    setup_parser.add_argument("--collection-interval")
    setup_parser.add_argument("--demo", action="store_true", default=None)
    setup_parser.add_argument("--start", action="store_true", default=None)
    setup_parser.add_argument("--force", action="store_true")
    setup_parser.add_argument("--health-timeout", type=int, default=180)
    demo_parser = commands.add_parser(
        "demo", help="start and seed an isolated demonstration environment")
    demo_parser.add_argument("--seed", type=int, default=1001)
    demo_parser.add_argument("--days", type=int, default=30)
    demo_parser.add_argument("--json", action="store_true")
    config_parser = commands.add_parser(
        "config", help="validate deterministic connector configuration")
    config_actions = config_parser.add_subparsers(
        dest="config_action", required=True)
    config_validate = config_actions.add_parser("validate")
    config_validate.add_argument("--json", action="store_true")
    profile_parser = commands.add_parser("profile")
    actions = profile_parser.add_subparsers(dest="action", required=True)
    actions.add_parser("list")
    create_parser = actions.add_parser("create"); create_parser.add_argument("profile")
    for name in ("validate", "status", "sites", "virtualisation-status",
                 "up", "down", "restart", "logs",
                 "init-secrets", "dashboards", "operations", "services",
                 "wallboard", "capabilities", "shell"):
        item = actions.add_parser(name); item.add_argument("profile")
    virt = actions.add_parser("virtualisation")
    virt.add_argument("profile")
    virt.add_argument("--provider", choices=("vmware", "hyperv", "proxmox"))
    virt.add_argument("--fixture", choices=("vmware", "hyperv", "proxmox"))
    collect = actions.add_parser("collect"); collect.add_argument("profile"); collect.add_argument("collector")
    args = parser.parse_args()
    try:
        runtime_registry = ConnectorMetadataRegistry.load(ROOT)
    except ValueError:
        # Test/embedded callers may replace ROOT without copying the tracked
        # catalogue. Runtime deployments still use the packaged registry.
        runtime_registry = ConnectorMetadataRegistry.load(
            Path(__file__).resolve().parents[1])
    runtime_manager = RuntimeDeploymentManager(
        ROOT, registry=runtime_registry)
    if args.group == "credentials":
        if not args.credential_action:
            print("Credential targets: grafana, ca")
            return
        deployment = runtime_manager.select(args.deployment)
        if args.credential_action == "ca":
            if args.ca_action == "add":
                result = runtime_manager.ca_add(
                    deployment, args.certificate_file)
            elif args.ca_action == "list":
                result = runtime_manager.ca_list(deployment)
            else:
                result = runtime_manager.ca_remove(
                    deployment, args.identifier)
            if args.json:
                print(json.dumps(result, indent=2, sort_keys=True))
            elif args.ca_action == "list":
                if not result:
                    print("No deployment-specific CA certificates installed.")
                for item in result:
                    print(f"{item['name']}\t{item['fingerprint']}")
            else:
                values = result if isinstance(result, list) else [result]
                for item in values:
                    verb = "Added" if args.ca_action == "add" else "Removed"
                    print(f"{verb} CA certificate "
                          f"{item['name']} ({item['fingerprint']})")
            return
        result = runtime_manager.grafana_credentials(deployment)
        if args.json:
            print(json.dumps({
                key: result[key] for key in (
                    "deployment_id", "url", "username", "password")
            }, indent=2, sort_keys=True))
        else:
            print(f"Deployment: {result['deployment_id']}")
            print(f"Grafana URL: {result['url']}")
            print(f"Username: {result['username']}")
            print(f"Password: {result['password']}")
        return
    if args.group in {"deploy", "init"}:
        verbose = deployment_verbose(args.verbose)
        def phase(number, label, operation, retry):
            print(f"[{number}/6] {label}")
            try:
                return operation()
            except subprocess.CalledProcessError as exc:
                captured = ((exc.stderr or "") + "\n" + (
                    exc.stdout or "")).strip()
                if captured:
                    print(captured[-4000:], file=sys.stderr)
                print(
                    f"ERROR: {label} failed. Retry with: {retry}",
                    file=sys.stderr)
                raise SystemExit(exc.returncode)
            except RuntimeDeploymentError as exc:
                raise RuntimeDeploymentError(
                    f"{label} failed: {exc}. Retry with: {retry}") from exc

        if args.group == "deploy" and not args.no_start:
            phase(
                1, "Checking Docker", runtime_manager.verify_docker,
                retry_command("deploy", "--verbose"))
        deployment = phase(
            2, "Creating deployment configuration",
            lambda: runtime_manager.create(
                name=args.deployment_name,
                deployment_id=args.deployment_id,
                timezone=args.timezone,
                grafana_port=args.grafana_port,
                influxdb_port=args.influxdb_port,
                listen_address=args.listen_address,
                collectors=args.collector,
                non_interactive=args.non_interactive,
                force=args.force,
                site_id=args.site_id,
                site_name=args.site_name),
            retry_command("deploy", "--verbose"))
        load_runtime_env(deployment.env_file)
        deployment_config = load_config(deployment.collectors)
        enabled_collectors = sorted(
            name for name, settings in
            (deployment_config.get("collectors") or {}).items()
            if isinstance(settings, dict) and settings.get("enabled"))
        if not args.non_interactive:
            for name in enabled_collectors:
                runtime_manager.add_collector(deployment, name)
        DashboardRegistry(
            ROOT, deployment_config,
            deployment.generated / "dashboard/managed",
            deployment.generated / "dashboard/provisioning/dashboards.yml",
            registry_validation_mode="runtime").generate()
        if args.group == "deploy" and not args.no_start:
            if args.reset_influx:
                phase(
                    3, "Resetting disposable InfluxDB telemetry",
                    lambda: runtime_manager.reset_influx(
                        deployment, non_interactive=args.non_interactive),
                    retry_command(
                        "deploy", "--force", "--reset-influx", "--verbose"))
            deployment.run_compose(
                "config", "--quiet", capture=not verbose)
            phase(
                3, "Preparing InfluxDB",
                lambda: runtime_manager.bootstrap_influx(
                    deployment, capture=not verbose,
                    non_interactive=args.non_interactive),
                retry_command("deploy", "--force", "--verbose"))
            phase(
                4, "Building ITP collectors",
                lambda: deployment.run_compose(
                    "build", capture=not verbose),
                retry_command("deploy", "--force", "--verbose"))
            phase(
                5, "Starting platform services",
                lambda: deployment.run_compose(
                    "up", "-d", "--remove-orphans",
                    capture=not verbose),
                retry_command("deploy", "--force", "--verbose"))
            print("[6/6] Verifying service health")
            deadline = time.monotonic() + 180
            healthy = False
            while time.monotonic() < deadline:
                state = deployment.run_compose(
                    "ps", "--format", "json", capture=True, check=False)
                text = state.stdout.casefold()
                if text and "unhealthy" not in text and (
                        '"running"' in text or '"state":"running"' in
                        text.replace(" ", "")):
                    healthy = True
                    break
                time.sleep(2)
            if not healthy:
                raise RuntimeDeploymentError(
                    "service-health validation failed: containers did not all "
                    "report running within 180 seconds; run ./itp deployment "
                    "list and ./itp logs --deployment " + deployment.deployment_id)
            report = DoctorEngine(
                ROOT, runtime_deployment=deployment,
                env_path=deployment.env_file,
                config_path=deployment.collectors).run()
            if report.exit_code(False):
                raise RuntimeDeploymentError(
                    "Doctor validation failed after services started; inspect "
                    f"with ./itp doctor --deployment {deployment.deployment_id} "
                    "and retry after correcting the reported failure")
        value = deployment.load()
        network = value["network"]
        print(
            "ITP deployment complete."
            if args.group == "deploy" and not args.no_start
            else "ITP deployment configuration initialized.")
        print(f"Deployment: {deployment.deployment_id} ({value['display_name']})")
        try:
            runtime_display = deployment.path.relative_to(ROOT)
        except ValueError:
            runtime_display = deployment.path
        print(f"Runtime: {runtime_display}")
        display_host = (
            "127.0.0.1" if network["listen_address"] in {"0.0.0.0", "::"}
            else network["listen_address"])
        access_note = (
            "local only" if network["listen_address"] == "127.0.0.1"
            else f"listening on {network['listen_address']}")
        print("Services:")
        print(
            f"  Grafana  http://{display_host}:{network['grafana_port']} "
            f"({access_note})")
        print(
            f"  InfluxDB http://{network['listen_address']}:"
            f"{network['influxdb_port']}")
        grafana_url = (
            f"http://{display_host}:{network['grafana_port']}")
        for line in grafana_onboarding_summary(
                runtime_manager, deployment, grafana_url,
                non_interactive=args.non_interactive):
            print(line)
        print("Dashboards:")
        print("  Infrastructure Overview is the default Grafana landing dashboard.")
        print("  Managed dashboards are generated automatically during deployment.")
        readiness = runtime_manager.collector_readiness(deployment)
        print("Collectors:")
        for item in readiness:
            if item["state"] != "disabled" or item["id"] in enabled_collectors:
                print(f"  {item['display_name']}: {item['state']}")
                if item["next_action"]:
                    print(f"    Next: {item['next_action']}")
        if not enabled_collectors:
            print("  none enabled")
        if args.group == "deploy" and not args.no_start:
            print("Next:")
            print("  ./itp doctor")
            print("  ./itp status")
            summary = report.summary
            print(
                "Doctor: "
                f"{report.overall_status} "
                f"({summary['pass']} passed, {summary['warn']} warnings, "
                f"{summary['fail']} failed)")
        else:
            print("Next: ./itp deploy")
        return
    if args.group == "deployment":
        if args.deployment_action == "list":
            result = runtime_manager.deployment_inventory()
        elif args.deployment_action == "select":
            deployment = runtime_manager.activate(args.deployment_id)
            result = {
                "deployment_id": deployment.deployment_id,
                "active": True,
            }
        else:
            deployment = runtime_manager.select(args.deployment_id)
            result = {
                **deployment.load(),
                "path": str(deployment.path),
                "secrets_path": str(deployment.secrets_dir),
            }
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        elif isinstance(result, list):
            for item in result:
                print(
                    f"{item['deployment_id']}\t{item.get('site_id') or '-'}\t"
                    f"{item['status']}\t{item['compose_project']}")
        else:
            print(yaml.safe_dump(result, sort_keys=False).rstrip())
        return
    if args.group in {"reset", "remove", "cleanup"}:
        if args.group == "reset":
            result = runtime_manager.reset(
                args.deployment, reset_influx=args.reset_influx, yes=args.yes)
        elif args.group == "remove":
            result = runtime_manager.remove(
                args.deployment, remove_telemetry=args.remove_telemetry,
                yes=args.yes)
        else:
            result = runtime_manager.cleanup(
                yes=args.yes, deployment_id=args.deployment)
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(yaml.safe_dump(result, sort_keys=False).rstrip())
        return
    if args.group == "collector":
        deployment = runtime_manager.select(args.deployment)
        load_deployment_environment(deployment)
        if args.runtime_collector_action == "list":
            config = yaml.safe_load(deployment.collectors.read_text()) or {}
            result = [{
                **item.to_dict(),
                "enabled": bool((config.get("collectors") or {}).get(
                    item.id, {}).get("enabled")),
            } for item in runtime_manager.registry.all()]
        elif args.runtime_collector_action == "add":
            item = runtime_manager.add_collector(
                deployment, args.collector)
            result = {"collector": item.id, "enabled": True,
                      "configuration": str(deployment.collectors)}
        elif args.runtime_collector_action == "remove":
            item = runtime_manager.remove_collector(
                deployment, args.collector)
            result = {"collector": item.id, "enabled": False,
                      "configuration": str(deployment.collectors)}
        elif args.runtime_collector_action == "test":
            readiness = {
                item["id"]: item
                for item in runtime_manager.collector_readiness(deployment)}
            completed = deployment.run_compose(
                "run", "--rm", "collector", "python", "-m", "collectors",
                "--config", "/app/config.yml", "inspect", args.collector,
                "--json", capture=True, check=False)
            payload = last_json_object(
                (completed.stdout or "").strip()) if completed.stdout else {}
            result = {
                "collector": args.collector,
                "success": completed.returncode == 0,
                "tls_verification": readiness.get(
                    args.collector, {}).get("tls_verification"),
                **(payload if isinstance(payload, dict) else {}),
            }
        else:
            config = load_config(deployment.collectors)
            result = runtime_collection(
                runtime_manager, deployment, config, args.collector)
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        elif isinstance(result, list):
            for item in result:
                print(f"{item['id']}\t{'enabled' if item['enabled'] else 'disabled'}")
        else:
            print(json.dumps(result, indent=2, sort_keys=True))
        return
    if args.group == "dashboard":
        deployment = runtime_manager.select(args.deployment)
        load_runtime_env(deployment.env_file)
        config = load_config(deployment.collectors)
        result = DashboardRegistry(
            ROOT, config, deployment.generated / "dashboard/managed",
            deployment.generated / "dashboard/provisioning/dashboards.yml",
            registry_validation_mode="runtime").generate()
        dashboards = sorted(
            result["dashboards"],
            key=lambda item: (item["folder"], item["uid"]))
        print(f"Generated {len(dashboards)} managed dashboards for "
              f"deployment {deployment.deployment_id}:")
        for item in dashboards:
            path = next(
                deployment.generated.glob(
                    f"dashboard/managed/*/{item['uid']}.json"),
                None)
            title = item["uid"]
            if path is not None:
                title = json.loads(path.read_text()).get("title", title)
            print(f"  {title} (Grafana folder: {item['folder']})")
        print("Grafana provisions these dashboards into their managed folders "
              "automatically within 30 seconds; no restart is required.")
        return
    if args.group == "update":
        deployment = runtime_manager.select(args.deployment)
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=ROOT, check=True,
            text=True, capture_output=True).stdout.strip()
        if dirty:
            raise RuntimeDeploymentError(
                "source working tree is dirty; preserve or discard changes before update")
        subprocess.run(["git", "pull", "--ff-only"], cwd=ROOT, check=True)
        deployment.run_compose("build", "--pull")
        deployment.run_compose("up", "-d", "--remove-orphans")
        print("Update complete; run ./itp doctor")
        return
    if args.group == "help":
        parser.print_help()
        return
    if args.group == "setup":
        def setup_provision():
            load_root_env()
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                setup_config = load_config(ROOT / "discovery/config.yml")
            runtime = Path(os.getenv("ITP_RUNTIME_DIR", ROOT / "runtime"))
            compose_runtime = DockerCompose(ROOT)
            return Provisioner(
                ROOT, setup_config, runtime, compose_runtime).provision()

        def setup_start():
            load_root_env()
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                setup_config = load_config(ROOT / "discovery/config.yml")
            runtime = Path(os.getenv("ITP_RUNTIME_DIR", ROOT / "runtime"))
            compose_runtime = DockerCompose(ROOT)
            return StackLifecycle(
                compose_runtime,
                Provisioner(ROOT, setup_config, runtime, compose_runtime)).start()

        BootstrapWizard(
            ROOT, provision_fn=setup_provision, start_fn=setup_start,
            demo_fn=run_demo).run(SetupOptions(
            non_interactive=args.non_interactive,
            deployment_name=args.deployment_name,
            deployment_type=args.deployment_type,
            grafana_port=args.grafana_port,
            influxdb_port=args.influxdb_port,
            timezone=args.timezone,
            collection_interval=args.collection_interval,
            demo=args.demo,
            start=args.start,
            force=args.force,
            health_timeout=args.health_timeout))
        return
    if args.group == "demo":
        result = run_demo(args.seed, args.days)
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print("ITP demo environment is ready.")
            print(f"Telemetry: {result['points_written']} points over "
                  f"{result['days']} days")
            print(f"Pipeline runs: {result['pipeline_runs']}")
            print(f"Notifications: {result['notifications']}")
            print("Grafana: http://localhost:3300")
            print("Runtime: runtime/demo")
        return
    if args.group == "config":
        explicit = dict(os.environ)
        registry = ConnectorMetadataRegistry.load(ROOT)
        result = ConfigurationResolver.root(
            ROOT, registry, process_environment=explicit).evaluate()
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print("Configuration: " + (
                "ready" if result["ready"] else "not ready"))
            for connector in result["connectors"]:
                if not connector["enabled"]:
                    continue
                print(
                    f"[{'PASS' if connector['ready'] else 'FAIL'}] "
                    f"{connector['display_name']}: enabled")
                for setting in connector["settings"]:
                    alias = (
                        f" deprecated={setting['deprecated_alias']}"
                        if setting["deprecated_alias"] else "")
                    print(
                        f"  {setting['name']}: {setting['status']} "
                        f"source={setting['source']} "
                        f"secret={'yes' if setting['secret'] else 'no'}"
                        f"{alias}")
                print(
                    f"  TLS verification: "
                    f"{connector['tls_verification']}")
                print(f"  Site: {connector['site'] or 'not configured'}")
        if not result["ready"]:
            raise SystemExit(1)
        return
    if args.group == "doctor":
        doctor_paths = {}
        selected = resolve_runtime_deployment(
            runtime_manager, args.deployment)
        if selected:
            load_deployment_environment(selected)
            doctor_paths = {
                "env_path": selected.env_file,
                "config_path": selected.collectors,
            }
        report = DoctorEngine(
            ROOT, offline=args.offline, platform_only=args.platform_only,
            connectors_only=args.connectors_only, connector=args.connector,
            runtime_deployment=selected,
            **doctor_paths).run()
        print(render_json(report, args.strict) if args.json
              else render_human(report, args.strict))
        raise SystemExit(report.exit_code(args.strict))

    if args.group in {
            "collect", "status", "daemon", "notifications",
            "start", "stop", "restart", "logs"}:
        selected = resolve_runtime_deployment(
            runtime_manager, args.deployment)
        if selected:
            load_deployment_environment(selected)
            config = load_config(selected.collectors)
            runtime_dir = selected.path
            if args.group in {"start", "stop", "restart"}:
                runtime_stack_action(selected, args.group)
                result = {
                    "deployment": selected.deployment_id,
                    "action": args.group,
                          "success": True}
                print(json.dumps(result, indent=2) if args.json else
                      f"Deployment {selected.deployment_id}: "
                      f"{args.group} complete")
                return
            if args.group == "logs":
                service = args.service or args.service_positional
                supported = {
                    "collector", "discovery", "telegraf", "grafana",
                    "influxdb3-core"}
                if service and service not in supported:
                    raise RuntimeDeploymentError(
                        f"unknown log service {service}; choose one of: "
                        + ", ".join(sorted(supported)))
                command = ["logs", "--tail", str(args.tail)]
                if args.follow:
                    command.append("--follow")
                if service:
                    command.append(service)
                selected.run_compose(*command, capture=False)
                return
            if args.group == "collect":
                result = runtime_collection(
                    runtime_manager, selected, config)
                print(json.dumps(result, indent=2, sort_keys=True)
                      if args.json else render_collect(result))
                if result["summary"]["overall"] in {"failed", "partial"}:
                    raise SystemExit(1)
                return
            if args.group == "status":
                result = OperatorStatusEngine(
                    ROOT, config, runtime_dir=runtime_dir,
                    readiness=runtime_manager.collector_readiness(
                        selected)).run()
                payload = selected.run_compose(
                    "ps", "--format", "json", capture=True, check=False)
                result["deployment_id"] = selected.deployment_id
                result["configuration"] = str(selected.manifest)
                result["containers"] = payload.stdout.strip()
                result["shared_collector_service"] = {
                    "service": "collector",
                    "container": f"itp-{selected.deployment_id}-collector-1",
                    "state": compose_service_state(
                        payload.stdout, "collector"),
                }
                result["discovery"] = {
                    "enabled": bool(
                        (config.get("discovery") or {}).get("enabled", True)),
                    "state": (
                        "enabled" if
                        (config.get("discovery") or {}).get("enabled", True)
                        else "disabled"),
                }
                network = selected.load()["network"]
                result["grafana"] = (
                    f"http://{network['listen_address']}:"
                    f"{network['grafana_port']}")
                if args.json:
                    print(json.dumps(result, indent=2, sort_keys=True))
                else:
                    print(render_status(result))
                    print(
                        "Shared collector service: "
                        f"{result['shared_collector_service']['state']}")
                    print(
                        "Discovery: "
                        f"{result['discovery']['state']} "
                        "(independent service)")
                    print(
                        "Containers: "
                        f"{'running' if result['containers'] else 'stopped'}")
                return
            if args.group == "notifications":
                notification_config = config.get("notifications") or {}
                channel_registry = NotificationChannelRegistry(
                    output=lambda value: print(value, file=sys.stderr))
                engine = NotificationEngine(
                    runtime_dir, notification_config,
                    channel_registry=channel_registry)
                store = NotificationStore(runtime_dir)
                if args.notification_action == "evaluate":
                    status_result = OperatorStatusEngine(
                        ROOT, config, runtime_dir=runtime_dir,
                        readiness=runtime_manager.collector_readiness(
                            selected)).run()
                    doctor_result = DoctorEngine(
                        ROOT, runtime_deployment=selected,
                        env_path=selected.env_file,
                        config_path=selected.collectors).run()
                    result = engine.evaluate(status_result, doctor_result)
                elif args.notification_action == "test":
                    result = engine.test()
                elif args.notification_action == "list":
                    state = store.read()
                    result = {
                        "enabled": engine.enabled,
                        "active": sorted(
                            state["active"].values(),
                            key=lambda value: value["id"]),
                        "events": state["events"],
                        "deliveries": state["deliveries"],
                    }
                elif args.notification_action == "inspect":
                    result = store.find(args.notification_id)
                    if result is None:
                        raise ValueError(
                            "notification not found: "
                            f"{args.notification_id}")
                else:
                    result = store.acknowledge(
                        args.notification_id,
                        datetime.now(timezone.utc).isoformat().replace(
                            "+00:00", "Z"))
                    if result is None:
                        raise ValueError(
                            "notification not found: "
                            f"{args.notification_id}")
                if args.json:
                    print(json.dumps(result, indent=2, sort_keys=True))
                elif args.notification_action == "list":
                    print(
                        f"Notifications: {len(result['active'])} active, "
                        f"{len(result['events'])} total")
                    for value in result["active"]:
                        print(
                            f"[{value['severity'].upper()}] {value['id']} "
                            f"{value['title']} "
                            f"occurrences={value['occurrence_count']}")
                elif args.notification_action == "evaluate":
                    print(
                        "Notification evaluation: "
                        f"{result['active_count']} active, "
                        f"{len(result['new_events'])} new, "
                        f"{len(result['recoveries'])} recovered")
                elif args.notification_action == "test":
                    print(
                        "Test notification: "
                        f"{len(result['deliveries'])} delivery attempt(s)")
                else:
                    print(
                        f"Notification: {result['id']}\n"
                        f"Severity: {result['severity']}\n"
                        f"Title: {result['title']}\n"
                        f"Summary: {result['summary']}\n"
                        f"Acknowledged: "
                        f"{result.get('acknowledged', False)}")
                return
            if args.once:
                result = OperatorDaemon(
                    ROOT, config, runtime_dir=runtime_dir).run(once=True)
                print(render_collect(result))
                if result["summary"]["overall"] in {"failed", "partial"}:
                    raise SystemExit(1)
            elif args.foreground:
                OperatorDaemon(
                    ROOT, config, runtime_dir=runtime_dir).run()
            else:
                start_background(
                    Path(__file__).resolve(), runtime_dir, output=print,
                    arguments=(
                        "--deployment", selected.deployment_id))
            return
        load_root_env()
        # These commands intentionally operate on the backwards-compatible
        # root deployment, so its profile migration warning is not actionable.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            config = load_config(ROOT / "discovery/config.yml")
        runtime_dir = Path(os.getenv("ITP_RUNTIME_DIR", ROOT / "runtime"))
        compose_runtime = DockerCompose(ROOT)
        lifecycle = StackLifecycle(
            compose_runtime,
            Provisioner(ROOT, config, runtime_dir, compose_runtime))
        if args.group in {"start", "stop", "restart"}:
            result = getattr(lifecycle, args.group)()
            if args.json:
                print(json.dumps(result, indent=2, sort_keys=True))
            else:
                print(
                    f"Stack {args.group}: "
                    f"{result['stack']['compose_project_state']}")
                for service in result["stack"]["services"]:
                    print(
                        f"  {service['service']}: {service['state']}"
                        + (f"/{service['health']}" if service["health"] else ""))
        elif args.group == "logs":
            lifecycle.logs(
                follow=args.follow,
                service=args.service or args.service_positional,
                tail=args.tail)
        elif args.group == "notifications":
            notification_config = config.get("notifications") or {}
            channel_registry = NotificationChannelRegistry(
                output=lambda value: print(value, file=sys.stderr))
            engine = NotificationEngine(
                runtime_dir, notification_config,
                channel_registry=channel_registry)
            store = NotificationStore(runtime_dir)
            if args.notification_action == "evaluate":
                status_result = OperatorStatusEngine(
                    ROOT, config, runtime_dir=runtime_dir).run()
                doctor_result = DoctorEngine(ROOT).run()
                result = engine.evaluate(status_result, doctor_result)
            elif args.notification_action == "test":
                result = engine.test()
            elif args.notification_action == "list":
                state = store.read()
                result = {
                    "enabled": engine.enabled,
                    "active": sorted(
                        state["active"].values(),
                        key=lambda value: value["id"]),
                    "events": state["events"],
                    "deliveries": state["deliveries"],
                }
            elif args.notification_action == "inspect":
                result = store.find(args.notification_id)
                if result is None:
                    raise ValueError(
                        f"notification not found: {args.notification_id}")
            else:
                result = store.acknowledge(
                    args.notification_id,
                    datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
                if result is None:
                    raise ValueError(
                        f"notification not found: {args.notification_id}")
            if args.json:
                print(json.dumps(result, indent=2, sort_keys=True))
            elif args.notification_action == "list":
                print(
                    f"Notifications: {len(result['active'])} active, "
                    f"{len(result['events'])} total")
                for value in result["active"]:
                    print(
                        f"[{value['severity'].upper()}] {value['id']} "
                        f"{value['title']} occurrences={value['occurrence_count']}")
            elif args.notification_action == "evaluate":
                print(
                    f"Notification evaluation: {result['active_count']} active, "
                    f"{len(result['new_events'])} new, "
                    f"{len(result['recoveries'])} recovered")
            elif args.notification_action == "test":
                print(
                    "Test notification: "
                    f"{len(result['deliveries'])} delivery attempt(s)")
            else:
                print(
                    f"Notification: {result['id']}\n"
                    f"Severity: {result['severity']}\n"
                    f"Title: {result['title']}\n"
                    f"Summary: {result['summary']}\n"
                    f"Acknowledged: {result.get('acknowledged', False)}")
        elif args.group == "collect":
            result = OperatorCollectEngine(
                ROOT, config, runtime_dir=runtime_dir).run()
            print(json.dumps(result, indent=2, sort_keys=True)
                  if args.json else render_collect(result))
            if result["summary"]["overall"] in {"failed", "partial"}:
                raise SystemExit(1)
        elif args.group == "status":
            result = OperatorStatusEngine(
                ROOT, config, runtime_dir=runtime_dir).run()
            result["stack"] = lifecycle.status()
            result["stack"]["daemon"] = result["daemon"]
            print(json.dumps(result, indent=2, sort_keys=True)
                  if args.json else render_status(result) + "\n"
                  + f"Stack: {result['stack']['compose_project_state']}\n"
                  + f"InfluxDB: {result['stack']['influxdb']}\n"
                  + f"Grafana: {result['stack']['grafana']}\n"
                  + "Provisioning: "
                  + result["stack"]["provisioning"]["status"] + "\n"
                  + "Dashboard packs: "
                  + (", ".join(
                      f"{value['id']}@{value['version']}"
                      for value in result["stack"]["dashboard_packs"])
                     or "none"))
        elif args.once:
            result = OperatorDaemon(
                ROOT, config, runtime_dir=runtime_dir).run(once=True)
            print(render_collect(result))
            if result["summary"]["overall"] in {"failed", "partial"}:
                raise SystemExit(1)
        elif args.foreground:
            OperatorDaemon(ROOT, config, runtime_dir=runtime_dir).run()
        else:
            start_background(
                Path(__file__).resolve(), runtime_dir, output=print)
        return

    if args.group == "connectors":
        registry = ConnectorMetadataRegistry.load(ROOT)
        if args.connector_action == "list":
            if args.json:
                print(json.dumps(registry.to_dict(), indent=2, sort_keys=True))
            else:
                print("ID\tName\tDomains\tStatus\tSetup\tValidation\tDocumentation")
                for connector in registry.all():
                    print(
                        f"{connector.id}\t{connector.display_name}\t"
                        f"{','.join(connector.domains)}\t"
                        f"{connector.implementation_status}\t"
                        f"{'guided' if connector.guided_setup else connector.configuration_mode}\t"
                        f"{'yes' if connector.capabilities['validation'] else 'no'}\t"
                        f"{connector.documentation}")
            return
        try:
            connector = registry.get(args.connector)
        except KeyError as exc:
            raise ValueError(str(exc).strip("'")) from exc
        if args.json:
            print(json.dumps(connector.to_dict(), indent=2, sort_keys=True))
        else:
            print(f"Connector: {connector.display_name} ({connector.id})")
            print(f"Vendor: {connector.vendor}")
            print("Domains: " + ", ".join(connector.domains))
            print(f"Support: {connector.implementation_status}")
            print("Setup: " + (
                "guided" if connector.guided_setup
                else f"{connector.configuration_mode} (manual-only)"))
            print("Validation: " + (
                "available" if connector.capabilities["validation"]
                else "not available"))
            print("Doctor: " + (
                "available" if connector.capabilities["doctor"]
                else "not available"))
            print("Status: " + (
                "available" if connector.capabilities["status"]
                else "not available"))
            print(f"Documentation: {connector.documentation}")
            print(f"Notes: {connector.notes}")
        return

    if args.action == "list":
        for value in discover_profiles(ROOT):
            print(f"{value.id}\t{value.name}\t{value.environment}")
        return
    if args.action == "create":
        create(args.profile)
        return
    explicit_environment = dict(os.environ)
    value = profile(args.profile)
    if args.action == "validate":
        validate(value, process_environment=explicit_environment)
    elif args.action == "status": status(value)
    elif args.action == "sites": sites_status(value)
    elif args.action == "virtualisation":
        virtualisation(value, fixture=args.fixture, provider_name=args.provider)
    elif args.action == "virtualisation-status": virtualisation_status(value)
    elif args.action == "init-secrets": init_secrets(value)
    elif args.action in {"up", "down", "restart"}:
        describe(value)
        if args.action == "up":
            preflight_start(value)
            bootstrap_influx(value)
            validate(value)
            generate_profile_dashboards(value)
            if value.deployment_mode == "cluster_member":
                compose(value, "up", "-d", "--build", "--no-deps",
                        "collector", "discovery")
            else:
                compose(value, "up", "-d", "--build")
        elif args.action == "down":
            if value.deployment_mode == "cluster_member":
                compose(value, "stop", "collector", "discovery")
            else:
                compose(value, "down")
        else:
            preflight_start(value)
            bootstrap_influx(value)
            validate(value)
            generate_profile_dashboards(value)
            if value.deployment_mode == "cluster_member":
                compose(value, "up", "-d", "--build", "--no-deps",
                        "--force-recreate", "collector", "discovery")
            else:
                compose(value, "up", "-d", "--build", "--remove-orphans")
    elif args.action == "logs":
        compose(value, "logs", "--tail=200", "-f")
    elif args.action == "shell":
        compose(value, "exec", "collector", "/bin/sh")
    elif args.action == "collect":
        compose(value, "exec", "collector", "python", "-m", "collectors",
                "--profile", value.id, "collect", args.collector)
    elif args.action in {"dashboards", "operations", "services", "wallboard",
                         "capabilities"}:
        compose(value, "exec", "collector", "python", "-m", "collectors",
                "--profile", value.id, args.action, "generate")


if __name__ == "__main__":
    try:
        main()
    except DoctorUsageError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
    except DoctorFatalError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(3)
    except (DaemonAlreadyRunningError, DeploymentError, ProfileError, SetupError,
            subprocess.CalledProcessError,
            OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
