import csv
import json
from datetime import datetime, timezone

import yaml

from analysis.infrastructure import InfrastructureStateEngine
from analysis.operations import OperationsEngine
from analysis.sites import SiteRegistry, normalize_alias


NOW = datetime(2026, 7, 23, tzinfo=timezone.utc)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value))


def write_sites(path, sites):
    path.write_text(yaml.safe_dump({"sites": sites}, sort_keys=False))


def test_alias_normalization_and_resolution_are_exact_and_explainable(tmp_path):
    path = tmp_path / "sites.yml"
    write_sites(path, [{"id": "example-campus", "display_name": "Example College, Campus",
        "aliases": ["EC", "Example College", "EXAMPLE-CAMPUS"]}])
    registry = SiteRegistry.load(path)
    for value in (" ec ", "Example College", "example-campus"):
        resolved = registry.resolver.resolve(value)
        assert resolved.site_id == "site:example-campus" and resolved.status == "resolved"
        assert "configured" in resolved.explanation
    assert registry.resolver.resolve("Example Colleg").status == "unknown"
    assert normalize_alias("St John’s, Campus") == "st johns campus"


def test_duplicate_ambiguous_unused_and_unknown_validation(tmp_path):
    path = tmp_path / "sites.yml"
    write_sites(path, [
        {"id": "one", "display_name": "One", "aliases": ["Shared", "Main-Site", "Main Site"]},
        {"id": "two", "display_name": "Two", "aliases": ["Shared", "Unused"]},
    ])
    registry = SiteRegistry.load(path)
    findings = registry.validation({normalize_alias("Main Site")}, ["Unknown Campus"])
    kinds = {value["type"] for value in findings}
    assert {"duplicate_alias", "ambiguous_alias", "unused_alias", "unknown_site"} <= kinds
    assert registry.resolver.resolve("Shared").status == "ambiguous"


def test_registry_loading_order_and_site_ids_are_deterministic(tmp_path):
    left = tmp_path / "left.yml"; right = tmp_path / "right.yml"
    values = [{"id": "z-site", "display_name": "Z Site", "aliases": ["Z"]},
              {"id": "a-site", "display_name": "A Site", "aliases": ["A"]}]
    write_sites(left, values); write_sites(right, list(reversed(values)))
    assert [site.site_id for site in SiteRegistry.load(left).sites] == ["site:a-site", "site:z-site"]
    assert [site.site_id for site in SiteRegistry.load(left).sites] == [site.site_id for site in SiteRegistry.load(right).sites]


def _integrated(tmp_path):
    inventory = tmp_path / "inventory"; operations = tmp_path / "operations"
    config = tmp_path / "sites.yml"
    write_sites(config, [{"id": "example-campus", "display_name": "Example College, Campus",
        "aliases": ["EC", "Example College"]}])
    write_json(inventory / "assets.json", {"assets": [
        {"source": "mist", "collector": "mist", "asset_id": "mist-1", "source_asset_id": "mist-1",
         "serial_number": "SW100", "hostname": "CORE-1", "device_type": "switch", "device_role": "core", "online": False,
         "site": "Example College"},
        {"source": "snmp", "collector": "snmp", "asset_id": "snmp-1", "source_asset_id": "snmp-1",
         "serial_number": "SW100", "hostname": "CORE-1", "device_type": "switch", "online": False,
         "site": "EC", "management_ip": "192.0.2.10"},
    ]})
    write_json(inventory / "source_runs.json", {"sources": {}})
    engine = InfrastructureStateEngine(inventory, operations, tmp_path / "state", tmp_path / "dashboard",
        sites_config=config, sites_output=tmp_path / "site-output")
    return engine, config, operations


def test_site_resolution_precedes_fusion_and_preserves_provenance(tmp_path):
    engine, _, _ = _integrated(tmp_path); state = engine.run(NOW)
    assert state["summary"]["devices"] == 1
    asset = state["assets"][0]
    assert asset["site"]["site_id"] == "site:example-campus"
    assert asset["site"]["display_name"] == "Example College, Campus"
    assert asset["site"]["source_values"] == [
        {"source": "mist", "value": "Example College"}, {"source": "snmp", "value": "EC"}]
    assert not any(value["field"] == "site" for value in asset["merge"]["conflicts"])
    assert state["site_registry_statistics"]["aliases_used"] == 2


def test_operations_and_runtime_outputs_use_site_id(tmp_path):
    engine, config, operations = _integrated(tmp_path); state = engine.run(NOW)
    operations_engine = OperationsEngine(tmp_path / "inventory", operations, tmp_path / "missing.json",
        infrastructure_state=tmp_path / "state/state.json", sites_config=config)
    result = operations_engine.run(NOW)
    issue = next(value for value in result["issues"] if value["canonical_id"] == state["assets"][0]["canonical_id"])
    assert issue["site_id"] == "site:example-campus" and issue["site"] == "Example College, Campus"
    payload, summary = engine.site_registry.write(tmp_path / "site-output", tmp_path / "dashboard",
                                                  state, result, state["site_validation"],
                                                  state["site_registry_statistics"])
    assert payload["sites"][0]["issues"] == 1 and summary["total_sites"] == 1
    with (tmp_path / "site-output/sites.csv").open(newline="") as handle:
        assert list(csv.DictReader(handle))[0]["site_id"] == "site:example-campus"


def test_dashboard_variable_and_estate_metrics_are_generated(tmp_path):
    engine, config, operations = _integrated(tmp_path); engine.run(NOW)
    template = __import__("pathlib").Path(__file__).resolve().parents[2] / "dashboards/Infrastructure Overview/infrastructure-overview.json"
    output = tmp_path / "dashboard/grafana/overview.json"
    OperationsEngine(tmp_path / "inventory", operations, template, dashboard_output=output,
        infrastructure_state=tmp_path / "state/state.json",
        infrastructure_summary=tmp_path / "dashboard/infrastructure-summary.json",
        sites_config=config).run(NOW)
    dashboard = json.loads(output.read_text())
    variable = next(value for value in dashboard["templating"]["list"] if value["name"] == "site")
    assert [value["value"] for value in variable["options"]] == ["site:example-campus"]
    assert variable["query"] == r"Example College\, Campus : site:example-campus"
    panels = {value["title"]: value for value in dashboard["panels"]}
    site_rows = list(csv.DictReader(__import__("io").StringIO(
        panels["Sites"]["targets"][0]["csvContent"])))
    critical_rows = list(csv.DictReader(__import__("io").StringIO(
        panels["Critical Sites"]["targets"][0]["csvContent"])))
    assert next(value for value in site_rows if value["scope"] == "all")["value"] == "1"
    assert next(value for value in critical_rows if value["scope"] == "all")["value"] == "1"


def test_site_registry_does_not_require_collector_changes(tmp_path):
    engine, _, _ = _integrated(tmp_path)
    first = engine.evaluate(NOW); second = engine.evaluate(NOW)
    assert first == second
    assert first["assets"][0]["sources"] == ["mist", "snmp"]


def test_conflicting_explicit_site_ids_remain_a_fusion_conflict(tmp_path):
    config = tmp_path / "sites.yml"
    write_sites(config, [
        {"id": "one", "display_name": "One", "aliases": ["ONE"]},
        {"id": "two", "display_name": "Two", "aliases": ["TWO"]},
    ])
    inventory = tmp_path / "inventory"
    write_json(inventory / "assets.json", {"assets": [
        {"source": "mist", "asset_id": "a", "serial_number": "S1", "site": "One", "site_id": "site:one"},
        {"source": "snmp", "asset_id": "b", "serial_number": "S1", "site": "Two", "site_id": "site:two"},
    ]})
    state = InfrastructureStateEngine(inventory, tmp_path / "ops", tmp_path / "state", tmp_path / "dash",
        sites_config=config, sites_output=tmp_path / "site-output").evaluate(NOW)
    assert any(value["field"] == "site_id" for value in state["assets"][0]["merge"]["conflicts"])
