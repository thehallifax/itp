"""Infrastructure state models and deterministic classifications."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AdapterResult:
    name: str
    priority: int
    assets: list = field(default_factory=list)
    collectors: list = field(default_factory=list)


def asset_name(asset):
    return str(asset.get("hostname") or asset.get("display_name") or
               asset.get("management_ip") or asset.get("asset_id") or "Unknown device")


def asset_kind(asset):
    return " ".join(str(asset.get(key, "")).lower()
                    for key in ("device_type", "device_role", "platform", "model"))


def state_of(asset):
    if asset.get("online") is True: return "online"
    if asset.get("online") is False: return "offline"
    return "unknown"


def health_of(asset):
    if state_of(asset) == "offline":
        return "critical" if any(value in asset_kind(asset) for value in ("core", "firewall")) else "offline"
    if (asset.get("lifecycle_state") in {"stale", "missing"} or
            asset.get("reconciliation_status") in {"ambiguous", "conflict"}):
        return "warning"
    if state_of(asset) == "online": return "healthy"
    return "unknown"
