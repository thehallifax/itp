"""Normalize Mist API inventory and statistics."""
import hashlib
import json


def device_kind(device):
    kind = str(device.get("type", "")).lower().replace("_", "-")
    model = str(device.get("model", "")).upper()
    if kind == "ap" or model.startswith("AP"): return "wireless-access-point", "access-point"
    if kind in ("switch", "ex") or model.startswith(("EX", "QFX")): return "network-switch", "switch"
    if kind in ("mxedge", "mist-edge") or model.startswith("ME-"): return "mist-edge", "mist-edge"
    if kind in ("gateway", "wan-edge", "wanedge") or model.startswith(("SRX", "SSR")): return "wan-edge", "wan-edge"
    return "unknown", "unknown"


def stable_id(device, organization_id):
    strongest = device.get("id") or device.get("device_id") or device.get("serial") or device.get("mac")
    if strongest: return f"mist:{strongest}"
    basis = json.dumps([organization_id, device.get("site_id"), device.get("type"),
                        device.get("model"), device.get("name")], separators=(",", ":"))
    return "mist:fallback:" + hashlib.sha256(basis.encode()).hexdigest()[:24]


def normalize_device(device, stats, sites, organization_id, customer, default_site):
    platform, role = device_kind({**device, **stats})
    external_id = device.get("id") or device.get("device_id") or stats.get("id") or stats.get("device_id")
    site_id = device.get("site_id") or stats.get("site_id")
    status_value = str(stats.get("status", "unknown")).lower()
    operational = {"connected": "online", "online": "online", "disconnected": "disconnected",
                   "offline": "offline"}.get(status_value, "unknown")
    authoritative = {"customer", "vendor"}
    combined = {**stats, **device}
    if any(key in combined for key in ("id", "device_id")): authoritative.add("source_asset_id")
    if "name" in combined: authoritative.add("hostname")
    if "site_id" in combined: authoritative.add("site")
    if "model" in combined: authoritative.update(("model", "platform", "device_type", "device_role"))
    if "type" in combined: authoritative.update(("platform", "device_type", "device_role"))
    if "serial" in combined: authoritative.add("serial_number")
    if "mac" in combined: authoritative.add("mac_address")
    if "ip" in combined or "ip_stat" in combined: authoritative.add("management_ip")
    if "version" in combined: authoritative.add("firmware_version")
    if "claimed" in combined: authoritative.add("claimed")
    if "managed" in combined: authoritative.add("managed")
    result = {
        "id": stable_id({**stats, **device}, organization_id), "source": "mist", "collector": "mist",
        "customer": customer, "site": sites.get(site_id, default_site),
        "external_site_id": site_id, "external_device_id": external_id,
        "hostname": device.get("name") or stats.get("name") or "",
        "ip": stats.get("ip") or stats.get("ip_stat", {}).get("ip") or device.get("ip") or "",
        "mac": device.get("mac") or stats.get("mac") or "", "serial": device.get("serial") or stats.get("serial") or "",
        "model": device.get("model") or stats.get("model") or "", "vendor": "juniper",
        "platform": platform, "device_role": role, "firmware": stats.get("version") or device.get("version") or "",
        "operational_status": operational,
        "capabilities": ["inventory", "device-health"] + (["wireless-clients"] if platform == "wireless-access-point" else []),
        "_authoritative_fields": sorted(authoritative),
    }
    if "claimed" in combined: result["claimed"] = bool(combined["claimed"])
    if "managed" in combined: result["managed"] = bool(combined["managed"])
    return result


METRIC_FIELDS = {
    "uptime": "uptime_seconds", "uptime_seconds": "uptime_seconds", "cpu_util": "cpu_percent",
    "cpu_percent": "cpu_percent", "mem_used_percent": "memory_used_percent",
    "memory_used_percent": "memory_used_percent", "temperature": "temperature_celsius",
    "temperature_celsius": "temperature_celsius", "num_clients": "client_count",
    "temp": "temperature_celsius",
    "client_count": "client_count", "rx_bytes": "rx_bytes", "tx_bytes": "tx_bytes",
    "rx_bps": "rx_bps", "tx_bps": "tx_bps",
}


def metric_fields(stats):
    values = {"online": str(stats.get("status", "")).lower() in ("connected", "online")}
    if "status" not in stats: values.pop("online")
    for source, target in METRIC_FIELDS.items():
        if source in stats and stats[source] not in (None, ""): values[target] = stats[source]
    if "memory_used_percent" not in values and stats.get("mem_total_kb") and stats.get("mem_used_kb") is not None:
        values["memory_used_percent"] = 100.0 * stats["mem_used_kb"] / stats["mem_total_kb"]
    return values


def metric_points(record, stats):
    fields = metric_fields(stats)
    if not fields: return []
    common = {key: record.get(key) for key in ("customer", "site", "vendor", "platform", "device_role", "collector", "hostname", "model")}
    common["device_id"] = record["id"]
    points = [{"measurement": "infrastructure_device", "tags": common, "fields": fields}]
    device_fields = {"online": fields.get("online", False)}
    for key in ("uptime_seconds",):
        if key in fields: device_fields[key] = fields[key]
    points.append({"measurement": "device", "tags": common, "fields": device_fields})
    if "online" in fields:
        points.append({"measurement": "availability", "tags": common,
                       "fields": {"available": fields["online"]}})
    performance = {key: fields[key] for key in
                   ("cpu_percent", "memory_used_percent", "uptime_seconds") if key in fields}
    if performance:
        points.append({"measurement": "performance", "tags": common, "fields": performance})
    if record["platform"] == "wireless-access-point":
        ap_tags = {key: common[key] for key in ("customer", "site", "vendor", "collector", "device_id", "hostname", "model")}
        points.append({"measurement": "wireless_access_point", "tags": ap_tags, "fields": fields})
        wireless = {key: fields[key] for key in
                    ("online", "client_count", "rx_bytes", "tx_bytes", "rx_bps", "tx_bps")
                    if key in fields}
        if wireless: points.append({"measurement": "wireless", "tags": ap_tags, "fields": wireless})
    return points
