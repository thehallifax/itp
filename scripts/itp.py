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
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analysis.dashboards import DashboardRegistry
from analysis.sites import SiteRegistry
from analysis.virtualisation import VirtualisationEngine
from analysis.virtualisation.config import validate_virtualisation
from analysis.virtualisation.renderer import render as render_virtualisation
from analysis.virtualisation.telemetry import points as virtualisation_points
from collectors.vmware.client import VMwareClient
from collectors.proxmox.client import ProxmoxClient
from collectors.hyperv.runner import LocalPowerShellRunner
from collectors.writer import InfluxWriter
from collectors.config import load_config
from itp_profiles import DeploymentProfile, ProfileError, discover_profiles
from itp_profiles.profiles import PLACEHOLDERS, PROFILE_ID
from itp_profiles.setup import BootstrapWizard, SetupError, SetupOptions


SECRET_REQUIREMENTS = {
    "snmp": ("NETWORK_SNMP_COMMUNITY",),
    "mist": ("MIST_ORG_ID", "MIST_API_TOKEN"),
    "fortigate": ("FORTIGATE_HOST", "FORTIGATE_API_TOKEN"),
    "paloalto": ("PALOALTO_API_KEY",),
}


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


def profile(value, *, secrets=True):
    load_root_env()
    result = DeploymentProfile.load(value, ROOT)
    result.activate(load_secrets=secrets)
    return result


def describe(value):
    print(f"Profile: {value.id} ({value.name})")
    print(f"Configuration: {value.paths.discovery}")
    print(f"Sites: {value.paths.sites}")
    print(f"Secrets: {value.paths.secrets}")
    print(f"Runtime: {value.paths.runtime}")
    print(f"Compose project: {value.compose_project}")


def compose(value, *arguments, capture=False):
    environment = {**os.environ, **value.env()}
    return subprocess.run(["docker", "compose", *arguments], cwd=ROOT, env=environment,
                          check=True, text=True, capture_output=capture)


def port_available(port):
    with socket.socket() as connection:
        connection.settimeout(0.25)
        return connection.connect_ex(("127.0.0.1", port)) != 0


def required_secrets(config):
    required = {"INFLUXDB_TOKEN"}
    for name, settings in config.get("collectors", {}).items():
        if not isinstance(settings, dict) or not settings.get("enabled"):
            continue
        required.update(SECRET_REQUIREMENTS.get(name, ()))
        for key in ("api_key_env", "token_env"):
            if settings.get(key):
                required.add(str(settings[key]))
    return sorted(required)


def validate(value):
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
    check("Manifest", True, f"deployment_id={value.deployment_id} timezone={value.timezone}")
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
    occupied = [str(port) for port in (value.grafana_port, value.influxdb_port)
                if not port_available(port)]
    check("Ports", not occupied or _project_running(value),
          "available" if not occupied else "in use: " + ", ".join(occupied))
    if not all(checks):
        raise ProfileError(f"profile {value.id} validation failed")
    print(f"Status: valid ({len(checks)} checks)")


def _project_running(value):
    try:
        return bool(compose(value, "ps", "-q", capture=True).stdout.strip())
    except Exception:
        return False


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
    print(f"Deployment model: {sites.deployment_model}")
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
    print(f"InfluxDB: http://localhost:{value.influxdb_port}")
    print(f"Grafana: http://localhost:{value.grafana_port}")


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
                username = os.getenv(endpoint.get("username_env", "VMWARE_USERNAME"), "")
                password = os.getenv(endpoint.get("password_env", "VMWARE_PASSWORD"), "")
                if not username or not password:
                    raise ProfileError("VMware read-only credentials are unavailable")
                verify = endpoint.get("ca_bundle") or endpoint.get("verify_tls", True)
                contract = VMwareClient(endpoint["endpoint"], username, password,
                    verify=verify, timeout=float(endpoint.get("timeout_seconds", 20))).collect()
            elif selected == "proxmox":
                token_id = os.getenv(endpoint.get("token_id_env", "PROXMOX_TOKEN_ID"), "")
                token_secret = os.getenv(
                    endpoint.get("token_secret_env", "PROXMOX_TOKEN_SECRET"), "")
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
        written = InfluxWriter().write(virtualisation_points(result))
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
        target.chmod(0o600)
        created.append(target)
    print("Created: " + (", ".join(str(path) for path in created) or "none; existing files preserved"))


def bootstrap_influx(value):
    """Create the first profile-local InfluxDB admin token without exposing it."""
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
        cwd=ROOT, env=environment, check=True, text=True, stdout=subprocess.DEVNULL)
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


def main():
    parser = argparse.ArgumentParser(prog="./itp", description=__doc__)
    commands = parser.add_subparsers(dest="group", required=True)
    commands.add_parser("help")
    setup_parser = commands.add_parser(
        "setup", help="prepare a new root Docker Compose deployment")
    setup_parser.add_argument("--non-interactive", action="store_true")
    setup_parser.add_argument("--deployment-name")
    setup_parser.add_argument("--deployment-type",
                              choices=("Home Lab", "School", "Business",
                                       "MSP", "Enterprise"))
    setup_parser.add_argument("--grafana-port", type=int)
    setup_parser.add_argument("--start", action="store_true", default=None)
    setup_parser.add_argument("--force", action="store_true")
    setup_parser.add_argument("--health-timeout", type=int, default=180)
    profile_parser = commands.add_parser("profile")
    actions = profile_parser.add_subparsers(dest="action", required=True)
    actions.add_parser("list")
    create_parser = actions.add_parser("create"); create_parser.add_argument("profile")
    for name in ("validate", "status", "sites", "virtualisation-status",
                 "up", "down", "restart", "logs",
                 "init-secrets", "dashboards", "operations", "services",
                 "wallboard", "shell"):
        item = actions.add_parser(name); item.add_argument("profile")
    virt = actions.add_parser("virtualisation")
    virt.add_argument("profile")
    virt.add_argument("--provider", choices=("vmware", "hyperv", "proxmox"))
    virt.add_argument("--fixture", choices=("vmware", "hyperv", "proxmox"))
    collect = actions.add_parser("collect"); collect.add_argument("profile"); collect.add_argument("collector")
    args = parser.parse_args()
    if args.group == "help":
        parser.print_help()
        return
    if args.group == "setup":
        BootstrapWizard(ROOT).run(SetupOptions(
            non_interactive=args.non_interactive,
            deployment_name=args.deployment_name,
            deployment_type=args.deployment_type,
            grafana_port=args.grafana_port,
            start=args.start,
            force=args.force,
            health_timeout=args.health_timeout))
        return
    if args.action == "list":
        for value in discover_profiles(ROOT):
            print(f"{value.id}\t{value.name}\t{value.environment}")
        return
    if args.action == "create":
        create(args.profile)
        return
    value = profile(args.profile)
    if args.action == "validate": validate(value)
    elif args.action == "status": status(value)
    elif args.action == "sites": sites_status(value)
    elif args.action == "virtualisation":
        virtualisation(value, fixture=args.fixture, provider_name=args.provider)
    elif args.action == "virtualisation-status": virtualisation_status(value)
    elif args.action == "init-secrets": init_secrets(value)
    elif args.action in {"up", "down", "restart"}:
        describe(value)
        if args.action == "up":
            bootstrap_influx(value)
            validate(value); compose(value, "up", "-d", "--build")
        elif args.action == "down": compose(value, "down")
        else:
            bootstrap_influx(value)
            validate(value); compose(value, "up", "-d", "--build", "--remove-orphans")
    elif args.action == "logs":
        compose(value, "logs", "--tail=200", "-f")
    elif args.action == "shell":
        compose(value, "exec", "collector", "/bin/sh")
    elif args.action == "collect":
        compose(value, "exec", "collector", "python", "-m", "collectors",
                "--profile", value.id, "collect", args.collector)
    elif args.action in {"dashboards", "operations", "services", "wallboard"}:
        compose(value, "exec", "collector", "python", "-m", "collectors",
                "--profile", value.id, args.action, "generate")


if __name__ == "__main__":
    try:
        main()
    except (ProfileError, SetupError, subprocess.CalledProcessError,
            OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
