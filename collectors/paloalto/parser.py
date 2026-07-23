"""Version-tolerant extraction from PAN-OS XML result elements."""
import re
from datetime import datetime, timezone


def text(node, *paths, default=None):
    for path in paths:
        value = node.findtext(path)
        if value is not None and value.strip(): return value.strip()
    return default


def boolean(value):
    if value is None: return None
    lowered = str(value).strip().lower()
    if lowered in {"yes", "true", "enabled", "on", "1"}: return True
    if lowered in {"no", "false", "disabled", "off", "0"}: return False
    return None


def number(value):
    if value in (None, ""): return None
    match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group()) if match else None


def parse_uptime(value):
    if not value: return None
    if str(value).isdigit(): return int(value)
    units = {"day": 86400, "hour": 3600, "minute": 60, "second": 1}
    total = 0
    for amount, unit in re.findall(r"(\d+)\s*(day|hour|minute|second)s?", str(value).lower()):
        total += int(amount) * units[unit]
    return total or None


def system(result):
    node = result.find(".//system")
    if node is None: node = result
    return {
        "hostname": text(node, "hostname", "devicename"),
        "serial": text(node, "serial"),
        "model": text(node, "model"),
        "software_version": text(node, "sw-version", "sw_version"),
        "management_ip": text(node, "ip-address", "management-ip"),
        "uptime_seconds": parse_uptime(text(node, "uptime")),
        "family": text(node, "family"),
        "operational_mode": text(node, "operational-mode", "mode"),
        "multi_vsys": boolean(text(node, "multi-vsys")),
        "vsys_count": number(text(node, "vsys-count")),
        "content_versions": {key: value for key, value in {
            "applications": text(node, "app-version"),
            "antivirus": text(node, "av-version"),
            "threat": text(node, "threat-version"),
            "wildfire": text(node, "wildfire-version"),
            "globalprotect": text(node, "global-protect-client-package-version"),
            "url_filtering": text(node, "url-filtering-version"),
        }.items() if value},
    }


def ha(result):
    enabled = boolean(text(result, ".//enabled"))
    local = text(result, ".//local-info/state", ".//local/state")
    peer = text(result, ".//peer-info/state", ".//peer/state")
    mode = text(result, ".//group/mode", ".//mode")
    sync = text(result, ".//running-sync", ".//running-sync-enabled",
                ".//peer-info/running-sync")
    config_sync = text(result, ".//configuration-synchronized",
                       ".//peer-info/configuration-synchronized")
    if enabled is False or (enabled is None and not local and not peer):
        state = "standalone"
    else:
        evidence = " ".join(filter(None, (local, peer, sync, config_sync))).lower()
        degraded = any(token in evidence for token in
                       ("suspend", "non-functional", "down", "out of sync", "not synchronized"))
        state = "degraded" if degraded else "healthy" if local else "unavailable"
    return {"enabled": enabled, "status": state, "mode": mode, "local_state": local,
            "peer_state": peer, "running_sync": sync, "configuration_sync": config_sync,
            "local_priority": number(text(result, ".//local-info/priority")),
            "peer_serial": text(result, ".//peer-info/serial"),
            "last_state_change_reason": text(result, ".//last-error-reason",
                                             ".//state-reason")}


def interfaces(result):
    rows = result.findall(".//entry")
    parsed = []
    for row in rows:
        name = row.attrib.get("name") or text(row, "name")
        if not name: continue
        interface_type = text(row, "type", "iftype")
        logical = "." in name or str(interface_type or "").lower() in {"layer3", "virtual", "tunnel"}
        parsed.append({"name": name, "interface_type": interface_type,
            "admin_status": text(row, "state", "admin", "admin-status"),
            "operational_status": text(row, "status", "link-state", "state"),
            "speed": text(row, "speed"), "duplex": text(row, "duplex"),
            "mac_address": text(row, "mac"), "ip_address": text(row, "ip", "ip-address"),
            "zone": text(row, "zone"), "logical": logical,
            "aggregate_group": text(row, "aggregate-group", "aggregate")})
    return sorted(parsed, key=lambda value: value["name"])


def licenses(result, *, now=None):
    now = now or datetime.now(timezone.utc)
    rows = []
    for entry in result.findall(".//entry"):
        name = entry.attrib.get("name") or text(entry, "feature", "name")
        expiry = text(entry, "expires", "expiry", "expiration")
        expired = boolean(text(entry, "expired"))
        days = None
        if expiry:
            for fmt in ("%B %d, %Y", "%Y/%m/%d", "%Y-%m-%d"):
                try:
                    date = datetime.strptime(expiry, fmt).replace(tzinfo=timezone.utc)
                    days = (date - now).days; expired = days < 0; break
                except ValueError: pass
        if name:
            rows.append({"name": name, "expiry": expiry, "expired": expired,
                         "days_remaining": days, "status": text(entry, "status")})
    return sorted(rows, key=lambda value: value["name"])


def resources(result):
    raw = " ".join(" ".join(result.itertext()).split())
    values = {}
    patterns = {
        "management_cpu_percent": r"(?:CPU|cpu).*?(\d+(?:\.\d+)?)\s*%",
        "memory_percent": r"(?:memory|Memory).*?(\d+(?:\.\d+)?)\s*%",
        "session_count": r"(?:sessions?|Sessions?)\D+(\d[\d,]*)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, raw)
        if match: values[key] = number(match.group(1))
    return values
