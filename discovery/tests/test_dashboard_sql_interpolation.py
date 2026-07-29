import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DASHBOARDS = (
    ROOT / "dashboards/vendor/mist-infrastructure-overview.json",
    ROOT / "dashboards/vendor/fortigate-overview.json",
)
TEXT_VARIABLES = ("site", "customer", "device", "device_type", "status")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _queries(dashboard: dict) -> list[str]:
    queries = [
        target["rawSql"]
        for panel in dashboard["panels"]
        for target in panel["targets"]
    ]
    for variable in dashboard["templating"]["list"]:
        if variable["type"] == "query":
            queries.extend(
                value
                for value in (variable.get("query"), variable.get("definition"))
                if isinstance(value, str)
            )
    return queries


def _sqlstring(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def test_dashboards_use_classic_schema_and_flightsql_target_contract() -> None:
    for path in DASHBOARDS:
        dashboard = _load(path)
        assert isinstance(dashboard["panels"], list)
        assert "elements" not in dashboard
        assert "layout" not in dashboard

        for panel in dashboard["panels"]:
            for target in panel["targets"]:
                assert target["rawSql"]
                assert target["rawQuery"] is True
                assert target["format"] == "table"


def test_text_variables_are_sqlstring_formatted_without_manual_quotes() -> None:
    manually_quoted = re.compile(
        r"['\"]\$\{(?:" + "|".join(TEXT_VARIABLES) + r")(?:\}|:[^}]+\})['\"]"
    )

    for path in DASHBOARDS:
        queries = _queries(_load(path))
        assert not any(manually_quoted.search(query) for query in queries)

        for query in queries:
            for variable in TEXT_VARIABLES:
                if "${" + variable + "}" in query:
                    raise AssertionError(f"unsafe interpolation for {variable} in {path.name}")


def test_apostrophe_sites_render_as_valid_sql_literals() -> None:
    sites = (
        "O'Brien's Campus",
        "O'Brien's Campus P2P",
    )

    for path in DASHBOARDS:
        site_queries = [
            query for query in _queries(_load(path)) if "${site:sqlstring}" in query
        ]
        assert site_queries
        for site in sites:
            rendered_literal = _sqlstring(site)
            assert rendered_literal == "'" + site.replace("'", "''") + "'"
            for query in site_queries:
                rendered = query.replace("${site:sqlstring}", rendered_literal)
                assert "${site" not in rendered
                assert "O''Brien''s Campus" in rendered


def test_all_value_wildcards_keep_like_semantics() -> None:
    for path in DASHBOARDS:
        dashboard = _load(path)
        variables = {
            variable["name"]: variable
            for variable in dashboard["templating"]["list"]
            if variable.get("includeAll")
        }
        assert variables
        assert all(variable["allValue"] == "'%'" for variable in variables.values())

        queries = "\n".join(_queries(dashboard))
        for name in variables:
            if "${" + name + ":sqlstring}" in queries:
                assert "LIKE ${" + name + ":sqlstring}" in queries
                rendered = queries.replace(
                    "${" + name + ":sqlstring}", variables[name]["allValue"]
                )
                assert "LIKE '%'" in rendered
                assert "LIKE %" not in rendered.replace("LIKE '%'", "")


def test_mist_collection_success_is_numeric_and_mapped() -> None:
    dashboard = _load(DASHBOARDS[0])
    panel = next(
        panel
        for panel in dashboard["panels"]
        if panel["title"] == "Last Collection Successful"
    )
    assert "CASE WHEN success THEN 1 ELSE 0 END AS collection_success" in panel[
        "targets"
    ][0]["rawSql"]

    mappings = panel["fieldConfig"]["defaults"]["mappings"][0]["options"]
    assert mappings["1"] == {"color": "green", "index": 1, "text": "Successful"}
    assert mappings["0"] == {"color": "red", "index": 0, "text": "Failed"}
