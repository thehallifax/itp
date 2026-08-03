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
    wan_by_name = {value.name: value for value in config.wan_interfaces}
    wan_interfaces = []
    for item in interfaces:
        configured = wan_by_name.get(item["name"])
        if not configured:
            continue
        state = str(item.get("operational_status") or "").lower()
        wan_interfaces.append({
            "name": configured.name,
            "interface_name": configured.name,
            "display_name": configured.display_name,
            "role": configured.role,
            "available": True if state in {"up", "active"} else
                         False if state in {"down", "inactive"} else None,
            "classification_authoritative": True,
            "site": config.site_name or config.site, "site_id": config.site,
            "device": hostname, "device_id": identifier,
            "observed_at": observed_at,
            "rx_bytes_total": item.get("rx_bytes_total"),
            "tx_bytes_total": item.get("tx_bytes_total"),
        })
    licenses = parsed.get("licenses", [])
    certificate_status = system.get("device_certificate_status")
    extensions = {"ha": ha, "interfaces": interfaces,
        "interface_summary": {"total": len(interfaces), "expected": len(expected),
                              "expected_down": down},
        "wan_interfaces": wan_interfaces,
        "wan_validation": parsed.get("wan_validation", {
            "configured": bool(config.wan_interfaces), "missing": []}),
        "content_versions": system.get("content_versions", {}),
        "content_packages": parsed.get("content_packages", []),
        "licenses": licenses,
        "device_certificate": {
            "status": certificate_status,
            "classification": (
                "valid" if str(certificate_status).lower() == "valid"
                else "missing" if str(certificate_status).lower() in {"none", "missing", "not installed"}
                else "expired" if str(certificate_status).lower() == "expired"
                else "unknown")},
        "resources": {**(parsed.get("resources") or {}),
                      **(parsed.get("resource_monitor") or {}),
                      **(parsed.get("sessions") or {})}}
    record = {"id": identifier, "source": "paloalto", "collector": "paloalto",
        "source_asset_id": system.get("serial") or hostname.lower(),
        "source_record_id": identifier,
        "deployment_id": config.deployment_id,
        "customer_id": config.customer,
        "customer": config.customer,
        "site_id": config.site,
        "site": config.site_name or config.site,
        "hostname": hostname, "display_name": hostname, "management_ip": management_ip,
        "serial_number": system.get("serial"), "model": system.get("model"),
        "vendor": "Palo Alto Networks", "platform": "PAN-OS",
        "device_type": "firewall", "device_role": "firewall",
        "firmware_version": system.get("software_version"),
        "online": True, "operational_status": status,
        "source_last_seen_at": observed_at, "extensions": extensions,
        "_authoritative_fields": ["customer_id", "site_id", "hostname", "management_ip",
            "serial_number", "model", "vendor", "platform", "device_type", "device_role",
            "firmware_version", "online"]}
    tags = {"collector": "paloalto", "deployment_id": config.deployment_id,
        "customer_id": config.customer, "customer": config.customer,
        "customer_name": config.customer_name,
        "site_id": config.site, "site": config.site,
        "site_name": config.site_name,
        "device_id": identifier, "hostname": hostname, "vendor": "Palo Alto Networks",
        "platform": "PAN-OS", "device_role": "firewall"}
    device_fields = {"online": True}
    for key, value in (("model", system.get("model")), ("serial", system.get("serial")),
                       ("firmware", system.get("software_version")),
                       ("uptime_seconds", system.get("uptime_seconds")),
                       ("platform_family", system.get("platform_family")),
                       ("management_ip", management_ip)):
        if value not in (None, ""): device_fields[key] = value
    points = [
        {"measurement": "device", "tags": tags, "fields": device_fields},
        {"measurement": "availability", "tags": tags, "fields": {"available": True}},
        {"measurement": "firewall", "tags": tags, "fields": {
            "ha_status": ha.get("status", "unavailable"),
            "ha_mode": ha.get("mode") or "not_collected",
            "software_version": system.get("software_version") or "not_collected",
            "device_certificate_status": certificate_status or "unknown",
            "platform_family": system.get("platform_family") or "unknown"}},
    ]
    resources = {**(parsed.get("resources") or {}),
                 **(parsed.get("resource_monitor") or {}),
                 **(parsed.get("sessions") or {})}
    if resources: points.append({"measurement": "performance", "tags": tags, "fields": resources})
    for item in interfaces:
        itags = {**tags, "interface_id": item["name"], "interface_name": item["name"]}
        fields = {key: value for key, value in {
            "admin_status": item.get("admin_status"),
            "operational_status": item.get("operational_status"),
            "speed": item.get("speed"), "duplex": item.get("duplex"),
            "logical": item.get("logical"), "aggregate_group": item.get("aggregate_group"),
            "rx_bytes_total": item.get("rx_bytes_total"),
            "tx_bytes_total": item.get("tx_bytes_total"),
            "rx_packets_total": item.get("rx_packets_total"),
            "tx_packets_total": item.get("tx_packets_total"),
            "rx_errors_total": item.get("rx_errors_total"),
            "tx_errors_total": item.get("tx_errors_total"),
            "rx_discards_total": item.get("rx_discards_total"),
            "tx_discards_total": item.get("tx_discards_total"),
            "link_flaps_total": item.get("link_flaps_total"),
            "wan_classified": item["name"] in wan_by_name,
            "wan_role": wan_by_name[item["name"]].role if item["name"] in wan_by_name else None,
            "wan_display_name": wan_by_name[item["name"]].display_name
                                if item["name"] in wan_by_name else None,
        }.items() if value not in (None, "")}
        if fields: points.append({"measurement": "interface", "tags": itags, "fields": fields})
    for item in licenses:
        ltags = {**tags, "subscription_name": item["name"]}
        fields = {key: value for key, value in {
            "status": item.get("status") or item.get("expiry_state"),
            "expiry_date": item.get("expiry"),
            "remaining_days": item.get("days_remaining"),
            "expired_days": item.get("expired_days"),
            "expired": item.get("expired"),
            "perpetual": item.get("perpetual"),
            "expiry_state": item.get("expiry_state"),
            "raw_status": item.get("raw_status"),
            "raw_expiry": item.get("raw_expiry"),
        }.items() if value not in (None, "")}
        if fields:
            points.append({"measurement": "license", "tags": ltags, "fields": fields})
    for item in parsed.get("content_packages", []):
        ctags = {**tags, "package_name": item["package_name"]}
        fields = {key: value for key, value in {
            "version": item.get("version"),
            "release_time": item.get("release_time"),
            "release_time_raw": item.get("release_time_raw"),
            "age_days": item.get("age_days"),
        }.items() if value not in (None, "")}
        if fields:
            points.append({"measurement": "content_package", "tags": ctags, "fields": fields})
    return record, points
