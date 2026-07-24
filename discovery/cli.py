"""Repository-safe discovery validation commands."""
from __future__ import annotations

import argparse
from pathlib import Path

from analysis.dashboards import DashboardRegistry
from analysis.sites import SiteRegistry
from analysis.virtualisation.config import validate_virtualisation
from collectors.config import load_config
from itp_profiles import DeploymentProfile, discover_profiles


ROOT = Path(__file__).resolve().parents[1]


def validate_profiles(root):
    root = Path(root).resolve()
    profiles = discover_profiles(root)
    if not profiles:
        raise ValueError("no deployment profiles found")
    for discovered in profiles:
        profile = DeploymentProfile.load(discovered.id, root)
        config = load_config(profile.paths.discovery)
        if config.get("schema_version") != 1:
            raise ValueError(
                f"profile {profile.id} has unsupported configuration schema")
        sites = SiteRegistry.load(profile.paths.sites)
        blocking = [value for value in sites.validation()
                    if value["type"] not in {"unused_alias", "unknown_site"}]
        if not sites.sites or blocking:
            raise ValueError(
                f"profile {profile.id} has invalid canonical site configuration")
        validate_virtualisation(config, profile.paths.sites, root)
        DashboardRegistry(root, config).resolve()
        print(f"[PASS] {profile.id}: configuration, sites, virtualisation and dashboards")
    print(f"Validated {len(profiles)} deployment profile(s)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    profiles = subparsers.add_parser("validate-profiles")
    profiles.add_argument("--root", default=str(ROOT))
    args = parser.parse_args()
    try:
        if args.command == "validate-profiles":
            validate_profiles(args.root)
    except Exception as exc:
        parser.exit(1, f"validation failed: {exc}\n")


if __name__ == "__main__":
    main()
