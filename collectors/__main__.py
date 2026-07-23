"""Collector framework command-line interface."""
import argparse
import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import CollectorRegistry
from .config import load_config
from .inventory import InventoryManager
from .scheduler import Scheduler
from .writer import InfluxWriter
from analysis.operations import OperationsEngine, Rule
from analysis.infrastructure import InfrastructureStateEngine, SignalAdapter

ROOT = Path(__file__).resolve().parents[1]


def _since_timestamp(value):
    if not value: return None
    match = re.fullmatch(r"(\d+)([mhdw])", value)
    if match:
        seconds = int(match.group(1)) * {"m": 60, "h": 3600, "d": 86400, "w": 604800}[match.group(2)]
        return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except ValueError as exc:
        raise ValueError("--since must be ISO-8601 or a duration such as 7d") from exc


def _enabled_collectors(config):
    result = []
    settings = config.get("collectors", {})
    runtime_mode = os.getenv("ITP_RUNTIME_MODE", "central").strip().lower()
    if runtime_mode not in ("central", "edge"):
        raise ValueError(f"unsupported ITP_RUNTIME_MODE: {runtime_mode}")
    inventory_path = os.getenv("INVENTORY_PATH", "/app/runtime/inventory/devices.json")
    for name in ("mist", "fortigate"):
        collector_settings = settings.get(name, {})
        if not collector_settings.get("enabled", False): continue
        default_execution = CollectorRegistry.metadata(name)["execution"]
        execution = collector_settings.get("execution", default_execution)
        if execution not in ("either", runtime_mode):
            logging.info("collector=%s result=skipped execution=%s runtime_mode=%s",
                         name, execution, runtime_mode)
            continue
        result.append(CollectorRegistry.create(name, config, inventory_path))
    return result


def inspection_lines(name, result):
    lines = [f"Collector: {name}", f"Enabled: {'yes' if result['enabled'] else 'no'}",
        f"API hostname: {result['api_hostname']}", f"Organisation ID: {result['organization_id']}",
        f"Sites: {result['site_count']}", f"Devices: {result['device_count']}",
        "Device types: " + ", ".join(f"{key}={value}" for key, value in result["device_types"].items())]
    for measurement, shape in result["measurements"].items():
        lines.extend((f"Measurement: {measurement}", "  tags: " + ", ".join(shape["tags"]),
            "  fields: " + ", ".join(f"{key} ({shape['field_counts'][key]}/{shape['points']})" for key in shape["fields"])))
    lines.extend((f"Points produced: {result['points_produced']}",
        "Always empty supported fields: " + (", ".join(result["always_empty_fields"]) or "none"),
        "High-cardinality warnings: " + (", ".join(result["high_cardinality_warnings"]) or "none"),
        f"Influx write completed: {'yes' if result['influx_write_completed'] else 'no'} ({result['points_written']} points)"))
    return lines


async def _run_idle():
    """Keep the framework healthy when all native collectors are disabled."""
    health_path = Path(os.getenv("COLLECTOR_HEALTH_PATH", "/tmp/collector-health"))
    health_path.parent.mkdir(parents=True, exist_ok=True)
    health_path.touch()
    logging.info("collector=framework phase=run result=idle enabled_collectors=0")
    while True:
        await asyncio.sleep(3600)


async def _validate(config):
    checks = []; errors = []
    def check(name, ok, detail):
        checks.append((name, ok, detail))
        if not ok: errors.append(f"{name}: {detail}")
    mode = os.getenv("ITP_RUNTIME_MODE", "central").lower()
    check("runtime mode", mode in ("central", "edge"), mode)
    check("schema version", config.get("schema_version") == 1,
          f"configured={config.get('schema_version')!r} supported=1")
    registered = set(CollectorRegistry.names())
    check("collector registration", {"mist", "fortigate", "snmp"} <= registered,
          ", ".join(sorted(registered)))
    for name, settings in config.get("collectors", {}).items():
        if not settings.get("enabled") or name not in registered: continue
        if name == "mist":
            ok = bool(settings.get("organization_id") and settings.get("api_token"))
            check("Mist secrets", ok, "configured" if ok else "MIST_ORG_ID and MIST_API_TOKEN are required")
        if name == "fortigate":
            ok = bool(settings.get("host") and settings.get("api_token"))
            check("FortiGate secrets", ok, "configured" if ok else "FORTIGATE_HOST and FORTIGATE_API_TOKEN are required")
    provision = ROOT / "grafana/provisioning/dashboards/dashboards.yml"
    try:
        import yaml
        providers = yaml.safe_load(provision.read_text()).get("providers", [])
        expected_folders = {"Infrastructure Overview", "Network", "Compute", "Printing",
                            "Services", "Inventory", "Collectors", "Operations", "Vendor"}
        folder_uids = [item.get("folderUid") for item in providers]
        valid_provisioning = (
            {item.get("folder") for item in providers} == expected_folders
            and len(folder_uids) == len(set(folder_uids)) == len(expected_folders)
            and all(folder_uids)
            and all(item.get("type") == "file" for item in providers)
        )
        provisioning_detail = ", ".join(item.get("folder", "") for item in providers)
    except Exception as exc:
        valid_provisioning = False; provisioning_detail = str(exc)
    check("Grafana provisioning", valid_provisioning, provisioning_detail)
    dashboards = list((ROOT / "dashboards").rglob("*.json"))
    try:
        uids = [json.loads(path.read_text()).get("uid") for path in dashboards]
        valid_dashboards = bool(uids) and all(uids) and len(uids) == len(set(uids))
    except Exception:
        valid_dashboards = False; uids = []
    check("dashboard UIDs", valid_dashboards, ", ".join(str(value) for value in uids))
    inventory_path = Path(os.getenv("INVENTORY_PATH", "/app/runtime/inventory/devices.json"))
    check("inventory location", inventory_path.parent.exists() and os.access(inventory_path.parent, os.W_OK),
          str(inventory_path.parent))
    host = os.getenv("INFLUXDB_HOST", ""); token = os.getenv("INFLUXDB_TOKEN", "")
    try:
        import httpx
        url = InfluxWriter._normalize_url(host)
        response = await asyncio.to_thread(httpx.get, url.rstrip("/") + "/health",
            headers={"Authorization": f"Bearer {token}"}, timeout=5)
        influx_ok = response.status_code < 400
        detail = f"HTTP {response.status_code}"
    except Exception as exc:
        influx_ok = False; detail = type(exc).__name__
    check("InfluxDB connectivity", influx_ok, detail)
    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    if errors: raise ValueError(f"validation failed ({len(errors)} checks)")
    print(f"ITP validation successful: {len(checks)} checks passed")


async def _run(args):
    if args.command == "list":
        for name in CollectorRegistry.names(): print(name)
        return
    config = load_config(args.config)
    if args.command == "validate":
        await _validate(config)
        return
    if args.command == "operations":
        settings = config.get("operations", {})
        engine = OperationsEngine(
            inventory_dir=settings.get("inventory_path", "/app/runtime/inventory"),
            output_dir=settings.get("output_path", "/app/runtime/operations"),
            dashboard_template=settings.get("dashboard_template", "/app/dashboards/Infrastructure Overview/infrastructure-overview.json"),
            settings=settings)
        if args.action == "rules":
            for rule in Rule.registered(): print(f"{rule.id}\t{rule.category}")
        else:
            result = engine.run()
            print(f"Generated {len(result['issues'])} issues, {len(result['risks'])} risks, "
                  f"and {len(result['recommendations'])} recommendations")
        return
    if args.command == "infrastructure":
        settings = config.get("infrastructure", {})
        engine = InfrastructureStateEngine(
            inventory_dir=settings.get("inventory_path", "/app/runtime/inventory"),
            operations_dir=settings.get("operations_path", "/app/runtime/operations"),
            output_dir=settings.get("output_path", "/app/runtime/infrastructure"),
            dashboard_dir=settings.get("dashboard_path", "/app/runtime/dashboard"),
            status_freshness_seconds=settings.get("status_freshness_seconds", 300))
        if args.action == "adapters":
            for adapter in SignalAdapter.registered(engine.inventory_dir):
                print(f"{adapter.name}\t{adapter.priority}")
        else:
            result = engine.run()
            if args.action == "fusion-report":
                stats = result["fusion_statistics"]; summary = result["summary"]
                for label, value in (("Source records", stats["source_records"]),
                    ("Canonical assets", stats["canonical_assets"]),
                    ("Records fused", stats["records_fused"]), ("Conflicts", stats["conflicts"]),
                    ("Low-confidence candidates", stats["low_confidence_candidates"]),
                    ("Suppressed findings", summary["suppressed_findings"]),
                    ("Actionable warnings", summary["actionable_warnings"])):
                    print(f"{label}: {value}")
            else:
                print(f"Generated infrastructure state for {result['summary']['devices']} devices "
                      f"across {result['summary']['sites']} sites")
        return
    if args.command == "inventory":
        inventory_path = os.getenv("INVENTORY_PATH", "/app/runtime/inventory/devices.json")
        engine = InventoryManager(inventory_path, config.get("inventory")).engine
        if args.action == "list":
            result = engine.list_assets()
        elif args.action == "show":
            result = engine.get_asset(args.asset_id)
            if result is None: raise ValueError(f"unknown asset_id: {args.asset_id}")
        elif args.action == "summary":
            result = engine.summary()
        elif args.action == "reconcile":
            result = engine.reconcile()
        elif args.action == "lifecycle":
            result = engine.update_lifecycle()
        elif args.action == "retire":
            result = engine.retire(args.asset_id, args.reason)
        elif args.action == "restore":
            result = engine.restore(args.asset_id, args.reason)
        elif args.action == "history":
            result = engine.history(args.asset_id, state=args.state, source=args.source, limit=args.limit)
        elif args.action == "changes":
            result = engine.changes(args.asset_id, source=args.source, field=args.field,
                                    severity=args.severity, since=_since_timestamp(args.since),
                                    limit=args.limit)
        elif args.action == "changes-summary":
            result = engine.changes_summary(since=_since_timestamp(args.since))
        else:
            result = engine.load_source_runs()
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        elif args.action == "summary":
            print(f"Total assets: {result['total_assets']}")
            for name in ("by_collector", "by_vendor", "by_device_type", "by_site",
                         "by_lifecycle_state", "by_reconciliation_status"):
                print(name.replace("_", " ").title() + ": " +
                      ", ".join(f"{key}={value}" for key, value in result[name].items()))
            print(f"Stale assets: {result['stale_assets']}")
            print(f"Missing assets: {result['missing_assets']}")
            print(f"Assets without successful source observation: "
                  f"{result['assets_without_successful_source_observation']}")
            print("Sources healthy: " + (", ".join(result["sources_healthy"]) or "none"))
            print("Sources failing: " + (", ".join(result["sources_failing"]) or "none"))
            print("Oldest successful source run: " + (result["oldest_successful_source_run"] or "none"))
        elif args.action == "list":
            for item in result:
                print("\t".join(str(item.get(key, "")) for key in
                                ("asset_id", "lifecycle_state", "collector", "hostname", "management_ip")))
        elif args.action == "reconcile":
            counts = {}
            for item in result["reconciliations"]:
                counts[item["status"]] = counts.get(item["status"], 0) + 1
            print(f"Reconciliation relationships: {len(result['reconciliations'])}")
            print("By status: " + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))
        elif args.action == "lifecycle":
            summary = result["lifecycle_summary"]
            print(" ".join(f"{key}={value}" for key, value in summary.items()))
            for item in result["assets"]:
                if item.get("lifecycle_state") in ("stale", "missing"):
                    print("\t".join(str(item.get(key, "")) for key in
                        ("asset_id", "lifecycle_state", "lifecycle_reason", "lifecycle_source_run_id")))
        elif args.action in ("retire", "restore"):
            print(f"Asset: {result['asset_id']}")
            print(f"Lifecycle state: {result['lifecycle_state']}")
            print(f"Changed at: {result.get('last_changed_at', '')}")
        elif args.action == "history":
            for item in result:
                print("\t".join(str(item.get(key, "")) for key in
                    ("occurred_at", "asset_id", "previous_state", "new_state", "reason", "source", "actor")))
        elif args.action == "changes":
            for item in result:
                print("\t".join(str(item.get(key, "")) for key in
                    ("detected_at", "severity", "asset_id", "field", "previous_value",
                     "new_value", "change_type", "source")))
        elif args.action == "changes-summary":
            print(f"Total changes: {result['total_changes']}")
            for name in ("by_severity", "by_field", "by_source"):
                print(name.replace("_", " ").title() + ": " +
                      ", ".join(f"{key}={value}" for key, value in result[name].items()))
            print(f"Identity conflicts: {result['identity_conflicts']}")
            print(f"Firmware changes: {result['firmware_changes']}")
            print(f"Site or location changes: {result['site_or_location_changes']}")
            print(f"Ownership or management changes: {result['ownership_or_management_changes']}")
        elif args.action == "sources":
            for name, state in sorted(result["sources"].items()):
                run = state.get("last_run", {})
                print("\t".join(str(value) for value in (name, run.get("success", ""),
                    run.get("completed_at", ""), run.get("records_returned", 0),
                    state.get("consecutive_successes", 0), state.get("consecutive_failures", 0))))
        else:
            print(json.dumps(result, indent=2, sort_keys=True))
        return
    collectors = _enabled_collectors(config)
    if args.command in ("discover", "collect", "inspect"):
        if args.name not in CollectorRegistry.names(): raise ValueError(f"unknown collector: {args.name}")
        collector = next((item for item in collectors if item.name == args.name), None)
        if collector is None: raise ValueError(f"collector {args.name} is not enabled")
        try:
            result = await getattr(collector, args.command)()
            if args.command == "inspect":
                print("\n".join(inspection_lines(args.name, result)))
        finally:
            close = getattr(collector, "close", None)
            if close: await close()
        return
    inventory_settings = config.get("inventory", {})
    inventory_enabled = inventory_settings.get("enabled", True)
    inventory_path = os.getenv("INVENTORY_PATH", "/app/runtime/inventory/devices.json")
    engine = InventoryManager(inventory_path, inventory_settings).engine if inventory_enabled else None
    if not collectors and not engine:
        await _run_idle()
        return
    operations_settings = config.get("operations", {})
    infrastructure_settings = config.get("infrastructure", {})
    infrastructure = InfrastructureStateEngine(
        inventory_dir=infrastructure_settings.get("inventory_path", "/app/runtime/inventory"),
        operations_dir=infrastructure_settings.get("operations_path", "/app/runtime/operations"),
        output_dir=infrastructure_settings.get("output_path", "/app/runtime/infrastructure"),
        dashboard_dir=infrastructure_settings.get("dashboard_path", "/app/runtime/dashboard"),
        status_freshness_seconds=infrastructure_settings.get("status_freshness_seconds", 300)) \
        if infrastructure_settings.get("enabled", True) else None
    operations = OperationsEngine(
        inventory_dir=operations_settings.get("inventory_path", "/app/runtime/inventory"),
        output_dir=operations_settings.get("output_path", "/app/runtime/operations"),
        dashboard_template=operations_settings.get("dashboard_template", "/app/dashboards/Infrastructure Overview/infrastructure-overview.json"),
        dashboard_output=operations_settings.get("dashboard_output", "/app/runtime/dashboard/grafana/infrastructure-overview.json"),
        infrastructure_state=infrastructure_settings.get("output_path", "/app/runtime/infrastructure") + "/state.json",
        infrastructure_summary=infrastructure_settings.get("dashboard_path", "/app/runtime/dashboard") + "/infrastructure-summary.json",
        settings=operations_settings) if operations_settings.get("enabled", True) else None
    await Scheduler(collectors, os.getenv("COLLECTOR_HEALTH_PATH", "/tmp/collector-health"),
                    inventory_engine=engine,
                    lifecycle_interval=inventory_settings.get(
                        "lifecycle_evaluation_interval_seconds", 3600),
                    infrastructure_engine=infrastructure,
                    operations_engine=operations,
                    operations_interval=operations_settings.get("interval_seconds", 300)).run()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=os.getenv("COLLECTOR_CONFIG", "/app/config.yml"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    for command in ("discover", "collect", "inspect"):
        item = sub.add_parser(command); item.add_argument("name")
    inventory = sub.add_parser("inventory")
    inventory.add_argument("action", choices=("list", "show", "summary", "reconcile", "lifecycle",
                                               "retire", "restore", "history", "sources", "changes",
                                               "changes-summary"))
    inventory.add_argument("asset_id", nargs="?")
    inventory.add_argument("--reason")
    inventory.add_argument("--state", choices=("discovered", "active", "offline", "stale", "missing", "retired"))
    inventory.add_argument("--source")
    inventory.add_argument("--field")
    inventory.add_argument("--severity", choices=("info", "low", "medium", "high"))
    inventory.add_argument("--since")
    inventory.add_argument("--limit", type=int, default=50)
    inventory.add_argument("--json", action="store_true")
    sub.add_parser("run")
    sub.add_parser("validate")
    operations = sub.add_parser("operations")
    operations.add_argument("action", choices=("generate", "rules"), default="generate", nargs="?")
    infrastructure = sub.add_parser("infrastructure")
    infrastructure.add_argument("action", choices=("generate", "adapters", "fusion-report"),
                                default="generate", nargs="?")
    args = parser.parse_args()
    if args.command == "inventory" and args.action in ("show", "retire", "restore") and not args.asset_id:
        parser.error(f"inventory {args.action} requires asset_id")
    if args.command == "inventory" and args.action in ("retire", "restore") and not args.reason:
        parser.error(f"inventory {args.action} requires --reason")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try: asyncio.run(_run(args))
    except Exception as exc:
        logging.error("collector=framework phase=%s result=failed error=%s", args.command, exc)
        raise SystemExit(1)


if __name__ == "__main__": main()
