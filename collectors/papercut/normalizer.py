"""Normalize PaperCut System Health data into canonical ITP records."""
import hashlib
from urllib.parse import urlsplit


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    if isinstance(value, list):
        return value
    return [] if value in (None, "") else [value]


def _fields(values):
    return {key: value for key, value in values.items()
            if value not in (None, "")}


def _status(value):
    return str(value or "UNKNOWN").strip().upper()


def _service(name, value):
    value = _dict(value)
    offline = int(value.get("offlineCount") or 0)
    status = _status(value.get("status"))
    if status == "UNKNOWN":
        status = "ERROR" if offline else "OK"
    return {"name": name, "status": status,
            "total": int(value.get("count") or 0),
            "offline": offline}


def normalize(snapshot, config, observed_at):
    root = _dict(snapshot.get("health"))
    application = _dict(root.get("applicationServer"))
    info = _dict(application.get("systemInfo"))
    metrics = _dict(application.get("systemMetrics"))
    database = _dict(root.get("database"))
    printers = _dict(root.get("printers"))
    devices_summary = _dict(root.get("devices"))
    license_data = _dict(root.get("license"))
    hostname = urlsplit(config.base_url).hostname or "papercut"
    identifier = "papercut:" + hashlib.sha256(
        config.base_url.casefold().encode()).hexdigest()[:24]
    devices_payload = _dict(snapshot.get("devices"))
    device_values = _list(devices_payload.get("devices"))
    if not device_values:
        device_values = _list(devices_summary.get("inError"))
    services = [
        _service("Mobility Print", root.get("mobilityPrintServers")),
        _service("Print Providers", root.get("printProviders")),
        _service("Site Servers", root.get("siteServers")),
        _service("Web Print", root.get("webPrint")),
        _service("Job Ticketing", root.get("job-ticketing")),
    ]
    conditions = []
    if _status(database.get("status")) != "OK":
        conditions.append({"code": "database_not_ok", "severity": "Critical",
                           "value": _status(database.get("status"))})
    print_provider = next(
        value for value in services if value["name"] == "Print Providers")
    if print_provider["offline"] > 0 or print_provider["status"] != "OK":
        conditions.append({"code": "print_provider_offline",
                           "severity": "Critical"})
    if int(devices_summary.get("inErrorCount") or 0) > 0:
        conditions.append({"code": "embedded_device_errors",
                           "severity": "Medium",
                           "value": int(devices_summary["inErrorCount"])})
    disk = metrics.get("diskSpaceUsedPercentage")
    if disk is not None and float(disk) >= config.disk_warning_percent:
        conditions.append({"code": "disk_utilisation", "severity": "Medium",
                           "value": float(disk),
                           "threshold": config.disk_warning_percent})
    memory = metrics.get("jvmMemoryUsedPercentage")
    if memory is not None and float(memory) >= config.jvm_warning_percent:
        conditions.append({"code": "jvm_memory", "severity": "Medium",
                           "value": float(memory),
                           "threshold": config.jvm_warning_percent})
    held = int(printers.get("heldJobsCountTotal") or 0)
    if held >= config.held_jobs_warning:
        conditions.append({"code": "held_jobs", "severity": "Low",
                           "value": held,
                           "threshold": config.held_jobs_warning})
    assurance = license_data.get("upgradeAssuranceRemainingDays")
    if assurance is not None and int(assurance) <= \
            config.upgrade_assurance_warning_days:
        conditions.append({"code": "upgrade_assurance", "severity": "Low",
                           "value": int(assurance),
                           "threshold": config.upgrade_assurance_warning_days})
    uptime = metrics.get("uptimeHours")
    if uptime is not None and float(uptime) / 24 >= config.uptime_advisory_days:
        conditions.append({"code": "long_uptime", "severity": "Info",
                           "value": round(float(uptime) / 24, 1),
                           "threshold": config.uptime_advisory_days})
    record = {
        "id": identifier, "source": "papercut", "collector": "papercut",
        "source_asset_id": hostname, "source_record_id": identifier,
        "deployment_id": config.deployment_id,
        "customer_id": config.customer, "customer": config.customer,
        "site_id": config.site, "site": config.site_name or config.site,
        "hostname": hostname, "display_name": "PaperCut MF",
        "management_ip": hostname, "vendor": "PaperCut",
        "platform": "PaperCut MF", "device_type": "server",
        "device_role": "print-management-server",
        "firmware_version": info.get("version"), "online": True,
        "source_last_seen_at": observed_at,
        "extensions": {"papercut": {
            "application": {"system_info": info, "metrics": metrics},
            "database": database, "printers": printers,
            "devices": devices_summary, "services": services,
            "license": license_data, "conditions": conditions,
            "partial": bool(snapshot.get("partial"))}},
        "_authoritative_fields": [
            "customer_id", "site_id", "hostname", "vendor", "platform",
            "device_type", "device_role", "firmware_version", "online"],
    }
    records = [record]
    for device in sorted(
            (_dict(value) for value in device_values),
            key=lambda value: str(value.get("name") or "")):
        name = str(device.get("name") or "").strip()
        if not name:
            continue
        status = _status(device.get("status") or device.get("state"))
        device_id = "papercut-device:" + hashlib.sha256(
            f"{identifier}|{name}".casefold().encode()).hexdigest()[:24]
        records.append({
            "id": device_id, "source": "papercut",
            "collector": "papercut", "source_asset_id": name,
            "source_record_id": device_id,
            "deployment_id": config.deployment_id,
            "customer_id": config.customer, "customer": config.customer,
            "site_id": config.site, "site": config.site_name or config.site,
            "hostname": name, "display_name": name,
            "vendor": str(device.get("type") or "PaperCut managed"),
            "platform": "PaperCut Embedded", "device_type": "printer",
            "device_role": "embedded-print-device",
            "online": True if status == "OK" else False if status == "ERROR"
                      else None,
            "operational_status": status.casefold(),
            "source_last_seen_at": observed_at,
            "extensions": {"papercut": {
                "status": status,
                "status_description": device.get("statusDescription"),
                "last_job_age_seconds": device.get("lastJobSeconds")}},
            "_authoritative_fields": [
                "customer_id", "site_id", "hostname", "vendor", "platform",
                "device_type", "device_role", "online"],
        })
    tags = {"collector": "papercut", "deployment_id": config.deployment_id,
            "customer_id": config.customer, "customer": config.customer,
            "customer_name": config.customer_name,
            "site_id": config.site, "site": config.site,
            "site_name": config.site_name, "device_id": identifier,
            "hostname": hostname, "vendor": "PaperCut",
            "platform": "PaperCut MF"}
    points = [
        {"measurement": "device", "tags": tags,
         "fields": _fields({"online": True, "version": info.get("version"),
                            "operating_system": info.get("operatingSystem"),
                            "cpu_count": info.get("processors"),
                            "uptime_seconds": (
                                int(float(metrics["uptimeHours"]) * 3600)
                                if metrics.get("uptimeHours") is not None
                                else None)})},
        {"measurement": "availability", "tags": tags,
         "fields": {
             "available": not any(
                 value["severity"] == "Critical" for value in conditions),
             "status": (
                 "Critical" if any(value["severity"] == "Critical"
                                   for value in conditions)
                 else "Warning" if any(value["severity"] in {"Medium", "Low"}
                                       for value in conditions)
                 else "Healthy")}},
        {"measurement": "performance",
         "tags": {**tags, "component": "application"},
         "fields": _fields({
             "cpu_percent": metrics.get("systemCpuLoadPercentage"),
             "process_cpu_percent": metrics.get(
                 "processCpuLoadPercentage"),
             "jvm_memory_max_mb": metrics.get("jvmMemoryMaxMB"),
             "jvm_memory_used_mb": metrics.get("jvmMemoryUsedMB"),
             "jvm_memory_used_percent": metrics.get(
                 "jvmMemoryUsedPercentage"),
             "disk_free_mb": metrics.get("diskSpaceFreeMB"),
             "disk_total_mb": metrics.get("diskSpaceTotalMB"),
             "disk_used_percent": metrics.get(
                 "diskSpaceUsedPercentage")})},
        {"measurement": "performance",
         "tags": {**tags, "component": "database"},
         "fields": _fields({
             "status": database.get("status"),
             "total_connections": database.get("totalConnections"),
             "active_connections": database.get("activeConnections"),
             "maximum_connections": database.get("maxConnections"),
             "query_latency_ms": database.get("timeToQueryMilliseconds"),
             "connection_latency_ms": database.get(
                 "timeToConnectMilliseconds")})},
        {"measurement": "performance",
         "tags": {**tags, "component": "printing"},
         "fields": _fields({
             "printer_count": printers.get("count"),
             "held_jobs": printers.get("heldJobsCountTotal"),
             "printer_errors": printers.get("inErrorCount"),
             "device_count": devices_summary.get("count"),
             "device_errors": devices_summary.get("inErrorCount")})},
    ]
    for service in services:
        points.append({
            "measurement": "availability",
            "tags": {**tags, "service": service["name"]},
            "fields": {"available": service["status"] == "OK",
                       "status": service["status"],
                       "total": service["total"],
                       "offline": service["offline"]}})
    packs = license_data.get(
        "installedLicensePacks", license_data.get("licensePacks", []))
    if isinstance(packs, dict):
        packs = [{"name": key, "value": value}
                 for key, value in sorted(packs.items())]
    points.append({
        "measurement": "license",
        "tags": {**tags, "license_type": "papercut"},
        "fields": _fields({
            "valid": license_data.get("valid"),
            "remaining_days": license_data.get("licenseRemainingDays"),
            "upgrade_assurance_remaining_days": assurance,
            "users_used": _dict(license_data.get("users")).get("used"),
            "users_licensed": _dict(license_data.get("users")).get(
                "licensed"),
            "user_utilisation_percent": (
                round(float(_dict(license_data.get("users")).get("used", 0))
                      / float(_dict(license_data.get("users")).get(
                          "licensed", 0)) * 100, 2)
                if _dict(license_data.get("users")).get("licensed")
                else None),
            "installed_packs": ", ".join(
                str(_dict(value).get("name") or value) for value in _list(packs))
        })})
    return records, points, conditions
