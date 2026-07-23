"""Deterministic canonical-site configuration and reference validation."""
from __future__ import annotations


def validate_registry(sites, alias_owners, configured_alias_owners=None,
                      duplicate_aliases=(), used_aliases=(), unknown_values=()):
    findings = []
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
