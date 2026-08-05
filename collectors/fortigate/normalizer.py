"""Normalize version-variable FortiGate API responses."""
import hashlib
import json


def payload(value):
    if isinstance(value, dict):
        for key in ("results", "data"):
            if key in value: return value[key]
    return value


def first_mapping(value):
    value = payload(value)
    if isinstance(value, dict): return value
    if isinstance(value, list): return next((item for item in value if isinstance(item, dict)), {})
    return {}


def pick(mapping, *keys, default=None):
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""): return value
    return default


def stable_id(status, host):
    serial = pick(status, "serial", "serial_number", "serial-number")
    if serial: return f"fortigate:{serial}"
    basis = str(pick(status, "hostname", "name", default=host)).lower()
    return "fortigate:fallback:" + hashlib.sha256(basis.encode()).hexdigest()[:24]


def _percent(value):
    if isinstance(value, dict):
        value = pick(value, "current", "value", "percent")
    try: return float(value) if value is not None else None
    except (TypeError, ValueError): return None


def normalize(endpoints, config, observed_at=None):
    system = first_mapping(endpoints.get("system"))
    resources = first_mapping(endpoints.get("resources"))
    host = config.base_url.split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    hostname = str(pick(system, "hostname", "name", default=host))
    device_id = stable_id(system, host)
    serial = str(pick(system, "serial", "serial_number", "serial-number", default=""))
    model = str(pick(system, "model", "platform", default=""))
    firmware = str(pick(system, "version", "firmware", "firmware_version", default=""))
    management_ip = str(pick(system, "management_ip", "ip", default=host))
    cpu = _percent(pick(resources, "cpu", "cpu_percent", "cpu_usage"))
    memory = _percent(pick(resources, "memory", "memory_percent", "mem", "memory_usage"))
    uptime = pick(system, "uptime", "uptime_seconds")
    record = {"id": device_id, "source": "fortigate", "collector": "fortigate",
        "source_asset_id": serial or hostname.lower(), "customer": config.customer, "site": config.site,
        "hostname": hostname, "ip": management_ip, "serial": serial, "model": model,
        "vendor": "fortinet", "platform": "fortigate", "device_role": "firewall",
        "firmware": firmware, "operational_status": "online",
        "source_record_id": device_id, "source_last_seen_at": observed_at,
        "_authoritative_fields": ["customer", "site", "hostname", "management_ip", "serial_number",
                                  "model", "vendor", "platform", "device_role", "firmware_version"]}
    common = {"collector": "fortigate", "customer": config.customer, "site": config.site,
              "device_id": device_id, "hostname": hostname, "vendor": "fortinet",
              "platform": "fortigate", "device_role": "firewall",
              "model": model}
    fields = {"online": True}
    if uptime is not None:
        try: fields["uptime_seconds"] = float(uptime)
        except (TypeError, ValueError): pass
    if cpu is not None: fields["cpu_percent"] = cpu
    if memory is not None: fields["memory_used_percent"] = memory
    if serial: fields["serial"] = serial
    if firmware: fields["firmware"] = firmware
    points = [{"measurement": "infrastructure_device", "tags": common,
               "fields": fields}]
    points.append({"measurement": "device", "tags": common,
                   "fields": {key: value for key, value in fields.items()
                              if key in ("online", "uptime_seconds", "serial", "firmware")}})
    points.append({"measurement": "availability", "tags": common, "fields": {"available": True}})
    performance = {key: value for key, value in fields.items()
                   if key in ("cpu_percent", "memory_used_percent", "uptime_seconds")}
    if performance: points.append({"measurement": "performance", "tags": common, "fields": performance})
    security = {key: value for key, value in {"cpu_percent": cpu, "memory_used_percent": memory,
        "firmware": firmware, "last_seen": pick(system, "last_seen")}.items() if value not in (None, "")}
    ha = first_mapping(endpoints.get("ha")); security["ha_mode"] = str(pick(ha, "mode", "ha_mode", default="not_collected")); security["ha_status"] = str(pick(ha, "status", "ha_status", default="not_collected"))
    sessions = pick(resources, "session_count", "current_sessions", "sessions")
    if sessions is not None: security["session_count"] = sessions
    points.append({"measurement": "security_appliance", "tags": common, "fields": security})
    points.append({"measurement": "firewall", "tags": common, "fields": dict(security)})
    interfaces = payload(endpoints.get("interfaces"))
    if isinstance(interfaces, dict):
        interfaces = [({"name": name, **item} if isinstance(item, dict) else
                       {"name": name}) for name, item in interfaces.items()]
    configured_wan = {value.name: value for value in getattr(
        config, "wan_interfaces", ())}
    observed_interfaces = []
    for index, item in enumerate(interfaces if isinstance(interfaces, list) else []):
        if not isinstance(item, dict): continue
        name = str(pick(item, "name", "interface_name", "interface", default=f"interface-{index}"))
        itags = {**common, "interface_id": str(pick(item, "id", "index", default=name)), "interface_name": name}
        if (description := pick(item, "description", "alias")): itags["interface_description"] = str(description)
        role = pick(item, "role", "interface_role");
        if role: itags["interface_role"] = str(role)
        aliases = {"admin_status": ("admin_status", "status"), "operational_status": ("operational_status", "link"),
            "speed_bps": ("speed_bps", "speed"), "rx_bytes": ("rx_bytes", "in_octets"), "tx_bytes": ("tx_bytes", "out_octets"),
            "rx_bps": ("rx_bps",), "tx_bps": ("tx_bps",), "rx_errors": ("rx_errors", "in_errors"),
            "tx_errors": ("tx_errors", "out_errors"), "rx_discards": ("rx_discards", "in_discards"), "tx_discards": ("tx_discards", "out_discards")}
        ifields = {target: pick(item, *sources) for target, sources in aliases.items()}
        ifields = {key: value for key, value in ifields.items() if value not in (None, "")}
        observed_interfaces.append((name, item, itags, ifields))
        selected = configured_wan.get(name)
        ifields.update({
            "wan_classified": selected is not None,
            "wan_role": selected.role if selected else "not_configured",
            "wan_display_name": selected.display_name if selected else
                str(itags.get("interface_description") or name),
        })
        if ifields:
            points.append({"measurement": "network_interface", "tags": itags, "fields": ifields})
            points.append({"measurement": "interface", "tags": itags, "fields": dict(ifields)})
    discovered = {name for name, _item, _tags, _fields in observed_interfaces}
    wan_interfaces = []
    for name, item, _tags, values in observed_interfaces:
        selected = configured_wan.get(name)
        if not selected:
            continue
        state = values.get("operational_status")
        state_text = str(state).strip().casefold()
        available = (state if isinstance(state, bool) else
                     True if state_text in {"1", "true", "up", "active"} else
                     False if state_text in {"0", "2", "false", "down", "inactive"} else None)
        observation = {
            "name": name, "interface_name": name,
            "display_name": selected.display_name, "role": selected.role,
            "available": available, "classification_authoritative": True,
            "collector": "fortigate", "device": hostname,
            "device_id": device_id, "observed_at": observed_at,
            "speed_bps": values.get("speed_bps"),
            "rx_bytes_total": values.get("rx_bytes"),
            "tx_bytes_total": values.get("tx_bytes"),
            "rx_bps": values.get("rx_bps"), "tx_bps": values.get("tx_bps"),
        }
        direct_sample = {"time": observed_at,
                         "rx_bps": observation.get("rx_bps"),
                         "tx_bps": observation.get("tx_bps")}
        direct_sample = {key: value for key, value in direct_sample.items()
                         if value is not None}
        if len(direct_sample) > 1:
            observation["samples"] = [direct_sample]
        wan_interfaces.append({key: value for key, value in observation.items()
                               if value is not None})
    for name in sorted(set(configured_wan) - discovered):
        selected = configured_wan[name]
        wan_interfaces.append({
            "name": name, "interface_name": name,
            "display_name": selected.display_name, "role": selected.role,
            "available": None, "classification_authoritative": True,
            "collector": "fortigate", "device": hostname,
            "device_id": device_id, "observed_at": observed_at,
            "missing": True,
        })
    record["extensions"] = {
        "interfaces": [{"interface_name": name,
                        "alias": tags.get("interface_description", ""),
                        **values} for name, _item, tags, values in observed_interfaces],
        "wan_interfaces": wan_interfaces,
        "wan_validation": {"configured": bool(configured_wan),
                           "missing": sorted(set(configured_wan) - discovered)},
        "ha": {"mode": security["ha_mode"], "status": security["ha_status"]},
    }
    return record, points
