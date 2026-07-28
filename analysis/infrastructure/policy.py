"""Central field authority, health, and device-aware validation policy."""
from __future__ import annotations

import hashlib

from .identity import normalize_device_type


SOURCE_PRIORITY = {"inventory": 300, "mist": 200, "fortigate": 200, "paloalto": 200,
                   "papercut": 200, "snmp": 100,
                   "discovery": 100, "legacy": 50}
VENDOR_SOURCES = {"mist", "fortigate", "paloalto"}
STATUS_FRESHNESS_SECONDS = 300


MANAGEMENT_IP_POLICY = {
    "switch": {"required_roles": {"core", "core-switch", "distribution", "distribution-switch"}},
    "firewall": {"required": True}, "router": {"required": True},
    "server": {"required_when_managed": True}, "printer": {"required_when_managed": True},
    "access-point": {"required": False}, "ups": {"required": False}, "storage": {"required": False},
}


def source_priority(source, authority=None):
    return max(SOURCE_PRIORITY.get(str(source or "").lower(), 0),
               SOURCE_PRIORITY.get(str(authority or "").lower(), 0))


def management_ip_required(asset):
    if asset.get("lifecycle_state") in {"retired", "archived"} or asset.get("managed") is False:
        return False, "asset is retired, archived, or explicitly unmanaged"
    kind = normalize_device_type(asset.get("device_type") or asset.get("device_role") or asset.get("platform"))
    if kind == "access-point" and (asset.get("status") == "offline" or not asset.get("last_seen_at")):
        return False, "offline or never-seen access point"
    policy = MANAGEMENT_IP_POLICY.get(kind, {})
    role = str(asset.get("device_role") or "").lower().replace("_", "-")
    if policy.get("required") or role in policy.get("required_roles", set()): return True, "required by device policy"
    if policy.get("required_when_managed") and asset.get("managed", True): return True, "required for managed device"
    return False, "management address is optional for this device class"


def finding(kind, canonical_id, message, *, severity="Info", actionable=False,
            suppressed=False, explanation=""):
    raw = "|".join((kind, canonical_id, message))
    return {"id": "finding:" + hashlib.sha256(raw.encode()).hexdigest()[:20], "type": kind,
        "canonical_id": canonical_id, "severity": severity, "actionable": actionable,
        "suppressed": suppressed, "message": message, "explanation": explanation}


def infrastructure_health(assets, actionable_warnings, wan_status=None):
    if not assets: return "Unknown"
    critical = [asset for asset in assets if asset.get("status") == "offline" and
                any(token in " ".join(str(asset.get(key, "")).lower()
                    for key in ("device_type", "device_role", "hostname"))
                    for token in ("firewall", "router", "core"))]
    if critical or wan_status == "Offline": return "Critical"
    if any(asset.get("status") == "offline" for asset in assets) or actionable_warnings: return "Warning"
    return "Healthy"


def observability_health(collectors, has_state):
    if not collectors: return "Unknown"
    failed = sum(value.get("status") == "failed" for value in collectors)
    healthy = sum(value.get("status") == "healthy" for value in collectors)
    if not has_state or (failed and healthy == 0): return "Critical"
    if failed or any(value.get("status") != "healthy" for value in collectors): return "Warning"
    return "Healthy"
