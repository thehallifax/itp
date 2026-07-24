"""Canonical site registry, estate aggregation, and runtime rendering."""
from __future__ import annotations

import csv
import io
import json
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from collectors.writer import atomic_write
from .aliases import normalize_alias
from .models import SiteDefinition
from .resolver import SiteResolver
from .validation import validate_registry


def _asset_health(asset):
    online = asset.get("online")
    kind = " ".join(str(asset.get(key, "")).lower()
                    for key in ("device_type", "device_role", "platform", "model"))
    if online is False: return "critical" if "core" in kind or "firewall" in kind else "offline"
    if asset.get("lifecycle_state") in {"stale", "missing"}: return "warning"
    if online is True: return "healthy"
    return "unknown"


class SiteRegistry:
    def __init__(self, sites=(), dependencies=()):
        self._configured_sites = tuple(sites)
        self.dependencies = tuple(dependencies)
        self.sites = tuple(self._ordered(site for site in sites if site.enabled))
        self.disabled_sites = tuple(self._ordered(site for site in sites if not site.enabled))
        owners = defaultdict(set); configured = defaultdict(set); duplicate_aliases = []
        for site in self.sites:
            for value in (site.id, site.site_id, site.display_name, *site.aliases):
                alias = normalize_alias(value)
                if alias: owners[alias].add(site.site_id)
            seen = set()
            for value in site.aliases:
                alias = normalize_alias(value)
                if not alias: continue
                if alias in seen: duplicate_aliases.append(alias)
                seen.add(alias); configured[alias].add(site.site_id)
        self.alias_owners = {key: set(value) for key, value in sorted(owners.items())}
        self.configured_alias_owners = {key: set(value) for key, value in sorted(configured.items())}
        self.duplicate_aliases = tuple(sorted(set(duplicate_aliases)))
        self.resolver = SiteResolver(self.sites, self.alias_owners)

    @staticmethod
    def _ordered(sites):
        values = list(sites)
        by_id = {value.site_id: value for value in values}

        def depth(site):
            result, seen, current = 0, set(), site
            while current.canonical_parent_id and current.canonical_parent_id in by_id:
                if current.site_id in seen:
                    return 99
                seen.add(current.site_id)
                result += 1
                current = by_id[current.canonical_parent_id]
            return result

        return sorted(values, key=lambda value: (
            depth(value), value.display_order is None,
            value.display_order if value.display_order is not None else 2**31,
            value.display_name.casefold(), value.site_id))

    @classmethod
    def load(cls, path):
        path = Path(path)
        if not path.exists(): return cls()
        data = yaml.safe_load(path.read_text()) or {}
        values = []
        for raw in data.get("sites", []):
            metadata = {key: raw[key] for key in ("timezone", "region", "address", "notes")
                        if raw.get(key) is not None}
            values.append(SiteDefinition(
                str(raw["id"]), str(raw.get("display_name") or raw.get("name")),
                tuple(str(value) for value in raw.get("aliases", [])), metadata,
                str(raw.get("type") or "other"), raw.get("parent_id"),
                raw.get("rollup_group"), bool(raw.get("enabled", True)),
                int(raw["display_order"]) if raw.get("display_order") is not None else None,
                str(raw.get("description") or "")))
        return cls(values, data.get("dependencies", []))

    def resolve_records(self, results):
        from analysis.infrastructure.models import AdapterResult
        used = set(); unknown = []; resolved_results = []
        for result in results:
            assets = []
            for original in result.assets:
                record = dict(original); source_value = record.get("site")
                resolution = self.resolver.resolve(source_value, record.get("site_id"))
                record["_site_source_value"] = source_value
                record["_site_resolution_status"] = resolution.status
                if resolution.site_id:
                    record["site_id"] = resolution.site_id
                    record["site_display_name"] = resolution.display_name
                    record["site"] = resolution.display_name
                if resolution.status == "resolved" and resolution.matched_alias:
                    used.add(resolution.matched_alias)
                elif resolution.status != "resolved" and source_value:
                    unknown.append(str(source_value))
                assets.append(record)
            resolved_results.append(AdapterResult(result.name, result.priority, assets, result.collectors))
        return resolved_results, used, unknown

    def definition(self, site_id):
        return next((value for value in self.sites if value.site_id == site_id), None)

    def validation(self, used_aliases=(), unknown_values=()):
        findings = validate_registry(
            self._configured_sites, self.alias_owners, self.configured_alias_owners,
            self.duplicate_aliases, used_aliases, unknown_values)
        supported_services = {
            "internet", "wireless", "switching", "printing", "identity", "compute",
            "storage", "voice", "email", "security", "monitoring",
            "virtualisation management plane", "virtualisation_management_plane",
            "hypervisor cluster", "hypervisor_cluster",
            "compute capacity", "compute_capacity",
            "virtual machine hosting", "virtual_machine_hosting",
            "shared storage", "shared_storage",
            "workload availability", "workload_availability",
        }
        configured_ids = {site.site_id for site in self._configured_sites}
        for index, dependency in enumerate(self.dependencies):
            service = str(dependency.get("service", "")).casefold()
            if service not in supported_services:
                findings.append({"type": "invalid_service_dependency",
                    "dependency": index, "service": service,
                    "message": "Dependency service is not canonical."})
            references = [dependency.get("provider_site_id"),
                          *dependency.get("consumer_site_ids", [])]
            for reference in references:
                site_id = str(reference or "")
                site_id = site_id if site_id.startswith("site:") else f"site:{site_id}"
                if site_id not in configured_ids:
                    findings.append({"type": "unknown_dependency_site",
                        "dependency": index, "site_id": site_id,
                        "message": "Dependency references a site outside this profile."})
        return findings

    @property
    def deployment_model(self):
        if len(self.sites) <= 1:
            return "single_site"
        return "multi_site_hierarchical" if any(site.parent_id for site in self.sites) \
            else "multi_site_flat"

    @property
    def roots(self):
        return tuple(site for site in self.sites if not site.parent_id)

    @property
    def children(self):
        return tuple(site for site in self.sites if site.parent_id)

    @property
    def estate_enabled(self):
        return len(self.sites) > 1

    def hierarchy_payload(self, deployment_id="", generated_at=None):
        return {
            "schema_version": 1,
            "generated_at": generated_at,
            "deployment_id": deployment_id,
            "deployment_model": self.deployment_model,
            "estate_enabled": self.estate_enabled,
            "sites": [{
                "site_id": site.site_id, "name": site.display_name, "type": site.type,
                "parent_id": site.canonical_parent_id, "rollup_group": site.rollup_group,
                "enabled": site.enabled, "display_order": site.display_order,
                "description": site.description, "aliases": list(site.aliases),
            } for site in (*self.sites, *self.disabled_sites)],
            "validation": self.validation(),
        }

    def statistics(self, used_aliases=()):
        loaded = set(self.configured_alias_owners); used = loaded & set(used_aliases)
        return {"canonical_sites": len(self.sites), "aliases_loaded": len(loaded),
                "aliases_used": len(used), "aliases_unused": len(loaded - used)}

    def estate(self, state, operations=None):
        operations = operations or {"issues": [], "risks": [], "recommendations": []}
        assets_by_site = defaultdict(list)
        for asset in state.get("assets", []):
            site = asset.get("site") or {}; site_id = site.get("site_id") if isinstance(site, dict) else None
            if site_id: assets_by_site[site_id].append(asset)
        counts = {kind: Counter(value.get("site_id") for value in operations.get(kind, []) if value.get("site_id"))
                  for kind in ("issues", "risks", "recommendations")}
        records = []
        for definition in self.sites:
            assets = assets_by_site.get(definition.site_id, []); health = Counter(_asset_health(value) for value in assets)
            if health["critical"]: infra = "Critical"
            elif health["offline"] or health["warning"]: infra = "Warning"
            elif assets: infra = "Healthy"
            else: infra = "Unknown"
            sources = sorted({source for asset in assets for source in asset.get("sources", [])})
            records.append({"site_id": definition.site_id, "display_name": definition.display_name,
                "aliases": list(definition.aliases), "type": definition.type,
                "parent_id": definition.canonical_parent_id,
                "rollup_group": definition.rollup_group,
                "display_order": definition.display_order,
                **definition.metadata, "devices": len(assets),
                "collectors": len(sources), "collector_names": sources,
                "issues": counts["issues"][definition.site_id], "risks": counts["risks"][definition.site_id],
                "recommendations": counts["recommendations"][definition.site_id],
                "infrastructure_health": infra,
                "observability_health": state.get("summary", {}).get("observability_health", "Unknown")})
        return records

    def write(self, output_dir, dashboard_dir, state, operations=None, validation=None, statistics=None):
        records = self.estate(state, operations); output_dir = Path(output_dir)
        deployment_id = state.get("deployment_id", "")
        payload = {"generated_at": state.get("generated_at"),
                   "deployment_id": deployment_id,
                   "deployment_model": self.deployment_model, "sites": records,
                   "validation": validation or [], "statistics": statistics or self.statistics()}
        atomic_write(output_dir / "sites.json", json.dumps(payload, indent=2, sort_keys=True) + "\n")
        fields = ("site_id", "display_name", "devices", "collectors", "issues", "risks",
                  "recommendations", "infrastructure_health", "observability_health", "aliases")
        stream = io.StringIO(); writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for record in records: writer.writerow({**record, "aliases": ";".join(record["aliases"])})
        atomic_write(output_dir / "sites.csv", stream.getvalue())
        atomic_write(output_dir / "hierarchy.json",
            json.dumps(self.hierarchy_payload(deployment_id, state.get("generated_at")),
                       indent=2, sort_keys=True) + "\n")
        health = Counter(value["infrastructure_health"] for value in records)
        summary = {"generated_at": state.get("generated_at"),
            "deployment_id": deployment_id, "deployment_model": self.deployment_model,
            "estate_enabled": self.estate_enabled, "total_sites": len(records),
            "healthy_sites": health["Healthy"], "warning_sites": health["Warning"],
            "critical_sites": health["Critical"],
            "collectors_by_site": {value["site_id"]: value["collectors"] for value in records},
            "devices_by_site": {value["site_id"]: value["devices"] for value in records},
            "sites": [{"site_id": value["site_id"], "display_name": value["display_name"]} for value in records]}
        atomic_write(Path(dashboard_dir) / "site-summary.json", json.dumps(summary, indent=2, sort_keys=True) + "\n")
        return payload, summary
