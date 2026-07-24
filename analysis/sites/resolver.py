"""Pure deterministic site resolution."""
from __future__ import annotations

from .aliases import canonical_key, normalize_alias
from .models import SiteResolution


class SiteResolver:
    def __init__(self, sites, alias_owners):
        self.sites = {site.site_id: site for site in sites}
        self.alias_owners = alias_owners

    def resolve(self, value, explicit_id=None):
        raw = str(value or "").strip()
        if explicit_id:
            site_id = str(explicit_id)
            site_id = site_id if site_id.startswith("site:") else f"site:{site_id}"
            site = self.sites.get(site_id)
            if site:
                return SiteResolution(site_id, site.display_name, raw, "resolved",
                                      explanation="Matched explicit canonical site ID.")
            return SiteResolution(site_id, raw, raw, "unknown",
                                  explanation="Explicit canonical site ID is not configured.")
        alias = normalize_alias(raw)
        owners = self.alias_owners.get(alias, set()) if alias else set()
        if len(owners) == 1:
            site_id = next(iter(owners)); site = self.sites[site_id]
            return SiteResolution(site_id, site.display_name, raw, "resolved", alias,
                                  "Matched a configured normalized alias.")
        if len(owners) > 1:
            return SiteResolution(None, raw, raw, "ambiguous", alias or "",
                                  "Normalized alias maps to multiple canonical sites.")
        return SiteResolution(None, raw, raw, "unknown", alias or canonical_key(raw) or "",
                              "No configured site alias matched.")
