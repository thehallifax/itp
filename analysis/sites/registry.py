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
    def __init__(self, sites=()):
        self.sites = tuple(sorted(sites, key=lambda value: value.site_id))
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

    @classmethod
    def load(cls, path):
        path = Path(path)
        if not path.exists(): return cls()
        data = yaml.safe_load(path.read_text()) or {}
        values = []
        for raw in data.get("sites", []):
            metadata = {key: raw[key] for key in ("timezone", "region", "address", "notes") if raw.get(key) is not None}
            values.append(SiteDefinition(str(raw["id"]), str(raw["display_name"]),
                tuple(str(value) for value in raw.get("aliases", [])), metadata))
        return cls(values)

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
        return validate_registry(self.sites, self.alias_owners, self.configured_alias_owners,
                                 self.duplicate_aliases, used_aliases, unknown_values)

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
                "aliases": list(definition.aliases), **definition.metadata, "devices": len(assets),
                "collectors": len(sources), "collector_names": sources,
                "issues": counts["issues"][definition.site_id], "risks": counts["risks"][definition.site_id],
                "recommendations": counts["recommendations"][definition.site_id],
                "infrastructure_health": infra,
                "observability_health": state.get("summary", {}).get("observability_health", "Unknown")})
        return records

    def write(self, output_dir, dashboard_dir, state, operations=None, validation=None, statistics=None):
        records = self.estate(state, operations); output_dir = Path(output_dir)
        payload = {"generated_at": state.get("generated_at"), "sites": records,
                   "validation": validation or [], "statistics": statistics or self.statistics()}
        atomic_write(output_dir / "sites.json", json.dumps(payload, indent=2, sort_keys=True) + "\n")
        fields = ("site_id", "display_name", "devices", "collectors", "issues", "risks",
                  "recommendations", "infrastructure_health", "observability_health", "aliases")
        stream = io.StringIO(); writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for record in records: writer.writerow({**record, "aliases": ";".join(record["aliases"])})
        atomic_write(output_dir / "sites.csv", stream.getvalue())
        health = Counter(value["infrastructure_health"] for value in records)
        summary = {"generated_at": state.get("generated_at"), "total_sites": len(records),
            "healthy_sites": health["Healthy"], "warning_sites": health["Warning"],
            "critical_sites": health["Critical"],
            "collectors_by_site": {value["site_id"]: value["collectors"] for value in records},
            "devices_by_site": {value["site_id"]: value["devices"] for value in records},
            "sites": [{"site_id": value["site_id"], "display_name": value["display_name"]} for value in records]}
        atomic_write(Path(dashboard_dir) / "site-summary.json", json.dumps(summary, indent=2, sort_keys=True) + "\n")
        return payload, summary
