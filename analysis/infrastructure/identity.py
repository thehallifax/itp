"""Stable normalization and deterministic identity evidence."""
from __future__ import annotations

import hashlib
import ipaddress
import re


def normalize_serial(value):
    return re.sub(r"\s+", "", str(value or "")).upper() or None


def normalize_hostname(value):
    value = str(value or "").strip().lower().rstrip(".")
    return value or None


def short_hostname(value):
    value = normalize_hostname(value)
    return value.split(".", 1)[0] if value else None


def normalize_ip(value):
    try: return str(ipaddress.ip_address(str(value or "").strip()))
    except ValueError: return None


def normalize_mac(value):
    compact = "".join(character for character in str(value or "").lower()
                      if character in "0123456789abcdef")
    return compact if len(compact) == 12 else None


def normalize_site(value):
    value = " ".join(str(value or "").strip().lower().split())
    return value or None


def normalize_device_type(value):
    value = str(value or "").strip().lower().replace("_", "-").replace(" ", "-")
    aliases = {"network-switch": "switch", "wireless-access-point": "access-point",
               "wireless-ap": "access-point", "security-appliance": "firewall",
               "printer-device": "printer"}
    return aliases.get(value, value) or None


def normalized(record):
    kind = normalize_device_type(record.get("device_type") or record.get("device_role") or record.get("platform"))
    return {"serial": normalize_serial(record.get("serial_number") or record.get("serial")),
        "hostname": normalize_hostname(record.get("hostname") or record.get("display_name")),
        "short_hostname": short_hostname(record.get("hostname") or record.get("display_name")),
        "management_ip": normalize_ip(record.get("management_ip") or record.get("device_ip") or record.get("ip")),
        "management_mac": normalize_mac(record.get("management_mac") or record.get("mac_address") or record.get("mac")),
        "chassis_id": normalize_mac(record.get("chassis_mac") or record.get("chassis_id")),
        "site": normalize_site(record.get("site_id") or record.get("site")), "device_type": kind}


def compatible_types(left, right):
    left = normalize_device_type(left); right = normalize_device_type(right)
    if not left or not right: return True
    families = ({"switch", "router", "network-device"}, {"access-point", "wireless"},
                {"firewall"}, {"printer"}, {"server"}, {"storage", "nas"}, {"ups"})
    return left == right or any(left in family and right in family for family in families)


def canonical_id(records):
    values = [normalized(record) for record in records]
    candidates = []
    for key in ("serial", "chassis_id", "management_mac"):
        candidates = sorted({value[key] for value in values if value[key]})
        if candidates: identity = f"{key}:{candidates[0]}"; break
    else:
        pairs = sorted({f"{value['short_hostname']}|{value['site']}" for value in values
                        if value["short_hostname"] and value["site"]})
        if pairs: identity = "hostname-site:" + pairs[0]
        else:
            pairs = sorted({f"{value['management_ip']}|{value['site']}" for value in values
                            if value["management_ip"] and value["site"]})
            if pairs: identity = "ip-site:" + pairs[0]
            else:
                source_ids = sorted(str(record.get("source_asset_id") or record.get("asset_id") or "")
                                    for record in records)
                identity = "source:" + source_ids[0]
    partitions = sorted({
        f"{str(record.get('deployment_id') or '').casefold()}|"
        f"{str(record.get('customer_id') or record.get('customer') or '').casefold()}"
        for record in records})
    partition = partitions[0] if partitions else "|"
    return "asset:canonical:" + hashlib.sha256(
        f"{partition}|{identity}".encode()).hexdigest()[:24]
