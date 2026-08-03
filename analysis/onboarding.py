"""Reusable guided connector-onboarding helpers."""
from __future__ import annotations

import ipaddress
import socket
import ssl
from datetime import datetime, timezone
from urllib.parse import urlsplit

WAN_ROLES = {"primary", "secondary", "backup", "cellular", "mpls", "other"}


def wan_candidate(interface, vendor):
    name = str(interface.get("interface_name") or interface.get("name") or "")
    alias = str(interface.get("alias") or interface.get("description") or "")
    role = str(interface.get("interface_role") or interface.get("role") or "")
    zone = str(interface.get("zone") or "")
    address = str(interface.get("ip_address") or interface.get("ip") or "").split("/", 1)[0]
    reasons = []
    score = 0
    combined = " ".join((name, alias, role, zone)).casefold()
    if any(word in combined for word in ("wan", "internet", "untrust", "isp")):
        score += 40; reasons.append("WAN/Internet device metadata")
    if vendor == "fortigate" and role.casefold() == "wan":
        score += 50; reasons.append("FortiGate WAN role")
    if interface.get("sdwan_member"):
        score += 50; reasons.append("SD-WAN member")
    try:
        if address and not ipaddress.ip_address(address).is_private:
            score += 35; reasons.append("public address")
    except ValueError:
        pass
    if interface.get("default_route"):
        score += 50; reasons.append("default-route association")
    if str(interface.get("operational_status") or interface.get("status")).casefold() == "up":
        score += 5; reasons.append("active routed interface")
    return {**interface, "interface_name": name,
            "suggested_display_name": alias or name,
            "likely_wan": score >= 40, "confidence": min(score, 100),
            "reason": ", ".join(reasons) or "no authoritative WAN evidence"}


def validate_wan_selection(values, discovered=()):
    names = [str(item.get("name") or "").strip() for item in values]
    if any(not name for name in names):
        raise ValueError("every WAN mapping requires a canonical interface name")
    if len(names) != len(set(names)):
        raise ValueError("WAN interface selection contains duplicates")
    roles = [str(item.get("role") or "").casefold() for item in values]
    invalid = sorted(set(roles) - WAN_ROLES)
    if invalid:
        raise ValueError("unsupported WAN role: " + ", ".join(invalid))
    if roles.count("primary") != 1 and values:
        raise ValueError("WAN mapping requires exactly one primary interface")
    known = {str(item.get("interface_name") or item.get("name"))
             for item in discovered}
    missing = sorted(set(names) - known) if known else []
    return {"mappings": [{"name": name, "role": role,
                           "display_name": str(item.get("display_name") or name)}
                          for name, role, item in zip(names, roles, values)],
            "missing": missing}


def inspect_tls(endpoint, *, timeout=5, context=None,
                resolver=socket.gethostbyname,
                connection_fn=socket.create_connection):
    parsed = urlsplit(endpoint if "://" in endpoint else "https://" + endpoint)
    host = parsed.hostname
    port = parsed.port or 443
    if not host:
        return {"success": False, "category": "invalid_endpoint"}
    try:
        address = resolver(host)
    except socket.gaierror:
        return {"success": False, "category": "dns_failure", "hostname": host}
    try:
        with connection_fn((host, port), timeout=timeout) as raw:
            with (context or ssl.create_default_context()).wrap_socket(
                    raw, server_hostname=host) as connection:
                certificate = connection.getpeercert()
    except ssl.SSLCertVerificationError as exc:
        category = "tls_hostname_mismatch" if "hostname" in str(exc).casefold() \
            else "tls_trust_failure"
        return {"success": False, "category": category, "hostname": host,
                "resolved_address": address,
                "guidance": "Import the private CA with `./itp credentials ca add <certificate> --deployment <id>`."}
    except (TimeoutError, socket.timeout):
        return {"success": False, "category": "timeout", "hostname": host}
    except OSError:
        return {"success": False, "category": "tcp_failure", "hostname": host}
    expires = certificate.get("notAfter")
    expired = False
    if expires:
        try:
            expired = datetime.fromtimestamp(
                ssl.cert_time_to_seconds(expires), timezone.utc) < datetime.now(timezone.utc)
        except ValueError:
            pass
    return {"success": not expired,
            "category": "certificate_expired" if expired else "success",
            "hostname": host, "resolved_address": address,
            "subject": certificate.get("subject"), "issuer": certificate.get("issuer"),
            "san": certificate.get("subjectAltName"), "not_after": expires,
            "trusted": True, "hostname_match": True, "expired": expired}
