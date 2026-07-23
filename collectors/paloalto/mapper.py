"""Map parsed PAN-OS observations to canonical inventory and telemetry."""
import hashlib
from urllib.parse import urlsplit


def _id(system, base_url):
    serial = system.get("serial")
    if serial: return f"paloalto:{serial}"
    basis = system.get("hostname") or system.get("management_ip") or urlsplit(base_url).hostname or "unknown"
    return "paloalto:fallback:" + hashlib.sha256(str(basis).lower().encode()).hexdigest()[:24]


def map_snapshot(parsed, config, observed_at):
    system = parsed["system"]; ha = parsed.get("ha") or {"status": "unavailable"}
    host = urlsplit(config.base_url).hostname or ""
    identifier = _id(system, config.base_url)
    hostname = system.get("hostname") or host
    management_ip = system.get("management_ip") or host
    expected = set(config.expected_interfaces)
    interfaces = parsed.get("interfaces") or []
    down = sorted(item["name"] for item in interfaces
                  if item["name"] in expected
                  and str(item.get("operational_status", "")).lower() not in {"up", "active"})
    licence_expired = [item["name"] for item in parsed.get("licenses", []) if item.get("expired") is True]
    status = "warning" if ha.get("status") == "degraded" or down or licence_expired else "online"
    extensions = {"ha": ha, "interfaces": interfaces,
        "interface_summary": {"total": len(interfaces), "expected": len(expected),
                              "expected_down": down},
        "content_versions": system.get("content_versions", {}),
        "licenses": parsed.get("licenses", []), "resources": parsed.get("resources", {})}
    record = {"id": identifier, "source": "paloalto", "collector": "paloalto",
        "source_asset_id": system.get("serial") or hostname.lower(),
        "source_record_id": identifier, "customer": config.customer, "site": config.site,
        "hostname": hostname, "display_name": hostname, "management_ip": management_ip,
        "serial_number": system.get("serial"), "model": system.get("model"),
        "vendor": "Palo Alto Networks", "platform": "PAN-OS",
        "device_type": "firewall", "device_role": "firewall",
        "firmware_version": system.get("software_version"),
        "online": True, "operational_status": status,
        "source_last_seen_at": observed_at, "extensions": extensions,
        "_authoritative_fields": ["customer", "site", "hostname", "management_ip",
            "serial_number", "model", "vendor", "platform", "device_type", "device_role",
            "firmware_version", "online"]}
    tags = {"collector": "paloalto", "customer": config.customer, "site": config.site,
        "device_id": identifier, "hostname": hostname, "vendor": "Palo Alto Networks",
        "platform": "PAN-OS", "device_role": "firewall"}
    device_fields = {"online": True}
    for key, value in (("model", system.get("model")), ("serial", system.get("serial")),
                       ("firmware", system.get("software_version")),
                       ("uptime_seconds", system.get("uptime_seconds"))):
        if value not in (None, ""): device_fields[key] = value
    points = [
        {"measurement": "device", "tags": tags, "fields": device_fields},
        {"measurement": "availability", "tags": tags, "fields": {"available": True}},
        {"measurement": "firewall", "tags": tags, "fields": {
            "ha_status": ha.get("status", "unavailable"),
            "ha_mode": ha.get("mode") or "not_collected",
            "software_version": system.get("software_version") or "not_collected"}},
    ]
    resources = parsed.get("resources") or {}
    if resources: points.append({"measurement": "performance", "tags": tags, "fields": resources})
    for item in interfaces:
        itags = {**tags, "interface_id": item["name"], "interface_name": item["name"]}
        fields = {key: value for key, value in {
            "admin_status": item.get("admin_status"),
            "operational_status": item.get("operational_status"),
            "speed": item.get("speed"), "duplex": item.get("duplex"),
            "logical": item.get("logical"), "aggregate_group": item.get("aggregate_group"),
        }.items() if value not in (None, "")}
        if fields: points.append({"measurement": "interface", "tags": itags, "fields": fields})
    return record, points
