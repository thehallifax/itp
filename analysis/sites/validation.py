"""Deterministic canonical-site configuration and reference validation."""
from __future__ import annotations

SITE_TYPES = frozenset({
    "head_office", "school", "campus", "office", "data_centre", "warehouse",
    "branch", "remote_site", "other",
})
MAX_HIERARCHY_DEPTH = 8


def validate_registry(sites, alias_owners, configured_alias_owners=None,
                      duplicate_aliases=(), used_aliases=(), unknown_values=()):
    findings = []
    ids = {site.site_id: site for site in sites}
    seen_ids = set()
    for site in sites:
        if site.site_id in seen_ids:
            findings.append({"type": "duplicate_site_id", "site_id": site.site_id,
                "message": "Canonical site ID is repeated."})
        seen_ids.add(site.site_id)
        if site.type not in SITE_TYPES:
            findings.append({"type": "invalid_site_type", "site_id": site.site_id,
                "value": site.type, "message": "Site type is not supported."})
        if site.rollup_group is not None and (
                not site.rollup_group.strip()
                or not site.rollup_group.replace("-", "").replace("_", "").isalnum()):
            findings.append({"type": "invalid_rollup_group", "site_id": site.site_id,
                "value": site.rollup_group, "message": "Rollup group must be filesystem-safe."})
        parent = site.canonical_parent_id
        if parent == site.site_id:
            findings.append({"type": "self_parent", "site_id": site.site_id,
                "message": "A site cannot be its own parent."})
        elif parent and parent not in ids:
            findings.append({"type": "unknown_parent", "site_id": site.site_id,
                "parent_id": parent, "message": "Parent site is not in this profile."})
        elif parent and not ids[parent].enabled and site.enabled:
            findings.append({"type": "disabled_parent", "site_id": site.site_id,
                "parent_id": parent, "message": "An enabled child cannot use a disabled parent."})
    orders = {}
    for site in sites:
        if site.display_order is not None:
            key = (site.canonical_parent_id, site.display_order)
            if key in orders:
                findings.append({"type": "duplicate_display_order",
                    "site_ids": sorted((orders[key], site.site_id)),
                    "display_order": site.display_order,
                    "message": "Sibling sites must not share display_order."})
            orders[key] = site.site_id
    for site in sites:
        chain, current = [], site
        while current and current.canonical_parent_id:
            if current.site_id in chain:
                findings.append({"type": "circular_hierarchy", "site_id": site.site_id,
                    "path": chain + [current.site_id],
                    "message": "Site hierarchy contains a cycle."})
                break
            chain.append(current.site_id)
            current = ids.get(current.canonical_parent_id)
            if len(chain) > MAX_HIERARCHY_DEPTH:
                findings.append({"type": "excessive_hierarchy_depth", "site_id": site.site_id,
                    "depth": len(chain), "message": "Site hierarchy exceeds the supported depth."})
                break
    configured_alias_owners = configured_alias_owners or alias_owners
    for alias in sorted(set(duplicate_aliases)):
        findings.append({"type": "duplicate_alias", "alias": alias,
            "message": "Alias is repeated within one canonical site."})
    for alias, owners in sorted(alias_owners.items()):
        if len(owners) > 1:
            findings.append({"type": "ambiguous_alias", "alias": alias,
                "site_ids": sorted(owners), "message": "Alias maps to multiple canonical sites."})
    configured = set(configured_alias_owners)
    for alias in sorted(configured - set(used_aliases)):
        findings.append({"type": "unused_alias", "alias": alias,
            "site_ids": sorted(configured_alias_owners[alias]), "message": "Configured alias was not observed."})
    for value in sorted(set(str(item) for item in unknown_values if item)):
        findings.append({"type": "unknown_site", "value": value,
            "message": "Asset site value is not present in the canonical registry."})
    return findings
