"""Version-tolerant extraction from PAN-OS XML result elements."""
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


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
    packages = (
        ("applications", "app-version", "app-release-date"),
        ("threats", "threat-version", "threat-release-date"),
        ("antivirus", "av-version", "av-release-date"),
        ("wildfire", "wildfire-version", "wildfire-release-date"),
        ("url_filtering", "url-filtering-version", "url-filtering-release-date"),
        ("device_dictionary", "device-dictionary-version", "device-dictionary-release-date"),
        ("globalprotect_datafile", "global-protect-datafile-version",
         "global-protect-datafile-release-date"),
        ("globalprotect_client", "global-protect-client-package-version",
         "global-protect-client-package-release-date"),
        ("globalprotect_clientless_vpn", "global-protect-clientless-vpn-version",
         "global-protect-clientless-vpn-release-date"),
    )
    return {
        "hostname": text(node, "hostname", "devicename"),
        "serial": text(node, "serial"),
        "model": text(node, "model"),
        "software_version": text(node, "sw-version", "sw_version"),
        "management_ip": text(node, "ip-address", "management-ip"),
        "uptime_seconds": parse_uptime(text(node, "uptime")),
        "family": text(node, "family"),
        "platform_family": text(node, "platform-family", "family"),
        "operational_mode": text(node, "operational-mode", "mode"),
        "multi_vsys": boolean(text(node, "multi-vsys")),
        "vsys_count": number(text(node, "vsys-count")),
        "device_certificate_status": text(node, "device-certificate-status"),
        "firewall_time": text(node, "time"),
        "content_packages": [{
            "package_name": name,
            "version": text(node, version_path),
            "release_time_raw": text(node, release_path),
        } for name, version_path, release_path in packages
            if text(node, version_path)],
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
    parsed = {}
    for row in rows:
        name = row.attrib.get("name") or text(row, "name")
        if not name: continue
        current = parsed.setdefault(name, {"name": name})
        interface_type = text(row, "type", "iftype")
        logical = "." in name or str(interface_type or "").lower() in {"layer3", "virtual", "tunnel"}
        values = {"interface_type": interface_type,
            "admin_status": text(row, "admin", "admin-status"),
            "operational_status": text(row, "status", "link-state", "state"),
            "speed": text(row, "speed"), "duplex": text(row, "duplex"),
            "mac_address": text(row, "mac"), "ip_address": text(row, "ip", "ip-address"),
            "zone": text(row, "zone"), "logical": logical,
            "aggregate_group": text(row, "aggregate-group", "aggregate")}
        for key, value in values.items():
            if value not in (None, "", False) or key == "logical":
                current[key] = value
    return [parsed[name] for name in sorted(parsed)]


def interface_counters(result):
    """Parse `show counter interface all` cumulative interface counters."""
    values = {}
    for row in result.findall(".//entry"):
        name = row.attrib.get("name") or text(row, "name", "interface")
        if not name or not any(row.find(key) is not None for key in
                               ("ibytes", "rx-bytes", "ipackets")):
            continue
        current = values.setdefault(name, {"name": name})
        candidates = {
            "rx_bytes_total": number(text(row, "ibytes", "rx-bytes")),
            "tx_bytes_total": number(text(row, "obytes", "tx-bytes")),
            "rx_packets_total": number(text(row, "ipackets")),
            "tx_packets_total": number(text(row, "opackets")),
            "rx_errors_total": number(text(row, "ierrors", "port/rx-error", "rx-error")),
            "tx_errors_total": number(text(row, "port/tx-error", "tx-error")),
            "rx_discards_total": number(text(row, "idrops")),
        }
        for key, candidate in candidates.items():
            if candidate is None:
                continue
            candidate = int(candidate)
            current[key] = max(int(current.get(key, 0)), candidate)
    return [values[name] for name in sorted(values)]


def licenses(result, *, now=None):
    now = now or datetime.now(timezone.utc)
    rows = []
    for entry in result.findall(".//entry"):
        name = entry.attrib.get("name") or text(entry, "feature", "name")
        expiry = text(entry, "expires", "expiry", "expiration")
        raw_expiry = expiry
        expired = boolean(text(entry, "expired"))
        days = None; expired_days = None; perpetual = False; expiry_state = "unavailable"
        if expiry and expiry.strip().lower() in {
                "never", "never expires", "perpetual", "n/a", "not applicable", "none"}:
            perpetual = True; expired = False; expiry_state = "perpetual"
        elif expiry:
            for fmt in ("%B %d, %Y", "%Y/%m/%d", "%Y-%m-%d"):
                try:
                    date = datetime.strptime(expiry, fmt).replace(tzinfo=timezone.utc)
                    signed = (date - now).days
                    expired = signed < 0
                    days = max(0, signed)
                    expired_days = max(0, -signed) if expired else 0
                    expiry_state = "expired" if expired else "active"
                    break
                except ValueError: pass
            if expiry_state == "unavailable": expiry_state = "malformed"
        elif expired is True:
            expiry_state = "expired"
        status = text(entry, "status")
        if expiry_state == "unavailable" and status:
            expiry_state = str(status).strip().lower()
        if name:
            rows.append({"name": name, "expiry": expiry, "expired": expired,
                         "days_remaining": days, "expired_days": expired_days,
                         "perpetual": perpetual, "expiry_state": expiry_state,
                         "status": status, "raw_expiry": raw_expiry,
                         "raw_status": status})
    return sorted(rows, key=lambda value: value["name"])


def resources(result):
    raw = " ".join(" ".join(result.itertext()).split())
    values = {}
    idle = re.search(r"%Cpu\(s\):.*?([0-9.]+)\s+id\b", raw, re.I)
    if idle:
        values["management_cpu_percent"] = round(
            min(100.0, max(0.0, 100.0 - float(idle.group(1)))), 2)
    memory = re.search(
        r"(?:MiB|GiB|KiB)\s+Mem\s*:\s*([0-9.]+)\s+total,.*?([0-9.]+)\s+used",
        raw, re.I)
    if memory and float(memory.group(1)) > 0:
        values["memory_used_percent"] = round(
            min(100.0, max(0.0, float(memory.group(2)) / float(memory.group(1)) * 100)), 2)
    return values


def resource_monitor(result):
    values = {}
    cores = [number(entry.findtext("value")) for entry in result.findall(".//entry")
             if entry.find("coreid") is not None]
    cores = [value for value in cores if value is not None and 0 <= value <= 100]
    if cores:
        values["dataplane_cpu_percent"] = round(sum(cores) / len(cores), 2)
    for entry in result.findall(".//entry"):
        name = (text(entry, "name") or "").strip().lower()
        value = number(text(entry, "value"))
        if name == "packet buffer" and value is not None and 0 <= value <= 100:
            values["packet_buffer_used_percent"] = value
    return values


def sessions(result):
    active = number(text(result, ".//num-active"))
    maximum = number(text(result, ".//num-max"))
    values = {}
    if active is not None and active >= 0:
        values["sessions_active"] = int(active)
    if maximum is not None and maximum > 0:
        values["sessions_max"] = int(maximum)
    if active is not None and active >= 0 and maximum is not None and maximum > 0:
        values["session_utilisation_percent"] = round(
            min(100.0, active / maximum * 100), 2)
    for source, target in (("num-tcp", "sessions_tcp"),
                           ("num-udp", "sessions_udp")):
        value = number(text(result, f".//{source}"))
        if value is not None and value >= 0:
            values[target] = int(value)
    return values


def content_packages(system_value, *, now=None):
    now = now or datetime.now(timezone.utc)
    timezone_hint = ZoneInfo("Australia/Perth")
    values = []
    for package in system_value.get("content_packages", []):
        raw = package.get("release_time_raw")
        released = None
        if raw and str(raw).strip().lower() not in {"unknown", "n/a", "none"}:
            cleaned = re.sub(r"\s+(AWST|WST)$", "", str(raw).strip(), flags=re.I)
            for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                try:
                    released = datetime.strptime(cleaned, fmt).replace(
                        tzinfo=timezone_hint).astimezone(timezone.utc)
                    break
                except ValueError:
                    pass
        age = max(0, (now - released).days) if released else None
        values.append({**package,
            "release_time": released.isoformat().replace("+00:00", "Z") if released else None,
            "age_days": age})
    return sorted(values, key=lambda value: value["package_name"])
