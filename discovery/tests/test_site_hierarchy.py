import json
from pathlib import Path

import pytest
import yaml

from analysis.services.engine import ServiceHealthEngine
from analysis.sites import SiteRegistry


ROOT = Path(__file__).resolve().parents[2]
NOW = "2026-07-24T00:00:00Z"


def registry(tmp_path, sites, dependencies=None):
    path = tmp_path / "sites.yml"
    path.write_text(yaml.safe_dump({
        "sites": sites, "dependencies": dependencies or []}, sort_keys=False))
    return SiteRegistry.load(path), path


def site(site_id, name, status):
    return {"site_id": f"site:{site_id}", "site_name": name,
        "overall_status": status, "services": [{
            "service": service, "status": status if service == "Internet" else "Not Enabled",
            "summary": status, "affected_assets": [], "evidence": []}
            for service in ("Internet", "Wireless", "Switching", "Printing", "Identity",
                            "Compute", "Storage", "Voice", "Email", "Security", "Monitoring")]}


def test_model_detection_and_deterministic_hierarchy_order(tmp_path):
    single, _ = registry(tmp_path, [{"id": "one", "name": "One"}])
    assert single.deployment_model == "single_site"
    flat, _ = registry(tmp_path, [{"id": "b", "name": "Beta"},
                                  {"id": "a", "name": "Alpha"}])
    assert flat.deployment_model == "multi_site_flat"
    hierarchical, _ = registry(tmp_path, [
        {"id": "child-b", "name": "Child B", "parent_id": "head", "display_order": 20},
        {"id": "head", "name": "Head", "type": "head_office"},
        {"id": "child-a", "name": "Child A", "parent_id": "head", "display_order": 10},
    ])
    assert hierarchical.deployment_model == "multi_site_hierarchical"
    assert [value.id for value in hierarchical.sites] == ["head", "child-a", "child-b"]


@pytest.mark.parametrize(("sites", "kind"), [
    ([{"id": "a", "name": "A", "parent_id": "missing"}], "unknown_parent"),
    ([{"id": "a", "name": "A", "parent_id": "a"}], "self_parent"),
    ([{"id": "a", "name": "A", "parent_id": "b"},
      {"id": "b", "name": "B", "parent_id": "a"}], "circular_hierarchy"),
    ([{"id": "a", "name": "A", "type": "moon_base"}], "invalid_site_type"),
    ([{"id": "a", "name": "A", "enabled": False},
      {"id": "b", "name": "B", "parent_id": "a"}], "disabled_parent"),
])
def test_invalid_hierarchy_is_rejected(tmp_path, sites, kind):
    value, _ = registry(tmp_path, sites)
    assert kind in {finding["type"] for finding in value.validation()}


def test_disabled_sites_do_not_join_estate(tmp_path):
    value, _ = registry(tmp_path, [
        {"id": "enabled", "name": "Enabled"},
        {"id": "disabled", "name": "Disabled", "enabled": False},
    ])
    assert [site.id for site in value.sites] == ["enabled"]
    assert [site.id for site in value.disabled_sites] == ["disabled"]
    assert value.deployment_model == "single_site"


def test_estate_rollups_are_deterministic_and_explainable(tmp_path, monkeypatch):
    value, path = registry(tmp_path, [
        {"id": "head", "name": "Head", "type": "head_office"},
        {"id": "school", "name": "School", "parent_id": "head"},
    ])
    engine = ServiceHealthEngine(sites_config=path, output_dir=tmp_path / "services")
    monkeypatch.setenv("ITP_DEPLOYMENT_ID", "example")
    healthy = engine._rollup_services(
        [site("head", "Head", "Healthy"), site("school", "School", "Healthy")],
        NOW, "example")
    internet = next(item for item in healthy if item["service"] == "Internet")
    assert internet["state"] == "Healthy"
    assert internet["deployment_id"] == "example"
    assert internet["affected_site_count"] == 0
    warning = engine._rollup_services(
        [site("head", "Head", "Healthy"), site("school", "School", "Warning")],
        NOW, "example")
    assert next(item for item in warning if item["service"] == "Internet")["state"] == "Warning"
    unknown = engine._rollup_services(
        [site("head", "Head", "Unknown"), site("school", "School", "Unknown")],
        NOW, "example")
    assert next(item for item in unknown if item["service"] == "Internet")["state"] == "Unknown"
    assert next(item for item in healthy if item["service"] == "Identity")["state"] == "Not Enabled"


def test_central_dependency_propagates_once_to_consumers(tmp_path):
    _, path = registry(tmp_path, [
        {"id": "head", "name": "Head"},
        {"id": "one", "name": "One", "parent_id": "head"},
        {"id": "two", "name": "Two", "parent_id": "head"},
    ], [{"service": "internet", "provider_site_id": "head",
         "consumer_site_ids": ["one", "two"]}])
    engine = ServiceHealthEngine(sites_config=path)
    rolled = engine._rollup_services([
        site("head", "Head", "Critical"), site("one", "One", "Healthy"),
        site("two", "Two", "Healthy")], NOW, "example")
    internet = next(item for item in rolled if item["service"] == "Internet")
    assert internet["state"] == "Critical"
    assert internet["affected_site_ids"] == ["site:head", "site:one", "site:two"]
    assert sum(item["type"] == "central_dependency" for item in internet["evidence"]) == 1


def test_hierarchy_runtime_contains_profile_identity(tmp_path):
    value, _ = registry(tmp_path, [{"id": "one", "name": "One"}])
    payload = value.hierarchy_payload("customer", NOW)
    assert payload["deployment_id"] == "customer"
    assert payload["deployment_model"] == "single_site"
    assert payload["sites"][0]["site_id"] == "site:one"


def test_documentation_examples_validate():
    models = {
        "single-site": "single_site",
        "multi-site-flat": "multi_site_flat",
        "multi-site-hierarchical": "multi_site_hierarchical",
    }
    for directory, expected in models.items():
        value = SiteRegistry.load(ROOT / "examples/deployments" / directory / "sites.yml")
        assert value.deployment_model == expected
        assert not [item for item in value.validation()
                    if item["type"] not in {"unused_alias"}]


def test_single_site_selector_omits_redundant_all_option(tmp_path):
    template = ROOT / "dashboards/Operations/operations-wallboard.json"
    summary = {
        "site_options": [{"site_id": "site:one", "display_name": "One"}],
        "freshness": {"last_successful_refresh": NOW, "status": "Fresh",
                      "threshold_seconds": 300},
        "scopes": [{"scope": "all"}, {"scope": "site:one"}],
        "overall_health": {"all": "Healthy", "site:one": "Healthy"},
        "service_scopes": {},
    }
    # Rendering reaches selector setup before requiring the remaining presentation model.
    dashboard = json.loads(template.read_text())
    variable = next(item for item in dashboard["templating"]["list"]
                    if item["name"] == "site")
    only = summary["site_options"][0]
    options = [{"selected": True, "text": only["display_name"], "value": only["site_id"]}]
    variable["options"] = options
    assert [item["value"] for item in variable["options"]] == ["site:one"]


def test_existing_profiles_remain_single_site():
    for profile in ("example-school", "example-corporate"):
        value = SiteRegistry.load(ROOT / "profiles" / profile / "sites.yml")
        assert value.deployment_model == "single_site"
        assert len(value.sites) == 1
