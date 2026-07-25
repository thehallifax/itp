"""Static virtualisation profile validation; credentials are never loaded here."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

from analysis.sites import SiteRegistry


PROVIDER_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
TRANSPORTS = {"local_powershell", "powershell_remoting", "fixture"}


def validate_virtualisation(config, sites_path, root):
    section = config.get("virtualisation") or {}
    if not section or section.get("enabled") is not True:
        return []
    providers = section.get("providers") or []
    if not isinstance(providers, list):
        raise ValueError("virtualisation.providers must be a list")
    sites = SiteRegistry.load(sites_path)
    ids, endpoints, result = set(), set(), []
    root = Path(root).resolve()
    for index, value in enumerate(providers):
        if not isinstance(value, dict):
            raise ValueError(f"virtualisation provider {index + 1} must be a mapping")
        provider_id = str(value.get("id") or "")
        provider = str(value.get("provider") or "")
        if not PROVIDER_ID.fullmatch(provider_id) or provider_id in ids:
            raise ValueError(f"invalid or duplicate virtualisation provider ID: {provider_id}")
        ids.add(provider_id)
        if provider not in {"vmware", "hyperv", "proxmox"}:
            raise ValueError(f"unsupported virtualisation provider: {provider}")
        resolution = sites.resolver.resolve(None, value.get("site_id"))
        if resolution.status != "resolved":
            raise ValueError(f"virtualisation provider {provider_id} references an unknown site")
        endpoint = str(value.get("endpoint") or "").strip()
        if not endpoint or endpoint.casefold() in endpoints:
            raise ValueError(f"missing or duplicate virtualisation endpoint: {endpoint}")
        endpoints.add(endpoint.casefold())
        if provider in {"vmware", "proxmox"}:
            parsed = urlsplit(endpoint)
            if parsed.scheme != "https" and not value.get("allow_insecure_http"):
                raise ValueError(f"{provider_id} must use HTTPS")
            if not isinstance(value.get("verify_tls", True), bool):
                raise ValueError(f"{provider_id} verify_tls must be boolean")
        if provider == "hyperv" and value.get("transport", "powershell_remoting") not in TRANSPORTS:
            raise ValueError(f"{provider_id} has unsupported Hyper-V transport")
        secret = value.get("secret_file")
        if secret:
            path = (root / str(secret)).resolve()
            if root not in path.parents:
                raise ValueError(f"{provider_id} secret_file escapes the repository")
        thresholds = {**section.get("thresholds", {}), **value.get("thresholds", {})}
        for name in ("host_cpu_warning_percent", "host_memory_warning_percent",
                     "storage_warning_percent", "storage_critical_percent"):
            if name in thresholds and not 0 < float(thresholds[name]) <= 100:
                raise ValueError(f"{provider_id} {name} must be between 0 and 100")
        result.append({**value, "id": provider_id, "provider": provider,
                       "site_id": resolution.site_id})
    return sorted((value for value in result if value.get("enabled", True)),
                  key=lambda value: value["id"])
