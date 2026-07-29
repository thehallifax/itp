"""Strict, repository-rooted ITP deployment profiles."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from .identity import IdentityError, IdentityResolver, validate_id


PROFILE_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62})$")
PLACEHOLDERS = {"", "change_me", "changeme", "replace_me", "example", "placeholder"}


class ProfileError(ValueError):
    pass


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


@dataclass(frozen=True)
class ProfilePaths:
    root: Path
    profile_root: Path
    manifest: Path
    discovery: Path
    sites: Path
    dashboards: Path
    secrets: Path
    runtime: Path
    canonical_sites: Path

    @property
    def inventory(self): return self.runtime / "inventory"

    @property
    def infrastructure(self): return self.runtime / "infrastructure"

    @property
    def operations(self): return self.runtime / "operations"

    @property
    def services(self): return self.runtime / "services"

    @property
    def sites_runtime(self): return self.runtime / "sites"

    @property
    def dashboard_runtime(self): return self.runtime / "dashboard"

    @property
    def managed_dashboards(self): return self.dashboard_runtime / "managed"

    @property
    def connectors_local(self):
        candidate = self.profile_root / "connectors.local.yml"
        return candidate if candidate.is_file() else \
            self.root / "config/connectors.local.example.yml"

    @property
    def logs(self): return self.runtime / "logs"

    def create_runtime(self):
        for path in (self.inventory, self.infrastructure, self.operations, self.services,
                     self.sites_runtime, self.dashboard_runtime, self.logs):
            path.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class DeploymentProfile:
    id: str
    name: str
    environment: str
    timezone: str
    runtime_mode: str
    deployment_id: str
    customer_id: str
    deployment_mode: str
    cluster_id: str
    shared_grafana_url: str
    shared_influxdb_url: str
    influx_bucket: str
    influx_org: str
    folder_prefix: str
    provisioning_namespace: str
    grafana_port: int
    influxdb_port: int
    paths: ProfilePaths
    raw: dict

    @classmethod
    def load(cls, profile_id: str, root=None):
        root = Path(root or Path(__file__).resolve().parents[1]).resolve()
        if not PROFILE_ID.fullmatch(str(profile_id or "")):
            raise ProfileError("profile ID must be lowercase and filesystem-safe")
        manifest = root / "profiles" / profile_id / "profile.yml"
        try:
            raw = yaml.safe_load(manifest.read_text())
        except (OSError, yaml.YAMLError) as exc:
            raise ProfileError(f"unable to load profile {profile_id}: {exc}") from exc
        if not isinstance(raw, dict) or not isinstance(raw.get("profile"), dict):
            raise ProfileError(f"profile manifest {manifest} must contain a profile mapping")
        identity = raw["profile"]
        declared = str(identity.get("id") or "")
        if declared != profile_id:
            raise ProfileError(f"profile directory {profile_id} declares mismatched ID {declared!r}")
        name = str(identity.get("name") or "").strip()
        timezone = str(identity.get("timezone") or "").strip()
        runtime_mode = str(identity.get("runtime_mode") or "central").strip().lower()
        if not name or not timezone:
            raise ProfileError("profile name and timezone are required")
        if runtime_mode not in {"central", "edge"}:
            raise ProfileError("profile runtime_mode must be central or edge")
        try:
            ZoneInfo(timezone)
        except Exception as exc:
            raise ProfileError(f"profile timezone is invalid: {timezone}") from exc
        path_values = raw.get("paths") or {}
        required = ("discovery_config", "sites_config", "dashboards_config",
                    "secrets_dir", "runtime_dir")
        if any(not path_values.get(key) for key in required):
            raise ProfileError("profile paths must define discovery, sites, dashboards, secrets, and runtime")

        def resolved(key, approved_root):
            value = Path(str(path_values[key]))
            if value.is_absolute():
                raise ProfileError(f"profile path {key} must be relative to the repository root")
            path = (root / value).resolve()
            if not _within(path, approved_root):
                raise ProfileError(f"profile path {key} escapes its approved root")
            return path

        profile_root = (root / "profiles" / profile_id).resolve()
        discovery = resolved("discovery_config", profile_root)
        canonical_sites = resolved("sites_config", profile_root)
        sites = canonical_sites
        local_sites = sites.with_name(f"{sites.stem}.local{sites.suffix}")
        if local_sites.is_file():
            cls._validate_site_overlay(canonical_sites, local_sites)
            sites = local_sites.resolve()
        dashboards = resolved("dashboards_config", profile_root)
        secrets = resolved("secrets_dir", root / "secrets")
        runtime = resolved("runtime_dir", root / "runtime")
        for label, path in (("discovery", discovery), ("sites", sites), ("dashboards", dashboards)):
            if not path.is_file():
                raise ProfileError(f"profile {profile_id} is missing required {label} file: {path}")
        telemetry = raw.get("telemetry") or {}
        deployment_id = str(telemetry.get("deployment_id") or "")
        if deployment_id != profile_id:
            raise ProfileError("telemetry.deployment_id must equal the stable profile ID")
        influx_bucket = str(telemetry.get("influx_bucket") or "").strip()
        influx_org = str(telemetry.get("influx_org") or "").strip()
        if not influx_bucket or not influx_org:
            raise ProfileError("telemetry influx_bucket and influx_org are required")
        identity_config = raw.get("identity") or {}
        customer_id = str(identity_config.get("customer_id") or profile_id)
        try:
            validate_id("customer_id", customer_id)
            IdentityResolver.from_sites_file(
                deployment_id, customer_id, sites, customer_name=name)
        except IdentityError as exc:
            raise ProfileError(str(exc)) from exc
        deployment = raw.get("deployment") or {}
        deployment_mode = str(
            deployment.get("mode") or "standalone").strip().lower()
        if deployment_mode not in {"standalone", "cluster_member"}:
            raise ProfileError(
                "deployment.mode must be standalone or cluster_member")
        cluster_id = str(deployment.get("cluster_id") or "").strip()
        shared = deployment.get("shared_services") or {}
        shared_grafana = str(shared.get("grafana_url") or "").strip()
        shared_influxdb = str(shared.get("influxdb_url") or "").strip()
        if deployment_mode == "cluster_member":
            if not cluster_id:
                raise ProfileError(
                    "cluster-member deployment requires deployment.cluster_id")
            try:
                validate_id("cluster_id", cluster_id)
            except IdentityError as exc:
                raise ProfileError(str(exc)) from exc
            if not shared_grafana or not shared_influxdb:
                raise ProfileError(
                    "cluster-member deployment requires explicit shared "
                    "Grafana and InfluxDB endpoints")
        grafana = raw.get("grafana") or {}
        folder_prefix = str(grafana.get("folder_prefix") or "").strip()
        namespace = str(grafana.get("provisioning_namespace") or "").strip()
        if not folder_prefix or namespace != profile_id:
            raise ProfileError("Grafana folder prefix is required and namespace must equal profile ID")
        ports = raw.get("ports") or {}
        values = [int(ports.get("grafana", 3000)), int(ports.get("influxdb", 8181))]
        if any(value < 1 or value > 65535 for value in values) or values[0] == values[1]:
            raise ProfileError("profile ports must be distinct values between 1 and 65535")
        paths = ProfilePaths(
            root, profile_root, manifest, discovery, sites, dashboards,
            secrets, runtime, canonical_sites)
        return cls(profile_id, name, str(identity.get("environment") or "production"),
                   timezone, runtime_mode, deployment_id, customer_id,
                   deployment_mode, cluster_id, shared_grafana,
                   shared_influxdb, influx_bucket, influx_org, folder_prefix,
                   namespace, values[0], values[1], paths, raw)

    @staticmethod
    def _validate_site_overlay(canonical_path, overlay_path):
        """Local overlays may alter presentation only, never site identity."""
        try:
            canonical = yaml.safe_load(Path(canonical_path).read_text()) or {}
            overlay = yaml.safe_load(Path(overlay_path).read_text()) or {}
        except yaml.YAMLError as exc:
            raise ProfileError("site metadata overlay is invalid YAML") from exc
        base = {str(value.get("id")): value
                for value in canonical.get("sites", [])}
        local = {str(value.get("id")): value
                 for value in overlay.get("sites", [])}
        if set(base) != set(local):
            raise ProfileError(
                "sites.local.yml must preserve the canonical site ID set")
        allowed = {"id", "display_name", "aliases"}
        for site_id, value in local.items():
            unsupported = set(value) - allowed
            if unsupported:
                raise ProfileError(
                    f"site overlay {site_id} changes canonical fields: "
                    + ", ".join(sorted(unsupported)))

    @property
    def compose_project(self):
        return f"itp-{self.id}"

    def env(self):
        return {
            "ITP_PROFILE": self.id,
            "ITP_DEPLOYMENT_ID": self.deployment_id,
            "ITP_CUSTOMER_ID": self.customer_id,
            "ITP_CUSTOMER_NAME": self.name,
            "ITP_DEPLOYMENT_MODE": self.deployment_mode,
            "ITP_CLUSTER_ID": self.cluster_id,
            "ITP_RUNTIME_MODE": self.runtime_mode,
            "COMPOSE_PROJECT_NAME": self.compose_project,
            "ITP_DISCOVERY_CONFIG": str(self.paths.discovery),
            "ITP_CONNECTORS_CONFIG": str(
                self.paths.connectors_local),
            "ITP_SITES_CONFIG": str(self.paths.sites),
            "ITP_DASHBOARDS_CONFIG": str(self.paths.dashboards),
            "ITP_SECRETS_DIR": str(self.paths.secrets),
            "ITP_RUNTIME_DIR": str(self.paths.runtime),
            "INVENTORY_PATH": str(self.paths.inventory / "devices.json"),
            "SITES_CONFIG": str(self.paths.sites),
            "DASHBOARD_MANAGED_OUTPUT": str(self.paths.managed_dashboards),
            "DASHBOARD_PROVISIONING": str(
                self.paths.dashboard_runtime / "provisioning" / "dashboards.yml"),
            "COLLECTOR_HEALTH_PATH": str(self.paths.runtime / "collector-health"),
            "INFLUXDB_BUCKET": self.influx_bucket,
            "INFLUXDB_ORG": self.influx_org,
            "INFLUXDB_HOST": (
                self.shared_influxdb_url
                if self.deployment_mode == "cluster_member"
                else "http://influxdb3-core:8181"),
            "ITP_SHARED_GRAFANA_URL": self.shared_grafana_url,
            "TZ": self.timezone,
            "GRAFANA_PORT": str(self.grafana_port),
            "INFLUXDB_PORT": str(self.influxdb_port),
        }

    def load_secrets(self, *, protected_keys=()):
        loaded = []
        protected = set(protected_keys)
        if not self.paths.secrets.exists():
            return loaded
        for path in sorted(self.paths.secrets.glob("*.env")):
            for line in path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key, value = key.strip(), value.strip().strip("'\"")
                if key and key not in protected:
                    os.environ[key] = value
            loaded.append(path)
        return loaded

    def activate(self, *, load_secrets=True, protected_environment=()):
        self.paths.create_runtime()
        for key, value in self.env().items():
            os.environ[key] = value
        if load_secrets:
            self.load_secrets(protected_keys=protected_environment)
        return self


def discover_profiles(root=None):
    root = Path(root or Path(__file__).resolve().parents[1]).resolve()
    profiles = []
    seen = set()
    for manifest in sorted((root / "profiles").glob("*/profile.yml")):
        profile = DeploymentProfile.load(manifest.parent.name, root)
        if profile.id in seen:
            raise ProfileError(f"duplicate profile ID: {profile.id}")
        seen.add(profile.id)
        profiles.append(profile)
    return profiles
