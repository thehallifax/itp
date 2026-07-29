"""Normalize Aruba Central inventory into OPS-014 canonical identities."""
from __future__ import annotations


def _items(payload, keys):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _device_items(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    direct = payload.get("devices") or payload.get("items") or payload.get("data")
    if isinstance(direct, list):
        return direct
    values = []
    for key in ("aps", "switches", "gateways", "controllers"):
        if isinstance(payload.get(key), list):
            values.extend(payload[key])
    return values


def _value(item, *keys):
    for key in keys:
        if item.get(key) not in (None, ""):
            return item[key]
    return None


def _device_type(item):
    value = str(_value(item, "device_type", "type", "category") or "network").lower()
    if value in {"ap", "iap", "access_point"}:
        return "access-point"
    if "switch" in value:
        return "switch"
    if value in {"gw", "gateway", "controller"}:
        return "gateway"
    return value.replace("_", "-")


def normalize(snapshot, config, observed_at):
    device_classes = snapshot.get("device_classes")
    if isinstance(device_classes, dict):
        devices = []
        for name in ("access_points", "switches", "gateways"):
            devices.extend(_device_items(device_classes.get(name)))
    else:
        # Compatibility with the initial combined-device response fixture.
        devices = _device_items(snapshot.get("devices"))
    records, points = [], []
    for item in sorted(
            (value for value in devices if isinstance(value, dict)),
            key=lambda value: str(_value(value, "serial", "serial_number") or "")):
        serial = str(_value(item, "serial", "serial_number") or "").strip()
        if not serial:
            continue
        identifier = f"aruba:{serial}"
        hostname = str(_value(item, "name", "hostname") or serial)
        status = str(_value(item, "status", "health", "state") or "unknown")
        normalized_status = status.casefold()
        online = (
            True if normalized_status in {
                "up", "online", "good", "healthy", "connected"}
            else False if normalized_status in {
                "down", "offline", "bad", "critical", "disconnected"}
            else None)
        device_type = _device_type(item)
        firmware = _value(item, "firmware_version", "firmware", "version", "sw_version")
        site = _value(item, "site", "site_name")
        group = _value(item, "group", "group_name")
        last_seen = _value(
            item, "last_seen", "last_seen_at", "last_connected_at") or observed_at
        extensions = {"aruba_central": {
            "account_id": config.account_id,
            "group": group, "central_site": site,
            "subscription": _value(
                item, "subscription", "subscription_key", "subscription_status"),
            "mac": _value(item, "mac", "macaddr", "mac_address"),
            "client_count": _value(
                item, "client_count", "clients", "connected_clients"),
        }}
        record = {
            "id": identifier, "source": "aruba", "collector": "aruba",
            "source_asset_id": serial, "source_record_id": identifier,
            "deployment_id": config.deployment_id,
            "customer_id": config.customer, "customer": config.customer,
            "site_id": config.site, "site": config.site_name or config.site,
            "hostname": hostname, "display_name": hostname,
            "serial_number": serial,
            "model": _value(item, "model", "model_name"),
            "vendor": "HPE Aruba Networking", "platform": "Aruba Central",
            "device_type": device_type,
            "device_role": device_type,
            "firmware_version": firmware,
            "management_ip": _value(
                item, "ip_address", "ip", "management_ip"),
            "online": online, "operational_status": normalized_status,
            "source_last_seen_at": last_seen,
            "extensions": extensions,
            "_authoritative_fields": [
                "customer_id", "site_id", "hostname", "serial_number", "model",
                "vendor", "platform", "device_type", "device_role",
                "firmware_version", "management_ip", "online"],
        }
        records.append(record)
        tags = {
            "collector": "aruba", "deployment_id": config.deployment_id,
            "customer_id": config.customer, "customer": config.customer,
            "customer_name": config.customer_name,
            "site_id": config.site, "site": config.site,
            "site_name": config.site_name,
            "device_id": identifier, "hostname": hostname,
            "device_role": device_type, "vendor": "HPE Aruba Networking",
            "platform": "Aruba Central",
        }
        fields = {
            "serial": serial, "status": status,
            "last_seen": str(last_seen),
        }
        if online is not None:
            fields["online"] = online
        for key, value in {
            "model": record["model"], "firmware": firmware,
            "management_ip": record["management_ip"],
            "mac": extensions["aruba_central"]["mac"],
            "group": group, "central_site": site,
            "subscription": extensions["aruba_central"]["subscription"],
        }.items():
            if value not in (None, ""):
                fields[key] = value
        availability_fields = {"status": status}
        if online is not None:
            availability_fields["available"] = online
        points.extend([
            {"measurement": "device", "tags": tags, "fields": fields},
            {"measurement": "availability", "tags": tags,
             "fields": availability_fields},
        ])
        clients = extensions["aruba_central"]["client_count"]
        if device_type == "access-point" and clients is not None:
            points.append({
                "measurement": "wireless", "tags": tags,
                "fields": {"clients_connected": int(clients)}})
    return records, points
