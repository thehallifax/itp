#!/usr/bin/env python3
"""Safe, bounded SNMP discovery and Telegraf target generation."""
import ipaddress
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

LOG = logging.getLogger("snmp-discovery")
OIDS = ("1.3.6.1.2.1.1.1.0", "1.3.6.1.2.1.1.2.0", "1.3.6.1.2.1.1.5.0", "1.3.6.1.2.1.1.6.0")
ENTERPRISES = {
    "12356": ("fortinet", "fortigate", "firewall"),
    "14823": ("aruba", "network-switch", "switch"),
    "11": ("hpe", "network-switch", "switch"),
    # Juniper's enterprise tree spans switches, routers and Mist APs. It is
    # intentionally handled by model/description rules in classify().
    "9": ("cisco", "network-switch", "switch"),
    "1347": ("kyocera", "printer", "printer"),
    "367": ("ricoh", "printer", "printer"),
    "1602": ("canon", "printer", "printer"),
    "2435": ("brother", "printer", "printer"),
    "641": ("lexmark", "printer", "printer"),
    "1248": ("epson", "printer", "printer"),
    "318": ("apc", "ups", "ups"),
    "534": ("eaton", "ups", "ups"),
    "6574": ("synology", "synology", "nas"),
}

WIRELESS_ENTERPRISES = {
    "2636": "juniper", "14823": "aruba", "11": "aruba", "9": "cisco",
    "25053": "ruckus", "1916": "extreme", "45": "extreme", "41112": "ubiquiti",
}
AP_MODEL_MARKERS = {
    "juniper": ("mist ap", "mist edge ap", "ap12", "ap21", "ap32", "ap33", "ap34", "ap41", "ap43", "ap45", "ap47", "ap61", "ap63"),
    "aruba": ("aruba ap-", "aruba iap-", "instant ap", "hpe aruba ap"),
    "cisco": ("air-ap", "aironet access point", "cisco ap", "c910", "c911", "c912", "c913", "cw91"),
    "ruckus": ("ruckus access point", "ruckus wireless ap", "zoneflex", "ruckus r3", "ruckus r5", "ruckus r6", "ruckus t3", "ruckus t5", "ruckus t6"),
    "extreme": ("extreme access point", "extreme wireless ap", "aerohive", "wing ap"),
    "ubiquiti": ("unifi ap", "ubiquiti access point", "uap-", "unifi u6", "unifi u7"),
}
JUNIPER_SWITCH_OBJECT_IDS = {
    # Observed, repeatable model IDs for the site's Juniper/Mist-managed EX
    # switches. Keep these exact: the surrounding Juniper tree is heterogeneous.
    "1.3.6.1.4.1.2636.1.1.1.2.109",
    "1.3.6.1.4.1.2636.1.1.1.4.132.3",
    "1.3.6.1.4.1.2636.1.1.1.4.132.4",
    "1.3.6.1.4.1.2636.1.1.1.4.132.6",
    "1.3.6.1.4.1.2636.1.1.1.4.132.8",
}


def utcnow():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def classify(sys_object_id, description="", hostname="", purpose=None):
    oid = str(sys_object_id).lstrip(".")
    parts = oid.split(".")
    enterprise = parts[6] if parts[:6] == ["1", "3", "6", "1", "4", "1"] and len(parts) > 6 else None
    text = f"{description} {hostname}".lower()
    description_text = str(description).lower()
    hostname_text = str(hostname).lower()
    vendor_hint = WIRELESS_ENTERPRISES.get(enterprise)
    if oid in JUNIPER_SWITCH_OBJECT_IDS:
        return "juniper", "network-switch", "switch"
    # Model/description evidence precedes generic enterprise classification so
    # an AP living in a vendor's broad enterprise tree is not called a switch.
    for vendor, markers in AP_MODEL_MARKERS.items():
        if any(marker in description_text for marker in markers):
            if vendor_hint in (None, vendor) or (vendor == "aruba" and enterprise in ("11", "14823")):
                return vendor, "wireless-access-point", "access-point"
    if vendor_hint and ("access point" in description_text or "wireless ap" in description_text):
        return vendor_hint, "wireless-access-point", "access-point"
    for vendor, markers in AP_MODEL_MARKERS.items():
        if vendor_hint == vendor and any(marker in hostname_text for marker in markers):
            return vendor, "wireless-access-point", "access-point"
    # A wireless purpose can break a weak vendor-text tie, but never turns an
    # otherwise unidentified host—or an unknown Juniper product—into an AP.
    if purpose == "wireless" and vendor_hint != "juniper" and vendor_hint and vendor_hint in description_text:
        return vendor_hint, "wireless-access-point", "access-point"
    if enterprise == "2636":
        if any(marker in description_text for marker in ("juniper ex", " ex2", " ex3", " ex4", " ex9")):
            return "juniper", "network-switch", "switch"
        return "juniper", "unknown", "unknown"
    # Enterprise 11 covers both HPE networking and HP printers; the product text
    # resolves that otherwise-ambiguous branch.
    if enterprise == "11" and ("printer" in text or "laserjet" in text or "officejet" in text):
        return "hp", "printer", "printer"
    if enterprise in ENTERPRISES:
        return ENTERPRISES[enterprise]
    signals = (("fortigate", "fortinet"), ("aruba", "aruba"), ("procurve", "hpe"),
               ("juniper", "juniper"), ("cisco", "cisco"), ("kyocera", "kyocera"),
               ("ricoh", "ricoh"), ("canon", "canon"), ("brother", "brother"),
               ("lexmark", "lexmark"), ("epson", "epson"), ("synology", "synology"),
               ("apc", "apc"), ("eaton", "eaton"))
    for needle, vendor in signals:
        if needle in text:
            if vendor == "fortinet": return vendor, "fortigate", "firewall"
            if vendor == "synology": return vendor, "synology", "nas"
            if vendor in ("apc", "eaton"): return vendor, "ups", "ups"
            if vendor in ("aruba", "hpe", "juniper", "cisco"): return vendor, "network-switch", "switch"
            return vendor, "printer", "printer"
    if "printer" in text or "laserjet" in text:
        return ("hp" if "hp" in text or "hewlett" in text else "unknown", "printer", "printer")
    return "unknown", "unknown", "unknown"


def load_config(path):
    try:
        data = yaml.safe_load(Path(path).read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read valid YAML config {path}: {exc}") from exc
    if not isinstance(data, dict): raise ValueError("configuration must be a YAML mapping")
    for key in ("customer", "site", "discovery", "snmp", "networks"):
        if not data.get(key): raise ValueError(f"missing required configuration key: {key}")
    if data["snmp"].get("version") != 2: raise ValueError("only SNMP version 2 is supported")
    if not data["snmp"].get("communities"): raise ValueError("at least one SNMP community is required")
    addresses = enumerate_addresses(data)
    if not addresses: LOG.warning("configuration contains no scannable addresses")
    return data


def enumerate_addresses(config):
    return [ip for ip, _ in enumerate_targets(config)]


def enumerate_targets(config):
    excluded = {ipaddress.ip_address(x) for x in config.get("exclusions", [])}
    result = {}
    for item in config["networks"]:
        net = ipaddress.ip_network(item["cidr"], strict=True)
        if net.version != 4: raise ValueError(f"IPv6 is not supported: {net}")
        if net.prefixlen < 22 and not config["discovery"].get("allow_large_networks", False):
            raise ValueError(f"network {net} is broader than /22; set discovery.allow_large_networks to true")
        purpose = str(item.get("purpose", "")).lower() or None
        for ip in net.hosts():
            if ip not in excluded: result.setdefault(str(ip), purpose)
    return sorted(result.items(), key=lambda item: ipaddress.ip_address(item[0]))


async def snmp_get(ip, communities, timeout, retries, semaphore):
    from pysnmp.hlapi.v3arch.asyncio import (CommunityData, ContextData, ObjectIdentity,
        ObjectType, SnmpEngine, UdpTransportTarget, get_cmd)
    async with semaphore:
        engine = SnmpEngine()
        try:
            target = await UdpTransportTarget.create((ip, 161), timeout=timeout, retries=retries)
            for index, community in enumerate(communities):
                error_indication, error_status, _, var_binds = await get_cmd(
                    engine, CommunityData(community, mpModel=1), target, ContextData(),
                    *(ObjectType(ObjectIdentity(oid)) for oid in OIDS))
                if not error_indication and not error_status:
                    return index, [str(value) for _, value in var_binds]
        finally:
            engine.close_dispatcher()
    return None


def merge_inventory(config, discoveries, previous, now=None):
    now = now or utcnow()
    identity_tags = {
        "deployment_id": config.get("deployment_id", ""),
        "customer_id": config.get("customer_id", config["customer"]),
        "site_id": config.get("site_id", config["site"]),
        "customer": config.get("customer_id", config["customer"]),
        "site": config.get("site_id", config["site"]),
    }
    snmp_devices = [d for d in previous.get("devices", [])
                    if d.get("source") in (None, "snmp") and d.get("ip") and d.get("sys_object_id")]
    other_devices = [d for d in previous.get("devices", []) if d not in snmp_devices]
    prior = {(d["ip"], d["sys_object_id"]): d for d in snmp_devices}
    active_ips = set()
    devices = []
    for discovery in discoveries:
        ip, community_index, values = discovery[:3]
        purpose = discovery[3] if len(discovery) > 3 else None
        description, object_id, hostname, location = values
        vendor, platform, role = classify(object_id, description, hostname, purpose)
        old = prior.get((ip, object_id), {})
        active_ips.add(ip)
        devices.append({**identity_tags,
            "ip": ip, "hostname": hostname, "description": description,
            "sys_object_id": object_id, "location": location, "vendor": vendor,
            "platform": platform, "device_role": role, "snmp_version": 2,
            "community_index": community_index, "first_seen": old.get("first_seen", now),
            "last_seen": now, "status": "active"})
    cutoff = datetime.fromisoformat(now.replace("Z", "+00:00")) - timedelta(days=7)
    for old in snmp_devices:
        if old["ip"] in active_ips: continue
        try: last = datetime.fromisoformat(old["last_seen"].replace("Z", "+00:00"))
        except (KeyError, ValueError): continue
        if last >= cutoff:
            retained = {
                **old, **identity_tags, "status": "unreachable"}
            devices.append(retained)
    devices.sort(key=lambda d: ipaddress.ip_address(d["ip"]))
    devices.extend(other_devices)
    return {
        "schema_version": 1, "generated_at": now,
        "deployment_id": config.get("deployment_id", ""),
        "customer_id": config.get("customer_id", config["customer"]),
        "site_id": config.get("site_id", config["site"]),
        "customer": config["customer"], "site": config["site"],
        "devices": devices}


def toml_string(value):
    return json.dumps(str(value), ensure_ascii=False)


def group_devices(inventory):
    groups = {}
    for d in inventory["devices"]:
        if d.get("source") not in (None, "snmp"): continue
        if d["status"] != "active" or d["platform"] not in ("printer", "network-switch", "wireless-access-point", "ups", "synology"): continue
        key = (
            inventory["customer"], inventory["site"], d["vendor"],
            d["platform"], d["device_role"], d["community_index"],
            inventory.get("deployment_id", ""))
        groups.setdefault(key, []).append(d)
    return groups


def render_group(key, devices, communities):
    customer, site, vendor, platform, role, community_index, deployment_id = key
    measurement = {"printer": "printer_status", "network-switch": "network_device", "wireless-access-point": "wireless_access_point", "ups": "ups_status", "synology": "synology_status"}[platform]
    interval = "60s" if platform == "printer" else "30s"
    agents = ",\n    ".join(toml_string(f"udp://{d['ip']}:161") for d in sorted(devices, key=lambda x: ipaddress.ip_address(x["ip"])))
    out = f'''[[inputs.snmp]]
  agents = [
    {agents}
  ]
  version = 2
  community = {toml_string(communities[community_index])}
  timeout = "5s"
  retries = 2
  interval = {toml_string(interval)}
  name = {toml_string(measurement)}
  agent_host_tag = "device_ip"

  [inputs.snmp.tags]
    collector = "snmp"
    deployment_id = {toml_string(deployment_id)}
    customer_id = {toml_string(customer)}
    customer = {toml_string(customer)}
    site_id = {toml_string(site)}
    site = {toml_string(site)}
    vendor = {toml_string(vendor)}
    platform = {toml_string(platform)}
    device_role = {toml_string(role)}

  [[inputs.snmp.field]]
    name = "hostname"
    oid = "1.3.6.1.2.1.1.5.0"
    is_tag = true
  [[inputs.snmp.field]]
    name = "description"
    oid = "1.3.6.1.2.1.1.1.0"
  [[inputs.snmp.field]]
    name = "uptime_ticks"
    oid = "1.3.6.1.2.1.1.3.0"
'''
    if platform == "wireless-access-point":
        out += '''  [[inputs.snmp.field]]
    name = "sys_object_id"
    oid = "1.3.6.1.2.1.1.2.0"
  [[inputs.snmp.field]]
    name = "location"
    oid = "1.3.6.1.2.1.1.6.0"
    is_tag = true
  [[inputs.snmp.field]]
    name = "contact"
    oid = "1.3.6.1.2.1.1.4.0"
  [[inputs.snmp.table]]
    name = "wireless_interfaces"
    inherit_tags = ["deployment_id", "customer_id", "customer", "site_id", "site", "vendor", "platform", "device_role", "device_ip", "hostname"]
    [[inputs.snmp.table.field]]
      name = "interface_index"
      oid = "1.3.6.1.2.1.2.2.1.1"
      is_tag = true
    [[inputs.snmp.table.field]]
      name = "interface_description"
      oid = "1.3.6.1.2.1.2.2.1.2"
      is_tag = true
    [[inputs.snmp.table.field]]
      name = "interface_name"
      oid = "1.3.6.1.2.1.31.1.1.1.1"
      is_tag = true
    [[inputs.snmp.table.field]]
      name = "interface_alias"
      oid = "1.3.6.1.2.1.31.1.1.1.18"
      is_tag = true
    [[inputs.snmp.table.field]]
      name = "interface_type"
      oid = "1.3.6.1.2.1.2.2.1.3"
    [[inputs.snmp.table.field]]
      name = "interface_mtu"
      oid = "1.3.6.1.2.1.2.2.1.4"
    [[inputs.snmp.table.field]]
      name = "interface_speed_mbps"
      oid = "1.3.6.1.2.1.31.1.1.1.15"
    [[inputs.snmp.table.field]]
      name = "admin_status"
      oid = "1.3.6.1.2.1.2.2.1.7"
    [[inputs.snmp.table.field]]
      name = "operational_status"
      oid = "1.3.6.1.2.1.2.2.1.8"
    [[inputs.snmp.table.field]]
      name = "in_octets"
      oid = "1.3.6.1.2.1.31.1.1.1.6"
    [[inputs.snmp.table.field]]
      name = "out_octets"
      oid = "1.3.6.1.2.1.31.1.1.1.10"
    [[inputs.snmp.table.field]]
      name = "in_errors"
      oid = "1.3.6.1.2.1.2.2.1.14"
    [[inputs.snmp.table.field]]
      name = "out_errors"
      oid = "1.3.6.1.2.1.2.2.1.20"
    [[inputs.snmp.table.field]]
      name = "in_discards"
      oid = "1.3.6.1.2.1.2.2.1.13"
    [[inputs.snmp.table.field]]
      name = "out_discards"
      oid = "1.3.6.1.2.1.2.2.1.19"
    [[inputs.snmp.table.field]]
      name = "last_change_ticks"
      oid = "1.3.6.1.2.1.2.2.1.9"
    [[inputs.snmp.table.field]]
      name = "physical_address"
      oid = "1.3.6.1.2.1.2.2.1.6"
      conversion = "hwaddr"
'''
    if platform == "printer":
        out += '''  [[inputs.snmp.field]]
    name = "device_status"
    oid = "1.3.6.1.2.1.25.3.2.1.5.1"
  [[inputs.snmp.field]]
    name = "printer_status"
    oid = "1.3.6.1.2.1.43.16.5.1.2.1.1"
  [[inputs.snmp.table]]
    name = "printer_supplies"
    inherit_tags = ["deployment_id", "customer_id", "customer", "site_id", "site", "vendor", "platform", "device_role", "device_ip", "hostname"]
    [[inputs.snmp.table.field]]
      name = "supply_description"
      oid = "1.3.6.1.2.1.43.11.1.1.6"
      is_tag = true
    [[inputs.snmp.table.field]]
      name = "supply_type"
      oid = "1.3.6.1.2.1.43.11.1.1.5"
    [[inputs.snmp.table.field]]
      name = "supply_maximum"
      oid = "1.3.6.1.2.1.43.11.1.1.8"
    [[inputs.snmp.table.field]]
      name = "supply_level"
      oid = "1.3.6.1.2.1.43.11.1.1.9"
'''
    elif platform == "network-switch":
        out += '''  [[inputs.snmp.field]]
    name = "location"
    oid = "1.3.6.1.2.1.1.6.0"
    is_tag = true
  [[inputs.snmp.table]]
    name = "network_interfaces"
    inherit_tags = ["deployment_id", "customer_id", "customer", "site_id", "site", "vendor", "platform", "device_role", "device_ip", "hostname"]
    [[inputs.snmp.table.field]]
      name = "interface_index"
      oid = "1.3.6.1.2.1.2.2.1.1"
      is_tag = true
    [[inputs.snmp.table.field]]
      name = "interface_name"
      oid = "1.3.6.1.2.1.31.1.1.1.1"
      is_tag = true
    [[inputs.snmp.table.field]]
      name = "interface_description"
      oid = "1.3.6.1.2.1.2.2.1.2"
      is_tag = true
    [[inputs.snmp.table.field]]
      name = "interface_alias"
      oid = "1.3.6.1.2.1.31.1.1.1.18"
      is_tag = true
    [[inputs.snmp.table.field]]
      name = "admin_status"
      oid = "1.3.6.1.2.1.2.2.1.7"
    [[inputs.snmp.table.field]]
      name = "operational_status"
      oid = "1.3.6.1.2.1.2.2.1.8"
    [[inputs.snmp.table.field]]
      name = "interface_speed_mbps"
      oid = "1.3.6.1.2.1.31.1.1.1.15"
    [[inputs.snmp.table.field]]
      name = "in_octets"
      oid = "1.3.6.1.2.1.31.1.1.1.6"
    [[inputs.snmp.table.field]]
      name = "out_octets"
      oid = "1.3.6.1.2.1.31.1.1.1.10"
    [[inputs.snmp.table.field]]
      name = "in_errors"
      oid = "1.3.6.1.2.1.2.2.1.14"
    [[inputs.snmp.table.field]]
      name = "out_errors"
      oid = "1.3.6.1.2.1.2.2.1.20"
    [[inputs.snmp.table.field]]
      name = "in_discards"
      oid = "1.3.6.1.2.1.2.2.1.13"
    [[inputs.snmp.table.field]]
      name = "out_discards"
      oid = "1.3.6.1.2.1.2.2.1.19"
'''
    return out
