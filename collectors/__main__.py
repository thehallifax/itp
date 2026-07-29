"""Collector framework command-line interface."""
import argparse
import asyncio
import json
import logging
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from . import CollectorRegistry
from .connector_registry import ConnectorMetadataRegistry
from .capabilities import CapabilityManifestEngine, MANIFESTS
from .config import load_config
from .inventory import InventoryManager
from .scheduler import Scheduler
from .writer import InfluxWriter
from analysis.operations import OperationsEngine, Rule
from analysis.infrastructure import InfrastructureStateEngine, SignalAdapter
from analysis.sites import SiteRegistry
from analysis.wallboard import WallboardEngine
from analysis.dashboards import DashboardRegistry, FOLDERS
from analysis.services import ServiceHealthEngine, ServiceEvaluator
from analysis.state_history import (
    FileStateStore,
    PipelineStateCapture,
    StateHistoryEngine,
)
from analysis.doctor import (
    DoctorEngine, DoctorFatalError, DoctorUsageError, render_human, render_json)

ROOT = Path(__file__).resolve().parents[1]


def _default_health_path():
    return str(Path(tempfile.gettempdir()) / "itp-collector-health")


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
    for name in ("mist", "fortigate", "paloalto", "papercut", "aruba"):
        collector_settings = settings.get(name, {})
        if not collector_settings.get("enabled", False): continue
        eligible, execution = CollectorRegistry.execution_eligible(
            name, collector_settings, runtime_mode)
        if not eligible:
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
    if name == "aruba":
        lines.extend((
            f"API base URL: {result['api_base_url']}",
            f"Authentication: {result['authentication_result']}",
            f"Account discovery: {result['account_discovery']}",
            f"Groups: {result['group_count']}",
            f"Access points: {result['access_point_count']}",
            f"Switches: {result['switch_count']}",
            f"Gateways: {result['gateway_count']}",
            f"Readiness: {result['diagnostics']['category']}",
            "Capabilities: " + ", ".join(
                f"{key}={value}"
                for key, value in sorted(result["capability_states"].items())
            ),
        ))
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
    health_path = Path(os.getenv(
        "COLLECTOR_HEALTH_PATH", _default_health_path()))
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
    check("collector registration",
          {"mist", "fortigate", "paloalto", "papercut", "aruba", "snmp"}
          <= registered,
          ", ".join(sorted(registered)))
    declared = set(MANIFESTS) - {"framework"}
    check("collector capability manifests",
          {"paloalto", "papercut", "aruba", "snmp"} <= declared,
          ", ".join(sorted(declared)))
    for name, settings in config.get("collectors", {}).items():
        if not settings.get("enabled") or name not in registered: continue
        if name == "mist":
            ok = bool(settings.get("organization_id") and settings.get("api_token"))
            check("Mist secrets", ok, "configured" if ok else "MIST_ORG_ID and MIST_API_TOKEN are required")
        if name == "fortigate":
            ok = bool(settings.get("host") and settings.get("api_token"))
            check("FortiGate secrets", ok, "configured" if ok else "FORTIGATE_HOST and FORTIGATE_API_TOKEN are required")
        if name == "paloalto":
            from .paloalto.collector import validate_settings
            try:
                validate_settings(config)
                ok = True; detail = "configured"
            except ValueError as exc:
                ok = False; detail = str(exc)
            check("Palo Alto configuration", ok, detail)
        if name == "aruba":
            from .aruba.collector import validate_settings
            try:
                validate_settings(config)
                ok = True; detail = "configured"
            except ValueError as exc:
                ok = False; detail = str(exc)
            check("Aruba Central configuration", ok, detail)
    provision = ROOT / "grafana/provisioning/dashboards/dashboards.yml"
    try:
        import yaml
        providers = yaml.safe_load(provision.read_text()).get("providers", [])
        expected_folders = set(FOLDERS)
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
    registry = SiteRegistry.load(os.getenv("SITES_CONFIG", "/app/config/sites.yml"))
    site_findings = registry.validation()
    blocking = [value for value in site_findings
                if value["type"] in {"duplicate_alias", "ambiguous_alias"}]
    check("canonical site registry", bool(registry.sites) and not blocking,
          f"sites={len(registry.sites)} aliases={registry.statistics()['aliases_loaded']} conflicts={len(blocking)}")
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
    if args.command == "doctor":
        report = DoctorEngine(
            ROOT, offline=args.offline, platform_only=args.platform_only,
            connectors_only=args.connectors_only, connector=args.connector).run()
        print(render_json(report, args.strict) if args.json
              else render_human(report, args.strict))
        raise SystemExit(report.exit_code(args.strict))
    if args.command == "connectors":
        registry = ConnectorMetadataRegistry.load(
            ROOT, validation_mode="runtime")
        if args.action == "list":
            if args.json:
                print(json.dumps(registry.to_dict(), indent=2, sort_keys=True))
            else:
                for connector in registry.all():
                    print(
                        f"{connector.id}\t{connector.display_name}\t"
                        f"domains={','.join(connector.domains)}\t"
                        f"status={connector.implementation_status}\t"
                        f"setup={'guided' if connector.guided_setup else connector.configuration_mode}\t"
                        f"validation={'yes' if connector.capabilities['validation'] else 'no'}\t"
                        f"docs={connector.documentation}")
            return
        try:
            connector = registry.get(args.connector)
        except KeyError as exc:
            raise DoctorUsageError(f"unknown connector: {args.connector}") from exc
        if args.json:
            print(json.dumps(connector.to_dict(), indent=2, sort_keys=True))
        else:
            print(f"Connector: {connector.display_name} ({connector.id})")
            print("Domains: " + ", ".join(connector.domains))
            print(f"Support: {connector.implementation_status}")
            print(f"Setup: {'guided' if connector.guided_setup else connector.configuration_mode}")
            print("Validation: " + (
                "available" if connector.capabilities["validation"]
                else "not available"))
            print("Doctor: " + (
                "available" if connector.capabilities["doctor"]
                else "not available"))
            print("Status: " + (
                "available" if connector.capabilities["status"]
                else "not available"))
            print(f"Documentation: {connector.documentation}")
            print(f"Notes: {connector.notes}")
        return
    if args.command == "state-history":
        store = FileStateStore(args.store)
        if args.action == "inspect-run":
            result = store.capture_result(args.run_id)
            if result is None:
                raise ValueError(f"state-history run not found: {args.run_id}")
            payload = result.to_dict()
            if args.json:
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                print(f"Run {result.run_id}: {result.status}; "
                      f"{payload['change_count']} change(s)")
            return
        try:
            payload = json.loads(Path(args.input).read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid state-history input {args.input}: {exc}") from exc
        engine = StateHistoryEngine(store)
        if args.action == "capture-run":
            try:
                run_payload = json.loads(Path(args.run_metadata).read_text())
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(
                    f"invalid pipeline run metadata {args.run_metadata}: {exc}") from exc
            result = engine.capture_payload(
                run_payload, payload, removal_policy=args.removal_policy)
            output = result.to_dict()
        else:
            output = engine.process_payload(
                payload, observed_at=args.observed_at)
        if args.json:
            print(json.dumps(output, indent=2, sort_keys=True))
        else:
            print(f"Persisted {len(output['snapshots'])} canonical snapshot(s); "
                  f"detected {output['change_count']} change(s)")
        return
    config = load_config(args.config)
    if args.command == "capabilities":
        runtime = Path(os.getenv("ITP_RUNTIME_DIR", ROOT / "runtime"))
        engine = CapabilityManifestEngine(config, runtime)
        result = engine.generate() if args.action == "generate" else engine.build()
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            for name, value in result["collectors"].items():
                counts = value["capability_counts"]
                print(
                    f"{name}\t{value['execution']['state']}\t"
                    f"collected={counts['collected']} failed={counts['failed']} "
                    f"unavailable={counts['unavailable']} "
                    f"not_applicable={counts['not_applicable']}")
        return
    if args.command == "validate":
        await _validate(config)
        return
    if args.command == "dashboards":
        registry = DashboardRegistry(ROOT, config,
            os.getenv("DASHBOARD_MANAGED_OUTPUT", str(ROOT / "runtime/dashboard/managed")),
            os.getenv("DASHBOARD_PROVISIONING",
                      str(ROOT / "grafana/provisioning/dashboards/dashboards.yml")),
            registry_validation_mode="runtime")
        result = registry.generate() if args.action == "generate" else registry.resolve()
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print("Enabled collectors: " + (", ".join(result["enabled_collectors"]) or "none"))
            print("Capabilities: " + ", ".join(result["capabilities"]))
            for value in result["dashboards"]:
                print(f"{value['folder']}\t{value['uid']}\t{value['collector']}")
        return
    if args.command == "paloalto":
        from .paloalto.collector import validate_settings
        settings = config.get("collectors", {}).get("paloalto", {})
        if not settings.get("enabled", False):
            raise ValueError("collector paloalto is not enabled")
        if args.action == "validate":
            validated = validate_settings(config)
            print(f"Palo Alto configuration valid: endpoint={urlsplit(validated.base_url).hostname} "
                  f"site={validated.site} tls_verify={'yes' if validated.verify_tls else 'no'}")
            return
        collector = CollectorRegistry.create("paloalto", config,
            os.getenv("INVENTORY_PATH", "/app/runtime/inventory/devices.json"))
        try:
            if args.action == "discover":
                result = await collector.inspect()
                print(json.dumps(result, indent=2, sort_keys=True))
            else:
                await collector.discover()
                result = await collector.collect()
                print(json.dumps(result, indent=2, sort_keys=True))
        finally:
            await collector.close()
        return
    if args.command == "operations":
        settings = config.get("operations", {})
        engine = OperationsEngine(
            inventory_dir=settings.get("inventory_path", "/app/runtime/inventory"),
            output_dir=settings.get("output_path", "/app/runtime/operations"),
            dashboard_template=settings.get("dashboard_template", "/app/dashboards/Infrastructure Overview/infrastructure-overview.json"),
            settings={**settings, "virtualisation": config.get("virtualisation", {})},
            virtualisation_dir=settings.get(
                "virtualisation_path", "/app/runtime/virtualisation"),
            capability_registry=settings.get(
                "capability_registry", "/app/runtime/dashboard/managed/registry.json"))
        if args.action == "rules":
            for rule in Rule.registered(): print(f"{rule.id}\t{rule.category}")
        else:
            result = engine.run()
            print(f"Generated {len(result['issues'])} issues, {len(result['risks'])} risks, "
                  f"and {len(result['recommendations'])} recommendations")
        return
    if args.command == "services":
        settings = config.get("services", {})
        engine = ServiceHealthEngine(
            infrastructure_state=settings.get(
                "infrastructure_state", "/app/runtime/infrastructure/state.json"),
            operations_state=settings.get(
                "operations_state", "/app/runtime/operations/operations.json"),
            capability_registry=settings.get(
                "capability_registry", "/app/runtime/dashboard/managed/registry.json"),
            capability_manifest=settings.get(
                "capability_manifest", "/app/runtime/capabilities/collectors.json"),
            output_dir=settings.get("output_path", "/app/runtime/services"),
            sites_config=settings.get("sites_config", "/app/config/sites.yml"))
        if args.action == "evaluators":
            for evaluator in ServiceEvaluator.registered():
                print(f"{evaluator.definition.name}\t{evaluator.definition.capability}")
        else:
            result = engine.run()
            print(f"Generated health for {len(result['sites'])} canonical sites "
                  f"and {len(result['estate']['services'])} estate services")
        return
    if args.command == "sites":
        registry = SiteRegistry.load(os.getenv("SITES_CONFIG", "/app/config/sites.yml"))
        state = json.loads(Path(os.getenv("INFRASTRUCTURE_STATE", "/app/runtime/infrastructure/state.json")).read_text())
        operations_path = Path(os.getenv("OPERATIONS_STATE", "/app/runtime/operations/operations.json"))
        operations = json.loads(operations_path.read_text()) if operations_path.exists() else {}
        payload, _ = registry.write(os.getenv("SITES_OUTPUT", "/app/runtime/sites"),
            os.getenv("DASHBOARD_OUTPUT", "/app/runtime/dashboard"), state, operations,
            state.get("site_validation", []), state.get("site_registry_statistics", {}))
        print(f"Generated canonical estate for {len(payload['sites'])} sites")
        return
    if args.command == "wallboard":
        settings = config.get("wallboard", {})
        result = WallboardEngine(
            infrastructure_state=settings.get("infrastructure_state", "/app/runtime/infrastructure/state.json"),
            operations_state=settings.get("operations_state", "/app/runtime/operations/operations.json"),
            sites_state=settings.get("sites_state", "/app/runtime/sites/sites.json"),
            dashboard_template=settings.get("dashboard_template", "/app/dashboards/Operations/operations-wallboard.json"),
            summary_output=settings.get("summary_output", "/app/runtime/dashboard/wallboard-summary.json"),
            dashboard_output=settings.get("dashboard_output", "/app/runtime/dashboard/operations/operations-wallboard.json"),
            capability_registry=settings.get(
                "capability_registry", "/app/runtime/dashboard/managed/registry.json"),
            service_health=settings.get(
                "service_health", "/app/runtime/services/service-health.json"),
            freshness_seconds=settings.get("freshness_seconds", 900)).run()
        print(f"Generated Operations Wallboard for {len(result['site_options'])} canonical sites")
        return
    if args.command == "infrastructure":
        settings = config.get("infrastructure", {})
        engine = InfrastructureStateEngine(
            inventory_dir=settings.get("inventory_path", "/app/runtime/inventory"),
            operations_dir=settings.get("operations_path", "/app/runtime/operations"),
            output_dir=settings.get("output_path", "/app/runtime/infrastructure"),
            dashboard_dir=settings.get("dashboard_path", "/app/runtime/dashboard"),
            status_freshness_seconds=settings.get("status_freshness_seconds", 300),
            sites_config=settings.get("sites_config", "/app/config/sites.yml"),
            sites_output=settings.get("sites_output", "/app/runtime/sites"),
            readiness_config=config,
            registry_validation_mode="runtime")
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
                if args.json:
                    print(json.dumps(result, indent=2, sort_keys=True))
                else:
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
    wallboard_settings = config.get("wallboard", {})
    services_settings = config.get("services", {})
    state_history = PipelineStateCapture(config.get("state_history", {}))
    capability_engine = CapabilityManifestEngine(
        config, Path(os.getenv("ITP_RUNTIME_DIR", "/app/runtime")))
    capability_engine.generate()
    dashboard_registry = DashboardRegistry(ROOT, config,
        os.getenv("DASHBOARD_MANAGED_OUTPUT", str(ROOT / "runtime/dashboard/managed")),
        os.getenv("DASHBOARD_PROVISIONING",
                  str(ROOT / "grafana/provisioning/dashboards/dashboards.yml")),
        registry_validation_mode="runtime")
    dashboard_registry.generate()
    infrastructure = InfrastructureStateEngine(
        inventory_dir=infrastructure_settings.get("inventory_path", "/app/runtime/inventory"),
        operations_dir=infrastructure_settings.get("operations_path", "/app/runtime/operations"),
        output_dir=infrastructure_settings.get("output_path", "/app/runtime/infrastructure"),
        dashboard_dir=infrastructure_settings.get("dashboard_path", "/app/runtime/dashboard"),
        status_freshness_seconds=infrastructure_settings.get("status_freshness_seconds", 300),
        sites_config=infrastructure_settings.get("sites_config", "/app/config/sites.yml"),
        sites_output=infrastructure_settings.get("sites_output", "/app/runtime/sites"),
        readiness_config=config,
        registry_validation_mode="runtime") \
        if infrastructure_settings.get("enabled", True) else None
    operations = OperationsEngine(
        inventory_dir=operations_settings.get("inventory_path", "/app/runtime/inventory"),
        output_dir=operations_settings.get("output_path", "/app/runtime/operations"),
        dashboard_template=operations_settings.get("dashboard_template", "/app/dashboards/Infrastructure Overview/infrastructure-overview.json"),
            dashboard_output=operations_settings.get("dashboard_output", "/app/runtime/dashboard/grafana/infrastructure-overview.json"),
            infrastructure_state=infrastructure_settings.get("output_path", "/app/runtime/infrastructure") + "/state.json",
            infrastructure_summary=infrastructure_settings.get("dashboard_path", "/app/runtime/dashboard") + "/infrastructure-summary.json",
            capability_registry=operations_settings.get(
                "capability_registry", "/app/runtime/dashboard/managed/registry.json"),
            settings={**operations_settings,
                      "virtualisation": config.get("virtualisation", {})},
            virtualisation_dir=operations_settings.get(
                "virtualisation_path", "/app/runtime/virtualisation")) \
        if operations_settings.get("enabled", True) else None
    wallboard = WallboardEngine(
        infrastructure_state=wallboard_settings.get("infrastructure_state", "/app/runtime/infrastructure/state.json"),
        operations_state=wallboard_settings.get("operations_state", "/app/runtime/operations/operations.json"),
        sites_state=wallboard_settings.get("sites_state", "/app/runtime/sites/sites.json"),
        dashboard_template=wallboard_settings.get("dashboard_template", "/app/dashboards/Operations/operations-wallboard.json"),
        summary_output=wallboard_settings.get("summary_output", "/app/runtime/dashboard/wallboard-summary.json"),
        dashboard_output=wallboard_settings.get("dashboard_output", "/app/runtime/dashboard/operations/operations-wallboard.json"),
        capability_registry=wallboard_settings.get(
            "capability_registry", "/app/runtime/dashboard/managed/registry.json"),
        service_health=wallboard_settings.get(
            "service_health", "/app/runtime/services/service-health.json"),
        freshness_seconds=wallboard_settings.get("freshness_seconds", 900)) \
        if wallboard_settings.get("enabled", True) else None
    service_health = ServiceHealthEngine(
        infrastructure_state=services_settings.get(
            "infrastructure_state", "/app/runtime/infrastructure/state.json"),
        operations_state=services_settings.get(
            "operations_state", "/app/runtime/operations/operations.json"),
        capability_registry=services_settings.get(
            "capability_registry", "/app/runtime/dashboard/managed/registry.json"),
        output_dir=services_settings.get("output_path", "/app/runtime/services"),
        sites_config=services_settings.get("sites_config", "/app/config/sites.yml")) \
        if services_settings.get("enabled", True) else None
    await Scheduler(collectors, os.getenv(
        "COLLECTOR_HEALTH_PATH", _default_health_path()),
                    inventory_engine=engine,
                    lifecycle_interval=inventory_settings.get(
                        "lifecycle_evaluation_interval_seconds", 3600),
                    infrastructure_engine=infrastructure,
                    operations_engine=operations,
                    service_health_engine=service_health,
                    wallboard_engine=wallboard,
                    dashboard_registry=dashboard_registry,
                    capability_engine=capability_engine,
                    state_history_capture=state_history,
                    operations_interval=operations_settings.get("interval_seconds", 300),
                    state_path=Path(os.getenv(
                        "ITP_RUNTIME_DIR", "/app/runtime"))
                    / "scheduler/state.json").run()


def build_parser():
    """Build the collector CLI parser without executing any collector code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=os.getenv("ITP_PROFILE"))
    parser.add_argument("--config", default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    connectors = sub.add_parser("connectors")
    connectors.add_argument("action", choices=("list", "inspect"))
    connectors.add_argument("connector", nargs="?")
    connectors.add_argument("--json", action="store_true")
    for command in ("discover", "collect", "inspect"):
        item = sub.add_parser(command); item.add_argument("name")
        if command == "inspect":
            item.add_argument("--json", action="store_true")
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
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--json", action="store_true")
    doctor_scope = doctor.add_mutually_exclusive_group()
    doctor_scope.add_argument("--platform-only", action="store_true")
    doctor_scope.add_argument("--connectors-only", action="store_true")
    doctor.add_argument("--connector")
    doctor.add_argument("--offline", action="store_true")
    doctor.add_argument("--strict", action="store_true")
    operations = sub.add_parser("operations")
    operations.add_argument("action", choices=("generate", "rules"), default="generate", nargs="?")
    services = sub.add_parser("services")
    services.add_argument("action", choices=("generate", "evaluators"),
                          default="generate", nargs="?")
    infrastructure = sub.add_parser("infrastructure")
    infrastructure.add_argument("action", choices=("generate", "adapters", "fusion-report"),
                                default="generate", nargs="?")
    sites = sub.add_parser("sites")
    sites.add_argument("action", choices=("generate",), default="generate", nargs="?")
    wallboard = sub.add_parser("wallboard")
    wallboard.add_argument("action", choices=("generate",), default="generate", nargs="?")
    dashboards = sub.add_parser("dashboards")
    dashboards.add_argument("action", choices=("generate", "status"), default="generate", nargs="?")
    dashboards.add_argument("--json", action="store_true")
    capabilities = sub.add_parser("capabilities")
    capabilities.add_argument("action", choices=("generate", "inspect"),
                              default="inspect", nargs="?")
    capabilities.add_argument("--json", action="store_true")
    history = sub.add_parser("state-history")
    history.add_argument("action", choices=("process", "capture-run", "inspect-run"),
                         default="process", nargs="?")
    history.add_argument("--input")
    history.add_argument("--store", required=True)
    history.add_argument("--observed-at")
    history.add_argument("--run-metadata")
    history.add_argument("--run-id")
    history.add_argument("--removal-policy", choices=("complete_only", "disabled"),
                         default="complete_only")
    history.add_argument("--json", action="store_true")
    paloalto = sub.add_parser("paloalto")
    paloalto.add_argument("action", choices=("validate", "discover", "run"))
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if (args.command == "connectors" and args.action == "inspect"
            and not args.connector):
        parser.error("connectors inspect requires connector ID")
    if args.command == "state-history":
        if args.action in ("process", "capture-run") and not args.input:
            parser.error(f"state-history {args.action} requires --input")
        if args.action == "capture-run" and not args.run_metadata:
            parser.error("state-history capture-run requires --run-metadata")
        if args.action == "inspect-run" and not args.run_id:
            parser.error("state-history inspect-run requires --run-id")
    if args.command in ("state-history", "doctor", "connectors"):
        # Canonical fixture/history processing is deliberately independent of
        # deployment configuration and any inherited ITP_PROFILE value.
        logging_context = ""
    elif args.profile and not (args.config or os.getenv("COLLECTOR_CONFIG")):
        from itp_profiles import DeploymentProfile
        profile = DeploymentProfile.load(args.profile, ROOT).activate()
        args.config = args.config or str(profile.paths.discovery)
        logging_context = f" deployment_id={profile.deployment_id}"
    else:
        args.config = args.config or os.getenv("COLLECTOR_CONFIG", "/app/config.yml")
        deployment_id = os.getenv("ITP_DEPLOYMENT_ID", "")
        logging_context = (
            f" deployment_id={deployment_id}" if deployment_id else "")
    if args.command == "inventory" and args.action in ("show", "retire", "restore") and not args.asset_id:
        parser.error(f"inventory {args.action} requires asset_id")
    if args.command == "inventory" and args.action in ("retire", "restore") and not args.reason:
        parser.error(f"inventory {args.action} requires --reason")
    logging.basicConfig(level=logging.INFO,
        format=f"%(asctime)s %(levelname)s{logging_context} %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try: asyncio.run(_run(args))
    except DoctorUsageError as exc:
        logging.error("collector=framework phase=%s result=failed error=%s",
                      args.command, exc)
        raise SystemExit(2)
    except DoctorFatalError as exc:
        logging.error("collector=framework phase=%s result=failed error=%s",
                      args.command, exc)
        raise SystemExit(3)
    except Exception as exc:
        logging.error("collector=framework phase=%s result=failed error=%s", args.command, exc)
        raise SystemExit(1)


if __name__ == "__main__": main()
